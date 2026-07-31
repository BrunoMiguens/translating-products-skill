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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()

    root = args.root or Path(__file__).resolve().parents[1]
    references = root / "skills" / "translating-products" / "references"
    matched = render_catalog(
        root / "skills-manifest.json",
        references / "capability-catalog.json",
        references / "capability-catalog.md",
        check=args.check,
    )
    return 0 if matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
