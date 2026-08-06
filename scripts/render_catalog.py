from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CATEGORIES = (
    "orchestrator",
    "core",
    "quality",
    "surface",
    "platform",
    "script",
    "language",
)

AUTHORITY_ORDER = (
    "explicit-user-requirements",
    "approved-project-configuration",
    "core-semantic-fidelity",
    "domain-terminology",
    "language-locale-mechanics",
    "product-platform-formatting",
    "stylistic-preferences",
)
INVENTORY_START = "<!-- skill-inventory:start -->"
INVENTORY_END = "<!-- skill-inventory:end -->"
ROUTING_FIELDS = (
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
    "ownership",
)


class InventoryMarkerError(ValueError):
    """A README inventory marker contract is malformed."""


def split_readme_inventory(text: str) -> tuple[str, str, str]:
    start_count = text.count(INVENTORY_START)
    end_count = text.count(INVENTORY_END)
    if start_count == 0:
        raise InventoryMarkerError("skill inventory start marker is missing")
    if start_count != 1:
        raise InventoryMarkerError(
            f"skill inventory start marker appears {start_count} times"
        )
    if end_count == 0:
        raise InventoryMarkerError("skill inventory end marker is missing")
    if end_count != 1:
        raise InventoryMarkerError(
            f"skill inventory end marker appears {end_count} times"
        )

    start = text.index(INVENTORY_START)
    end = text.index(INVENTORY_END)
    if start > end:
        raise InventoryMarkerError("skill inventory markers are reversed")
    before = text[:start]
    inventory = text[start + len(INVENTORY_START) : end]
    after = text[end + len(INVENTORY_END) :]
    return before, inventory, after


def _catalog_from_manifest(manifest: dict) -> dict:
    return {
        "schema_version": 2,
        "suite_version": manifest["suite_version"],
        "skills": [
            {
                field: (
                    item.get(
                        "ownership",
                        {capability: item["phases"] for capability in item["capabilities"]},
                    )
                    if field == "ownership"
                    else item[field]
                )
                for field in ROUTING_FIELDS
            }
            for item in manifest["skills"]
        ],
    }


def _render_markdown(catalog: dict) -> str:
    lines = [
        "# Capability catalog",
        "",
        f"Suite version: `{catalog['suite_version']}`",
        "",
        "## Authority order",
        "",
    ]
    lines.extend(
        f"{index}. `{authority}`"
        for index, authority in enumerate(AUTHORITY_ORDER, start=1)
    )
    lines.append("")

    category = None
    for item in catalog["skills"]:
        if item["category"] != category:
            category = item["category"]
            lines.extend((f"## {category}", ""))
        capabilities = ", ".join(
            f"`{capability}`" for capability in item["capabilities"]
        ) or "none"
        dependencies = ", ".join(
            f"`{dependency}`" for dependency in item["depends_on"]
        ) or "none"
        selectors = " OR ".join(
            ", ".join(
                f"`{axis}`: {', '.join(f'`{value}`' for value in values)}"
                for axis, values in selector.items()
            )
            for selector in item["selectors"]
        ) or "none"
        phases = ", ".join(f"`{phase}`" for phase in item["phases"]) or "none"
        required_context = ", ".join(
            f"`{context}`" for context in item["required_context"]
        ) or "none"
        conflicts = ", ".join(
            f"`{conflict}`" for conflict in item["conflicts"]
        ) or "none"
        supersedes = ", ".join(
            f"`{skill}`" for skill in item["supersedes"]
        ) or "none"
        ownership = "; ".join(
            f"`{capability}`: {', '.join(f'`{phase}`' for phase in phases)}"
            for capability, phases in item["ownership"].items()
        ) or "none"
        lines.extend(
            (
                f"### {item['name']}",
                "",
                f"- Version: `{item['version']}`",
                f"- Description: {item['description']}",
                f"- Capabilities: {capabilities}",
                f"- Depends on: {dependencies}",
                f"- Selectors: {selectors}",
                f"- Phases: {phases}",
                f"- Specificity: `{item['specificity']}`",
                f"- Required context: {required_context}",
                f"- Conflicts: {conflicts}",
                f"- Supersedes: {supersedes}",
                f"- Ownership: {ownership}",
                "",
            )
        )
    return "\n".join(lines)


def render_catalog(
    manifest_path: Path,
    json_path: Path,
    markdown_path: Path,
    *,
    check: bool = False,
) -> bool:
    """Return True when outputs already match or were written successfully."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog = _catalog_from_manifest(manifest)
    expected_json = json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"
    expected_markdown = _render_markdown(catalog)

    if check:
        return (
            json_path.exists()
            and markdown_path.exists()
            and json_path.read_text(encoding="utf-8") == expected_json
            and markdown_path.read_text(encoding="utf-8") == expected_markdown
        )

    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(expected_json, encoding="utf-8")
    markdown_path.write_text(expected_markdown, encoding="utf-8")
    return True


def _render_readme_inventory(manifest: dict) -> str:
    lines = [
        INVENTORY_START,
        "| Skill | Category | When to use |",
        "| --- | --- | --- |",
    ]
    for item in manifest["skills"]:
        description = item["description"].replace("|", "\\|")
        lines.append(
            f"| [`{item['name']}`](skills/{item['name']}/) "
            f"| `{item['category']}` | {description} |"
        )
    lines.append(INVENTORY_END)
    return "\n".join(lines)


def render_readme_inventory(
    manifest_path: Path,
    readme_path: Path,
    *,
    check: bool = False,
) -> bool:
    if not readme_path.exists():
        raise InventoryMarkerError("README.md is missing")
    text = readme_path.read_text(encoding="utf-8")
    before, _, after = split_readme_inventory(text)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = before + _render_readme_inventory(manifest) + after
    if check:
        return text == expected
    readme_path.write_text(expected, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()

    root = args.root or Path(__file__).resolve().parents[1]
    references = root / "skills" / "translating-products" / "references"
    catalog_matched = render_catalog(
        root / "skills-manifest.json",
        references / "capability-catalog.json",
        references / "capability-catalog.md",
        check=args.check,
    )
    try:
        readme_matched = render_readme_inventory(
            root / "skills-manifest.json",
            root / "README.md",
            check=args.check,
        )
    except InventoryMarkerError as error:
        print(f"README.md: {error}", file=sys.stderr)
        readme_matched = False
    return 0 if catalog_matched and readme_matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
