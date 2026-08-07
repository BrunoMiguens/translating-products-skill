import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_install.sh"
MANIFEST = ROOT / "skills-manifest.json"


FAKE_NPX = textwrap.dedent(
    f"""\
    #!{sys.executable}
    import json
    import os
    from pathlib import Path
    import sys

    argv = sys.argv[1:]
    cwd = Path.cwd()
    agent = argv[argv.index("--agent") + 1]
    with Path(os.environ["SMOKE_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps({{"cwd": str(cwd), "argv": argv}}) + "\\n")

    mode = os.environ.get("SMOKE_FAKE_MODE", "success")
    if mode == "fail-codex" and agent == "codex":
        raise SystemExit(9)

    manifest = json.loads(
        Path(os.environ["SMOKE_MANIFEST"]).read_text(encoding="utf-8")
    )
    names = [skill["name"] for skill in manifest["skills"]]
    if mode in {{"missing", "duplicate"}}:
        names = names[:-1]

    for name in names:
        skill_file = cwd / ".agents" / "skills" / name / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(f"---\\nname: {{name}}\\n---\\n", encoding="utf-8")

    if mode == "duplicate":
        duplicate = cwd / ".duplicate" / "translating-core" / "SKILL.md"
        duplicate.parent.mkdir(parents=True, exist_ok=True)
        duplicate.write_text("duplicate\\n", encoding="utf-8")
    """
)


class SmokeInstallTests(unittest.TestCase):
    def run_smoke(self, mode: str = "success", manifest: dict | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            npx = fake_bin / "npx"
            npx.write_text(FAKE_NPX, encoding="utf-8")
            npx.chmod(0o755)

            home = root / "home"
            protected = home / ".agents" / "skills" / "do-not-touch" / "SKILL.md"
            protected.parent.mkdir(parents=True)
            protected.write_text("sentinel\n", encoding="utf-8")
            log = root / "npx.jsonl"
            if manifest is None:
                suite_root = ROOT
                suite_script = SCRIPT
                suite_manifest = MANIFEST
            else:
                suite_root = root / "suite"
                suite_script = suite_root / "scripts" / SCRIPT.name
                suite_script.parent.mkdir(parents=True)
                suite_script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
                suite_manifest = suite_root / MANIFEST.name
                suite_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                    "SMOKE_FAKE_MODE": mode,
                    "SMOKE_LOG": str(log),
                    "SMOKE_MANIFEST": str(suite_manifest),
                }
            )

            result = subprocess.run(
                ["bash", str(suite_script)],
                cwd=suite_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if log.exists():
                entries = [
                    json.loads(line)
                    for line in log.read_text(encoding="utf-8").splitlines()
                ]
            else:
                entries = []
            targets_exist = [Path(entry["cwd"]).exists() for entry in entries]
            scratch_exists = (
                bool(entries) and Path(entries[0]["cwd"]).parent.exists()
            )
            snapshot = {
                "result": result,
                "entries": entries,
                "targets_exist": targets_exist,
                "scratch_exists": scratch_exists,
                "protected": protected.read_text(encoding="utf-8"),
                "home": home,
            }
            return snapshot

    def test_script_has_valid_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installs_manifest_suite_for_each_agent_in_disposable_targets(self):
        run = self.run_smoke()
        result = run["result"]
        entries = run["entries"]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "cross-agent installation smoke test passed\n",
        )
        self.assertEqual(
            [entry["argv"][6] for entry in entries],
            ["claude-code", "codex", "cursor", "universal"],
        )
        self.assertEqual(len({entry["cwd"] for entry in entries}), 4)
        for entry, target_exists in zip(entries, run["targets_exist"]):
            self.assertEqual(
                entry["argv"],
                [
                    "skills",
                    "add",
                    str(ROOT),
                    "--skill",
                    "*",
                    "--agent",
                    entry["argv"][6],
                    "--yes",
                    "--copy",
                ],
            )
            target = Path(entry["cwd"])
            self.assertFalse(target_exists)
            self.assertFalse(target.is_relative_to(run["home"]))
            self.assertFalse(target.is_relative_to(ROOT))
        self.assertFalse(run["scratch_exists"])
        self.assertEqual(run["protected"], "sentinel\n")

    def test_suite_size_is_derived_from_the_manifest(self):
        manifest = {
            "skills": [
                {"name": "translating-products"},
                {"name": "translating-core"},
                {"name": "reviewing-translations"},
                {"name": "translating-demo"},
            ]
        }
        run = self.run_smoke(manifest=manifest)
        self.assertEqual(run["result"].returncode, 0, run["result"].stderr)
        self.assertEqual(len(run["entries"]), 4)

        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('if [[ "$expected_count" != "22" ]]', script)
        self.assertIn("print(len(names))", script)

    def test_reports_duplicate_and_missing_skill_for_the_specific_agent(self):
        run = self.run_smoke("duplicate")
        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(run["entries"]), 1)
        self.assertEqual(
            result.stderr.splitlines(),
            [
                "claude-code: duplicate skills: translating-core",
                "claude-code: missing skills: translating-german",
            ],
        )
        self.assertFalse(run["scratch_exists"])
        self.assertEqual(run["protected"], "sentinel\n")

    def test_reports_count_for_missing_installation(self):
        run = self.run_smoke("missing")
        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            result.stderr.splitlines(),
            [
                "claude-code: expected 22 SKILL.md files, found 21",
                "claude-code: missing skills: translating-german",
            ],
        )

    def test_cleans_scratch_when_the_installer_fails(self):
        run = self.run_smoke("fail-codex")
        result = run["result"]
        self.assertEqual(result.returncode, 9)
        self.assertEqual(len(run["entries"]), 2)
        self.assertFalse(run["scratch_exists"])
        self.assertEqual(run["protected"], "sentinel\n")


if __name__ == "__main__":
    unittest.main()
