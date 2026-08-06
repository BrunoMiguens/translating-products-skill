from __future__ import annotations

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
MANDATORY_CAPABILITIES = ("core-translation", "translation-qa")


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


def apply_supersedes(
    selected: set[str],
    by_name: dict[str, dict],
    reasons: dict[str, list[str]],
) -> set[str]:
    removed: set[str] = set()
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
            removed.add(target)
            reasons[name].append(f"supersedes:{target}")
    return selected - removed


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
) -> set[str]:
    direct_matches = set(names)
    while True:
        selected = set(direct_matches)
        expand_dependencies(selected, by_name, reasons)
        retained = apply_supersedes(selected, by_name, reasons)
        validate_dependency_closure(retained, by_name)
        removed_direct_matches = direct_matches - retained
        if not removed_direct_matches:
            return retained
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
    selected = resolve_selection(names, by_name, reasons)
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
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("invalid profile: expected one profile JSON path", file=sys.stderr)
        return 2

    try:
        profile = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        catalog_path = (
            Path(__file__).resolve().parents[1]
            / "references"
            / "capability-catalog.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        result = route_profile(profile, catalog)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"invalid profile: {error}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
