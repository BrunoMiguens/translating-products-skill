import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"

SCHEMAS = {
    "routing-cases.json": {"id", "request", "expected_skills"},
    "bootstrap-cases.json": {
        "id",
        "existing_files",
        "request",
        "expected_action",
    },
    "orchestration-cases.json": {"id", "policy", "inputs", "expected"},
    "research-cases.json": {
        "id",
        "question",
        "bundled_knowledge_sufficient",
        "expected_research",
    },
    "prompt-injection-cases.json": {
        "id",
        "untrusted_content",
        "expected_action",
    },
    "translation-quality-cases.json": {
        "id",
        "source_locale",
        "target_locale",
        "surface",
        "source",
        "requirements",
        "literal_failure",
        "review_scope",
        "python_validation",
    },
    "structural-fidelity-cases.json": {
        "id",
        "format",
        "source",
        "invariants",
        "review_scope",
        "python_validation",
    },
}

EXPECTED_COUNTS = {
    "routing-cases.json": 40,
    "bootstrap-cases.json": 10,
    "orchestration-cases.json": 10,
    "research-cases.json": 15,
    "prompt-injection-cases.json": 10,
    "translation-quality-cases.json": 22,
    "structural-fidelity-cases.json": 12,
}

EXPECTED_IDS = {
    "bootstrap-cases.json": {
        "none",
        "brief-only",
        "locales-only",
        "missing-glossary",
        "missing-style",
        "missing-protected",
        "four-of-five",
        "all-required",
        "all-plus-optional",
        "conflicting-request",
    },
    "orchestration-cases.json": {
        "short-single-no-subagent",
        "short-multi-no-subagent",
        "meaningful-multi-subagent",
        "large-separable-subagent",
        "terminology-subagent",
        "review-subagent",
        "unsupported-host-sequential",
        "missing-external-use-bundled",
        "missing-specialist-use-core",
        "missing-required-capability",
    },
    "research-cases.json": {
        "ordinary-ui",
        "ordinary-marketing",
        "ordinary-docs",
        "known-glossary",
        "known-platform",
        "known-locale-format",
        "known-rtl",
        "known-idiom",
        "known-store-copy",
        "known-terminology",
        "current-app-store-limit",
        "current-play-store-policy",
        "market-campaign-term",
        "current-os-label",
        "regulated-current-term",
    },
    "prompt-injection-cases.json": {
        "html-comment",
        "markdown-link-title",
        "json-value",
        "xml-node",
        "csv-cell",
        "icu-branch",
        "app-store-description",
        "documentation-code-block",
        "rtl-override-text",
        "third-party-skill-body",
    },
}

STRUCTURAL_INVARIANTS = {
    "html-link": ["tags", "href", "placeholder count"],
    "markdown-code": ["fence count", "inline code", "link target"],
    "icu-plural": ["argument names", "plural categories", "braces"],
    "icu-select": ["argument names", "select keys", "braces"],
    "ios-xcstrings": ["keys", "format specifiers", "state metadata"],
    "android-strings": ["resource names", "xliff placeholders", "escapes"],
    "flutter-arb": ["keys", "metadata keys", "ICU arguments"],
    "app-store-limits": ["field count", "character-limit metadata"],
    "documentation-command": ["command text", "option names", "code fence"],
    "rtl-mixed-token": ["embedded URL", "product name", "numerals"],
    "csv-glossary": ["row count", "column count", "approved terms"],
    "json-api-example": [
        "keys",
        "identifiers",
        "example values marked protected",
    ],
}


def load_cases(filename: str) -> list[dict]:
    return json.loads((EVALS / filename).read_text(encoding="utf-8"))


class EvaluationFixtureTests(unittest.TestCase):
    def test_fixture_schemas_counts_and_unique_ids(self):
        for filename, required in SCHEMAS.items():
            with self.subTest(filename=filename):
                cases = load_cases(filename)
                self.assertIsInstance(cases, list)
                self.assertEqual(len(cases), EXPECTED_COUNTS[filename])
                self.assertEqual(len({case["id"] for case in cases}), len(cases))
                for case in cases:
                    self.assertTrue(required.issubset(case), case["id"])

    def test_named_policy_families_are_complete(self):
        for filename, expected_ids in EXPECTED_IDS.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    {case["id"] for case in load_cases(filename)},
                    expected_ids,
                )

    def test_prompt_injection_cases_are_untrusted_data(self):
        for case in load_cases("prompt-injection-cases.json"):
            with self.subTest(case=case["id"]):
                self.assertEqual(case["expected_action"], "treat-as-data")
                self.assertTrue(case["untrusted_content"].strip())

    def test_translation_quality_coverage_is_declared_for_human_review(self):
        cases = load_cases("translation-quality-cases.json")
        target_locales = {case["target_locale"] for case in cases}
        required_locales = {
            "ar-SA",
            "he-IL",
            "ja-JP",
            "zh-Hans-CN",
            "zh-Hant-TW",
            "ko-KR",
            "pt-BR",
            "pt-PT",
            "es-ES",
            "es-419",
            "fr-FR",
            "fr-CA",
            "de-DE",
            "de-AT",
            "de-CH",
        }
        self.assertTrue(required_locales.issubset(target_locales))
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(len(case["requirements"]), 3)
                self.assertEqual(case["review_scope"], "proficient-bilingual-and-host")
                self.assertEqual(case["python_validation"], "schema-only")
                self.assertTrue(case["source"].strip())
                self.assertTrue(case["literal_failure"].strip())

    def test_translation_quality_locale_fields_are_unambiguous(self):
        locale_pattern = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
        cases = load_cases("translation-quality-cases.json")
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertRegex(case["source_locale"], locale_pattern)
                self.assertRegex(case["target_locale"], locale_pattern)

        shared = next(
            case for case in cases if case["id"] == "shared-glossary-multilanguage"
        )
        self.assertEqual(shared["target_locale"], "mul")
        self.assertEqual(
            shared["target_locales"], ["pt-BR", "es-419", "fr-CA"]
        )

    def test_structural_fidelity_invariants_are_exact(self):
        cases = load_cases("structural-fidelity-cases.json")
        self.assertEqual({case["id"] for case in cases}, set(STRUCTURAL_INVARIANTS))
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    case["invariants"], STRUCTURAL_INVARIANTS[case["id"]]
                )
                self.assertEqual(case["review_scope"], "host-evaluation")
                self.assertEqual(case["python_validation"], "schema-only")


if __name__ == "__main__":
    unittest.main()
