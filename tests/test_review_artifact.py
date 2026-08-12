from __future__ import annotations

import copy
import importlib.util
import json
import math
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

    def test_broken_or_unknown_automatic_checks_are_rejected(self):
        """Break: malformed declarations could skip the shared invariant engine."""
        request = request_fixture()
        request["targets"][0]["units"][1]["automatic_checks"][0]["type"] = "placeholders"
        self.assertTrue(any("unknown automatic check" in error for error in self.validate(request)))
        request = request_fixture()
        request["targets"][0]["units"][1]["automatic_checks"] = "placeholder_multiset"
        self.assertTrue(any("automatic_checks" in error for error in self.validate(request)))

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


if __name__ == "__main__":
    unittest.main()
