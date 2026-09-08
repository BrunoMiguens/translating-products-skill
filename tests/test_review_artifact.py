from __future__ import annotations

import copy
import importlib.util
import json
import math
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "skills/reviewing-translations/scripts/validate_review_artifact.py"
SCHEMA = ROOT / "skills/reviewing-translations/references/review-artifact-schema.json"


def load_validator():
    spec = importlib.util.spec_from_file_location("review_artifact_validator", VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load review artifact validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = load_validator()


def request_fixture() -> dict:
    return {
        "schema_version": 1,
        "review_id": "review-001",
        "source_locale": "en",
        "targets": [{
            "target_locale": "fr-FR",
            "review_depth": "selective_challenge",
            "selected_capabilities": ["reviewing-translations"],
            "missing_capabilities": [],
            "units": [
                {
                    "unit_id": "message.title",
                    "source": "Hello {name}",
                    "current_target": "Bonjour {name}",
                    "protected_terms": [],
                    "automatic_checks": [{
                        "type": "placeholder_multiset",
                        "severity": "critical",
                    }],
                },
                {
                    "unit_id": "payment.action",
                    "source": "Pay {amount}",
                    "current_target": "Verser {amount}",
                    "protected_terms": [],
                    "automatic_checks": [{
                        "type": "placeholder_multiset",
                        "severity": "critical",
                    }],
                },
            ],
        }],
    }


def human_review(status: str = "not_requested") -> dict:
    return {
        "status": status,
        "decision": None,
        "correction": None,
        "notes": None,
        "severity": None,
        "reviewer": None,
    }


def review_pass(*, issues: list[dict] | None = None, confidence: str = "high") -> dict:
    return {
        "issues": [] if issues is None else issues,
        "confidence": confidence,
        "native_review_required": False,
        "native_review_reason": None,
    }


def review_issue(label: str, *, owner: str = "translation") -> dict:
    return {
        "dimension": "accuracy",
        "issue": label,
        "owner": owner,
        "affected_segment": label,
    }


def result_fixture() -> dict:
    return {
        "schema_version": 1,
        "review_id": "review-001",
        "source_locale": "en",
        "locales": [{
            "target_locale": "fr-FR",
            "review_depth": "selective_challenge",
            "execution_mode": "sequential",
            "selected_capabilities": ["reviewing-translations"],
            "missing_capabilities": [],
            "units": [
                {
                    "unit_id": "message.title",
                    "source": "Hello {name}",
                    "current_target": "Bonjour {name}",
                    "classification": "no_issue_detected",
                    "primary": review_pass(),
                    "challenge": review_pass(),
                    "adjudication": {"status": "not_required", "rationale": None},
                    "recommendation": None,
                    "recommendation_qa": None,
                    "source_issue": None,
                    "human_review": human_review(),
                },
                {
                    "unit_id": "payment.action",
                    "source": "Pay {amount}",
                    "current_target": "Verser {amount}",
                    "classification": "change_recommended",
                    "primary": review_pass(issues=[{
                        "dimension": "accuracy",
                        "issue": "Verb changes the payment action",
                        "owner": "translation",
                        "affected_segment": "Verser",
                    }]),
                    "challenge": None,
                    "adjudication": {"status": "not_required", "rationale": None},
                    "recommendation": {
                        "text": "Payer {amount}",
                        "owner": "translation",
                    },
                    "recommendation_qa": {"status": "passed", "issues": []},
                    "source_issue": None,
                    "human_review": human_review(),
                },
            ],
        }],
        "summary": {
            "locales": 1,
            "units": 2,
            "counts": {
                "no_issue_detected": 1,
                "change_recommended": 1,
                "blocked_by_source": 0,
                "unresolved": 0,
            },
        },
    }


class ReviewArtifactTests(unittest.TestCase):
    def validate(self, request: dict | None = None, result: dict | None = None):
        return validator.validate_review_artifact(
            request_fixture() if request is None else request,
            result_fixture() if result is None else result,
        )

    def assert_invalid(self, result: dict, fragment: str, request: dict | None = None):
        errors = self.validate(request, result)
        self.assertTrue(errors, "mutated artifact unexpectedly passed")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"{fragment!r} not found in {errors!r}",
        )

    def single_unit_pair(self) -> tuple[dict, dict, dict]:
        request = request_fixture()
        result = result_fixture()
        request["targets"][0]["units"] = request["targets"][0]["units"][:1]
        unit = result["locales"][0]["units"][0]
        result["locales"][0]["units"] = [unit]
        result["summary"] = {
            "locales": 1,
            "units": 1,
            "counts": {
                "no_issue_detected": 1,
                "change_recommended": 0,
                "blocked_by_source": 0,
                "unresolved": 0,
            },
        }
        return request, result, unit

    @staticmethod
    def set_classification(result: dict, unit: dict, classification: str) -> None:
        unit["classification"] = classification
        result["summary"]["counts"] = {
            name: int(name == classification)
            for name in validator.CLASSIFICATIONS
        }

    def test_two_unit_review_is_accepted(self):
        """Break: a complete review could be rejected or reported with non-derived totals."""
        self.assertEqual(self.validate(), ())

    def test_schema_declares_request_and_result_vocabularies(self):
        """Break: producers could lack a canonical machine-readable request/result contract."""
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["schema_version"], 1)
        self.assertEqual(
            schema["classifications"],
            ["no_issue_detected", "change_recommended", "blocked_by_source", "unresolved"],
        )
        self.assertEqual(schema["request"]["required"], [
            "schema_version", "review_id", "source_locale", "targets",
        ])
        self.assertEqual(schema["result"]["required"], [
            "schema_version", "review_id", "source_locale", "locales", "summary",
        ])
        self.assertIs(schema["$defs"]["string_list"].get("uniqueItems"), True)
        self.assertEqual(schema["$defs"]["review_pass"]["type"], "object")
        self.assertEqual(schema["$defs"]["result_unit"]["properties"]["challenge"], {
            "anyOf": [
                {"$ref": "#/$defs/review_pass"},
                {"type": "null"},
            ],
        })
        self.assertEqual(
            schema["$defs"]["request_unit"]["properties"].get("source_invariant"),
            {"type": "boolean", "default": False},
        )
        self.assertNotIn(
            "source_invariant",
            schema["$defs"]["request_unit"]["required"],
        )
        self.assertEqual(
            schema["$defs"]["result_locale"]["properties"].get("research"),
            {
                "anyOf": [
                    {"$ref": "#/$defs/research"},
                    {"type": "null"},
                ]
            },
        )
        self.assertNotIn("research", schema["$defs"]["result_locale"]["required"])

    def test_schema_nonblank_strings_reject_whitespace_only_values(self):
        """Break: schema consumers could accept whitespace rejected by the runtime gate."""
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        nonblank_fragments = []

        def collect(value):
            if isinstance(value, dict):
                if value.get("minLength") == 1:
                    nonblank_fragments.append(value)
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(schema)
        self.assertGreater(len(nonblank_fragments), 0)
        for fragment in nonblank_fragments:
            with self.subTest(fragment=fragment):
                pattern = fragment.get("pattern")
                self.assertIsInstance(pattern, str)
                self.assertIsNone(re.search(pattern, " \t\n"))
                self.assertIsNotNone(re.search(pattern, "x"))

    def test_schema_nullable_conditional_strings_are_nonblank_when_present(self):
        """Break: activated nullable evidence fields could accept whitespace-only strings."""
        definitions = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]
        branches = {
            "native_review_reason": definitions["review_pass"]["properties"][
                "native_review_reason"
            ],
            "adjudication.rationale": definitions["adjudication"]["properties"][
                "rationale"
            ],
            "human_review.reviewer": definitions["human_review"]["properties"][
                "reviewer"
            ],
            "human_review.correction": definitions["human_review"]["properties"][
                "correction"
            ],
        }
        for name, branch in branches.items():
            with self.subTest(branch=name):
                self.assertIn("null", branch["type"])
                self.assertEqual(branch.get("minLength"), 1)
                pattern = branch.get("pattern")
                self.assertIsInstance(pattern, str)
                self.assertIsNone(re.search(pattern, " \t\n"))
                self.assertIsNotNone(re.search(pattern, "human evidence"))

    def test_all_classifications_have_valid_explicit_shapes(self):
        """Break: a canonical status could become impossible to represent."""
        cases = {
            "no_issue_detected": {},
            "change_recommended": {
                "recommendation": {"text": "Salut {name}", "owner": "translation"},
                "recommendation_qa": {"status": "passed", "issues": []},
            },
            "blocked_by_source": {
                "source_issue": {"issue": "Source is contradictory", "blocks_decision": True},
            },
            "unresolved": {},
        }
        for classification, changes in cases.items():
            with self.subTest(classification=classification):
                request = request_fixture()
                result = result_fixture()
                request["targets"][0]["units"] = request["targets"][0]["units"][:1]
                unit = result["locales"][0]["units"][0]
                unit["classification"] = classification
                unit["challenge"] = review_pass()
                unit["recommendation"] = None
                unit["recommendation_qa"] = None
                unit["source_issue"] = None
                unit.update(changes)
                if classification == "change_recommended":
                    accepted = [review_issue("accepted target defect")]
                    unit["primary"] = review_pass(issues=accepted)
                    unit["challenge"] = review_pass(issues=accepted)
                result["locales"][0]["units"] = [unit]
                result["summary"] = {
                    "locales": 1,
                    "units": 1,
                    "counts": {
                        "no_issue_detected": int(classification == "no_issue_detected"),
                        "change_recommended": int(classification == "change_recommended"),
                        "blocked_by_source": int(classification == "blocked_by_source"),
                        "unresolved": int(classification == "unresolved"),
                    },
                }
                self.assertEqual(self.validate(request, result), ())

    def test_automated_approval_claims_are_rejected(self):
        """Break: automation could encode a human or native-quality approval claim."""
        for forbidden in ("good", "approved", "verified", "native-quality", "native_reviewed"):
            with self.subTest(classification=forbidden):
                result = result_fixture()
                result["locales"][0]["units"][0]["classification"] = forbidden
                self.assert_invalid(result, "classification")

    def test_missing_duplicate_and_unknown_units_are_rejected(self):
        """Break: incomplete, ambiguous, or out-of-scope unit coverage could pass."""
        mutations = []
        missing = result_fixture()
        missing["locales"][0]["units"].pop()
        mutations.append((missing, "missing unit"))
        duplicate = result_fixture()
        duplicate["locales"][0]["units"].append(
            copy.deepcopy(duplicate["locales"][0]["units"][0])
        )
        mutations.append((duplicate, "duplicate unit"))
        unknown = result_fixture()
        unknown["locales"][0]["units"][0]["unit_id"] = "unknown.unit"
        mutations.append((unknown, "unknown unit"))
        for result, fragment in mutations:
            with self.subTest(fragment=fragment):
                self.assert_invalid(result, fragment)

    def test_source_and_current_target_must_match_request_exactly(self):
        """Break: a result could silently review text other than the requested text."""
        for field in ("source", "current_target"):
            with self.subTest(field=field):
                result = result_fixture()
                result["locales"][0]["units"][0][field] += " changed"
                self.assert_invalid(result, f"{field} does not match")

    def test_change_recommendation_is_nonblank_changed_and_strict(self):
        """Break: a change status could carry no actionable changed text or extra claims."""
        for value, fragment in ((None, "requires recommendation"), ("", "nonblank"),
                                ("Verser {amount}", "must differ")):
            with self.subTest(value=value):
                result = result_fixture()
                if value is None:
                    result["locales"][0]["units"][1]["recommendation"] = None
                else:
                    result["locales"][0]["units"][1]["recommendation"]["text"] = value
                self.assert_invalid(result, fragment)
        result = result_fixture()
        result["locales"][0]["units"][1]["recommendation"]["approved"] = True
        self.assert_invalid(result, "unknown field")

    def test_source_equal_recommendation_requires_exact_source_invariant_opt_in(self):
        """Break: untranslated source text could be recommended without explicit invariance."""
        request = request_fixture()
        result = result_fixture()
        result["locales"][0]["units"][1]["recommendation"]["text"] = "Pay {amount}"
        self.assert_invalid(result, "source_invariant", request)

        request["targets"][0]["units"][1]["source_invariant"] = True
        self.assertEqual(self.validate(request, result), ())

        request["targets"][0]["units"][1]["source_invariant"] = 1
        self.assertTrue(
            any("source_invariant must be boolean" in error for error in self.validate(request, result))
        )

    def test_change_recommendation_requires_passed_empty_qa(self):
        """Break: unverified or failed recommendation QA could be reported as a change."""
        variants = (
            (None, "recommendation_qa"),
            ({"status": "failed", "issues": []}, "status"),
            ({"status": "passed", "issues": ["placeholder changed"]}, "issues must be empty"),
        )
        for qa, fragment in variants:
            with self.subTest(qa=qa):
                result = result_fixture()
                result["locales"][0]["units"][1]["recommendation_qa"] = qa
                self.assert_invalid(result, fragment)

    def test_unresolved_recommendation_attempt_requires_unresolved_qa(self):
        """Break: an unresolved retained QA record could falsely claim it passed."""
        result = result_fixture()
        unit = result["locales"][0]["units"][1]
        unit["classification"] = "unresolved"
        unit["recommendation"] = None
        unit["recommendation_qa"] = {"status": "passed", "issues": []}
        result["summary"]["counts"] = {
            "no_issue_detected": 1,
            "change_recommended": 0,
            "blocked_by_source": 0,
            "unresolved": 1,
        }
        self.assert_invalid(result, "recommendation_qa.status must be unresolved")

    def test_recommendation_runs_declared_invariant_engine(self):
        """Break: a recommendation that drops a source placeholder could pass QA."""
        result = result_fixture()
        result["locales"][0]["units"][1]["recommendation"]["text"] = "Payer maintenant"
        self.assert_invalid(result, "placeholder_multiset")

    def test_no_issue_detected_enforces_full_protected_names(self):
        """Break: clean QA could accept a partial brand even with approved terms."""
        for target in ("Ouvrez l’application Cedar", "Ouvrez Cedar app", ""):
            for declared_values in (None, ["Cedar"]):
                with self.subTest(target=target, declared_values=declared_values):
                    request = request_fixture()
                    result = result_fixture()
                    request_unit = request["targets"][0]["units"][0]
                    result_unit = result["locales"][0]["units"][0]
                    for unit in (request_unit, result_unit):
                        unit["source"] = "Open Cedar App"
                        unit["current_target"] = target
                    request_unit["protected_terms"] = ["Cedar", "Cedar App"]
                    request_unit["automatic_checks"] = []
                    if declared_values is not None:
                        request_unit["automatic_checks"] = [{
                            "type": "protected_term_multiset",
                            "severity": "critical",
                            "values": declared_values,
                        }]
                    errors = self.validate(request, result)
                    protected_errors = [
                        error for error in errors
                        if "current_target failed protected_term_multiset" in error
                    ]
                    self.assertEqual(len(protected_errors), 1, errors)

    def test_no_issue_detected_checks_declared_structure_and_term_counts(self):
        """Break: reviewer agreement could bypass placeholders or repeated names."""
        cases = (
            ("Hello {name}", "Bonjour {person}", [],
             [{"type": "placeholder_multiset", "severity": "critical"}],
             "placeholder_multiset"),
            ("Open Cedar App, then return to Cedar App.", "Ouvrez Cedar App.",
             ["Cedar App"], [], "protected_term_multiset"),
        )
        for source, target, terms, checks, expected_check in cases:
            with self.subTest(check=expected_check):
                request = request_fixture()
                result = result_fixture()
                request_unit = request["targets"][0]["units"][0]
                for unit in (request_unit, result["locales"][0]["units"][0]):
                    unit["source"] = source
                    unit["current_target"] = target
                request_unit["protected_terms"] = terms
                request_unit["automatic_checks"] = checks
                self.assert_invalid(result, f"current_target failed {expected_check}", request)

    def test_no_issue_detected_accepts_preserved_terms_and_generic_app_word(self):
        """Break: protection must preserve names without freezing surrounding prose."""
        for source, target, terms in (
            ("Open Cedar App", "Ouvrez Cedar App", ["Cedar", "Cedar App"]),
            ("Open Cedar in your app", "Ouvrez Cedar dans votre application", ["Cedar"]),
        ):
            with self.subTest(source=source):
                request = request_fixture()
                result = result_fixture()
                request_unit = request["targets"][0]["units"][0]
                for unit in (request_unit, result["locales"][0]["units"][0]):
                    unit["source"] = source
                    unit["current_target"] = target
                request_unit["protected_terms"] = terms
                request_unit["automatic_checks"] = []
                self.assertEqual(self.validate(request, result), ())

    def test_unresolved_review_can_report_target_with_broken_protected_term(self):
        """Break: an incomplete review must remain recordable without pretending it passed."""
        request = request_fixture()
        result = result_fixture()
        request_unit = request["targets"][0]["units"][0]
        result_unit = result["locales"][0]["units"][0]
        for unit in (request_unit, result_unit):
            unit["source"] = "Open Cedar App"
            unit["current_target"] = "Ouvrez Cedar"
        request_unit["protected_terms"] = ["Cedar App"]
        request_unit["automatic_checks"] = []
        result_unit["classification"] = "unresolved"
        result["summary"]["counts"]["no_issue_detected"] = 0
        result["summary"]["counts"]["unresolved"] = 1
        self.assertEqual(self.validate(request, result), ())

    def test_recommendation_checks_embedded_approved_protected_terms(self):
        """Break: approved request terms could be ignored when validating a recommendation."""
        request = request_fixture()
        request["targets"][0]["units"][1]["source"] = "Pay API {amount}"
        request["targets"][0]["units"][1]["current_target"] = "Verser API {amount}"
        request["targets"][0]["units"][1]["protected_terms"] = ["API"]
        request["targets"][0]["units"][1]["automatic_checks"].append({
            "type": "protected_term_multiset",
            "severity": "critical",
        })
        result = result_fixture()
        unit = result["locales"][0]["units"][1]
        unit["source"] = "Pay API {amount}"
        unit["current_target"] = "Verser API {amount}"
        unit["recommendation"]["text"] = "Payer Api {amount}"
        self.assert_invalid(result, "protected_term_multiset", request)

    def test_nonempty_protected_terms_are_automatic_without_duplicate_findings(self):
        """Break: request-owned protected terms could depend on a producer-declared check."""
        for declared in ("omitted", "request-backed", "configured"):
            with self.subTest(declared=declared):
                request = request_fixture()
                request_unit = request["targets"][0]["units"][1]
                request_unit["source"] = "Pay API {amount}"
                request_unit["current_target"] = "Verser API {amount}"
                request_unit["protected_terms"] = ["API"]
                if declared != "omitted":
                    declaration = {
                        "type": "protected_term_multiset",
                        "severity": "major" if declared == "configured" else "critical",
                    }
                    if declared == "configured":
                        declaration["values"] = ["API"]
                    request_unit["automatic_checks"].append(declaration)
                result = result_fixture()
                unit = result["locales"][0]["units"][1]
                unit["source"] = request_unit["source"]
                unit["current_target"] = request_unit["current_target"]
                unit["recommendation"]["text"] = "Payer Api {amount}"

                errors = self.validate(request, result)
                protected_errors = [
                    error for error in errors if "failed protected_term_multiset" in error
                ]
                self.assertEqual(len(protected_errors), 1, errors)

    def test_broken_or_unknown_automatic_checks_are_rejected(self):
        """Break: malformed declarations could skip the shared invariant engine."""
        request = request_fixture()
        request["targets"][0]["units"][1]["automatic_checks"][0]["type"] = "placeholders"
        self.assertTrue(any("unknown automatic check" in error for error in self.validate(request)))
        request = request_fixture()
        request["targets"][0]["units"][1]["automatic_checks"] = "placeholder_multiset"
        self.assertTrue(any("automatic_checks" in error for error in self.validate(request)))
        request = request_fixture()
        request_unit = request["targets"][0]["units"][1]
        request_unit["protected_terms"] = ["API"]
        request_unit["automatic_checks"].append({
            "type": "protected_term_multiset",
            "severity": "critical",
            "values": 1,
        })
        self.assertTrue(
            any("values must be a list" in error for error in self.validate(request))
        )

    def test_challenge_shape_rejects_leakage_fields(self):
        """Break: a blind challenge could contain primary output or recommendation leakage."""
        result = result_fixture()
        result["locales"][0]["units"][0]["challenge"]["primary_issues"] = []
        self.assert_invalid(result, "unknown field")

    def test_review_depth_enforces_challenge_coverage(self):
        """Break: selective/full challenge could omit required blind reviews."""
        result = result_fixture()
        result["locales"][0]["units"][0]["challenge"] = None
        self.assert_invalid(result, "challenge is required")

        request = request_fixture()
        result = result_fixture()
        request["targets"][0]["review_depth"] = "full_challenge"
        result["locales"][0]["review_depth"] = "full_challenge"
        self.assert_invalid(result, "challenge is required", request)

        request = request_fixture()
        result = result_fixture()
        request["targets"][0]["review_depth"] = "single"
        result["locales"][0]["review_depth"] = "single"
        self.assert_invalid(result, "challenge must be null", request)

    def test_selective_challenge_covers_a_primary_pass_even_if_final_status_changes(self):
        """Break: final classification could be used to evade challenge of a primary pass."""
        result = result_fixture()
        unit = result["locales"][0]["units"][0]
        unit["classification"] = "change_recommended"
        unit["challenge"] = None
        unit["recommendation"] = {"text": "Salut {name}", "owner": "translation"}
        unit["recommendation_qa"] = {"status": "passed", "issues": []}
        result["summary"]["counts"] = {
            "no_issue_detected": 0,
            "change_recommended": 2,
            "blocked_by_source": 0,
            "unresolved": 0,
        }
        self.assert_invalid(result, "challenge is required")

    def test_disagreement_requires_adjudication_and_rationale(self):
        """Break: conflicting primary/challenge findings could remain silently unresolved."""
        issue = {
            "dimension": "terminology",
            "issue": "Unexpected term",
            "owner": "translation",
            "affected_segment": "Bonjour",
        }
        for adjudication, fragment in (
            ({"status": "not_required", "rationale": None}, "requires adjudication"),
            ({"status": "merged", "rationale": ""}, "nonblank rationale"),
        ):
            with self.subTest(adjudication=adjudication):
                result = result_fixture()
                result["locales"][0]["units"][0]["challenge"]["issues"] = [issue]
                result["locales"][0]["units"][0]["adjudication"] = adjudication
                self.assert_invalid(result, fragment)

    def test_challenge_only_defect_accepted_primary_derives_an_empty_issue_set(self):
        """Break: a rejected challenge-only defect could still drive the final classification."""
        request, result, unit = self.single_unit_pair()
        unit["challenge"]["issues"] = [review_issue("challenge-only")]
        unit["adjudication"] = {
            "status": "accepted_primary",
            "rationale": "Primary evidence is controlling",
        }
        self.assertEqual(self.validate(request, result), ())

        self.set_classification(result, unit, "change_recommended")
        unit["recommendation"] = {"text": "Salut {name}", "owner": "translation"}
        unit["recommendation_qa"] = {"status": "passed", "issues": []}
        self.assert_invalid(result, "accepted issue set is empty", request)

    def test_primary_false_positive_accepted_challenge_derives_an_empty_issue_set(self):
        """Break: primary issues could survive after adjudication accepts an empty challenge."""
        request, result, unit = self.single_unit_pair()
        unit["primary"]["issues"] = [review_issue("primary false positive")]
        unit["adjudication"] = {
            "status": "accepted_challenge",
            "rationale": "Challenge evidence overturns the primary finding",
        }
        self.assertEqual(self.validate(request, result), ())

    def test_merged_and_agreed_issue_sets_drive_change_evidence(self):
        """Break: merged issue direction or agreement could be ignored by final evidence checks."""
        first = review_issue("first", owner="language")
        second = review_issue("second", owner="platform")
        cases = (
            ([first], [second], "language"),
            ([second], [first], "platform"),
            ([first], [first], "language"),
        )
        for primary_issues, challenge_issues, owner in cases:
            with self.subTest(primary=primary_issues, challenge=challenge_issues):
                request, result, unit = self.single_unit_pair()
                unit["primary"]["issues"] = primary_issues
                unit["challenge"]["issues"] = challenge_issues
                if primary_issues == challenge_issues:
                    unit["adjudication"] = {"status": "not_required", "rationale": None}
                else:
                    unit["adjudication"] = {
                        "status": "merged",
                        "rationale": "Both independent issues are retained",
                    }
                self.set_classification(result, unit, "change_recommended")
                unit["recommendation"] = {"text": "Salut {name}", "owner": owner}
                unit["recommendation_qa"] = {"status": "passed", "issues": []}
                self.assertEqual(self.validate(request, result), ())

                unit["recommendation"]["owner"] = "unrelated-owner"
                self.assert_invalid(result, "recommendation.owner must match", request)

    def test_unresolved_adjudication_requires_unresolved_final_classification(self):
        """Break: an unresolved disagreement could be serialized as an accepted completion."""
        request, result, unit = self.single_unit_pair()
        unit["primary"]["issues"] = [review_issue("primary")]
        unit["challenge"]["issues"] = [review_issue("challenge")]
        unit["adjudication"] = {
            "status": "unresolved",
            "rationale": "Evidence remains incompatible",
        }
        self.set_classification(result, unit, "unresolved")
        self.assertEqual(self.validate(request, result), ())

        self.set_classification(result, unit, "change_recommended")
        unit["recommendation"] = {"text": "Salut {name}", "owner": "translation"}
        unit["recommendation_qa"] = {"status": "passed", "issues": []}
        self.assert_invalid(result, "must be unresolved", request)

    def test_source_issue_blocking_contract_covers_every_classification(self):
        """Break: source evidence could be rejected when nonblocking or accepted when blocking."""
        nonblocking = {"issue": "Source has a recoverable defect", "blocks_decision": False}
        for classification in ("no_issue_detected", "change_recommended"):
            with self.subTest(classification=classification, blocking=False):
                request, result, unit = self.single_unit_pair()
                if classification == "change_recommended":
                    unit["primary"]["issues"] = [review_issue("target defect")]
                    unit["challenge"]["issues"] = [review_issue("target defect")]
                    self.set_classification(result, unit, classification)
                    unit["recommendation"] = {
                        "text": "Salut {name}",
                        "owner": "translation",
                    }
                    unit["recommendation_qa"] = {"status": "passed", "issues": []}
                unit["source_issue"] = copy.deepcopy(nonblocking)
                self.assertEqual(self.validate(request, result), ())

                unit["source_issue"]["blocks_decision"] = True
                self.assert_invalid(result, "must be false", request)

        request, result, unit = self.single_unit_pair()
        self.set_classification(result, unit, "blocked_by_source")
        unit["source_issue"] = {"issue": "Source blocks a decision", "blocks_decision": True}
        self.assertEqual(self.validate(request, result), ())
        unit["source_issue"]["blocks_decision"] = False
        self.assert_invalid(result, "must be true", request)

        for blocks_decision in (False, True):
            with self.subTest(classification="unresolved", blocks_decision=blocks_decision):
                request, result, unit = self.single_unit_pair()
                self.set_classification(result, unit, "unresolved")
                unit["source_issue"] = {
                    "issue": "Source issue remains recorded",
                    "blocks_decision": blocks_decision,
                }
                self.assert_invalid(result, "must be null for unresolved", request)

    def test_locale_research_provenance_is_optional_nullable_and_exact(self):
        """Break: research provenance could be malformed or required for old artifacts."""
        result = result_fixture()
        self.assertEqual(self.validate(result=result), ())

        result["locales"][0]["research"] = None
        self.assertEqual(self.validate(result=result), ())
        result["locales"][0]["research"] = {
            "question": "Which current market term resolves this unit?",
            "source": "https://example.test/authoritative-source",
        }
        self.assertEqual(self.validate(result=result), ())

        invalid_values = (
            ({"question": " ", "source": "https://example.test"}, "question"),
            ({"question": "Current term?", "source": 7}, "source"),
            ({"question": "Current term?", "source": "source", "decision": "x"}, "unknown field"),
        )
        for value, fragment in invalid_values:
            with self.subTest(value=value):
                result = result_fixture()
                result["locales"][0]["research"] = value
                self.assert_invalid(result, fragment)

    def test_summary_is_fully_derived(self):
        """Break: caller-supplied totals could disagree with reviewed coverage."""
        mutations = (("locales", 2), ("units", 3))
        for field, value in mutations:
            with self.subTest(field=field):
                result = result_fixture()
                result["summary"][field] = value
                self.assert_invalid(result, f"summary.{field}")
        result = result_fixture()
        result["summary"]["counts"]["change_recommended"] = 2
        self.assert_invalid(result, "summary.counts.change_recommended")

    def test_human_completion_requires_explicit_provenance(self):
        """Break: automation could infer completed human review without a reviewer decision."""
        variants = (
            ({"status": "completed", "decision": None, "correction": None,
              "notes": None, "severity": None, "reviewer": None}, "decision"),
            ({"status": "completed", "decision": "change_required", "correction": None,
              "notes": None, "severity": "major", "reviewer": "reviewer-17"}, "correction"),
            ({"status": "pending", "decision": "no_issue_detected", "correction": None,
              "notes": None, "severity": None, "reviewer": "reviewer-17"}, "must be null"),
        )
        for human, fragment in variants:
            with self.subTest(human=human):
                result = result_fixture()
                result["locales"][0]["units"][0]["human_review"] = human
                self.assert_invalid(result, fragment)

    def test_unknown_fields_wrong_exact_types_and_nonfinite_values_are_rejected(self):
        """Break: permissive coercion could admit ambiguous or non-JSON artifact data."""
        result = result_fixture()
        result["automatically_verified"] = True
        self.assert_invalid(result, "unknown field")
        request = request_fixture()
        request["schema_version"] = True
        self.assertTrue(any("schema_version" in error for error in self.validate(request)))
        result = result_fixture()
        result["locales"][0]["units"][0]["primary"]["confidence"] = math.nan
        self.assert_invalid(result, "finite JSON value")


