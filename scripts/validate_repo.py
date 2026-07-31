from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.repo_model import Manifest, load_manifest


NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
HOST_TOKENS = (
    re.compile(r"\$[a-z0-9-]+", re.I),
    re.compile(r"@[a-z0-9-]+", re.I),
    re.compile(r"(?m)^/[a-z0-9-]+", re.I),
)
FRONTMATTER_FIELDS = {"name", "description"}


def parse_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing opening frontmatter delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("missing closing frontmatter delimiter") from error

    result: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            raise ValueError(f"invalid frontmatter line: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in FRONTMATTER_FIELDS:
            raise ValueError(f"unexpected frontmatter field: {key}")
        if key in result:
            raise ValueError(f"duplicate frontmatter field: {key}")
        result[key] = value.strip()
    return result


def validate_skill(path: Path) -> list[str]:
    try:
        metadata = parse_frontmatter(path)
    except ValueError as error:
        return [str(error)]

    errors = []
    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if not NAME.fullmatch(name):
        errors.append("invalid name")
    if name != path.parent.name:
        errors.append("frontmatter name does not match directory")
    if not 1 <= len(description) <= 1024:
        errors.append("description must contain 1 through 1024 characters")

    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > 500:
        errors.append("SKILL.md exceeds 500 lines")
    closing = lines.index("---", 1) if lines and lines[0] == "---" else 0
    body = "\n".join(lines[closing + 1 :])
    if any(pattern.search(body) for pattern in HOST_TOKENS):
        errors.append("host-specific invocation")
    return errors


def validate_repository(
    root: Path,
    manifest: Manifest,
    *,
    partial: bool = False,
) -> list[str]:
    errors = []
    skills_root = root / "skills"
    declared = {skill.name for skill in manifest.skills}
    present = (
        {path.name for path in skills_root.iterdir() if path.is_dir()}
        if skills_root.exists()
        else set()
    )

    if (root / "SKILL.md").exists():
        errors.append("SKILL.md: root skill is not allowed")
    for name in sorted(declared - present):
        if not partial:
            errors.append(f"skills/{name}: manifest skill directory is missing")
    for name in sorted(present - declared):
        errors.append(f"skills/{name}: directory is not declared in manifest")
    for name in sorted(present & declared):
        path = skills_root / name / "SKILL.md"
        if not path.exists():
            errors.append(f"skills/{name}/SKILL.md: file is missing")
            continue
        errors.extend(
            f"{path.relative_to(root)}: {message}"
            for message in validate_skill(path)
        )

    graph = defaultdict(tuple)
    for skill in sorted(manifest.skills, key=lambda item: item.name):
        graph[skill.name] = skill.depends_on
        for dependency in sorted(skill.depends_on):
            if dependency not in declared:
                errors.append(
                    f"skills-manifest.json: {skill.name} has unknown dependency "
                    f"{dependency}"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            errors.append(f"skills-manifest.json: dependency cycle at {name}")
            return
        if name in visited:
            return
        visiting.add(name)
        for dependency in sorted(graph[name]):
            if dependency in declared:
                visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in sorted(declared):
        visit(name)

    known_capabilities = {
        capability
        for skill in manifest.skills
        for capability in skill.capabilities
    }
    expected_versions = declared - {manifest.orchestrator}
    if set(manifest.minimum_skill_versions) != expected_versions:
        errors.append(
            "skills-manifest.json: minimum_skill_versions must name every "
            "non-orchestrator skill exactly once"
        )
    for name, version in sorted(manifest.minimum_skill_versions.items()):
        if not VERSION.fullmatch(version):
            errors.append(f"skills-manifest.json: invalid minimum version for {name}")

    for source in sorted(manifest.sources, key=lambda item: item.id):
        if source.mode != "adapted":
            errors.append(
                f"skills-manifest.json: {source.id} has unsupported mode {source.mode}"
            )
        for capability in sorted(source.capabilities):
            if capability not in known_capabilities:
                errors.append(
                    f"skills-manifest.json: {source.id} references missing "
                    f"capability {capability}"
                )
        adapter = root / source.adapter
        if not adapter.exists() and not partial:
            errors.append(f"{source.adapter}: source adapter is missing")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = load_manifest(root / "skills-manifest.json")
    errors = validate_repository(root, manifest, partial=args.partial)
    if errors:
        print("\n".join(errors))
        return 1
    if args.partial:
        count = sum((root / "skills" / skill.name).is_dir() for skill in manifest.skills)
        print(f"validated {count} implemented skills (partial manifest)")
    else:
        print(f"validated {len(manifest.skills)} skills and {len(manifest.sources)} sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
