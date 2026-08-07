#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="$repo_root/skills-manifest.json"
scratch="$(mktemp -d)"
scratch_marker="$scratch/.human-translation-skills-smoke"

cleanup() {
  if [[ -n "${scratch:-}" && "$scratch" != "/" && -d "$scratch" && -f "$scratch_marker" ]]; then
    rm -rf -- "$scratch"
  fi
}
trap cleanup EXIT
: > "$scratch_marker"

cli_version="$(npx skills --version)"

manifest_contract="$(
  python3 - "$manifest" "$repo_root" <<'PY'
import json
from pathlib import Path
import sys

manifest_path, root_text = sys.argv[1:]
root = Path(root_text)
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
records = manifest["skills"]
names = [skill["name"] for skill in records]
duplicates = sorted({name for name in names if names.count(name) > 1})
if duplicates:
    raise SystemExit("manifest contains duplicate skill names: " + ", ".join(duplicates))
skills = {skill["name"]: skill for skill in records}
entrypoint = manifest.get("orchestrator")
if entrypoint not in skills:
    raise SystemExit("manifest orchestrator is not a declared skill")

individual = {entrypoint}
seen_categories = set()
for skill in records:
    category = skill.get("category", "uncategorized")
    if category not in seen_categories:
        seen_categories.add(category)
        individual.add(skill["name"])
    skill_root = root / "skills" / skill["name"]
    resources = [
        path for path in skill_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    ]
    if len(resources) > 1:
        individual.add(skill["name"])

for skill in records:
    missing = sorted(set(skill.get("depends_on", [])) - set(skills))
    if missing:
        raise SystemExit(
            f"manifest skill {skill['name']} has unknown dependencies: "
            + ", ".join(missing)
        )
print(len(names), entrypoint, ",".join(name for name in names if name in individual))
PY
)"
read -r expected_count entrypoint individual_csv <<< "$manifest_contract"
IFS=',' read -r -a individual_skills <<< "$individual_csv"

verify_install() {
  local target="$1"
  local agent="$2"
  local scope="$3"
  local selected_name="${4:-}"
  python3 - "$manifest" "$repo_root" "$target" "$agent" "$expected_count" "$entrypoint" "$scope" "$selected_name" <<'PY'
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

(
    manifest_path, repo_root_text, target_text, agent, expected_count_text,
    entrypoint, scope, selected_name,
) = sys.argv[1:]
repo_root = Path(repo_root_text)
target = Path(target_text)
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
skills = {skill["name"]: skill for skill in manifest["skills"]}
expected = sorted(skills) if scope == "suite" else [selected_name]
host_roots = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
    "cursor": ".agents/skills",
    "universal": ".agents/skills",
}
skill_root = target / host_roots[agent]

target_resolved = target.resolve(strict=True)
cursor = target
if target.is_symlink():
    print(f"{agent}: symlinked disposable target", file=sys.stderr)
    raise SystemExit(1)
for part in Path(host_roots[agent]).parts:
    cursor = cursor / part
    if cursor.is_symlink():
        print(f"{agent}: symlinked host skills root: {cursor}", file=sys.stderr)
        raise SystemExit(1)
    if cursor.exists() and not cursor.resolve(strict=True).is_relative_to(target_resolved):
        print(f"{agent}: host skills root escapes disposable target: {cursor}", file=sys.stderr)
        raise SystemExit(1)

def inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"symlink {relative.as_posix()}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"special file {relative.as_posix()}")
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result

direct = sorted(
    path.name for path in skill_root.iterdir()
    if path.is_dir() and (path / "SKILL.md").is_file()
) if skill_root.is_dir() else []
skill_files = sorted(skill_root.rglob("SKILL.md")) if skill_root.is_dir() else []
names = [path.parent.name for path in skill_files]
counts = Counter(names)
misplaced = sorted(
    path.relative_to(target).as_posix()
    for path in skill_files
    if path.parent.parent != skill_root
)
actual = direct
errors = []
for name in actual:
    installed_path = skill_root / name
    if (
        installed_path.is_symlink()
        or not installed_path.resolve(strict=True).is_relative_to(target_resolved)
    ):
        errors.append(f"{agent}: installed skill escapes disposable target: {name}")
