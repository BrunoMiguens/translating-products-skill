import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.render_catalog import render_catalog, render_readme_inventory


ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def test_readme_inventory_is_rendered_from_manifest_order(self):
        manifest = json.loads(
            (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "before\n<!-- skill-inventory:start -->\nstale\n"
                "<!-- skill-inventory:end -->\nafter\n",
                encoding="utf-8",
            )

            self.assertTrue(
                render_readme_inventory(ROOT / "skills-manifest.json", readme)
            )
            text = readme.read_text(encoding="utf-8")
            linked = [
                line.split("(skills/", 1)[1].split("/)", 1)[0]
                for line in text.splitlines()
                if "(skills/" in line
            ]
            self.assertEqual(linked, [item["name"] for item in manifest["skills"]])
            self.assertTrue(
                render_readme_inventory(
                    ROOT / "skills-manifest.json", readme, check=True
                )
            )

    def test_catalog_contains_every_manifest_skill(self):
        manifest = json.loads(
            (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "catalog.json"
            markdown_path = Path(tmp) / "catalog.md"

            self.assertTrue(
                render_catalog(
                    ROOT / "skills-manifest.json", json_path, markdown_path
                )
            )

            catalog = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["name"] for item in catalog["skills"]],
                [item["name"] for item in manifest["skills"]],
            )
            self.assertIn("Authority order", markdown_path.read_text(encoding="utf-8"))

    def test_catalog_entries_expose_only_routing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "catalog.json"
            markdown_path = Path(tmp) / "catalog.md"
            render_catalog(ROOT / "skills-manifest.json", json_path, markdown_path)

            catalog = json.loads(json_path.read_text(encoding="utf-8"))

        self.assertTrue(catalog["skills"])
        self.assertEqual(catalog["schema_version"], 2)
        self.assertEqual(
            set(catalog["skills"][0]),
            {
                "name",
                "version",
                "category",
                "capabilities",
                "depends_on",
                "selectors",
                "phases",
                "specificity",
                "required_context",
                "conflicts",
                "supersedes",
            },
        )

    def test_check_mode_reports_stale_outputs_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "catalog.json"
            markdown_path = Path(tmp) / "catalog.md"
            json_path.write_text("stale json\n", encoding="utf-8")
            markdown_path.write_text("stale markdown\n", encoding="utf-8")

            self.assertFalse(
                render_catalog(
                    ROOT / "skills-manifest.json",
                    json_path,
                    markdown_path,
                    check=True,
                )
            )
            self.assertEqual(json_path.read_text(encoding="utf-8"), "stale json\n")
            self.assertEqual(
                markdown_path.read_text(encoding="utf-8"), "stale markdown\n"
            )

    def test_check_cli_returns_one_for_stale_published_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            manifest = json.loads(
                (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
            )
            manifest["skills"] = []
            (root / "skills-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            script = ROOT / "scripts" / "render_catalog.py"

            result = subprocess.run(
                [sys.executable, str(script), "--root", str(root), "--check"],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)

    def test_compatibility_registry_starts_empty_and_versioned(self):
        registry = json.loads(
            (
                ROOT
                / "skills/translating-products/references/compatibility-registry.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(registry, {"schema_version": 1, "skills": []})


if __name__ == "__main__":
    unittest.main()
