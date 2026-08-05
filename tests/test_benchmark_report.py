from __future__ import annotations

import copy
import errno
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.benchmark.common import (
    BenchmarkError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)
from scripts.benchmark import report
from scripts.benchmark.report import render_report
from scripts.benchmark.score import evaluate_gates, score_evidence
from tests.test_benchmark_score import base_evidence


def fixed_score_document() -> dict:
    metrics = score_evidence(base_evidence())
    return {
        "schema_version": 1,
        "provenance": {
            "dataset_sha256": "1" * 64,
            "dataset_manifest_sha256": "2" * 64,
            "run_manifest_sha256": "3" * 64,
            "review_bundle_sha256": "4" * 64,
            "condition_key_sha256": "5" * 64,
            "annotations_sha256": "6" * 64,
            "annotation_lock_sha256": "7" * 64,
            "validation_sha256": "8" * 64,
            "bootstrap_seed": 20260804,
            "review_mappings_sha256": None,
            "learned_metrics_sha256": None,
        },
        "metrics": metrics,
        "gates": evaluate_gates(metrics),
    }


def unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


def available(value: object) -> dict:
    return {"status": "available", "value": value}


def fixed_report_provenance(scores: dict | None = None) -> dict:
    scores = fixed_score_document() if scores is None else scores
    score_bytes = canonical_bytes(scores)
    return {
        "schema": "report-provenance-v1",
        "scores_sha256": sha256_bytes(score_bytes),
        "benchmark": {
            "benchmark_schema_version": 1,
            "dataset_version": "pt-pt-v1",
        },
        "git": {
            "commit_sha": "a" * 40,
            "tree_state": "clean",
            "diff_snapshot": unavailable("tree was clean"),
        },
        "input_hashes": {
            "dataset_sha256": scores["provenance"]["dataset_sha256"],
            "dataset_manifest_sha256": scores["provenance"]["dataset_manifest_sha256"],
            "run_manifest_sha256": scores["provenance"]["run_manifest_sha256"],
            "source_sha256": "9" * 64,
            "prompt_sha256": "a" * 64,
            "context_sha256": "b" * 64,
            "rubric_sha256": "c" * 64,
            "configuration_sha256": "d" * 64,
        },
        "execution": {
            "host": {"status": "available", "name": "fixture-host", "version": "1.2.3"},
            "runner": {
                "status": "available",
                "name": "fixture-runner",
                "version": "2.0",
                "invocation_mode": "cli",
            },
            "model": {
                "status": "available",
                "provider": "fixture-provider",
                "name": "fixture-model",
                "version": "2026-08-03",
            },
            "generation_settings": [
                {"name": "temperature", "value": 0.0},
                {"name": "top_p", "value": 1.0},
            ],
        },
        "seeds": {
            "schedule": available(20260803),
            "blinding": available(20260805),
            "bootstrap": available(scores["provenance"]["bootstrap_seed"]),
        },
        "attempt_history": [
            {
                "run_id": "run-1",
                "attempt": 1,
                "outcome": "infrastructure_failure",
                "retry_of": None,
                "retry_reason": None,
                "started_at": unavailable("process did not start"),
                "completed_at": unavailable("process did not start"),
            },
            {
                "run_id": "run-2",
                "attempt": 2,
                "outcome": "success",
                "retry_of": "run-1",
                "retry_reason": "runner executable was temporarily unavailable",
                "started_at": available("2026-08-03T00:00:00Z"),
                "completed_at": available("2026-08-03T00:00:01Z"),
            },
        ],
        "raw_outputs": [
            {"run_id": "run-1", "sha256": "e" * 64},
            {"run_id": "run-2", "sha256": "f" * 64},
        ],
        "result_bindings": {
            "structural_sha256": scores["provenance"]["validation_sha256"],
            "learned_metrics": unavailable("learned metrics were not produced"),
            "review_mappings": unavailable("review mappings were not produced"),
        },
        "review_bindings": {
            "blind_bundle_sha256": scores["provenance"]["review_bundle_sha256"],
            "condition_key_sha256": scores["provenance"]["condition_key_sha256"],
            "annotations_sha256": scores["provenance"]["annotations_sha256"],
            "annotation_lock_sha256": scores["provenance"]["annotation_lock_sha256"],
        },
        "code_versions": {
            "scoring": {
                "version": "score-v1",
                "sha256": sha256_file(Path(sys.modules["scripts.benchmark.score"].__file__)),
            },
            "report": {
                "version": "report-v1",
                "sha256": sha256_file(Path(report.__file__)),
            },
        },
    }


def render_fixed(scores: dict | None = None) -> tuple[bytes, bytes]:
    scores = fixed_score_document() if scores is None else scores
    return render_report(scores, fixed_report_provenance(scores))


