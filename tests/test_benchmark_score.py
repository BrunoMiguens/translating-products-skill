from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.benchmark.blind import (
    _snapshot_manifest,
    _validate_prepared_manifest,
    build_blind_bundle,
)
from scripts.benchmark.common import (
    BenchmarkError,
    append_jsonl_fsync,
    atomic_write_json,
    canonical_bytes,
    sha256_bytes,
)
from scripts.benchmark.prepare import build_dataset_manifest, build_run_manifest
from scripts.benchmark.review_app import ReviewStore
from scripts.benchmark.score import (
    Pair,
    ScorePaths,
    evaluate_gates,
    paired_bootstrap,
    quantile,
    score_evidence,
    score_paths,
    verify_locked_inputs,
)
from tests.benchmark_helpers import write_reviewer_signoff, write_synthetic_dataset
from tests.test_benchmark_blind import complete_synthetic_runs


def validation(
    run_id: str,
    case_id: str,
    condition: str,
    *,
    attempt: int = 1,
    applicable: int = 1,
    passed: int = 1,
    findings: list[dict] | None = None,
) -> dict:
    findings = [] if findings is None else findings
    failed = 1 if findings else 0
    return {
        "run_id": run_id,
        "case_id": case_id,
        "condition": condition,
        "attempt": attempt,
        "status": "failed" if findings else "passed",
        "findings": findings,
        "validator_errors": [],
        "applicable_checks": applicable,
        "passed_checks": passed if not findings else max(0, passed - 1),
        "failed_checks": failed,
        "validator_error_checks": 0,
    }


def item(
    item_id: str,
    case_id: str,
    task: str,
    *,
    labels: dict[str, str],
    comparisons: dict[str, str],
    mqm: list[dict] | None = None,
    major_or_worse: dict[str, bool] | None = None,
    repeat_of: str | None = None,
) -> dict:
    return {
        "item_id": item_id,
        "case_id": case_id,
        "attempt": 1,
        "task": task,
        "repeat_of": repeat_of,
        "labels": labels,
        "comparisons": comparisons,
        "mqm": [] if mqm is None else mqm,
        "major_or_worse": (
            {label: False for label in labels}
            if major_or_worse is None
            else major_or_worse
        ),
        "word_counts": {label: 2 for label in labels},
    }


def base_evidence() -> dict:
    return {
        "schema_version": 1,
        "bootstrap_seed": 20260804,
        "items": [
            item(
                "item-t1",
                "translation-1",
                "translation",
                labels={"A": "normal", "B": "suite", "C": "context_only"},
                comparisons={"A:B": "right_clear", "A:C": "tie", "B:C": "left_slight"},
                mqm=[
                    {"output": "A", "severity": "major"},
                    {"output": "B", "severity": "minor"},
                ],
                major_or_worse={"A": True, "B": False, "C": False},
            ),
            item(
                "item-t2",
                "translation-2",
                "translation",
                labels={"A": "suite", "B": "normal"},
                comparisons={"A:B": "tie"},
            ),
        ],
        "validations": [
            validation("t1-n", "translation-1", "normal"),
            validation("t1-s", "translation-1", "suite"),
            validation("t1-c", "translation-1", "context_only"),
            validation("t2-n", "translation-2", "normal"),
            validation("t2-s", "translation-2", "suite"),
        ],
        "seeded_errors": {},
        "review_mappings": None,
        "learned_metrics": None,
    }


def passing_metrics() -> dict:
    return {
        "translation": {
            "non_tied_win_rate": 0.60,
            "bootstrap_lower": 0.5001,
            "mqm_reduction": 0.25,
            "suite_critical": 0,
            "normal_critical": 0,
            "suite_structural_pass_rate": 0.99,
            "critical_invariant_regressions": 0,
        },
        "review": {
            "available": True,
            "required_error_recall": {"suite": 0.75, "normal": 0.60},
            "false_positive_correction_rate": {"suite": 0.15, "normal": 0.10},
            "correction_success_rate": {"suite": 0.70, "normal": 0.69},
            "introduced_error_rate": {"suite": 0.05, "normal": 0.05},
            "critical_misses": {"suite": 1, "normal": 2},
            "structural_pass_rate": {"suite": 1.0, "normal": 1.0},
            "critical_invariant_regressions": 0,
        },
    }


