from __future__ import annotations

import json
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.benchmark.common import BenchmarkError
from scripts.benchmark import product_runner, product_setup


def run_git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


class ProductSetupStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.product = self.root / "product"
        self.suite = self.root / "suite"
        self.context = self.root / "approved-context"
        self.product.mkdir()
        self.suite.mkdir()
        for repository in (self.product, self.suite):
            run_git(repository, "init")
            run_git(repository, "config", "user.email", "setup@example.invalid")
            run_git(repository, "config", "user.name", "Setup Tests")

        self.product_file = self.product / "emails.json"
        self.product_file.write_text("committed\n", encoding="utf-8")
        run_git(self.product, "add", ".")
        run_git(self.product, "commit", "-m", "product")
        self.product_commit = run_git(self.product, "rev-parse", "HEAD")

        self._write_suite("current")
        run_git(self.suite, "add", ".")
        run_git(self.suite, "commit", "-m", "current")
        self.current_commit = run_git(self.suite, "rev-parse", "HEAD")
        self._write_suite("improved")
        run_git(self.suite, "add", ".")
        run_git(self.suite, "commit", "-m", "improved")
        self.improved_commit = run_git(self.suite, "rev-parse", "HEAD")

        self.source_home = self.root / "source-home"
        (self.source_home / ".codex").mkdir(parents=True)
        (self.source_home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")

    def _write_suite(self, marker: str) -> None:
        skill = self.suite / "skills" / "translating-products"
        (skill / "scripts").mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: translating-products\ndescription: {marker}\n---\n",
            encoding="utf-8",
        )
        (skill / "scripts" / "policy.py").write_text(
            """import argparse
import hashlib
import json
from pathlib import Path

files = ('project-brief.md', 'locales.yaml', 'glossary.csv', 'style-guide.md', 'protected-terms.txt')
parser = argparse.ArgumentParser()
commands = parser.add_subparsers(dest='command', required=True)
bootstrap = commands.add_parser('bootstrap')
bootstrap.add_argument('--project-root', required=True)
bootstrap.add_argument('--request-json')
approve = commands.add_parser('approve')
approve.add_argument('--project-root', required=True)
approve.add_argument('--approved-by', required=True)
approve.add_argument('--approved-at', required=True)
approve.add_argument('--approved-empty', action='append', default=[])
args = parser.parse_args()
translation = Path(args.project_root) / '.translation'
if args.command == 'approve':
    hashes = {name: hashlib.sha256((translation / name).read_bytes()).hexdigest() for name in files}
    record = {'status': 'approved', 'approved_by': args.approved_by, 'approved_at': args.approved_at, 'context_sha256': hashes, 'approved_empty': args.approved_empty}
    (translation / 'setup-approval.json').write_text(json.dumps(record, sort_keys=True) + '\\n', encoding='utf-8')
    print(json.dumps(record, sort_keys=True))
else:
    try:
        record = json.loads((translation / 'setup-approval.json').read_text(encoding='utf-8'))
        valid = record.get('status') == 'approved' and all(record['context_sha256'][name] == hashlib.sha256((translation / name).read_bytes()).hexdigest() for name in files)
    except Exception:
        valid = False
    print(json.dumps({'action': 'translate' if valid else 'setup-one-question-at-a-time'}))
""",
            encoding="utf-8",
        )

    def options(self, *, app: str = "codex", executable: Path | None = None):
        return product_setup.ProductSetupOptions(
            product_repo=self.product,
            product_git_object=self.product_commit,
            translation_context=self.context,
            suite_repo=self.suite,
            current_suite_git_object=self.current_commit,
            improved_suite_git_object=self.improved_commit,
            app=app,
            approved_by="Example Reviewer",
            executable=str(executable) if executable is not None else None,
            model="setup-test",
            source_home=self.source_home,
        )

    def test_stages_exact_product_and_selected_suite(self):
        self.product_file.write_text("dirty\n", encoding="utf-8")
        stage = product_setup.stage_setup_project(
            self.options(app="codex"), self.root / "stage"
        )

        self.assertEqual((stage.project / "emails.json").read_text(), "committed\n")
        skill = stage.project / ".agents" / "skills" / "translating-products"
        self.assertIn("improved", (skill / "SKILL.md").read_text())
        self.assertEqual(stage.policy, skill / "scripts" / "policy.py")
        self.assertFalse((stage.project / ".claude").exists())
        self.assertEqual(stage.product_commit, self.product_commit)
        self.assertEqual(stage.suite_commit, self.improved_commit)

    def test_claude_stages_only_its_project_skill_root(self):
        stage = product_setup.stage_setup_project(
            self.options(app="claude"), self.root / "stage"
        )
        self.assertTrue(
            (
                stage.project
                / ".claude"
                / "skills"
                / "translating-products"
                / "SKILL.md"
            ).is_file()
        )
        self.assertFalse((stage.project / ".agents").exists())

    def test_context_must_be_outside_product_checkout(self):
        with self.assertRaisesRegex(BenchmarkError, "outside the product repository"):
            product_setup.ProductSetupOptions(
                **{
                    **self.options().__dict__,
                    "translation_context": self.product / ".translation",
                }
            )

    def test_context_path_rejects_symlink_target_or_parent(self):
        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        linked_target = self.root / "linked-target"
        linked_target.symlink_to(real_parent, target_is_directory=True)

        for context in (linked_parent / "context", linked_target):
            with self.subTest(context=context):
                with self.assertRaisesRegex(BenchmarkError, "symlink"):
                    product_setup.ProductSetupOptions(
                        **{
                            **self.options().__dict__,
                            "translation_context": context,
                        }
                    )


