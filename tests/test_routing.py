import importlib.util
import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
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


def routing_request(targets, *, platforms=(), domains=()):
    return {
        "source_locale": "en-GB",
        "targets": targets,
        "surfaces": ["web"],
        "platforms": list(platforms),
        "formats": ["html"],
        "domains": list(domains),
        "capabilities": [],
        "audience": "Adults",
        "purpose": "Product onboarding",
        "register": "neutral product",
    }


def synthetic_skill(
    name,
    *,
    capabilities=(),
    selectors=(),
    depends_on=(),
    phases=("refine",),
    specificity="language",
    required_context=(),
    conflicts=(),
    supersedes=(),
    ownership=None,
    category="language",
):
    skill = {
        "name": name,
        "version": "1.0.0",
        "category": category,
        "description": "Use when testing generic routing behavior.",
        "capabilities": list(capabilities),
        "depends_on": list(depends_on),
        "selectors": list(selectors),
        "phases": list(phases),
        "specificity": specificity,
        "required_context": list(required_context),
        "conflicts": list(conflicts),
        "supersedes": list(supersedes),
    }
    if ownership is not None:
        skill["ownership"] = {
            capability: list(phases) for capability in ownership
        }
    return skill


def synthetic_catalog(*skills):
    return {"schema_version": 2, "skills": list(skills)}


def errors_across_hash_seeds(profile, catalog):
    script = textwrap.dedent(
        """
        import importlib.util
        import json
        import sys

        spec = importlib.util.spec_from_file_location("router", sys.argv[1])
        router = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(router)
        payload = json.loads(sys.stdin.read())
        try:
            router.route_profile(payload["profile"], payload["catalog"])
        except ValueError as error:
            print(error)
        """
    )
    payload = json.dumps({"profile": profile, "catalog": catalog})
    errors = []
    for seed in ("1", "2", "3", "4", "5"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(ROUTER)],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        errors.append(result.stdout.strip())
    return errors


