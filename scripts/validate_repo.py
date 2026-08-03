from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re
import shlex
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
PUBLICATION_FILES = (
    ".github/workflows/validate.yml",
    "CONTRIBUTING.md",
    "README.md",
    "docs/architecture.md",
)
SHELL_FENCE = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.DOTALL)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
SKILL_LINK = re.compile(r"\]\(skills/([a-z0-9]+(?:-[a-z0-9]+)*)/?\)")
INVENTORY_START = "<!-- skill-inventory:start -->"
INVENTORY_END = "<!-- skill-inventory:end -->"
SUPPORTED_AGENTS = {"claude-code", "codex", "cursor", "universal"}


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


def _shell_commands(markdown: str) -> list[str]:
    commands = []
    for block in SHELL_FENCE.findall(markdown):
        pending = ""
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            pending = f"{pending} {line}".strip()
            if pending.endswith("\\"):
                pending = pending[:-1].rstrip()
                continue
            if pending.startswith("npx skills add "):
                commands.append(pending)
            pending = ""
    return commands


def _parse_install_command(
    command: str,
) -> tuple[dict[str, object] | None, list[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError as error:
        return None, [f"invalid install command: {error}"]
    if tokens[:3] != ["npx", "skills", "add"] or len(tokens) < 4:
        return None, ["invalid install command form"]

    record: dict[str, object] = {
        "source": tokens[3],
        "list": False,
        "all": False,
        "skills": [],
        "agents": [],
    }
    errors = []
    index = 4
    while index < len(tokens):
        option = tokens[index]
        if option in {"--list", "--all", "--yes", "--global"}:
            record[option[2:]] = True
            index += 1
            continue
        if option not in {"--skill", "--agent"}:
            errors.append(f"unsupported install argument {option}")
            index += 1
            continue
        values = []
        index += 1
        while index < len(tokens) and not tokens[index].startswith("--"):
            values.append(tokens[index])
            index += 1
        if not values:
            errors.append(f"{option} requires at least one value")
            continue
        key = "skills" if option == "--skill" else "agents"
        record[key] = [*record[key], *values]

    if not (record["list"] or record["all"] or record["skills"]):
        errors.append("install command needs --list, --all, or --skill")
    for agent in record["agents"]:
        if agent not in SUPPORTED_AGENTS:
            errors.append(f"unsupported --agent value {agent}")
    return record, errors


def _validate_install_commands(markdown: str) -> list[str]:
    errors = []
    records = []
    for command in _shell_commands(markdown):
        record, command_errors = _parse_install_command(command)
        errors.extend(command_errors)
        if record is not None:
            records.append(record)

    if not records:
        return ["no fenced npx skills add commands found"]

    coverage = {
        "local preview": any(
            record["source"] == "." and record["list"] for record in records
        ),
        "local full-suite install": any(
            record["source"] == "." and record["all"] for record in records
        ),
        "remote full-suite install": any(
            record["source"] == "OWNER/REPOSITORY" and record["all"]
            for record in records
        ),
        "individual specialist install": any(
            record["source"] == "OWNER/REPOSITORY"
            and "translating-japanese" in record["skills"]
            for record in records
        ),
    }
    for agent in sorted(SUPPORTED_AGENTS):
        coverage[f"remote wildcard install for {agent}"] = any(
            record["source"] == "OWNER/REPOSITORY"
            and "*" in record["skills"]
            and agent in record["agents"]
            for record in records
        )
    errors.extend(
        f"missing {name} example"
        for name, covered in coverage.items()
        if not covered
    )
    return errors


def _validate_markdown_links(root: Path, path: Path) -> list[str]:
    errors = []
    text = path.read_text(encoding="utf-8")
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.split(maxsplit=1)[0].strip("<>")
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        local = target.split("#", 1)[0]
        if local and not (path.parent / local).resolve().exists():
            errors.append(f"{path.relative_to(root)}: broken link {target}")
    return errors


def validate_distribution(root: Path, manifest: Manifest) -> list[str]:
    errors = [
        f"{relative}: file is missing"
        for relative in PUBLICATION_FILES
        if not (root / relative).is_file()
    ]
    readme = root / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        if INVENTORY_START not in text or INVENTORY_END not in text:
            errors.append("README.md: skill inventory markers are missing")
        else:
            inventory = text.split(INVENTORY_START, 1)[1].split(INVENTORY_END, 1)[0]
            linked_skills = SKILL_LINK.findall(inventory)
            expected_skills = [skill.name for skill in manifest.skills]
            if linked_skills != expected_skills:
                errors.append(
                    "README.md: skill inventory does not match skills-manifest.json"
                )
        errors.extend(
            f"README.md: {error}" for error in _validate_install_commands(text)
        )

    for relative in ("README.md", "CONTRIBUTING.md", "docs/architecture.md"):
        path = root / relative
        if path.is_file():
            errors.extend(_validate_markdown_links(root, path))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = load_manifest(root / "skills-manifest.json")
    errors = validate_repository(root, manifest, partial=args.partial)
    if not args.partial:
        errors.extend(validate_distribution(root, manifest))
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
