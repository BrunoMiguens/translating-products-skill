import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "skills/translating-products/scripts/route_capabilities.py"
POLICY = ROOT / "skills/translating-products/scripts/policy.py"
CATALOG = (
    ROOT / "skills/translating-products/references/capability-catalog.json"
)
EVALS = ROOT / "evals"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def load_cases(name: str) -> list[dict]:
    return json.loads((EVALS / name).read_text(encoding="utf-8"))


class RoutingTests(unittest.TestCase):
    def test_all_routing_evaluations_match_exact_order(self):
        router = load_module("route_capabilities_evals", ROUTER)
        catalog = load_catalog()
        cases = load_cases("routing-cases.json")
        mismatches = []

        for case in cases:
            actual = router.route(case["request"], catalog)
            if actual != case["expected_skills"]:
                mismatches.append(
                    {
                        "id": case["id"],
                        "expected": case["expected_skills"],
                        "actual": actual,
                    }
                )

        accuracy = (len(cases) - len(mismatches)) / len(cases)
        self.assertEqual(
            mismatches,
            [],
            f"routing exact-match accuracy was {accuracy:.1%}",
        )
        self.assertGreaterEqual(accuracy, 0.95)

    def test_arabic_marketing_web_route(self):
        router = load_module("route_capabilities", ROUTER)
        request = {
            "languages": ["arabic"],
            "locales": ["ar-SA"],
            "surfaces": ["web", "marketing"],
            "domains": [],
            "scripts": ["rtl"],
        }

        self.assertEqual(
            router.route(request, load_catalog()),
            [
                "translating-core",
                "translating-web",
                "translating-marketing",
                "translating-rtl",
                "translating-arabic",
                "reviewing-translations",
            ],
        )

    def test_unknown_language_uses_core_without_inventing_specialist(self):
        router = load_module("route_capabilities", ROUTER)
        request = {
            "languages": ["icelandic"],
            "locales": ["is-IS"],
            "surfaces": ["documentation"],
            "domains": [],
            "scripts": ["latin"],
        }

        self.assertEqual(
            router.route(request, load_catalog()),
            [
                "translating-core",
                "translating-documentation",
                "reviewing-translations",
            ],
        )

    def test_ios_route_includes_mobile_before_ios_and_no_orchestrator(self):
        router = load_module("route_capabilities", ROUTER)
        request = {
            "languages": ["german"],
            "locales": ["de-DE"],
            "surfaces": ["ios"],
            "domains": [],
            "scripts": ["latin"],
        }

        result = router.route(request, load_catalog())

        self.assertEqual(
            result,
            [
                "translating-core",
                "translating-mobile",
                "translating-ios",
                "translating-german",
                "reviewing-translations",
            ],
        )
        self.assertNotIn("translating-products", result)

    def test_cli_returns_two_for_malformed_request_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            request_path = Path(tmp) / "request.json"
            request_path.write_text("not json", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(ROUTER), str(request_path)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertRegex(result.stderr, r"^invalid request: .+\n$")

    def test_cli_returns_two_when_classified_fields_are_not_string_lists(self):
        fields = ("languages", "locales", "surfaces", "domains", "scripts")
        invalid_values = ("not-a-list", [123])

        for field in fields:
            for invalid_value in invalid_values:
                with self.subTest(field=field, invalid_value=invalid_value):
                    with tempfile.TemporaryDirectory() as tmp:
                        request_path = Path(tmp) / "request.json"
                        request_path.write_text(
                            json.dumps({field: invalid_value}), encoding="utf-8"
                        )

                        result = subprocess.run(
                            [sys.executable, str(ROUTER), str(request_path)],
                            capture_output=True,
                            text=True,
                            check=False,
                        )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(
                        result.stderr,
                        f"invalid request: {field} must be a list of strings\n",
                    )


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_module("translation_policy", POLICY)

    def test_bootstrap_legacy_filename_callers_fail_safe(self):
        self.assertEqual(
            self.policy.bootstrap_action(
                {
                    "project-brief.md",
                    "locales.yaml",
                    "glossary.csv",
                    "style-guide.md",
                    "protected-terms.txt",
                }
            ),
            "setup-one-question-at-a-time",
        )
        self.assertEqual(
            self.policy.bootstrap_action({"project-brief.md", "locales.yaml"}),
            "setup-one-question-at-a-time",
        )

    def test_research_requires_one_concrete_unresolved_question(self):
        self.assertFalse(self.policy.should_research("   ", False))
        self.assertFalse(
            self.policy.should_research(
                "What is the current regulatory term for this market?", True
            )
        )
        self.assertTrue(
            self.policy.should_research(
                "What is the current regulatory term for this market?", False
            )
        )

    def test_subagents_require_host_support_and_an_explicit_benefit(self):
        base = {
            "host_supports_subagents": True,
            "target_locales": 1,
            "source_units": 19,
            "separable_sections": 1,
            "terminology_pass": False,
            "independent_review": False,
        }
        self.assertFalse(self.policy.should_use_subagents(**base))
        self.assertTrue(
            self.policy.should_use_subagents(
                **{**base, "target_locales": 2, "source_units": 20}
            )
        )
        self.assertFalse(
            self.policy.should_use_subagents(
                **{
                    **base,
                    "host_supports_subagents": False,
                    "terminology_pass": True,
                }
            )
        )

    def test_missing_specialist_prefers_bundled_then_core(self):
        self.assertEqual(
            self.policy.missing_specialist_action(
                core_can_cover=True, bundled_available=True
            ),
            "use-bundled-specialist",
        )
        self.assertEqual(
            self.policy.missing_specialist_action(
                core_can_cover=True, bundled_available=False
            ),
            "use-core",
        )
        self.assertEqual(
            self.policy.missing_specialist_action(
                core_can_cover=False, bundled_available=False
            ),
            "report-missing-capability",
        )

    def test_research_evaluations_match_capability_gate(self):
        for case in load_cases("research-cases.json"):
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    self.policy.should_research(
                        case["question"],
                        case["bundled_knowledge_sufficient"],
                    ),
                    case["expected_research"],
                )

    def test_orchestration_evaluations_match_policy_functions(self):
        functions = {
            "subagents": self.policy.should_use_subagents,
            "missing-specialist": self.policy.missing_specialist_action,
        }
        for case in load_cases("orchestration-cases.json"):
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    functions[case["policy"]](**case["inputs"]),
                    case["expected"],
                )


if __name__ == "__main__":
    unittest.main()
