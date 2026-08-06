from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


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
        "selectors",
        "phases",
        "specificity",
        "required_context",
        "conflicts",
        "supersedes",
    )
)
EXTERNAL_OPTIONAL_FIELDS = frozenset(("ownership",))
EXTERNAL_AUTHORIZATION_FIELDS = (
    "authorized_external_skills",
    "project_authorized_external_skills",
)


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


def _external_string_list(value: object, field: str, skill_name: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(
            f"external skill {skill_name} has invalid {field}: expected string list"
        )
    return value


def validate_external_catalog(catalog: object) -> list[dict]:
    """Validate one installed external manifest or capability catalog."""
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 2:
        raise ValueError("external catalog must use schema_version 2")
    skills = catalog.get("skills")
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
        for field in (
            "capabilities",
            "depends_on",
            "phases",
            "required_context",
            "conflicts",
            "supersedes",
        ):
            _external_string_list(item[field], field, name)
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
        ownership = item.get("ownership")
        if item["supersedes"] and not ownership:
            raise ValueError(
                f"external skill {name} must declare ownership when superseding"
            )
        if ownership is not None:
            ownership = _external_string_list(ownership, "ownership", name)
            unknown_ownership = sorted(set(ownership) - set(item["capabilities"]))
            if unknown_ownership:
                raise ValueError(
                    f"external skill {name} owns undeclared capabilities: "
                    + ", ".join(unknown_ownership)
                )
    return skills


def _authorization_names(request: dict, registry: object) -> set[str]:
    authorized: set[str] = set()
    for field in EXTERNAL_AUTHORIZATION_FIELDS:
        if field not in request:
            continue
        values = _external_string_list(request[field], field, "request")
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
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("compatibility registry skill name must be a non-empty string")
        authorized.add(name)
    return authorized


def merge_external_catalogs(
    bundled_catalog: dict, external_catalogs: list[object], request: dict, registry: object
) -> dict:
    """Merge authorized installed external records without changing bundled precedence."""
    authorized = _authorization_names(request, registry)
    merged = dict(bundled_catalog)
    merged_skills = list(bundled_catalog["skills"])
    known_names = {item["name"] for item in merged_skills}
    for external_catalog in external_catalogs:
        for skill in validate_external_catalog(external_catalog):
            name = skill["name"]
            if name not in authorized:
                raise ValueError(f"unauthorized external skill: {name}")
            if name in known_names:
                raise ValueError(f"duplicate external skill name: {name}")
            known_names.add(name)
            merged_skills.append(skill)
    merged["skills"] = merged_skills
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


def _ownership(skill: dict) -> set[str]:
    return set(skill.get("ownership", skill["capabilities"]))


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
            shared_ownership = sorted(_ownership(by_name[name]) & _ownership(by_name[target]))
            if not shared_ownership:
                raise ValueError(
                    f"supersedes without shared ownership: {name}, {target}"
                )
            if _ownership(by_name[target]).issubset(_ownership(by_name[name])):
                removed.add(target)
                reasons[name].append(f"supersedes:{target}")
            else:
                ownership_overrides.setdefault(name, []).append(
                    {"skill": target, "ownership": shared_ownership}
                )
                reasons[name].append(
                    f"refines:{target}:{','.join(shared_ownership)}"
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
            catalog, external_catalogs, request, registry
        )
        result = route(request, admitted_catalog)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"invalid request: {error}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
