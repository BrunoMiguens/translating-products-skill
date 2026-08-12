from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile


CONTEXT_FILES = (
    "project-brief.md",
    "locales.yaml",
    "glossary.csv",
    "style-guide.md",
    "protected-terms.txt",
)
REQUIRED_PROJECT_FILES = frozenset(CONTEXT_FILES)
APPROVAL_FILE = "setup-approval.json"
APPROVAL_FIELDS = {
    "status",
    "approved_by",
    "approved_at",
    "context_sha256",
    "approved_empty",
}
EMPTY_APPROVABLE_FILES = ("glossary.csv", "protected-terms.txt")
GLOSSARY_HEADER = (
    "source_term",
    "target_term",
    "locale",
    "context",
    "status",
    "notes",
)
LOCALES_FIELDS = {
    "source_locale",
    "target_locales",
    "fallback_locale",
    "neutral_variants_allowed",
}
LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

PROJECT_BRIEF_FIELDS = (
    ("Name", "name"),
    ("Summary", "summary"),
    ("Audience", "audience"),
    ("Source content", "source-content"),
    ("Surfaces", "surfaces"),
    ("Domains", "domains"),
    ("Constraints", "constraints"),
    ("Explicit requirements", "explicit-requirements"),
    ("Approved reviewers", "approved-reviewers"),
    ("Approved external specialists", "approved-external-specialists"),
    ("Formats", "formats"),
    ("Delivery location", "delivery-location"),
    ("Acceptance criteria", "acceptance-criteria"),
)
STYLE_GUIDE_FIELDS = (
    ("Voice", "voice"),
    ("Formality", "formality"),
    ("Audience relationship", "audience-relationship"),
    ("Capitalization", "capitalization"),
    ("Punctuation", "punctuation"),
    ("Numbers, dates, and currency", "numbers-dates-currency"),
    ("Inclusive language", "inclusive-language"),
    ("Length limits", "length-limits"),
    ("Accessibility", "accessibility"),
    ("Formatting", "formatting"),
)


def _translation_directory(project_root: object) -> Path | None:
    if isinstance(project_root, (set, frozenset, list, tuple)):
        return None
    try:
        root = Path(project_root)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return root / ".translation"


def _safe_file_bytes(
    translation_dir: Path,
    name: str,
) -> tuple[str | None, bytes | None, str | None]:
    candidate = translation_dir / name
    try:
        if candidate.is_symlink():
            return f"unsafe:{name}", None, None
        if not candidate.exists():
            return f"missing:{name}", None, None
        if not candidate.is_file():
            return f"unreadable:{name}", None, None
        directory = translation_dir.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(directory)
        raw = candidate.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return f"unreadable:{name}", None, None
    return None, raw, text


def _load_context_files(
    project_root: object,
) -> tuple[str | None, Path | None, dict[str, bytes], dict[str, str]]:
    translation_dir = _translation_directory(project_root)
    if translation_dir is None:
        return "context-inspection-required", None, {}, {}
    try:
        if translation_dir.is_symlink():
            return "unsafe:.translation", None, {}, {}
        if not translation_dir.exists():
            return "missing:.translation", None, {}, {}
        if not translation_dir.is_dir():
            return "unreadable:.translation", None, {}, {}
    except OSError:
        return "unreadable:.translation", None, {}, {}

    raw_files: dict[str, bytes] = {}
    text_files: dict[str, str] = {}
    for name in CONTEXT_FILES:
        issue, raw, text = _safe_file_bytes(translation_dir, name)
        if issue is not None:
            return issue, translation_dir, {}, {}
        assert raw is not None and text is not None
        raw_files[name] = raw
        text_files[name] = text
    return None, translation_dir, raw_files, text_files


