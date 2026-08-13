from __future__ import annotations

import errno
import json
import io
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.benchmark.common import BenchmarkError, canonical_bytes, sha256_bytes
from scripts.benchmark import product_runner


def run_git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


class ProductRunnerManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup_temporary)
        self.root = Path(self.temporary.name)
        self.product = self.root / "product"
        self.suite = self.root / "suite"
        self.context = self.root / "approved-context"
        self.output = self.root / "output"
        self._init_repo(self.product)
        self._init_repo(self.suite)

        self.product_file = self.product / "emails.json"
        self.product_file.write_text('{"email":"committed"}\n', encoding="utf-8")
        self._commit_all(self.product, "product")
        self.product_commit = run_git(self.product, "rev-parse", "HEAD")

        self._write_suite("old")
        self._commit_all(self.suite, "old suite")
        self.old_commit = run_git(self.suite, "rev-parse", "HEAD")
        self._write_suite("improved")
        self._commit_all(self.suite, "improved suite")
        self.improved_commit = run_git(self.suite, "rev-parse", "HEAD")

        self.context.mkdir()
        for name, value in {
            "project-brief.md": "Status: approved\nProduct: Push emails\n",
            "locales.yaml": "source_locale: en-US\ntarget_locales: [pt-PT]\n",
            "glossary.csv": "source,target,locale,status\n",
            "style-guide.md": "Status: approved\nRegister: formal\n",
            "protected-terms.txt": "Push\n",
            "setup-approval.json": '{"status":"approved"}\n',
        }.items():
            (self.context / name).write_text(value, encoding="utf-8")

    def _cleanup_temporary(self) -> None:
        # Filesystem observers may briefly recreate VCS metadata while rmtree
        # is walking a disposable repository. Retry only transient ENOTEMPTY.
        for attempt in range(5):
            try:
                self.temporary.cleanup()
                return
            except OSError as error:
                if error.errno != errno.ENOTEMPTY or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def _init_repo(self, root: Path) -> None:
        root.mkdir()
        run_git(root, "init")
        run_git(root, "config", "user.email", "benchmark@example.invalid")
        run_git(root, "config", "user.name", "Benchmark")

    def _commit_all(self, root: Path, message: str) -> None:
        run_git(root, "add", ".")
        run_git(root, "commit", "-m", message)

    def _write_suite(self, marker: str) -> None:
        skills = self.suite / "skills"
        orchestrator = skills / "translating-products"
        scripts = orchestrator / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (orchestrator / "SKILL.md").write_text(
            f"---\nname: translating-products\ndescription: {marker}\n---\n",
            encoding="utf-8",
        )
        (scripts / "policy.py").write_text(
            "import json\nprint(json.dumps({'action': 'translate'}))\n",
            encoding="utf-8",
        )

    def config(self) -> product_runner.RunnerConfig:
        return product_runner.RunnerConfig(
            product_repo=self.product,
            product_git_object=self.product_commit[:12],
            translation_context=self.context,
            suite_repo=self.suite,
            current_suite_git_object=self.old_commit[:12],
            improved_suite_git_object=self.improved_commit[:12],
            output_root=self.output,
            apps=frozenset({"claude", "codex"}),
            claude_model="claude-test",
            codex_model="codex-test",
        )

    def test_manifest_and_staging_bind_exact_git_objects_not_dirty_trees(self):
        config = self.config()
        manifest = product_runner.prepare_manifest(config)
        self.product_file.write_text('{"email":"dirty"}\n', encoding="utf-8")

        project = self.root / "staged-normal"
        task = product_runner.ProductTask("codex", "normal", manifest, config)
        product_runner.stage_project(task, project)

        self.assertEqual(
            (project / "emails.json").read_text(encoding="utf-8"),
            '{"email":"committed"}\n',
        )
        self.assertEqual(manifest.product_commit, self.product_commit)
        self.assertEqual(manifest.current_suite_commit, self.old_commit)
        self.assertEqual(manifest.improved_suite_commit, self.improved_commit)
        self.assertRegex(manifest.product_tree_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(manifest.context_tree_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(manifest.prompt_sha256, sha256_bytes(product_runner.PROMPT.encode()))
        self.assertTrue((project / ".translation" / "setup-approval.json").is_file())
        self.assertFalse((project / ".agents").exists())

    def test_suite_conditions_stage_only_the_selected_git_snapshot(self):
        config = self.config()
        manifest = product_runner.prepare_manifest(config)
        current = self.root / "staged-current"
        improved = self.root / "staged-improved"

        product_runner.stage_project(
            product_runner.ProductTask("claude", "current_suite", manifest, config),
            current,
        )
        product_runner.stage_project(
            product_runner.ProductTask("codex", "improved", manifest, config),
            improved,
        )

        self.assertIn(
            "old",
            (current / ".claude" / "skills" / "translating-products" / "SKILL.md").read_text(),
        )
        self.assertIn(
            "improved",
            (improved / ".agents" / "skills" / "translating-products" / "SKILL.md").read_text(),
        )
        self.assertFalse((current / ".agents").exists())
        self.assertFalse((improved / ".claude").exists())

    def test_manifest_is_canonical_and_rejects_symlinked_context(self):
        config = self.config()
        manifest = product_runner.prepare_manifest(config)
        encoded = canonical_bytes(manifest.to_dict())
        self.assertEqual(json.loads(encoded), manifest.to_dict())

        linked = self.context / "linked"
        linked.symlink_to(self.context / "project-brief.md")
        with self.assertRaisesRegex(BenchmarkError, "symlink"):
            product_runner.prepare_manifest(config)

    def test_config_rejects_unknown_apps_and_missing_private_output_boundary(self):
        with self.assertRaisesRegex(BenchmarkError, "apps"):
            product_runner.RunnerConfig(
                **{**self.config().__dict__, "apps": frozenset({"other"})}
            )

        repository_output = Path(product_runner.__file__).resolve().parents[2] / "public-output"
        with self.assertRaisesRegex(BenchmarkError, "benchmark-private"):
            product_runner.RunnerConfig(
                **{**self.config().__dict__, "output_root": repository_output}
            )


class ProductRunnerExecutionTests(ProductRunnerManifestTests):
    CSV = (
        "locale,key,english_source,current_translation,status,reason,recommended_correction\n"
        "pt-PT,welcome,Welcome,Olá,no_issue_detected,,\n"
    )

    def setUp(self):
        super().setUp()
        self.invocation_log = self.root / "invocations.jsonl"
        self.fake_cli = self.root / "fake-agent"
        script = f"""#!/opt/homebrew/bin/python3
import json
import os
import pathlib
import sys
import time

if '--version' in sys.argv:
    print('fake-agent 1.0')
    raise SystemExit(0)
record = {{
    'argv': sys.argv[1:],
    'cwd': os.getcwd(),
    'home': os.environ.get('HOME'),
    'codex_home': os.environ.get('CODEX_HOME'),
    'condition': os.environ.get('PRODUCT_REVIEW_CONDITION'),
}}
with pathlib.Path(os.environ['FAKE_INVOCATION_LOG']).open('a', encoding='utf-8') as target:
    target.write(json.dumps(record, sort_keys=True) + '\\n')
if os.environ.get('FAKE_TIMEOUT_CONDITION') == record['condition']:
    time.sleep(5)
csv = {self.CSV!r}
if os.environ.get('FAKE_MISMATCH_CONDITION') == record['condition']:
    csv = csv.replace(',Welcome,', ',Different source,')
if '--print' in sys.argv:
    print(json.dumps({{'result': csv, 'modelUsage': {{'claude-observed': {{}}}}}}))
else:
    output = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])
    output.write_text(csv, encoding='utf-8')
    print(json.dumps({{'type': 'turn.completed', 'model': 'codex-observed', 'usage': {{'tokens': 1}}}}))
"""
        self.fake_cli.write_text(script, encoding="utf-8")
        self.fake_cli.chmod(0o755)
        self.source_home = self.root / "source-home"
        (self.source_home / ".codex").mkdir(parents=True)
        (self.source_home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")

    def options(self, **overrides) -> product_runner.RunOptions:
        values = {
            "apps": frozenset({"claude", "codex"}),
            "conditions": frozenset(product_runner.CONDITIONS),
            "timeout_seconds": 10,
            "claude_executable": str(self.fake_cli),
            "codex_executable": str(self.fake_cli),
            "source_home": self.source_home,
        }
        values.update(overrides)
        return product_runner.RunOptions(**values)

    def invocation_records(self) -> list[dict]:
        if not self.invocation_log.exists():
            return []
        return [json.loads(line) for line in self.invocation_log.read_text().splitlines()]

    def test_complete_successes_resume_without_cli_invocation(self):
        config = self.config()
        manifest = product_runner.prepare_manifest(config)
        with mock.patch.dict(os.environ, {"FAKE_INVOCATION_LOG": str(self.invocation_log)}):
            first = product_runner.run_tasks(manifest, config, self.options())
            second = product_runner.run_tasks(manifest, config, self.options())

        self.assertEqual(first, product_runner.RunSummary(succeeded=6, skipped=0, failed=0))
        self.assertEqual(second, product_runner.RunSummary(succeeded=0, skipped=6, failed=0))
        self.assertEqual(len(self.invocation_records()), 6)
        for app in ("claude", "codex"):
            for condition in product_runner.CONDITIONS:
                path = self.output / app / f"{condition.replace('_', '-')}.csv"
                self.assertEqual(path.read_text(encoding="utf-8"), self.CSV)
        records = [
            json.loads(line)
            for line in (self.output / "evidence.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(records), 6)
        self.assertTrue(all(record["status"] == "success" for record in records))
        self.assertTrue(all(record["manifest_sha256"] == manifest.sha256 for record in records))

    def test_corrupt_output_is_invalid_and_force_replaces_only_selected_task(self):
        config = self.config()
        manifest = product_runner.prepare_manifest(config)
        with mock.patch.dict(os.environ, {"FAKE_INVOCATION_LOG": str(self.invocation_log)}):
            product_runner.run_tasks(manifest, config, self.options())
            corrupted = self.output / "codex" / "improved.csv"
            corrupted.write_text("corrupted\n", encoding="utf-8")
            states = product_runner.task_states(
                manifest,
                config,
                product_runner.TaskFilter(
                    frozenset({"codex"}), frozenset({"improved"})
                ),
            )
            self.assertEqual(states[0].status, "invalid")
            with self.assertRaisesRegex(BenchmarkError, "--force"):
                product_runner.run_tasks(
                    manifest,
                    config,
                    self.options(
                        apps=frozenset({"codex"}),
                        conditions=frozenset({"improved"}),
                    ),
                )
            summary = product_runner.run_tasks(
                manifest,
                config,
                self.options(
                    apps=frozenset({"codex"}),
                    conditions=frozenset({"improved"}),
                    force=True,
                ),
            )
        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(corrupted.read_text(encoding="utf-8"), self.CSV)
        self.assertEqual(len(self.invocation_records()), 7)

    def test_timeout_is_durable_and_does_not_rerun_completed_work(self):
        config = self.config()
        manifest = product_runner.prepare_manifest(config)
        options = self.options(
            apps=frozenset({"codex"}),
            conditions=frozenset({"normal", "current_suite"}),
            timeout_seconds=2,
        )
        environment = {
            "FAKE_INVOCATION_LOG": str(self.invocation_log),
            "FAKE_TIMEOUT_CONDITION": "current_suite",
        }
        with mock.patch.dict(os.environ, environment):
            with self.assertRaisesRegex(product_runner.ProductRunError, "timed out"):
                product_runner.run_tasks(manifest, config, options)
            with self.assertRaisesRegex(BenchmarkError, "--force"):
                product_runner.run_tasks(manifest, config, options)
        states = product_runner.task_states(
            manifest,
            config,
            product_runner.TaskFilter(
                frozenset({"codex"}), frozenset({"normal", "current_suite"})
            ),
        )
        self.assertEqual([state.status for state in states], ["completed", "failed"])
        self.assertEqual(len(self.invocation_records()), 2)


class ProductRunnerCliTests(ProductRunnerExecutionTests):
    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            status = product_runner.main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def run_arguments(self) -> tuple[str, ...]:
        return (
            "run",
            "--root",
            str(self.output),
            "--product-repo",
            str(self.product),
            "--product-git-object",
            self.product_commit,
            "--translation-context",
            str(self.context),
            "--suite-repo",
            str(self.suite),
            "--current-suite-git-object",
            self.old_commit,
            "--improved-suite-git-object",
            self.improved_commit,
            "--app",
            "all",
            "--claude-executable",
            str(self.fake_cli),
            "--codex-executable",
            str(self.fake_cli),
            "--claude-model",
            "claude-test",
            "--codex-model",
            "codex-test",
        )

    def test_cli_runs_statuses_and_inspects_all_conditions(self):
        with mock.patch.dict(os.environ, {"FAKE_INVOCATION_LOG": str(self.invocation_log)}):
            status, output, error = self.run_cli(*self.run_arguments())
        self.assertEqual((status, error), (0, ""))
        self.assertIn("completed=6", output)

        status, output, error = self.run_cli(
            "status", "--root", str(self.output), "--app", "all"
        )
        self.assertEqual((status, error), (0, ""))
        self.assertIn("claude: pending=0 completed=3 failed=0 invalid=0", output)
        self.assertIn("codex: pending=0 completed=3 failed=0 invalid=0", output)

        status, output, error = self.run_cli(
            "inspect", "--root", str(self.output), "--app", "all"
        )
        self.assertEqual((status, error), (0, ""))
        self.assertIn("claude: passed=3 failed=0 pending=0 invalid=0", output)
        self.assertIn("codex: passed=3 failed=0 pending=0 invalid=0", output)

    def test_cli_probe_runs_first_pending_condition_per_app(self):
        with mock.patch.dict(os.environ, {"FAKE_INVOCATION_LOG": str(self.invocation_log)}):
            status, output, error = self.run_cli(*self.run_arguments(), "--probe")
        self.assertEqual((status, error), (0, ""))
        self.assertIn("completed=2", output)
        self.assertEqual(
            [(row["condition"], "--sandbox" in row["argv"]) for row in self.invocation_records()],
            [("normal", False), ("normal", True)],
        )

    def test_inspect_rejects_cross_condition_source_mismatch(self):
        environment = {
            "FAKE_INVOCATION_LOG": str(self.invocation_log),
            "FAKE_MISMATCH_CONDITION": "improved",
        }
        with mock.patch.dict(os.environ, environment):
            status, _, error = self.run_cli(*self.run_arguments())
        self.assertEqual((status, error), (0, ""))

        status, _, error = self.run_cli(
            "inspect", "--root", str(self.output), "--app", "all"
        )
        self.assertEqual(status, 1)
        self.assertIn("source fields do not match", error)


if __name__ == "__main__":
    unittest.main()
