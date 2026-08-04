from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.benchmark.blind import build_blind_bundle, scan_visible_bundle
from scripts.benchmark.common import (
    BenchmarkError,
    append_jsonl_fsync,
    atomic_write_json,
    canonical_bytes,
    sha256_bytes,
)
from scripts.benchmark.prepare import build_dataset_manifest
from tests.benchmark_helpers import (
    synthetic_balanced_cases,
    write_reviewer_signoff,
    write_synthetic_dataset,
)


SEED = 20260806


def complete_synthetic_runs() -> list[dict]:
    cases, _ = synthetic_balanced_cases()
    records: list[dict] = []
    for case in cases:
        conditions = ["normal", "suite"]
        if case["diagnostic"]:
            conditions.append("context_only")
        for condition in conditions:
            for attempt in (1, 2, 3):
                output = f"Output {case['id']} {condition} {attempt}"
                records.append({
                    "schema_version": 1,
                    "run_id": f"run-{len(records):03d}",
                    "case_id": case["id"],
                    "condition": condition,
                    "attempt": attempt,
                    "runner_mode": "fake",
                    "status": "completed",
                    "failure_class": "success",
                    "output": output,
                    "output_sha256": sha256_bytes(output.encode("utf-8")),
                    "raw_output_path": f"raw/{len(records):03d}.txt",
                    "telemetry": {"runner": "fixture-runner"},
                    "usage": {"output_tokens": 5},
                })
    return records


def cases() -> list[dict]:
    return synthetic_balanced_cases()[0]


def nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(nested_keys(item) for item in value))
    return set()


