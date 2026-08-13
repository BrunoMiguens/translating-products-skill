from __future__ import annotations

import argparse
from collections import defaultdict
import json
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
    parse_frontmatter_text,
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
FIXTURE_TEXT_MINIMUM = 48
EXPECTED_FIXTURE_FIELDS = {
    "accepted_corrections",
    "literal_failure",
    "reference",
}


def _normalized_prose(value: str) -> str:
    return " ".join(value.split()).casefold()


def _skill_body(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0] == "---":
        try:
            closing = lines.index("---", 1)
        except ValueError:
            pass
        else:
            return "\n".join(lines[closing + 1 :])
    return "\n".join(lines)


def _fixture_texts(root: Path) -> list[tuple[str, str, str, str]]:
    fixtures = []

    def add(
        record: object,
        relative: Path,
        fields: tuple[str, ...],
        case: object,
    ) -> None:
        if not isinstance(record, dict):
            return
        case_id = str(record.get("id", case))
        for field in fields:
            value = record.get(field)
            if not isinstance(value, str):
                continue
            normalized = _normalized_prose(value)
            if len(normalized) >= FIXTURE_TEXT_MINIMUM:
                fixtures.append((normalized, relative.as_posix(), case_id, field))

    benchmarks = root / "benchmarks"
    if benchmarks.is_dir():
        for path in sorted(benchmarks.glob("*/cases.jsonl")):
            relative = path.relative_to(root)
            with path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if line.strip():
                        add(
                            json.loads(line),
                            relative,
                            ("source", "reference"),
                            line_number,
                        )

    for filename, fields in (
        ("translation-quality-cases.json", ("source", "literal_failure")),
        ("structural-fidelity-cases.json", ("source",)),
    ):
        path = root / "evals" / filename
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        for index, record in enumerate(
            json.loads(path.read_text(encoding="utf-8")), start=1
        ):
            add(record, relative, fields, index)

    return fixtures


def _fixture_documents(root: Path) -> list[tuple[Path, object]]:
    documents = []
    for directory in (root / "benchmarks", root / "evals"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
                continue
            if path.suffix == ".json":
                documents.append((path.relative_to(root), json.loads(path.read_text(encoding="utf-8"))))
                continue
            records = []
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        records.append(json.loads(line))
            documents.append((path.relative_to(root), records))
    return documents


def _nested_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(
            string
            for item in value
            for string in _nested_strings(item)
        )
    if isinstance(value, dict):
        return tuple(
            string
            for item in value.values()
            for string in _nested_strings(item)
        )
    return ()


def _evaluation_leakage_values(
    root: Path,
) -> tuple[list[tuple[str, str]], list[tuple[object, str, str, str]]]:
    case_ids: list[tuple[str, str]] = []
    expected: list[tuple[object, str, str, str]] = []

    def add_expected(
        value: object, relative: Path, case_id: str, field: str
    ) -> None:
        if _nested_strings(value):
            expected.append((value, relative.as_posix(), case_id, field))
        if isinstance(value, list):
            for item in value:
                add_expected(item, relative, case_id, field)
        elif isinstance(value, dict):
            for item in value.values():
                add_expected(item, relative, case_id, field)

    def visit(value: object, relative: Path, case_id: str) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, relative, case_id)
            return
        if not isinstance(value, dict):
            return
        local_case = value.get("id")
        if isinstance(local_case, str) and local_case.strip():
            case_id = local_case
            case_ids.append((local_case, relative.as_posix()))
        for field, item in value.items():
            if field.startswith("expected") or field in EXPECTED_FIXTURE_FIELDS:
                add_expected(item, relative, case_id, field)
            visit(item, relative, case_id)

    for relative, document in _fixture_documents(root):
        visit(document, relative, "unknown")
    return case_ids, expected


def _production_files(root: Path) -> list[Path]:
    paths = []
    skills = root / "skills"
    if skills.is_dir():
        paths.extend(
            path
            for path in skills.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    manifest = root / "skills-manifest.json"
    if manifest.is_file():
        paths.append(manifest)
    for name in ("policy.py", "route_capabilities.py"):
        path = root / "scripts" / name
        if path.is_file():
            paths.append(path)
    return sorted(set(paths))


def _contains_complete_token(text: str, token: str) -> bool:
    return re.search(
        rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])",
        text,
    ) is not None


def _json_contains_value(container: object, target: object) -> bool:
    if type(container) is type(target) and container == target:
        return True
    if isinstance(container, list):
        return any(_json_contains_value(item, target) for item in container)
    if isinstance(container, dict):
        return any(_json_contains_value(item, target) for item in container.values())
    return False


def _generic_expected_target(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) < FIXTURE_TEXT_MINIMUM
        and re.fullmatch(r"[a-z][a-z0-9_-]*", value) is not None
    )


