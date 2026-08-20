import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.render_catalog import (
    render_catalog,
    render_readme_inventory,
    render_release_checklist_version,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "skills/translating-products/scripts/metadata_contract.py"


def load_contract():
    spec = importlib.util.spec_from_file_location("metadata_contract", CONTRACT)
    contract = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(contract)
    return contract


class CatalogTests(unittest.TestCase):
    def test_skill_verification_defaults_to_no_independent_review(self):
        contract = load_contract()
        skill = json.loads(
            (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
        )["skills"][0]

        validated = contract.validate_skill_record(skill)

        self.assertEqual(
            validated["verification"],
            {"independent_review_required": False},
        )

    def test_skill_verification_accepts_independent_review_requirement(self):
        contract = load_contract()
        skill = json.loads(
            (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
        )["skills"][0]
        skill["verification"] = {"independent_review_required": True}

        validated = contract.validate_skill_record(skill)

        self.assertEqual(
            validated["verification"],
            {"independent_review_required": True},
        )

    def test_skill_verification_rejects_unknown_fields(self):
        contract = load_contract()
        skill = json.loads(
            (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
        )["skills"][0]
        skill["verification"] = {"unexpected": True}

        with self.assertRaisesRegex(ValueError, "invalid verification"):
            contract.validate_skill_record(skill)

    def test_skill_verification_rejects_non_boolean_requirement(self):
        contract = load_contract()
        skill = json.loads(
            (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
        )["skills"][0]
        skill["verification"] = {"independent_review_required": 1}

        with self.assertRaisesRegex(ValueError, "verification must use a boolean"):
            contract.validate_skill_record(skill)

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
            self.assertIn("Suite version: `0.4.0`", text)
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
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertIn("- Independent review required: `true`", markdown)
            self.assertIn("- Independent review required: `false`", markdown)

    def test_catalog_disclosure_is_generated_and_check_detects_its_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "catalog.json"
            markdown_path = Path(tmp) / "catalog.md"
            render_catalog(ROOT / "skills-manifest.json", json_path, markdown_path)

            markdown = markdown_path.read_text(encoding="utf-8")
            disclosure = (
                "Translations produced with this suite are AI-generated and have "
                "not been reviewed by a human translator."
            )
            self.assertIn(disclosure, markdown)
            self.assertEqual(
                json.loads(json_path.read_text(encoding="utf-8"))["disclosure"],
                disclosure,
            )

            markdown_path.write_text(
                markdown.replace(disclosure, ""), encoding="utf-8"
            )
            self.assertFalse(
                render_catalog(
                    ROOT / "skills-manifest.json",
                    json_path,
                    markdown_path,
                    check=True,
                )
            )

    def test_release_checklist_version_is_rendered_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads(
                (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
            )
            manifest["suite_version"] = "9.8.7"
            for skill in manifest["skills"]:
                if skill["name"] == manifest["orchestrator"]:
                    skill["version"] = "9.8.7"
                    break
            manifest_path = root / "skills-manifest.json"
            checklist = root / "release-checklist.md"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            checklist.write_text(
                "Version: `<!-- suite-version:start -->stale"
                "<!-- suite-version:end -->`\n",
                encoding="utf-8",
            )

            self.assertTrue(render_release_checklist_version(manifest_path, checklist))
            self.assertEqual(
                checklist.read_text(encoding="utf-8"),
                "Version: `<!-- suite-version:start -->9.8.7"
                "<!-- suite-version:end -->`\n",
            )
            self.assertTrue(
                render_release_checklist_version(manifest_path, checklist, check=True)
            )

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
                "description",
                "capabilities",
                "depends_on",
                "selectors",
                "phases",
                "specificity",
                "required_context",
                "conflicts",
                "supersedes",
                "ownership",
                "verification",
            },
        )
        self.assertEqual(
            catalog["skills"][0]["verification"],
            {"independent_review_required": False},
        )

    def test_catalog_preserves_absent_selectors_as_universal_scope(self):
        manifest = json.loads(
            (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
        )
        del manifest["skills"][0]["selectors"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "skills-manifest.json"
            json_path = root / "catalog.json"
            markdown_path = root / "catalog.md"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            render_catalog(manifest_path, json_path, markdown_path)
            catalog = json.loads(json_path.read_text(encoding="utf-8"))

        self.assertNotIn("selectors", catalog["skills"][0])

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
        self.assertEqual(registry, {"schema_version": 2, "skills": []})


if __name__ == "__main__":
    unittest.main()
