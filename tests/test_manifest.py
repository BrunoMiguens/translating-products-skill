import unittest
from pathlib import Path

from scripts.repo_model import load_manifest, skill_by_name


ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def test_manifest_has_expected_suite_and_orchestrator(self):
        manifest = load_manifest(ROOT / "skills-manifest.json")
        self.assertEqual(manifest.schema_version, 1)
        self.assertEqual(manifest.suite_version, "0.1.0")
        self.assertEqual(manifest.orchestrator, "translating-products")
        self.assertEqual(len(manifest.skills), 22)
        self.assertEqual(
            set(manifest.minimum_skill_versions),
            {skill.name for skill in manifest.skills if skill.name != manifest.orchestrator},
        )

    def test_arabic_declares_core_rtl_and_review_dependencies(self):
        manifest = load_manifest(ROOT / "skills-manifest.json")
        skill = skill_by_name(manifest, "translating-arabic")
        self.assertEqual(
            skill.depends_on,
            ("translating-core", "translating-rtl", "reviewing-translations"),
        )
        self.assertIn("language:arabic", skill.capabilities)

    def test_every_source_is_pinned_and_checksumed(self):
        manifest = load_manifest(ROOT / "skills-manifest.json")
        for source in manifest.sources:
            self.assertRegex(source.ref, r"^[0-9a-f]{40}$")
            self.assertRegex(source.sha256, r"^[0-9a-f]{64}$")
            self.assertIn(source.license, {"MIT", "BSD-3-Clause", "Apache-2.0"})
            self.assertEqual(source.mode, "adapted")
            self.assertTrue(source.capabilities)


if __name__ == "__main__":
    unittest.main()
