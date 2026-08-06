import importlib.util
import ast
import json
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


def complete_profile(**overrides):
    target_locale = overrides.pop("target_locale", "pt-PT")
    profile = {
        "source_locale": "en-GB",
        "target_locale": target_locale,
        "language": target_locale.split("-", 1)[0].casefold(),
        "scripts": [],
        "surfaces": ["web"],
        "platforms": [],
        "formats": ["html"],
        "domains": [],
        "capabilities": ["core-translation", "translation-qa"],
        "audience": "Adults in Portugal",
        "purpose": "Account settings",
        "register": "familiar second person",
    }
    profile.update(overrides)
    return profile


class RoutingTests(unittest.TestCase):
    def test_profile_routes_from_catalog_without_a_skill_map(self):
        router = load_module("route_capabilities_generic", ROUTER)
        result = router.route_profile(complete_profile(), load_catalog())
        self.assertEqual(
            result["selected"],
            [
                "translating-core",
                "translating-web",
                "translating-portuguese",
                "reviewing-translations",
            ],
        )
        self.assertEqual(result["phases"]["inspect"], ["translating-web"])
        self.assertEqual(result["phases"]["translate"], ["translating-core"])
        self.assertEqual(result["phases"]["refine"], ["translating-portuguese"])
        self.assertEqual(result["phases"]["integrate"], ["translating-web"])
        self.assertEqual(result["phases"]["review"], ["reviewing-translations"])

    def test_catalog_only_locale_specialist_supersedes_broader_language_skill(self):
        router = load_module("route_capabilities_external", ROUTER)
        catalog = load_catalog()
        catalog["skills"].append(
            {
                "name": "external-pt-pt-product-copy",
                "version": "1.0.0",
                "category": "language",
                "capabilities": ["locale:pt-PT"],
                "depends_on": ["translating-core", "reviewing-translations"],
                "selectors": [{"locales": ["pt-PT"], "domains": ["product-copy"]}],
                "phases": ["refine"],
                "specificity": "locale",
                "required_context": ["target_locale", "register"],
                "conflicts": [],
                "supersedes": ["translating-portuguese"],
            }
        )

        result = router.route_profile(
            complete_profile(target_locale="pt-PT", domains=["product-copy"]),
            catalog,
        )

        self.assertIn("external-pt-pt-product-copy", result["selected"])
        self.assertNotIn("translating-portuguese", result["selected"])
        self.assertEqual(
            result["phases"]["refine"], ["external-pt-pt-product-copy"]
        )

    def test_selector_conjoins_axes_and_matches_locale_ranges(self):
        router = load_module("route_capabilities_selectors", ROUTER)
        profile = complete_profile(domains=["product-copy"])
        self.assertTrue(
            router.selector_matches(
                profile,
                {"locales": ["pt"], "domains": ["product-copy"]},
            )
        )
        self.assertFalse(
            router.selector_matches(
                profile,
                {"locales": ["pt"], "domains": ["legal"]},
            )
        )

    def test_two_matching_conflicting_skills_fail_deterministically(self):
        router = load_module("route_capabilities_conflicts", ROUTER)
        catalog = load_catalog()
        common = {
            "version": "1.0.0",
            "category": "language",
            "capabilities": ["domain:demo"],
            "depends_on": [],
            "selectors": [{"domains": ["demo"]}],
            "phases": ["refine"],
            "specificity": "language",
            "required_context": [],
            "supersedes": [],
        }
        catalog["skills"].extend(
            [
                {**common, "name": "external-demo-a", "conflicts": ["external-demo-b"]},
                {**common, "name": "external-demo-b", "conflicts": []},
            ]
        )

        with self.assertRaisesRegex(
            ValueError, "conflicting skills: external-demo-a, external-demo-b"
        ):
            router.route_profile(complete_profile(domains=["demo"]), catalog)

    def test_router_source_contains_no_bundled_skill_name_literals(self):
        tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        bundled_names = {item["name"] for item in load_catalog()["skills"]}
        self.assertEqual(literals & bundled_names, set())


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
