from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata
from urllib.parse import unquote_to_bytes, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from metadata_contract import parse_frontmatter_text, validate_name, validate_skill_record


ROUTING_AXES = (
    "languages",
    "locales",
    "scripts",
    "surfaces",
    "platforms",
    "formats",
    "domains",
    "capabilities",
)
PHASES = ("inspect", "translate", "refine", "integrate", "review")
LOAD_PHASES = ("translate", "inspect", "refine", "integrate", "review")
SPECIFICITY_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "universal",
            "writing-system",
            "language",
            "locale",
            "surface",
            "platform",
            "format",
            "domain",
            "quality",
        )
    )
}
SHARED_LIST_FIELDS = (
    "surfaces",
    "platforms",
    "formats",
    "domains",
    "capabilities",
)
TARGET_LIST_FIELDS = ("scripts",)
TEXT_FIELDS = ("audience", "purpose", "register")
MANDATORY_CAPABILITIES = ("core-translation", "translation-qa")
EXTERNAL_SKILL_FIELDS = frozenset(
    (
        "name",
        "version",
        "category",
        "description",
        "capabilities",
        "depends_on",
        "phases",
        "specificity",
        "required_context",
        "conflicts",
        "supersedes",
    )
)
EXTERNAL_OPTIONAL_FIELDS = frozenset(("ownership", "selectors"))
EXTERNAL_AUTHORIZATION_FIELDS = (
    "authorized_external_skills",
    "project_authorized_external_skills",
)
REQUEST_FIELDS = frozenset(
    (
        "schema_version",
        "source_locale",
        "targets",
        *SHARED_LIST_FIELDS,
        *TEXT_FIELDS,
        *EXTERNAL_AUTHORIZATION_FIELDS,
    )
)
TARGET_FIELDS = frozenset(("locale", "register", *TARGET_LIST_FIELDS))
REGISTRY_FIELDS = frozenset(
    (
        "name",
        "version_constraint",
        "compatible_orchestrator_version",
        "capabilities",
        "authority_scope",
        "dependencies",
        "conflicts",
        "supersedes",
        "reviewer",
        "evaluation_evidence",
        "review_date",
    )
)
REGISTRY_AUTHORITY_FIELDS = frozenset(("phases", "ownership", "selectors"))
SEMVER = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)$"
)
VERSION_CONSTRAINT = re.compile(
    r"^(?P<operator>>=|<=|==|>|<)?"
    r"(?P<version>(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*))$"
)
REVIEW_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SHA256_EVIDENCE = re.compile(r"^sha256:[0-9a-f]{64}$")
DNS_LABEL = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
URL_HEXDIGITS = frozenset("0123456789abcdefABCDEF")
URL_UNRESERVED_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
URL_PATH_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-._~!$&'()*+,;=:@/"
)
MAX_INSTALLED_FILES = 1024
MAX_INSTALLED_FILE_BYTES = 10 * 1024 * 1024
MAX_INSTALLED_TREE_BYTES = 50 * 1024 * 1024