scope_count = int(expected_count_text) if scope == "suite" else 1
if len(skill_files) != scope_count:
    errors.append(f"{agent}: expected {scope_count} installed skills, found {len(actual)}")
if misplaced:
    errors.append(
        f"{agent}: skills not directly under {host_roots[agent]}: "
        + ", ".join(misplaced)
    )
duplicates = sorted(name for name, count in counts.items() if count > 1)
if duplicates:
    errors.append(f"{agent}: duplicate skills: {', '.join(duplicates)}")
missing = sorted(set(expected) - set(actual))
unexpected = sorted(set(actual) - set(expected))
if missing:
    errors.append(f"{agent}: missing skills: {', '.join(missing)}")
if unexpected:
    errors.append(f"{agent}: unexpected skills: {', '.join(unexpected)}")

for name in sorted(set(expected) & set(actual)):
    try:
        source_inventory = inventory(repo_root / "skills" / name)
        installed_inventory = inventory(skill_root / name)
    except ValueError as error:
        errors.append(f"{agent}: unsafe installed inventory for {name}: {error}")
        continue
    if source_inventory != installed_inventory:
        missing_files = sorted(set(source_inventory) - set(installed_inventory))
        extra_files = sorted(set(installed_inventory) - set(source_inventory))
        changed_files = sorted(
            path for path in set(source_inventory) & set(installed_inventory)
            if source_inventory[path] != installed_inventory[path]
        )
        details = []
        if missing_files:
            details.append("missing " + ", ".join(missing_files))
        if extra_files:
            details.append("unexpected " + ", ".join(extra_files))
        if changed_files:
            details.append("changed " + ", ".join(changed_files))
        errors.append(
            f"{agent}: installed inventory mismatch for {name}: " + "; ".join(details)
        )

if scope == "individual" and selected_name in skills:
    dependencies = sorted(set(skills[selected_name].get("depends_on", [])) & set(actual))
    if dependencies:
        errors.append(
            f"{agent}: individual install unexpectedly included dependencies: "
            + ", ".join(dependencies)
        )

if scope == "suite":
    source_router = repo_root / "skills" / entrypoint / "scripts" / "route_capabilities.py"
    if source_router.is_file():
        installed_router = skill_root / entrypoint / "scripts" / "route_capabilities.py"
        request = target / "smoke-routing-request.json"
        request.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "source_locale": "en-US",
                    "targets": [{"locale": "en-GB"}],
                    "surfaces": [], "platforms": [], "formats": [],
                    "domains": [], "capabilities": [], "audience": "Adults",
                    "purpose": "Installation smoke", "register": "neutral",
                }
            ),
            encoding="utf-8",
        )
        probe = subprocess.run(
            [sys.executable, str(installed_router), str(request)],
            capture_output=True, text=True, check=False,
        )
        try:
            payload = json.loads(probe.stdout) if probe.returncode == 0 else None
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict) or payload.get("schema_version") != 2 or not payload.get("routes"):
            errors.append(
                f"{agent}: installed router probe failed: "
                + (probe.stderr.strip() or "invalid output")
            )

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
PY
}

for agent in claude-code codex cursor universal; do
  target="$scratch/$agent/full"
  mkdir -p -- "$target"
  (
    cd "$target"
    npx skills add "$repo_root" --skill '*' --agent "$agent" --yes --copy
  )
  verify_install "$target" "$agent" suite

  for individual_skill in "${individual_skills[@]}"; do
    individual_target="$scratch/$agent/individual/$individual_skill"
    mkdir -p -- "$individual_target"
    (
      cd "$individual_target"
      npx skills add "$repo_root" --skill "$individual_skill" --agent "$agent" --yes --copy
    )
    verify_install "$individual_target" "$agent" individual "$individual_skill"
  done
done

printf 'cross-agent installation smoke test passed (skills CLI %s)\n' "$cli_version"