def _parse_labeled_markdown(
    text: str,
    name: str,
    required_fields: tuple[tuple[str, str], ...],
) -> tuple[str | None, list[str]]:
    if "\x00" in text:
        return f"malformed:{name}", []
    status_values: list[str] = []
    values: dict[str, list[str]] = {}
    required = {label.casefold(): slug for label, slug in required_fields}

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.casefold().startswith("status:"):
            status_values.append(stripped.split(":", 1)[1].strip())
            continue
        match = re.match(r"^-\s*([^:]+):\s*(.*)$", stripped)
        if match is None:
            continue
        label = match.group(1).strip().casefold()
        if label in required:
            values.setdefault(label, []).append(match.group(2).strip())

    if len(status_values) != 1:
        return f"malformed:{name}", []
    if any(len(values.get(label.casefold(), [])) != 1 for label, _ in required_fields):
        return f"malformed:{name}", []

    incomplete = []
    if status_values[0].casefold() != "approved":
        incomplete.append(f"incomplete:{name}:status")
    for label, slug in required_fields:
        if not values[label.casefold()][0]:
            incomplete.append(f"incomplete:{name}:{slug}")
    return None, incomplete


def _parse_independent_review_requirement(text: str) -> tuple[str | None, bool]:
    values = []
    for line in text.splitlines():
        match = re.match(r"^-\s*Independent review required:\s*(.*)$", line.strip())
        if match is not None:
            values.append(match.group(1))
    if not values:
        return None, False
    if len(values) != 1 or values[0] not in {"true", "false"}:
        return "malformed:project-brief.md", False
    return None, values[0] == "true"


def _parse_yaml_string(value: str) -> str | None:
    value = value.strip()
    if not value:
        return ""
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, str) else None
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            return None
        return value[1:-1]
    if any(character in value for character in "{}[],:#"):
        return None
    return value


def _parse_target_locales(value: str) -> list[str] | None:
    value = value.strip()
    if not value.startswith("[") or not value.endswith("]"):
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []
    parsed = []
    for item in inner.split(","):
        locale = _parse_yaml_string(item)
        if locale is None or not locale or not LOCALE.fullmatch(locale):
            return None
        parsed.append(locale)
    if len({locale.casefold() for locale in parsed}) != len(parsed):
        return None
    return parsed


def _parse_locales(text: str) -> tuple[str | None, list[str], dict[str, object]]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if raw_line[:1].isspace() or ":" not in line:
            return "malformed:locales.yaml", [], {}
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in LOCALES_FIELDS or key in values:
            return "malformed:locales.yaml", [], {}
        values[key] = value.strip()
    if set(values) != LOCALES_FIELDS:
        return "malformed:locales.yaml", [], {}

    source = _parse_yaml_string(values["source_locale"])
    fallback = _parse_yaml_string(values["fallback_locale"])
    targets = _parse_target_locales(values["target_locales"])
    neutral_text = values["neutral_variants_allowed"].casefold()
    if (
        source is None
        or fallback is None
        or targets is None
        or neutral_text not in {"true", "false"}
    ):
        return "malformed:locales.yaml", [], {}
    if source and not LOCALE.fullmatch(source):
        return "malformed:locales.yaml", [], {}
    if fallback and not LOCALE.fullmatch(fallback):
        return "malformed:locales.yaml", [], {}

    incomplete = []
    if not source:
        incomplete.append("incomplete:locales.yaml:source-locale")
    if not targets:
        incomplete.append("incomplete:locales.yaml:target-locales")
    return (
        None,
        incomplete,
        {
            "source_locale": source,
            "target_locales": targets,
            "fallback_locale": fallback,
            "neutral_variants_allowed": neutral_text == "true",
        },
    )