class BlindingTests(unittest.TestCase):
    def test_bundle_has_exact_unique_output_split_and_repeat_counts(self):
        """Break: wrong cohort expansion or repeat insertion would change the reviewed sample."""
        bundle, key = build_blind_bundle(complete_synthetic_runs(), cases(), seed=SEED)
        repeat_ids = {
            item_id for item_id, hidden in key["items"].items() if "repeat_of" in hidden
        }
        unique = [item for item in bundle["items"] if item["id"] not in repeat_ids]
        repeats = [item for item in bundle["items"] if item["id"] in repeat_ids]

        self.assertEqual(len(unique), 180)
        self.assertEqual(sum(len(item["outputs"]) == 2 for item in unique), 135)
        self.assertEqual(sum(len(item["outputs"]) == 3 for item in unique), 45)
        self.assertEqual(len(repeats), 18)
        self.assertEqual(len(bundle["items"]), 198)
        self.assertEqual(len(key["items"]), 198)
        self.assertEqual(len({item["id"] for item in bundle["items"]}), 198)

    def test_bundle_is_deterministic_and_decodes_every_output(self):
        """Break: unstable RNG use or a wrong label mapping could misattribute reviewer choices."""
        runs = complete_synthetic_runs()
        first = build_blind_bundle(runs, cases(), SEED)
        second = build_blind_bundle(runs, cases(), SEED)

        self.assertEqual(first, second)
        expected = {
            (run["case_id"], run["condition"], run["attempt"]): run["output"]
            for run in runs
        }
        for item in first[0]["items"]:
            hidden = first[1]["items"][item["id"]]
            for label, condition in hidden["labels"].items():
                self.assertEqual(
                    item["outputs"][label],
                    expected[(hidden["case_id"], condition, hidden["attempt"])],
                )

    def test_repeats_are_visibly_silent_new_ids_with_fresh_label_orders(self):
        """Break: a repeat flag, reused ID, or unchanged label order could cue the reviewer."""
        bundle, key = build_blind_bundle(complete_synthetic_runs(), cases(), SEED)
        visible = {item["id"]: item for item in bundle["items"]}
        repeats = {
            item_id: hidden
            for item_id, hidden in key["items"].items()
            if "repeat_of" in hidden
        }

        self.assertEqual(len(repeats), 18)
        for repeat_id, hidden in repeats.items():
            original_id = hidden["repeat_of"]
            self.assertNotEqual(repeat_id, original_id)
            self.assertEqual(set(visible[repeat_id]), set(visible[original_id]))
            self.assertNotEqual(hidden["labels"], key["items"][original_id]["labels"])
            for field in ("source", "candidate", "context", "constraints"):
                if field in visible[original_id]:
                    self.assertEqual(visible[repeat_id][field], visible[original_id][field])

        forbidden_repeat_keys = {
            key_name
            for key_name in nested_keys(bundle)
            if "repeat" in key_name.casefold()
        }
        self.assertEqual(forbidden_repeat_keys, set())
        scan_visible_bundle(bundle)

    def test_scanner_recurses_through_metadata_and_serialized_payloads(self):
        """Break: nesting or serialized metadata could smuggle a condition key into review JSON."""
        bundle, _ = build_blind_bundle(complete_synthetic_runs(), cases(), SEED)
        mutations = [
            {"condition": "suite"},
            {"safe": [{"runner_model": "fixture-model-v1"}]},
            {"safe": '{"metric":{"threshold":0.75}}'},
            {"safe": "%7B%22condition%22%3A%22context_only%22%7D"},
            {"safe": "/Users/operator/private/suite.log"},
            {"safe": "a" * 64},
            {"safe": "b" * 40},
            {"safe": "123e4567-e89b-12d3-a456-426614174000"},
            {"safe": "skills/translating-products/SKILL.md"},
            {"seeded_error_id": "review-case-e1"},
            {"safe": 0.60},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(bundle)
                changed["items"][0]["constraints"]["nested"] = mutation
                with self.assertRaises(BenchmarkError):
                    scan_visible_bundle(changed)

    def test_scanner_allows_condition_like_literals_only_in_review_content(self):
        """Break: scanning raw linguistic content would reject legitimate adversarial cases/outputs."""
        changed_cases = cases()
        changed_cases[0] = {
            **changed_cases[0],
            "source": "The normal condition uses suite, runner, model, metric and /tmp/literal.",
        }
        review_index = next(
            index for index, case in enumerate(changed_cases) if case["task"] == "review"
        )
        changed_cases[review_index] = {
            **changed_cases[review_index],
            "candidate": "context_only repeat_of deadbeefdeadbeefdead",
        }
        changed_runs = complete_synthetic_runs()
        for run in changed_runs:
            if run["case_id"] == changed_cases[0]["id"]:
                run["output"] = "condition suite runner model latency metric /private/literal"
                run["output_sha256"] = sha256_bytes(run["output"].encode("utf-8"))

        bundle, _ = build_blind_bundle(changed_runs, changed_cases, SEED)

        scan_visible_bundle(bundle)

    def test_scanner_rejects_visible_repeat_and_condition_metadata_mutations(self):
        """Break: later producers could add an apparently helpful flag that silently unblinds review."""
        bundle, _ = build_blind_bundle(complete_synthetic_runs(), cases(), SEED)
        for field, value in (("is_repeat", True), ("condition_name", "A")):
            changed = copy.deepcopy(bundle)
            changed["items"][0][field] = value
            with self.subTest(field=field), self.assertRaises(BenchmarkError):
                scan_visible_bundle(changed)

    def test_builder_rejects_incomplete_duplicate_or_infrastructure_runs(self):
        """Break: missing/duplicate generations could be hidden behind plausible presentation counts."""
        runs = complete_synthetic_runs()
        broken_inputs = [
            runs[:-1],
            [*runs, dict(runs[0])],
            [{**run, "failure_class": "infrastructure"} if index == 0 else run
             for index, run in enumerate(runs)],
        ]
        for broken in broken_inputs:
            with self.subTest(count=len(broken)), self.assertRaises(BenchmarkError):
                build_blind_bundle(broken, cases(), SEED)

    def test_builder_rejects_mismatched_output_hash_and_duplicate_run_ids(self):
        """Break: unbound raw text or ambiguous run identity could silently mislabel an output."""
        runs = complete_synthetic_runs()
        wrong_hash = [dict(run) for run in runs]
        wrong_hash[0]["output_sha256"] = "0" * 64
        duplicate_id = [dict(run) for run in runs]
        duplicate_id[1]["run_id"] = duplicate_id[0]["run_id"]

        for broken in (wrong_hash, duplicate_id):
            with self.assertRaises(BenchmarkError):
                build_blind_bundle(broken, cases(), SEED)


class BlindingCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dataset = write_synthetic_dataset(self.root)
        write_reviewer_signoff(
            self.dataset,
            reviewer="pt-PT-reviewer",
            approved_at="2026-08-03T12:00:00Z",
        )
        manifest = build_dataset_manifest(
            self.dataset, suite_commit="abc123", suite_dirty=False
        )
        atomic_write_json(self.dataset / "dataset-manifest.json", manifest)
        self.evidence = self.root / "evidence"
        self._write_evidence()

    def tearDown(self):
        self.temp.cleanup()

    def _write_evidence(self) -> None:
        self.evidence.mkdir()
        run_ids: list[str] = []
        for index, run in enumerate(complete_synthetic_runs()):
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
            run_ids.append(record["run_id"])
        atomic_write_json(self.evidence / "run-manifest.json", {
            "schema_version": 1,
            "schedule": {
                "run_ids": run_ids,
                "sha256": sha256_bytes(canonical_bytes(run_ids)),
            },
        })

    def run_cli(self, review: Path, key: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.benchmark.blind",
                "--dataset",
                str(self.dataset),
                "--evidence",
                str(self.evidence),
                "--seed",
                str(SEED),
                "--review-bundle",
                str(review),
                "--condition-key",
                str(key),
            ],
            cwd=Path(__file__).parents[1],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cli_atomically_creates_rescanned_outputs_with_separate_permissions(self):
        """Break: publication could overwrite evidence, leak the key, or report unverified bytes."""
        public_dir = self.root / "public"
        private_dir = self.root / "private"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "condition-key.json"

        completed = self.run_cli(review, key)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["review_bundle_sha256"], sha256_bytes(review.read_bytes()))
        self.assertEqual(result["condition_key_sha256"], sha256_bytes(key.read_bytes()))
        self.assertEqual(stat.S_IMODE(review.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        scan_visible_bundle(json.loads(review.read_text(encoding="utf-8")))
        self.assertFalse(any("condition" in name.casefold() for name in nested_keys(
            json.loads(review.read_text(encoding="utf-8"))
        )))

    def test_cli_refuses_existing_same_directory_and_symlink_aliased_targets(self):
        """Break: unsafe target resolution could co-locate or overwrite the private condition key."""
        shared = self.root / "shared"
        shared.mkdir()
        existing = shared / "bundle.json"
        existing.write_text("owned", encoding="utf-8")
        private = self.root / "private"
        private.mkdir()

        completed = self.run_cli(existing, private / "key.json")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(existing.read_text(encoding="utf-8"), "owned")
        self.assertFalse((private / "key.json").exists())

        completed = self.run_cli(shared / "new.json", shared / "key.json")
        self.assertEqual(completed.returncode, 2)
        self.assertFalse((shared / "new.json").exists())
        self.assertFalse((shared / "key.json").exists())

        alias = self.root / "public-alias"
        os.symlink(shared, alias)
        completed = self.run_cli(shared / "new.json", alias / "key.json")
        self.assertEqual(completed.returncode, 2)
        self.assertFalse((shared / "new.json").exists())
        self.assertFalse((shared / "key.json").exists())


if __name__ == "__main__":
    unittest.main()