class ReportTests(unittest.TestCase):
    def test_fixed_scores_render_byte_identical_reports(self):
        """Break: report rendering could add wall-clock or iteration-order data."""
        first_json, first_md = render_fixed()
        second_json, second_md = render_fixed()

        self.assertEqual(first_json, second_json)
        self.assertEqual(first_md, second_md)

    def test_report_has_registered_sections_scope_and_disclosure(self):
        """Break: publication could omit registered evidence or overstate human review."""
        _, markdown = render_fixed()
        text = markdown.decode("utf-8")

        self.assertIn("## Gate-by-gate verdict", text)
        self.assertIn("UI and mobile", text)
        self.assertIn("## Reviewer consistency", text)
        self.assertIn("AI-generated", text)
        self.assertIn("does not imply human review of future translations", text)
        self.assertIn("tested model", text)

    def test_headings_are_exact_and_in_registered_order(self):
        """Break: omitted, renamed, reordered, or generic warning sections could alter publication."""
        _, markdown = render_fixed()

        self.assertEqual(
            [line for line in markdown.decode().splitlines() if line.startswith("#")],
            [
                "# PT-PT Translation Benchmark Report",
                "## Verdict and scope",
                "## Experimental configuration",
                "## Gate-by-gate verdict",
                "## Blind human preference",
                "## MQM-lite quality",
                "## Product-integrity checks",
                "## Translation-review performance",
                "## Surface and difficulty scorecards",
                "## Context-only diagnostic",
                "## Learned-metric diagnostics",
                "## Latency, usage, tools, and research",
                "## Reviewer consistency",
                "## Representative anonymized outcomes",
                "## Critical and hard failures",
                "## Provenance and reproduction",
                "## Disclosure",
            ],
        )

    def test_report_renders_verdicts_all_scorecards_and_unavailable_states(self):
        """Break: an aggregate report could hide a gate, stratum, dimension, or missing input."""
        _, encoded = render_fixed()
        markdown = encoded.decode()

        for expected in (
            "Overall verdict: UNAVAILABLE",
            "Translation gate: FAIL",
            "non_tied_win_rate_at_least_60pct",
            "Overall scorecard",
            "Task scorecard",
            "Surface scorecard",
            "Difficulty scorecard",
            "Error-dimension scorecard",
            "Invariant scorecard",
            "Context-only",
            "review mappings unavailable",
            "learned metrics unavailable",
            "Model: provider=fixture-provider",
            "Runner configuration: name=fixture-runner",
        ):
            self.assertIn(expected, markdown)

    def test_representative_numeric_outcomes_are_sorted_and_publication_safe(self):
        """Break: examples could inherit input order or expose hidden labels and reviewer metadata."""
        document = fixed_score_document()
        pairs = document["metrics"]["translation"]["mqm_case_attempt_points"]
        document["metrics"]["translation"]["mqm_case_attempt_points"] = list(reversed(pairs))

        _, encoded = render_fixed(document)
        markdown = encoded.decode()

        self.assertLess(markdown.index("translation-1"), markdown.index("translation-2"))
        self.assertIn("Preference outcome unavailable", markdown)
        self.assertNotIn("condition_key_sha256", markdown)
        self.assertNotIn("private_labels", markdown)

    def test_markdown_cells_escape_metacharacters_controls_and_bound_huge_text(self):
        """Break: untrusted metric labels could inject tables/headings or make reports unbounded."""
        document = fixed_score_document()
        hostile = "tool|name\n## injected\x00" + "x" * 10000
        document["metrics"]["operational"]["tools"] = {
            "available": True,
            "by_condition": {
                "normal": {"calls": 1, "runs": 1, "unique": [hostile]},
                "suite": {"available": False},
                "context_only": {"available": False},
            },
        }

        _, encoded = render_fixed(document)
        markdown = encoded.decode()

        self.assertIn("toolU+007CnameU+000AU+0023U+0023 injectedU+0000", markdown)
        self.assertNotIn("\n## injected", markdown)
        self.assertLess(len(markdown), 50000)

    def test_exact_task7_schema_rejects_missing_extra_bool_nonfinite_and_private_fields(self):
        """Break: structurally invalid or private score data could be rendered as trusted results."""
        mutations = []
        missing = fixed_score_document()
        del missing["gates"]
        mutations.append(missing)
        extra = fixed_score_document()
        extra["private_labels"] = {"A": "suite"}
        mutations.append(extra)
        boolean = fixed_score_document()
        boolean["schema_version"] = True
        mutations.append(boolean)
        nonfinite = fixed_score_document()
        nonfinite["metrics"]["translation"]["non_tied_win_rate"] = math.nan
        mutations.append(nonfinite)

        for value in mutations:
            with self.subTest(keys=sorted(value)):
                with self.assertRaises(BenchmarkError):
                    render_report(value, fixed_report_provenance(value))


