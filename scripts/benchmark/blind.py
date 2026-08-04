from __future__ import annotations

import argparse
import base64
import binascii
import html
import json
import os
import random
import re
import stat
import sys
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from .common import (
    BenchmarkError,
    canonical_bytes,
    read_json,
    read_jsonl,
    sha256_bytes,
)
from .prepare import verify_dataset_manifest
from .run import RunResult, _snapshot_manifest, classify_failure
from .schema import PRIMARY_ATTEMPTS, SCHEMA_VERSION


_LABELS = "ABC"
_EXPECTED_UNIQUE = 180
_EXPECTED_REPEATS = 18
_EXPECTED_PRESENTATIONS = 198
_EXPECTED_TWO_OUTPUT = 135
_EXPECTED_THREE_OUTPUT = 45
_SHA256 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)
_RUN_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{20}(?![0-9a-f])", re.IGNORECASE)
_HEX_IDENTIFIER = re.compile(r"(?<![0-9a-f])[0-9a-f]{16,64}(?![0-9a-f])", re.IGNORECASE)
_UUID = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])",
    re.IGNORECASE,
)
_FROZEN_RUN_ID = re.compile(r"[0-9a-f]{20}")
_POSIX_ABSOLUTE = re.compile(r"(?<![:/\\\w])/(?:[^\s/]+(?:/|$))+", re.UNICODE)
_WINDOWS_ABSOLUTE = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|\\\\)[^\s]+")
_FORBIDDEN_KEY_PARTS = {
    "condition", "repeat", "runner", "model", "latency", "duration", "timing",
    "metric", "threshold", "reference", "seeded", "answer", "runid", "caseid",
    "outputsha", "hash", "digest", "path", "filepath", "suite", "attempt",
    "status", "failure", "stderr", "telemetry", "usage", "token", "argv", "shell",
    "prompt", "finding", "score", "automaticcheck", "projectfingerprint", "log",
    "runtime", "startedat", "completedat", "createdat", "updatedat", "elapsed",
    "timestamp", "tool", "generationorder", "scheduleorder", "runorder", "seed",
    "host", "agent", "execution", "bootstrap",
}
_FORBIDDEN_VALUE_WORDS = {
    "condition", "conditions", "context_only", "latency", "metric", "metrics",
    "model", "normal", "repeat_of", "runner", "seeded_error", "suite", "threshold",
}
_SUITE_NAMES = {
    "localizing-software", "reviewing-translations", "translating-android",
    "translating-app-stores", "translating-arabic", "translating-chinese",
    "translating-core", "translating-documentation", "translating-flutter",
    "translating-french", "translating-german", "translating-hebrew",
    "translating-ios", "translating-japanese", "translating-korean",
    "translating-marketing", "translating-mobile", "translating-portuguese",
    "translating-products", "translating-rtl", "translating-spanish", "translating-web",
}
_HIDDEN_THRESHOLD_VALUES = {0.05, 0.15, 0.25, 0.50, 0.60, 0.99}
_HIDDEN_THRESHOLD_TEXT = re.compile(
    r"(?<![\d.])(?:0?\.0?5|0?\.15|0?\.25|0?\.50?|0?\.60?|0?\.99|"
    r"5\s*%|15\s*%|25\s*%|50\s*%|60\s*%|99\s*%)(?![\d.])",
    re.IGNORECASE,
)
_RUN_RECORD_FIELDS = {field.name for field in fields(RunResult)} | {"schema_version", "output"}


def _anonymous_item_id(rng: random.Random, used: set[str]) -> str:
    while True:
        item_id = f"item-{rng.getrandbits(128):032x}"
        if item_id not in used:
            used.add(item_id)
            return item_id


def _conditions_for_case(case: Mapping[str, object]) -> list[str]:
    diagnostic = case.get("diagnostic")
    if type(diagnostic) is not bool:
        raise BenchmarkError(f"case {case.get('id')!r} diagnostic must be boolean")
    return ["normal", "suite"] + (["context_only"] if diagnostic else [])


def _reviewer_context(case: Mapping[str, object]) -> dict:
    return {
        "product": case.get("context"),
        "audience": case.get("audience"),
        "register": case.get("register"),
        "glossary": case.get("glossary"),
        "protected_terms": case.get("protected_terms"),
    }