class ScoreTests(unittest.TestCase):
    def test_pairwise_mqm_and_secondary_word_scores_decode_each_mapping(self):
        """Break: anonymous position order could be mistaken for treatment order."""
        metrics = score_evidence(base_evidence())

        self.assertEqual(metrics["translation"]["suite_wins"], 1)
        self.assertEqual(metrics["translation"]["normal_wins"], 0)
        self.assertEqual(metrics["translation"]["ties"], 1)
        self.assertEqual(
            metrics["translation"]["mqm_points"],
            {"context_only": 0, "normal": 5, "suite": 1},
        )
        self.assertEqual(
            metrics["translation"]["mqm_points_per_1000_words"],
            {"context_only": 0.0, "normal": 1250.0, "suite": 250.0},
        )
        self.assertEqual(metrics["translation"]["case_attempts"], 2)
        self.assertFalse(metrics["review"]["available"])
        self.assertFalse(metrics["learned_metrics"]["available"])

    def test_three_way_comparisons_reject_a_strict_cycle(self):
        """Break: contradictory rankings could be guessed from comparison order."""
        evidence = base_evidence()
        evidence["items"][0]["comparisons"] = {
            "A:B": "left_clear",
            "A:C": "right_clear",
            "B:C": "left_clear",
        }

        with self.assertRaisesRegex(BenchmarkError, "cycle"):
            score_evidence(evidence)

    def test_primary_identities_and_exact_types_are_enforced(self):
        """Break: duplicates and bool-as-int values could alter denominators."""
        duplicate = base_evidence()
        duplicate["validations"].append(copy.deepcopy(duplicate["validations"][0]))
        with self.assertRaisesRegex(BenchmarkError, "duplicate validation"):
            score_evidence(duplicate)

        bool_attempt = base_evidence()
        bool_attempt["items"][0]["attempt"] = True
        with self.assertRaisesRegex(BenchmarkError, "attempt"):
            score_evidence(bool_attempt)

        extra = base_evidence()
        extra["items"][0]["unexpected"] = 1
        with self.assertRaisesRegex(BenchmarkError, "fields"):
            score_evidence(extra)

    def test_missing_validation_and_zero_denominators_fail_closed(self):
        """Break: incomplete or denominator-free evidence could receive passing rates."""
        missing = base_evidence()
        missing["validations"].pop()
        with self.assertRaisesRegex(BenchmarkError, "validation identities"):
            score_evidence(missing)

        zero = base_evidence()
        for record in zero["validations"]:
            record["applicable_checks"] = 0
            record["passed_checks"] = 0
        metrics = score_evidence(zero)
        self.assertEqual(metrics["translation"]["suite_structural_pass_rate"], 0.0)
        self.assertFalse(evaluate_gates(metrics)["translation"]["passed"])

    def test_review_mappings_calculate_condition_metrics_and_unresolved_count(self):
        """Break: baits, unknown mappings, or run identities could corrupt review rates."""
        evidence = base_evidence()
        evidence["items"] = [
            item(
                "item-r1",
                "review-1",
                "review",
                labels={"A": "normal", "B": "suite"},
                comparisons={"A:B": "right_slight"},
            )
        ]
        evidence["validations"] = [
            validation("r-normal", "review-1", "normal"),
            validation("r-suite", "review-1", "suite"),
        ]
        evidence["seeded_errors"] = {
            "review-1": [
                {"id": "required", "severity": "critical", "correction_required": True},
                {"id": "bait", "severity": "neutral", "correction_required": False},
            ]
        }
        evidence["review_mappings"] = [
            {
                "run_id": "r-normal", "seeded_error_id": "required",
                "reported": False, "corrected": False, "introduced_error": False,
                "adjudicator": "pt-PT-reviewer", "note": "missed",
            },
            {
                "run_id": "r-normal", "seeded_error_id": "bait",
                "reported": True, "corrected": True, "introduced_error": True,
                "adjudicator": "pt-PT-reviewer", "note": "overcorrected",
            },
            {
                "run_id": "r-suite", "seeded_error_id": "required",
                "reported": True, "corrected": True, "introduced_error": False,
                "adjudicator": "pt-PT-reviewer", "note": "fixed",
            },
            {
                "run_id": "r-suite", "seeded_error_id": "bait",
                "reported": False, "corrected": False, "introduced_error": False,
                "adjudicator": "pt-PT-reviewer", "note": "left intact",
            },
            {
                "run_id": "r-suite", "seeded_error_id": "ambiguous-free-text",
                "reported": True, "corrected": False, "introduced_error": False,
                "adjudicator": "pt-PT-reviewer", "note": "unresolved",
            },
        ]

        review = score_evidence(evidence)["review"]

        self.assertTrue(review["available"])
        self.assertEqual(review["required_error_recall"], {"normal": 0.0, "suite": 1.0})
        self.assertEqual(review["reported_error_precision"], {"normal": 0.0, "suite": 1.0})
        self.assertEqual(review["false_positive_correction_rate"], {"normal": 1.0, "suite": 0.0})
        self.assertEqual(review["critical_misses"], {"normal": 1, "suite": 0})
        self.assertEqual(review["unresolved"], 1)

    def test_repeat_consistency_normalizes_fresh_anonymous_orders(self):
        """Break: repeats with reordered labels could look inconsistent after unblinding."""
        evidence = base_evidence()
        original = evidence["items"][0]
        repeat = item(
            "item-repeat",
            "translation-1",
            "translation",
            labels={"A": "suite", "B": "context_only", "C": "normal"},
            comparisons={"A:B": "left_slight", "A:C": "left_clear", "B:C": "tie"},
            mqm=[
                {"output": "C", "severity": "major"},
                {"output": "A", "severity": "minor"},
            ],
            major_or_worse={"A": False, "B": False, "C": True},
            repeat_of=original["item_id"],
        )
        evidence["items"].append(repeat)

        consistency = score_evidence(evidence)["consistency"]

        self.assertEqual(consistency["repeats"], 1)
        self.assertEqual(consistency["exact_five_level_agreement"], 1.0)
        self.assertEqual(consistency["quadratic_weighted_agreement"], 1.0)
        self.assertEqual(consistency["major_or_worse_agreement"], 1.0)

    def test_learned_metrics_are_secondary_and_reject_non_finite_values(self):
        """Break: optional learned scores could alter gates or admit NaN/Infinity."""
        evidence = base_evidence()
        evidence["learned_metrics"] = [
            {"run_id": "t1-n", "metric": "comet", "value": 0.4},
            {"run_id": "t1-s", "metric": "comet", "value": 0.6},
        ]
        learned = score_evidence(evidence)["learned_metrics"]
        self.assertEqual(learned["means"]["comet"], {"normal": 0.4, "suite": 0.6})

        evidence["learned_metrics"][0]["value"] = math.nan
        with self.assertRaisesRegex(BenchmarkError, "finite"):
            score_evidence(evidence)

    def test_result_is_canonical_and_deterministic(self):
        """Break: scoring the same frozen evidence could produce different documents."""
        first = score_evidence(base_evidence())
        second = score_evidence(copy.deepcopy(base_evidence()))
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))


