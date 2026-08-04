from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.benchmark.common import (
    BenchmarkError,
    append_jsonl_fsync,
    atomic_write_json,
    canonical_bytes,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    sha256_text,
)
from scripts.benchmark.schema import validate_cases
from scripts.benchmark.prepare import (
    build_dataset_manifest,
    build_run_manifest,
    verify_dataset_manifest,
)
from tests.benchmark_helpers import (
    synthetic_balanced_cases,
    write_reviewer_signoff,
    write_synthetic_dataset,
)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        for path in sorted(self.temp_dir.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            else:
                path.rmdir()
        self.temp_dir.rmdir()

    def test_canonical_json_and_hash_are_stable(self):
        """Break: changing canonical JSON formatting changes frozen hashes."""
        self.assertEqual(canonical_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}\n')
        self.assertEqual(
            sha256_bytes(canonical_bytes({"b": 2, "a": 1})),
            "e8d38819d39f705646bfb643368eca78f7db476c16471dbc33b941b27326410d",
        )

    def test_schema_rejects_wrong_balance_duplicate_ids_and_unknown_fields(self):
        """Break: invalid cohorts could otherwise be frozen as comparable evidence."""
        cases, errors = synthetic_balanced_cases()
        validate_cases(cases, errors)
        with self.assertRaisesRegex(BenchmarkError, "duplicate case id"):
            validate_cases(cases + [cases[0]], errors)
        broken = [dict(case) for case in cases]
        broken[0]["surface"] = "email"
        with self.assertRaisesRegex(BenchmarkError, "surface"):
            validate_cases(broken, errors)

    def test_schema_requires_complete_seeded_error_records_for_review_cases(self):
        """Break: scoring could silently use incomplete seeded-error decisions."""
        cases, errors = synthetic_balanced_cases()
        review_id = next(case["id"] for case in cases if case["task"] == "review")
        errors[review_id] = [{"id": f"{review_id}-e1"}]

        with self.assertRaisesRegex(BenchmarkError, "seeded error.*accepted_corrections"):
            validate_cases(cases, errors)

    def test_json_helpers_preserve_canonical_bytes_and_jsonl_records(self):
        """Break: JSON data could be written non-canonically or without durable records."""
        target = self.temp_dir / "record.json"
        atomic_write_json(target, {"b": "á", "a": 1})
        self.assertEqual(target.read_bytes(), b'{"a":1,"b":"\xc3\xa1"}\n')
        self.assertEqual(read_json(target), {"a": 1, "b": "á"})
        self.assertEqual(sha256_file(target), sha256_bytes(target.read_bytes()))
        self.assertEqual(sha256_text("á"), sha256_bytes("á".encode("utf-8")))

        events = self.temp_dir / "events.jsonl"
        append_jsonl_fsync(events, {"id": 1})
        append_jsonl_fsync(events, {"id": 2})
        self.assertEqual(read_jsonl(events), [{"id": 1}, {"id": 2}])

    def test_atomic_write_refuses_an_output_symlink(self):
        """Break: an output path symlink could redirect a generated manifest."""
        outside = self.temp_dir / "outside.json"
        outside.write_text("untouched", encoding="utf-8")
        output_link = self.temp_dir / "manifest.json"
        os.symlink(outside, output_link)

        with self.assertRaisesRegex(BenchmarkError, "symlink"):
            atomic_write_json(output_link, {"replaced": True})
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")

    def test_manifest_refuses_unsigned_or_changed_dataset(self):
        """Break: unsigned or byte-modified benchmark data could be executed."""
        dataset = write_synthetic_dataset(self.temp_dir)
        with self.assertRaisesRegex(BenchmarkError, "PT-PT reviewer sign-off"):
            build_dataset_manifest(dataset, suite_commit="abc123", suite_dirty=False)
        write_reviewer_signoff(
            dataset,
            reviewer="pt-PT-reviewer",
            approved_at="2026-08-03T12:00:00Z",
        )
        manifest = build_dataset_manifest(dataset, suite_commit="abc123", suite_dirty=False)
        atomic_write_json(dataset / "dataset-manifest.json", manifest)
        verify_dataset_manifest(dataset)
        (dataset / "rubric.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(BenchmarkError, "hash mismatch"):
            verify_dataset_manifest(dataset)

    def test_dirty_tree_requires_snapshot_and_diff_hash(self):
        """Break: a dirty suite state could be unrecorded in a run manifest."""
        dataset = write_synthetic_dataset(self.temp_dir)
        write_reviewer_signoff(
            dataset,
            reviewer="pt-PT-reviewer",
            approved_at="2026-08-03T12:00:00Z",
        )
        atomic_write_json(
            dataset / "dataset-manifest.json",
            build_dataset_manifest(dataset, suite_commit="abc123", suite_dirty=False),
        )
        config = self.temp_dir / "runner-config.json"
        atomic_write_json(config, {"runner": "fake"})
        diff_path = self.temp_dir / "suite.diff"
        diff_path.write_text("diff --git a/a b/a\n", encoding="utf-8")

        with self.assertRaisesRegex(BenchmarkError, "dirty suite tree"):
            build_run_manifest(dataset, config, suite_dirty=True)
        manifest = build_run_manifest(
            dataset,
            config,
            suite_dirty=True,
            snapshot_id="working-tree-20260803",
            diff_artifact=diff_path,
        )
        self.assertEqual(manifest["suite"]["diff_sha256"], sha256_file(diff_path))

    def test_prepare_cli_writes_frozen_dataset_and_run_manifests(self):
        """Break: operators could not materialize the same verified inputs offline."""
        dataset = write_synthetic_dataset(self.temp_dir)
        write_reviewer_signoff(
            dataset,
            reviewer="pt-PT-reviewer",
            approved_at="2026-08-03T12:00:00Z",
        )
        dataset_manifest = dataset / "dataset-manifest.json"
        dataset_diff = self.temp_dir / "dataset.diff"
        dataset_diff.write_text("recorded dirty snapshot\n", encoding="utf-8")
        dataset_result = subprocess.run(
            [
                sys.executable, "-m", "scripts.benchmark.prepare", "dataset",
                "--dataset", str(dataset), "--write-manifest", str(dataset_manifest),
                "--snapshot-id", "test-dataset-snapshot", "--diff-artifact", str(dataset_diff),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(dataset_result.returncode, 0, dataset_result.stderr)
        self.assertEqual(json.loads(dataset_result.stdout), read_json(dataset_manifest))

        config = self.temp_dir / "runner.json"
        atomic_write_json(config, {"runner": "fake"})
        evidence = self.temp_dir / "evidence"
        diff = self.temp_dir / "suite.diff"
        diff.write_text("recorded local changes\n", encoding="utf-8")
        run_result = subprocess.run(
            [
                sys.executable, "-m", "scripts.benchmark.prepare", "run",
                "--dataset", str(dataset), "--config", str(config),
                "--evidence", str(evidence), "--schedule-seed", "20260803",
                "--bootstrap-seed", "20260804", "--snapshot-id", "test-snapshot",
                "--diff-artifact", str(diff),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(run_result.returncode, 0, run_result.stderr)
        self.assertEqual(json.loads(run_result.stdout), read_json(evidence / "run-manifest.json"))

    def test_prepare_cli_returns_two_for_data_errors(self):
        """Break: scripts could misclassify invalid data as an infrastructure failure."""
        missing = self.temp_dir / "missing"
        diff_path = self.temp_dir / "missing-data.diff"
        diff_path.write_text("recorded dirty snapshot\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable, "-m", "scripts.benchmark.prepare", "dataset",
                "--dataset", str(missing), "--snapshot-id", "missing-data-snapshot",
                "--diff-artifact", str(diff_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read JSONL", result.stderr)

    def test_dirty_dataset_cli_requires_and_records_snapshot_provenance(self):
        """Break: a dirty tree could freeze a dataset with no reproducible snapshot."""
        dataset = write_synthetic_dataset(self.temp_dir)
        write_reviewer_signoff(
            dataset,
            reviewer="pt-PT-reviewer",
            approved_at="2026-08-03T12:00:00Z",
        )
        manifest_path = dataset / "dataset-manifest.json"
        without_provenance = subprocess.run(
            [
                sys.executable, "-m", "scripts.benchmark.prepare", "dataset",
                "--dataset", str(dataset), "--write-manifest", str(manifest_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(without_provenance.returncode, 2)
        self.assertIn("dirty suite tree", without_provenance.stderr)
        self.assertFalse(manifest_path.exists())

        diff_path = self.temp_dir / "dataset.diff"
        diff_path.write_text("recorded dirty snapshot\n", encoding="utf-8")
        with_provenance = subprocess.run(
            [
                sys.executable, "-m", "scripts.benchmark.prepare", "dataset",
                "--dataset", str(dataset), "--write-manifest", str(manifest_path),
                "--snapshot-id", "dataset-test-snapshot", "--diff-artifact", str(diff_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(with_provenance.returncode, 0, with_provenance.stderr)
        self.assertEqual(
            read_json(manifest_path)["suite"]["diff_sha256"], sha256_file(diff_path)
        )

    def test_schema_rejects_a_container_as_seeded_error_severity(self):
        """Break: malformed JSON types could escape as a TypeError instead of data errors."""
        cases, errors = synthetic_balanced_cases()
        review_id = next(case["id"] for case in cases if case["task"] == "review")
        errors[review_id][0]["severity"] = {"major": True}

        with self.assertRaisesRegex(BenchmarkError, "severity"):
            validate_cases(cases, errors)

    def test_manifest_rejects_a_container_as_an_approved_case_id(self):
        """Break: malformed sign-off JSON could escape data-error handling as TypeError."""
        dataset = write_synthetic_dataset(self.temp_dir)
        write_reviewer_signoff(
            dataset,
            reviewer="pt-PT-reviewer",
            approved_at="2026-08-03T12:00:00Z",
        )
        signoff = read_json(dataset / "reference-signoff.json")
        signoff["approved_case_ids"][0] = {"case": "not-an-id"}
        atomic_write_json(dataset / "reference-signoff.json", signoff)

        with self.assertRaisesRegex(BenchmarkError, "approved case ids"):
            build_dataset_manifest(dataset, suite_commit="abc123", suite_dirty=False)

    def test_prepare_cli_returns_two_without_traceback_for_malformed_types(self):
        """Break: malformed user data could produce a traceback and exit code one."""
        dataset = write_synthetic_dataset(self.temp_dir)
        write_reviewer_signoff(
            dataset,
            reviewer="pt-PT-reviewer",
            approved_at="2026-08-03T12:00:00Z",
        )
        seeded = read_json(dataset / "seeded-errors.json")
        first_review_id = next(iter(seeded))
        seeded[first_review_id][0]["severity"] = ["major"]
        atomic_write_json(dataset / "seeded-errors.json", seeded)
        diff_path = self.temp_dir / "malformed.diff"
        diff_path.write_text("recorded dirty snapshot\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable, "-m", "scripts.benchmark.prepare", "dataset",
                "--dataset", str(dataset), "--snapshot-id", "malformed-data-snapshot",
                "--diff-artifact", str(diff_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("severity", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
