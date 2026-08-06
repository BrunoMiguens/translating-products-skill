from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.repo_model import (
    ROUTING_AXES,
    ROUTING_PHASES,
    SPECIFICITIES,
    Manifest,
    load_manifest,
)
from scripts.render_catalog import InventoryMarkerError, split_readme_inventory


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
SKILL_LINK = re.compile(r"\]\(skills/([a-z0-9]+(?:-[a-z0-9]+)*)/?\)")
RESIDUAL_PATH_CONTROL = re.compile(r"%(?:00|2e|2f|5c)", re.IGNORECASE)
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
SUPPORTED_AGENTS = {"claude-code", "codex", "cursor", "universal"}
WORKFLOW_TRIGGERS = {"push", "pull_request", "schedule", "workflow_dispatch"}
OFFLINE_VALIDATION_COMMANDS = (
    "python3 scripts/render_catalog.py --check",
    "python3 scripts/validate_repo.py",
    "python3 -m unittest discover -s tests -v",
)
SOURCE_VERIFICATION_COMMAND = "python3 scripts/verify_sources.py"
PROFILE_FIELDS = {
    "source_locale",
    "target_locale",
    "language",
    "scripts",
    "surfaces",
    "platforms",
    "formats",
    "domains",
    "capabilities",
    "audience",
    "purpose",
    "register",
}


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

    for skill in sorted(manifest.skills, key=lambda item: item.name):
        if not skill.selectors:
            errors.append(
                f"skills-manifest.json: {skill.name} must declare at least one selector"
            )
        for selector in skill.selectors:
            for axis, _values in selector.criteria:
                if axis not in ROUTING_AXES:
                    errors.append(
                        f"skills-manifest.json: {skill.name} has unknown selector axis {axis}"
                    )
        for phase in sorted(set(skill.phases)):
            if phase not in ROUTING_PHASES:
                errors.append(
                    f"skills-manifest.json: {skill.name} has invalid phase {phase}"
                )
        if skill.specificity not in SPECIFICITIES:
            errors.append(
                "skills-manifest.json: "
                f"{skill.name} has invalid specificity {skill.specificity}"
            )
        for context in sorted(set(skill.required_context)):
            if context not in PROFILE_FIELDS:
                errors.append(
                    f"skills-manifest.json: {skill.name} requires unknown context "
                    f"{context}"
                )
        for dependency in sorted(set(skill.depends_on)):
            if dependency == skill.name:
                errors.append(
                    f"skills-manifest.json: {skill.name} cannot depend on itself"
                )
        for conflict in sorted(set(skill.conflicts)):
            if conflict not in declared:
                errors.append(
                    f"skills-manifest.json: {skill.name} conflicts with unknown skill "
                    f"{conflict}"
                )
            elif conflict == skill.name:
                errors.append(
                    f"skills-manifest.json: {skill.name} cannot conflict with itself"
                )
        for superseded in sorted(set(skill.supersedes)):
            if superseded not in declared:
                errors.append(
                    f"skills-manifest.json: {skill.name} supersedes unknown skill "
                    f"{superseded}"
                )
            elif superseded == skill.name:
                errors.append(
                    f"skills-manifest.json: {skill.name} cannot supersede itself"
                )
        for target in sorted(set(skill.depends_on) & set(skill.supersedes)):
            errors.append(
                f"skills-manifest.json: {skill.name} cannot both depend on and "
                f"supersede {target}"
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

    supersedes_graph = defaultdict(tuple)
    for skill in sorted(manifest.skills, key=lambda item: item.name):
        supersedes_graph[skill.name] = skill.supersedes

    visiting.clear()
    visited.clear()

    def visit_supersedes(name: str) -> None:
        if name in visiting:
            errors.append(f"skills-manifest.json: supersedes cycle at {name}")
            return
        if name in visited:
            return
        visiting.add(name)
        for superseded in sorted(supersedes_graph[name]):
            if superseded in declared:
                visit_supersedes(superseded)
        visiting.remove(name)
        visited.add(name)

    for name in sorted(declared):
        visit_supersedes(name)

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


def _markdown_link_destinations(markdown: str) -> list[str]:
    destinations = []
    cursor = 0
    while (close := markdown.find("](", cursor)) != -1:
        label_start = markdown.rfind("[", 0, close)
        cursor = close + 2
        if label_start == -1:
            continue
        if label_start > 0 and markdown[label_start - 1] == "!":
            continue
        start = cursor
        while start < len(markdown) and markdown[start].isspace():
            start += 1
        if start >= len(markdown):
            break
        if markdown[start] == "<":
            end = markdown.find(">", start + 1)
            if end == -1:
                continue
            destinations.append(markdown[start + 1 : end])
            cursor = end + 1
            continue
        end = start
        while end < len(markdown) and not (
            markdown[end].isspace() or markdown[end] == ")"
        ):
            end += 1
        if end > start:
            destinations.append(markdown[start:end])
        cursor = max(end + 1, cursor)
    return destinations


def _validate_markdown_links(root: Path, path: Path) -> list[str]:
    errors = []
    text = path.read_text(encoding="utf-8")
    repository = root.resolve()
    for target in _markdown_link_destinations(text):
        if target.startswith("#"):
            continue
        try:
            parsed = urlsplit(target)
        except ValueError as error:
            errors.append(
                f"{path.relative_to(root)}: invalid link destination {target}: "
                f"{error}"
            )
            continue
        if parsed.scheme.lower() in {"http", "https", "mailto"}:
            continue
        if not parsed.scheme and parsed.netloc:
            continue
        if not parsed.path:
            continue
        try:
            decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            errors.append(
                f"{path.relative_to(root)}: invalid UTF-8 in local link: {target}"
            )
            continue
        if "\x00" in decoded:
            errors.append(
                f"{path.relative_to(root)}: decoded local link contains NUL: {target}"
            )
            continue
        # Decode exactly once. Residual encoded path controls are rejected
        # instead of being decoded a second time or trusted as literal names.
        if RESIDUAL_PATH_CONTROL.search(decoded):
            errors.append(
                f"{path.relative_to(root)}: local link retains encoded path "
                f"control after one decode: {target}"
            )
            continue
        relative = Path(decoded.replace("\\", "/"))
        if relative.is_absolute() or WINDOWS_ABSOLUTE.match(decoded):
            errors.append(
                f"{path.relative_to(root)}: absolute local link is not allowed: "
                f"{target}"
            )
            continue
        resolved = (path.parent / relative).resolve()
        try:
            resolved.relative_to(repository)
        except ValueError:
            errors.append(
                f"{path.relative_to(root)}: local link escapes repository: {target}"
            )
            continue
        if not resolved.exists():
            errors.append(f"{path.relative_to(root)}: broken link {target}")
    return errors


def _schedule_has_cron(lines: list[str]) -> bool:
    try:
        start = lines.index("  schedule:") + 1
    except ValueError:
        return False
    for line in lines[start:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= 2:
            break
        match = re.match(r"^-\s+cron:\s*(.*)$", line.strip())
        if match and match.group(1).strip().strip('"\''):
            return True
    return False


def _mapping_entries(
    lines: list[str],
    name: str,
    *,
    indent: int = 0,
) -> dict[str, str]:
    header = f"{' ' * indent}{name}:"
    try:
        start = lines.index(header) + 1
    except ValueError:
        return {}
    entries = {}
    for line in lines[start:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= indent:
            break
        if current_indent != indent + 2:
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$", line.strip())
        if match:
            entries[match.group(1)] = (match.group(2) or "").strip('"\'')
    return entries


def _workflow_jobs(lines: list[str]) -> dict[str, list[str]]:
    try:
        start = lines.index("jobs:") + 1
    except ValueError:
        return {}
    boundaries = []
    for index in range(start, len(lines)):
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", lines[index])
        if match:
            boundaries.append((index, match.group(1)))
    jobs = {}
    for position, (index, name) in enumerate(boundaries):
        end = (
            boundaries[position + 1][0]
            if position + 1 < len(boundaries)
            else len(lines)
        )
        jobs[name] = lines[index + 1 : end]
    return jobs


def _yaml_mapping_key(line: str) -> str | None:
    match = re.match(
        r"^(?:(?P<quote>['\"])(?P<quoted>[^'\"]+)(?P=quote)|"
        r"(?P<plain>[A-Za-z0-9_-]+))\s*:",
        line,
    )
    if match is None:
        return None
    return match.group("quoted") or match.group("plain")


def _job_contract(lines: list[str]) -> dict[str, object]:
    commands = []
    setup_python = False
    python_versions = []
    condition = ""
    needs = ""
    collecting_needs = False
    permissions_override = False
    for line in lines:
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if collecting_needs:
            if indent > 4 and stripped.startswith("-"):
                needs = f"{needs} {stripped[1:].strip()}".strip()
            elif stripped and indent <= 4:
                collecting_needs = False
        run = re.match(r"^(?:-\s+)?run:\s*(.+)$", stripped)
        if run:
            commands.append(run.group(1).strip())
        uses = re.match(r"^(?:-\s+)?uses:\s*(.+)$", stripped)
        if uses and uses.group(1).startswith("actions/setup-python@"):
            setup_python = True
        version = re.match(r'^python-version:\s*["\']?([^"\']+)', stripped)
        if version:
            python_versions.append(version.group(1).strip())
        if indent == 4 and stripped.startswith("if:"):
            condition = stripped.split(":", 1)[1].strip()
        if indent == 4 and stripped.startswith("needs:"):
            needs = stripped.split(":", 1)[1].strip()
            collecting_needs = not needs
        if indent == 4 and _yaml_mapping_key(stripped) == "permissions":
            permissions_override = True
    return {
        "commands": commands,
        "uses_python_311": setup_python and "3.11" in python_versions,
        "condition": condition,
        "needs": needs,
        "permissions_override": permissions_override,
    }


def _is_source_verification_gate(condition: str) -> bool:
    expression = condition.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    expression = expression.replace("(", "").replace(")", "")
    branches = re.split(r"\s*\|\|\s*", expression)
    if len(branches) != 2:
        return False
    events = []
    for branch in branches:
        match = re.fullmatch(
            r"\s*github\.event_name\s*==\s*(['\"])(schedule|workflow_dispatch)\1\s*",
            branch,
        )
        if match is None:
            return False
        events.append(match.group(2))
    return set(events) == {"schedule", "workflow_dispatch"}


def validate_workflow(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    errors = []
    triggers = set(_mapping_entries(lines, "on"))
    for trigger in sorted(WORKFLOW_TRIGGERS - triggers):
        errors.append(f"workflow: missing trigger {trigger}")
    if "schedule" in triggers and not _schedule_has_cron(lines):
        errors.append(
            "workflow: schedule trigger must contain a nonempty cron entry"
        )

    permissions = _mapping_entries(lines, "permissions")
    if permissions != {"contents": "read"}:
        errors.append(
            "workflow: top-level permissions must be exactly contents: read"
        )

    parsed_jobs = {
        name: _job_contract(job_lines)
        for name, job_lines in _workflow_jobs(lines).items()
    }
    for name, job in parsed_jobs.items():
        if job["permissions_override"]:
            errors.append(
                f"workflow: job {name} must not override top-level permissions"
            )

    validate_job = parsed_jobs.get("validate")
    if validate_job is None:
        errors.append("workflow: validate job is missing")
    else:
        if not validate_job["uses_python_311"]:
            errors.append("workflow: validate job must use Python 3.11")
        for command in OFFLINE_VALIDATION_COMMANDS:
            if command not in validate_job["commands"]:
                errors.append(
                    f"workflow: validate job is missing required command: {command}"
                )
        if SOURCE_VERIFICATION_COMMAND in validate_job["commands"]:
            errors.append(
                "workflow: source verification must not run in validate job"
            )

    source_jobs = [
        (name, job)
        for name, job in parsed_jobs.items()
        if name != "validate" and SOURCE_VERIFICATION_COMMAND in job["commands"]
    ]
    if len(source_jobs) != 1:
        errors.append(
            "workflow: expected exactly one separate source verification job"
        )
    else:
        source_name, source_job = source_jobs[0]
        if not source_job["uses_python_311"]:
            errors.append(
                "workflow: source verification job must use Python 3.11"
            )
        if not _is_source_verification_gate(str(source_job["condition"])):
            errors.append(
                "workflow: source verification job must be gated to schedule "
                "and workflow_dispatch only"
            )
        if validate_job is not None and source_name in str(validate_job["needs"]):
            errors.append(
                "workflow: validate job must not depend on source verification job"
            )
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
        try:
            _, inventory, _ = split_readme_inventory(text)
        except InventoryMarkerError as error:
            errors.append(f"README.md: {error}")
        else:
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
    workflow = root / ".github/workflows/validate.yml"
    if workflow.is_file():
        errors.extend(validate_workflow(workflow))
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