class BootstrapTests(unittest.TestCase):
    def test_seeded_bootstrap_and_interpolated_quantile_are_reproducible(self):
        """Break: resampling order or quantile boundaries could vary between runs."""
        pairs = [
            Pair("a", 1, 1.0, 0.0),
            Pair("b", 1, 0.0, 1.0),
            Pair("c", 1, 1.0, 0.0),
        ]

        def suite_rate(sample):
            return sum(pair.suite_value > pair.normal_value for pair in sample) / len(sample)

        first = paired_bootstrap(pairs, suite_rate, seed=7, draws=100)
        second = paired_bootstrap(pairs, suite_rate, seed=7, draws=100)
        self.assertEqual(first, second)
        self.assertEqual(first["estimate"], 2 / 3)
        self.assertEqual(quantile([0.0, 10.0], 0.25), 2.5)
        self.assertEqual(quantile([0.0, 10.0], 0.0), 0.0)
        self.assertEqual(quantile([0.0, 10.0], 1.0), 10.0)

    def test_bootstrap_rejects_empty_bool_and_non_finite_statistics(self):
        """Break: invalid draws or statistics could silently create meaningless intervals."""
        with self.assertRaisesRegex(BenchmarkError, "observations"):
            paired_bootstrap([], lambda _: 0.0, seed=1)
        with self.assertRaisesRegex(BenchmarkError, "seed"):
            paired_bootstrap([Pair("a", 1, 1.0, 0.0)], lambda _: 1.0, seed=True)
        with self.assertRaisesRegex(BenchmarkError, "draws"):
            paired_bootstrap([Pair("a", 1, 1.0, 0.0)], lambda _: 1.0, seed=1, draws=False)
        with self.assertRaisesRegex(BenchmarkError, "finite"):
            paired_bootstrap([Pair("a", 1, 1.0, 0.0)], lambda _: math.inf, seed=1)


