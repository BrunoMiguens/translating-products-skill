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

manifest_contract="$(
  python3 - "$manifest" <<'PY'
import json
from pathlib import Path
import sys

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
records = manifest["skills"]
names = [skill["name"] for skill in records]
duplicates = sorted({name for name in names if names.count(name) > 1})
if duplicates:
    raise SystemExit("manifest contains duplicate skill names: " + ", ".join(duplicates))
skills = {skill["name"]: skill for skill in records}
entrypoint = manifest.get("orchestrator")
if entrypoint not in skills:
    raise SystemExit("manifest orchestrator is not a declared skill")
specialists = [
    skill["name"]
    for skill in records
    if "language:japanese" in skill.get("capabilities", [])
]
if len(specialists) != 1:
    raise SystemExit("manifest must declare exactly one language:japanese specialist")
specialist = specialists[0]

required = set()
pending = [entrypoint]
while pending:
    name = pending.pop()
    if name in required:
        continue
    required.add(name)
    dependencies = skills[name].get("depends_on", [])
    missing = sorted(set(dependencies) - set(skills))
    if missing:
        raise SystemExit(
            f"manifest skill {name} has unknown dependencies: " + ", ".join(missing)
        )
    pending.extend(dependencies)

if not required:
    raise SystemExit(
        "manifest orchestrator dependency closure is empty"
    )
print(len(names), entrypoint, specialist)
PY
)"
read -r expected_count entrypoint specialist <<< "$manifest_contract"

for agent in claude-code codex cursor universal; do
  target="$scratch/$agent/full"
  mkdir -p -- "$target"
  (
    cd "$target"
    npx skills add "$repo_root" \
      --skill '*' \
      --agent "$agent" \
      --yes \
      --copy
  )

  python3 - "$manifest" "$target" "$agent" "$expected_count" "$entrypoint" suite <<'PY'
from collections import Counter
import json
from pathlib import Path
import sys

manifest_path, target_path, agent, expected_count_text, entrypoint, scope = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
skills = {skill["name"]: skill for skill in manifest["skills"]}
expected = (
    sorted(skills)
    if scope == "suite"
    else [entrypoint]
)
expected_set = set(expected)
host_roots = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
    "cursor": ".agents/skills",
    "universal": ".agents/skills",
}
skill_root = Path(target_path) / host_roots[agent]
actual = sorted(
    path.parent.name
    for path in skill_root.rglob("SKILL.md")
    if path.is_file()
)
outside_layout = sorted(
    path.relative_to(Path(target_path)).as_posix()
    for path in Path(target_path).rglob("SKILL.md")
    if path.is_file() and not path.is_relative_to(skill_root)
)
counts = Counter(actual)
misplaced = sorted(
    path.relative_to(Path(target_path)).as_posix()
    for path in skill_root.rglob("SKILL.md")
    if path.is_file() and path.parent.parent != skill_root
)

errors = []
expected_count = int(expected_count_text)
scope_count = expected_count if scope == "suite" else 1
if len(actual) != scope_count:
    errors.append(
        f"{agent}: expected {scope_count} SKILL.md files, found {len(actual)}"
    )

if outside_layout:
    errors.append(
        f"{agent}: skills outside {host_roots[agent]}: {', '.join(outside_layout)}"
    )
if misplaced:
    errors.append(
        f"{agent}: skills not directly under {host_roots[agent]}: "
        + ", ".join(misplaced)
    )

duplicates = sorted(name for name, count in counts.items() if count > 1)
if duplicates:
    errors.append(f"{agent}: duplicate skills: {', '.join(duplicates)}")

missing = sorted(expected_set - counts.keys())
if missing:
    errors.append(f"{agent}: missing skills: {', '.join(missing)}")

unexpected = sorted(counts.keys() - expected_set)
if unexpected:
    errors.append(f"{agent}: unexpected skills: {', '.join(unexpected)}")

if scope == "individual":
    dependencies = sorted(set(skills[entrypoint].get("depends_on", [])) & counts.keys())
    if dependencies:
        errors.append(
            f"{agent}: individual install unexpectedly included dependencies: "
            + ", ".join(dependencies)
        )

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
PY

  for individual_skill in "$entrypoint" "$specialist"; do
    individual_target="$scratch/$agent/individual/$individual_skill"
    mkdir -p -- "$individual_target"
    (
      cd "$individual_target"
      npx skills add "$repo_root" \
        --skill "$individual_skill" \
        --agent "$agent" \
        --yes \
        --copy
    )

    python3 - "$manifest" "$individual_target" "$agent" "$expected_count" "$individual_skill" individual <<'PY'
from collections import Counter
import json
from pathlib import Path
import sys

manifest_path, target_path, agent, expected_count_text, entrypoint, scope = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
skills = {skill["name"]: skill for skill in manifest["skills"]}
host_roots = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
    "cursor": ".agents/skills",
    "universal": ".agents/skills",
}
skill_root = Path(target_path) / host_roots[agent]
actual = sorted(
    path.parent.name for path in skill_root.rglob("SKILL.md") if path.is_file()
)
outside_layout = sorted(
    path.relative_to(Path(target_path)).as_posix()
    for path in Path(target_path).rglob("SKILL.md")
    if path.is_file() and not path.is_relative_to(skill_root)
)
counts = Counter(actual)
misplaced = sorted(
    path.relative_to(Path(target_path)).as_posix()
    for path in skill_root.rglob("SKILL.md")
    if path.is_file() and path.parent.parent != skill_root
)
errors = []

if len(actual) != 1:
    errors.append(f"{agent}: expected 1 SKILL.md files, found {len(actual)}")
if outside_layout:
    errors.append(
        f"{agent}: skills outside {host_roots[agent]}: {', '.join(outside_layout)}"
    )
if misplaced:
    errors.append(
        f"{agent}: skills not directly under {host_roots[agent]}: "
        + ", ".join(misplaced)
    )
duplicates = sorted(name for name, count in counts.items() if count > 1)
if duplicates:
    errors.append(f"{agent}: duplicate skills: {', '.join(duplicates)}")
missing = [entrypoint] if entrypoint not in counts else []
if missing:
    errors.append(f"{agent}: missing skills: {', '.join(missing)}")
unexpected = sorted(counts.keys() - {entrypoint})
if unexpected:
    errors.append(f"{agent}: unexpected skills: {', '.join(unexpected)}")
dependencies = sorted(set(skills[entrypoint].get("depends_on", [])) & counts.keys())
if dependencies:
    errors.append(
        f"{agent}: individual install unexpectedly included dependencies: "
        + ", ".join(dependencies)
    )
if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
PY
  done
done

printf 'cross-agent installation smoke test passed\n'