class ProductSetupAdapterTests(ProductSetupStagingTests):
    def setUp(self):
        super().setUp()
        self.invocation_log = self.root / "setup-invocations.jsonl"
        self.fake_agent = self.root / "fake-agent"
        self.fake_agent.write_text(
            """#!/opt/homebrew/bin/python3
import json
import os
import pathlib
import sys
import time
if '--version' in sys.argv:
    print('fake-agent 1.0')
    raise SystemExit(0)
if os.environ.get('PRODUCT_REVIEW_CONDITION'):
    response = json.dumps({'rows': [{
        'locale': 'pt-PT',
        'key': 'welcome',
        'english_source': 'Welcome',
        'current_translation': 'Olá',
        'status': 'no_issue_detected',
        'reason': 'Natural and faithful.',
        'resolved_translation': 'Olá',
    }]}, ensure_ascii=False)
    if '--print' in sys.argv:
        print(json.dumps({'result': response, 'modelUsage': {'claude-observed': {}}}))
    else:
        output = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])
        output.write_text(response, encoding='utf-8')
        print(json.dumps({'type': 'turn.completed', 'model': 'codex-observed', 'usage': {'tokens': 1}}))
    raise SystemExit(0)
mode = os.environ.get("SETUP_MODE", "record-only")
record = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "home": os.environ.get("HOME"),
    "codex_home": os.environ.get("CODEX_HOME"),
    "claude_config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
    "stdin_isatty": sys.stdin.isatty(),
    "stdin_text": sys.stdin.read(),
    "auth_exists": (
        pathlib.Path(os.environ["CODEX_HOME"]) / "auth.json"
    ).is_file() if os.environ.get("CODEX_HOME") else False,
    "auth_mode": oct((
        pathlib.Path(os.environ["CODEX_HOME"]) / "auth.json"
    ).stat().st_mode & 0o777) if os.environ.get("CODEX_HOME") and (
        pathlib.Path(os.environ["CODEX_HOME"]) / "auth.json"
    ).is_file() else None,
}
with pathlib.Path(os.environ["SETUP_INVOCATION_LOG"]).open("a", encoding="utf-8") as out:
    out.write(json.dumps(record, sort_keys=True) + "\\n")
if mode != "record-only":
    translation = pathlib.Path.cwd() / ".translation"
    translation.mkdir(exist_ok=True)
    values = {
        "project-brief.md": "Status: approved\\nProduct: Push emails\\n",
        "locales.yaml": "source_locale: en-US\\ntarget_locales: [pt-PT]\\n",
        "glossary.csv": "source,target,locale,status\\n",
        "style-guide.md": "Status: approved\\nRegister: formal\\n",
        "protected-terms.txt": "Push\\n",
    }
    if mode == "missing-file":
        values.pop("style-guide.md")
    for name, value in values.items():
        (translation / name).write_text(value, encoding="utf-8")
    if mode == "agent-approval":
        (translation / "setup-approval.json").write_text("{}\\n", encoding="utf-8")
if mode == "fail":
    raise SystemExit(7)
if mode == "timeout":
    time.sleep(1)
""",
            encoding="utf-8",
        )
        self.fake_agent.chmod(0o755)

    def records(self):
        return [
            json.loads(line)
            for line in self.invocation_log.read_text(encoding="utf-8").splitlines()
        ]

    def test_codex_runs_noninteractively_in_isolated_workspace(self):
        options = self.options(app="codex", executable=self.fake_agent)
        stage = product_setup.stage_setup_project(options, self.root / "stage")
        with mock.patch.dict(
            os.environ, {"SETUP_INVOCATION_LOG": str(self.invocation_log)}
        ):
            product_setup.run_setup_agent(stage, options)

        record = self.records()[0]
        self.assertEqual(Path(record["cwd"]).resolve(), stage.project.resolve())
        self.assertEqual(record["argv"][:8], [
            "exec", "-m", "setup-test", "-C", str(stage.project),
            "--sandbox", "workspace-write", "--ephemeral",
        ])
        self.assertEqual(record["argv"][8], "--ignore-user-config")
        self.assertEqual(record["argv"][9], "--skip-git-repo-check")
        self.assertEqual(record["argv"][10], "--output-last-message")
        self.assertEqual(record["argv"][-1], product_setup.SETUP_PROMPT)
        self.assertNotIn("--ask-for-approval", record["argv"])
        self.assertFalse(record["stdin_isatty"])
        self.assertEqual(record["stdin_text"], "")
        self.assertNotEqual(record["home"], str(self.source_home))
        self.assertNotEqual(record["codex_home"], str(self.source_home / ".codex"))
        self.assertTrue(record["auth_exists"])
        self.assertEqual(record["auth_mode"], "0o600")
        self.assertFalse((Path(record["codex_home"]) / "auth.json").exists())

    def test_claude_runs_noninteractively_with_project_settings(self):
        options = self.options(app="claude", executable=self.fake_agent)
        stage = product_setup.stage_setup_project(options, self.root / "stage")
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "CLAUDE_CONFIG_DIR": "must-be-removed",
        }
        with mock.patch.dict(os.environ, environment):
            product_setup.run_setup_agent(stage, options)

        record = self.records()[0]
        self.assertEqual(Path(record["cwd"]).resolve(), stage.project.resolve())
        self.assertEqual(
            record["argv"],
            [
                "--model",
                "setup-test",
                "--print",
                "--no-session-persistence",
                "--setting-sources",
                "project",
                "--permission-mode",
                "acceptEdits",
                "--no-chrome",
                "--tools",
                "Read,Glob,Grep,Edit,Write,Bash",
                product_setup.SETUP_PROMPT,
            ],
        )
        self.assertIsNone(record["claude_config_dir"])
        self.assertFalse(record["stdin_isatty"])
        self.assertEqual(record["stdin_text"], "")

    def test_autonomous_setup_timeout_is_bounded(self):
        options = product_setup.ProductSetupOptions(
            **{**self.options(app="codex", executable=self.fake_agent).__dict__,
               "timeout_seconds": 0.01}
        )
        stage = product_setup.stage_setup_project(options, self.root / "stage")
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "timeout",
        }
        with mock.patch.dict(os.environ, environment):
            with self.assertRaisesRegex(BenchmarkError, "timed out"):
                product_setup.run_setup_agent(stage, options)