def validate_fixture_separation(root: Path) -> list[str]:
    skill_bodies = [
        (path.relative_to(root).as_posix(), _normalized_prose(_skill_body(path)))
        for path in sorted((root / "skills").glob("*/SKILL.md"))
    ]
    errors = []
    for fixture, relative, case_id, field in _fixture_texts(root):
        for skill_path, body in skill_bodies:
            if fixture in body:
                errors.append(
                    f"{skill_path}: contains normalized benchmark text from "
                    f"{relative} case {case_id} field {field}"
                )
    production = []
    for path in _production_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        document = None
        if path.suffix == ".json":
            try:
                document = json.loads(text)
            except json.JSONDecodeError:
                pass
        production.append((path.relative_to(root).as_posix(), text, document))

    case_ids, expected_values = _evaluation_leakage_values(root)
    for case_id, source in case_ids:
        for production_path, text, _document in production:
            if _contains_complete_token(text, case_id):
                errors.append(
                    f"{production_path}: contains evaluation case id {case_id} from {source}"
                )
    for expected, source, case_id, field in expected_values:
        if _generic_expected_target(expected):
            continue
        rendered = json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
        normalized = (
            _normalized_prose(expected)
            if isinstance(expected, str)
            else None
        )
        for production_path, text, document in production:
            if (
                normalized is not None
                and len(normalized) >= FIXTURE_TEXT_MINIMUM
                and normalized in _normalized_prose(text)
            ) or (
                document is not None and _json_contains_value(document, expected)
            ):
                old_skill_check = (
                    production_path.startswith("skills/")
                    and production_path.endswith("/SKILL.md")
                    and field in {"literal_failure", "reference"}
                    and isinstance(expected, str)
                    and len(normalized or "") >= FIXTURE_TEXT_MINIMUM
                )
                if old_skill_check:
                    continue
                errors.append(
                    f"{production_path}: contains expected evaluation target {rendered} "
                    f"from {source} case {case_id} field {field}"
                )
    return sorted(errors)


def parse_frontmatter(path: Path) -> dict[str, str]:
    return parse_frontmatter_text(path.read_text(encoding="utf-8"), label=str(path))


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
    by_name = {skill.name: skill for skill in manifest.skills}
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
        skill_errors = validate_skill(path)
        errors.extend(
            f"{path.relative_to(root)}: {message}"
            for message in skill_errors
        )
        if not skill_errors:
            metadata = parse_frontmatter(path)
            record = by_name[name]
            if metadata["name"] != record.name:
                errors.append(
                    f"{path.relative_to(root)}: frontmatter name does not match manifest"
                )
            if metadata["description"] != record.description:
                errors.append(
                    f"{path.relative_to(root)}: frontmatter description does not match manifest"
                )

    errors.extend(validate_fixture_separation(root))

    for skill in sorted(manifest.skills, key=lambda item: item.name):
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
            else:
                target = by_name[superseded]
                if skill.category != target.category:
                    errors.append(
                        f"skills-manifest.json: {skill.name} cannot supersede "
                        f"{superseded} across categories"
                    )
                elif (
                    skill.specificity in SPECIFICITIES
                    and target.specificity in SPECIFICITIES
                    and SPECIFICITIES.index(skill.specificity)
                    <= SPECIFICITIES.index(target.specificity)
                ):
                    errors.append(
                        f"skills-manifest.json: {skill.name} must be more specific "
                        f"than superseded skill {superseded}"
                    )
                replacement_ownership = {
                    capability: set(phases)
                    for capability, phases in skill.ownership
                }
                target_ownership = {
                    capability: set(phases)
                    for capability, phases in target.ownership
                }
                if not any(
                    replacement_ownership.get(capability, set()) & phases
                    for capability, phases in target_ownership.items()
                ):
                    errors.append(
                        f"skills-manifest.json: {skill.name} supersedes {superseded} "
                        "without shared ownership"
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
    dependency_cycle_nodes: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            dependency_cycle_nodes.add(name)
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
    supersedes_cycle_nodes: set[str] = set()

    def visit_supersedes(name: str) -> None:
        if name in visiting:
            supersedes_cycle_nodes.add(name)
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

    relationship_graph = defaultdict(tuple)
    for skill in sorted(manifest.skills, key=lambda item: item.name):
        relationship_graph[skill.name] = skill.depends_on + skill.supersedes

    visiting.clear()
    visited.clear()

    def visit_relationship(name: str) -> None:
        if name in visiting:
            if name not in dependency_cycle_nodes | supersedes_cycle_nodes:
                errors.append(
                    f"skills-manifest.json: relationship cycle at {name}"
                )
            return
        if name in visited:
            return
        visiting.add(name)
        for target in sorted(relationship_graph[name]):
            if target in declared:
                visit_relationship(target)
        visiting.remove(name)
        visited.add(name)

    for name in sorted(declared):
        visit_relationship(name)

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
