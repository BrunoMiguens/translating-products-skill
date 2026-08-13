from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

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
        self.addCleanup(self.temporary.cleanup)
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


if __name__ == "__main__":
    unittest.main()
