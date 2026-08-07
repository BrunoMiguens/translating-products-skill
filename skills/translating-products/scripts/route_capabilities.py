from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


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
    skills = catalog.get("skills")
    if skills is None and isinstance(catalog.get("skill"), dict):
        skills = [catalog["skill"]]
    if not isinstance(skills, list):
        raise ValueError("external catalog skills must be a list")

    names: set[str] = set()
    for item in skills:
        if not isinstance(item, dict):
            raise ValueError("external catalog skill must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("external catalog skill name must be a non-empty string")
        missing = sorted(EXTERNAL_SKILL_FIELDS - set(item))
        if missing:
            raise ValueError(
                f"external skill {name} is missing fields: {', '.join(missing)}"
            )
        unknown = sorted(
            set(item) - EXTERNAL_SKILL_FIELDS - EXTERNAL_OPTIONAL_FIELDS
        )
        if unknown:
            raise ValueError(
                f"external skill {name} has unknown fields: {', '.join(unknown)}"
            )
        if name in names:
            raise ValueError(f"duplicate external skill name: {name}")
        names.add(name)
        for field in ("version", "category", "description", "specificity"):
            if not isinstance(item[field], str) or not item[field].strip():
                raise ValueError(f"external skill {name} has invalid {field}")
        if item["specificity"] not in SPECIFICITY_ORDER:
            raise ValueError(f"external skill {name} has invalid specificity")
        for field in ("capabilities", "phases"):
            if item[field] == []:
                raise ValueError(f"external skill {name} requires non-empty {field}")
            _external_string_list(item[field], field, name)
        for field in ("depends_on", "required_context", "conflicts", "supersedes"):
            _external_string_list(item[field], field, name, allow_empty=True)
        if "selectors" in item:
            if not item["selectors"]:
                raise ValueError(f"external skill {name} must declare selectors")
            if not isinstance(item["selectors"], list):
                raise ValueError(f"external skill {name} has invalid selectors")
            for selector in item["selectors"]:
                if not isinstance(selector, dict) or not selector:
                    raise ValueError(f"external skill {name} has invalid selector")
                for axis, values in selector.items():
                    if axis not in ROUTING_AXES:
                        raise ValueError(
                            f"external skill {name} has unknown selector axis: {axis}"
                        )
                    _external_string_list(values, f"selector {axis}", name)
        unknown_phases = sorted(set(item["phases"]) - set(PHASES))
        if unknown_phases:
            raise ValueError(
                f"external skill {name} has unknown phases: {', '.join(unknown_phases)}"
            )
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


def _validate_reviewer(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(
        character.isspace() for character in value
    ):
        raise ValueError(f"compatibility registry skill {name} has invalid reviewer")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"compatibility registry skill {name} has invalid reviewer")
    return value


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


def _review_attestation_digest(skill: dict, entry: dict) -> str:
    claims = {
        field: value
        for field, value in entry.items()
        if field != "evaluation_evidence"
    }
    canonical = json.dumps(
        {"admitted_skill": skill, "registry_claims": claims},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validate_registry_bindings(
    registry: object, skills: list[dict], suite_version: str
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
        skill = by_name.get(name)
        if skill is None:
            raise ValueError(f"compatibility registry references unknown skill: {name}")
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
        expected_evidence = _review_attestation_digest(skill, entry)
        if entry["evaluation_evidence"] != [expected_evidence]:
            raise ValueError(f"compatibility registry evidence mismatch: {name}")


def _frontmatter_values(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"installed skill has invalid frontmatter: {path.name}")
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return values
        if ":" not in line:
            raise ValueError(f"installed skill has invalid frontmatter: {path.name}")
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    raise ValueError(f"installed skill has invalid frontmatter: {path.name}")


def _verify_installed_skill(skill: dict, installed_roots: list[Path]) -> None:
    name = skill["name"]
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError(f"external skill has unsafe name: {name}")
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
    frontmatter = _frontmatter_values(installed / "SKILL.md")
    if frontmatter.get("name") != name or frontmatter.get("description") != skill["description"]:
        raise ValueError(f"installed skill identity does not match catalog: {name}")
    manifest = json.loads((installed / "capability-manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 2
        or manifest.get("skill") != skill
    ):
        raise ValueError(f"installed skill manifest does not match catalog: {name}")


def merge_external_catalogs(
    bundled_catalog: dict,
    external_catalogs: list[object],
    request: dict,
    registry: object,
    installed_roots: list[Path],
) -> dict:
    """Merge authorized installed external records without changing bundled precedence."""
    authorized = _authorization_names(request, registry)
    merged = dict(bundled_catalog)
    merged_skills = list(bundled_catalog["skills"])
    known_names = {item["name"] for item in merged_skills}
    external_names: set[str] = set()
    for external_catalog in external_catalogs:
        for skill in validate_external_catalog(external_catalog):
            name = skill["name"]
            if name not in authorized:
                raise ValueError(f"unauthorized external skill: {name}")
            if name in known_names:
                raise ValueError(f"duplicate external skill name: {name}")
            _verify_installed_skill(skill, installed_roots)
            known_names.add(name)
            external_names.add(name)
            merged_skills.append(skill)
    merged["skills"] = merged_skills
    _validate_merged_relationships(merged_skills, external_names)
    _validate_registry_bindings(registry, merged_skills, bundled_catalog["suite_version"])
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
    return list(value)


def normalize_request(request: dict) -> tuple[dict, ...]:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")

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
    profiles = []
    target_locales = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("target must be an object")
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
) -> tuple[set[str], dict[str, list[dict[str, object]]]]:
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
    return selected - removed, ownership_overrides


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
) -> tuple[set[str], dict[str, list[dict[str, object]]]]:
    direct_matches = set(names)
    while True:
        selected = set(direct_matches)
        expand_dependencies(selected, by_name, reasons)
        retained, ownership_overrides = apply_supersedes(selected, by_name, reasons)
        validate_dependency_closure(retained, by_name)
        removed_direct_matches = direct_matches - retained
        if not removed_direct_matches:
            return retained, ownership_overrides
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


def route_profile(profile: dict, catalog: dict) -> dict:
    profile = _normalized_profile(profile)
    names, reasons = matching_skill_names(profile, catalog)
    by_name = {item["name"]: item for item in catalog["skills"]}
    selected, ownership_overrides = resolve_selection(names, by_name, reasons)
    validate_conflicts(selected, by_name)
    for name in by_name:
        if name not in selected:
            continue
        for field in by_name[name]["required_context"]:
            if not profile.get(field):
                raise ValueError(f"{name} requires context: {field}")
    phase_plan = {
        phase: ordered_for_phase(phase, selected, catalog) for phase in PHASES
    }
    selected_in_load_order = []
    for phase in LOAD_PHASES:
        for name in phase_plan[phase]:
            if name not in selected_in_load_order:
                selected_in_load_order.append(name)
    return {
        "target_locale": profile["target_locale"],
        "language": profile["language"],
        "selected": selected_in_load_order,
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
        )
        result = route(request, admitted_catalog)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"invalid request: {error}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
