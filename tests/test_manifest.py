import json
import tempfile
import unittest
from pathlib import Path

from scripts.repo_model import load_manifest, skill_by_name


ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def assert_manifest_rejected(self, field: str, value: object, message: str):
        manifest = json.loads(
            (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
        )
        manifest["skills"][0][field] = value
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skills-manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, message):
                load_manifest(path)

    def test_manifest_has_expected_suite_and_orchestrator(self):
        manifest = load_manifest(ROOT / "skills-manifest.json")
        self.assertEqual(manifest.schema_version, 2)
        self.assertEqual(manifest.suite_version, "0.2.0")
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

    def test_manifest_exposes_declarative_routing_metadata(self):
        manifest = load_manifest(ROOT / "skills-manifest.json")
        self.assertEqual(manifest.schema_version, 2)
        self.assertEqual(manifest.suite_version, "0.2.0")

        web = skill_by_name(manifest, "translating-web")
        self.assertEqual(
            tuple(selector.as_dict() for selector in web.selectors),
            (
                {"surfaces": ("web",)},
                {"formats": ("html", "markdown")},
            ),
        )
        self.assertEqual(web.phases, ("inspect", "integrate"))
        self.assertEqual(web.specificity, "surface")
        self.assertEqual(web.required_context, ("target_locale",))
        self.assertEqual(web.conflicts, ())
        self.assertEqual(web.supersedes, ())

    def test_language_and_quality_records_have_distinct_ownership(self):
        manifest = load_manifest(ROOT / "skills-manifest.json")
        portuguese = skill_by_name(manifest, "translating-portuguese")
        review = skill_by_name(manifest, "reviewing-translations")

        self.assertEqual(
            tuple(selector.as_dict() for selector in portuguese.selectors),
            ({"languages": ("pt",)},),
        )
        self.assertEqual(portuguese.phases, ("refine",))
        self.assertEqual(portuguese.specificity, "language")
        self.assertEqual(review.phases, ("review",))
        self.assertEqual(review.specificity, "quality")

    def test_manifest_rejects_invalid_routing_phases(self):
        self.assert_manifest_rejected(
            "phases", [], "phases must be a non-empty string list"
        )
        self.assert_manifest_rejected(
            "phases", ["route"], "unknown routing phases: route"
        )

    def test_manifest_rejects_invalid_routing_specificity(self):
        self.assert_manifest_rejected(
            "specificity",
            "unsupported",
            "specificity must be one of: universal, writing-system, language, locale, surface, platform, format, domain, quality",
        )

    def test_manifest_rejects_malformed_routing_collections(self):
        for field, value, message in (
            ("selectors", [], "selectors must be a non-empty selector list"),
            ("required_context", "target_locale", "required_context must be a string list"),
            ("conflicts", [""], "conflicts must be a string list"),
            ("supersedes", [1], "supersedes must be a string list"),
        ):
            with self.subTest(field=field):
                self.assert_manifest_rejected(field, value, message)

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