class RoutingTests(unittest.TestCase):
    def test_multi_locale_request_isolates_each_linguistic_branch(self):
        router = load_module("route_capabilities_multi", ROUTER)
        request = {
            "source_locale": "en-GB",
            "targets": [
                {"locale": "pt-PT", "register": "familiar"},
                {"locale": "ja-JP", "register": "polite"},
                {
                    "locale": "ar-SA",
                    "register": "modern product",
                    "scripts": ["rtl"],
                },
            ],
            "surfaces": ["web"],
            "platforms": [],
            "formats": ["html"],
            "domains": [],
            "capabilities": [],
            "audience": "Adults",
            "purpose": "Product onboarding",
            "register": "neutral product",
        }

        result = router.route(request, load_catalog())

        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(
            [route["target_locale"] for route in result["routes"]],
            ["pt-PT", "ja-JP", "ar-SA"],
        )
        selected = {
            route["target_locale"]: route["selected"] for route in result["routes"]
        }
        self.assertIn("translating-portuguese", selected["pt-PT"])
        self.assertNotIn("translating-japanese", selected["pt-PT"])
        self.assertIn("translating-japanese", selected["ja-JP"])
        self.assertNotIn("translating-arabic", selected["ja-JP"])
        self.assertIn("translating-arabic", selected["ar-SA"])
        self.assertIn("translating-rtl", selected["ar-SA"])

    def test_locale_variant_changes_profile_not_skill_family(self):
        router = load_module("route_capabilities_variant", ROUTER)
        pt_pt = router.route(
            routing_request([{"locale": "pt-PT", "register": "familiar"}]),
            load_catalog(),
        )["routes"][0]
        pt_br = router.route(
            routing_request([{"locale": "pt-BR", "register": "friendly"}]),
            load_catalog(),
        )["routes"][0]

        self.assertEqual(pt_pt["selected"], pt_br["selected"])
        self.assertEqual(pt_pt["target_locale"], "pt-PT")
        self.assertEqual(pt_br["target_locale"], "pt-BR")

    def test_adding_ios_adds_only_declared_platform_and_dependency(self):
        router = load_module("route_capabilities_ios_delta", ROUTER)
        request = routing_request([{"locale": "pt-PT", "register": "familiar"}])
        base = router.route(request, load_catalog())["routes"][0]
        ios = router.route(
            {**request, "platforms": ["ios"]}, load_catalog()
        )["routes"][0]

        self.assertEqual(
            set(ios["selected"]) - set(base["selected"]),
            {"translating-mobile", "translating-ios"},
        )

    def test_unmatched_domain_does_not_change_a_route(self):
        router = load_module("route_capabilities_domain_delta", ROUTER)
        targets = [{"locale": "pt-PT", "register": "familiar"}]
        base = router.route(routing_request(targets), load_catalog())
        changed = router.route(
            routing_request(targets, domains=["unmatched-demo-domain"]),
            load_catalog(),
        )

        self.assertEqual(base, changed)

    def test_duplicate_normalized_target_locales_are_rejected(self):
        router = load_module("route_capabilities_duplicates", ROUTER)

        with self.assertRaisesRegex(ValueError, "duplicate target locale: pt-PT"):
            router.route(
                routing_request(
                    [
                        {"locale": "pt_pt", "register": "familiar"},
                        {"locale": "pt-PT", "register": "familiar"},
                    ]
                ),
                load_catalog(),
            )

    def test_normalize_request_normalizes_each_target_locale(self):
        router = load_module("route_capabilities_normalization", ROUTER)

        profiles = router.normalize_request(
            routing_request([{"locale": "pt_pt", "register": "familiar"}])
        )

        self.assertEqual(profiles[0]["target_locale"], "pt-PT")
        self.assertEqual(profiles[0]["language"], "pt")

    def test_target_routing_errors_identify_normalized_target_locale(self):
        router = load_module("route_capabilities_target_error", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "first-conflict",
                selectors=({"locales": ["ja"]},),
                conflicts=("second-conflict",),
            ),
            synthetic_skill(
                "second-conflict",
                selectors=({"locales": ["ja"]},),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "target locale ja-JP: conflicting skills: first-conflict, second-conflict",
        ) as context:
            router.route(
                routing_request(
                    [
                        {"locale": "pt-PT", "register": "familiar"},
                        {"locale": "ja_jp", "register": "polite"},
                    ]
                ),
                catalog,
            )

        self.assertIsInstance(context.exception.__cause__, ValueError)
        self.assertEqual(
            str(context.exception.__cause__),
            "conflicting skills: first-conflict, second-conflict",
        )

    def test_target_routes_do_not_share_mutable_containers(self):
        router = load_module("route_capabilities_isolation", ROUTER)
        routes = router.route(
            routing_request(
                [
                    {"locale": "pt-PT", "register": "familiar"},
                    {"locale": "ja-JP", "register": "polite"},
                ]
            ),
            load_catalog(),
        )["routes"]

        self.assertIsNot(routes[0]["selected"], routes[1]["selected"])
        self.assertIsNot(routes[0]["reasons"], routes[1]["reasons"])
        self.assertIsNot(routes[0]["phases"], routes[1]["phases"])

    def test_all_routing_evaluations_match_exact_order(self):
        router = load_module("route_capabilities_evaluations", ROUTER)
        for case in load_cases("routing-cases.json"):
            with self.subTest(case=case["id"]):
                result = router.route(case["request"], load_catalog())
                self.assertEqual(
                    [
                        {
                            "target_locale": route["target_locale"],
                            "selected": route["selected"],
                        }
                        for route in result["routes"]
                    ],
                    case["expected_routes"],
                )

    def test_cli_routes_one_request_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    routing_request([{"locale": "pt-PT", "register": "familiar"}])
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(ROUTER), str(request_path)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            json.loads(completed.stdout)["routes"][0]["target_locale"], "pt-PT"
        )

    def test_cli_rejects_malformed_request_without_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text("{}", encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(ROUTER), str(request_path)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertRegex(completed.stderr, r"^invalid request: .+\n$")

    def test_dependencies_expand_transitively_and_order_before_dependents(self):
        router = load_module("route_capabilities_dependencies", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "consumer",
                selectors=({"domains": ["dependency-demo"]},),
                depends_on=("intermediate",),
            ),
            synthetic_skill("intermediate", depends_on=("foundation",)),
            synthetic_skill("foundation"),
        )

        result = router.route_profile(
            complete_profile(domains=["dependency-demo"]), catalog
        )

        self.assertEqual(
            result["selected"], ["foundation", "intermediate", "consumer"]
        )
        self.assertEqual(
            result["phases"]["refine"], ["foundation", "intermediate", "consumer"]
        )
        self.assertEqual(
            result["reasons"]["intermediate"], ["dependency-of:consumer"]
        )
        self.assertEqual(
            result["reasons"]["foundation"], ["dependency-of:intermediate"]
        )

    def test_selected_load_order_respects_cross_phase_dependency_edges(self):
        router = load_module("route_capabilities_cross_phase_order", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "translate-consumer",
                selectors=({"domains": ["cross-phase-demo"]},),
                depends_on=("review-foundation",),
                phases=("translate",),
            ),
            synthetic_skill("review-foundation", phases=("review",)),
        )

        result = router.route_profile(
            complete_profile(domains=["cross-phase-demo"]), catalog
        )

        self.assertEqual(
            result["selected"], ["review-foundation", "translate-consumer"]
        )
        self.assertEqual(result["phases"]["translate"], ["translate-consumer"])
        self.assertEqual(result["phases"]["review"], ["review-foundation"])

    def test_selected_load_order_uses_phase_then_catalog_tiebreakers(self):
        router = load_module("route_capabilities_load_tiebreakers", ROUTER)
        selector = ({"domains": ["load-tiebreak-demo"]},)
        catalog = synthetic_catalog(
            synthetic_skill("review-first-in-catalog", selectors=selector, phases=("review",)),
            synthetic_skill("translate-first", selectors=selector, phases=("translate",)),
            synthetic_skill("inspect-ready", selectors=selector, phases=("inspect",)),
            synthetic_skill("translate-second", selectors=selector, phases=("translate",)),
        )

        result = router.route_profile(
            complete_profile(domains=["load-tiebreak-demo"]), catalog
        )

        self.assertEqual(
            result["selected"],
            [
                "translate-first",
                "translate-second",
                "inspect-ready",
                "review-first-in-catalog",
            ],
        )

    def test_selected_load_order_respects_transitive_cross_phase_dependencies(self):
        router = load_module("route_capabilities_transitive_cross_phase", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "translate-consumer",
                selectors=({"domains": ["transitive-cross-phase-demo"]},),
                depends_on=("inspect-intermediate",),
                phases=("translate",),
            ),
            synthetic_skill(
                "inspect-intermediate",
                depends_on=("review-foundation",),
                phases=("inspect",),
            ),
            synthetic_skill("review-foundation", phases=("review",)),
        )

        result = router.route_profile(
            complete_profile(domains=["transitive-cross-phase-demo"]), catalog
        )

        self.assertEqual(
            result["selected"],
            ["review-foundation", "inspect-intermediate", "translate-consumer"],
        )

    def test_selected_load_order_preserves_phase_intent_without_cross_phase_edges(self):
        router = load_module("route_capabilities_phase_load_order", ROUTER)
        selector = ({"domains": ["phase-load-demo"]},)
        catalog = synthetic_catalog(
            synthetic_skill("review-ready", selectors=selector, phases=("review",)),
            synthetic_skill("integrate-ready", selectors=selector, phases=("integrate",)),
            synthetic_skill("refine-ready", selectors=selector, phases=("refine",)),
            synthetic_skill("inspect-ready", selectors=selector, phases=("inspect",)),
            synthetic_skill("translate-ready", selectors=selector, phases=("translate",)),
        )

        result = router.route_profile(
            complete_profile(domains=["phase-load-demo"]), catalog
        )

        self.assertEqual(
            result["selected"],
            [
                "translate-ready",
                "inspect-ready",
                "refine-ready",
                "integrate-ready",
                "review-ready",
            ],
        )

    def test_cycles_in_a_phase_fail_deterministically(self):
        router = load_module("route_capabilities_cycle", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "first",
                selectors=({"domains": ["cycle-demo"]},),
                depends_on=("second",),
            ),
            synthetic_skill("second", depends_on=("first",)),
        )

        with self.assertRaisesRegex(ValueError, "dependency cycle in phase: refine"):
            router.route_profile(complete_profile(domains=["cycle-demo"]), catalog)

    def test_supersession_prunes_dependencies_owned_only_by_the_removed_skill(self):
        router = load_module("route_capabilities_supersession_pruning", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "broader",
                capabilities=("grammar",),
                selectors=({"domains": ["supersession-demo"]},),
                depends_on=("broader-support",),
            ),
            synthetic_skill("broader-support"),
            synthetic_skill(
                "narrower",
                capabilities=("grammar",),
                ownership=("grammar",),
                selectors=({"domains": ["supersession-demo"]},),
                specificity="locale",
                supersedes=("broader",),
            ),
        )

        result = router.route_profile(
            complete_profile(domains=["supersession-demo"]), catalog
        )

        self.assertEqual(result["selected"], ["narrower"])

    def test_supersession_rejects_a_surviving_skill_without_its_dependency(self):
        router = load_module("route_capabilities_supersession_closure", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "consumer",
                selectors=({"domains": ["closure-demo"]},),
                depends_on=("broader",),
            ),
            synthetic_skill(
                "broader",
                capabilities=("grammar",),
                selectors=({"domains": ["closure-demo"]},),
            ),
            synthetic_skill(
                "narrower",
                capabilities=("grammar",),
                ownership=("grammar",),
                selectors=({"domains": ["closure-demo"]},),
                specificity="locale",
                supersedes=("broader",),
            ),
        )

        with self.assertRaisesRegex(
            ValueError, "missing selected dependency: consumer, broader"
        ):
            router.route_profile(complete_profile(domains=["closure-demo"]), catalog)

    def test_invalid_route_diagnostics_are_manifest_ordered_across_hash_seeds(self):
        dependency_catalog = synthetic_catalog(
            synthetic_skill(
                "first",
                selectors=({"domains": ["deterministic-demo"]},),
                depends_on=("missing-first",),
            ),
            synthetic_skill(
                "second",
                selectors=({"domains": ["deterministic-demo"]},),
                depends_on=("missing-second",),
            ),
        )
        context_catalog = synthetic_catalog(
            synthetic_skill(
                "first",
                selectors=({"domains": ["deterministic-demo"]},),
                required_context=("audience",),
            ),
            synthetic_skill(
                "second",
                selectors=({"domains": ["deterministic-demo"]},),
                required_context=("purpose",),
            ),
        )
        profile = complete_profile(domains=["deterministic-demo"])
        missing_context_profile = complete_profile(
            domains=["deterministic-demo"], audience="", purpose=""
        )

        self.assertEqual(
            errors_across_hash_seeds(profile, dependency_catalog),
            ["unknown dependency: missing-first"] * 5,
        )
        self.assertEqual(
            errors_across_hash_seeds(missing_context_profile, context_catalog),
            ["first requires context: audience"] * 5,
        )

    def test_profile_routes_from_catalog_without_a_skill_map(self):
        router = load_module("route_capabilities_generic", ROUTER)
        result = router.route_profile(complete_profile(), load_catalog())
        self.assertEqual(
            result["selected"],
            [
                "reviewing-translations",
                "translating-core",
                "translating-web",
                "translating-portuguese",
            ],
        )
        self.assertEqual(result["phases"]["inspect"], ["translating-web"])
        self.assertEqual(result["phases"]["translate"], ["translating-core"])
        self.assertEqual(result["phases"]["refine"], ["translating-portuguese"])
        self.assertEqual(result["phases"]["integrate"], ["translating-web"])
        self.assertEqual(result["phases"]["review"], ["reviewing-translations"])

    def test_partial_supersession_retains_broader_unowned_capabilities(self):
        router = load_module("route_capabilities_partial_supersession", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "broader-language",
                capabilities=("grammar", "terminology"),
                selectors=({"domains": ["ownership-demo"]},),
            ),
            synthetic_skill(
                "narrower-locale",
                capabilities=("grammar",),
                ownership=("grammar",),
                selectors=({"domains": ["ownership-demo"]},),
                specificity="locale",
                supersedes=("broader-language",),
            ),
        )

        result = router.route_profile(
            complete_profile(domains=["ownership-demo"]), catalog
        )

        self.assertEqual(
            result["phases"]["refine"],
            ["broader-language", "narrower-locale"],
        )
        self.assertEqual(
            result["ownership_overrides"],
            {
                "narrower-locale": [
                    {
                        "skill": "broader-language",
                        "ownership": {"grammar": ["refine"]},
                    }
                ]
            },
        )

    def test_full_supersession_prunes_replaced_skill_and_its_dependency(self):
        router = load_module("route_capabilities_full_supersession", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill("broader-dependency"),
            synthetic_skill(
                "broader-language",
                capabilities=("grammar",),
                selectors=({"domains": ["ownership-demo"]},),
                depends_on=("broader-dependency",),
            ),
            synthetic_skill(
                "narrower-locale",
                capabilities=("grammar",),
                ownership=("grammar",),
                selectors=({"domains": ["ownership-demo"]},),
                specificity="locale",
                supersedes=("broader-language",),
            ),
        )

        result = router.route_profile(
            complete_profile(domains=["ownership-demo"]), catalog
        )

        self.assertEqual(result["selected"], ["narrower-locale"])
        self.assertEqual(result["ownership_overrides"], {})

    def test_refine_only_replacement_preserves_broader_inspection(self):
        router = load_module("route_capabilities_phase_scoped_ownership", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "broader-surface",
                capabilities=("structure",),
                selectors=({"domains": ["ownership-demo"]},),
                phases=("inspect", "refine"),
            ),
            synthetic_skill(
                "narrower-locale",
                capabilities=("structure",),
                ownership=("structure",),
                selectors=({"domains": ["ownership-demo"]},),
                specificity="locale",
                supersedes=("broader-surface",),
            ),
        )

        result = router.route_profile(
            complete_profile(domains=["ownership-demo"]), catalog
        )

        self.assertEqual(result["phases"]["inspect"], ["broader-surface"])
        self.assertEqual(
            result["phases"]["refine"], ["broader-surface", "narrower-locale"]
        )

    def test_unrelated_ownership_cannot_declare_supersession(self):
        router = load_module("route_capabilities_unrelated_ownership", ROUTER)
        catalog = synthetic_catalog(
            synthetic_skill(
                "broader-language",
                capabilities=("terminology",),
                selectors=({"domains": ["ownership-demo"]},),
            ),
            synthetic_skill(
                "narrower-locale",
                capabilities=("grammar",),
                ownership=("grammar",),
                selectors=({"domains": ["ownership-demo"]},),
                specificity="locale",
                supersedes=("broader-language",),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "supersedes without shared ownership: narrower-locale, broader-language",
        ):
            router.route_profile(complete_profile(domains=["ownership-demo"]), catalog)

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
