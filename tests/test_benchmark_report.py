from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.benchmark.common import BenchmarkError, canonical_bytes
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


class ReportTests(unittest.TestCase):
    def test_fixed_scores_render_byte_identical_reports(self):
        """Break: report rendering could add wall-clock or iteration-order data."""
        first_json, first_md = render_report(fixed_score_document())
        second_json, second_md = render_report(fixed_score_document())

        self.assertEqual(first_json, second_json)
        self.assertEqual(first_md, second_md)

    def test_report_has_registered_sections_scope_and_disclosure(self):
        """Break: publication could omit registered evidence or overstate human review."""
        _, markdown = render_report(fixed_score_document())
        text = markdown.decode("utf-8")

        self.assertIn("## Gate-by-gate verdict", text)
        self.assertIn("UI and mobile", text)
        self.assertIn("## Reviewer consistency", text)
        self.assertIn("AI-generated", text)
        self.assertIn("does not imply human review of future translations", text)
        self.assertIn("tested model", text)

    def test_headings_are_exact_and_in_registered_order(self):
        """Break: omitted, renamed, reordered, or generic warning sections could alter publication."""
        _, markdown = render_report(fixed_score_document())

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
        _, encoded = render_report(fixed_score_document())
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
            "Model: unavailable",
            "Runner configuration: unavailable",
        ):
            self.assertIn(expected, markdown)

    def test_representative_numeric_outcomes_are_sorted_and_publication_safe(self):
        """Break: examples could inherit input order or expose hidden labels and reviewer metadata."""
        document = fixed_score_document()
        pairs = document["metrics"]["translation"]["mqm_case_attempt_points"]
        document["metrics"]["translation"]["mqm_case_attempt_points"] = list(reversed(pairs))

        _, encoded = render_report(document)
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

        _, encoded = render_report(document)
        markdown = encoded.decode()

        self.assertIn(r"tool\|name\u000A## injected\u0000", markdown)
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
                    render_report(value)


class ReportCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scores = self.root / "scores.json"
        self.scores.write_bytes(canonical_bytes(fixed_score_document()))
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
        expected_results, expected_markdown = render_report(fixed_score_document())

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
             "--results", str(endpoint), "--markdown", str(self.markdown)],
            cwd=Path(__file__).resolve().parents[1], text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertTrue(endpoint.is_symlink())

    def test_second_write_failure_rolls_back_both_outputs(self):
        """Break: failure while writing the second artifact could leave partial publication."""
        expected = render_report(fixed_score_document())
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
        expected_results, expected_markdown = render_report(fixed_score_document())
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


if __name__ == "__main__":
    unittest.main()