class ProductSetupApprovalTests(ProductSetupAdapterTests):
    def setup_options(self, **overrides):
        values = {
            **self.options(app="codex", executable=self.fake_agent).__dict__,
            **overrides,
        }
        return product_setup.ProductSetupOptions(**values)

    def test_exact_terminal_approval_binds_and_publishes(self):
        emitted: list[str] = []
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "proposal",
        }
        with mock.patch.dict(os.environ, environment):
            changed = product_setup.ensure_translation_context(
                self.setup_options(),
                confirm=lambda _: "approve",
                emit=emitted.append,
            )

        self.assertTrue(changed)
        approval = json.loads(
            (self.context / "setup-approval.json").read_text(encoding="utf-8")
        )
        self.assertEqual(approval["approved_by"], "Example Reviewer")
        self.assertEqual(approval["approved_empty"], ["glossary.csv"])
        self.assertTrue(any("glossary.csv: empty" in line for line in emitted))
        self.assertTrue(any("Product: Push emails" in line for line in emitted))

    def test_declined_approval_does_not_publish(self):
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "proposal",
        }
        with mock.patch.dict(os.environ, environment):
            with self.assertRaisesRegex(BenchmarkError, "approval token"):
                product_setup.ensure_translation_context(
                    self.setup_options(), confirm=lambda _: "yes"
                )
        self.assertFalse(self.context.exists())

    def test_agent_created_approval_is_rejected(self):
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "agent-approval",
        }
        with mock.patch.dict(os.environ, environment):
            with self.assertRaisesRegex(BenchmarkError, "must not create"):
                product_setup.ensure_translation_context(
                    self.setup_options(), confirm=lambda _: "approve"
                )
        self.assertFalse(self.context.exists())

    def test_invalid_proposal_and_host_failure_do_not_publish(self):
        for mode, message in (("missing-file", "proposal"), ("fail", "status 7")):
            with self.subTest(mode=mode):
                environment = {
                    "SETUP_INVOCATION_LOG": str(self.invocation_log),
                    "SETUP_MODE": mode,
                }
                with mock.patch.dict(os.environ, environment):
                    with self.assertRaisesRegex(BenchmarkError, message):
                        product_setup.ensure_translation_context(
                            self.setup_options(), confirm=lambda _: "approve"
                        )
                self.assertFalse(self.context.exists())

    def test_ready_context_skips_host_and_confirmation(self):
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "proposal",
        }
        with mock.patch.dict(os.environ, environment):
            product_setup.ensure_translation_context(
                self.setup_options(), confirm=lambda _: "approve"
            )
        invocation_count = len(self.records())

        def forbidden_confirmation(_: str) -> str:
            self.fail("ready context requested approval again")

        changed = product_setup.ensure_translation_context(
            self.setup_options(), confirm=forbidden_confirmation
        )
        self.assertFalse(changed)
        self.assertEqual(len(self.records()), invocation_count)

    def test_existing_incomplete_context_requires_replace(self):
        self.context.mkdir()
        (self.context / "project-brief.md").write_text("draft\n", encoding="utf-8")
        with self.assertRaisesRegex(BenchmarkError, "--replace-context"):
            product_setup.ensure_translation_context(self.setup_options())

    def test_replace_context_publishes_only_after_successful_approval(self):
        self.context.mkdir()
        (self.context / "project-brief.md").write_text("old draft\n", encoding="utf-8")
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "proposal",
        }
        with mock.patch.dict(os.environ, environment):
            changed = product_setup.ensure_translation_context(
                self.setup_options(replace_context=True),
                confirm=lambda _: "approve",
                emit=lambda _: None,
            )

        self.assertTrue(changed)
        self.assertIn(
            "Product: Push emails",
            (self.context / "project-brief.md").read_text(encoding="utf-8"),
        )
        self.assertTrue((self.context / "setup-approval.json").is_file())

    def test_failed_replacement_preserves_existing_context(self):
        self.context.mkdir()
        original = b"old draft\n"
        (self.context / "project-brief.md").write_bytes(original)
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "missing-file",
        }
        with mock.patch.dict(os.environ, environment):
            with self.assertRaisesRegex(BenchmarkError, "proposal"):
                product_setup.ensure_translation_context(
                    self.setup_options(replace_context=True),
                    confirm=lambda _: "approve",
                    emit=lambda _: None,
                )

        self.assertEqual((self.context / "project-brief.md").read_bytes(), original)
        self.assertEqual(
            [path.name for path in self.context.iterdir()], ["project-brief.md"]
        )