def _parse_glossary(text: str) -> tuple[str | None, list[str], int]:
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error:
        return "malformed:glossary.csv", [], 0
    if not rows or tuple(rows[0]) != GLOSSARY_HEADER:
        return "malformed:glossary.csv", [], 0

    malformed = False
    incomplete = []
    entries = 0
    for line_number, row in enumerate(rows[1:], start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != len(GLOSSARY_HEADER):
            malformed = True
            continue
        fields = [cell.strip() for cell in row]
        entries += 1
        if (
            not fields[0]
            or not fields[1]
            or not fields[2]
            or not LOCALE.fullmatch(fields[2])
            or fields[4].casefold() != "approved"
        ):
            incomplete.append(f"incomplete:glossary.csv:row-{line_number}")
    if malformed:
        return "malformed:glossary.csv", [], 0
    return None, incomplete, entries


def _parse_protected_terms(text: str) -> tuple[str | None, int]:
    if "\x00" in text:
        return "malformed:protected-terms.txt", 0
    terms = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return None, len(terms)


def _inspect_semantics(
    text_files: Mapping[str, str],
    *,
    parse_independent_review_requirement: bool = True,
) -> tuple[str | None, dict[str, object]]:
    brief_malformed, brief_incomplete = _parse_labeled_markdown(
        text_files["project-brief.md"],
        "project-brief.md",
        PROJECT_BRIEF_FIELDS,
    )
    independent_review_required = False
    if brief_malformed is None and parse_independent_review_requirement:
        (
            brief_malformed,
            independent_review_required,
        ) = _parse_independent_review_requirement(text_files["project-brief.md"])
    locales_malformed, locales_incomplete, locales = _parse_locales(
        text_files["locales.yaml"]
    )
    glossary_malformed, glossary_incomplete, glossary_entries = _parse_glossary(
        text_files["glossary.csv"]
    )
    style_malformed, style_incomplete = _parse_labeled_markdown(
        text_files["style-guide.md"],
        "style-guide.md",
        STYLE_GUIDE_FIELDS,
    )
    protected_malformed, protected_entries = _parse_protected_terms(
        text_files["protected-terms.txt"]
    )

    for issue in (
        brief_malformed,
        locales_malformed,
        glossary_malformed,
        style_malformed,
        protected_malformed,
    ):
        if issue is not None:
            return issue, {}
    for issues in (
        brief_incomplete,
        locales_incomplete,
        glossary_incomplete,
        style_incomplete,
    ):
        if issues:
            return issues[0], {}
    return (
        None,
        {
            "locales": locales,
            "independent_review_required": independent_review_required,
            "empty_collections": {
                "glossary.csv": glossary_entries == 0,
                "protected-terms.txt": protected_entries == 0,
            },
        },
    )


def _load_approval(
    translation_dir: Path,
    raw_files: Mapping[str, bytes],
    semantic: Mapping[str, object],
) -> tuple[str | None, dict[str, object] | None]:
    issue, _, text = _safe_file_bytes(translation_dir, APPROVAL_FILE)
    if issue is not None:
        return issue, None
    assert text is not None

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field {key}")
            result[key] = value
        return result

    try:
        record = json.loads(text, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, TypeError, ValueError):
        return f"malformed:{APPROVAL_FILE}", None
    if not isinstance(record, dict) or set(record) != APPROVAL_FIELDS:
        return f"malformed:{APPROVAL_FILE}", None
    if not isinstance(record["status"], str):
        return f"malformed:{APPROVAL_FILE}", None
    if record["status"].casefold() != "approved":
        return f"unapproved:{APPROVAL_FILE}", None
    if (
        not isinstance(record["approved_by"], str)
        or not record["approved_by"].strip()
    ):
        return f"unapproved:{APPROVAL_FILE}", None
    if (
        not isinstance(record["approved_at"], str)
        or not record["approved_at"].strip()
    ):
        return f"unapproved:{APPROVAL_FILE}", None

    hashes = record["context_sha256"]
    if (
        not isinstance(hashes, dict)
        or set(hashes) != set(CONTEXT_FILES)
        or any(
            not isinstance(value, str) or not SHA256.fullmatch(value)
            for value in hashes.values()
        )
    ):
        return f"malformed:{APPROVAL_FILE}", None
    approved_empty = record["approved_empty"]
    if (
        not isinstance(approved_empty, list)
        or any(not isinstance(name, str) for name in approved_empty)
        or len(set(approved_empty)) != len(approved_empty)
        or any(name not in EMPTY_APPROVABLE_FILES for name in approved_empty)
    ):
        return f"malformed:{APPROVAL_FILE}", None

    empty_collections = semantic["empty_collections"]
    assert isinstance(empty_collections, dict)
    for name in EMPTY_APPROVABLE_FILES:
        is_empty = empty_collections[name]
        if is_empty and name not in approved_empty:
            return f"unapproved-empty:{name}", None
        if not is_empty and name in approved_empty:
            return f"malformed:{APPROVAL_FILE}", None

    for name in CONTEXT_FILES:
        actual = hashlib.sha256(raw_files[name]).hexdigest()
        if hashes[name] != actual:
            return f"approval-hash-mismatch:{name}", None
    return None, record


def _request_issue(
    request: object,
    locales: Mapping[str, object],
) -> str | None:
    if request is None:
        request = {}
    if not isinstance(request, Mapping):
        return "malformed-request"

    source = request.get("source_locale")
    if source is not None:
        if not isinstance(source, str) or not source.strip() or not LOCALE.fullmatch(source):
            return "malformed-request:source-locale"
        configured_source = locales["source_locale"]
        assert isinstance(configured_source, str)
        if source.casefold() != configured_source.casefold():
            return "conflict:source-locale"

    requested_targets: list[str] = []
    target = request.get("target_locale")
    if target is not None:
        if (
            not isinstance(target, str)
            or not target.strip()
            or not LOCALE.fullmatch(target)
        ):
            return "malformed-request:target-locale"
        requested_targets.append(target)
    targets = request.get("target_locales")
    if targets is not None:
        if (
            not isinstance(targets, Sequence)
            or isinstance(targets, (str, bytes))
            or any(
                not isinstance(item, str)
                or not item.strip()
                or not LOCALE.fullmatch(item)
                for item in targets
            )
        ):
            return "malformed-request:target-locales"
        requested_targets.extend(targets)

    configured_targets = locales["target_locales"]
    assert isinstance(configured_targets, list)
    configured = {item.casefold() for item in configured_targets}
    for locale in requested_targets:
        if locale.casefold() not in configured:
            return f"conflict:target-locale:{locale}"
    return None


def bootstrap_issue(
    project_root: object,
    request: object = None,
) -> str | None:
    issue, translation_dir, raw_files, text_files = _load_context_files(project_root)
    if issue is not None:
        return issue
    assert translation_dir is not None
    issue, semantic = _inspect_semantics(text_files)
    if issue is not None:
        return issue
    issue, _ = _load_approval(translation_dir, raw_files, semantic)
    if issue is not None:
        return issue
    locales = semantic["locales"]
    assert isinstance(locales, dict)
    return _request_issue(request, locales)


def bootstrap_action(
    project_root: object,
    request: object = None,
) -> str:
    return (
        "translate"
        if bootstrap_issue(project_root, request) is None
        else "setup-one-question-at-a-time"
    )


def project_independent_review_required(project_root: object) -> bool:
    issue, translation_dir, raw_files, text_files = _load_context_files(project_root)
    if issue is not None:
        raise ValueError(issue)
    assert translation_dir is not None
    issue, semantic = _inspect_semantics(
        text_files,
        parse_independent_review_requirement=False,
    )
    if issue is not None:
        raise ValueError(issue)
    issue, _ = _load_approval(translation_dir, raw_files, semantic)
    if issue is not None:
        raise ValueError(issue)
    issue, required = _parse_independent_review_requirement(
        text_files["project-brief.md"]
    )
    if issue is not None:
        raise ValueError(issue)
    return required


def bootstrap_question(issue: str | None) -> str | None:
    if issue is None:
        return None
    if issue == "context-inspection-required":
        return "May I inspect the actual .translation project context before translating?"
    if issue == "missing:.translation":
        return "What source locale should I use to begin the translation project setup?"
    if issue in {f"unapproved:{APPROVAL_FILE}", f"missing:{APPROVAL_FILE}"}:
        return "Do you approve the complete translation project configuration as shown?"
    if issue.startswith("missing:"):
        name = issue.split(":", 1)[1]
        return f"What approved content should I record in {name} before translating?"
    if issue.startswith(("unsafe:", "unreadable:", "malformed:")):
        name = issue.split(":", 1)[1]
        return f"May I repair {name} to the documented project-context schema?"
    if issue.startswith("incomplete:"):
        _, name, field = issue.split(":", 2)
        return f"What approved value should I use for {field} in {name}?"
    if issue.startswith("unapproved-empty:"):
        name = issue.split(":", 1)[1]
        return f"Do you explicitly approve {name} as intentionally empty?"
    if issue.startswith("approval-hash-mismatch:"):
        name = issue.split(":", 1)[1]
        return f"The approved {name} changed; do you approve the updated configuration?"
    if issue == "conflict:source-locale":
        return (
            "The requested source locale conflicts with project configuration; "
            "which source locale should govern this translation?"
        )
    if issue.startswith("conflict:target-locale:"):
        locale = issue.split(":", 2)[2]
        return (
            f"The requested target {locale} is not configured; should I add and "
            "reapprove it or use an approved target locale?"
        )
    if issue.startswith("malformed-request"):
        return "Which explicit source and target locales should this request use?"
    return "What should I clarify before continuing the translation setup?"


def write_setup_approval(
    project_root: object,
    *,
    approved_by: str,
    approved_at: str,
    approved_empty: Sequence[str],
) -> dict[str, object]:
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ValueError("approved_by must be nonblank")
    if not isinstance(approved_at, str) or not approved_at.strip():
        raise ValueError("approved_at must be nonblank")
    if (
        isinstance(approved_empty, (str, bytes))
        or any(not isinstance(name, str) for name in approved_empty)
        or len(set(approved_empty)) != len(approved_empty)
        or any(name not in EMPTY_APPROVABLE_FILES for name in approved_empty)
    ):
        raise ValueError("approved_empty may contain glossary.csv and protected-terms.txt only")

    issue, translation_dir, raw_files, text_files = _load_context_files(project_root)
    if issue is not None:
        raise ValueError(issue)
    assert translation_dir is not None
    issue, semantic = _inspect_semantics(text_files)
    if issue is not None:
        raise ValueError(issue)
    empty_collections = semantic["empty_collections"]
    assert isinstance(empty_collections, dict)
    for name in EMPTY_APPROVABLE_FILES:
        if empty_collections[name] and name not in approved_empty:
            raise ValueError(f"{name} is empty but is not in approved_empty")
        if not empty_collections[name] and name in approved_empty:
            raise ValueError(f"{name} has content and cannot be in approved_empty")

    approval_path = translation_dir / APPROVAL_FILE
    if approval_path.is_symlink():
        raise ValueError(f"unsafe:{APPROVAL_FILE}")
    record: dict[str, object] = {
        "status": "approved",
        "approved_by": approved_by.strip(),
        "approved_at": approved_at.strip(),
        "context_sha256": {
            name: hashlib.sha256(raw_files[name]).hexdigest()
            for name in CONTEXT_FILES
        },
        "approved_empty": list(approved_empty),
    }
    payload = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=translation_dir,
            prefix=".setup-approval.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary_name = temporary.name
        os.replace(temporary_name, approval_path)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
    return record


def should_research(
    question: str,
    bundled_knowledge_sufficient: bool,
) -> bool:
    return bool(question.strip()) and not bundled_knowledge_sufficient


def should_use_subagents(
    *,
    host_supports_subagents: bool,
    target_locales: int,
    source_units: int,
    separable_sections: int,
    terminology_pass: bool,
    independent_review: bool,
) -> bool:
    if not host_supports_subagents:
        return False
    return (
        (target_locales > 1 and source_units >= 20)
        or (separable_sections > 1 and source_units >= 100)
        or terminology_pass
        or independent_review
    )


def missing_specialist_action(
    *,
    core_can_cover: bool,
    bundled_available: bool,
) -> str:
    if bundled_available:
        return "use-bundled-specialist"
    if core_can_cover:
        return "use-core"
    return "report-missing-capability"


@dataclass(frozen=True)
class ReviewDepthDecision:
    depth: str
    reasons: tuple[str, ...]


def select_review_depth(
    *,
    task_kind: str,
    source_units: int,
    requested_depth: str | None = None,
    independent_review_required: bool = False,
    missing_essential_specialist: bool = False,
    primary_confidence: str = "medium",
    source_blocked: bool = False,
) -> ReviewDepthDecision:
    depths = {"single", "selective_challenge", "full_challenge"}
    if task_kind not in {"translation", "audit"}:
        raise ValueError("task_kind must be translation or audit")
    if type(source_units) is not int or source_units < 1:
        raise ValueError("source_units must be a positive integer")
    if requested_depth is not None and requested_depth not in depths:
        raise ValueError("requested_depth is invalid")
    if primary_confidence not in {"low", "medium", "high"}:
        raise ValueError("primary_confidence is invalid")
    flags = {
        "independent_review_required": independent_review_required,
        "missing_essential_specialist": missing_essential_specialist,
        "source_blocked": source_blocked,
    }
    if any(type(value) is not bool for value in flags.values()):
        raise ValueError("review-depth flags must be booleans")

    reasons = []
    if requested_depth == "full_challenge":
        reasons.append("caller-exhaustive")
    if independent_review_required:
        reasons.append("capability-requires-independent-review")
    if missing_essential_specialist:
        reasons.append("missing-essential-specialist")
    if primary_confidence == "low":
        reasons.append("low-primary-confidence")
    if source_blocked:
        reasons.append("source-blocked")
    if reasons:
        return ReviewDepthDecision("full_challenge", tuple(reasons))
    if requested_depth == "selective_challenge":
        return ReviewDepthDecision("selective_challenge", ("caller-selective",))
    if task_kind == "audit" and source_units > 1:
        return ReviewDepthDecision("selective_challenge", ("multi-unit-audit",))
    reason = "caller-single" if requested_depth == "single" else "ordinary-translation-qa"
    return ReviewDepthDecision("single", (reason,))


AUTHORITY_ORDER = (
    "explicit-user-requirements",
    "approved-project-configuration",
    "core-semantic-fidelity",
    "domain-terminology",
    "language-locale-mechanics",
    "product-platform-formatting",
    "stylistic-preferences",
)


def _request_from_json(path: str | None) -> object:
    if path is None:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _review_depth_request(request: object) -> dict[str, object]:
    if not isinstance(request, Mapping):
        raise ValueError("review-depth request must be an object")
    allowed = {
        "task_kind",
        "source_units",
        "requested_depth",
        "route_independent_review_required",
        "missing_essential_specialist",
        "primary_confidence",
        "source_blocked",
    }
    unknown = set(request) - allowed
    if unknown:
        raise ValueError(
            "unknown review-depth request fields: " + ", ".join(sorted(unknown))
        )
    for name in ("task_kind", "source_units"):
        if name not in request:
            raise ValueError(f"review-depth request missing {name}")

    route_required = request.get("route_independent_review_required", False)
    if type(route_required) is not bool:
        raise ValueError("route_independent_review_required must be boolean")
    return {
        "task_kind": request["task_kind"],
        "source_units": request["source_units"],
        "requested_depth": request.get("requested_depth"),
        "route_independent_review_required": route_required,
        "missing_essential_specialist": request.get(
            "missing_essential_specialist", False
        ),
        "primary_confidence": request.get("primary_confidence", "medium"),
        "source_blocked": request.get("source_blocked", False),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect translation bootstrap policy")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("bootstrap")
    inspect_parser.add_argument("--project-root", required=True)
    inspect_parser.add_argument("--request-json")
    review_depth_parser = subparsers.add_parser("review-depth")
    review_depth_parser.add_argument("--project-root", required=True)
    review_depth_parser.add_argument("--request-json", required=True)
    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("--project-root", required=True)
    approve_parser.add_argument("--approved-by", required=True)
    approve_parser.add_argument("--approved-at", required=True)
    approve_parser.add_argument(
        "--approved-empty",
        action="append",
        default=[],
        choices=EMPTY_APPROVABLE_FILES,
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "approve":
            record = write_setup_approval(
                args.project_root,
                approved_by=args.approved_by,
                approved_at=args.approved_at,
                approved_empty=args.approved_empty,
            )
            print(json.dumps(record, sort_keys=True))
            return 0
        if args.command == "review-depth":
            request = _review_depth_request(_request_from_json(args.request_json))
            project_required = project_independent_review_required(args.project_root)
            decision = select_review_depth(
                task_kind=request["task_kind"],  # type: ignore[arg-type]
                source_units=request["source_units"],  # type: ignore[arg-type]
                requested_depth=request["requested_depth"],  # type: ignore[arg-type]
                independent_review_required=(
                    project_required
                    or request["route_independent_review_required"]  # type: ignore[truthy-bool]
                ),
                missing_essential_specialist=request["missing_essential_specialist"],  # type: ignore[arg-type]
                primary_confidence=request["primary_confidence"],  # type: ignore[arg-type]
                source_blocked=request["source_blocked"],  # type: ignore[arg-type]
            )
            print(
                json.dumps(
                    {"review_depth": decision.depth, "reasons": list(decision.reasons)},
                    separators=(",", ":"),
                )
            )
            return 0
        request = _request_from_json(args.request_json)
        issue = bootstrap_issue(args.project_root, request)
        result = {
            "action": "translate" if issue is None else "setup-one-question-at-a-time",
            "issue": issue,
            "question": bootstrap_question(issue),
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        parser.exit(2, f"invalid bootstrap input: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