def normalize_locale(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("locale must be a string")
    parts = value.replace("_", "-").split("-")
    if (
        not parts
        or not parts[0].isalpha()
        or not 2 <= len(parts[0]) <= 8
        or any(not part or not part.isalnum() or len(part) > 8 for part in parts)
    ):
        raise ValueError(f"invalid locale: {value}")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        elif (len(part) == 2 and part.isalpha()) or (
            len(part) == 3 and part.isdigit()
        ):
            normalized.append(part.upper())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


def language_from_locale(locale: str) -> str:
    return normalize_locale(locale).split("-", 1)[0]


def locale_range_matches(locale: str, candidate: str) -> bool:
    normalized_locale = normalize_locale(locale).casefold()
    normalized_candidate = normalize_locale(candidate.rstrip("*-")).casefold()
    return normalized_locale == normalized_candidate or normalized_locale.startswith(
        normalized_candidate + "-"
    )


def _axis_values(profile: dict, axis: str) -> tuple[str, ...]:
    if axis == "languages":
        return (profile["language"],)
    if axis == "locales":
        return (profile["target_locale"],)
    return tuple(profile.get(axis, ()))


def selector_matches(profile: dict, selector: dict) -> bool:
    for axis, candidates in selector.items():
        if axis not in ROUTING_AXES:
            raise ValueError(f"unknown selector axis: {axis}")
        actual = _axis_values(profile, axis)
        if axis == "locales":
            if not any(
                locale_range_matches(locale, candidate)
                for locale in actual
                for candidate in candidates
            ):
                return False
            continue
        actual_folded = {value.casefold() for value in actual}
        if not actual_folded.intersection(value.casefold() for value in candidates):
            return False
    return True


def _external_string_list(
    value: object,
    field: str,
    skill_name: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        qualifier = "string list" if allow_empty else "non-empty string list"
        raise ValueError(
            f"external skill {skill_name} has invalid {field}: expected {qualifier}"
        )
    return value


def validate_external_catalog(catalog: object) -> list[dict]:
    """Validate one installed external manifest or capability catalog."""
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 2:
        raise ValueError("external catalog must use schema_version 2")
    valid_fields = (
        {"schema_version", "skills"}
        if "skills" in catalog
        else {"schema_version", "skill"}
    )
    if set(catalog) != valid_fields:
        raise ValueError("external catalog has unknown or ambiguous fields")
    skills = catalog.get("skills")
    if skills is None and isinstance(catalog.get("skill"), dict):
        skills = [catalog["skill"]]
    if not isinstance(skills, list):
        raise ValueError("external catalog skills must be a list")

    names: set[str] = set()
    for item in skills:
        validate_skill_record(item, label_prefix="external skill")
        name = item["name"]
        if name in names:
            raise ValueError(f"duplicate external skill name: {name}")
        names.add(name)
        _ownership_map(item, name)
    return skills


def _registry_string_list(
    value: object,
    field: str,
    name: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        qualifier = "string list" if allow_empty else "non-empty string list"
        raise ValueError(
            f"compatibility registry skill {name} has invalid {field}: "
            f"expected unique {qualifier}"
        )
    return value


def _parse_version(value: str, field: str, name: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ValueError(
            f"compatibility registry skill {name} has invalid {field}: {value}"
        )
    return tuple(int(match[group]) for group in ("major", "minor", "patch"))


def _parse_constraint(
    value: str, field: str, name: str
) -> tuple[str, tuple[int, int, int]]:
    match = VERSION_CONSTRAINT.fullmatch(value)
    if match is None:
        label = (
            "version constraint"
            if field == "version_constraint"
            else "orchestrator compatibility constraint"
        )
        raise ValueError(f"unsupported {label} for {name}: {value}")
    return match.group("operator") or "==", _parse_version(
        match.group("version"), field, name
    )


def _constraint_satisfied(
    version: str,
    constraint: str,
    field: str,
    name: str,
) -> bool:
    actual = _parse_version(version, field, name)
    operator, expected = _parse_constraint(constraint, field, name)
    return {
        "==": actual == expected,
        ">=": actual >= expected,
        "<=": actual <= expected,
        ">": actual > expected,
        "<": actual < expected,
    }[operator]


def _validate_registry_selector(selector: object, name: str) -> None:
    if not isinstance(selector, dict) or not selector:
        raise ValueError(
            f"compatibility registry skill {name} has invalid authority selector"
        )
    for axis, values in selector.items():
        if axis not in ROUTING_AXES:
            raise ValueError(
                f"compatibility registry skill {name} has unknown authority "
                f"selector axis: {axis}"
            )
        _registry_string_list(values, f"authority selector {axis}", name)


def _validate_authority_scope(scope: object, name: str) -> dict:
    if not isinstance(scope, dict) or set(scope) != REGISTRY_AUTHORITY_FIELDS:
        raise ValueError(
            f"compatibility registry skill {name} has invalid authority_scope"
        )
    phases = _registry_string_list(scope["phases"], "authority phases", name)
    unknown_phases = sorted(set(phases) - set(PHASES))
    if unknown_phases:
        raise ValueError(
            f"compatibility registry skill {name} has unknown authority phases: "
            + ", ".join(unknown_phases)
        )
    ownership = scope["ownership"]
    if not isinstance(ownership, dict) or not ownership:
        raise ValueError(
            f"compatibility registry skill {name} has invalid authority ownership"
        )
    for capability, owned_phases in ownership.items():
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError(
                f"compatibility registry skill {name} has invalid authority capability"
            )
        values = _registry_string_list(
            owned_phases, "authority ownership phases", name
        )
        if not set(values).issubset(phases):
            raise ValueError(
                f"compatibility registry skill {name} authority ownership phases "
                "must be declared phases"
            )
    selectors = scope["selectors"]
    if selectors is not None:
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(
                f"compatibility registry skill {name} has invalid authority selectors"
            )
        for selector in selectors:
            _validate_registry_selector(selector, name)
    return scope


def _invalid_reviewer(name: str) -> ValueError:
    return ValueError(f"compatibility registry skill {name} has invalid reviewer")


def _canonical_reviewer_path(path: str, name: str) -> str:
    if path in {"", "/"} or not path.startswith("/"):
        raise _invalid_reviewer(name)
    canonical: list[str] = []
    index = 0
    while index < len(path):
        character = path[index]
        if character == "%":
            if (
                index + 2 >= len(path)
                or path[index + 1] not in URL_HEXDIGITS
                or path[index + 2] not in URL_HEXDIGITS
            ):
                raise _invalid_reviewer(name)
            encoded = path[index + 1 : index + 3]
            octet = int(encoded, 16)
            if octet in URL_UNRESERVED_BYTES:
                canonical.append(chr(octet))
            else:
                canonical.append("%" + encoded.upper())
            index += 3
            continue
        if character not in URL_PATH_CHARACTERS:
            raise _invalid_reviewer(name)
        canonical.append(character)
        index += 1

    try:
        decoded = unquote_to_bytes(path).decode("utf-8")
    except UnicodeDecodeError:
        raise _invalid_reviewer(name) from None
    if any(
        character.isspace()
        or unicodedata.category(character).startswith("C")
        for character in decoded
    ):
        raise _invalid_reviewer(name)
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        raise _invalid_reviewer(name)
    return "".join(canonical)


def _validate_reviewer(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise _invalid_reviewer(name)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise _invalid_reviewer(name) from None
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or "?" in value
        or "#" in value
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise _invalid_reviewer(name)

    authority = parsed.netloc
    if authority.count(":") > 1:
        raise _invalid_reviewer(name)
    if ":" in authority:
        authority_host, port_text = authority.rsplit(":", 1)
        if not port_text.isdigit() or not port_text:
            raise _invalid_reviewer(name)
    else:
        authority_host = authority
    canonical_host = hostname.casefold()
    if (
        authority_host.casefold() != canonical_host
        or len(canonical_host) > 253
        or canonical_host.endswith(".")
        or "." not in canonical_host
        or any(DNS_LABEL.fullmatch(label) is None for label in canonical_host.split("."))
    ):
        raise _invalid_reviewer(name)

    canonical_path = _canonical_reviewer_path(parsed.path, name)
    canonical_port = "" if port in {None, 443} else f":{port}"
    return f"https://{canonical_host}{canonical_port}{canonical_path}"


def _validate_review_date(value: object, name: str) -> date:
    if not isinstance(value, str) or REVIEW_DATE.fullmatch(value) is None:
        raise ValueError(f"compatibility registry review date is invalid: {name}")
    try:
        reviewed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"compatibility registry review date is invalid: {name}"
        ) from None
    if reviewed > date.today():
        raise ValueError(
            f"compatibility registry review date is in the future: {name}"
        )
    return reviewed


def _validate_evidence(value: object, name: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], str)
        or SHA256_EVIDENCE.fullmatch(value[0]) is None
    ):
        raise ValueError(
            f"compatibility registry skill {name} has invalid evaluation_evidence"
        )
    return value


def _authorization_names(request: dict, registry: object) -> set[str]:
    authorized: set[str] = set()
    for field in EXTERNAL_AUTHORIZATION_FIELDS:
        if field not in request:
            continue
        values = _external_string_list(
            request[field], field, "request", allow_empty=True
        )
        authorized.update(values)
    if registry is None:
        return authorized
    if not isinstance(registry, dict) or registry.get("schema_version") != 2:
        raise ValueError("compatibility registry must use schema_version 2")
    entries = registry.get("skills")
    if not isinstance(entries, list):
        raise ValueError("compatibility registry skills must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("compatibility registry skill must be an object")
        missing = sorted(REGISTRY_FIELDS - set(entry))
        if missing:
            raise ValueError(
                "compatibility registry skill is missing fields: " + ", ".join(missing)
            )
        unknown = sorted(set(entry) - REGISTRY_FIELDS)
        if unknown:
            raise ValueError(
                "compatibility registry skill has unknown fields: " + ", ".join(unknown)
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("compatibility registry skill name must be a non-empty string")
        for field in (
            "version_constraint",
            "compatible_orchestrator_version",
            "review_date",
            "reviewer",
        ):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise ValueError(f"compatibility registry skill {name} has invalid {field}")
        _parse_constraint(entry["version_constraint"], "version_constraint", name)
        _parse_constraint(
            entry["compatible_orchestrator_version"],
            "compatible_orchestrator_version",
            name,
        )
        _registry_string_list(entry["capabilities"], "capabilities", name)
        for field in ("dependencies", "conflicts", "supersedes"):
            _registry_string_list(
                entry[field], field, name, allow_empty=True
            )
        _validate_authority_scope(entry["authority_scope"], name)
        _validate_reviewer(entry["reviewer"], name)
        _validate_review_date(entry["review_date"], name)
        _validate_evidence(entry["evaluation_evidence"], name)
        authorized.add(name)
    return authorized


def _validate_merged_relationships(
    skills: list[dict], external_names: set[str]
) -> None:
    by_name = {skill["name"]: skill for skill in skills}
    for name in sorted(by_name):
        skill = by_name[name]
        label = "external skill" if name in external_names else "bundled skill"
        for field in ("depends_on", "conflicts", "supersedes"):
            for target in sorted(set(skill[field])):
                if target not in by_name:
                    raise ValueError(f"{label} {name} has unknown {field}: {target}")
                if target == name:
                    verb = {
                        "depends_on": "depend on",
                        "conflicts": "conflict with",
                        "supersedes": "supersede",
                    }[field]
                    raise ValueError(f"{label} {name} cannot {verb} itself")
        for target in sorted(set(skill["depends_on"]) & set(skill["supersedes"])):
            raise ValueError(
                f"{label} {name} cannot both depend on and supersede {target}"
            )

    def reject_cycle(graph: dict[str, tuple[str, ...]], kind: str) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise ValueError(f"{kind} cycle at {name}")
            if name in visited:
                return
            visiting.add(name)
            for target in sorted(graph[name]):
                visit(target)
            visiting.remove(name)
            visited.add(name)

        for name in sorted(graph):
            visit(name)

    dependency_graph = {
        name: tuple(by_name[name]["depends_on"]) for name in by_name
    }
    supersedes_graph = {
        name: tuple(by_name[name]["supersedes"]) for name in by_name
    }
    reject_cycle(dependency_graph, "dependency")
    reject_cycle(supersedes_graph, "supersedes")
    reject_cycle(
        {
            name: tuple(dependency_graph[name] + supersedes_graph[name])
            for name in by_name
        },
        "relationship",
    )

    for name in sorted(by_name):
        skill = by_name[name]
        label = "external skill" if name in external_names else "bundled skill"
        for target in sorted(set(skill["supersedes"])):
            superseded = by_name[target]
            if skill["category"] != superseded["category"]:
                raise ValueError(
                    f"{label} {name} cannot supersede {target} across categories"
                )
            if SPECIFICITY_ORDER[skill["specificity"]] <= SPECIFICITY_ORDER[
                superseded["specificity"]
            ]:
                raise ValueError(
                    f"{label} {name} must be more specific than superseded skill: {target}"
                )
            replacement_ownership = _ownership_map(skill)
            target_ownership = _ownership_map(superseded)
            if not any(
                replacement_ownership.get(capability, set()) & phases
                for capability, phases in target_ownership.items()
            ):
                raise ValueError(
                    f"{label} {name} supersedes {target} without shared ownership"
                )


def _canonical_selectors(selectors: object) -> object:
    if selectors is None:
        return None
    return tuple(
        sorted(
            tuple(
                (axis, tuple(sorted(set(values))))
                for axis, values in sorted(selector.items())
            )
            for selector in selectors
        )
    )


def _authority_matches(scope: dict, skill: dict) -> bool:
    reviewed_ownership = {
        capability: set(phases)
        for capability, phases in scope["ownership"].items()
    }
    return (
        set(scope["phases"]) == set(skill["phases"])
        and reviewed_ownership == _ownership_map(skill)
        and _canonical_selectors(scope["selectors"])
        == _canonical_selectors(skill.get("selectors"))
    )


def _review_attestation_digest(
    skill: dict, entry: dict, installed_skill: dict
) -> str:
    claims = {
        field: value
        for field, value in entry.items()
        if field != "evaluation_evidence"
    }
    claims["reviewer"] = _validate_reviewer(entry["reviewer"], skill["name"])
    canonical = json.dumps(
        {
            "admitted_skill": skill,
            "installed_skill": installed_skill,
            "registry_claims": claims,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validate_registry_bindings(
    registry: object,
    skills: list[dict],
    suite_version: str,
    installed_skills: dict[str, dict],
) -> None:
    if registry is None:
        return
    by_name = {skill["name"]: skill for skill in skills}
    seen: set[str] = set()
    for entry in registry["skills"]:
        name = entry["name"]
        if name in seen:
            raise ValueError(f"duplicate compatibility registry skill: {name}")
        seen.add(name)
        installed_skill = installed_skills.get(name)
        if installed_skill is None:
            continue
        skill = by_name[name]
        if not _constraint_satisfied(
            skill["version"], entry["version_constraint"], "version_constraint", name
        ):
            raise ValueError(f"compatibility registry version mismatch: {name}")
        if not _constraint_satisfied(
            suite_version,
            entry["compatible_orchestrator_version"],
            "compatible_orchestrator_version",
            name,
        ):
            raise ValueError(f"compatibility registry suite mismatch: {name}")
        if set(entry["capabilities"]) != set(skill["capabilities"]):
            raise ValueError(f"compatibility registry capabilities mismatch: {name}")
        if (
            set(entry["dependencies"]) != set(skill["depends_on"])
            or set(entry["conflicts"]) != set(skill["conflicts"])
            or set(entry["supersedes"]) != set(skill["supersedes"])
        ):
            raise ValueError(f"compatibility registry relationships mismatch: {name}")
        if not _authority_matches(entry["authority_scope"], skill):
            raise ValueError(f"compatibility registry authority mismatch: {name}")
        _validate_review_date(entry["review_date"], name)
        expected_evidence = _review_attestation_digest(
            skill, entry, installed_skill
        )
        if entry["evaluation_evidence"] != [expected_evidence]:
            raise ValueError(f"compatibility registry evidence mismatch: {name}")


def _portable_relative_path(relative: Path, seen: set[str], name: str) -> str:
    parts = relative.parts
    if not parts or any(
        part in {"", ".", ".."}
        or "\\" in part
        or unicodedata.normalize("NFC", part) != part
        or any(unicodedata.category(character).startswith("C") for character in part)
        for part in parts
    ):
        raise ValueError(f"unsafe installed skill tree path: {name}")
    canonical = relative.as_posix()
    collision_key = canonical.casefold()
    if collision_key in seen:
        raise ValueError(f"ambiguous installed skill tree path: {name}")
    seen.add(collision_key)
    return canonical


def _installed_tree(installed: Path, name: str) -> tuple[list[dict], dict[str, bytes]]:
    inventory: list[dict] = []
    contents: dict[str, bytes] = {}
    seen: set[str] = set()
    total_size = 0

    def visit(directory: Path) -> None:
        nonlocal total_size
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            raise ValueError(f"unsafe installed skill tree: {name}") from None
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(installed)
            canonical = _portable_relative_path(relative, seen, name)
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                raise ValueError(f"unsafe installed skill tree: {name}") from None
            if stat.S_ISLNK(mode):
                raise ValueError(f"unsafe installed skill tree symlink: {name}: {canonical}")
            if stat.S_ISDIR(mode):
                visit(path)
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(f"unsafe installed skill tree special file: {name}: {canonical}")
            size = entry.stat(follow_symlinks=False).st_size
            if size > MAX_INSTALLED_FILE_BYTES:
                raise ValueError(f"installed skill file is too large: {name}: {canonical}")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags)
                with os.fdopen(descriptor, "rb") as handle:
                    data = handle.read(MAX_INSTALLED_FILE_BYTES + 1)
            except OSError:
                raise ValueError(f"unsafe installed skill tree: {name}") from None
            if len(data) != size or len(data) > MAX_INSTALLED_FILE_BYTES:
                raise ValueError(f"installed skill changed during verification: {name}")
            total_size += len(data)
            if total_size > MAX_INSTALLED_TREE_BYTES:
                raise ValueError(f"installed skill tree is too large: {name}")
            contents[canonical] = data
            inventory.append(
                {
                    "path": canonical,
                    "sha256": f"sha256:{hashlib.sha256(data).hexdigest()}",
                    "size": len(data),
                }
            )
            if len(inventory) > MAX_INSTALLED_FILES:
                raise ValueError(f"installed skill tree has too many files: {name}")

    visit(installed)
    inventory.sort(key=lambda item: item["path"])
    return inventory, contents


def _verify_installed_skill(
    skill: dict, installed_roots: list[Path]
) -> tuple[dict, dict[str, bytes]]:
    name = skill["name"]
    validate_name(name, "external skill")
    matches: list[Path] = []
    for root in installed_roots:
        try:
            resolved_root = root.resolve(strict=True)
        except OSError:
            raise ValueError(f"installed root is unavailable: {root}") from None
        candidate = resolved_root / name
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        skill_path = candidate / "SKILL.md"
        manifest_path = candidate / "capability-manifest.json"
        if skill_path.is_symlink() or manifest_path.is_symlink():
            continue
        if skill_path.is_file() and manifest_path.is_file():
            matches.append(candidate)
    if not matches:
        raise ValueError(f"external skill is not installed: {name}")
    if len(matches) != 1:
        raise ValueError(f"external skill installation is ambiguous: {name}")
    installed = matches[0]
    inventory, contents = _installed_tree(installed, name)
    try:
        skill_bytes = contents["SKILL.md"]
        manifest_bytes = contents["capability-manifest.json"]
    except KeyError:
        raise ValueError(f"external skill installation is incomplete: {name}") from None
    try:
        skill_text = skill_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(
            f"installed skill has invalid UTF-8 content: {name}"
        ) from None
    frontmatter = parse_frontmatter_text(skill_text, label="SKILL.md")
    if frontmatter.get("name") != name or frontmatter.get("description") != skill["description"]:
        raise ValueError(f"installed skill identity does not match catalog: {name}")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"installed skill manifest is invalid: {name}") from None
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "skill"}
        or manifest.get("schema_version") != 2
        or manifest.get("skill") != skill
    ):
        raise ValueError(f"installed skill manifest does not match catalog: {name}")
    tree_canonical = json.dumps(
        inventory,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "name": name,
        "tree_sha256": f"sha256:{hashlib.sha256(tree_canonical).hexdigest()}",
        "files": inventory,
    }, contents


def _snapshot_installed_skill(
    name: str,
    attestation: dict,
    contents: dict[str, bytes],
    snapshot_root: Path | None,
) -> dict:
    if snapshot_root is not None:
        snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if snapshot_root.is_symlink() or not snapshot_root.is_dir():
            raise ValueError(f"external snapshot root is unsafe: {snapshot_root}")
        container = Path(
            tempfile.mkdtemp(prefix="translation-admitted-", dir=snapshot_root)
        )
    else:
        container = Path(tempfile.mkdtemp(prefix="translation-admitted-"))
    os.chmod(container, 0o700)
    snapshot = container / name
    snapshot.mkdir(mode=0o700)
    try:
        directories = {snapshot}
        for item in attestation["files"]:
            relative = Path(item["path"])
            destination = snapshot / relative
            pending = destination.parent
            missing = []
            while pending != snapshot and not pending.exists():
                missing.append(pending)
                pending = pending.parent
            for directory in reversed(missing):
                directory.mkdir(mode=0o700)
                directories.add(directory)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(destination, flags, 0o400)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    handle.write(contents[item["path"]])
                    handle.flush()
                    os.fsync(handle.fileno())
                os.fchmod(descriptor, 0o400)
            finally:
                os.close(descriptor)
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            os.chmod(directory, 0o500)
        verified_inventory, _ = _installed_tree(snapshot, name)
        if verified_inventory != attestation["files"]:
            raise ValueError(f"external skill snapshot verification failed: {name}")
        os.chmod(container, 0o500)
    except Exception:
        for path in sorted(snapshot.rglob("*"), reverse=True):
            if path.is_dir():
                path.chmod(0o700)
            else:
                path.chmod(0o600)
        snapshot.chmod(0o700)
        import shutil

        shutil.rmtree(container, ignore_errors=True)
        raise
    return {
        "name": name,
        "load_path": str(snapshot),
        "tree_sha256": attestation["tree_sha256"],
    }


def merge_external_catalogs(
    bundled_catalog: dict,
    external_catalogs: list[object],
    request: dict,
    registry: object,
    installed_roots: list[Path],
    snapshot_root: Path | None = None,
) -> dict:
    """Merge authorized installed external records without changing bundled precedence."""
    authorized = _authorization_names(request, registry)
    merged = dict(bundled_catalog)
    merged_skills = list(bundled_catalog["skills"])
    known_names = {item["name"] for item in merged_skills}
    external_names: set[str] = set()
    installed_skills: dict[str, dict] = {}
    installed_contents: dict[str, dict[str, bytes]] = {}
    for external_catalog in external_catalogs:
        for skill in validate_external_catalog(external_catalog):
            name = skill["name"]
            if name not in authorized:
                raise ValueError(f"unauthorized external skill: {name}")
            if name in known_names:
                raise ValueError(f"duplicate external skill name: {name}")
            attestation, contents = _verify_installed_skill(skill, installed_roots)
            installed_skills[name] = attestation
            installed_contents[name] = contents
            known_names.add(name)
            external_names.add(name)
            merged_skills.append(skill)
    merged["skills"] = merged_skills
    _validate_merged_relationships(merged_skills, external_names)
    _validate_registry_bindings(
        registry,
        merged_skills,
        bundled_catalog["suite_version"],
        installed_skills,
    )
    merged["external_skill_snapshots"] = {
        name: _snapshot_installed_skill(
            name,
            installed_skills[name],
            installed_contents[name],
            snapshot_root,
        )
        for name in installed_skills
    }
    return merged


def _normalized_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")
    normalized = dict(profile)
    for field in ("source_locale", "target_locale", "language"):
        value = normalized.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
    normalized["source_locale"] = normalize_locale(normalized["source_locale"])
    normalized["target_locale"] = normalize_locale(normalized["target_locale"])
    normalized["language"] = normalized["language"].casefold()
    for axis in ROUTING_AXES:
        if axis in ("languages", "locales"):
            continue
        values = normalized.get(axis, [])
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise ValueError(f"{axis} must be a list of strings")
        normalized[axis] = values
    normalized["capabilities"] = list(
        dict.fromkeys((*MANDATORY_CAPABILITIES, *normalized["capabilities"]))
    )
    return normalized


def _nonblank_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must contain unique values")
    return list(value)


def normalize_request(request: dict) -> tuple[dict, ...]:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    if type(request.get("schema_version")) is not int or request["schema_version"] != 2:
        raise ValueError("request must use schema_version 2")
    unknown = sorted(set(request) - REQUEST_FIELDS)
    if unknown:
        raise ValueError(f"unknown request field: $.{unknown[0]}")

    source_locale = normalize_locale(
        _nonblank_string(request.get("source_locale"), "source_locale")
    )
    targets = request.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("targets must be a non-empty list")

    shared_lists = {
        field: _string_list(request.get(field, []), field)
        for field in SHARED_LIST_FIELDS
    }
    shared_text = {
        field: _nonblank_string(request.get(field), field) for field in TEXT_FIELDS
    }
    for field in EXTERNAL_AUTHORIZATION_FIELDS:
        _string_list(request.get(field, []), field)
    profiles = []
    target_locales = set()
    for target_index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise ValueError("target must be an object")
        unknown_target = sorted(set(target) - TARGET_FIELDS)
        if unknown_target:
            raise ValueError(
                f"unknown request field: $.targets[{target_index}].{unknown_target[0]}"
            )
        target_locale = normalize_locale(
            _nonblank_string(target.get("locale"), "target locale")
        )
        if target_locale in target_locales:
            raise ValueError(f"duplicate target locale: {target_locale}")
        target_locales.add(target_locale)

        target_lists = {
            field: _string_list(target.get(field, []), field)
            for field in TARGET_LIST_FIELDS
        }
        register = _nonblank_string(
            target.get("register", shared_text["register"]), "register"
        )
        capabilities = list(
            dict.fromkeys((*MANDATORY_CAPABILITIES, *shared_lists["capabilities"]))
        )
        profiles.append(
            {
                "source_locale": source_locale,
                "target_locale": target_locale,
                "language": language_from_locale(target_locale),
                **{
                    field: list(values)
                    for field, values in shared_lists.items()
                    if field != "capabilities"
                },
                **{field: list(values) for field, values in target_lists.items()},
                "capabilities": capabilities,
                "audience": shared_text["audience"],
                "purpose": shared_text["purpose"],
                "register": register,
            }
        )
    return tuple(profiles)


def matching_skill_names(
    profile: dict, catalog: dict
) -> tuple[list[str], dict[str, list[str]]]:
    selected = []
    reasons: dict[str, list[str]] = {}
    for item in catalog["skills"]:
        if "selectors" not in item:
            selected.append(item["name"])
            reasons[item["name"]] = ["selector:universal"]
            continue
        matched = [
            index
            for index, selector in enumerate(item["selectors"])
            if selector_matches(profile, selector)
        ]
        if matched:
            selected.append(item["name"])
            reasons[item["name"]] = [f"selector:{index}" for index in matched]
    return selected, reasons


def expand_dependencies(
    selected: set[str],
    by_name: dict[str, dict],
    reasons: dict[str, list[str]],
) -> None:
    pending = [name for name in reversed(by_name) if name in selected]
    while pending:
        name = pending.pop()
        for dependency in by_name[name]["depends_on"]:
            if dependency not in by_name:
                raise ValueError(f"unknown dependency: {dependency}")
            reasons.setdefault(dependency, []).append(f"dependency-of:{name}")
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)


def _ownership_map(skill: dict, name: str | None = None) -> dict[str, set[str]]:
    raw = skill.get("ownership")
    if raw is None:
        return {capability: set(skill["phases"]) for capability in skill["capabilities"]}
    label = name or skill["name"]
    if not isinstance(raw, dict) or set(raw) != set(skill["capabilities"]):
        raise ValueError(
            f"external skill {label} ownership must map every declared capability"
        )
    ownership = {}
    for capability, phases in raw.items():
        if not isinstance(capability, str) or capability not in skill["capabilities"]:
            raise ValueError(f"external skill {label} owns undeclared capability")
        values = _external_string_list(phases, "ownership phases", label)
        if not values or not set(values).issubset(skill["phases"]):
            raise ValueError(
                f"external skill {label} ownership phases must be declared phases"
            )
        ownership[capability] = set(values)
    return ownership


def apply_supersedes(
    selected: set[str],
    by_name: dict[str, dict],
    reasons: dict[str, list[str]],
) -> tuple[set[str], set[str], dict[str, list[dict[str, object]]]]:
    removed: set[str] = set()
    ownership_overrides: dict[str, list[dict[str, object]]] = {}
    for name in by_name:
        if name not in selected:
            continue
        for target in by_name[name]["supersedes"]:
            if target not in selected:
                continue
            if by_name[name]["category"] != by_name[target]["category"]:
                raise ValueError(f"invalid cross-category supersedes: {name}, {target}")
            if SPECIFICITY_ORDER[by_name[name]["specificity"]] <= SPECIFICITY_ORDER[
                by_name[target]["specificity"]
            ]:
                raise ValueError(f"invalid supersedes specificity: {name}, {target}")
            replacement_ownership = _ownership_map(by_name[name])
            target_ownership = _ownership_map(by_name[target])
            shared_ownership = {
                capability: sorted(replacement_ownership[capability] & phases)
                for capability, phases in target_ownership.items()
                if capability in replacement_ownership
                and replacement_ownership[capability] & phases
            }
            if not shared_ownership:
                raise ValueError(
                    f"supersedes without shared ownership: {name}, {target}"
                )
            if all(
                set(phases).issubset(replacement_ownership.get(capability, set()))
                for capability, phases in target_ownership.items()
            ):
                removed.add(target)
                ownership_overrides.setdefault(name, []).append(
                    {"skill": target, "ownership": shared_ownership}
                )
                reasons[name].append(f"supersedes:{target}")
            else:
                ownership_overrides.setdefault(name, []).append(
                    {"skill": target, "ownership": shared_ownership}
                )
                reasons[name].append(
                    f"refines:{target}:"
                    + ",".join(
                        f"{capability}@{'/'.join(phases)}"
                        for capability, phases in sorted(shared_ownership.items())
                    )
                )
    return selected - removed, removed, ownership_overrides


def validate_dependency_closure(
    selected: set[str], by_name: dict[str, dict]
) -> None:
    for name in by_name:
        if name not in selected:
            continue
        for dependency in by_name[name]["depends_on"]:
            if dependency not in selected:
                raise ValueError(f"missing selected dependency: {name}, {dependency}")


def resolve_selection(
    names: list[str],
    by_name: dict[str, dict],
    reasons: dict[str, list[str]],
) -> tuple[
    set[str], set[str], dict[str, list[dict[str, object]]]
]:
    direct_matches = set(names)
    while True:
        selected = set(direct_matches)
        expand_dependencies(selected, by_name, reasons)
        execution_selected, fully_overridden, ownership_overrides = apply_supersedes(
            selected, by_name, reasons
        )
        load_selected = set(execution_selected)
        expand_dependencies(load_selected, by_name, reasons)
        load_only = load_selected & fully_overridden
        ownership_overrides = {
            owner: [
                override
                for override in overrides
                if override["skill"] in execution_selected or override["skill"] in load_only
            ]
            for owner, overrides in ownership_overrides.items()
            if owner in execution_selected
        }
        ownership_overrides = {
            owner: overrides
            for owner, overrides in ownership_overrides.items()
            if overrides
        }
        validate_dependency_closure(load_selected, by_name)
        removed_direct_matches = direct_matches - load_selected
        if not removed_direct_matches:
            return load_selected, load_only, ownership_overrides
        direct_matches.difference_update(removed_direct_matches)


def validate_conflicts(selected: set[str], by_name: dict[str, dict]) -> None:
    for name in by_name:
        if name not in selected:
            continue
        for conflict in by_name[name]["conflicts"]:
            if conflict in selected:
                raise ValueError(f"conflicting skills: {name}, {conflict}")


def ordered_for_phase(phase: str, selected: set[str], catalog: dict) -> list[str]:
    by_name = {item["name"]: item for item in catalog["skills"]}
    manifest_index = {
        item["name"]: index for index, item in enumerate(catalog["skills"])
    }
    pending = {
        name for name in selected if phase in by_name[name]["phases"]
    }
    ordered = []
    while pending:
        ready = [
            name
            for name in pending
            if not set(by_name[name]["depends_on"]).intersection(pending)
        ]
        if not ready:
            raise ValueError(f"dependency cycle in phase: {phase}")
        ready.sort(
            key=lambda name: (
                SPECIFICITY_ORDER[by_name[name]["specificity"]],
                manifest_index[name],
            )
        )
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


def ordered_for_load(selected: set[str], catalog: dict) -> list[str]:
    by_name = {item["name"]: item for item in catalog["skills"]}
    manifest_index = {
        item["name"]: index for index, item in enumerate(catalog["skills"])
    }
    phase_index = {phase: index for index, phase in enumerate(LOAD_PHASES)}
    load_phase = {
        name: min(phase_index[phase] for phase in by_name[name]["phases"])
        for name in selected
    }
    pending = set(selected)
    ordered: list[str] = []
    while pending:
        ready = [
            name
            for name in pending
            if not set(by_name[name]["depends_on"]).intersection(pending)
        ]
        if not ready:
            raise ValueError("dependency cycle in selected load order")
        ready.sort(
            key=lambda name: (
                load_phase[name],
                manifest_index[name],
                name,
            )
        )
        chosen = ready[0]
        ordered.append(chosen)
        pending.remove(chosen)
    return ordered


def route_profile(profile: dict, catalog: dict) -> dict:
    profile = _normalized_profile(profile)
    names, reasons = matching_skill_names(profile, catalog)
    by_name = {item["name"]: item for item in catalog["skills"]}
    selected, load_only, ownership_overrides = resolve_selection(names, by_name, reasons)
    validate_conflicts(selected, by_name)
    for name in by_name:
        if name not in selected:
            continue
        for field in by_name[name]["required_context"]:
            if not profile.get(field):
                raise ValueError(f"{name} requires context: {field}")
    execution_selected = selected - load_only
    phase_plan = {
        phase: ordered_for_phase(phase, execution_selected, catalog)
        for phase in PHASES
    }
    selected_in_load_order = ordered_for_load(selected, catalog)
    external_snapshots = catalog.get("external_skill_snapshots", {})
    return {
        "target_locale": profile["target_locale"],
        "language": profile["language"],
        "selected": selected_in_load_order,
        "load_only": [name for name in selected_in_load_order if name in load_only],
        "external_loads": [
            external_snapshots[name]
            for name in selected_in_load_order
            if name in external_snapshots
        ],
        "phases": phase_plan,
        "reasons": {
            name: sorted(set(reasons[name])) for name in selected_in_load_order
        },
        "ownership_overrides": ownership_overrides,
    }


def route(request: dict, catalog: dict) -> dict:
    profiles = normalize_request(request)
    routes = []
    for profile in profiles:
        try:
            routes.append(route_profile(profile, catalog))
        except ValueError as error:
            target_locale = profile["target_locale"]
            raise ValueError(f"target locale {target_locale}: {error}") from error
    return {
        "schema_version": 2,
        "routes": routes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Route bundled and already-installed external translation skills."
    )
    parser.add_argument("request_json", help="schema-2 routing request JSON path")
    parser.add_argument(
        "--external-catalog",
        action="append",
        default=[],
        metavar="PATH",
        help="installed external schema-2 catalog or manifest; repeat for more inputs",
    )
    parser.add_argument(
        "--compatibility-registry",
        metavar="PATH",
        help="reviewed schema-2 registry that authorizes installed external skills",
    )
    parser.add_argument(
        "--installed-root",
        action="append",
        default=[],
        metavar="PATH",
        help="root containing installed <skill-name>/SKILL.md and capability-manifest.json",
    )
    parser.add_argument(
        "--snapshot-root",
        metavar="PATH",
        help="private parent for immutable admitted external-skill snapshots",
    )
    arguments = parser.parse_args()

    try:
        request = json.loads(Path(arguments.request_json).read_text(encoding="utf-8"))
        catalog_path = (
            Path(__file__).resolve().parents[1]
            / "references"
            / "capability-catalog.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        external_catalogs = [
            json.loads(Path(path).read_text(encoding="utf-8"))
            for path in arguments.external_catalog
        ]
        registry = (
            json.loads(Path(arguments.compatibility_registry).read_text(encoding="utf-8"))
            if arguments.compatibility_registry
            else None
        )
        admitted_catalog = merge_external_catalogs(
            catalog,
            external_catalogs,
            request,
            registry,
            [Path(path) for path in arguments.installed_root],
            Path(arguments.snapshot_root) if arguments.snapshot_root else None,
        )
        result = route(request, admitted_catalog)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"invalid request: {error}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