class GateTests(unittest.TestCase):
    def test_translation_gates_have_exact_boundaries(self):
        """Break: inclusive/exclusive threshold changes could reverse the verdict."""
        metrics = passing_metrics()
        self.assertTrue(evaluate_gates(metrics)["translation"]["passed"])

        metrics["translation"]["bootstrap_lower"] = 0.50
        self.assertFalse(evaluate_gates(metrics)["translation"]["passed"])
        metrics = passing_metrics()
        metrics["translation"]["non_tied_win_rate"] = 0.599999
        self.assertFalse(evaluate_gates(metrics)["translation"]["passed"])
        metrics = passing_metrics()
        metrics["translation"]["mqm_reduction"] = 0.249999
        self.assertFalse(evaluate_gates(metrics)["translation"]["passed"])

    def test_review_gates_have_exact_boundaries_and_no_unavailable_verdict(self):
        """Break: review deltas at exact limits or absent mappings could be misclassified."""
        metrics = passing_metrics()
        self.assertTrue(evaluate_gates(metrics)["review"]["passed"])

        metrics["review"]["correction_success_rate"]["suite"] = 0.69
        self.assertFalse(evaluate_gates(metrics)["review"]["passed"])
        metrics = passing_metrics()
        metrics["review"]["required_error_recall"]["suite"] = 0.749999
        self.assertFalse(evaluate_gates(metrics)["review"]["passed"])

        metrics = passing_metrics()
        metrics["review"] = {"available": False}
        review_gate = evaluate_gates(metrics)["review"]
        self.assertFalse(review_gate["available"])
        self.assertIsNone(review_gate["passed"])

    def test_gate_inputs_reject_bool_nan_infinity_and_override_fields(self):
        """Break: Python numeric coercions or overrides could bypass registered gates."""
        for value in (True, math.nan, math.inf):
            with self.subTest(value=value):
                metrics = passing_metrics()
                metrics["translation"]["non_tied_win_rate"] = value
                with self.assertRaisesRegex(BenchmarkError, "finite number"):
                    evaluate_gates(metrics)
        metrics = passing_metrics()
        metrics["override"] = True
        with self.assertRaisesRegex(BenchmarkError, "fields"):
            evaluate_gates(metrics)


class LockedScoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = write_synthetic_dataset(self.root)
        write_reviewer_signoff(
            self.dataset,
            reviewer="pt-PT-reviewer",
            approved_at="2026-08-03T12:00:00Z",
        )
        atomic_write_json(
            self.dataset / "dataset-manifest.json",
            build_dataset_manifest(
                self.dataset, suite_commit="abc123", suite_dirty=False
            ),
        )
        self.evidence = self.root / "evidence"
        self._write_evidence()
        self.review_bundle = self.root / "review-bundle.json"
        self.condition_key = self.root / "condition-key.json"
        self._write_blind_artifacts()
        self.annotations_dir = self.root / "annotations"
        self.annotations_dir.mkdir()
        self._write_locked_annotations()
        self.paths = ScorePaths(
            dataset_dir=self.dataset,
            evidence_dir=self.evidence,
            review_bundle=self.review_bundle,
            condition_key=self.condition_key,
            annotations=self.annotations_dir / "annotations.jsonl",
            annotation_lock=self.annotations_dir / "annotation-lock.json",
        )

    def _write_evidence(self) -> None:
        self.evidence.mkdir()
        snapshot = self.evidence / "input-snapshot"
        skill = snapshot / "skills" / "translating-products"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("approved suite\n", encoding="utf-8")
        translation = snapshot / ".translation"
        translation.mkdir()
        context_files = {
            "project-brief.md": "Status: approved\n- Name: Fixture\n",
            "locales.yaml": "source_locale: en-US\ntarget_locales: [pt-PT]\n",
            "glossary.csv": "source_term,target_term,locale,context,status,notes\n",
            "style-guide.md": "Status: approved\n- Voice: Clear\n",
            "protected-terms.txt": "Codex\n",
        }
        for name, content in context_files.items():
            (translation / name).write_text(content, encoding="utf-8")
        atomic_write_json(translation / "setup-approval.json", {
            "status": "approved",
            "approved_by": "fixture-owner",
            "approved_at": "2026-08-03T10:00:00Z",
            "context_sha256": {
                name: sha256_bytes((translation / name).read_bytes())
                for name in context_files
            },
            "approved_empty": ["glossary.csv"],
        })
        self.runs: list[dict] = []
        run_ids: list[str] = []
        for index, source in enumerate(complete_synthetic_runs()):
            run = dict(source)
            output = run.pop("output")
            digest = sha256_bytes(output.encode("utf-8"))
            raw_path = self.evidence / "raw" / f"{digest}.txt"
            raw_path.parent.mkdir(exist_ok=True)
            raw_path.write_text(output, encoding="utf-8")
            record = {
                **run,
                "run_id": f"{index:020x}",
                "output_sha256": digest,
                "raw_output_path": raw_path.relative_to(self.evidence).as_posix(),
                "process_started": True,
                "started_at": "2026-08-03T00:00:00Z",
                "completed_at": "2026-08-03T00:00:01Z",
                "exit_code": 0,
                "timed_out": False,
                "refused": False,
                "malformed_output": False,
                "tool_misuse": False,
                "reason": None,
                "stderr": "",
                "expected_policy_sha256": None,
                "applied_policy_sha256": None,
                "policy_integrity": "not_required",
                "redacted": False,
                "project_fingerprint": f"project-{index}",
                "argv": ["fake"],
                "shell": False,
            }
            append_jsonl_fsync(self.evidence / "runs.jsonl", record)
            self.runs.append({**record, "output": output})
            run_ids.append(record["run_id"])
            append_jsonl_fsync(self.evidence / "validation.jsonl", {
                "schema_version": 1,
                "run_id": record["run_id"],
                "case_id": record["case_id"],
                "status": "passed",
                "output": output,
                "findings": [],
                "validator_errors": [],
                "applicable_checks": 0,
                "passed_checks": 0,
                "failed_checks": 0,
                "validator_error_checks": 0,
            })
        manifest = build_run_manifest(
            self.dataset,
            {"fixture": "runner-config"},
            suite_commit="abc123",
            evidence=self.evidence.resolve(),
            schedule_seed=20260803,
            bootstrap_seed=20260804,
        )
        manifest.update({
            "execution_config_sha256": "e" * 64,
            "schedule": {
                "run_ids": run_ids,
                "sha256": sha256_bytes(canonical_bytes(run_ids)),
            },
            "input_snapshot": _snapshot_manifest(snapshot),
        })
        atomic_write_json(self.evidence / "run-manifest.json", manifest)

    def _write_blind_artifacts(self) -> None:
        cases = [json.loads(line) for line in (self.dataset / "cases.jsonl").read_text().splitlines()]
        _, prepared = _validate_prepared_manifest(self.dataset, self.evidence)
        bundle, key = build_blind_bundle(
            self.runs, cases, 20260805, prepared_provenance=prepared
        )
        atomic_write_json(self.review_bundle, bundle)
        atomic_write_json(self.condition_key, key)

    def _write_locked_annotations(self) -> None:
        store = ReviewStore(self.review_bundle, self.annotations_dir)
        self.addCleanup(store.close)
        for review_item in store.bundle["items"]:
            labels = tuple(review_item["outputs"])
            comparisons = {
                f"{left}:{right}": "tie"
                for index, left in enumerate(labels)
                for right in labels[index + 1 :]
            }
            store.append({
                "item_id": review_item["id"],
                "revision": 1,
                "comparisons": comparisons,
                "confidence": "medium",
                "mqm": [],
                "major_or_worse": {label: False for label in labels},
                "note": "",
            })
        store.lock(reviewer_id="pt-PT-reviewer")

    def test_locked_paths_and_cli_produce_one_canonical_deterministic_document(self):
        """Break: scoring could skip lock verification or publish unstable/non-atomic JSON."""
        verify_locked_inputs(self.paths)
        first = score_paths(self.paths)
        second = score_paths(self.paths)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertEqual(first["provenance"]["bootstrap_seed"], 20260804)
        self.assertIsNone(first["gates"]["review"]["passed"])

        output = self.root / "score.json"
        completed = subprocess.run(
            [
                sys.executable, "-m", "scripts.benchmark.score",
                "--dataset", str(self.dataset),
                "--evidence", str(self.evidence),
                "--review-bundle", str(self.review_bundle),
                "--condition-key", str(self.condition_key),
                "--annotations", str(self.annotations_dir / "annotations.jsonl"),
                "--annotation-lock", str(self.annotations_dir / "annotation-lock.json"),
                "--output", str(output),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {
            "output": str(output),
            "sha256": sha256_bytes(output.read_bytes()),
        })
        self.assertEqual(output.read_bytes(), canonical_bytes(first))

    def test_annotation_tampering_fails_before_condition_key_loading(self):
        """Break: private labels could be exposed before annotation hash/completeness checks."""
        with (self.annotations_dir / "annotations.jsonl").open("ab") as target:
            target.write(b"{}\n")

        with mock.patch(
            "scripts.benchmark.score._load_verified_condition_key",
            side_effect=AssertionError("condition key read too early"),
        ):
            with self.assertRaisesRegex(BenchmarkError, "annotations hash mismatch"):
                verify_locked_inputs(self.paths)

    def test_tampering_malformed_json_and_incomplete_validation_fail_closed(self):
        """Break: changed keys or partial/hostile scoring inputs could be accepted."""
        original_key = self.condition_key.read_bytes()
        key = json.loads(original_key)
        first = next(iter(key["items"].values()))
        first["attempt"] = True
        self.condition_key.write_bytes(canonical_bytes(key))
        with self.assertRaisesRegex(BenchmarkError, "attempt"):
            verify_locked_inputs(self.paths)

        self.condition_key.write_bytes(original_key)
        validation_path = self.evidence / "validation.jsonl"
        records = validation_path.read_bytes().splitlines(keepends=True)
        validation_path.write_bytes(b"".join(records[:-1]))
        with self.assertRaisesRegex(BenchmarkError, "validation identities"):
            score_paths(self.paths)

        validation_path.write_bytes(b"".join(records))
        self.condition_key.write_bytes(
            b'{"items":' + b'[' * 257 + b'0' + b']' * 257 + b'}\n'
        )
        with self.assertRaisesRegex(BenchmarkError, "nesting"):
            verify_locked_inputs(self.paths)


if __name__ == "__main__":
    unittest.main()
