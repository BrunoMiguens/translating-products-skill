from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.benchmark.common import BenchmarkError
from scripts.benchmark import product_setup


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
            "print('policy')\n", encoding="utf-8"
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
record = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "home": os.environ.get("HOME"),
    "codex_home": os.environ.get("CODEX_HOME"),
    "claude_config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
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
""",
            encoding="utf-8",
        )
        self.fake_agent.chmod(0o755)

    def records(self):
        return [
            json.loads(line)
            for line in self.invocation_log.read_text(encoding="utf-8").splitlines()
        ]

    def test_codex_uses_isolated_interactive_workspace(self):
        options = self.options(app="codex", executable=self.fake_agent)
        stage = product_setup.stage_setup_project(options, self.root / "stage")
        with mock.patch.dict(
            os.environ, {"SETUP_INVOCATION_LOG": str(self.invocation_log)}
        ):
            product_setup.launch_setup_session(stage, options)

        record = self.records()[0]
        self.assertEqual(Path(record["cwd"]).resolve(), stage.project.resolve())
        self.assertEqual(
            record["argv"],
            [
                "-m",
                "setup-test",
                "-C",
                str(stage.project),
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "on-request",
                "--no-alt-screen",
                product_setup.SETUP_PROMPT,
            ],
        )
        self.assertNotEqual(record["home"], str(self.source_home))
        self.assertNotEqual(record["codex_home"], str(self.source_home / ".codex"))
        self.assertTrue(record["auth_exists"])
        self.assertEqual(record["auth_mode"], "0o600")
        self.assertFalse((Path(record["codex_home"]) / "auth.json").exists())

    def test_claude_uses_project_settings_without_print_mode(self):
        options = self.options(app="claude", executable=self.fake_agent)
        stage = product_setup.stage_setup_project(options, self.root / "stage")
        environment = {
            "SETUP_INVOCATION_LOG": str(self.invocation_log),
            "CLAUDE_CONFIG_DIR": "must-be-removed",
        }
        with mock.patch.dict(os.environ, environment):
            product_setup.launch_setup_session(stage, options)

        record = self.records()[0]
        self.assertEqual(Path(record["cwd"]).resolve(), stage.project.resolve())
        self.assertEqual(
            record["argv"],
            [
                "--model",
                "setup-test",
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


if __name__ == "__main__":
    unittest.main()
