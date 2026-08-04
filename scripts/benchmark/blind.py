from __future__ import annotations

import argparse
import base64
import binascii
import html
import json
import os
import random
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
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
from .run import RunResult
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
_POSIX_ABSOLUTE = re.compile(r"(?<![:/\\\w])/(?:[^\s/]+(?:/|$))+", re.UNICODE)
_WINDOWS_ABSOLUTE = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|\\\\)[^\s]+")
_FORBIDDEN_KEY_PARTS = {
    "condition", "repeat", "runner", "model", "latency", "duration", "timing",
    "metric", "threshold", "reference", "seeded", "answer", "runid", "caseid",
    "outputsha", "hash", "digest", "path", "filepath", "suite", "attempt",
    "status", "failure", "stderr", "telemetry", "usage", "token", "argv", "shell",
    "prompt", "finding", "score", "automaticcheck", "projectfingerprint", "log",
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
        if run.get("schema_version") != SCHEMA_VERSION:
            raise BenchmarkError("run schema version mismatch")
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in run_ids:
            raise BenchmarkError(f"duplicate or invalid run id: {run_id!r}")
        run_ids.add(run_id)
        case_id = run.get("case_id")
        condition = run.get("condition")
        attempt = run.get("attempt")
        if case_id not in cases:
            raise BenchmarkError(f"run has unknown case id: {case_id!r}")
        if condition not in _conditions_for_case(cases[str(case_id)]):
            raise BenchmarkError(
                f"run {run.get('run_id')!r} has invalid condition for case {case_id}"
            )
        if type(attempt) is not int or not 1 <= attempt <= PRIMARY_ATTEMPTS:
            raise BenchmarkError(f"run {run.get('run_id')!r} has invalid attempt")
        if run.get("failure_class") not in {"success", "model_outcome"}:
            raise BenchmarkError(f"run {run.get('run_id')!r} is not complete")
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
    key = {
        "schema_version": SCHEMA_VERSION,
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
    forms = {value}
    current = value
    for _ in range(3):
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
        forms.add(decoded)
        if decoded == current:
            break
        current = decoded
    stripped = value.strip()
    if len(stripped) >= 8:
        try:
            decoded_bytes = base64.b64decode(stripped, validate=True)
            decoded_text = decoded_bytes.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            pass
        else:
            if decoded_text.lstrip().startswith(("{", "[")):
                forms.add(decoded_text)
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
        stripped = normalized.strip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            _scan_metadata_value(decoded, f"{location}<serialized>", hidden_values)
    for hidden in hidden_values:
        if hidden and hidden in value:
            raise BenchmarkError(f"visible bundle contains exact hidden data at {location}")


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
        unicodedata.normalize("NFKC", value)
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


def _manifest_run_ids(evidence_dir: Path) -> list[str]:
    manifest = read_json(evidence_dir / "run-manifest.json")
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("run manifest schema version mismatch")
    schedule = manifest.get("schedule")
    if not isinstance(schedule, Mapping):
        raise BenchmarkError("run manifest has no schedule")
    run_ids = schedule.get("run_ids")
    if (
        not isinstance(run_ids, list)
        or not all(isinstance(run_id, str) and run_id for run_id in run_ids)
        or len(run_ids) != len(set(run_ids))
    ):
        raise BenchmarkError("run manifest schedule has invalid run ids")
    if schedule.get("sha256") != sha256_bytes(canonical_bytes(run_ids)):
        raise BenchmarkError("run manifest schedule hash mismatch")
    return run_ids


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


def _load_complete_runs(evidence_dir: Path) -> list[dict]:
    evidence_dir = Path(evidence_dir)
    if evidence_dir.is_symlink():
        raise BenchmarkError("refusing symlink evidence directory")
    run_ids = _manifest_run_ids(evidence_dir)
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


def _validate_output_targets(
    review_bundle: Path,
    condition_key: Path,
    *,
    dataset_dir: Path,
    evidence_dir: Path,
) -> tuple[Path, Path]:
    targets = (Path(review_bundle), Path(condition_key))
    for target in targets:
        if not target.name or target.exists() or target.is_symlink():
            raise BenchmarkError(f"output target must be nonexistent: {target}")
        if not target.parent.is_dir():
            raise BenchmarkError(f"output parent must be an existing directory: {target.parent}")
        if target.parent.is_symlink():
            raise BenchmarkError(f"output parent may not be a symlink: {target.parent}")
    resolved = tuple(target.parent.resolve() / target.name for target in targets)
    if resolved[0] == resolved[1] or resolved[0].parent == resolved[1].parent:
        raise BenchmarkError("review bundle and condition key require distinct directories")
    input_roots = (Path(dataset_dir).resolve(), Path(evidence_dir).resolve())
    for target in resolved:
        if any(_inside(target, root) for root in input_roots):
            raise BenchmarkError("blinding outputs must be outside dataset and evidence inputs")
    return resolved


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_create_pair(review_path: Path, review: object, key_path: Path, key: object) -> None:
    staged: list[Path] = []
    created: list[Path] = []
    values = ((review_path, review, 0o644), (key_path, key, 0o600))
    try:
        for target, value, mode in values:
            temporary = target.with_name(f".{target.name}.tmp-{uuid4().hex}")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(descriptor, "wb") as output:
                output.write(canonical_bytes(value))
                output.flush()
                os.fsync(output.fileno())
            staged.append(temporary)
        for (target, _, _), temporary in zip(values, staged):
            os.link(temporary, target)
            created.append(target)
        for temporary in staged:
            temporary.unlink()
        for directory in {review_path.parent, key_path.parent}:
            _fsync_directory(directory)
    except (OSError, BenchmarkError) as error:
        for target in created:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        for temporary in staged:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        if isinstance(error, BenchmarkError):
            raise
        raise BenchmarkError(f"cannot atomically publish blinded outputs: {error}") from error


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
    try:
        review_path, key_path = _validate_output_targets(
            arguments.review_bundle,
            arguments.condition_key,
            dataset_dir=arguments.dataset,
            evidence_dir=arguments.evidence,
        )
        verify_dataset_manifest(arguments.dataset)
        cases = read_jsonl(arguments.dataset / "cases.jsonl")
        runs = _load_complete_runs(arguments.evidence)
        bundle, key = build_blind_bundle(runs, cases, arguments.seed)
        seeded_errors = read_json(arguments.dataset / "seeded-errors.json")
        hidden_values = _hidden_values(cases, runs)
        hidden_values.update(_flatten_hidden_strings(seeded_errors, excluded_keys=frozenset()))
        scan_visible_bundle(bundle, _hidden_values=hidden_values)
        _atomic_create_pair(review_path, bundle, key_path, key)
        try:
            reloaded = read_json(review_path)
            scan_visible_bundle(reloaded, _hidden_values=hidden_values)
            if canonical_bytes(reloaded) != canonical_bytes(bundle):
                raise BenchmarkError("reloaded review bundle differs from generated bytes")
        except Exception:
            for target in (review_path, key_path):
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            raise
        sys.stdout.buffer.write(canonical_bytes({
            "review_bundle_sha256": sha256_bytes(review_path.read_bytes()),
            "condition_key_sha256": sha256_bytes(key_path.read_bytes()),
        }))
        return 0
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