class ReviewArtifactCliTests(unittest.TestCase):
    def run_cli(self, request_text: str, result_text: str, *extra: str):
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            result_path = Path(directory) / "result.json"
            request_path.write_text(request_text, encoding="utf-8")
            result_path.write_text(result_text, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(VALIDATOR), "--request", str(request_path),
                 "--result", str(result_path), *extra],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_cli_prints_compact_derived_success_json(self):
        """Break: a valid gate could return unstable output or a failing exit."""
        completed = self.run_cli(json.dumps(request_fixture()), json.dumps(result_fixture()))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            '{"valid":true,"locales":1,"units":2,"counts":{"no_issue_detected":1,'
            '"change_recommended":1,"blocked_by_source":0,"unresolved":0}}\n',
        )
        self.assertEqual(completed.stderr, "")

    def test_validator_accepts_execution_mode_metadata_with_only_mode_difference(self):
        """Break: validator behavior could differ for otherwise identical execution metadata."""
        request = request_fixture()
        artifacts = {}
        for execution_mode in ("parallel", "sequential"):
            result = result_fixture()
            result["locales"][0]["execution_mode"] = execution_mode
            completed = self.run_cli(json.dumps(request), json.dumps(result))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["valid"], True)
            artifacts[execution_mode] = result

        parallel_without_mode = copy.deepcopy(artifacts["parallel"])
        sequential_without_mode = copy.deepcopy(artifacts["sequential"])
        del parallel_without_mode["locales"][0]["execution_mode"]
        del sequential_without_mode["locales"][0]["execution_mode"]
        self.assertEqual(parallel_without_mode, sequential_without_mode)

    def test_cli_emits_counts_in_canonical_order_from_reordered_input(self):
        """Break: caller insertion order could make valid success output nondeterministic."""
        result = result_fixture()
        result["summary"]["counts"] = {
            "unresolved": 0,
            "blocked_by_source": 0,
            "change_recommended": 1,
            "no_issue_detected": 1,
        }
        completed = self.run_cli(json.dumps(request_fixture()), json.dumps(result))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            '{"valid":true,"locales":1,"units":2,"counts":{"no_issue_detected":1,'
            '"change_recommended":1,"blocked_by_source":0,"unresolved":0}}\n',
        )

    def test_cli_rejects_abbreviated_option_names(self):
        """Break: argparse abbreviation could broaden the exact CLI surface."""
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            result_path = Path(directory) / "result.json"
            request_path.write_text(json.dumps(request_fixture()), encoding="utf-8")
            result_path.write_text(json.dumps(result_fixture()), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(VALIDATOR), "--req", str(request_path),
                 "--res", str(result_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertNotEqual(completed.stderr, "")

    def test_cli_reports_ordered_validation_errors_with_exit_one(self):
        """Break: artifact failures could be conflated with invocation failures."""
        result = result_fixture()
        result["review_id"] = "wrong"
        result["source_locale"] = "de"
        completed = self.run_cli(json.dumps(request_fixture()), json.dumps(result))
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr.splitlines()[:2], [
            "result.review_id does not match request.review_id",
            "result.source_locale does not match request.source_locale",
        ])

    def test_cli_rejects_duplicate_keys_and_nonfinite_constants_with_exit_two(self):
        """Break: the JSON loader could erase duplicate keys or accept NaN before validation."""
        duplicate = '{"schema_version":1,"schema_version":1}'
        completed = self.run_cli(duplicate, json.dumps(result_fixture()))
        self.assertEqual(completed.returncode, 2)
        self.assertIn("duplicate JSON key", completed.stderr)
        nonfinite = json.dumps(result_fixture()).replace('"high"', "NaN", 1)
        completed = self.run_cli(json.dumps(request_fixture()), nonfinite)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("non-finite JSON constant", completed.stderr)

    def test_cli_rejects_missing_files_and_invalid_arguments_with_exit_two(self):
        """Break: invalid invocation could be misreported as review failure."""
        completed = subprocess.run(
            [sys.executable, str(VALIDATOR), "--request", "/missing/request.json",
             "--result", "/missing/result.json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertNotEqual(completed.stderr, "")

    def test_cli_validates_claimed_draft_terminology_without_changing_success_output(self):
        """Break: a claimed malformed draft record could pass the review-output gate."""
        with tempfile.TemporaryDirectory() as directory:
            draft_path = Path(directory) / "draft-terminology.csv"
            draft_path.write_text("source,target\nterm,value\n", encoding="utf-8")
            rejected = self.run_cli(
                json.dumps(request_fixture()),
                json.dumps(result_fixture()),
                "--draft-terminology",
                str(draft_path),
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(rejected.stdout, "")
            self.assertEqual(
                rejected.stderr,
                "draft terminology has an invalid header\n",
            )

            draft_path.write_text(
                "source_term,target_term,locale,domain,context,status,provenance,alternatives\n"
                "source,target,fr-FR,interface,short label,draft,review run,\n",
                encoding="utf-8",
            )
            accepted = self.run_cli(
                json.dumps(request_fixture()),
                json.dumps(result_fixture()),
                "--draft-terminology",
                str(draft_path),
            )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(
            accepted.stdout,
            '{"valid":true,"locales":1,"units":2,"counts":{"no_issue_detected":1,'
            '"change_recommended":1,"blocked_by_source":0,"unresolved":0}}\n',
        )
        self.assertEqual(accepted.stderr, "")


if __name__ == "__main__":
    unittest.main()
