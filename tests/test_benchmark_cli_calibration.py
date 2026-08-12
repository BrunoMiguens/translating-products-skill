from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.benchmark.cli_calibration import (
    CalibrationRunError,
    RunOptions,
    load_pack,
    main,
    run_tasks,
    task_states,
)
from scripts.benchmark.common import BenchmarkError, append_jsonl_fsync, sha256_bytes


class CalibrationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def make_suite_source(self, text: str = "canonical suite\n") -> Path:
        source = self.temp_dir / f"suite-{len(list(self.temp_dir.glob('suite-*')))}"
        skill = source / "translating-products"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(text, encoding="utf-8")
        return source

    def make_pack(
        self,
        *,
        app: str = "claude",
        condition: str = "normal",
        prompt: str = "Translate this exactly.\n",
    ) -> Path:
        root = self.temp_dir / f"pack-{app}-{condition}"
        skills = condition == "suite"
        task_dir = root / "tasks" / app / f"01-{condition}-case-1"
        task_dir.mkdir(parents=True)
        prompt_path = task_dir / "PROMPT.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        (task_dir / "RESPONSE.txt").write_text("", encoding="utf-8")

        project = root / "projects" / f"{app}-{'with' if skills else 'without'}-skills"
        project.mkdir(parents=True)
        (project / "README.md").write_text("fixture project\n", encoding="utf-8")
        if skills:
            translation = project / ".translation"
            translation.mkdir()
            (translation / "project-brief.md").write_text(
                "Status: approved\n", encoding="utf-8"
            )
            if app == "claude":
                skill = project / ".claude" / "skills" / "translating-products"
            else:
                skill = project / ".agents" / "skills" / "translating-products"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: translating-products\ndescription: fixture\n---\n",
                encoding="utf-8",
            )
            debris = project / ".claude" / "worktrees" / "stale"
            debris.mkdir(parents=True)
            (debris / "SECRET.txt").write_text("must not copy\n", encoding="utf-8")

        manifest = {
            "schema_version": 1,
            "tasks": [
                {
                    "app": app,
                    "number": 1,
                    "run_id": f"{app}-{condition}-case-1",
                    "case_id": "case-1",
                    "condition": condition,
                    "skills": skills,
                    "task_dir": task_dir.relative_to(root).as_posix(),
                    "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                }
            ],
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        return root

    def make_two_app_pack(self) -> Path:
        claude = self.make_pack(app="claude")
        codex = self.make_pack(app="codex")
        root = self.temp_dir / "pack-all"
        root.mkdir()
        tasks = []
        for source in (claude, codex):
            manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            tasks.extend(manifest["tasks"])
            for directory in ("tasks", "projects"):
                for path in (source / directory).rglob("*"):
                    destination = root / directory / path.relative_to(source / directory)
                    if path.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(path.read_bytes())
        (root / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "tasks": tasks}), encoding="utf-8"
        )
        return root

    def add_second_task_per_app(self, root: Path) -> None:
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        additions = []
        for task in manifest["tasks"]:
            source = root / task["task_dir"]
            second = dict(task)
            second["number"] = 2
            second["run_id"] = second["run_id"].replace("case-1", "case-2")
            second["case_id"] = "case-2"
            second["task_dir"] = second["task_dir"].replace("01-normal-case-1", "02-normal-case-2")
            destination = root / second["task_dir"]
            destination.mkdir(parents=True)
            (destination / "PROMPT.txt").write_bytes((source / "PROMPT.txt").read_bytes())
            (destination / "RESPONSE.txt").write_text("", encoding="utf-8")
            additions.append(second)
        manifest["tasks"].extend(additions)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def make_fake_cli(self, app: str, behavior: str = "success") -> Path:
        executable = self.temp_dir / f"fake-{app}-{behavior}"
        executable.write_text(
            """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

app = __file__.split('-')[-2]
behavior = __file__.split('-')[-1]
if '--version' in sys.argv:
    print(f'{app}-cli 9.9.9')
    raise SystemExit(0)
prompt = sys.stdin.read()
record = {
    'argv': sys.argv[1:],
    'stdin': prompt,
    'cwd': os.getcwd(),
    'home': os.environ.get('HOME'),
    'codex_home': os.environ.get('CODEX_HOME'),
    'files': sorted(
        str(path.relative_to(pathlib.Path.cwd()))
        for path in pathlib.Path.cwd().rglob('*')
    ),
    'home_files': [
        relative
        for relative in ('.claude.json', '.claude/skills/unrelated/SKILL.md')
        if (pathlib.Path(os.environ['HOME']) / relative).exists()
    ],
    'skill_text': (
        pathlib.Path(
            '.claude/skills/translating-products/SKILL.md'
            if app == 'claude'
            else '.agents/skills/translating-products/SKILL.md'
        ).read_text(encoding='utf-8')
        if pathlib.Path(
            '.claude/skills/translating-products/SKILL.md'
            if app == 'claude'
            else '.agents/skills/translating-products/SKILL.md'
        ).exists()
        else None
    ),
}
pathlib.Path(__file__).with_suffix('.log').write_text(
    json.dumps(record, sort_keys=True), encoding='utf-8'
)
if behavior == 'fail':
    print(json.dumps({'type': 'error', 'error': 'fixture failure'}))
    print('host failed', file=sys.stderr)
    raise SystemExit(7)
if behavior == 'timeout':
    time.sleep(2)
if app == 'claude':
    print(json.dumps({
        'type': 'result',
        'result': 'Guardar alterações',
        'modelUsage': {'fake-claude-model': {'inputTokens': 12}},
        'total_cost_usd': 0.01,
    }))
else:
    output = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])
    output.write_text('Guardar alterações', encoding='utf-8')
    print(json.dumps({'type': 'turn.started', 'model': 'fake-codex-model'}))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 12}}))
""",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    def test_load_pack_validates_prompt_hash_and_rejects_drift(self):
        root = self.make_pack()
        pack = load_pack(root)
        self.assertEqual([task.run_id for task in pack.tasks], ["claude-normal-case-1"])

        (root / "tasks/claude/01-normal-case-1/PROMPT.txt").write_text(
            "changed\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(BenchmarkError, "prompt hash mismatch"):
            load_pack(root)

    def test_load_pack_rejects_duplicate_ids_and_escaping_task_paths(self):
        root = self.make_pack()
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["tasks"].append(dict(manifest["tasks"][0]))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(BenchmarkError, "duplicate run id"):
            load_pack(root)

        manifest["tasks"] = [dict(manifest["tasks"][0], task_dir="../outside")]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(BenchmarkError, "task_dir must stay inside"):
            load_pack(root)

    def test_task_state_requires_matching_success_evidence(self):
        root = self.make_pack()
        pack = load_pack(root)
        self.assertEqual(task_states(pack, {"claude"})[0].status, "pending")

        response = root / "tasks/claude/01-normal-case-1/RESPONSE.txt"
        response.write_text("manual answer\n", encoding="utf-8")
        pack = load_pack(root)
        self.assertEqual(task_states(pack, {"claude"})[0].status, "invalid")

        append_jsonl_fsync(
            root / "evidence.jsonl",
            {
                "schema_version": 1,
                "evidence_kind": "diagnostic_cli_calibration",
                "run_id": "claude-normal-case-1",
                "status": "success",
                "prompt_sha256": pack.tasks[0].prompt_sha256,
                "output_sha256": sha256_bytes(response.read_bytes()),
            },
        )
        pack = load_pack(root)
        self.assertEqual(task_states(pack, {"claude"})[0].status, "completed")

    def test_claude_run_stages_only_suite_files_and_captures_response(self):
        root = self.make_pack(app="claude", condition="suite")
        suite_source = self.make_suite_source("canonical claude suite\n")
        transient = suite_source / "translating-products" / "__pycache__"
        transient.mkdir()
        (transient / "ignored.pyc").write_bytes(b"ignored")
        (suite_source / ".DS_Store").write_bytes(b"ignored")
        (suite_source / "ignored.pyo").write_bytes(b"ignored")
        fake = self.make_fake_cli("claude")
        source_home = self.temp_dir / "claude-operator-home"
        global_skill = source_home / ".claude" / "skills" / "unrelated"
        global_skill.mkdir(parents=True)
        (global_skill / "SKILL.md").write_text("must not copy\n", encoding="utf-8")
        (source_home / ".claude.json").write_text(
            '{"oauthAccount":{"fixture":true}}\n', encoding="utf-8"
        )
        summary = run_tasks(
            load_pack(root, suite_source=suite_source),
            RunOptions(
                apps={"claude"},
                claude_executable=str(fake),
                source_home=source_home,
                timeout_seconds=5,
            ),
        )

        self.assertEqual((summary.succeeded, summary.skipped, summary.failed), (1, 0, 0))
        response = root / "tasks/claude/01-suite-case-1/RESPONSE.txt"
        self.assertEqual(response.read_text(encoding="utf-8"), "Guardar alterações\n")
        invocation = json.loads(fake.with_suffix(".log").read_text(encoding="utf-8"))
        self.assertEqual(invocation["stdin"], "Translate this exactly.\n")
        self.assertIn("--print", invocation["argv"])
        self.assertIn("--no-session-persistence", invocation["argv"])
        self.assertIn("--tools", invocation["argv"])
        tools_index = invocation["argv"].index("--tools")
        self.assertEqual(invocation["argv"][tools_index + 1], "Read,Glob,Grep,Skill")
        self.assertIn(".claude/skills/translating-products/SKILL.md", invocation["files"])
        self.assertEqual(invocation["skill_text"], "canonical claude suite\n")
        self.assertNotIn(
            ".claude/skills/translating-products/__pycache__/ignored.pyc",
            invocation["files"],
        )
        self.assertNotIn(".claude/skills/.DS_Store", invocation["files"])
        self.assertNotIn(".claude/skills/ignored.pyo", invocation["files"])
        self.assertIn(".translation/project-brief.md", invocation["files"])
        self.assertNotIn(".claude/worktrees/stale/SECRET.txt", invocation["files"])
        self.assertEqual(invocation["home"], str(source_home.resolve()))
        self.assertIn(".claude/skills/unrelated/SKILL.md", invocation["home_files"])
        self.assertNotIn(".claude/skills/unrelated/SKILL.md", invocation["files"])
        settings_index = invocation["argv"].index("--setting-sources")
        self.assertEqual(invocation["argv"][settings_index + 1], "project")
        evidence = json.loads((root / "evidence.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "success")
        self.assertEqual(evidence["output_sha256"], sha256_bytes(response.read_bytes()))
        self.assertEqual(evidence["model_observed"], "fake-claude-model")
        self.assertEqual(evidence["evidence_kind"], "diagnostic_cli_calibration")
        self.assertRegex(evidence["suite_tree_sha256"], r"^[0-9a-f]{64}$")

    def test_codex_run_isolates_auth_and_uses_nonpersistent_flags(self):
        root = self.make_pack(app="codex", condition="suite")
        suite_source = self.make_suite_source("canonical codex suite\n")
        fake = self.make_fake_cli("codex")
        source_home = self.temp_dir / "operator-home"
        auth = source_home / ".codex" / "auth.json"
        auth.parent.mkdir(parents=True)
        auth.write_text('{"token":"fixture"}\n', encoding="utf-8")
        auth.chmod(0o600)

        run_tasks(
            load_pack(root, suite_source=suite_source),
            RunOptions(
                apps={"codex"},
                codex_executable=str(fake),
                source_home=source_home,
                timeout_seconds=5,
            ),
        )

        invocation = json.loads(fake.with_suffix(".log").read_text(encoding="utf-8"))
        self.assertIn("exec", invocation["argv"])
        self.assertIn("--ephemeral", invocation["argv"])
        self.assertIn("--ignore-user-config", invocation["argv"])
        self.assertIn("--ignore-rules", invocation["argv"])
        self.assertIn("--json", invocation["argv"])
        self.assertIn("read-only", invocation["argv"])
        self.assertIn(".agents/skills/translating-products/SKILL.md", invocation["files"])
        self.assertEqual(invocation["skill_text"], "canonical codex suite\n")
        self.assertNotEqual(invocation["home"], str(source_home))
        self.assertNotEqual(invocation["codex_home"], str(source_home / ".codex"))
        self.assertFalse(Path(invocation["home"]).exists())
        self.assertEqual(
            (root / "tasks/codex/01-suite-case-1/RESPONSE.txt").read_text(encoding="utf-8"),
            "Guardar alterações\n",
        )

    def test_suite_resume_requires_current_tree_hash(self):
        root = self.make_pack(app="claude", condition="suite")
        suite_source = self.make_suite_source()
        fake = self.make_fake_cli("claude")
        options = RunOptions(
            apps={"claude"},
            claude_executable=str(fake),
            timeout_seconds=5,
        )

        run_tasks(load_pack(root, suite_source=suite_source), options)
        self.assertEqual(
            task_states(
                load_pack(root, suite_source=suite_source), {"claude"},
            )[0].status,
            "completed",
        )

        cache = suite_source / "translating-products" / "__pycache__"
        cache.mkdir()
        (cache / "ignored.pyc").write_bytes(b"changed")
        (suite_source / ".DS_Store").write_bytes(b"changed")
        (suite_source / "ignored.pyo").write_bytes(b"changed")
        self.assertEqual(
            task_states(
                load_pack(root, suite_source=suite_source), {"claude"},
            )[0].status,
            "completed",
        )

        (suite_source / "translating-products" / "SKILL.md").write_text(
            "changed canonical suite\n", encoding="utf-8",
        )
        state = task_states(
            load_pack(root, suite_source=suite_source), {"claude"},
        )[0]
        self.assertEqual(state.status, "invalid")
        self.assertIn("suite tree hash", state.reason)

    def test_suite_source_rejects_symlink_before_invocation(self):
        root = self.make_pack(app="claude", condition="suite")
        suite_source = self.make_suite_source()
        (suite_source / "linked-skill").symlink_to(
            suite_source / "translating-products", target_is_directory=True,
        )
        fake = self.make_fake_cli("claude")

        with self.assertRaisesRegex(BenchmarkError, "symlink"):
            run_tasks(
                load_pack(root, suite_source=suite_source),
                RunOptions(
                    apps={"claude"},
                    claude_executable=str(fake),
                    timeout_seconds=5,
                ),
            )
        self.assertFalse(fake.with_suffix(".log").exists())

    def test_host_failure_records_evidence_without_response_and_stops(self):
        root = self.make_pack(app="claude")
        fake = self.make_fake_cli("claude", behavior="fail")
        with self.assertRaisesRegex(CalibrationRunError, "claude-normal-case-1"):
            run_tasks(
                load_pack(root),
                RunOptions(apps={"claude"}, claude_executable=str(fake), timeout_seconds=5),
            )
        response = root / "tasks/claude/01-normal-case-1/RESPONSE.txt"
        self.assertEqual(response.read_text(encoding="utf-8"), "")
        evidence = json.loads((root / "evidence.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["exit_code"], 7)
        self.assertIn("host failed", evidence["stderr"])
        self.assertIn("fixture failure", evidence["stdout"])

    def test_missing_executable_records_a_bound_prestart_failure(self):
        root = self.make_pack(app="claude")
        missing = self.temp_dir / "does-not-exist"
        with self.assertRaisesRegex(CalibrationRunError, "claude-normal-case-1"):
            run_tasks(
                load_pack(root),
                RunOptions(
                    apps={"claude"},
                    claude_executable=str(missing),
                    timeout_seconds=5,
                ),
            )
        evidence = json.loads((root / "evidence.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(evidence["run_id"], "claude-normal-case-1")
        self.assertEqual(evidence["status"], "failed")
        self.assertFalse(evidence["process_started"])
        self.assertIn("not runnable", evidence["reason"])

    def test_matching_success_resumes_and_force_replaces_an_orphan(self):
        root = self.make_pack(app="claude")
        fake = self.make_fake_cli("claude")
        options = RunOptions(apps={"claude"}, claude_executable=str(fake), timeout_seconds=5)
        first = run_tasks(load_pack(root), options)
        second = run_tasks(load_pack(root), options)
        self.assertEqual(first.succeeded, 1)
        self.assertEqual((second.succeeded, second.skipped), (0, 1))

        (root / "evidence.jsonl").unlink()
        with self.assertRaisesRegex(BenchmarkError, "orphaned or mismatched response"):
            run_tasks(load_pack(root), options)
        forced = run_tasks(load_pack(root), RunOptions(**{**options.__dict__, "force": True}))
        self.assertEqual(forced.succeeded, 1)

    def test_timeout_is_recorded_without_a_completed_response(self):
        root = self.make_pack(app="codex")
        fake = self.make_fake_cli("codex", behavior="timeout")
        with self.assertRaisesRegex(CalibrationRunError, "timed out"):
            run_tasks(
                load_pack(root),
                RunOptions(apps={"codex"}, codex_executable=str(fake), timeout_seconds=0.05),
            )
        evidence = json.loads((root / "evidence.jsonl").read_text(encoding="utf-8"))
        self.assertTrue(evidence["timed_out"])
        self.assertEqual(
            (root / "tasks/codex/01-normal-case-1/RESPONSE.txt").read_text(encoding="utf-8"),
            "",
        )

    def test_cli_status_and_run_select_only_the_requested_app(self):
        root = self.make_two_app_pack()
        claude = self.make_fake_cli("claude")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status_code = main(["status", "--root", str(root)])
        self.assertEqual(status_code, 0)
        self.assertIn("claude: pending=1 completed=0 invalid=0", stdout.getvalue())
        self.assertIn("codex: pending=1 completed=0 invalid=0", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            run_code = main(
                [
                    "run",
                    "--root",
                    str(root),
                    "--app",
                    "claude",
                    "--claude-executable",
                    str(claude),
                    "--timeout-seconds",
                    "5",
                ]
            )
        self.assertEqual(run_code, 0)
        self.assertIn("completed=1 skipped=0 failed=0", stdout.getvalue())
        self.assertNotEqual(
            (root / "tasks/claude/01-normal-case-1/RESPONSE.txt").read_text(encoding="utf-8"),
            "",
        )
        self.assertEqual(
            (root / "tasks/codex/01-normal-case-1/RESPONSE.txt").read_text(encoding="utf-8"),
            "",
        )

    def test_probe_runs_only_the_first_pending_task_for_each_selected_app(self):
        root = self.make_two_app_pack()
        self.add_second_task_per_app(root)
        claude = self.make_fake_cli("claude")
        codex = self.make_fake_cli("codex")
        summary = run_tasks(
            load_pack(root),
            RunOptions(
                apps={"claude", "codex"},
                probe=True,
                claude_executable=str(claude),
                codex_executable=str(codex),
                timeout_seconds=5,
            ),
        )
        self.assertEqual(summary.succeeded, 2)
        for app in ("claude", "codex"):
            self.assertNotEqual(
                (root / f"tasks/{app}/01-normal-case-1/RESPONSE.txt").read_text(
                    encoding="utf-8"
                ),
                "",
            )
            self.assertEqual(
                (root / f"tasks/{app}/02-normal-case-2/RESPONSE.txt").read_text(
                    encoding="utf-8"
                ),
                "",
            )


if __name__ == "__main__":
    unittest.main()
