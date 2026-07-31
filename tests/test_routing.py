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


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


class RoutingTests(unittest.TestCase):
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


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_module("translation_policy", POLICY)

    def test_bootstrap_requires_all_required_project_files(self):
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
            "translate",
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


if __name__ == "__main__":
    unittest.main()
