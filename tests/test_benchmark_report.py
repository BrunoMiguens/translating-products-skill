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