class ProductSetupCliTests(ProductSetupAdapterTests):
    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                status = product_runner.main(list(arguments))
        except SystemExit as error:
            status = int(error.code)
        return status, stdout.getvalue(), stderr.getvalue()

    def run_arguments(self) -> tuple[str, ...]:
        return (
            "run",
            "--root",
            str(self.root / "runs"),
            "--product-repo",
            str(self.product),
            "--product-git-object",
            self.product_commit,
            "--translation-context",
            str(self.context),
            "--suite-repo",
            str(self.suite),
            "--current-suite-git-object",
            self.current_commit,
            "--improved-suite-git-object",
            self.improved_commit,
            "--app",
            "all",
            "--claude-executable",
            str(self.fake_agent),
            "--codex-executable",
            str(self.fake_agent),
            "--setup-app",
            "codex",
            "--approved-by",
            "Example Reviewer",
            "--setup-model",
            "setup-test",
        )

    def test_run_sets_up_context_then_runs_all_conditions(self):
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "proposal",
        }
        with mock.patch.dict(os.environ, environment), mock.patch(
            "builtins.input", return_value="approve"
        ):
            status, output, error = self.run_cli(*self.run_arguments())

        self.assertEqual((status, error), (0, ""))
        self.assertTrue((self.context / "setup-approval.json").is_file())
        self.assertTrue((self.root / "runs" / "manifest.json").is_file())
        self.assertIn("completed=6", output)
        self.assertEqual(len(self.records()), 1)

        with mock.patch.dict(os.environ, {**environment, "SETUP_MODE": "fail"}), mock.patch(
            "builtins.input", side_effect=AssertionError("ready context asked again")
        ):
            status, output, error = self.run_cli(*self.run_arguments())
        self.assertEqual((status, error), (0, ""))
        self.assertIn("completed=0 skipped=6", output)
        self.assertEqual(len(self.records()), 1)

    def test_probe_sets_up_once_then_runs_one_condition_per_host(self):
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "proposal",
        }
        with mock.patch.dict(os.environ, environment), mock.patch(
            "builtins.input", return_value="approve"
        ):
            status, output, error = self.run_cli(*self.run_arguments(), "--probe")

        self.assertEqual((status, error), (0, ""))
        self.assertIn("completed=2", output)
        self.assertEqual(len(self.records()), 1)

    def test_declined_or_unauthorized_setup_creates_no_run(self):
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "SETUP_MODE": "proposal",
        }
        with mock.patch.dict(os.environ, environment), mock.patch(
            "builtins.input", return_value="no"
        ):
            status, _, error = self.run_cli(*self.run_arguments())
        self.assertEqual(status, 2)
        self.assertIn("approval token", error)
        self.assertFalse((self.root / "runs" / "manifest.json").exists())
        self.assertFalse(self.context.exists())

        missing_approver = list(self.run_arguments())
        index = missing_approver.index("--approved-by")
        del missing_approver[index : index + 2]
        status, _, error = self.run_cli(*missing_approver)
        self.assertEqual(status, 2)
        self.assertIn("provided together", error)

        without_setup = list(self.run_arguments())
        del without_setup[without_setup.index("--setup-app") :]
        status, _, error = self.run_cli(*without_setup)
        self.assertEqual(status, 2)
        self.assertIn("--setup-app", error)
        self.assertFalse((self.root / "runs" / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