class ReportFixRoundTests(unittest.TestCase):
    def test_report_requires_bound_exact_provenance_and_embeds_it_in_results(self):
        """Break: a digest-only score could still publish without complete frozen provenance."""
        scores = fixed_score_document()
        provenance = fixed_report_provenance(scores)

        result_bytes, markdown = render_report(scores, provenance)
        result = json.loads(result_bytes)

        self.assertEqual(result, {
            "report_schema_version": 1,
            "report_provenance": provenance,
            "score_document": scores,
        })
        text = markdown.decode()
        for expected in (
            "fixture-host", "fixture-runner", "fixture-model", "temperature",
            "Schedule seed", "Blinding seed", "Attempt and retry history",
            "Raw-output content hashes", "score-v1", "report-v1",
        ):
            self.assertIn(expected, text)

        mismatched = copy.deepcopy(provenance)
        mismatched["scores_sha256"] = "0" * 64
        with self.assertRaisesRegex(BenchmarkError, "scores SHA-256"):
            render_report(scores, mismatched)

        overlap = copy.deepcopy(provenance)
        overlap["review_bindings"]["annotations_sha256"] = "0" * 64
        with self.assertRaisesRegex(BenchmarkError, "annotations"):
            render_report(scores, overlap)

    def test_provenance_exact_types_unavailable_records_and_code_bindings(self):
        """Break: malformed or stale provenance could be presented as reproducible evidence."""
        scores = fixed_score_document()
        mutations = []
        extra = fixed_report_provenance(scores)
        extra["hidden"] = {"A": "suite"}
        mutations.append(extra)
        boolean = fixed_report_provenance(scores)
        boolean["seeds"]["schedule"]["value"] = True
        mutations.append(boolean)
        missing = fixed_report_provenance(scores)
        del missing["input_hashes"]["rubric_sha256"]
        mutations.append(missing)
        malformed_unavailable = fixed_report_provenance(scores)
        malformed_unavailable["execution"]["host"] = {"status": "unavailable"}
        mutations.append(malformed_unavailable)
        wrong_code = fixed_report_provenance(scores)
        wrong_code["code_versions"]["report"]["sha256"] = "0" * 64
        mutations.append(wrong_code)
        nonfinite = fixed_report_provenance(scores)
        nonfinite["execution"]["generation_settings"][0]["value"] = math.inf
        mutations.append(nonfinite)

        for mutation in mutations:
            with self.subTest(keys=sorted(mutation)):
                with self.assertRaises(BenchmarkError):
                    render_report(scores, mutation)

    def test_condition_key_digest_is_exact_bound_provenance_without_private_mapping(self):
        """Break: report provenance could omit or misbind the frozen condition-key artifact."""
        scores = fixed_score_document()
        provenance = fixed_report_provenance(scores)
        provenance["review_bindings"]["condition_key_sha256"] = (
            scores["provenance"]["condition_key_sha256"]
        )

        result_bytes, markdown = render_report(scores, provenance)
        result = json.loads(result_bytes)
        expected = "5" * 64
        self.assertEqual(
            result["report_provenance"]["review_bindings"]["condition_key_sha256"],
            expected,
        )
        text = markdown.decode()
        self.assertIn(expected, text)
        self.assertNotIn("private_labels", text)
        self.assertNotIn('"A": "suite"', text)

        for replacement in ("A" * 64, "0" * 64):
            with self.subTest(replacement=replacement):
                mutation = copy.deepcopy(provenance)
                mutation["review_bindings"]["condition_key_sha256"] = replacement
                with self.assertRaises(BenchmarkError):
                    render_report(scores, mutation)

        missing = copy.deepcopy(provenance)
        del missing["review_bindings"]["condition_key_sha256"]
        with self.assertRaises(BenchmarkError):
            render_report(scores, missing)

    def test_retry_history_enforces_a_single_backward_attempt_chain(self):
        """Break: retry provenance could accept cycles, forward links, or impossible attempts."""
        scores = fixed_score_document()

        def attempt(
            run_id: str,
            number: int,
            outcome: str,
            retry_of: str | None,
            retry_reason: str | None,
        ) -> dict:
            return {
                "run_id": run_id,
                "attempt": number,
                "outcome": outcome,
                "retry_of": retry_of,
                "retry_reason": retry_reason,
                "started_at": available("2026-08-03T00:00:00Z"),
                "completed_at": available("2026-08-03T00:00:01Z"),
            }

        valid_chain = [
            attempt("run-1", 1, "infrastructure_failure", None, None),
            attempt("run-2", 2, "timeout", "run-1", "retry one"),
            attempt("run-3", 3, "success", "run-2", "retry two"),
        ]
        valid = fixed_report_provenance(scores)
        valid["attempt_history"] = valid_chain
        valid["raw_outputs"] = [
            {"run_id": "run-1", "sha256": "d" * 64},
            {"run_id": "run-2", "sha256": "e" * 64},
            {"run_id": "run-3", "sha256": "f" * 64},
        ]
        render_report(scores, valid)

        invalid_histories = {
            "self": [attempt("run-1", 1, "timeout", "run-1", "self")],
            "two-cycle": [
                attempt("run-1", 2, "timeout", "run-2", "cycle"),
                attempt("run-2", 2, "timeout", "run-1", "cycle"),
            ],
            "long-cycle": [
                attempt("run-1", 3, "timeout", "run-3", "cycle"),
                attempt("run-2", 2, "timeout", "run-1", "cycle"),
                attempt("run-3", 3, "timeout", "run-2", "cycle"),
            ],
            "forward": [
                attempt("run-1", 2, "timeout", "run-2", "forward"),
                attempt("run-2", 1, "timeout", None, None),
            ],
            "unknown": [attempt("run-1", 2, "timeout", "missing", "unknown")],
            "missing-reason": [
                attempt("run-1", 1, "timeout", None, None),
                attempt("run-2", 2, "success", "run-1", None),
            ],
            "reason-without-predecessor": [
                attempt("run-1", 1, "timeout", None, "unexpected")
            ],
            "duplicate-run-id": [
                attempt("run-1", 1, "timeout", None, None),
                attempt("run-1", 2, "success", "run-1", "duplicate"),
            ],
            "root-attempt-not-one": [
                attempt("run-1", 2, "timeout", None, None)
            ],
            "skipped-attempt": [
                attempt("run-1", 1, "timeout", None, None),
                attempt("run-2", 3, "success", "run-1", "skipped"),
            ],
            "retry-after-success": [
                attempt("run-1", 1, "success", None, None),
                attempt("run-2", 2, "success", "run-1", "impossible"),
            ],
            "branching-predecessor": [
                attempt("run-1", 1, "timeout", None, None),
                attempt("run-2", 2, "timeout", "run-1", "first"),
                attempt("run-3", 2, "success", "run-1", "branch"),
            ],
        }
        for name, history in invalid_histories.items():
            with self.subTest(name=name):
                provenance = fixed_report_provenance(scores)
                provenance["attempt_history"] = history
                provenance["raw_outputs"] = []
                with self.assertRaises(BenchmarkError):
                    render_report(scores, provenance)

    def test_markdown_neutralizes_links_images_code_emphasis_html_urls_and_format_controls(self):
        """Break: an untrusted label could trigger remote content or deceptive Markdown display."""
        scores = fixed_score_document()
        hostile = (
            "![remote](https://attacker.invalid/pixel) [link](https://example.invalid) "
            "https://bare.invalid `code` *em* _em_ <img src=x>\r\n"
            "bidi\u202ereverse\u2066 isolate\u200bzero"
        )
        scores["metrics"]["operational"]["tools"] = {
            "available": True,
            "by_condition": {
                "normal": {"calls": 1, "runs": 1, "unique": [hostile]},
                "suite": {"available": False},
                "context_only": {"available": False},
            },
        }
        provenance = fixed_report_provenance(scores)

        _, markdown = render_report(scores, provenance)
        text = markdown.decode()

        for active in (
            "![remote]", "[link](", "https://", "`code`", "*em*", "_em_",
            "<img", "\u202e", "\u2066", "\u200b",
        ):
            self.assertNotIn(active, text)
        for visible in (
            "U+005B", "U+0060", "U+002A", "U+003C", "U+000D", "U+000A",
            "U+202E", "U+2066", "U+200B",
        ):
            self.assertIn(visible, text)
        self.assertEqual(report._cell("surrogate\ud800"), "surrogateU+D800")


class ReportInputIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scores_value = fixed_score_document()
        self.scores = self.root / "Scores.json"
        self.scores.write_bytes(canonical_bytes(self.scores_value))
        self.provenance_value = fixed_report_provenance(self.scores_value)
        self.provenance = self.root / "provenance.json"
        self.provenance.write_bytes(canonical_bytes(self.provenance_value))
        self.results = self.root / "results.json"
        self.markdown = self.root / "report.md"

    def argv(self, *, results: Path | None = None, markdown: Path | None = None) -> list[str]:
        return [
            "--scores", str(self.scores),
            "--provenance", str(self.provenance),
            "--results", str(self.results if results is None else results),
            "--markdown", str(self.markdown if markdown is None else markdown),
        ]

    def write_expected_pair(self) -> tuple[bytes, bytes]:
        expected = render_report(self.scores_value, self.provenance_value)
        self.results.write_bytes(expected[0])
        self.markdown.write_bytes(expected[1])
        return expected

    def replace_output(self, target: Path, encoded: bytes = b"tampered") -> None:
        replacement = target.with_name(target.name + ".replacement")
        if replacement.exists():
            replacement.unlink()
        replacement.write_bytes(encoded)
        replacement.replace(target)

    def remove_path(self, path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def run_publication_failure(
        self,
        failure: str,
        armed: list[bool],
    ) -> tuple[int, str]:
        errors = io.StringIO()
        if failure == "second-publication":
            original = report._write_fd
            calls = 0

            def fail_second(descriptor: int, encoded: bytes) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    armed[0] = True
                    raise OSError("injected second publication failure")
                original(descriptor, encoded)

            patcher = mock.patch.object(report, "_write_fd", side_effect=fail_second)
        else:
            def fail_input(_value) -> None:
                armed[0] = True
                raise BenchmarkError("injected input acceptance failure")

            patcher = mock.patch.object(
                report, "_verify_held_input", side_effect=fail_input
            )
        with patcher, mock.patch("sys.stderr", errors):
            status = report.main(self.argv())
        return status, errors.getvalue()

    def make_replacement(self, label: str, kind: str) -> tuple[Path, int]:
        path = self.root / f"replacement-{label}"
        self.remove_path(path)
        source = self.root / f"source-{label}"
        self.remove_path(source)
        if kind == "file":
            path.write_bytes(b"replacement-file")
        elif kind == "symlink":
            source.write_bytes(b"symlink-target")
            path.symlink_to(source.name)
        elif kind == "hardlink":
            source.write_bytes(b"hardlink-source")
            os.link(source, path)
        elif kind == "directory":
            path.mkdir()
            (path / "sentinel").write_bytes(b"replacement-directory")
        else:
            raise AssertionError(kind)
        return path, path.lstat().st_ino

    def test_held_score_and_provenance_replacements_roll_back_publication(self):
        """Break: same-byte input replacement during rendering could still publish a pair."""
        for name, path in (("scores", self.scores), ("provenance", self.provenance)):
            with self.subTest(name=name):
                if self.results.exists():
                    self.results.unlink()
                if self.markdown.exists():
                    self.markdown.unlink()
                original_bytes = path.read_bytes()
                original_render = report._render_markdown

                def replace_after_render(*args, _path=path, _bytes=original_bytes, **kwargs):
                    rendered = original_render(*args, **kwargs)
                    replacement = _path.with_suffix(".replacement")
                    replacement.write_bytes(_bytes)
                    replacement.replace(_path)
                    return rendered

                with mock.patch.object(report, "_render_markdown", side_effect=replace_after_render):
                    self.assertEqual(report.main(self.argv()), 2)
                self.assertFalse(self.results.exists())
                self.assertFalse(self.markdown.exists())

    def test_verify_existing_rejects_every_input_output_identity_alias(self):
        """Break: verification could accept an input inode or one hard-linked file as an output."""
        expected_results, expected_markdown = render_report(
            self.scores_value, self.provenance_value
        )
        cases = (
            (self.scores, self.markdown, "consumed scores"),
            (self.provenance, self.markdown, "consumed provenance"),
            (self.results, self.scores, "consumed scores as Markdown"),
            (self.results, self.provenance, "consumed provenance as Markdown"),
        )
        for results, markdown, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                if markdown == self.markdown:
                    markdown.write_bytes(expected_markdown)
                if results == self.results:
                    results.write_bytes(expected_results)
                with mock.patch("sys.stderr"):
                    completed = report.main(self.argv(results=results, markdown=markdown) + ["--verify-existing"])
                self.assertEqual(completed, 2)

        if self.results.exists():
            self.results.unlink()
        if self.markdown.exists():
            self.markdown.unlink()
        self.results.write_bytes(expected_results)
        os.link(self.results, self.markdown)
        with mock.patch("sys.stderr"):
            self.assertEqual(report.main(self.argv() + ["--verify-existing"]), 2)

    def test_case_variant_consumed_alias_is_rejected_where_filesystem_folds_case(self):
        """Break: a case-insensitive pathname variant could disguise the consumed score inode."""
        variant = self.root / "scores.json"
        if not variant.exists() or not variant.samefile(self.scores):
            self.skipTest("filesystem is case-sensitive")
        self.markdown.write_bytes(render_report(self.scores_value, self.provenance_value)[1])

        with mock.patch("sys.stderr"):
            completed = report.main(
                self.argv(results=variant, markdown=self.markdown) + ["--verify-existing"]
            )

        self.assertEqual(completed, 2)

    def test_verify_existing_rechecks_replacements_after_each_initial_read(self):
        """Break: replacing either output after its first read could still verify successfully."""
        for trigger, target in ((1, self.results), (2, self.markdown)):
            with self.subTest(target=target.name):
                for path in (self.results, self.markdown):
                    if path.exists():
                        path.unlink()
                self.write_expected_pair()
                original = report._read_exact
                calls = 0

                def replace_after_read(*args, **kwargs):
                    nonlocal calls
                    held_output = original(*args, **kwargs)
                    calls += 1
                    if calls == trigger:
                        self.replace_output(target)
                    return held_output

                with mock.patch.object(report, "_read_exact", side_effect=replace_after_read):
                    with mock.patch("sys.stderr"):
                        status = report.main(self.argv() + ["--verify-existing"])
                self.assertEqual(status, 2)
                self.assertEqual(target.read_bytes(), b"tampered")

    def test_both_modes_recheck_replacements_during_input_acceptance(self):
        """Break: an output swap while accepting inputs could escape the earlier output checks."""
        for verify_existing in (False, True):
            for target in (self.results, self.markdown):
                with self.subTest(verify_existing=verify_existing, target=target.name):
                    for path in (self.results, self.markdown):
                        if path.exists():
                            path.unlink()
                    if verify_existing:
                        self.write_expected_pair()
                    original = report._verify_held_input
                    injected = False

                    def replace_during_acceptance(value):
                        nonlocal injected
                        original(value)
                        if not injected:
                            injected = True
                            self.replace_output(target)

                    arguments = self.argv() + (["--verify-existing"] if verify_existing else [])
                    with mock.patch.object(
                        report, "_verify_held_input", side_effect=replace_during_acceptance
                    ):
                        with mock.patch("sys.stderr"):
                            status = report.main(arguments)
                    self.assertEqual(status, 2)
                    self.assertEqual(target.read_bytes(), b"tampered")

    def test_both_modes_recheck_replacements_after_each_name_verification(self):
        """Break: swapping a name after a successful stat could create a false acceptance."""
        real_stat = os.stat
        for verify_existing in (False, True):
            for target in (self.results, self.markdown):
                with self.subTest(verify_existing=verify_existing, target=target.name):
                    for path in (self.results, self.markdown):
                        if path.exists():
                            path.unlink()
                    if verify_existing:
                        self.write_expected_pair()
                    injected = False

                    def replace_after_name(path, *args, **kwargs):
                        nonlocal injected
                        metadata = real_stat(path, *args, **kwargs)
                        if (
                            not injected
                            and kwargs.get("dir_fd") is not None
                            and os.fspath(path) == target.name
                        ):
                            injected = True
                            self.replace_output(target)
                        return metadata

                    arguments = self.argv() + (["--verify-existing"] if verify_existing else [])
                    with mock.patch.object(report.os, "stat", side_effect=replace_after_name):
                        with mock.patch("sys.stderr"):
                            status = report.main(arguments)
                    self.assertEqual(status, 2)
                    self.assertTrue(injected)
                    self.assertEqual(target.read_bytes(), b"tampered")

    def test_both_modes_reject_unlink_rename_hardlink_and_content_mutations(self):
        """Break: final acceptance could miss non-replacement output identity mutations."""
        for verify_existing in (False, True):
            for target in (self.results, self.markdown):
                for mutation in ("unlink", "rename", "hardlink", "content"):
                    with self.subTest(
                        verify_existing=verify_existing,
                        target=target.name,
                        mutation=mutation,
                    ):
                        sidecar = target.with_name(target.name + "." + mutation)
                        for path in (self.results, self.markdown, sidecar):
                            if path.exists():
                                path.unlink()
                        if verify_existing:
                            self.write_expected_pair()
                        original = report._verify_held_input
                        injected = False

                        def mutate_during_acceptance(value):
                            nonlocal injected
                            original(value)
                            if injected:
                                return
                            injected = True
                            if mutation == "unlink":
                                target.unlink()
                            elif mutation == "rename":
                                target.replace(sidecar)
                            elif mutation == "hardlink":
                                os.link(target, sidecar)
                            else:
                                target.write_bytes(b"tampered")

                        arguments = self.argv() + (
                            ["--verify-existing"] if verify_existing else []
                        )
                        with mock.patch.object(
                            report,
                            "_verify_held_input",
                            side_effect=mutate_during_acceptance,
                        ):
                            with mock.patch("sys.stderr"):
                                status = report.main(arguments)
                        self.assertEqual(status, 2)
                        self.assertTrue(injected)

    def test_rollback_never_deletes_a_swap_after_precleanup_stat(self):
        """Break: stat-then-unlink cleanup could delete an attacker replacement inode."""
        real_stat = os.stat
        for failure in ("second-publication", "input-acceptance"):
            for target in (self.results, self.markdown):
                with self.subTest(failure=failure, target=target.name):
                    for path in (self.results, self.markdown):
                        self.remove_path(path)
                    replacement, replacement_ino = self.make_replacement(
                        f"prestat-{failure}-{target.name}", "file"
                    )
                    displaced = self.root / f"displaced-{failure}-{target.name}"
                    self.remove_path(displaced)
                    armed = [False]
                    injected = False

                    def swap_after_stat(path, *args, **kwargs):
                        nonlocal injected
                        metadata = real_stat(path, *args, **kwargs)
                        if (
                            armed[0]
                            and not injected
                            and kwargs.get("dir_fd") is not None
                            and os.fspath(path) == target.name
                        ):
                            injected = True
                            target.replace(displaced)
                            replacement.replace(target)
                        return metadata

                    with mock.patch.object(report.os, "stat", side_effect=swap_after_stat):
                        status, _ = self.run_publication_failure(failure, armed)
                    self.assertEqual(status, 2)
                    surviving = [
                        path
                        for path in self.root.rglob("*")
                        if path.lstat().st_ino == replacement_ino
                    ]
                    self.assertTrue(
                        surviving,
                        "rollback deleted the attacker replacement inode",
                    )

    def test_rollback_restores_each_replacement_type_quarantined_before_rename(self):
        """Break: quarantine cleanup could lose file, symlink, hard-link, or directory replacements."""
        real_rename = os.rename
        atomic_move = report._rename_no_replace
        for failure in ("second-publication", "input-acceptance"):
            for target in (self.results, self.markdown):
                for kind in ("file", "symlink", "hardlink", "directory"):
                    with self.subTest(
                        failure=failure, target=target.name, kind=kind
                    ):
                        for path in (self.results, self.markdown):
                            self.remove_path(path)
                        replacement, replacement_ino = self.make_replacement(
                            f"before-{failure}-{target.name}-{kind}", kind
                        )
                        armed = [False]
                        injected = False

                        def replace_before_quarantine(
                            source_descriptor,
                            source_name,
                            destination_descriptor,
                            destination_name,
                        ):
                            nonlocal injected
                            if (
                                armed[0]
                                and not injected
                                and os.fspath(source_name) == target.name
                                and os.fspath(destination_name).startswith(
                                    ".report-recovery-"
                                )
                            ):
                                injected = True
                                target.unlink()
                                real_rename(replacement, target)
                            return atomic_move(
                                source_descriptor,
                                source_name,
                                destination_descriptor,
                                destination_name,
                            )

                        with mock.patch.object(
                            report,
                            "_rename_no_replace",
                            side_effect=replace_before_quarantine,
                        ):
                            status, _ = self.run_publication_failure(failure, armed)
                        self.assertEqual(status, 2)
                        self.assertTrue(injected)
                        self.assertTrue(target.exists() or target.is_symlink())
                        self.assertEqual(target.lstat().st_ino, replacement_ino)

    def test_rollback_preserves_replacement_installed_after_quarantine_rename(self):
        """Break: cleanup after quarantine could overwrite a newly occupied public name."""
        atomic_move = report._rename_no_replace
        for failure in ("second-publication", "input-acceptance"):
            for target in (self.results, self.markdown):
                with self.subTest(failure=failure, target=target.name):
                    for path in (self.results, self.markdown):
                        self.remove_path(path)
                    armed = [False]
                    injected = False

                    def occupy_after_quarantine(
                        source_descriptor,
                        source_name,
                        destination_descriptor,
                        destination_name,
                    ):
                        nonlocal injected
                        result = atomic_move(
                            source_descriptor,
                            source_name,
                            destination_descriptor,
                            destination_name,
                        )
                        if (
                            armed[0]
                            and not injected
                            and os.fspath(source_name) == target.name
                            and os.fspath(destination_name).startswith(
                                ".report-recovery-"
                            )
                        ):
                            injected = True
                            target.write_bytes(b"post-quarantine replacement")
                        return result

                    with mock.patch.object(
                        report,
                        "_rename_no_replace",
                        side_effect=occupy_after_quarantine,
                    ):
                        status, _ = self.run_publication_failure(failure, armed)
                    self.assertEqual(status, 2)
                    self.assertTrue(injected)
                    self.assertEqual(target.read_bytes(), b"post-quarantine replacement")

    def test_rollback_reports_recoverable_quarantine_when_restore_name_is_occupied(self):
        """Break: a failed exclusive restore could delete or hide the quarantined replacement."""
        real_rename = os.rename
        atomic_move = report._rename_no_replace
        for failure in ("second-publication", "input-acceptance"):
            for target in (self.results, self.markdown):
                with self.subTest(failure=failure, target=target.name):
                    for path in (self.results, self.markdown):
                        self.remove_path(path)
                    replacement, replacement_ino = self.make_replacement(
                        f"occupied-{failure}-{target.name}", "file"
                    )
                    armed = [False]
                    injected = False

                    def occupy_restore_name(
                        source_descriptor,
                        source_name,
                        destination_descriptor,
                        destination_name,
                    ):
                        nonlocal injected
                        if (
                            armed[0]
                            and not injected
                            and os.fspath(source_name) == target.name
                            and os.fspath(destination_name).startswith(
                                ".report-recovery-"
                            )
                        ):
                            injected = True
                            target.unlink()
                            real_rename(replacement, target)
                            result = atomic_move(
                                source_descriptor,
                                source_name,
                                destination_descriptor,
                                destination_name,
                            )
                            target.write_bytes(b"occupied-public-name")
                            return result
                        return atomic_move(
                            source_descriptor,
                            source_name,
                            destination_descriptor,
                            destination_name,
                        )

                    with mock.patch.object(
                        report,
                        "_rename_no_replace",
                        side_effect=occupy_restore_name,
                    ):
                        status, errors = self.run_publication_failure(failure, armed)
                    self.assertEqual(status, 2)
                    self.assertTrue(injected)
                    self.assertEqual(target.read_bytes(), b"occupied-public-name")
                    quarantined = [
                        path
                        for path in self.root.glob(".report-recovery-*")
                        if path.lstat().st_ino == replacement_ino
                    ]
                    self.assertEqual(len(quarantined), 1)
                    self.assertIn(str(quarantined[0]), errors)

    def test_rollback_never_accepts_a_directory_swapped_before_quarantine_open(self):
        """Break: mkdir-then-open could accept an attacker directory as trusted quarantine."""
        real_open = os.open
        real_rename = os.rename
        for failure in ("second-publication", "input-acceptance"):
            with self.subTest(failure=failure):
                for path in (self.results, self.markdown):
                    self.remove_path(path)
                armed = [False]
                injected = False
                attacker = self.root / f"attacker-quarantine-{failure}"
                displaced = self.root / f"created-quarantine-{failure}"
                self.remove_path(attacker)
                self.remove_path(displaced)
                attacker.mkdir()
                attacker_inode = attacker.stat().st_ino

                def swap_before_open(path, flags, *args, **kwargs):
                    nonlocal injected
                    name = os.fspath(path)
                    if (
                        armed[0]
                        and not injected
                        and name.startswith(".report-quarantine-")
                        and kwargs.get("dir_fd") is not None
                    ):
                        injected = True
                        created = self.root / name
                        real_rename(created, displaced)
                        real_rename(attacker, created)
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(report.os, "open", side_effect=swap_before_open):
                    status, errors = self.run_publication_failure(failure, armed)
                self.assertEqual(status, 2)
                surviving = [
                    path
                    for path in self.root.rglob("*")
                    if path.lstat().st_ino == attacker_inode
                ]
                self.assertTrue(surviving, "rollback deleted the swapped attacker directory")
                if injected:
                    self.assertIn(str(surviving[0]), errors)

    def test_rollback_never_rmdirs_a_cleanup_name_replacement(self):
        """Break: quarantine stat-then-rmdir could delete a replacement directory."""
        real_rmdir = os.rmdir
        real_rename = os.rename
        for failure in ("second-publication", "input-acceptance"):
            with self.subTest(failure=failure):
                for path in (self.results, self.markdown):
                    self.remove_path(path)
                armed = [False]
                injected = False
                replacement = self.root / f"cleanup-replacement-{failure}"
                displaced = self.root / f"cleanup-displaced-{failure}"
                self.remove_path(replacement)
                self.remove_path(displaced)
                replacement.mkdir()
                replacement_inode = replacement.stat().st_ino

                def swap_before_cleanup_removal(path, *args, **kwargs):
                    nonlocal injected
                    name = os.fspath(path)
                    if (
                        armed[0]
                        and not injected
                        and name.startswith(".report-quarantine-")
                        and kwargs.get("dir_fd") is not None
                    ):
                        injected = True
                        quarantine = self.root / name
                        real_rename(quarantine, displaced)
                        real_rename(replacement, quarantine)
                    return real_rmdir(path, *args, **kwargs)

                with mock.patch.object(
                    report.os, "rmdir", side_effect=swap_before_cleanup_removal
                ):
                    status, errors = self.run_publication_failure(failure, armed)
                self.assertEqual(status, 2)
                surviving = [
                    path
                    for path in self.root.rglob("*")
                    if path.lstat().st_ino == replacement_inode
                ]
                self.assertTrue(surviving, "rollback deleted the cleanup-name replacement")
                if injected:
                    self.assertIn(str(surviving[0]), errors)

    def test_rollback_fails_closed_when_atomic_recovery_move_is_unsupported(self):
        """Break: unsupported atomic moves could fall back to destructive path cleanup."""
        for failure in ("second-publication", "input-acceptance"):
            with self.subTest(failure=failure):
                for path in (self.results, self.markdown):
                    self.remove_path(path)
                armed = [False]
                with mock.patch.object(
                    report,
                    "_rename_no_replace",
                    side_effect=OSError(errno.ENOTSUP, "injected unsupported primitive"),
                ), mock.patch.object(report.os, "unlink", wraps=os.unlink) as unlink:
                    status, errors = self.run_publication_failure(failure, armed)
                self.assertEqual(status, 2)
                self.assertTrue(self.results.exists() or self.markdown.exists())
                self.assertIn("recoverable", errors)
                self.assertFalse(
                    any(
                        call.kwargs.get("dir_fd") is not None
                        for call in unlink.mock_calls
                    ),
                    "unsupported recovery must not fall back to unlinking public names",
                )

    def test_recovery_open_swap_preserves_both_the_moved_inode_and_replacement(self):
        """Break: a recovery-name swap around open could be accepted or destructively cleaned."""
        real_open = os.open
        real_rename = os.rename
        for failure in ("second-publication", "input-acceptance"):
            with self.subTest(failure=failure):
                for path in (self.results, self.markdown):
                    self.remove_path(path)
                armed = [False]
                injected = False
                displaced = self.root / f"recovery-open-displaced-{failure}"
                attacker = self.root / f"recovery-open-attacker-{failure}"
                self.remove_path(displaced)
                self.remove_path(attacker)
                attacker.write_bytes(b"unrelated recovery-name replacement")
                attacker_inode = attacker.stat().st_ino

                def swap_before_recovery_open(path, flags, *args, **kwargs):
                    nonlocal injected
                    name = os.fspath(path)
                    if (
                        armed[0]
                        and not injected
                        and name.startswith(".report-recovery-")
                        and kwargs.get("dir_fd") is not None
                    ):
                        injected = True
                        recovery = self.root / name
                        real_rename(recovery, displaced)
                        real_rename(attacker, recovery)
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(report.os, "open", side_effect=swap_before_recovery_open):
                    status, errors = self.run_publication_failure(failure, armed)
                self.assertEqual(status, 2)
                self.assertTrue(injected)
                self.assertTrue(displaced.exists())
                surviving = [
                    path
                    for path in self.root.rglob("*")
                    if path.lstat().st_ino == attacker_inode
                ]
                self.assertTrue(surviving)
                self.assertIn("recoverable", errors)

    def test_failed_recovery_open_preserves_the_atomic_move(self):
        """Break: failure opening moved recovery material could trigger path deletion."""
        real_open = os.open
        for failure in ("second-publication", "input-acceptance"):
            with self.subTest(failure=failure):
                for path in (self.results, self.markdown):
                    self.remove_path(path)
                for path in self.root.glob(".report-recovery-*"):
                    self.remove_path(path)
                armed = [False]
                injected = False

                def fail_recovery_open(path, flags, *args, **kwargs):
                    nonlocal injected
                    name = os.fspath(path)
                    if (
                        armed[0]
                        and not injected
                        and name.startswith(".report-recovery-")
                        and kwargs.get("dir_fd") is not None
                    ):
                        injected = True
                        raise OSError(errno.EIO, "injected recovery open failure")
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(report.os, "open", side_effect=fail_recovery_open):
                    status, errors = self.run_publication_failure(failure, armed)
                self.assertEqual(status, 2)
                self.assertTrue(injected)
                recoveries = list(self.root.glob(".report-recovery-*"))
                self.assertTrue(recoveries)
                self.assertTrue(
                    any(os.path.realpath(path) in errors for path in recoveries)
                )


class ReportCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scores = self.root / "scores.json"
        self.scores_value = fixed_score_document()
        self.scores.write_bytes(canonical_bytes(self.scores_value))
        self.provenance = self.root / "provenance.json"
        self.provenance_value = fixed_report_provenance(self.scores_value)
        self.provenance.write_bytes(canonical_bytes(self.provenance_value))
        self.results = self.root / "results.json"
        self.markdown = self.root / "report.md"

    def run_cli(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.benchmark.report",
                "--scores",
                str(self.scores),
                "--provenance",
                str(self.provenance),
                "--results",
                str(self.results),
                "--markdown",
                str(self.markdown),
                *extra,
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cli_exclusively_publishes_the_deterministic_pair(self):
        """Break: CLI publication could emit partial, noncanonical, or unstable artifacts."""
        expected_results, expected_markdown = render_report(
            self.scores_value, self.provenance_value
        )

        completed = self.run_cli()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.results.read_bytes(), expected_results)
        self.assertEqual(self.markdown.read_bytes(), expected_markdown)
        self.assertEqual(
            json.loads(completed.stdout),
            {"markdown": str(self.markdown), "results": str(self.results)},
        )

    def test_cli_refuses_existing_one_or_both_outputs_without_mutation(self):
        """Break: default publication could overwrite one artifact or leave a mixed pair."""
        cases = ((b"old-results", None), (None, b"old-markdown"), (b"old-r", b"old-m"))
        for index, (results, markdown) in enumerate(cases):
            with self.subTest(index=index):
                if self.results.exists():
                    self.results.unlink()
                if self.markdown.exists():
                    self.markdown.unlink()
                if results is not None:
                    self.results.write_bytes(results)
                if markdown is not None:
                    self.markdown.write_bytes(markdown)

                completed = self.run_cli()

                self.assertEqual(completed.returncode, 2)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertEqual(self.results.read_bytes() if self.results.exists() else None, results)
                self.assertEqual(self.markdown.read_bytes() if self.markdown.exists() else None, markdown)

    def test_cli_rejects_output_collisions_aliases_symlinks_and_consumed_input(self):
        """Break: aliased targets could collapse the pair or overwrite consumed score bytes."""
        original_scores = self.scores.read_bytes()
        alias_parent = self.root / "alias"
        try:
            alias_parent.symlink_to(self.root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {error}")
        cases = (
            (self.results, self.results),
            (self.results, alias_parent / self.results.name),
            (self.scores, self.markdown),
        )
        for index, (results, markdown) in enumerate(cases):
            with self.subTest(index=index):
                completed = subprocess.run(
                    [sys.executable, "-m", "scripts.benchmark.report", "--scores", str(self.scores),
                     "--provenance", str(self.provenance),
                     "--results", str(results), "--markdown", str(markdown)],
                    cwd=Path(__file__).resolve().parents[1], text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertEqual(self.scores.read_bytes(), original_scores)

        endpoint = self.root / "endpoint-link.json"
        endpoint.symlink_to(self.scores)
        completed = subprocess.run(
            [sys.executable, "-m", "scripts.benchmark.report", "--scores", str(self.scores),
             "--provenance", str(self.provenance),
             "--results", str(endpoint), "--markdown", str(self.markdown)],
            cwd=Path(__file__).resolve().parents[1], text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertTrue(endpoint.is_symlink())

    def test_second_write_failure_rolls_back_both_outputs(self):
        """Break: failure while writing the second artifact could leave partial publication."""
        expected = render_report(self.scores_value, self.provenance_value)
        original = report._write_fd
        calls = 0

        def fail_second(descriptor: int, encoded: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second write failure")
            original(descriptor, encoded)

        with mock.patch.object(report, "_write_fd", side_effect=fail_second):
            with self.assertRaisesRegex(BenchmarkError, "injected second write failure"):
                report._publish_pair(self.results, expected[0], self.markdown, expected[1])

        self.assertFalse(self.results.exists())
        self.assertFalse(self.markdown.exists())

    def test_verify_existing_accepts_only_an_exact_complete_pair(self):
        """Break: verification could accept missing, mismatched, or partially matching artifacts."""
        expected_results, expected_markdown = render_report(
            self.scores_value, self.provenance_value
        )
        self.results.write_bytes(expected_results)
        self.markdown.write_bytes(expected_markdown)
        completed = self.run_cli("--verify-existing")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        self.markdown.write_bytes(expected_markdown + b"changed")
        completed = self.run_cli("--verify-existing")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(self.results.read_bytes(), expected_results)
        self.assertEqual(self.markdown.read_bytes(), expected_markdown + b"changed")

        self.markdown.unlink()
        completed = self.run_cli("--verify-existing")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(self.results.read_bytes(), expected_results)
        self.assertFalse(self.markdown.exists())

    def test_cli_rejects_noncanonical_oversized_deep_and_hostile_scores_concisely(self):
        """Break: hostile score JSON could escape bounds/schema checks or create outputs."""
        original = self.scores.read_bytes()
        hostile = (
            b'{"schema_version": 1}\n',
            b'{"value":' + b"[" * 257 + b"0" + b"]" * 257 + b"}\n",
            b'{"schema_version":' + b"9" * 5000 + b"}\n",
            b"{" + b'\"x\":\"' + b"x" * (8 * 1024 * 1024) + b'\"}\n',
        )
        for index, encoded in enumerate(hostile):
            with self.subTest(index=index):
                self.scores.write_bytes(encoded)
                completed = self.run_cli()
                self.assertEqual(completed.returncode, 2)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertFalse(self.results.exists())
                self.assertFalse(self.markdown.exists())
        self.scores.write_bytes(original)

    def test_cli_requires_exact_bounded_canonical_report_provenance(self):
        """Break: malformed or resource-hostile frozen provenance could publish artifacts."""
        original = self.provenance.read_bytes()
        hostile = (
            b'{"schema": "report-provenance-v1"}\n',
            b'{"schema":"report-provenance-v1","schema":"report-provenance-v1"}\n',
            b'{"value":' + b"[" * 257 + b"0" + b"]" * 257 + b"}\n",
            b'{"seed":' + b"9" * 5000 + b"}\n",
            b"{" + b'\"x\":\"' + b"x" * (8 * 1024 * 1024) + b'\"}\n',
            b'{"schema":"report-provenance-v1","value":"\\ud800"}\n',
        )
        for index, encoded in enumerate(hostile):
            with self.subTest(index=index):
                self.provenance.write_bytes(encoded)
                completed = self.run_cli()
                self.assertEqual(completed.returncode, 2)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertFalse(self.results.exists())
                self.assertFalse(self.markdown.exists())
        self.provenance.write_bytes(original)

        completed = subprocess.run(
            [
                sys.executable, "-m", "scripts.benchmark.report",
                "--scores", str(self.scores),
                "--results", str(self.results),
                "--markdown", str(self.markdown),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertFalse(self.results.exists())
        self.assertFalse(self.markdown.exists())


if __name__ == "__main__":
    unittest.main()
