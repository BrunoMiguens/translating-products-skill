from __future__ import annotations

import argparse
import json
from pathlib import Path


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


def _catalog_from_manifest(manifest: dict) -> dict:
    fields = ("name", "version", "category", "capabilities", "depends_on")
    return {
        "schema_version": 1,
        "suite_version": manifest["suite_version"],
        "skills": [
            {field: item[field] for field in fields}
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

    for category in CATEGORIES:
        lines.extend((f"## {category}", ""))
        for item in catalog["skills"]:
            if item["category"] != category:
                continue
            capabilities = ", ".join(
                f"`{capability}`" for capability in item["capabilities"]
            )
            dependencies = ", ".join(
                f"`{dependency}`" for dependency in item["depends_on"]
            ) or "none"
            lines.extend(
                (
                    f"### {item['name']}",
                    "",
                    f"- Version: `{item['version']}`",
                    f"- Capabilities: {capabilities}",
                    f"- Depends on: {dependencies}",
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
        return False
    text = readme_path.read_text(encoding="utf-8")
    if INVENTORY_START not in text or INVENTORY_END not in text:
        return False
    before, remainder = text.split(INVENTORY_START, 1)
    _, after = remainder.split(INVENTORY_END, 1)
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
    readme_matched = render_readme_inventory(
        root / "skills-manifest.json",
        root / "README.md",
        check=args.check,
    )
    return 0 if catalog_matched and readme_matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
