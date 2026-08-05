from __future__ import annotations

import copy
import base64
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import scripts.benchmark.blind as blind
from scripts.benchmark.blind import build_blind_bundle, scan_visible_bundle
from scripts.benchmark.common import (
    BenchmarkError,
    append_jsonl_fsync,
    atomic_write_json,
    canonical_bytes,
    sha256_bytes,
)
from scripts.benchmark.prepare import build_dataset_manifest, build_run_manifest
from scripts.benchmark.run import _snapshot_manifest
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
                digest = sha256_bytes(output.encode("utf-8"))
                records.append({
                    "schema_version": 1,
                    "run_id": f"{len(records):020x}",
                    "case_id": case["id"],
                    "condition": condition,
                    "attempt": attempt,
                    "runner_mode": "fake",
                    "status": "completed",
                    "failure_class": "success",
                    "output": output,
                    "output_sha256": digest,
                    "raw_output_path": f"raw/{digest}.txt",
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
                    "telemetry": {"runner": "fixture-runner"},
                    "usage": {"output_tokens": 5},
                    "expected_policy_sha256": None,
                    "applied_policy_sha256": None,
                    "policy_integrity": "not_required",
                    "redacted": False,
                    "project_fingerprint": f"project-{len(records):03d}",
                    "argv": ["fake"],
                    "shell": False,
                })
    return records


def cases() -> list[dict]:
    return synthetic_balanced_cases()[0]