def _case_map(cases: Sequence[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        raise BenchmarkError("cases must be a sequence")
    result: dict[str, Mapping[str, object]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            raise BenchmarkError("case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise BenchmarkError("case id must be non-empty text")
        if case_id in result:
            raise BenchmarkError(f"duplicate case id: {case_id}")
        for field in ("source", "context", "audience", "register"):
            if not isinstance(case.get(field), str):
                raise BenchmarkError(f"case {case_id} {field} must be text")
        if case.get("task") == "review":
            if not isinstance(case.get("candidate"), str):
                raise BenchmarkError(f"review case {case_id} candidate must be text")
        elif "candidate" in case:
            raise BenchmarkError(f"translation case {case_id} must not have candidate")
        if not isinstance(case.get("constraints"), Mapping):
            raise BenchmarkError(f"case {case_id} constraints must be an object")
        result[case_id] = case
    if len(result) != 60:
        raise BenchmarkError("blinding requires exactly 60 unique cases")
    if sum(case.get("diagnostic") is True for case in result.values()) != 15:
        raise BenchmarkError("blinding requires exactly 15 diagnostic cases")
    return result


def _require_type(value: object, expected: type, description: str) -> None:
    if type(value) is not expected:
        raise BenchmarkError(f"run {description} must be {expected.__name__}")


def _validate_run_record(run: Mapping[str, object]) -> None:
    if set(run) != _RUN_RECORD_FIELDS:
        missing = sorted(_RUN_RECORD_FIELDS - set(run))
        unknown = sorted(set(run) - _RUN_RECORD_FIELDS)
        raise BenchmarkError(f"run record fields mismatch: missing={missing!r}, unknown={unknown!r}")
    if type(run.get("schema_version")) is not int or run.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("run schema version mismatch")
    for name in (
        "run_id", "case_id", "condition", "runner_mode", "status", "failure_class",
        "started_at", "completed_at", "output_sha256", "raw_output_path", "stderr",
        "policy_integrity", "project_fingerprint", "output",
    ):
        _require_type(run.get(name), str, name)
    for name in (
        "process_started", "timed_out", "refused", "malformed_output", "tool_misuse",
        "redacted", "shell",
    ):
        _require_type(run.get(name), bool, name)
    _require_type(run.get("attempt"), int, "attempt")
    exit_code = run.get("exit_code")
    if exit_code is not None and type(exit_code) is not int:
        raise BenchmarkError("run exit_code must be an integer or null")
    for name in ("reason", "expected_policy_sha256", "applied_policy_sha256"):
        value = run.get(name)
        if value is not None and not isinstance(value, str):
            raise BenchmarkError(f"run {name} must be text or null")
    argv = run.get("argv")
    if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
        raise BenchmarkError("run argv must be a list of strings")
    try:
        canonical_bytes(run.get("telemetry"))
        canonical_bytes(run.get("usage"))
    except (TypeError, ValueError) as error:
        raise BenchmarkError(f"run telemetry/usage must be canonical JSON: {error}") from error
    run_id = str(run["run_id"])
    if _FROZEN_RUN_ID.fullmatch(run_id) is None:
        raise BenchmarkError(f"invalid frozen run id: {run_id!r}")
    if not run["started_at"] or not run["completed_at"] or not run["project_fingerprint"]:
        raise BenchmarkError(f"run {run_id!r} timestamps and fingerprint must be non-empty")
    if run["runner_mode"] not in {"fake", "cli", "manual"}:
        raise BenchmarkError(f"run {run_id!r} runner mode is invalid")
    if run["policy_integrity"] not in {"not_required", "verified", "failed"}:
        raise BenchmarkError(f"run {run_id!r} policy integrity is invalid")
    expected_policy = run.get("expected_policy_sha256")
    applied_policy = run.get("applied_policy_sha256")
    if run["runner_mode"] == "cli":
        if (
            run["policy_integrity"] != "verified"
            or _require_sha256(expected_policy, "run expected policy hash")
            != _require_sha256(applied_policy, "run applied policy hash")
        ):
            raise BenchmarkError(f"run {run_id!r} CLI policy integrity is not verified")
    elif (
        expected_policy is not None
        or applied_policy is not None
        or run["policy_integrity"] != "not_required"
    ):
        raise BenchmarkError(f"run {run_id!r} has unexpected policy binding")
    if run["shell"] is not False:
        raise BenchmarkError(f"run {run_id!r} must record shell=false")
    derived_failure = classify_failure(run)
    if derived_failure == "infrastructure":
        raise BenchmarkError(f"run {run_id!r} is an infrastructure failure")
    expected_status = "completed" if derived_failure == "success" else "model_outcome"
    if run["failure_class"] != derived_failure or run["status"] != expected_status:
        raise BenchmarkError(f"run {run_id!r} outcome classification is inconsistent")


def _run_map(
    runs: Sequence[Mapping[str, object]],
    cases: Mapping[str, Mapping[str, object]],
) -> dict[tuple[str, str, int], Mapping[str, object]]:
    if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
        raise BenchmarkError("runs must be a sequence")
    result: dict[tuple[str, str, int], Mapping[str, object]] = {}
    run_ids: set[str] = set()
    for run in runs:
        if not isinstance(run, Mapping):
            raise BenchmarkError("run must be an object")
        _validate_run_record(run)
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in run_ids:
            raise BenchmarkError(f"duplicate or invalid run id: {run_id!r}")
        run_ids.add(run_id)
        case_id = run.get("case_id")
        condition = run.get("condition")
        attempt = run.get("attempt")
        if not isinstance(case_id, str) or case_id not in cases:
            raise BenchmarkError(f"run has unknown case id: {case_id!r}")
        if condition not in _conditions_for_case(cases[str(case_id)]):
            raise BenchmarkError(
                f"run {run.get('run_id')!r} has invalid condition for case {case_id}"
            )
        if type(attempt) is not int or not 1 <= attempt <= PRIMARY_ATTEMPTS:
            raise BenchmarkError(f"run {run.get('run_id')!r} has invalid attempt")
        output = run.get("output")
        if not isinstance(output, str):
            raise BenchmarkError(f"run {run.get('run_id')!r} has no raw output text")
        output_digest = run.get("output_sha256")
        if (
            not isinstance(output_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", output_digest) is None
            or output_digest != sha256_bytes(output.encode("utf-8"))
        ):
            raise BenchmarkError(f"run {run_id!r} output hash mismatch")
        if run.get("raw_output_path") != f"raw/{output_digest}.txt":
            raise BenchmarkError(f"run {run_id!r} raw output is not content-addressed")
        identity = (str(case_id), str(condition), attempt)
        if identity in result:
            raise BenchmarkError(f"duplicate run identity: {identity!r}")
        result[identity] = run
    expected = {
        (case_id, condition, attempt)
        for case_id, case in cases.items()
        for condition in _conditions_for_case(case)
        for attempt in range(1, PRIMARY_ATTEMPTS + 1)
    }
    actual = set(result)
    if actual != expected:
        raise BenchmarkError(
            "incomplete run set: "
            f"missing={len(expected - actual)}, unknown={len(actual - expected)}"
        )
    if len(result) != 405:
        raise BenchmarkError("blinding requires exactly 405 complete runs")
    return result


def _shuffle_conditions(
    conditions: Sequence[str],
    rng: random.Random,
    *,
    different_from: Mapping[str, str] | None = None,
) -> list[str]:
    shuffled = list(conditions)
    while True:
        rng.shuffle(shuffled)
        if different_from is None:
            return shuffled
        previous = [different_from[label] for label in _LABELS[:len(shuffled)]]
        if shuffled != previous:
            return shuffled


def _make_item(
    case: Mapping[str, object],
    attempt: int,
    run_map: Mapping[tuple[str, str, int], Mapping[str, object]],
    rng: random.Random,
    used_ids: set[str],
    *,
    different_from: Mapping[str, str] | None = None,
) -> tuple[dict, dict]:
    conditions = _conditions_for_case(case)
    labels = _LABELS[:len(conditions)]
    shuffled = _shuffle_conditions(conditions, rng, different_from=different_from)
    case_id = str(case["id"])
    visible = {
        "id": _anonymous_item_id(rng, used_ids),
        "source": case["source"],
        "context": _reviewer_context(case),
        "constraints": case["constraints"],
        "outputs": {
            label: run_map[(case_id, condition, attempt)]["output"]
            for label, condition in zip(labels, shuffled)
        },
    }
    if "candidate" in case:
        visible["candidate"] = case["candidate"]
    hidden = {
        "case_id": case_id,
        "attempt": attempt,
        "labels": dict(zip(labels, shuffled)),
    }
    return visible, hidden


def _flatten_hidden_strings(value: object, *, excluded_keys: frozenset[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) not in excluded_keys:
                found.update(_flatten_hidden_strings(item, excluded_keys=excluded_keys))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found.update(_flatten_hidden_strings(item, excluded_keys=excluded_keys))
    elif isinstance(value, str) and len(value) >= 4:
        found.add(value)
    return found


def _hidden_values(
    cases: Sequence[Mapping[str, object]],
    runs: Sequence[Mapping[str, object]],
) -> set[str]:
    values = _flatten_hidden_strings(
        cases,
        excluded_keys=frozenset({
            "source", "candidate", "context", "audience", "register", "constraints",
            "glossary", "protected_terms",
        }),
    )
    values.update(_flatten_hidden_strings(runs, excluded_keys=frozenset({"output"})))
    return values
def build_blind_bundle(
    runs: Sequence[Mapping[str, object]],
    cases: Sequence[Mapping[str, object]],
    seed: int,
    *,
    prepared_provenance: Mapping[str, object] | None = None,
) -> tuple[dict, dict]:
    if type(seed) is not int:
        raise BenchmarkError("blinding seed must be an integer")
    case_by_id = _case_map(cases)
    runs_by_identity = _run_map(runs, case_by_id)
    rng = random.Random(seed)
    used_ids: set[str] = set()
    unique: list[tuple[dict, dict]] = []
    for case_id in sorted(case_by_id):
        for attempt in range(1, PRIMARY_ATTEMPTS + 1):
            unique.append(_make_item(
                case_by_id[case_id], attempt, runs_by_identity, rng, used_ids
            ))
    if len(unique) != _EXPECTED_UNIQUE:
        raise BenchmarkError("blinding must create exactly 180 unique items")
    if Counter(len(item[0]["outputs"]) for item in unique) != {
        2: _EXPECTED_TWO_OUTPUT,
        3: _EXPECTED_THREE_OUTPUT,
    }:
        raise BenchmarkError("blinding requires 135 two-output and 45 three-output unique items")

    repeat_indices = rng.sample(range(len(unique)), _EXPECTED_REPEATS)
    presentations = list(unique)
    for index in repeat_indices:
        original_visible, original_hidden = unique[index]
        repeat_visible, repeat_hidden = _make_item(
            case_by_id[original_hidden["case_id"]],
            original_hidden["attempt"],
            runs_by_identity,
            rng,
            used_ids,
            different_from=original_hidden["labels"],
        )
        repeat_hidden["repeat_of"] = original_visible["id"]
        presentations.append((repeat_visible, repeat_hidden))
    rng.shuffle(presentations)
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "items": [visible for visible, _ in presentations],
    }
    canonical_cases = [dict(case_by_id[case_id]) for case_id in sorted(case_by_id)]
    canonical_runs = [dict(run) for _, run in sorted(runs_by_identity.items())]
    provenance = {
        "blinding_seed": seed,
        "cases_sha256": sha256_bytes(canonical_bytes(canonical_cases)),
        "runs_sha256": sha256_bytes(canonical_bytes(canonical_runs)),
    }
    if prepared_provenance is not None:
        try:
            canonical_bytes(prepared_provenance)
        except (TypeError, ValueError) as error:
            raise BenchmarkError(f"prepared provenance must be canonical JSON: {error}") from error
        provenance["prepared"] = dict(prepared_provenance)
    item_ids = [visible["id"] for visible, _ in presentations]
    key = {
        "schema_version": SCHEMA_VERSION,
        "provenance": provenance,
        "review_bundle": {
            "sha256": sha256_bytes(canonical_bytes(bundle)),
            "item_ids_sha256": sha256_bytes(canonical_bytes(item_ids)),
        },
        "items": {visible["id"]: hidden for visible, hidden in presentations},
    }
    if (
        len(bundle["items"]) != _EXPECTED_PRESENTATIONS
        or len(key["items"]) != _EXPECTED_PRESENTATIONS
    ):
        raise BenchmarkError("blinding must create exactly 198 presentations")
    scan_visible_bundle(bundle, _hidden_values=_hidden_values(cases, runs))
    return bundle, key


def _compact_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _decoded_forms(value: str) -> set[str]:
    forms: set[str] = set()
    pending = [value]
    while pending and len(forms) < 16:
        current = pending.pop()
        if current in forms:
            continue
        forms.add(current)
        decoded = html.unescape(unquote(current))
        decoded = re.sub(
            r"\\u([0-9a-fA-F]{4})",
            lambda match: chr(int(match.group(1), 16)),
            decoded,
        )
        decoded = re.sub(
            r"\\x([0-9a-fA-F]{2})",
            lambda match: chr(int(match.group(1), 16)),
            decoded,
        )
        if decoded != current:
            pending.append(decoded)
        stripped = current.strip()
        if len(stripped) >= 8:
            try:
                decoded_bytes = base64.b64decode(stripped.encode("ascii"), validate=True)
                decoded_text = decoded_bytes.decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError, ValueError):
                pass
            else:
                if decoded_text.isprintable() or decoded_text.lstrip().startswith(("{", "[")):
                    pending.append(decoded_text)
    return {unicodedata.normalize("NFKC", form).casefold() for form in forms}


def _scan_metadata_string(value: str, location: str, hidden_values: frozenset[str]) -> None:
    for normalized in _decoded_forms(value):
        words = set(re.findall(r"[\w-]+", normalized, re.UNICODE))
        if words & _FORBIDDEN_VALUE_WORDS:
            raise BenchmarkError(f"visible bundle leaks hidden value at {location}")
        if any(name in normalized for name in _SUITE_NAMES):
            raise BenchmarkError(f"visible bundle leaks suite metadata at {location}")
        if (
            _SHA256.search(normalized)
            or _RUN_ID.search(normalized)
            or _HEX_IDENTIFIER.search(normalized)
            or _UUID.search(normalized)
        ):
            raise BenchmarkError(f"visible bundle leaks an identifier or hash at {location}")
        if _POSIX_ABSOLUTE.search(normalized) or _WINDOWS_ABSOLUTE.search(normalized):
            raise BenchmarkError(f"visible bundle leaks an absolute path at {location}")
        if _HIDDEN_THRESHOLD_TEXT.search(normalized):
            raise BenchmarkError(f"visible bundle leaks a benchmark threshold at {location}")
        for hidden in hidden_values:
            if hidden and hidden in normalized:
                raise BenchmarkError(f"visible bundle contains exact hidden data at {location}")
        stripped = normalized.strip()
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, (Mapping, list)):
            _scan_metadata_value(decoded, f"{location}<serialized>", hidden_values)
        elif isinstance(decoded, str) and decoded != value:
            _scan_metadata_string(decoded, f"{location}<serialized>", hidden_values)
        elif type(decoded) in (int, float):
            _scan_metadata_value(decoded, f"{location}<serialized>", hidden_values)


def _scan_metadata_value(value: object, location: str, hidden_values: frozenset[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BenchmarkError(f"visible metadata key must be text at {location}")
            compact = _compact_key(key)
            if any(part in compact for part in _FORBIDDEN_KEY_PARTS):
                raise BenchmarkError(f"visible bundle leaks hidden key {key!r} at {location}")
            _scan_metadata_string(key, f"{location}.<key>", hidden_values)
            _scan_metadata_value(item, f"{location}.{key}", hidden_values)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_metadata_value(item, f"{location}[{index}]", hidden_values)
    elif isinstance(value, str):
        _scan_metadata_string(value, location, hidden_values)
    elif type(value) in (int, float):
        if value in _HIDDEN_THRESHOLD_VALUES:
            raise BenchmarkError(f"visible bundle leaks a benchmark threshold at {location}")
    elif value is not None and type(value) is not bool:
        raise BenchmarkError(f"visible metadata has unsupported value at {location}")


def scan_visible_bundle(
    bundle: object,
    *,
    _hidden_values: Sequence[str] = (),
) -> None:
    if not isinstance(bundle, Mapping) or set(bundle) != {"schema_version", "items"}:
        raise BenchmarkError("visible bundle has invalid top-level fields")
    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("visible bundle schema version mismatch")
    items = bundle.get("items")
    if not isinstance(items, list) or len(items) != _EXPECTED_PRESENTATIONS:
        raise BenchmarkError("visible bundle must contain exactly 198 items")
    hidden_values = frozenset(
        unicodedata.normalize("NFKC", value).casefold()
        for value in _hidden_values
        if isinstance(value, str) and len(value) >= 4
    )
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise BenchmarkError(f"visible item {index} must be an object")
        required = {"id", "source", "context", "constraints", "outputs"}
        allowed = required | {"candidate"}
        if not required <= set(item) or not set(item) <= allowed:
            raise BenchmarkError(f"visible item {index} has invalid fields")
        item_id = item.get("id")
        if (
            not isinstance(item_id, str)
            or re.fullmatch(r"item-[0-9a-f]{32}", item_id) is None
            or item_id in seen
        ):
            raise BenchmarkError(f"visible item {index} has invalid anonymous id")
        seen.add(item_id)
        for literal_field in ("source", "candidate"):
            if literal_field in item and not isinstance(item[literal_field], str):
                raise BenchmarkError(f"visible item {item_id} {literal_field} must be text")
        outputs = item.get("outputs")
        if not isinstance(outputs, Mapping) or set(outputs) not in ({"A", "B"}, {"A", "B", "C"}):
            raise BenchmarkError(f"visible item {item_id} has invalid anonymous outputs")
        if not all(isinstance(output, str) for output in outputs.values()):
            raise BenchmarkError(f"visible item {item_id} outputs must be text")
        _scan_metadata_value(item["context"], f"items[{index}].context", hidden_values)
        _scan_metadata_value(item["constraints"], f"items[{index}].constraints", hidden_values)


def _require_sha256(value: object, description: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise BenchmarkError(f"{description} must be a SHA-256 digest")
    return value


def _validate_schedule_manifest(manifest: Mapping[str, object]) -> list[str]:
    schedule = manifest.get("schedule")
    if not isinstance(schedule, Mapping) or set(schedule) != {"run_ids", "sha256"}:
        raise BenchmarkError("run manifest has no schedule")
    run_ids = schedule.get("run_ids")
    if (
        not isinstance(run_ids, list)
        or not all(isinstance(run_id, str) and run_id for run_id in run_ids)
        or len(run_ids) != 405
        or len(run_ids) != len(set(run_ids))
        or any(_FROZEN_RUN_ID.fullmatch(run_id) is None for run_id in run_ids)
    ):
        raise BenchmarkError("run manifest schedule has invalid run ids")
    if schedule.get("sha256") != sha256_bytes(canonical_bytes(run_ids)):
        raise BenchmarkError("run manifest schedule hash mismatch")
    return run_ids


def _validate_prepared_manifest(dataset_dir: Path, evidence_dir: Path) -> tuple[dict, dict]:
    dataset_dir = Path(dataset_dir)
    evidence_dir = Path(evidence_dir)
    manifest_path = evidence_dir / "run-manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("run manifest schema version mismatch")
    required = {
        "schema_version", "dataset", "suite", "runner_config_sha256",
        "execution_config_sha256", "schedule_seed", "bootstrap_seed", "evidence",
        "schedule", "input_snapshot",
    }
    allowed = required | {"sandbox_probe"}
    if set(manifest) != required and set(manifest) != allowed:
        missing = sorted(required - set(manifest))
        unknown = sorted(set(manifest) - allowed)
        raise BenchmarkError(
            f"run manifest provenance fields mismatch: missing={missing!r}, unknown={unknown!r}"
        )
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise BenchmarkError(f"cannot read run manifest bytes: {error}") from error
    if manifest_bytes != canonical_bytes(manifest):
        raise BenchmarkError("run manifest must use canonical JSON bytes")

    dataset_manifest_path = dataset_dir / "dataset-manifest.json"
    dataset_manifest = read_json(dataset_manifest_path)
    if not isinstance(dataset_manifest, dict):
        raise BenchmarkError("dataset manifest must be an object")
    try:
        dataset_manifest_bytes = dataset_manifest_path.read_bytes()
    except OSError as error:
        raise BenchmarkError(f"cannot read dataset manifest bytes: {error}") from error
    if dataset_manifest_bytes != canonical_bytes(dataset_manifest):
        raise BenchmarkError("dataset manifest must use canonical JSON bytes")

    dataset_binding = manifest.get("dataset")
    if not isinstance(dataset_binding, Mapping) or set(dataset_binding) != {
        "dataset_sha256", "manifest_sha256",
    }:
        raise BenchmarkError("run manifest dataset binding is malformed")
    expected_dataset_hash = _require_sha256(
        dataset_manifest.get("dataset_sha256"), "dataset manifest dataset hash"
    )
    if dataset_binding.get("dataset_sha256") != expected_dataset_hash:
        raise BenchmarkError("run manifest dataset hash mismatch")
    expected_manifest_hash = sha256_bytes(dataset_manifest_bytes)
    if dataset_binding.get("manifest_sha256") != expected_manifest_hash:
        raise BenchmarkError("run manifest dataset-manifest hash mismatch")

    suite = manifest.get("suite")
    dataset_dirty = dataset_manifest.get("suite_dirty")
    if type(dataset_dirty) is not bool:
        raise BenchmarkError("dataset manifest suite dirty state is malformed")
    expected_suite = {
        "commit": dataset_manifest.get("suite_commit"),
        "dirty": dataset_dirty,
    }
    if dataset_dirty:
        dataset_suite = dataset_manifest.get("suite")
        if not isinstance(dataset_suite, Mapping):
            raise BenchmarkError("dirty dataset suite provenance is malformed")
        expected_suite.update({
            "snapshot_id": dataset_suite.get("snapshot_id"),
            "diff_sha256": dataset_suite.get("diff_sha256"),
        })
    if suite != expected_suite:
        raise BenchmarkError("run manifest suite provenance mismatch")
    if not isinstance(expected_suite["commit"], str) or not expected_suite["commit"]:
        raise BenchmarkError("run manifest suite commit is malformed")
    if dataset_dirty:
        _require_sha256(expected_suite.get("diff_sha256"), "suite diff hash")

    _require_sha256(manifest.get("runner_config_sha256"), "runner config hash")
    _require_sha256(manifest.get("execution_config_sha256"), "execution config hash")
    for name in ("schedule_seed", "bootstrap_seed"):
        if type(manifest.get(name)) is not int:
            raise BenchmarkError(f"run manifest {name} must be an integer")
    evidence_value = manifest.get("evidence")
    if not isinstance(evidence_value, str) or not evidence_value:
        raise BenchmarkError("run manifest evidence path is malformed")
    try:
        bound_evidence = Path(evidence_value).resolve(strict=True)
        actual_evidence = evidence_dir.resolve(strict=True)
    except OSError as error:
        raise BenchmarkError(f"cannot resolve run manifest evidence path: {error}") from error
    if bound_evidence != actual_evidence or evidence_value != str(actual_evidence):
        raise BenchmarkError("run manifest evidence path mismatch")

    _validate_schedule_manifest(manifest)
    input_snapshot = manifest.get("input_snapshot")
    if not isinstance(input_snapshot, dict):
        raise BenchmarkError("run manifest input snapshot is malformed")
    actual_snapshot = _snapshot_manifest(evidence_dir / "input-snapshot")
    if input_snapshot != actual_snapshot:
        raise BenchmarkError("run manifest input snapshot mismatch")
    sandbox_probe = manifest.get("sandbox_probe")
    if sandbox_probe is not None:
        probe_fields = {
            "schema_version", "probe_version", "adapter_command_sha256",
            "probe_program_sha256", "policy_sha256", "snapshot_sha256", "sha256",
        }
        if not isinstance(sandbox_probe, dict) or set(sandbox_probe) != probe_fields:
            raise BenchmarkError("run manifest sandbox probe is malformed")
        if (
            sandbox_probe.get("schema_version") != SCHEMA_VERSION
            or sandbox_probe.get("probe_version") != 1
        ):
            raise BenchmarkError("run manifest sandbox probe version mismatch")
        for name in (
            "adapter_command_sha256", "probe_program_sha256", "snapshot_sha256", "sha256",
        ):
            _require_sha256(sandbox_probe.get(name), f"sandbox probe {name}")
        policies = sandbox_probe.get("policy_sha256")
        if (
            not isinstance(policies, list)
            or any(_require_sha256(value, "sandbox probe policy hash") != value for value in policies)
            or policies != sorted(set(policies))
        ):
            raise BenchmarkError("run manifest sandbox probe policies are malformed")
        binding = {key: value for key, value in sandbox_probe.items() if key != "sha256"}
        if sandbox_probe["sha256"] != sha256_bytes(canonical_bytes(binding)):
            raise BenchmarkError("run manifest sandbox probe hash mismatch")
        if sandbox_probe.get("snapshot_sha256") != input_snapshot.get("sha256"):
            raise BenchmarkError("run manifest sandbox probe snapshot mismatch")

    canonical_evidence = str(actual_evidence)
    prepared = {
        "run_manifest": manifest,
        "run_manifest_sha256": sha256_bytes(manifest_bytes),
        "dataset_manifest_sha256": expected_manifest_hash,
        "dataset_sha256": expected_dataset_hash,
        "dataset": str(dataset_dir.resolve(strict=True)),
        "evidence": canonical_evidence,
    }
    return manifest, prepared


def _read_raw_output(evidence_dir: Path, run: RunResult) -> str:
    relative = run.raw_output_path
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise BenchmarkError(f"run {run.run_id} has invalid raw output path")
    raw_root = evidence_dir / "raw"
    if raw_root.is_symlink():
        raise BenchmarkError("refusing symlink raw evidence directory")
    path = evidence_dir / relative
    if path.is_symlink():
        raise BenchmarkError(f"refusing symlink raw output: {relative}")
    try:
        path.resolve().relative_to(raw_root.resolve())
        encoded = path.read_bytes()
    except (OSError, ValueError) as error:
        raise BenchmarkError(f"cannot safely read raw output {relative}: {error}") from error
    if sha256_bytes(encoded) != run.output_sha256:
        raise BenchmarkError(f"raw output hash mismatch: {relative}")
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BenchmarkError(f"raw output is not UTF-8: {relative}") from error


def _load_complete_runs(evidence_dir: Path, run_ids: Sequence[str]) -> list[dict]:
    evidence_dir = Path(evidence_dir)
    if evidence_dir.is_symlink():
        raise BenchmarkError("refusing symlink evidence directory")
    records = read_jsonl(evidence_dir / "runs.jsonl")
    by_id: dict[str, dict] = {}
    for record in records:
        run = RunResult.from_record(record)
        if run.run_id in by_id:
            raise BenchmarkError(f"duplicate run id: {run.run_id}")
        by_id[run.run_id] = {**record, "output": _read_raw_output(evidence_dir, run)}
    if set(by_id) != set(run_ids) or len(by_id) != len(run_ids):
        raise BenchmarkError("run evidence does not match the frozen schedule")
    return [by_id[run_id] for run_id in run_ids]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass
class _HeldTarget:
    path: Path
    parent: Path
    name: str
    directory_fd: int
    directory_identity: tuple[int, int]
    mode: int
    published_identity: tuple[int, int] | None = None

    def close(self) -> None:
        if self.directory_fd >= 0:
            os.close(self.directory_fd)
            self.directory_fd = -1


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _recheck_held_target(target: _HeldTarget) -> None:
    try:
        held = os.fstat(target.directory_fd)
        named = os.stat(target.parent, follow_symlinks=False)
    except OSError as error:
        raise BenchmarkError(f"output directory identity changed: {target.parent}: {error}") from error
    if (
        not stat.S_ISDIR(held.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or _identity(held) != target.directory_identity
        or _identity(named) != target.directory_identity
    ):
        raise BenchmarkError(f"output directory identity changed: {target.parent}")


def _open_held_target(
    path: Path,
    *,
    mode: int,
    input_roots: Sequence[Path],
) -> _HeldTarget:
    path = Path(path)
    if path.name in {"", ".", ".."} or path.exists() or path.is_symlink():
        raise BenchmarkError(f"output target must be nonexistent: {path}")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise BenchmarkError(f"cannot resolve output parent {path.parent}: {error}") from error
    resolved = parent / path.name
    if any(_inside(resolved, root) for root in input_roots):
        raise BenchmarkError("blinding outputs must be outside dataset and evidence inputs")
    try:
        descriptor = os.open(path.parent, _directory_flags())
    except OSError as error:
        raise BenchmarkError(f"cannot securely open output parent {path.parent}: {error}") from error
    held = _HeldTarget(
        path=resolved,
        parent=path.parent,
        name=path.name,
        directory_fd=descriptor,
        directory_identity=_identity(os.fstat(descriptor)),
        mode=mode,
    )
    try:
        _recheck_held_target(held)
        current_parent = path.parent.resolve(strict=True)
        if current_parent != parent:
            raise BenchmarkError(f"output parent changed while opening: {path.parent}")
        if any(_inside(current_parent / path.name, root) for root in input_roots):
            raise BenchmarkError("blinding outputs must be outside dataset and evidence inputs")
        try:
            os.stat(held.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BenchmarkError(f"output target must be nonexistent: {resolved}")
    except Exception:
        held.close()
        raise
    return held


def _validate_output_targets(
    review_bundle: Path,
    condition_key: Path,
    *,
    dataset_dir: Path,
    evidence_dir: Path,
) -> tuple[_HeldTarget, _HeldTarget]:
    try:
        input_roots = (
            Path(dataset_dir).resolve(strict=True),
            Path(evidence_dir).resolve(strict=True),
        )
    except OSError as error:
        raise BenchmarkError(f"cannot resolve blinding input roots: {error}") from error
    review = _open_held_target(Path(review_bundle), mode=0o644, input_roots=input_roots)
    try:
        key = _open_held_target(Path(condition_key), mode=0o600, input_roots=input_roots)
    except Exception:
        review.close()
        raise
    if review.directory_identity == key.directory_identity:
        review.close()
        key.close()
        raise BenchmarkError("review bundle and condition key require distinct directories")
    return review, key


def _unlink_relative(target: _HeldTarget, name: str) -> None:
    try:
        os.unlink(name, dir_fd=target.directory_fd)
    except FileNotFoundError:
        pass


def _rollback_targets(targets: Sequence[_HeldTarget]) -> None:
    for target in targets:
        _unlink_relative(target, target.name)
        try:
            os.fsync(target.directory_fd)
        except OSError:
            pass


def _atomic_create_pair(
    review_target: _HeldTarget,
    review: object,
    key_target: _HeldTarget,
    key: object,
) -> None:
    staged: list[tuple[_HeldTarget, str]] = []
    created: list[_HeldTarget] = []
    values = ((review_target, review), (key_target, key))
    try:
        for target, value in values:
            _recheck_held_target(target)
            temporary = f".{target.name}.tmp-{uuid4().hex}"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                target.mode,
                dir_fd=target.directory_fd,
            )
            os.fchmod(descriptor, target.mode)
            with os.fdopen(descriptor, "wb") as output:
                output.write(canonical_bytes(value))
                output.flush()
                os.fsync(output.fileno())
            staged.append((target, temporary))
        for target, temporary in staged:
            _recheck_held_target(target)
            os.link(
                temporary,
                target.name,
                src_dir_fd=target.directory_fd,
                dst_dir_fd=target.directory_fd,
                follow_symlinks=False,
            )
            created.append(target)
            published = os.stat(
                target.name, dir_fd=target.directory_fd, follow_symlinks=False
            )
            if not stat.S_ISREG(published.st_mode):
                raise BenchmarkError("published blinded artifact is not a regular file")
            target.published_identity = _identity(published)
        for target, temporary in staged:
            _unlink_relative(target, temporary)
        for target in (review_target, key_target):
            os.fsync(target.directory_fd)
            _recheck_held_target(target)
    except (OSError, BenchmarkError) as error:
        for target in created:
            _unlink_relative(target, target.name)
        for target, temporary in staged:
            _unlink_relative(target, temporary)
        if isinstance(error, BenchmarkError):
            raise
        raise BenchmarkError(f"cannot atomically publish blinded outputs: {error}") from error


def _read_published(target: _HeldTarget) -> bytes:
    _recheck_held_target(target)
    try:
        descriptor = os.open(
            target.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=target.directory_fd,
        )
    except OSError as error:
        raise BenchmarkError(f"cannot reopen published artifact {target.path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _identity(before) != target.published_identity
            or stat.S_IMODE(before.st_mode) != target.mode
        ):
            raise BenchmarkError(f"published artifact identity or mode changed: {target.path}")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
        if _identity(after) != _identity(before) or after.st_size != before.st_size:
            raise BenchmarkError(f"published artifact changed while reading: {target.path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _decode_canonical_artifact(encoded: bytes, description: str) -> object:
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"published {description} is invalid JSON: {error}") from error
    if canonical_bytes(value) != encoded:
        raise BenchmarkError(f"published {description} is not canonical JSON")
    return value


def _validate_condition_key(
    key: object,
    bundle: Mapping[str, object],
    *,
    expected_key: Mapping[str, object],
    prepared_provenance: Mapping[str, object],
) -> None:
    if not isinstance(key, Mapping) or set(key) != {
        "schema_version", "provenance", "review_bundle", "items",
    }:
        raise BenchmarkError("condition key has invalid top-level fields")
    if key.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("condition key schema version mismatch")
    items = key.get("items")
    visible_items = bundle.get("items")
    if not isinstance(items, Mapping) or not isinstance(visible_items, list):
        raise BenchmarkError("condition key items are malformed")
    visible_by_id = {
        item.get("id"): item
        for item in visible_items
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    if len(visible_by_id) != _EXPECTED_PRESENTATIONS or set(items) != set(visible_by_id):
        raise BenchmarkError("condition key item IDs do not match the review bundle")

    bindings = key.get("review_bundle")
    item_ids = [item["id"] for item in visible_items]
    expected_bindings = {
        "sha256": sha256_bytes(canonical_bytes(bundle)),
        "item_ids_sha256": sha256_bytes(canonical_bytes(item_ids)),
    }
    if bindings != expected_bindings:
        raise BenchmarkError("condition key review-bundle binding mismatch")

    provenance = key.get("provenance")
    if not isinstance(provenance, Mapping) or set(provenance) != {
        "blinding_seed", "cases_sha256", "runs_sha256", "prepared",
    }:
        raise BenchmarkError("condition key provenance is malformed")
    if type(provenance.get("blinding_seed")) is not int:
        raise BenchmarkError("condition key blinding seed is malformed")
    _require_sha256(provenance.get("cases_sha256"), "condition key case hash")
    _require_sha256(provenance.get("runs_sha256"), "condition key run hash")
    if provenance.get("prepared") != prepared_provenance:
        raise BenchmarkError("condition key prepared provenance mismatch")

    repeats = 0
    for item_id, hidden in items.items():
        if not isinstance(hidden, Mapping):
            raise BenchmarkError(f"condition key item {item_id!r} is malformed")
        allowed = {"case_id", "attempt", "labels", "repeat_of"}
        required = {"case_id", "attempt", "labels"}
        if not required <= set(hidden) or not set(hidden) <= allowed:
            raise BenchmarkError(f"condition key item {item_id!r} fields are malformed")
        if not isinstance(hidden.get("case_id"), str):
            raise BenchmarkError(f"condition key item {item_id!r} case id is malformed")
        attempt = hidden.get("attempt")
        if type(attempt) is not int or not 1 <= attempt <= PRIMARY_ATTEMPTS:
            raise BenchmarkError(f"condition key item {item_id!r} attempt is malformed")
        labels = hidden.get("labels")
        visible_outputs = visible_by_id[item_id].get("outputs")
        if not isinstance(labels, Mapping) or not isinstance(visible_outputs, Mapping):
            raise BenchmarkError(f"condition key item {item_id!r} labels are malformed")
        if set(labels) != set(visible_outputs) or len(set(labels.values())) != len(labels):
            raise BenchmarkError(f"condition key item {item_id!r} label mapping is malformed")
        expected_conditions = (
            {"normal", "suite", "context_only"}
            if len(visible_outputs) == 3
            else {"normal", "suite"}
        )
        if set(labels.values()) != expected_conditions:
            raise BenchmarkError(f"condition key item {item_id!r} conditions are malformed")
        if "repeat_of" in hidden:
            repeats += 1
            original = hidden["repeat_of"]
            if not isinstance(original, str) or original == item_id or original not in items:
                raise BenchmarkError(f"condition key item {item_id!r} repeat target is malformed")
            original_hidden = items[original]
            if not isinstance(original_hidden, Mapping) or "repeat_of" in original_hidden:
                raise BenchmarkError(f"condition key item {item_id!r} repeat chain is malformed")
            if (
                hidden["case_id"] != original_hidden.get("case_id")
                or hidden["attempt"] != original_hidden.get("attempt")
            ):
                raise BenchmarkError(f"condition key item {item_id!r} repeat identity differs")
    if repeats != _EXPECTED_REPEATS:
        raise BenchmarkError("condition key must identify exactly 18 private repeats")
    if canonical_bytes(key) != canonical_bytes(expected_key):
        raise BenchmarkError("published condition key differs from generated key")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a blind PT-PT benchmark review queue.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--review-bundle", required=True, type=Path)
    parser.add_argument("--condition-key", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    review_target: _HeldTarget | None = None
    key_target: _HeldTarget | None = None
    try:
        review_target, key_target = _validate_output_targets(
            arguments.review_bundle,
            arguments.condition_key,
            dataset_dir=arguments.dataset,
            evidence_dir=arguments.evidence,
        )
        verify_dataset_manifest(arguments.dataset)
        run_manifest, prepared_provenance = _validate_prepared_manifest(
            arguments.dataset, arguments.evidence
        )
        cases = read_jsonl(arguments.dataset / "cases.jsonl")
        run_ids = _validate_schedule_manifest(run_manifest)
        runs = _load_complete_runs(arguments.evidence, run_ids)
        bundle, key = build_blind_bundle(
            runs,
            cases,
            arguments.seed,
            prepared_provenance=prepared_provenance,
        )
        seeded_errors = read_json(arguments.dataset / "seeded-errors.json")
        hidden_values = _hidden_values(cases, runs)
        hidden_values.update(_flatten_hidden_strings(seeded_errors, excluded_keys=frozenset()))
        scan_visible_bundle(bundle, _hidden_values=hidden_values)

        verify_dataset_manifest(arguments.dataset)
        current_manifest, current_provenance = _validate_prepared_manifest(
            arguments.dataset, arguments.evidence
        )
        if current_manifest != run_manifest or current_provenance != prepared_provenance:
            raise BenchmarkError("blinding inputs changed before publication")

        _atomic_create_pair(review_target, bundle, key_target, key)
        try:
            verify_dataset_manifest(arguments.dataset)
            published_manifest, published_provenance = _validate_prepared_manifest(
                arguments.dataset, arguments.evidence
            )
            if (
                published_manifest != run_manifest
                or published_provenance != prepared_provenance
            ):
                raise BenchmarkError("blinding inputs changed during publication")

            review_bytes = _read_published(review_target)
            key_bytes = _read_published(key_target)
            reloaded_bundle = _decode_canonical_artifact(review_bytes, "review bundle")
            reloaded_key = _decode_canonical_artifact(key_bytes, "condition key")
            scan_visible_bundle(reloaded_bundle, _hidden_values=hidden_values)
            if canonical_bytes(reloaded_bundle) != canonical_bytes(bundle):
                raise BenchmarkError("published review bundle differs from generated bundle")
            _validate_condition_key(
                reloaded_key,
                reloaded_bundle,
                expected_key=key,
                prepared_provenance=prepared_provenance,
            )

            if (
                _read_published(review_target) != review_bytes
                or _read_published(key_target) != key_bytes
            ):
                raise BenchmarkError("published blinded artifacts changed after validation")
        except Exception as error:
            _rollback_targets((review_target, key_target))
            if isinstance(error, BenchmarkError):
                raise
            raise BenchmarkError(f"cannot validate published blinded outputs: {error}") from error
        sys.stdout.buffer.write(canonical_bytes({
            "review_bundle_sha256": sha256_bytes(review_bytes),
            "condition_key_sha256": sha256_bytes(key_bytes),
        }))
        return 0
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    finally:
        if review_target is not None:
            review_target.close()
        if key_target is not None:
            key_target.close()


if __name__ == "__main__":
    raise SystemExit(main())
