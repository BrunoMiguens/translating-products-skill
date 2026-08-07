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
HOST_SKILL_ROOTS = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
    "cursor": ".agents/skills",
    "universal": ".agents/skills",
}


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
    mode = os.environ.get("SMOKE_FAKE_MODE", "success")
    if mode == "fail-codex" and agent == "codex":
        raise SystemExit(9)

    source = Path(argv[2])
    discovered = sorted(source.glob("skills/*/SKILL.md"))
    names = [path.parent.name for path in discovered]
    if "*" not in argv:
        requested = argv[argv.index("--skill") + 1]
        discovered = [
            path for path in discovered if path.parent.name == requested
        ]
        names = [path.parent.name for path in discovered]
    if mode in {{"missing", "duplicate"}}:
        discovered = discovered[:-1]
        names = [path.parent.name for path in discovered]

    roots = {{
        "claude-code": ".claude/skills",
        "codex": ".agents/skills",
        "cursor": ".agents/skills",
        "universal": ".agents/skills",
    }}
    installed = []
    for source_file in discovered:
        name = source_file.parent.name
        skill_file = cwd / roots[agent] / name / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(source_file.read_text(encoding="utf-8"), encoding="utf-8")
        installed.append(str(skill_file))

    if mode == "duplicate":
        duplicate = cwd / roots[agent] / ".duplicate" / names[0] / "SKILL.md"
        duplicate.parent.mkdir(parents=True, exist_ok=True)
        duplicate.write_text("duplicate\\n", encoding="utf-8")
    with Path(os.environ["SMOKE_LOG"]).open("a", encoding="utf-8") as log:
        log.write(
            json.dumps(
                {{"cwd": str(cwd), "argv": argv, "installed": installed, "names": names}}
            )
            + "\\n"
        )
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
                for skill in manifest["skills"]:
                    skill_file = suite_root / "skills" / skill["name"] / "SKILL.md"
                    skill_file.parent.mkdir(parents=True, exist_ok=True)
                    skill_file.write_text(
                        f"---\\nname: {skill['name']}\\n---\\n",
                        encoding="utf-8",
                    )
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
            manifest_data = (
                json.loads(MANIFEST.read_text(encoding="utf-8"))
                if manifest is None
                else manifest
            )
            entrypoint = manifest_data["orchestrator"]
            specialist = next(
                skill["name"]
                for skill in manifest_data["skills"]
                if "language:japanese" in skill.get("capabilities", [])
            )
            calls = [
                entry
                for entry in entries
                if entry["argv"][entry["argv"].index("--skill") + 1] == "*"
            ]
            entrypoint_entries = [
                entry
                for entry in entries
                if entry["argv"][entry["argv"].index("--skill") + 1]
                == entrypoint
            ]
            specialist_entries = [
                entry
                for entry in entries
                if entry["argv"][entry["argv"].index("--skill") + 1]
                == specialist
            ]
            targets_exist = [Path(entry["cwd"]).exists() for entry in calls]
            scratch_exists = (
                bool(calls) and Path(calls[0]["cwd"]).parent.exists()
            )
            snapshot = {
                "result": result,
                "entries": calls,
                "individual_entries": entrypoint_entries,
                "japanese_entries": specialist_entries,
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
        individual_entries = run["individual_entries"]
        self.assertEqual(len(individual_entries), 4)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        entrypoint = manifest["orchestrator"]
        specialist = next(
            skill["name"]
            for skill in manifest["skills"]
            if "language:japanese" in skill.get("capabilities", [])
        )
        specialist_dependencies = next(
            skill["depends_on"]
            for skill in manifest["skills"]
            if skill["name"] == specialist
        )
        manifest_names = [item["name"] for item in manifest["skills"]]
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
            installed = [Path(path) for path in entry["installed"]]
            self.assertEqual(
                [path.parent.name for path in installed], sorted(manifest_names)
            )
            self.assertEqual(
                [path.relative_to(target).parts[:2] for path in installed],
                [tuple(HOST_SKILL_ROOTS[entry["argv"][6]].split("/"))] * len(installed),
            )
        for entry in individual_entries:
            agent = entry["argv"][6]
            target = Path(entry["cwd"])
            self.assertEqual(
                entry["argv"],
                [
                    "skills",
                    "add",
                    str(ROOT),
                    "--skill",
                    entrypoint,
                    "--agent",
                    agent,
                    "--yes",
                    "--copy",
                ],
            )
            self.assertEqual(
                [Path(path).relative_to(target).as_posix() for path in entry["installed"]],
                [f"{HOST_SKILL_ROOTS[agent]}/{entrypoint}/SKILL.md"],
            )
        japanese_entries = run["japanese_entries"]
        self.assertEqual(len(japanese_entries), 4)
        for entry in japanese_entries:
            agent = entry["argv"][6]
            target = Path(entry["cwd"])
            self.assertEqual(
                entry["argv"],
                [
                    "skills",
                    "add",
                    str(ROOT),
                    "--skill",
                    specialist,
                    "--agent",
                    agent,
                    "--yes",
                    "--copy",
                ],
            )
            self.assertEqual(
                [Path(path).relative_to(target).as_posix() for path in entry["installed"]],
                [f"{HOST_SKILL_ROOTS[agent]}/{specialist}/SKILL.md"],
            )
            self.assertFalse(set(specialist_dependencies) & set(entry["names"]))
            self.assertEqual(entry["names"], [specialist])
        self.assertFalse(run["scratch_exists"])
        self.assertEqual(run["protected"], "sentinel\n")

    def test_suite_size_is_derived_from_the_manifest(self):
        manifest = {
            "orchestrator": "route-entry",
            "skills": [
                {"name": "route-entry", "depends_on": ["route-core"]},
                {"name": "route-core", "depends_on": ["route-review"]},
                {"name": "route-review", "depends_on": []},
                {"name": "route-demo", "depends_on": []},
                {
                    "name": "route-japanese",
                    "depends_on": ["route-core"],
                    "capabilities": ["language:japanese"],
                },
            ]
        }
        run = self.run_smoke(manifest=manifest)
        self.assertEqual(run["result"].returncode, 0, run["result"].stderr)
        self.assertEqual(len(run["entries"]), 4)
        self.assertEqual(len(run["individual_entries"]), 4)
        self.assertEqual(len(run["japanese_entries"]), 4)
        for entry in run["individual_entries"]:
            self.assertEqual(entry["names"], ["route-entry"])
        for entry in run["japanese_entries"]:
            self.assertEqual(entry["names"], ["route-japanese"])

        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('if [[ "$expected_count" != "22" ]]', script)
        self.assertIn("print(len(names), entrypoint, specialist)", script)
        self.assertNotIn('"translating-products"', script)

    def test_reports_duplicate_and_missing_skill_for_the_specific_agent(self):
        run = self.run_smoke("duplicate")
        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(run["entries"]), 1)
        self.assertEqual(
            result.stderr.splitlines(),
            [
                "claude-code: skills not directly under .claude/skills: "
                ".claude/skills/.duplicate/localizing-software/SKILL.md",
                "claude-code: duplicate skills: localizing-software",
                "claude-code: missing skills: translating-web",
            ],
        )
        self.assertFalse(run["scratch_exists"])
        self.assertEqual(run["protected"], "sentinel\n")

    def test_reports_count_for_missing_installation(self):
        run = self.run_smoke("missing")
        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        names = sorted(item["name"] for item in manifest["skills"])
        self.assertEqual(
            result.stderr.splitlines(),
            [
                f"claude-code: expected {len(names)} SKILL.md files, found {len(names) - 1}",
                f"claude-code: missing skills: {names[-1]}",
            ],
        )

    def test_cleans_scratch_when_the_installer_fails(self):
        run = self.run_smoke("fail-codex")
        result = run["result"]
        self.assertEqual(result.returncode, 9)
        self.assertEqual(len(run["entries"]), 1)
        self.assertEqual(len(run["individual_entries"]), 1)
        self.assertEqual(len(run["japanese_entries"]), 1)
        self.assertFalse(run["scratch_exists"])
        self.assertEqual(run["protected"], "sentinel\n")

    def test_rejects_duplicate_manifest_names_before_installing(self):
        manifest = {
            "orchestrator": "route-entry",
            "skills": [
                {"name": "route-entry", "depends_on": []},
                {"name": "route-core", "depends_on": []},
                {"name": "route-core", "depends_on": []},
                {
                    "name": "route-japanese",
                    "depends_on": [],
                    "capabilities": ["language:japanese"],
                },
            ],
        }
        run = self.run_smoke(manifest=manifest)
        self.assertNotEqual(run["result"].returncode, 0)
        self.assertEqual(
            run["result"].stderr,
            "manifest contains duplicate skill names: route-core\n",
        )
        self.assertEqual(run["entries"], [])
        self.assertEqual(run["individual_entries"], [])
        self.assertEqual(run["japanese_entries"], [])
        self.assertFalse(run["scratch_exists"])
        self.assertEqual(run["protected"], "sentinel\n")


if __name__ == "__main__":
    unittest.main()
