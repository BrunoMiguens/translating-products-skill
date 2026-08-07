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

expected_count="$(
  python3 - "$manifest" <<'PY'
import json
from pathlib import Path
import sys

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
names = [skill["name"] for skill in manifest["skills"]]
if len(names) != len(set(names)):
    raise SystemExit("manifest contains duplicate skill names")
required = {
    "translating-products",
    "translating-core",
    "reviewing-translations",
}
missing_required = sorted(required - set(names))
if missing_required:
    raise SystemExit(
        "manifest is missing required skills: " + ", ".join(missing_required)
    )
print(len(names))
PY
)"

for agent in claude-code codex cursor universal; do
  target="$scratch/$agent"
  mkdir -p -- "$target"
  (
    cd "$target"
    npx skills add "$repo_root" \
      --skill '*' \
      --agent "$agent" \
      --yes \
      --copy
  )

  python3 - "$manifest" "$target" "$agent" "$expected_count" <<'PY'
from collections import Counter
import json
from pathlib import Path
import sys

manifest_path, target_path, agent, expected_count_text = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
expected = sorted(skill["name"] for skill in manifest["skills"])
expected_set = set(expected)
actual = sorted(
    path.parent.name
    for path in Path(target_path).rglob("SKILL.md")
    if path.is_file()
)
counts = Counter(actual)

errors = []
expected_count = int(expected_count_text)
if len(actual) != expected_count:
    errors.append(
        f"{agent}: expected {expected_count} SKILL.md files, found {len(actual)}"
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

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
PY
done

printf 'cross-agent installation smoke test passed\n'