def as_manual(run: dict) -> dict:
    return {
        **run,
        "runner_mode": "manual",
        "status": "completed",
        "failure_class": "success",
        "process_started": False,
        "completed_at": run["started_at"],
        "exit_code": 0,
        "timed_out": False,
        "refused": False,
        "malformed_output": False,
        "tool_misuse": False,
        "reason": "manual_import",
        "stderr": "",
        "telemetry": {},
        "usage": None,
        "expected_policy_sha256": None,
        "applied_policy_sha256": None,
        "policy_integrity": "not_required",
        "project_fingerprint": "manual",
        "argv": [],
        "shell": False,
    }


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
            {"safe": "0.60"},
            {"safe": "60%"},
            {"runtime_ms": 10},
            {"started_at": "yesterday"},
            {"completed_at": "today"},
            {"tool_calls": []},
            {"generation_order": 1},
            {"seed": 20260806},
            {"host_version": "1.0"},
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
                run["raw_output_path"] = f"raw/{run['output_sha256']}.txt"

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

    def test_builder_recomputes_outcomes_and_rejects_malformed_record_types(self):
        """Break: stored labels could disguise infrastructure or malformed evidence as output."""
        runs = complete_synthetic_runs()
        mutations = (
            {"schema_version": True},
            {"process_started": False},
            {"status": "pending"},
            {"run_id": "not-a-run-id"},
            {"failure_class": "model_outcome"},
            {"case_id": [runs[0]["case_id"]]},
            {"attempt": True},
            {"timed_out": "false"},
            {"runner_mode": "cli"},
        )
        for mutation in mutations:
            changed = [dict(run) for run in runs]
            changed[0].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(BenchmarkError):
                build_blind_bundle(changed, cases(), SEED)

    def test_builder_accepts_a_consistently_classified_started_model_outcome(self):
        """Break: model refusals/timeouts are durable outcomes and must not be retried or dropped."""
        runs = complete_synthetic_runs()
        runs[0].update({
            "status": "model_outcome",
            "failure_class": "model_outcome",
            "timed_out": True,
            "reason": "timeout",
        })

        bundle, _ = build_blind_bundle(runs, cases(), SEED)

        self.assertEqual(len(bundle["items"]), 198)

    def test_builder_rejects_decoded_or_case_changed_exact_hidden_values(self):
        """Break: encoding a private reference must not turn it into harmless visible metadata."""
        original_cases = cases()
        private = original_cases[0]["reference"]
        encoded_values = (
            private.upper(),
            private.replace("R", "\\u0052", 1),
            private.replace("R", "&#82;", 1),
            private.replace(" ", "%20"),
            base64.b64encode(private.encode("utf-8")).decode("ascii"),
        )
        for encoded in encoded_values:
            changed_cases = [dict(case) for case in original_cases]
            changed_cases[0]["context"] = encoded
            with self.subTest(encoded=encoded), self.assertRaises(BenchmarkError):
                build_blind_bundle(complete_synthetic_runs(), changed_cases, SEED)

    def test_builder_rejects_multiline_base64_and_deep_html_hidden_values(self):
        """Break: control whitespace or encoding depth must not bypass hidden-value matching."""
        original_cases = cases()
        multiline = "Private first line\nPrivate second line"
        multiline_cases = [dict(case) for case in original_cases]
        multiline_cases[0].update({
            "reference": multiline,
            "context": base64.b64encode(multiline.encode("utf-8")).decode("ascii"),
        })

        private = "Depth bounded private scalar"
        deeply_encoded = "".join(f"&#{ord(character)};" for character in private)
        for _ in range(20):
            deeply_encoded = deeply_encoded.replace("&", "&amp;")
        deep_cases = [dict(case) for case in original_cases]
        deep_cases[0].update({"reviewer_secret": private, "context": deeply_encoded})

        excessive = "".join(f"&#{ord(character)};" for character in private)
        for _ in range(40):
            excessive = excessive.replace("&", "&amp;")
        excessive_cases = [dict(case) for case in original_cases]
        excessive_cases[0].update({"reviewer_secret": private, "context": excessive})

        for label, changed_cases in (
            ("multiline base64", multiline_cases),
            ("twenty-layer HTML", deep_cases),
            ("over-budget HTML", excessive_cases),
        ):
            with self.subTest(label=label), self.assertRaises(BenchmarkError):
                build_blind_bundle(complete_synthetic_runs(), changed_cases, SEED)

    def test_builder_rejects_surrogate_pairs_and_wrapped_bare_base64(self):
        """Break: JSON surrogate pairs and base64 whitespace must normalize before matching."""
        original_cases = cases()
        surrogate_hidden = "Hidden 😀 value"
        base64_hidden = "😀 Hidden base64 value"
        standard = base64.b64encode(base64_hidden.encode("utf-8")).decode("ascii")
        urlsafe = base64.urlsafe_b64encode(base64_hidden.encode("utf-8")).decode("ascii")
        self.assertNotEqual(standard, urlsafe)
        reproducers = (
            (surrogate_hidden, r"Hidden \ud83d\ude00 value"),
            (base64_hidden, "\n".join(
                standard[index:index + 8] for index in range(0, len(standard), 8)
            )),
            (base64_hidden, "\n".join(
                urlsafe[index:index + 8] for index in range(0, len(urlsafe), 8)
            )),
        )
        for hidden, encoded in reproducers:
            changed_cases = [dict(case) for case in original_cases]
            changed_cases[0].update({"reviewer_secret": hidden, "context": encoded})
            with self.subTest(encoded=encoded), self.assertRaises(BenchmarkError):
                build_blind_bundle(complete_synthetic_runs(), changed_cases, SEED)

    def test_builder_combines_each_surrogate_pair_despite_unrelated_malformed_text(self):
        """Break: one lone surrogate must not hide a separate encoded private scalar."""
        hidden = "Hidden 😀 value"
        nested = json.dumps(json.dumps(r"Hidden \ud83d\ude00 value \ud800"))
        bounded = r"Hidden \ud83d\ude00 value \ud800"
        for _ in range(20):
            bounded = bounded.replace("\\", r"\u005c")
        reproducers = (
            (hidden, r"\ud800 Hidden \ud83d\ude00 value"),
            (hidden, r"Hidden \ud83d\ude00 value \ud800"),
            (hidden, r"\udc00 Hidden \ud83d\ude00 value"),
            (hidden, r"Hidden \ud83d\ude00 value \udc00"),
            (
                "Other 🚀 then Hidden 😀 value",
                r"Other \ud83d\ude80 then Hidden \ud83d\ude00 value \ud800",
            ),
            (
                "Literal café Hidden 😀 value",
                "Literal café " + r"Hidden \ud83d\ude00 value \udc00",
            ),
            (hidden, r"Invalid \u12xz then Hidden \ud83d\ude00 value"),
            (hidden, nested),
            (hidden, bounded),
        )
        for private, encoded in reproducers:
            changed_cases = [dict(case) for case in cases()]
            changed_cases[0].update({"reviewer_secret": private, "context": encoded})
            with self.subTest(encoded=encoded), self.assertRaises(BenchmarkError):
                build_blind_bundle(complete_synthetic_runs(), changed_cases, SEED)

        excessive = r"\u0068armless"
        for _ in range(40):
            excessive = excessive.replace("\\", r"\u005c")
        over_budget_cases = [dict(case) for case in cases()]
        over_budget_cases[0]["context"] = excessive
        with self.assertRaises(BenchmarkError):
            build_blind_bundle(complete_synthetic_runs(), over_budget_cases, SEED)

        lone = r"before \ud800 middle \udc00 after"
        self.assertEqual(
            blind._unicode_unescape(lone),
            "before \ud800 middle \udc00 after",
        )

    def test_encoded_hidden_literals_remain_allowed_in_opaque_review_content(self):
        """Break: leak hardening must not scan source, candidate, or generated outputs."""
        changed_cases = cases()
        hidden = "Hidden 😀 value"
        changed_cases[0].update({
            "reviewer_secret": hidden,
            "source": r"Hidden \ud83d\ude00 value",
        })
        changed_runs = complete_synthetic_runs()
        changed_runs[0]["output"] = base64.b64encode(hidden.encode("utf-8")).decode("ascii")
        changed_runs[0]["output_sha256"] = sha256_bytes(
            changed_runs[0]["output"].encode("utf-8")
        )
        changed_runs[0]["raw_output_path"] = (
            f"raw/{changed_runs[0]['output_sha256']}.txt"
        )

        bundle, _ = build_blind_bundle(changed_runs, changed_cases, SEED)

        self.assertEqual(len(bundle["items"]), 198)

    def test_builder_accepts_complete_manual_and_mixed_supported_modes(self):
        """Break: Task 3 manual evidence must remain consumable with fake and CLI records."""
        manual = [as_manual(run) for run in complete_synthetic_runs()]

        manual_bundle, _ = build_blind_bundle(manual, cases(), SEED)

        mixed = complete_synthetic_runs()
        mixed[0] = as_manual(mixed[0])
        mixed[1].update({
            "runner_mode": "cli",
            "expected_policy_sha256": "a" * 64,
            "applied_policy_sha256": "a" * 64,
            "policy_integrity": "verified",
            "argv": ["sandbox-adapter", "runner"],
        })
        mixed_bundle, _ = build_blind_bundle(mixed, cases(), SEED)

        self.assertEqual(len(manual_bundle["items"]), 198)
        self.assertEqual(len(mixed_bundle["items"]), 198)

    def test_builder_rejects_incomplete_or_inconsistent_manual_records(self):
        """Break: manual compatibility must not admit pending, failed, or invented records."""
        manual = [as_manual(run) for run in complete_synthetic_runs()]
        mutations = (
            {"process_started": True},
            {"status": "pending"},
            {"failure_class": "model_outcome"},
            {"reason": None},
            {"argv": ["manual"]},
            {"usage": {}},
        )
        for mutation in mutations:
            changed = [dict(run) for run in manual]
            changed[0].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(BenchmarkError):
                build_blind_bundle(changed, cases(), SEED)


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

    @staticmethod
    def _replace_with_same_bytes(path: Path) -> None:
        original = path.stat()
        replacement = path.with_name(f".{path.name}.replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(stat.S_IMODE(original.st_mode))
        os.replace(replacement, path)

    @staticmethod
    def _sandbox_probe(manifest: dict, **overrides) -> dict:
        binding = {
            "schema_version": 1,
            "probe_version": 1,
            "adapter_command_sha256": "a" * 64,
            "probe_program_sha256": "b" * 64,
            "policy_sha256": ["c" * 64],
            "snapshot_sha256": manifest["input_snapshot"]["sha256"],
        }
        binding.update(overrides)
        return {**binding, "sha256": sha256_bytes(canonical_bytes(binding))}

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

    def _bind_dirty_suite(
        self,
        diff_artifact: Path,
        *,
        canonicalize: bool = True,
    ) -> None:
        if canonicalize:
            diff_artifact = diff_artifact.resolve(strict=True)
        dataset_manifest = build_dataset_manifest(
            self.dataset,
            suite_commit="abc123",
            suite_dirty=True,
            snapshot_id="fixture-dirty-snapshot",
            diff_artifact=diff_artifact,
        )
        atomic_write_json(self.dataset / "dataset-manifest.json", dataset_manifest)
        run_manifest_path = self.evidence / "run-manifest.json"
        run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        run_manifest["dataset"] = {
            "dataset_sha256": dataset_manifest["dataset_sha256"],
            "manifest_sha256": sha256_bytes(canonical_bytes(dataset_manifest)),
        }
        run_manifest["suite"] = {
            "commit": dataset_manifest["suite_commit"],
            "dirty": True,
            "snapshot_id": dataset_manifest["suite"]["snapshot_id"],
            "diff_sha256": dataset_manifest["suite"]["diff_sha256"],
        }
        atomic_write_json(run_manifest_path, run_manifest)

    def _dirty_diff(self, name: str, *, inside_dataset: bool = False) -> Path:
        parent = self.dataset if inside_dataset else self.root / f"diff-parent-{name}"
        parent.mkdir(exist_ok=True)
        artifact = parent / f"{name}.diff"
        artifact.write_text("diff --git a/input b/input\n+fixture\n", encoding="utf-8")
        self._bind_dirty_suite(artifact)
        return artifact

    def _set_serialized_diff_path(self, path_text: str) -> bytes:
        dataset_manifest_path = self.dataset / "dataset-manifest.json"
        dataset_manifest = json.loads(dataset_manifest_path.read_text(encoding="utf-8"))
        dataset_manifest["suite"]["diff_artifact"] = path_text
        encoded = canonical_bytes(dataset_manifest)
        dataset_manifest_path.write_bytes(encoded)
        run_manifest_path = self.evidence / "run-manifest.json"
        run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        run_manifest["dataset"]["manifest_sha256"] = sha256_bytes(encoded)
        atomic_write_json(run_manifest_path, run_manifest)
        return encoded

    def _set_raw_dataset_manifest_string(
        self,
        field: str,
        raw_json_string: bytes,
        *,
        section: str | None = None,
    ) -> bytes:
        return self._set_raw_dataset_manifest_value(
            field,
            raw_json_string,
            section=section,
        )

    @staticmethod
    def _replace_json_field(
        encoded: bytes,
        field: str,
        raw_json_value: bytes,
        *,
        section: str | None = None,
    ) -> bytes:
        value = json.loads(encoded.decode("utf-8"))
        container = value if section is None else value[section]
        placeholder = "__RAW_MANIFEST_VALUE__"
        container[field] = placeholder
        return canonical_bytes(value).replace(
            canonical_bytes(placeholder).rstrip(b"\n"),
            raw_json_value,
            1,
        )

    def _set_raw_dataset_manifest_value(
        self,
        field: str,
        raw_json_value: bytes,
        *,
        section: str | None = None,
    ) -> bytes:
        dataset_manifest_path = self.dataset / "dataset-manifest.json"
        encoded = self._replace_json_field(
            dataset_manifest_path.read_bytes(),
            field,
            raw_json_value,
            section=section,
        )
        dataset_manifest_path.write_bytes(encoded)
        run_manifest_path = self.evidence / "run-manifest.json"
        run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        run_manifest["dataset"]["manifest_sha256"] = sha256_bytes(encoded)
        atomic_write_json(run_manifest_path, run_manifest)
        return encoded

    def _nul_diff_paths(self) -> tuple[tuple[str, str], ...]:
        outer = self.root.resolve() / "nul-outer"
        parent = outer / "middle" / "parent"
        parent.mkdir(parents=True)
        artifact = parent / "suite.diff"
        artifact.write_text("diff --git a/input b/input\n+fixture\n", encoding="utf-8")
        self._bind_dirty_suite(artifact)
        parts = str(artifact).split(os.sep)
        positions = (
            ("first", -4),
            ("middle", -3),
            ("direct parent", -2),
            ("endpoint", -1),
        )
        malformed: list[tuple[str, str]] = []
        for label, index in positions:
            changed = list(parts)
            changed[index] = f"{changed[index]}\x00invalid"
            malformed.append((label, os.sep.join(changed)))
        return tuple(malformed)

    def cli_args(self, review: Path, key: Path) -> list[str]:
        return [
            "--dataset", str(self.dataset),
            "--evidence", str(self.evidence),
            "--seed", str(SEED),
            "--review-bundle", str(review),
            "--condition-key", str(key),
        ]

    def run_cli(
        self,
        review: Path,
        key: Path,
        *,
        umask: int | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.benchmark.blind",
                *self.cli_args(review, key),
            ],
            cwd=Path(__file__).parents[1],
            text=True,
            capture_output=True,
            check=False,
            preexec_fn=(None if umask is None else lambda: os.umask(umask)),
            timeout=timeout,
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

        condition_key = json.loads(key.read_text(encoding="utf-8"))
        run_manifest = json.loads(
            (self.evidence / "run-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            condition_key["review_bundle"]["sha256"],
            sha256_bytes(review.read_bytes()),
        )
        self.assertEqual(
            condition_key["provenance"]["prepared"]["run_manifest"],
            run_manifest,
        )
        self.assertEqual(
            condition_key["provenance"]["prepared"]["evidence"],
            str(self.evidence.resolve()),
        )
        self.assertEqual(set(condition_key["items"]), {
            item["id"] for item in json.loads(review.read_text(encoding="utf-8"))["items"]
        })

    def test_cli_rejects_incomplete_or_mismatched_prepared_provenance(self):
        """Break: evidence from another frozen experiment could be paired with this dataset."""
        manifest_path = self.evidence / "run-manifest.json"
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutations = {
            "dataset hash": lambda value: value["dataset"].update(
                dataset_sha256="0" * 64
            ),
            "dataset manifest hash": lambda value: value["dataset"].update(
                manifest_sha256="1" * 64
            ),
            "suite commit": lambda value: value["suite"].update(commit="wrong-commit"),
            "runner config": lambda value: value.update(runner_config_sha256="wrong"),
            "execution config": lambda value: value.update(execution_config_sha256="wrong"),
            "boolean schema version": lambda value: value.update(schema_version=True),
            "boolean schedule seed": lambda value: value.update(schedule_seed=True),
            "boolean bootstrap seed": lambda value: value.update(bootstrap_seed=True),
            "schedule seed": lambda value: value.update(schedule_seed=None),
            "bootstrap seed": lambda value: value.update(bootstrap_seed=None),
            "evidence path": lambda value: value.update(evidence="/wrong/evidence/path"),
            "snapshot": lambda value: value.pop("input_snapshot"),
            "null sandbox probe": lambda value: value.update(sandbox_probe=None),
            "boolean probe schema": lambda value: value.update(
                sandbox_probe=self._sandbox_probe(value, schema_version=True)
            ),
            "boolean probe version": lambda value: value.update(
                sandbox_probe=self._sandbox_probe(value, probe_version=True)
            ),
            "incomplete sandbox probe": lambda value: value.update(
                sandbox_probe={"sha256": "0" * 64}
            ),
            "unknown field": lambda value: value.update(unexpected="private"),
        }
        for index, (name, mutate) in enumerate(mutations.items()):
            changed = copy.deepcopy(original)
            mutate(changed)
            atomic_write_json(manifest_path, changed)
            public_dir = self.root / f"public-provenance-{index}"
            private_dir = self.root / f"private-provenance-{index}"
            public_dir.mkdir()
            private_dir.mkdir()
            review = public_dir / "bundle.json"
            key = private_dir / "key.json"
            with self.subTest(name=name):
                completed = self.run_cli(review, key)
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertFalse(review.exists())
                self.assertFalse(key.exists())
        atomic_write_json(manifest_path, original)

    def test_cli_accepts_a_complete_optional_sandbox_probe(self):
        """Break: exact optional validation must preserve a complete prepared probe object."""
        manifest_path = self.evidence / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sandbox_probe"] = self._sandbox_probe(manifest)
        atomic_write_json(manifest_path, manifest)
        public_dir = self.root / "public-probe"
        private_dir = self.root / "private-probe"
        public_dir.mkdir()
        private_dir.mkdir()

        completed = self.run_cli(
            public_dir / "bundle.json", private_dir / "key.json"
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_cli_rejects_non_content_addressed_raw_path(self):
        """Break: a valid hash must not authorize arbitrary evidence paths."""
        records = (self.evidence / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        first = json.loads(records[0])
        first["raw_output_path"] = "raw/renamed.txt"
        source = self.evidence / f"raw/{first['output_sha256']}.txt"
        (self.evidence / "raw/renamed.txt").write_bytes(source.read_bytes())
        records[0] = canonical_bytes(first).decode("utf-8").rstrip("\n")
        (self.evidence / "runs.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")
        public_dir = self.root / "public-path"
        private_dir = self.root / "private-path"
        public_dir.mkdir()
        private_dir.mkdir()

        completed = self.run_cli(public_dir / "bundle.json", private_dir / "key.json")

        self.assertEqual(completed.returncode, 2)
        self.assertFalse((public_dir / "bundle.json").exists())
        self.assertFalse((private_dir / "key.json").exists())

    def test_cli_malformed_container_ids_fail_as_benchmark_errors(self):
        """Break: list-valued record IDs must not escape the CLI as raw TypeError tracebacks."""
        records_path = self.evidence / "runs.jsonl"
        original = records_path.read_bytes()
        for index, field in enumerate(("run_id", "case_id")):
            records = original.decode("utf-8").splitlines()
            first = json.loads(records[0])
            first[field] = [first[field]]
            records[0] = canonical_bytes(first).decode("utf-8").rstrip("\n")
            records_path.write_text("\n".join(records) + "\n", encoding="utf-8")
            public_dir = self.root / f"public-malformed-{index}"
            private_dir = self.root / f"private-malformed-{index}"
            public_dir.mkdir()
            private_dir.mkdir()

            with self.subTest(field=field):
                completed = self.run_cli(
                    public_dir / "bundle.json", private_dir / "key.json"
                )
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertFalse((public_dir / "bundle.json").exists())
                self.assertFalse((private_dir / "key.json").exists())
        records_path.write_bytes(original)

    def test_cli_rolls_back_if_runs_change_during_publication(self):
        """Break: a post-load runs.jsonl mutation must not leave attributable artifacts."""
        public_dir = self.root / "public-runs-race"
        private_dir = self.root / "private-runs-race"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "key.json"
        runs_path = self.evidence / "runs.jsonl"
        original_publish = blind._atomic_create_pair

        def mutate_runs_then_publish(*args, **kwargs):
            runs_path.write_bytes(runs_path.read_bytes() + b"\n")
            return original_publish(*args, **kwargs)

        stdout = mock.Mock(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(blind, "_atomic_create_pair", mutate_runs_then_publish),
            mock.patch.object(blind.sys, "stdout", stdout),
            mock.patch.object(blind.sys, "stderr", stderr),
        ):
            result = blind.main(self.cli_args(review, key))

        self.assertEqual(result, 2)
        self.assertFalse(review.exists())
        self.assertFalse(key.exists())

    def test_cli_rolls_back_if_consumed_raw_output_changes_during_publication(self):
        """Break: a post-load raw-output mutation must invalidate both blinded artifacts."""
        public_dir = self.root / "public-raw-race"
        private_dir = self.root / "private-raw-race"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "key.json"
        first = json.loads(
            (self.evidence / "runs.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        raw_path = self.evidence / first["raw_output_path"]
        original_publish = blind._atomic_create_pair

        def mutate_raw_then_publish(*args, **kwargs):
            raw_path.write_text("tampered after load", encoding="utf-8")
            return original_publish(*args, **kwargs)

        stdout = mock.Mock(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(blind, "_atomic_create_pair", mutate_raw_then_publish),
            mock.patch.object(blind.sys, "stdout", stdout),
            mock.patch.object(blind.sys, "stderr", stderr),
        ):
            result = blind.main(self.cli_args(review, key))

        self.assertEqual(result, 2)
        self.assertFalse(review.exists())
        self.assertFalse(key.exists())

    def test_cli_rolls_back_same_byte_replacements_of_every_loaded_input_family(self):
        """Break: inode swaps of equal bytes must invalidate manifest, dataset, and snapshot inputs."""
        targets = (
            self.evidence / "run-manifest.json",
            self.dataset / "dataset-manifest.json",
            self.dataset / "cases.jsonl",
            self.dataset / "seeded-errors.json",
            self.dataset / "reference-signoff.json",
            self.dataset / "rubric.md",
            self.evidence / "input-snapshot" / "skills"
            / "translating-products" / "SKILL.md",
            self.evidence / "input-snapshot" / ".translation"
            / "project-brief.md",
            self.evidence / "input-snapshot" / ".translation"
            / "setup-approval.json",
        )
        original_publish = blind._atomic_create_pair
        for index, target in enumerate(targets):
            public_dir = self.root / f"public-input-swap-{index}"
            private_dir = self.root / f"private-input-swap-{index}"
            public_dir.mkdir()
            private_dir.mkdir()
            review = public_dir / "bundle.json"
            key = private_dir / "key.json"

            def replace_then_publish(*args, _target=target, **kwargs):
                self._replace_with_same_bytes(_target)
                return original_publish(*args, **kwargs)

            stdout = mock.Mock(buffer=io.BytesIO())
            stderr = io.StringIO()
            with (
                self.subTest(target=target.relative_to(self.root)),
                mock.patch.object(blind, "_atomic_create_pair", replace_then_publish),
                mock.patch.object(blind.sys, "stdout", stdout),
                mock.patch.object(blind.sys, "stderr", stderr),
            ):
                result = blind.main(self.cli_args(review, key))
                self.assertEqual(result, 2)
                self.assertFalse(review.exists())
                self.assertFalse(key.exists())

    def test_cli_accepts_bound_dirty_diff_paths_inside_and_outside_input_roots(self):
        """Break: dirty provenance binding must support valid in-root and external files."""
        for index, inside_dataset in enumerate((False, True)):
            self._dirty_diff(f"valid-{index}", inside_dataset=inside_dataset)
            public_dir = self.root / f"public-dirty-valid-{index}"
            private_dir = self.root / f"private-dirty-valid-{index}"
            public_dir.mkdir()
            private_dir.mkdir()

            completed = self.run_cli(
                public_dir / "bundle.json", private_dir / "key.json"
            )

            with self.subTest(inside_dataset=inside_dataset):
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_direct_diff_api_rejects_nul_in_every_component_without_leaking_descriptors(self):
        """Break: malformed path text must not escape or retain an anchored descriptor."""
        review = self.root / "nul-direct-review.json"
        key = self.root / "nul-direct-key.json"
        for label, path_text in self._nul_diff_paths():
            manifest_bytes = self._set_serialized_diff_path(path_text)
            before_fds = len(os.listdir("/dev/fd"))
            with self.subTest(component=label), self.assertRaises(BenchmarkError):
                blind._open_held_diff_artifact(manifest_bytes)
            self.assertEqual(len(os.listdir("/dev/fd")), before_fds)
            self.assertFalse(review.exists())
            self.assertFalse(key.exists())

    def test_direct_diff_api_converts_lone_surrogates_in_any_manifest_string(self):
        """Break: canonical re-encoding failures must remain benchmark input errors."""
        cases = (
            ("high diff path", "diff_artifact", "suite", br'"/invalid/\ud800/suite.diff"'),
            ("low diff path", "diff_artifact", "suite", br'"/invalid/\udc00/suite.diff"'),
            ("high adjacent", "snapshot_id", "suite", br'"snapshot-\ud800"'),
            ("low adjacent", "snapshot_id", "suite", br'"snapshot-\udc00"'),
        )
        for index, (label, field, section, raw_value) in enumerate(cases):
            self._dirty_diff(f"direct-surrogate-{index}")
            manifest_bytes = self._set_raw_dataset_manifest_string(
                field,
                raw_value,
                section=section,
            )
            before_fds = len(os.listdir("/dev/fd"))

            with self.subTest(case=label), self.assertRaises(BenchmarkError):
                blind._open_held_diff_artifact(manifest_bytes)

            self.assertEqual(len(os.listdir("/dev/fd")), before_fds)

    def test_module_cli_converts_lone_surrogates_without_traceback_or_outputs(self):
        """Break: non-encodable manifest strings must be one status-2 diagnostic."""
        cases = (
            ("high diff path", "diff_artifact", "suite", br'"/invalid/\ud800/suite.diff"'),
            ("low diff path", "diff_artifact", "suite", br'"/invalid/\udc00/suite.diff"'),
            ("high adjacent", "snapshot_id", "suite", br'"snapshot-\ud800"'),
            ("low adjacent", "snapshot_id", "suite", br'"snapshot-\udc00"'),
        )
        for index, (label, field, section, raw_value) in enumerate(cases):
            self._dirty_diff(f"cli-surrogate-{index}")
            self._set_raw_dataset_manifest_string(
                field,
                raw_value,
                section=section,
            )
            public_dir = self.root / f"public-surrogate-{index}"
            private_dir = self.root / f"private-surrogate-{index}"
            public_dir.mkdir()
            private_dir.mkdir()
            review = public_dir / "bundle.json"
            key = private_dir / "key.json"

            completed = self.run_cli(review, key, timeout=2)

            with self.subTest(case=label):
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr.count("error: "), 1, completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertFalse(review.exists())
                self.assertFalse(key.exists())

    def test_non_bmp_and_ordinary_unicode_diff_path_remains_accepted(self):
        """Break: rejecting lone surrogates must not reject valid Unicode scalars."""
        artifact = self._dirty_diff("valid-😀-café")
        manifest_bytes = (self.dataset / "dataset-manifest.json").read_bytes()
        before_fds = len(os.listdir("/dev/fd"))

        held = blind._open_held_diff_artifact(manifest_bytes)

        self.assertIsNotNone(held)
        self.assertEqual(held.path, artifact.resolve())
        held.close()
        self.assertEqual(len(os.listdir("/dev/fd")), before_fds)

        public_dir = self.root / "public-valid-unicode"
        private_dir = self.root / "private-valid-unicode"
        public_dir.mkdir()
        private_dir.mkdir()
        completed = self.run_cli(
            public_dir / "bundle.json",
            private_dir / "key.json",
            timeout=2,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_canonical_json_parsers_convert_integer_and_recursion_resource_failures(self):
        """Break: valid JSON resource failures must remain benchmark input errors."""
        malformed = (
            ("oversized positive", b"9" * 5000),
            ("oversized negative", b"-" + b"9" * 5000),
            ("deep array", b"[" * 2000 + b"0" + b"]" * 2000),
            ("deep object", b'{"nested":' * 1200 + b"0" + b"}" * 1200),
        )
        for label, raw_value in malformed:
            json_bytes = b'{"value":' + raw_value + b"}\n"
            jsonl_bytes = b'{"value":' + raw_value + b"}\n"
            before_fds = len(os.listdir("/dev/fd"))
            with self.subTest(parser="JSON", case=label), self.assertRaises(BenchmarkError):
                blind._parse_canonical_json(json_bytes, "test JSON")
            self.assertEqual(len(os.listdir("/dev/fd")), before_fds)
            with self.subTest(parser="JSONL", case=label), self.assertRaises(BenchmarkError):
                blind._parse_canonical_jsonl(jsonl_bytes, "test JSONL")
            self.assertEqual(len(os.listdir("/dev/fd")), before_fds)

    def test_canonical_json_parsers_preserve_ordinary_boundary_integers(self):
        """Break: resource limits must not change exact canonical bounded numbers."""
        encoded = (
            b'{"maximum":9223372036854775807,'
            b'"minimum":-9223372036854775808}\n'
        )
        expected = {
            "maximum": 9223372036854775807,
            "minimum": -9223372036854775808,
        }

        self.assertEqual(blind._parse_canonical_json(encoded, "test JSON"), expected)
        self.assertEqual(blind._parse_canonical_jsonl(encoded, "test JSONL"), [expected])

    def test_module_cli_converts_json_resource_failures_for_every_input_format(self):
        """Break: dataset, run-manifest, and JSONL failures must be status-2 diagnostics."""
        dataset_path = self.dataset / "dataset-manifest.json"
        run_manifest_path = self.evidence / "run-manifest.json"
        runs_path = self.evidence / "runs.jsonl"
        originals = {
            dataset_path: dataset_path.read_bytes(),
            run_manifest_path: run_manifest_path.read_bytes(),
            runs_path: runs_path.read_bytes(),
        }
        malformed = (
            ("oversized positive", b"9" * 5000),
            ("oversized negative", b"-" + b"9" * 5000),
            ("deep array", b"[" * 2000 + b"0" + b"]" * 2000),
            ("deep object", b'{"nested":' * 1200 + b"0" + b"}" * 1200),
        )
        resources = ("dataset manifest", "run manifest", "runs.jsonl")
        for resource_index, resource in enumerate(resources):
            for case_index, (label, raw_value) in enumerate(malformed):
                for path, encoded in originals.items():
                    path.write_bytes(encoded)
                if resource == "dataset manifest":
                    self._set_raw_dataset_manifest_value("schema_version", raw_value)
                elif resource == "run manifest":
                    run_manifest_path.write_bytes(self._replace_json_field(
                        run_manifest_path.read_bytes(),
                        "schema_version",
                        raw_value,
                    ))
                else:
                    lines = runs_path.read_bytes().splitlines(keepends=True)
                    lines[0] = self._replace_json_field(
                        lines[0],
                        "schema_version",
                        raw_value,
                    )
                    runs_path.write_bytes(b"".join(lines))
                public_dir = self.root / f"public-json-{resource_index}-{case_index}"
                private_dir = self.root / f"private-json-{resource_index}-{case_index}"
                public_dir.mkdir()
                private_dir.mkdir()
                review = public_dir / "bundle.json"
                key = private_dir / "key.json"
                before_fds = len(os.listdir("/dev/fd"))

                completed = self.run_cli(review, key, timeout=2)

                with self.subTest(resource=resource, case=label):
                    self.assertEqual(completed.returncode, 2, completed.stdout)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr.count("error: "), 1, completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertFalse(review.exists())
                    self.assertFalse(key.exists())
                    self.assertEqual(len(os.listdir("/dev/fd")), before_fds)

    def test_nul_path_validation_happens_before_the_anchor_is_opened(self):
        """Break: cleanup must not substitute for validating every component up front."""
        for label, path_text in self._nul_diff_paths():
            manifest_bytes = self._set_serialized_diff_path(path_text)
            with (
                self.subTest(component=label),
                mock.patch.object(
                    blind.os,
                    "open",
                    side_effect=AssertionError("malformed path reached os.open"),
                ),
                self.assertRaises(BenchmarkError),
            ):
                blind._open_held_diff_artifact(manifest_bytes)

    def test_module_cli_rejects_nul_in_every_component_without_traceback_or_outputs(self):
        """Break: malformed serialized paths must be ordinary status-2 CLI diagnostics."""
        for index, (label, path_text) in enumerate(self._nul_diff_paths()):
            self._set_serialized_diff_path(path_text)
            public_dir = self.root / f"public-nul-{index}"
            private_dir = self.root / f"private-nul-{index}"
            public_dir.mkdir()
            private_dir.mkdir()
            review = public_dir / "bundle.json"
            key = private_dir / "key.json"

            completed = self.run_cli(review, key, timeout=2)

            with self.subTest(component=label):
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertEqual(completed.stdout, "")
                self.assertTrue(completed.stderr.startswith("error: "), completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertFalse(review.exists())
                self.assertFalse(key.exists())

    def test_external_diff_walk_closes_descriptors_in_reverse_on_unexpected_failure(self):
        """Break: every pre-transfer traversal exception must release the anchored chain."""
        artifact = self._dirty_diff("unexpected-walk-failure")
        manifest_bytes = (self.dataset / "dataset-manifest.json").read_bytes()
        original_open = os.open
        original_close = os.close
        opened: list[int] = []
        closed: list[int] = []

        def fail_after_ancestors(path, flags, *args, **kwargs):
            if path == artifact.parent.name and kwargs.get("dir_fd") is not None:
                raise RuntimeError("injected traversal failure")
            descriptor = original_open(path, flags, *args, **kwargs)
            opened.append(descriptor)
            return descriptor

        def record_close(descriptor: int) -> None:
            closed.append(descriptor)
            original_close(descriptor)

        before_fds = len(os.listdir("/dev/fd"))
        with (
            mock.patch.object(blind.os, "open", fail_after_ancestors),
            mock.patch.object(blind.os, "close", record_close),
            self.assertRaisesRegex(RuntimeError, "injected traversal failure"),
        ):
            blind._open_held_diff_artifact(manifest_bytes)

        self.assertGreater(len(opened), 1)
        self.assertEqual(closed, list(reversed(opened)))
        self.assertEqual(len(os.listdir("/dev/fd")), before_fds)

    def test_cli_rejects_dirty_diff_symlink_and_parent_aliases(self):
        """Break: dirty provenance must not follow an artifact or parent-directory symlink."""
        real_parent = self.root / "real-diff-parent"
        real_parent.mkdir()
        real_diff = real_parent / "suite.diff"
        real_diff.write_text("diff --git a/input b/input\n+fixture\n", encoding="utf-8")
        artifact_alias = self.root / "artifact-alias.diff"
        os.symlink(real_diff, artifact_alias)
        parent_alias = self.root / "parent-alias"
        os.symlink(real_parent, parent_alias)
        for index, artifact in enumerate((artifact_alias, parent_alias / real_diff.name)):
            self._bind_dirty_suite(artifact, canonicalize=False)
            public_dir = self.root / f"public-dirty-alias-{index}"
            private_dir = self.root / f"private-dirty-alias-{index}"
            public_dir.mkdir()
            private_dir.mkdir()

            completed = self.run_cli(
                public_dir / "bundle.json", private_dir / "key.json"
            )

            with self.subTest(artifact=artifact):
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertFalse((public_dir / "bundle.json").exists())
                self.assertFalse((private_dir / "key.json").exists())

    def test_cli_rejects_symlinks_in_initial_external_diff_ancestor_chain(self):
        """Break: no intermediate ancestor may redirect the external diff traversal."""
        real_outer = self.root.resolve() / "initial-real-outer"
        real_middle = real_outer / "real-middle"
        real_parent = real_middle / "real-parent"
        real_parent.mkdir(parents=True)
        artifact = real_parent / "suite.diff"
        artifact.write_text("diff --git a/input b/input\n+fixture\n", encoding="utf-8")

        outer_alias = self.root.resolve() / "initial-outer-alias"
        os.symlink(real_outer, outer_alias)
        middle_alias = real_outer / "middle-alias"
        os.symlink(real_middle, middle_alias)
        paths = (
            outer_alias / "real-middle" / "real-parent" / artifact.name,
            real_outer / "middle-alias" / "real-parent" / artifact.name,
        )
        for index, diff_path in enumerate(paths):
            self._bind_dirty_suite(diff_path, canonicalize=False)
            public_dir = self.root / f"public-initial-ancestor-{index}"
            private_dir = self.root / f"private-initial-ancestor-{index}"
            public_dir.mkdir()
            private_dir.mkdir()

            completed = self.run_cli(
                public_dir / "bundle.json",
                private_dir / "key.json",
                timeout=2,
            )

            with self.subTest(component=index):
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertFalse((public_dir / "bundle.json").exists())
                self.assertFalse((private_dir / "key.json").exists())

    def test_cli_rolls_back_if_any_external_diff_ancestor_is_replaced_at_each_checkpoint(self):
        """Break: every held ancestor/name edge must survive every acceptance checkpoint."""
        original_publish = blind._atomic_create_pair
        original_validate = blind._validate_prepared_manifest
        original_read = blind._read_published
        for component_index in range(3):
            for phase_index, phase in enumerate(("prepublication", "postpublication", "final")):
                outer = self.root.resolve() / f"timing-{component_index}-{phase_index}-outer"
                middle = outer / "middle"
                parent = middle / "parent"
                parent.mkdir(parents=True)
                artifact = parent / "suite.diff"
                artifact.write_text(
                    "diff --git a/input b/input\n+fixture\n", encoding="utf-8"
                )
                self._bind_dirty_suite(artifact)
                components = (outer, middle, parent)
                component = components[component_index]
                displaced = component.with_name(f"{component.name}-displaced")
                public_dir = self.root / f"public-timing-{component_index}-{phase_index}"
                private_dir = self.root / f"private-timing-{component_index}-{phase_index}"
                public_dir.mkdir()
                private_dir.mkdir()
                review = public_dir / "bundle.json"
                key = private_dir / "key.json"

                def replace_component() -> None:
                    component.rename(displaced)
                    os.symlink(displaced, component)

                if phase == "prepublication":
                    validations = 0

                    def mutate_during_validation(*args, **kwargs):
                        nonlocal validations
                        result = original_validate(*args, **kwargs)
                        validations += 1
                        if validations == 2:
                            replace_component()
                        return result

                    phase_patch = mock.patch.object(
                        blind, "_validate_prepared_manifest", mutate_during_validation
                    )
                elif phase == "postpublication":
                    def mutate_after_publication(*args, **kwargs):
                        original_publish(*args, **kwargs)
                        replace_component()

                    phase_patch = mock.patch.object(
                        blind, "_atomic_create_pair", mutate_after_publication
                    )
                else:
                    reads = 0

                    def mutate_at_final_acceptance(target):
                        nonlocal reads
                        encoded = original_read(target)
                        reads += 1
                        if reads == 4:
                            replace_component()
                        return encoded

                    phase_patch = mock.patch.object(
                        blind, "_read_published", mutate_at_final_acceptance
                    )

                stdout = mock.Mock(buffer=io.BytesIO())
                stderr = io.StringIO()
                before_fds = len(os.listdir("/dev/fd"))
                with (
                    self.subTest(component=component_index, phase=phase),
                    phase_patch,
                    mock.patch.object(blind.sys, "stdout", stdout),
                    mock.patch.object(blind.sys, "stderr", stderr),
                ):
                    result = blind.main(self.cli_args(review, key))
                    self.assertEqual(result, 2)
                    self.assertFalse(review.exists())
                    self.assertFalse(key.exists())
                    self.assertEqual(len(os.listdir("/dev/fd")), before_fds)

    def test_cli_rejects_non_regular_or_multiply_linked_external_diff_endpoints_promptly(self):
        """Break: endpoint inspection must not block and must accept only one-link regular files."""
        mutations: list[tuple[str, object]] = []

        def fifo(path: Path) -> None:
            path.unlink()
            os.mkfifo(path)

        def character_device(_path: Path) -> None:
            dataset_manifest_path = self.dataset / "dataset-manifest.json"
            dataset_manifest = json.loads(
                dataset_manifest_path.read_text(encoding="utf-8")
            )
            dataset_manifest["suite"]["diff_artifact"] = str(Path("/dev/null").resolve())
            dataset_manifest["suite"]["diff_sha256"] = sha256_bytes(b"")
            atomic_write_json(dataset_manifest_path, dataset_manifest)
            run_manifest_path = self.evidence / "run-manifest.json"
            run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
            run_manifest["dataset"]["manifest_sha256"] = sha256_bytes(
                canonical_bytes(dataset_manifest)
            )
            run_manifest["suite"]["diff_sha256"] = dataset_manifest["suite"][
                "diff_sha256"
            ]
            atomic_write_json(run_manifest_path, run_manifest)

        def directory(path: Path) -> None:
            path.unlink()
            path.mkdir()

        def symlink(path: Path) -> None:
            target = path.with_name(f"{path.name}.target")
            target.write_text("diff --git a/input b/input\n+fixture\n", encoding="utf-8")
            path.unlink()
            os.symlink(target, path)

        def extra_link(path: Path) -> None:
            os.link(path, path.with_name(f"{path.name}.extra-link"))

        mutations.extend((
            ("fifo", fifo),
            ("character device", character_device),
            ("directory", directory),
            ("symlink", symlink),
            ("multiple links", extra_link),
        ))
        for index, (name, mutate) in enumerate(mutations):
            artifact = self._dirty_diff(f"endpoint-{index}")
            mutate(artifact)
            public_dir = self.root / f"public-endpoint-{index}"
            private_dir = self.root / f"private-endpoint-{index}"
            public_dir.mkdir()
            private_dir.mkdir()
            started = time.monotonic()
            with self.subTest(endpoint=name):
                try:
                    completed = self.run_cli(
                        public_dir / "bundle.json",
                        private_dir / "key.json",
                        timeout=2,
                    )
                except subprocess.TimeoutExpired:
                    self.fail(f"{name} endpoint did not return within two seconds")
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertFalse((public_dir / "bundle.json").exists())
                self.assertFalse((private_dir / "key.json").exists())

    def test_cli_rolls_back_external_dirty_diff_identity_type_and_byte_mutations(self):
        """Break: every externally loaded diff attribute must stay bound through publication."""
        def replace_same_bytes(path: Path) -> None:
            self._replace_with_same_bytes(path)

        def delete(path: Path) -> None:
            path.unlink()

        def replace_with_directory(path: Path) -> None:
            path.unlink()
            path.mkdir()

        def add_link(path: Path) -> None:
            os.link(path, path.with_name(f"{path.name}.extra-link"))

        def change_mode(path: Path) -> None:
            path.chmod(0o600 if stat.S_IMODE(path.stat().st_mode) != 0o600 else 0o644)

        def change_timestamp(path: Path) -> None:
            before = path.stat()
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

        def replace_parent(path: Path) -> None:
            parent = path.parent
            displaced = parent.with_name(f"{parent.name}-displaced")
            encoded = path.read_bytes()
            parent.rename(displaced)
            parent.mkdir()
            (parent / path.name).write_bytes(encoded)

        mutations = (
            ("same-byte replacement", replace_same_bytes),
            ("deletion", delete),
            ("type change", replace_with_directory),
            ("link count", add_link),
            ("mode", change_mode),
            ("timestamps", change_timestamp),
            ("parent identity", replace_parent),
        )
        original_publish = blind._atomic_create_pair
        for index, (name, mutate) in enumerate(mutations):
            artifact = self._dirty_diff(f"mutation-{index}")
            public_dir = self.root / f"public-dirty-mutation-{index}"
            private_dir = self.root / f"private-dirty-mutation-{index}"
            public_dir.mkdir()
            private_dir.mkdir()
            review = public_dir / "bundle.json"
            key = private_dir / "key.json"

            def mutate_then_publish(*args, _artifact=artifact, _mutate=mutate, **kwargs):
                _mutate(_artifact)
                return original_publish(*args, **kwargs)

            stdout = mock.Mock(buffer=io.BytesIO())
            stderr = io.StringIO()
            with (
                self.subTest(name=name),
                mock.patch.object(blind, "_atomic_create_pair", mutate_then_publish),
                mock.patch.object(blind.sys, "stdout", stdout),
                mock.patch.object(blind.sys, "stderr", stderr),
            ):
                result = blind.main(self.cli_args(review, key))
                self.assertEqual(result, 2)
                self.assertFalse(review.exists())
                self.assertFalse(key.exists())

    def test_cli_checks_external_dirty_diff_before_after_and_at_final_acceptance(self):
        """Break: a same-byte swap at any acceptance checkpoint must fail closed."""
        for index, phase in enumerate(("prepublication", "postpublication", "final")):
            artifact = self._dirty_diff(f"checkpoint-{index}")
            public_dir = self.root / f"public-dirty-checkpoint-{index}"
            private_dir = self.root / f"private-dirty-checkpoint-{index}"
            public_dir.mkdir()
            private_dir.mkdir()
            review = public_dir / "bundle.json"
            key = private_dir / "key.json"
            patches = []
            if phase == "prepublication":
                original_validate = blind._validate_prepared_manifest
                validations = 0

                def validate_then_replace(*args, **kwargs):
                    nonlocal validations
                    result = original_validate(*args, **kwargs)
                    validations += 1
                    if validations == 2:
                        self._replace_with_same_bytes(artifact)
                    return result

                patches.append(mock.patch.object(
                    blind, "_validate_prepared_manifest", validate_then_replace
                ))
            elif phase == "postpublication":
                original_publish = blind._atomic_create_pair

                def publish_then_replace(*args, **kwargs):
                    original_publish(*args, **kwargs)
                    self._replace_with_same_bytes(artifact)

                patches.append(mock.patch.object(
                    blind, "_atomic_create_pair", publish_then_replace
                ))
            else:
                original_read = blind._read_published
                reads = 0

                def read_then_replace(target):
                    nonlocal reads
                    encoded = original_read(target)
                    reads += 1
                    if reads == 4:
                        self._replace_with_same_bytes(artifact)
                    return encoded

                patches.append(mock.patch.object(
                    blind, "_read_published", read_then_replace
                ))

            stdout = mock.Mock(buffer=io.BytesIO())
            stderr = io.StringIO()
            with (
                self.subTest(phase=phase),
                patches[0],
                mock.patch.object(blind.sys, "stdout", stdout),
                mock.patch.object(blind.sys, "stderr", stderr),
            ):
                result = blind.main(self.cli_args(review, key))
                self.assertEqual(result, 2)
                self.assertFalse(review.exists())
                self.assertFalse(key.exists())

    def test_cli_rechecks_dataset_identity_after_published_artifact_reads(self):
        """Break: a final-acceptance inode swap must roll back already published artifacts."""
        public_dir = self.root / "public-final-input-swap"
        private_dir = self.root / "private-final-input-swap"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "key.json"
        cases_path = self.dataset / "cases.jsonl"
        original_read = blind._read_published
        reads = 0

        def read_then_replace(target):
            nonlocal reads
            encoded = original_read(target)
            reads += 1
            if reads == 1:
                self._replace_with_same_bytes(cases_path)
            return encoded

        stdout = mock.Mock(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(blind, "_read_published", read_then_replace),
            mock.patch.object(blind.sys, "stdout", stdout),
            mock.patch.object(blind.sys, "stderr", stderr),
        ):
            result = blind.main(self.cli_args(review, key))

        self.assertEqual(result, 2)
        self.assertFalse(review.exists())
        self.assertFalse(key.exists())

    def test_cli_rejects_snapshot_path_set_changes_during_publication(self):
        """Break: tree-ledger additions must invalidate the frozen snapshot file set."""
        public_dir = self.root / "public-snapshot-addition"
        private_dir = self.root / "private-snapshot-addition"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "key.json"
        added = self.evidence / "input-snapshot" / "skills" / "unexpected.txt"
        original_publish = blind._atomic_create_pair

        def add_snapshot_file_then_publish(*args, **kwargs):
            added.write_text("unexpected", encoding="utf-8")
            return original_publish(*args, **kwargs)

        stdout = mock.Mock(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(blind, "_atomic_create_pair", add_snapshot_file_then_publish),
            mock.patch.object(blind.sys, "stdout", stdout),
            mock.patch.object(blind.sys, "stderr", stderr),
        ):
            result = blind.main(self.cli_args(review, key))

        self.assertEqual(result, 2)
        self.assertFalse(review.exists())
        self.assertFalse(key.exists())

    def test_cli_rejects_an_added_private_key_hardlink(self):
        """Break: a second link could preserve or expose the private key after validation."""
        public_dir = self.root / "public-link-race"
        private_dir = self.root / "private-link-race"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "key.json"
        extra_link = private_dir / "extra-key-link.json"
        original_publish = blind._atomic_create_pair

        def publish_then_link(*args, **kwargs):
            original_publish(*args, **kwargs)
            os.link(key, extra_link)

        stdout = mock.Mock(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(blind, "_atomic_create_pair", publish_then_link),
            mock.patch.object(blind.sys, "stdout", stdout),
            mock.patch.object(blind.sys, "stderr", stderr),
        ):
            result = blind.main(self.cli_args(review, key))

        self.assertEqual(result, 2)
        self.assertFalse(review.exists())
        self.assertFalse(key.exists())
        self.assertTrue(extra_link.exists())

    def test_cli_reloads_and_rejects_a_replaced_private_key(self):
        """Break: success must not hash an attacker replacement without validating the key."""
        public_dir = self.root / "public-tamper"
        private_dir = self.root / "private-tamper"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "key.json"
        original_publish = blind._atomic_create_pair

        def publish_then_tamper(*args, **kwargs):
            original_publish(*args, **kwargs)
            key.write_text("{}\n", encoding="utf-8")
            key.chmod(0o644)

        stdout = mock.Mock(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(blind, "_atomic_create_pair", publish_then_tamper),
            mock.patch.object(blind.sys, "stdout", stdout),
            mock.patch.object(blind.sys, "stderr", stderr),
        ):
            result = blind.main(self.cli_args(review, key))

        self.assertEqual(result, 2)
        self.assertFalse(review.exists())
        self.assertFalse(key.exists())

    def test_cli_parent_swap_cannot_redirect_private_key_into_dataset(self):
        """Break: a validated directory pathname must not redirect descriptor-relative writes."""
        public_dir = self.root / "public-race"
        private_dir = self.root / "private-race"
        displaced = self.root / "private-race-displaced"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "key.json"
        original_publish = blind._atomic_create_pair

        def race_parent(*args, **kwargs):
            private_dir.rename(displaced)
            os.symlink(self.dataset, private_dir)
            return original_publish(*args, **kwargs)

        stdout = mock.Mock(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(blind, "_atomic_create_pair", race_parent),
            mock.patch.object(blind.sys, "stdout", stdout),
            mock.patch.object(blind.sys, "stderr", stderr),
        ):
            result = blind.main(self.cli_args(review, key))

        self.assertEqual(result, 2)
        self.assertFalse((self.dataset / "key.json").exists())
        self.assertFalse(review.exists())

    def test_cli_applies_exact_modes_under_restrictive_umask(self):
        """Break: a restrictive process umask must not erase public/private mode separation."""
        public_dir = self.root / "public-umask"
        private_dir = self.root / "private-umask"
        public_dir.mkdir()
        private_dir.mkdir()
        review = public_dir / "bundle.json"
        key = private_dir / "key.json"

        completed = self.run_cli(review, key, umask=0o077)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(stat.S_IMODE(review.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)

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
