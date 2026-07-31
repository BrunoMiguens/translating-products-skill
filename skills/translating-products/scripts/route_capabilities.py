from __future__ import annotations

import json
from pathlib import Path
import sys


SURFACE_SKILLS = {
    "web": "translating-web",
    "software": "localizing-software",
    "mobile": "translating-mobile",
    "ios": "translating-ios",
    "android": "translating-android",
    "flutter": "translating-flutter",
    "app-store": "translating-app-stores",
    "play-store": "translating-app-stores",
    "marketing": "translating-marketing",
    "documentation": "translating-documentation",
}

CLASSIFIED_FIELDS = ("languages", "locales", "surfaces", "domains", "scripts")


def route(request: dict, catalog: dict) -> list[str]:
    for field in CLASSIFIED_FIELDS:
        value = request.get(field, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(f"{field} must be a list of strings")

    entries = catalog["skills"]
    by_name = {item["name"]: item for item in entries}
    selected = {"translating-core", "reviewing-translations"}

    for surface in request.get("surfaces", []):
        if surface in SURFACE_SKILLS:
            selected.add(SURFACE_SKILLS[surface])
    if selected & {
        "translating-ios",
        "translating-android",
        "translating-flutter",
    }:
        selected.add("translating-mobile")
    if "rtl" in request.get("scripts", []):
        selected.add("translating-rtl")

    requested = {
        f"language:{language.casefold()}"
        for language in request.get("languages", [])
    }
    for item in entries:
        if requested.intersection(item["capabilities"]):
            selected.add(item["name"])

    pending = list(selected)
    while pending:
        name = pending.pop()
        for dependency in by_name[name]["depends_on"]:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)

    ordered = [
        item["name"]
        for item in entries
        if item["name"] in selected
        and item["name"]
        not in {"translating-products", "reviewing-translations"}
    ]
    return ordered + ["reviewing-translations"]


def main() -> int:
    if len(sys.argv) != 2:
        print("invalid request: expected one request JSON path", file=sys.stderr)
        return 2

    try:
        request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("top-level JSON must be an object")
        catalog_path = (
            Path(__file__).resolve().parents[1]
            / "references"
            / "capability-catalog.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        result = route(request, catalog)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"invalid request: {error}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
