from __future__ import annotations

import argparse
import math
import os
import random
import stat
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .blind import (
    _parse_canonical_json,
    _parse_canonical_jsonl,
    _validate_condition_key,
    _validate_prepared_manifest,
    _validate_run_record,
    build_blind_bundle,
    scan_visible_bundle,
)
from .common import (
    BenchmarkError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)
from .comparisons import PREFERENCE_ORDINAL, reject_comparison_cycle
from .prepare import verify_dataset_manifest
from .review_app import MQM_DIMENSIONS, ReviewStore
from .run import RunResult
from .schema import CONDITIONS, DIFFICULTIES, SCHEMA_VERSION, SURFACES, TASKS
from .validate import CHECKS


SEVERITY_POINTS = {"critical": 25, "major": 5, "minor": 1, "neutral": 0}
_ITEM_FIELDS = {
    "item_id", "case_id", "attempt", "task", "repeat_of", "labels",
    "comparisons", "mqm", "major_or_worse", "word_counts",
}
_VALIDATION_FIELDS = {
    "run_id", "case_id", "condition", "attempt", "status", "findings",
    "validator_errors", "applicable_checks", "passed_checks", "failed_checks",
    "validator_error_checks", "applicable_invariants",
}
_FINDING_FIELDS = {
    "invariant", "severity", "expected", "observed", "affected_span", "message",
}
_MAPPING_FIELDS = {
    "run_id", "seeded_error_id", "reported", "corrected", "introduced_error",
    "adjudicator", "note",
}
_LEARNED_FIELDS = {"run_id", "metric", "value"}
_LEARNED_METRICS = {"comet", "xcomet", "chrf"}
_CASE_FIELDS = {"case_id", "task", "surface", "difficulty"}
_RUN_FIELDS = {
    "run_id", "case_id", "condition", "attempt", "started_at", "completed_at",
    "telemetry", "usage",
}
_MAX_SMALL_INPUT_BYTES = 1024 * 1024
_MAX_DOCUMENT_INPUT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ScorePaths:
    dataset_dir: Path
    evidence_dir: Path
    review_bundle: Path
    condition_key: Path
    annotations: Path
    annotation_lock: Path


@dataclass(frozen=True)
class Pair:
    case_id: str
    attempt: int
    suite_value: float
    normal_value: float


def _exact_int(value: object, description: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        qualifier = f" at least {minimum}" if minimum is not None else ""
        raise BenchmarkError(f"{description} must be an integer{qualifier}")
    return value


def _text(value: object, description: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise BenchmarkError(f"{description} must be {'non-empty ' if nonempty else ''}text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise BenchmarkError(f"{description} must contain valid Unicode") from error
    return value


def _finite(value: object, description: str) -> float:
    if type(value) not in (int, float):
        raise BenchmarkError(f"{description} must be a finite number")
    try:
        if not math.isfinite(value):
            raise BenchmarkError(f"{description} must be a finite number")
        return float(value)
    except (OverflowError, ValueError) as error:
        raise BenchmarkError(f"{description} must be a finite number") from error


def _rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def quantile(values: Sequence[float], probability: float) -> float:
    """Return the linearly interpolated sample quantile on [0, 1]."""
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        raise BenchmarkError("quantile requires observations")
    probability_value = _finite(probability, "quantile probability")
    if not 0.0 <= probability_value <= 1.0:
        raise BenchmarkError("quantile probability must be between zero and one")
    ordered = sorted(_finite(value, "quantile observation") for value in values)
    position = probability_value * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def paired_bootstrap(
    pairs: Sequence[Pair],
    statistic: Callable[[Sequence[Pair]], float],
    *,
    seed: int,
    draws: int = 10000,
) -> dict:
    if not isinstance(pairs, Sequence) or isinstance(pairs, (str, bytes)) or not pairs:
        raise BenchmarkError("paired bootstrap requires observations")
    _exact_int(seed, "bootstrap seed")
    _exact_int(draws, "bootstrap draws", minimum=1)
    checked: list[Pair] = []
    identities: set[tuple[str, int]] = set()
    for pair in pairs:
        if not isinstance(pair, Pair):
            raise BenchmarkError("paired bootstrap observations must be Pair values")
        _text(pair.case_id, "bootstrap pair case_id")
        _exact_int(pair.attempt, "bootstrap pair attempt", minimum=1)
        _finite(pair.suite_value, "bootstrap suite value")
        _finite(pair.normal_value, "bootstrap normal value")
        identity = (pair.case_id, pair.attempt)
        if identity in identities:
            raise BenchmarkError(f"duplicate bootstrap pair identity: {identity!r}")
        identities.add(identity)
        checked.append(pair)
    if not callable(statistic):
        raise BenchmarkError("paired bootstrap statistic must be callable")

    def calculate(sample: Sequence[Pair]) -> float:
        try:
            value = statistic(sample)
        except Exception as error:
            raise BenchmarkError(f"paired bootstrap statistic failed: {error}") from error
        return _finite(value, "paired bootstrap statistic")

    estimate = calculate(checked)
    rng = random.Random(seed)
    sampled_values: list[float] = []
    for _ in range(draws):
        sample = [checked[rng.randrange(len(checked))] for _ in checked]
        sampled_values.append(calculate(sample))
    sampled_values.sort()
    return {
        "estimate": estimate,
        "lower_95": quantile(sampled_values, 0.025),
        "upper_95": quantile(sampled_values, 0.975),
        "seed": seed,
        "draws": draws,
    }


def non_tied_win_rate(outcomes: Sequence[int | float]) -> float:
    wins = sum(value > 0 for value in outcomes)
    losses = sum(value < 0 for value in outcomes)
    return _rate(wins, wins + losses)


def _comparison_keys(labels: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        f"{left}:{right}"
        for index, left in enumerate(labels)
        for right in labels[index + 1 :]
    )


def _validate_item(value: object, index: int) -> dict:
    if not isinstance(value, Mapping) or set(value) != _ITEM_FIELDS:
        raise BenchmarkError(f"score item {index} fields are invalid")
    result = dict(value)
    _text(result.get("item_id"), f"score item {index} item_id")
    _text(result.get("case_id"), f"score item {index} case_id")
    _exact_int(result.get("attempt"), f"score item {index} attempt", minimum=1)
    task = _text(result.get("task"), f"score item {index} task")
    if task not in TASKS:
        raise BenchmarkError(f"score item {index} task is invalid")
    repeat_of = result.get("repeat_of")
    if repeat_of is not None:
        _text(repeat_of, f"score item {index} repeat_of")
    labels = result.get("labels")
    if not isinstance(labels, Mapping):
        raise BenchmarkError(f"score item {index} labels must be an object")
    expected_labels = tuple("ABC"[:len(labels)])
    if tuple(labels) != expected_labels or len(labels) not in (2, 3):
        raise BenchmarkError(f"score item {index} anonymous labels are invalid")
    conditions = tuple(labels.values())
    if (
        any(type(condition) is not str or condition not in CONDITIONS for condition in conditions)
        or len(set(conditions)) != len(conditions)
        or not {"normal", "suite"} <= set(conditions)
        or (len(conditions) == 3 and set(conditions) != set(CONDITIONS))
    ):
        raise BenchmarkError(f"score item {index} condition mapping is invalid")
    comparisons = result.get("comparisons")
    expected_keys = _comparison_keys(expected_labels)
    if not isinstance(comparisons, Mapping) or set(comparisons) != set(expected_keys):
        raise BenchmarkError(f"score item {index} comparison fields are invalid")
    for key in expected_keys:
        if type(comparisons[key]) is not str or comparisons[key] not in PREFERENCE_ORDINAL:
            raise BenchmarkError(f"score item {index} comparison {key} is invalid")
    reject_comparison_cycle(expected_labels, comparisons)

    major = result.get("major_or_worse")
    if not isinstance(major, Mapping) or set(major) != set(expected_labels):
        raise BenchmarkError(f"score item {index} major_or_worse fields are invalid")
    if any(type(major[label]) is not bool for label in expected_labels):
        raise BenchmarkError(f"score item {index} major_or_worse values must be booleans")
    observed_major = {label: False for label in expected_labels}
    mqm = result.get("mqm")
    if not isinstance(mqm, list):
        raise BenchmarkError(f"score item {index} mqm must be a list")
    for finding_index, finding in enumerate(mqm):
        if not isinstance(finding, Mapping) or set(finding) != {"output", "dimension", "severity"}:
            raise BenchmarkError(f"score item {index} mqm entry {finding_index} fields are invalid")
        output = finding.get("output")
        dimension = finding.get("dimension")
        severity = finding.get("severity")
        if type(output) is not str or output not in labels:
            raise BenchmarkError(f"score item {index} mqm output is invalid")
        if type(severity) is not str or severity not in SEVERITY_POINTS:
            raise BenchmarkError(f"score item {index} mqm severity is invalid")
        if type(dimension) is not str or dimension not in MQM_DIMENSIONS:
            raise BenchmarkError(f"score item {index} mqm dimension is invalid")
        if severity in {"critical", "major"}:
            observed_major[output] = True
    if any(major[label] is not observed_major[label] for label in expected_labels):
        raise BenchmarkError(f"score item {index} major_or_worse does not match MQM")
    words = result.get("word_counts")
    if not isinstance(words, Mapping) or set(words) != set(expected_labels):
        raise BenchmarkError(f"score item {index} word_counts fields are invalid")
    for label in expected_labels:
        _exact_int(words[label], f"score item {index} word count {label}", minimum=0)
    return result


def _condition_ordinal(item: Mapping[str, object], left_condition: str, right_condition: str) -> int:
    labels = item["labels"]
    by_condition = {condition: label for label, condition in labels.items()}
    left_label = by_condition[left_condition]
    right_label = by_condition[right_condition]
    ordered = tuple(labels)
    if ordered.index(left_label) < ordered.index(right_label):
        key, sign = f"{left_label}:{right_label}", 1
    else:
        key, sign = f"{right_label}:{left_label}", -1
    return sign * PREFERENCE_ORDINAL[item["comparisons"][key]]


def _condition_major(item: Mapping[str, object]) -> dict[str, bool]:
    return {
        condition: item["major_or_worse"][label]
        for label, condition in item["labels"].items()
    }


def _validate_findings(value: object, description: str) -> list[dict]:
    if not isinstance(value, list):
        raise BenchmarkError(f"{description} findings must be a list")
    result: list[dict] = []
    for index, finding in enumerate(value):
        if not isinstance(finding, Mapping) or set(finding) != _FINDING_FIELDS:
            raise BenchmarkError(f"{description} finding {index} fields are invalid")
        invariant = _text(finding.get("invariant"), f"{description} finding invariant")
        severity = finding.get("severity")
        if type(severity) is not str or severity not in SEVERITY_POINTS:
            raise BenchmarkError(f"{description} finding severity is invalid")
        _text(finding.get("message"), f"{description} finding message", nonempty=False)
        span = finding.get("affected_span")
        if span is not None and (
            not isinstance(span, (list, tuple))
            or len(span) != 2
            or any(type(point) is not int or point < 0 for point in span)
            or span[1] < span[0]
        ):
            raise BenchmarkError(f"{description} finding affected_span is invalid")
        result.append({**dict(finding), "invariant": invariant})
    return result


def _validate_validations(values: object) -> tuple[list[dict], dict[str, dict]]:
    if not isinstance(values, list):
        raise BenchmarkError("validations must be a list")
    records: list[dict] = []
    by_run: dict[str, dict] = {}
    identities: set[tuple[str, str, int]] = set()
    declarations_by_case: dict[str, tuple[str, ...]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, Mapping) or set(value) != _VALIDATION_FIELDS:
            raise BenchmarkError(f"validation {index} fields are invalid")
        record = dict(value)
        run_id = _text(record.get("run_id"), f"validation {index} run_id")
        case_id = _text(record.get("case_id"), f"validation {index} case_id")
        condition = _text(record.get("condition"), f"validation {index} condition")
        if condition not in CONDITIONS:
            raise BenchmarkError(f"validation {index} condition is invalid")
        attempt = _exact_int(record.get("attempt"), f"validation {index} attempt", minimum=1)
        status = _text(record.get("status"), f"validation {index} status")
        if status not in {"passed", "failed", "validator_error"}:
            raise BenchmarkError(f"validation {index} status is invalid")
        record["findings"] = _validate_findings(record.get("findings"), f"validation {index}")
        applicable_invariants = record.get("applicable_invariants")
        if (
            not isinstance(applicable_invariants, list)
            or any(
                type(invariant) is not str or invariant not in CHECKS
                for invariant in applicable_invariants
            )
        ):
            raise BenchmarkError(
                f"validation {index} applicable_invariants are invalid"
            )
        declaration = tuple(applicable_invariants)
        prior_declaration = declarations_by_case.setdefault(case_id, declaration)
        if Counter(prior_declaration) != Counter(declaration):
            raise BenchmarkError(
                f"validation {index} applicable invariants differ within case"
            )
        declared_counts = Counter(declaration)
        finding_counts = Counter(
            finding["invariant"] for finding in record["findings"]
        )
        undeclared = sorted(
            invariant for invariant, count in finding_counts.items()
            if count > declared_counts[invariant]
        )
        if undeclared:
            raise BenchmarkError(
                f"validation {index} finding is not declared applicable: "
                f"{undeclared!r}"
            )
        errors = record.get("validator_errors")
        if not isinstance(errors, list) or any(type(error) is not str for error in errors):
            raise BenchmarkError(f"validation {index} validator_errors are invalid")
        counts = [
            _exact_int(record.get(field), f"validation {index} {field}", minimum=0)
            for field in (
                "applicable_checks", "passed_checks", "failed_checks",
                "validator_error_checks",
            )
        ]
        if counts[0] != sum(counts[1:]):
            raise BenchmarkError(f"validation {index} check counts are inconsistent")
        if len(declaration) != counts[0]:
            raise BenchmarkError(
                f"validation {index} invariant applicability count is inconsistent"
            )
        validator_error_counts = Counter(
            invariant
            for error in errors
            for invariant in declared_counts
            if error.startswith(f"{invariant}:")
        )
        if (
            sum(finding_counts.values()) != counts[2]
            or sum(validator_error_counts.values()) != counts[3]
            or any(
                finding_counts[invariant] + validator_error_counts[invariant]
                > declared_counts[invariant]
                for invariant in declared_counts
            )
            or sum(
                declared_counts[invariant]
                - finding_counts[invariant]
                - validator_error_counts[invariant]
                for invariant in declared_counts
            ) != counts[1]
        ):
            raise BenchmarkError(
                f"validation {index} per-invariant check counts are inconsistent"
            )
        expected_status = (
            "validator_error" if counts[3] else ("failed" if counts[2] else "passed")
        )
        if status != expected_status:
            raise BenchmarkError(f"validation {index} status is inconsistent")
        if run_id in by_run:
            raise BenchmarkError(f"duplicate validation run id: {run_id}")
        identity = (case_id, condition, attempt)
        if identity in identities:
            raise BenchmarkError(f"duplicate validation identity: {identity!r}")
        identities.add(identity)
        by_run[run_id] = record
        records.append(record)
    return records, by_run


def _structural_summary(
    validations: Sequence[Mapping[str, object]],
    task_identities: set[tuple[str, int]],
) -> tuple[dict[str, float], dict[str, int], int]:
    totals = {condition: [0, 0] for condition in CONDITIONS}
    critical_by_identity: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    critical_counts = {condition: 0 for condition in CONDITIONS}
    for record in validations:
        identity = (record["case_id"], record["attempt"])
        if identity not in task_identities:
            continue
        condition = record["condition"]
        totals[condition][0] += record["passed_checks"]
        totals[condition][1] += record["applicable_checks"]
        for finding in record["findings"]:
            if finding["severity"] == "critical":
                critical_counts[condition] += 1
                critical_by_identity[(identity[0], identity[1], condition)].add(
                    finding["invariant"]
                )
    rates = {
        condition: _rate(passed, applicable)
        for condition, (passed, applicable) in totals.items()
    }
    regressions = 0
    for case_id, attempt in task_identities:
        normal = critical_by_identity[(case_id, attempt, "normal")]
        suite = critical_by_identity[(case_id, attempt, "suite")]
        regressions += len(suite - normal)
    return rates, critical_counts, regressions


def _review_metrics(
    mappings: object,
    seeded_errors: Mapping[str, object],
    validations: Sequence[Mapping[str, object]],
    review_identities: set[tuple[str, int]],
    validations_by_run: Mapping[str, Mapping[str, object]],
) -> dict:
    if mappings is None:
        return {"available": False, "reason": "review mappings unavailable", "unresolved": 0}
    if not isinstance(mappings, list):
        raise BenchmarkError("review_mappings must be a list or null")
    inventory: dict[str, dict] = {}
    for case_id, errors in seeded_errors.items():
        _text(case_id, "seeded-error case id")
        if not isinstance(errors, list) or not errors:
            raise BenchmarkError(f"seeded errors for {case_id} must be a non-empty list")
        for error in errors:
            if not isinstance(error, Mapping) or set(error) != {
                "id", "severity", "correction_required"
            }:
                raise BenchmarkError(f"normalized seeded error for {case_id} has invalid fields")
            error_id = _text(error.get("id"), f"seeded error for {case_id} id")
            severity = error.get("severity")
            if type(severity) is not str or severity not in SEVERITY_POINTS:
                raise BenchmarkError(f"seeded error {error_id} severity is invalid")
            if type(error.get("correction_required")) is not bool:
                raise BenchmarkError(f"seeded error {error_id} correction_required must be boolean")
            if error_id in inventory:
                raise BenchmarkError(f"duplicate seeded error id: {error_id}")
            inventory[error_id] = {**dict(error), "case_id": case_id}

    seen: set[tuple[str, str]] = set()
    known: dict[tuple[str, str], dict] = {}
    unresolved = 0
    unresolved_reported = 0
    unresolved_corrected = 0
    mappings_by_run: dict[str, list[dict]] = defaultdict(list)
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, Mapping) or set(mapping) != _MAPPING_FIELDS:
            raise BenchmarkError(f"review mapping {index} fields are invalid")
        record = dict(mapping)
        run_id = _text(record.get("run_id"), f"review mapping {index} run_id")
        error_id = _text(record.get("seeded_error_id"), f"review mapping {index} seeded_error_id")
        if run_id not in validations_by_run:
            raise BenchmarkError(f"review mapping {index} has unknown run id")
        validation = validations_by_run[run_id]
        if (validation["case_id"], validation["attempt"]) not in review_identities:
            raise BenchmarkError(f"review mapping {index} does not name a review run")
        for field in ("reported", "corrected", "introduced_error"):
            if type(record.get(field)) is not bool:
                raise BenchmarkError(f"review mapping {index} {field} must be a boolean")
        _text(record.get("adjudicator"), f"review mapping {index} adjudicator")
        _text(record.get("note"), f"review mapping {index} note", nonempty=False)
        identity = (run_id, error_id)
        if identity in seen:
            raise BenchmarkError(f"duplicate review mapping: {identity!r}")
        seen.add(identity)
        mappings_by_run[run_id].append(record)
        seeded = inventory.get(error_id)
        if seeded is None or seeded["case_id"] != validation["case_id"]:
            unresolved += 1
            unresolved_reported += int(record["reported"])
            unresolved_corrected += int(record["corrected"])
            continue
        known[identity] = record

    expected = {
        (record["run_id"], error["id"])
        for record in validations
        if (record["case_id"], record["attempt"]) in review_identities
        for error in seeded_errors.get(record["case_id"], ())
    }
    if set(known) != expected:
        missing = sorted(expected - set(known))
        unknown = sorted(set(known) - expected)
        raise BenchmarkError(
            f"review mapping identities mismatch: missing={missing!r}, unknown={unknown!r}"
        )

    counters: dict[str, dict[str, int]] = {
        condition: defaultdict(int) for condition in CONDITIONS
    }
    for (run_id, error_id), mapping in known.items():
        condition = validations_by_run[run_id]["condition"]
        seeded = inventory[error_id]
        required = seeded["correction_required"]
        counters[condition]["known"] += 1
        if required:
            counters[condition]["required"] += 1
            counters[condition]["recalled"] += int(mapping["reported"])
            counters[condition]["corrected"] += int(mapping["corrected"])
            if seeded["severity"] == "critical" and not mapping["reported"]:
                counters[condition]["critical_misses"] += 1
        else:
            counters[condition]["bait"] += 1
            counters[condition]["bait_corrected"] += int(mapping["corrected"])
        if mapping["reported"]:
            counters[condition]["reported"] += 1
            counters[condition]["true_reported"] += int(required)

    for record in validations:
        if (record["case_id"], record["attempt"]) not in review_identities:
            continue
        condition = record["condition"]
        counters[condition]["response_runs"] += 1
        counters[condition]["introduced_runs"] += int(any(
            mapping["introduced_error"] for mapping in mappings_by_run[record["run_id"]]
        ))

    structural, _, regressions = _structural_summary(validations, review_identities)
    result: dict[str, object] = {
        "available": True,
        "unresolved": unresolved,
        "unresolved_reported": unresolved_reported,
        "unresolved_corrected": unresolved_corrected,
        "introduced_error_aggregation": "any_mapping_per_run",
        "introduced_error_runs": {
            condition: values["introduced_runs"] for condition, values in counters.items()
            if values["response_runs"]
        },
        "response_runs": {
            condition: values["response_runs"] for condition, values in counters.items()
            if values["response_runs"]
        },
    }
    result["required_error_recall"] = {
        condition: _rate(values["recalled"], values["required"])
        for condition, values in counters.items()
        if values["known"]
    }
    result["reported_error_precision"] = {
        condition: _rate(values["true_reported"], values["reported"])
        for condition, values in counters.items()
        if values["known"]
    }
    result["correction_success_rate"] = {
        condition: _rate(values["corrected"], values["required"])
        for condition, values in counters.items()
        if values["known"]
    }
    result["false_positive_correction_rate"] = {
        condition: _rate(values["bait_corrected"], values["bait"])
        for condition, values in counters.items()
        if values["known"]
    }
    result["introduced_error_rate"] = {
        condition: _rate(values["introduced_runs"], values["response_runs"])
        for condition, values in counters.items()
        if values["response_runs"]
    }
    result["critical_misses"] = {
        condition: values["critical_misses"]
        for condition, values in counters.items()
        if values["known"]
    }
    result["structural_pass_rate"] = {
        condition: structural[condition]
        for condition, values in counters.items()
        if values["known"]
    }
    result["critical_invariant_regressions"] = regressions
    return result


def _learned_metrics(values: object, validations_by_run: Mapping[str, Mapping[str, object]]) -> dict:
    if values is None:
        return {"available": False, "reason": "learned metrics unavailable"}
    if not isinstance(values, list):
        raise BenchmarkError("learned_metrics must be a list or null")
    observations: dict[tuple[str, str], float] = {}
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for index, value in enumerate(values):
        if not isinstance(value, Mapping) or set(value) != _LEARNED_FIELDS:
            raise BenchmarkError(f"learned metric {index} fields are invalid")
        run_id = _text(value.get("run_id"), f"learned metric {index} run_id")
        metric = _text(value.get("metric"), f"learned metric {index} metric")
        if metric not in _LEARNED_METRICS:
            raise BenchmarkError(f"learned metric {index} name is invalid")
        score = _finite(value.get("value"), f"learned metric {index} value")
        if run_id not in validations_by_run:
            raise BenchmarkError(f"learned metric {index} has unknown run id")
        identity = (run_id, metric)
        if identity in observations:
            raise BenchmarkError(f"duplicate learned metric: {identity!r}")
        observations[identity] = score
        grouped[metric][validations_by_run[run_id]["condition"]].append(score)
    return {
        "available": True,
        "means": {
            metric: {
                condition: sum(scores) / len(scores)
                for condition, scores in sorted(by_condition.items())
            }
            for metric, by_condition in sorted(grouped.items())
        },
    }


def _validate_cases_and_runs(
    cases_value: object,
    runs_value: object,
    validations_by_run: Mapping[str, Mapping[str, object]],
    primary_identities: set[tuple[str, int]],
) -> tuple[dict[str, dict], list[dict]]:
    if not isinstance(cases_value, list):
        raise BenchmarkError("cases must be a list")
    cases: dict[str, dict] = {}
    for index, value in enumerate(cases_value):
        if not isinstance(value, Mapping) or set(value) != _CASE_FIELDS:
            raise BenchmarkError(f"case metadata {index} fields are invalid")
        record = dict(value)
        case_id = _text(record.get("case_id"), f"case metadata {index} case_id")
        if case_id in cases:
            raise BenchmarkError(f"duplicate case metadata: {case_id}")
        if record.get("task") not in TASKS:
            raise BenchmarkError(f"case metadata {index} task is invalid")
        if record.get("surface") not in SURFACES:
            raise BenchmarkError(f"case metadata {index} surface is invalid")
        if record.get("difficulty") not in DIFFICULTIES:
            raise BenchmarkError(f"case metadata {index} difficulty is invalid")
        cases[case_id] = record
    expected_case_ids = {case_id for case_id, _ in primary_identities}
    if set(cases) != expected_case_ids:
        raise BenchmarkError("case metadata identities do not match primary score items")

    if not isinstance(runs_value, list):
        raise BenchmarkError("runs must be a list")
    runs: list[dict] = []
    seen: set[str] = set()
    for index, value in enumerate(runs_value):
        if not isinstance(value, Mapping) or set(value) != _RUN_FIELDS:
            raise BenchmarkError(f"run metadata {index} fields are invalid")
        record = dict(value)
        run_id = _text(record.get("run_id"), f"run metadata {index} run_id")
        if run_id in seen or run_id not in validations_by_run:
            raise BenchmarkError(f"run metadata {index} run id is invalid or duplicate")
        seen.add(run_id)
        validation = validations_by_run[run_id]
        if any(
            record.get(field) != validation[field]
            for field in ("case_id", "condition", "attempt")
        ):
            raise BenchmarkError(f"run metadata {index} does not match validation")
        if record["case_id"] not in cases:
            raise BenchmarkError(f"run metadata {index} has unknown case")
        for field in ("started_at", "completed_at"):
            timestamp = _text(record.get(field), f"run metadata {index} {field}")
            try:
                parsed = datetime.strptime(
                    timestamp, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
            except ValueError as error:
                raise BenchmarkError(f"run metadata {index} {field} is not canonical UTC") from error
            record[f"_{field}"] = parsed
        if record["_completed_at"] < record["_started_at"]:
            raise BenchmarkError(f"run metadata {index} completion precedes start")
        runs.append(record)
    if seen != set(validations_by_run):
        raise BenchmarkError("run metadata identities do not match validations")
    return cases, runs


def _numeric_summary(values: Sequence[float]) -> dict:
    if not values:
        return {"available": False}
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "total": sum(values),
    }


def _operational_diagnostics(runs: Sequence[Mapping[str, object]]) -> dict:
    latency: dict[str, list[float]] = {condition: [] for condition in CONDITIONS}
    usage: dict[str, dict[str, list[float]]] = {
        name: {condition: [] for condition in CONDITIONS}
        for name in ("input_tokens", "output_tokens", "total_tokens")
    }
    costs = {condition: [] for condition in CONDITIONS}
    research = {condition: [] for condition in CONDITIONS}
    tool_calls: dict[str, list[str]] = {condition: [] for condition in CONDITIONS}
    tool_runs = {condition: 0 for condition in CONDITIONS}
    for record in runs:
        condition = record["condition"]
        latency[condition].append(
            (record["_completed_at"] - record["_started_at"]).total_seconds()
        )
        usage_value = record.get("usage")
        if isinstance(usage_value, Mapping):
            for name in usage:
                if name in usage_value:
                    value = _finite(usage_value[name], f"run {record['run_id']} {name}")
                    if value < 0:
                        raise BenchmarkError(
                            f"run {record['run_id']} {name} must be non-negative"
                        )
                    usage[name][condition].append(value)
            if "cost_usd" in usage_value:
                value = _finite(
                    usage_value["cost_usd"], f"run {record['run_id']} cost_usd"
                )
                if value < 0:
                    raise BenchmarkError(f"run {record['run_id']} cost_usd must be non-negative")
                costs[condition].append(value)
        telemetry = record.get("telemetry")
        if isinstance(telemetry, Mapping):
            if "tools_invoked" in telemetry:
                tools = telemetry["tools_invoked"]
                if not isinstance(tools, list) or any(
                    type(name) is not str or not name for name in tools
                ):
                    raise BenchmarkError(f"run {record['run_id']} tools_invoked is invalid")
                tool_calls[condition].extend(tools)
                tool_runs[condition] += 1
            if "research_calls" in telemetry:
                value = _finite(
                    telemetry["research_calls"],
                    f"run {record['run_id']} research_calls",
                )
                if value < 0:
                    raise BenchmarkError(f"run {record['run_id']} research_calls must be non-negative")
                research[condition].append(value)

    def metric(by_condition: Mapping[str, Sequence[float]]) -> dict:
        summaries = {
            condition: _numeric_summary(by_condition[condition])
            for condition in CONDITIONS
        }
        return {
            "available": any(
                value.get("available") is not False for value in summaries.values()
            ),
            "by_condition": summaries,
        }

    tools_by_condition = {}
    for condition in CONDITIONS:
        calls = tool_calls[condition]
        tools_by_condition[condition] = (
            {"available": False}
            if not tool_runs[condition]
            else {
                "calls": len(calls),
                "runs": tool_runs[condition],
                "unique": sorted(set(calls)),
            }
        )
    return {
        "latency_seconds": metric(latency),
        "usage": {name: metric(values) for name, values in usage.items()},
        "cost_usd": metric(costs),
        "tools": {
            "available": any(
                value.get("available") is not False
                for value in tools_by_condition.values()
            ),
            "by_condition": tools_by_condition,
        },
        "research_calls": metric(research),
    }


def _paired_mean_difference(pairs: Sequence[Pair]) -> float:
    return sum(pair.suite_value - pair.normal_value for pair in pairs) / len(pairs)


def _preference_scorecard(items: Sequence[Mapping[str, object]], seed: int) -> dict:
    if not items:
        return {"available": False}
    outcomes = [_condition_ordinal(item, "suite", "normal") for item in items]
    pairs = [
        Pair(item["case_id"], item["attempt"], float(outcome), 0.0)
        for item, outcome in zip(items, outcomes)
    ]
    non_tied_interval = paired_bootstrap(
        pairs,
        lambda sampled: non_tied_win_rate([
            pair.suite_value - pair.normal_value for pair in sampled
        ]),
        seed=seed,
    )
    return {
        "available": True,
        "case_attempts": len(items),
        "suite_wins": sum(value > 0 for value in outcomes),
        "normal_wins": sum(value < 0 for value in outcomes),
        "ties": sum(value == 0 for value in outcomes),
        "ordinal_sum": sum(outcomes),
        "non_tied_win_rate": non_tied_win_rate(outcomes),
        "paired_bootstrap": non_tied_interval,
        "paired_bootstrap_estimand": "non_tied_win_rate",
    }


def _scorecards(
    items: Sequence[Mapping[str, object]],
    cases: Mapping[str, Mapping[str, object]],
    validations: Sequence[Mapping[str, object]],
    seed: int,
) -> dict:
    primaries = [item for item in items if item["repeat_of"] is None]

    def strata(field: str, values: Sequence[str]) -> dict:
        return {
            value: _preference_scorecard(
                [item for item in primaries if cases[item["case_id"]][field] == value],
                seed,
            )
            for value in values
        }

    dimensions: dict[str, dict] = {}
    for dimension in sorted(MQM_DIMENSIONS):
        totals = {condition: 0 for condition in CONDITIONS}
        pairs: list[Pair] = []
        for item in primaries:
            per_condition = {condition: 0 for condition in item["labels"].values()}
            for finding in item["mqm"]:
                if finding["dimension"] != dimension:
                    continue
                condition = item["labels"][finding["output"]]
                points = SEVERITY_POINTS[finding["severity"]]
                totals[condition] += points
                per_condition[condition] += points
            pairs.append(Pair(
                item["case_id"], item["attempt"],
                float(per_condition["normal"]), float(per_condition["suite"]),
            ))
        dimensions[dimension] = {
            "available": True,
            "mqm_points": totals,
            "paired_difference": paired_bootstrap(
                pairs, _paired_mean_difference, seed=seed
            ),
        }

    invariant_names = sorted({
        invariant
        for validation in validations
        for invariant in validation["applicable_invariants"]
    })
    invariants: dict[str, dict] = {}
    validations_by_identity = {
        (record["case_id"], record["attempt"], record["condition"]): record
        for record in validations
    }
    for invariant in invariant_names:
        applicable = {condition: 0 for condition in CONDITIONS}
        passed = {condition: 0 for condition in CONDITIONS}
        failures = {condition: 0 for condition in CONDITIONS}
        validator_errors = {condition: 0 for condition in CONDITIONS}
        for record in validations:
            condition = record["condition"]
            applicable_count = record["applicable_invariants"].count(invariant)
            failure_count = sum(
                finding["invariant"] == invariant
                for finding in record["findings"]
            )
            validator_error_count = sum(
                error.startswith(f"{invariant}:")
                for error in record["validator_errors"]
            )
            applicable[condition] += applicable_count
            failures[condition] += failure_count
            validator_errors[condition] += validator_error_count
            passed[condition] += (
                applicable_count - failure_count - validator_error_count
            )

        pairs: list[Pair] = []
        for item in primaries:
            per_condition = {}
            for condition in item["labels"].values():
                record = validations_by_identity[(item["case_id"], item["attempt"], condition)]
                if invariant not in record["applicable_invariants"]:
                    continue
                per_condition[condition] = sum(
                    finding["invariant"] == invariant
                    for finding in record["findings"]
                )
            if not {"normal", "suite"} <= set(per_condition):
                continue
            pairs.append(Pair(
                item["case_id"], item["attempt"],
                float(per_condition["normal"]), float(per_condition["suite"]),
            ))
        paired_failures = paired_bootstrap(
            pairs, _paired_mean_difference, seed=seed
        )
        invariants[invariant] = {
            "available": True,
            "applicable": applicable,
            "passed": passed,
            "failures": failures,
            "validator_errors": validator_errors,
            "pass_rate": {
                condition: _rate(passed[condition], applicable[condition])
                for condition in CONDITIONS
            },
            "paired_case_attempts": len(pairs),
            "paired_failure_difference_normal_minus_suite": paired_failures,
            "paired_difference": paired_failures,
        }
    return {
        "overall": _preference_scorecard(primaries, seed),
        "task": strata("task", TASKS),
        "surface": strata("surface", SURFACES),
        "difficulty": strata("difficulty", DIFFICULTIES),
        "error_dimension": dimensions,
        "invariant": invariants,
    }


def _consistency(items: Sequence[Mapping[str, object]]) -> dict:
    by_id = {item["item_id"]: item for item in items}
    repeats = [item for item in items if item["repeat_of"] is not None]
    exact = 0
    quadratic = 0.0
    major_equal = major_total = 0
    repeat_targets: set[str] = set()
    for repeat in repeats:
        original = by_id.get(repeat["repeat_of"])
        if original is None or original["repeat_of"] is not None:
            raise BenchmarkError(f"repeat {repeat['item_id']} has invalid original")
        if (
            repeat["case_id"], repeat["attempt"], repeat["task"]
        ) != (
            original["case_id"], original["attempt"], original["task"]
        ):
            raise BenchmarkError(f"repeat {repeat['item_id']} identity differs from original")
        if repeat["repeat_of"] in repeat_targets:
            raise BenchmarkError(f"multiple repeats name original {repeat['repeat_of']}")
        repeat_targets.add(repeat["repeat_of"])
        if set(repeat["labels"].values()) != set(original["labels"].values()):
            raise BenchmarkError(f"repeat {repeat['item_id']} condition set differs from original")
        original_ordinal = _condition_ordinal(original, "suite", "normal")
        repeat_ordinal = _condition_ordinal(repeat, "suite", "normal")
        exact += int(original_ordinal == repeat_ordinal)
        quadratic += 1.0 - ((original_ordinal - repeat_ordinal) / 4.0) ** 2
        original_major = _condition_major(original)
        repeat_major = _condition_major(repeat)
        for condition in sorted(original_major):
            major_equal += int(original_major[condition] == repeat_major[condition])
            major_total += 1
    count = len(repeats)
    return {
        "repeats": count,
        "exact_five_level_agreement": _rate(exact, count) if count else None,
        "quadratic_weighted_agreement": _rate(quadratic, count) if count else None,
        "quadratic_weighted_agreement_method": "direct_mean_not_chance_corrected_kappa",
        "major_or_worse_agreement": _rate(major_equal, major_total) if count else None,
    }


def score_evidence(evidence: Mapping[str, object]) -> dict:
    """Score an exact, verified and condition-decoded benchmark evidence document."""
    fields = {
        "schema_version", "bootstrap_seed", "items", "validations",
        "seeded_errors", "review_mappings", "learned_metrics", "cases", "runs",
    }
    if not isinstance(evidence, Mapping) or set(evidence) != fields:
        raise BenchmarkError("scoring evidence fields are invalid")
    if type(evidence.get("schema_version")) is not int or evidence["schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError("scoring evidence schema version mismatch")
    seed = _exact_int(evidence.get("bootstrap_seed"), "bootstrap seed")
    raw_items = evidence.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise BenchmarkError("scoring evidence items must be a non-empty list")
    items = [_validate_item(value, index) for index, value in enumerate(raw_items)]
    by_item: dict[str, dict] = {}
    primary_identities: set[tuple[str, int]] = set()
    expected_validation_identities: set[tuple[str, str, int]] = set()
    for item in items:
        item_id = item["item_id"]
        if item_id in by_item:
            raise BenchmarkError(f"duplicate score item id: {item_id}")
        by_item[item_id] = item
        if item["repeat_of"] is None:
            primary_identity = (item["case_id"], item["attempt"])
            if primary_identity in primary_identities:
                raise BenchmarkError(f"duplicate primary case-attempt: {primary_identity!r}")
            primary_identities.add(primary_identity)
            expected_validation_identities.update(
                (item["case_id"], condition, item["attempt"])
                for condition in item["labels"].values()
            )
    validations, validations_by_run = _validate_validations(evidence.get("validations"))
    actual_validation_identities = {
        (record["case_id"], record["condition"], record["attempt"])
        for record in validations
    }
    if actual_validation_identities != expected_validation_identities:
        missing = sorted(expected_validation_identities - actual_validation_identities)
        unknown = sorted(actual_validation_identities - expected_validation_identities)
        raise BenchmarkError(
            f"validation identities mismatch: missing={missing!r}, unknown={unknown!r}"
        )
    cases, runs = _validate_cases_and_runs(
        evidence.get("cases"), evidence.get("runs"), validations_by_run,
        primary_identities,
    )
    for item in items:
        if cases[item["case_id"]]["task"] != item["task"]:
            raise BenchmarkError(f"score item {item['item_id']} task differs from case metadata")

    seeded_errors = evidence.get("seeded_errors")
    if not isinstance(seeded_errors, Mapping):
        raise BenchmarkError("seeded_errors must be an object")
    translation_items = [
        item for item in items
        if item["repeat_of"] is None and item["task"] == "translation"
    ]
    review_items = [
        item for item in items
        if item["repeat_of"] is None and item["task"] == "review"
    ]
    translation_identities = {(item["case_id"], item["attempt"]) for item in translation_items}
    review_identities = {(item["case_id"], item["attempt"]) for item in review_items}

    outcomes = [_condition_ordinal(item, "suite", "normal") for item in translation_items]
    bootstrap_pairs = [
        Pair(item["case_id"], item["attempt"], float(outcome), 0.0)
        for item, outcome in zip(translation_items, outcomes)
    ]
    if bootstrap_pairs:
        bootstrap = paired_bootstrap(
            bootstrap_pairs,
            lambda pairs: non_tied_win_rate(
                [pair.suite_value - pair.normal_value for pair in pairs]
            ),
            seed=seed,
        )
    else:
        bootstrap = {
            "estimate": 0.0, "lower_95": 0.0, "upper_95": 0.0,
            "seed": seed, "draws": 0,
        }
    mqm_points = {condition: 0 for condition in CONDITIONS}
    critical_mqm = {condition: 0 for condition in CONDITIONS}
    words = {condition: 0 for condition in CONDITIONS}
    mqm_pairs: list[dict] = []
    for item in translation_items:
        per_item = {condition: 0 for condition in item["labels"].values()}
        for label, condition in item["labels"].items():
            words[condition] += item["word_counts"][label]
        for finding in item["mqm"]:
            condition = item["labels"][finding["output"]]
            points = SEVERITY_POINTS[finding["severity"]]
            mqm_points[condition] += points
            per_item[condition] += points
            critical_mqm[condition] += int(finding["severity"] == "critical")
        mqm_pairs.append({
            "case_id": item["case_id"],
            "attempt": item["attempt"],
            "suite": per_item["suite"],
            "normal": per_item["normal"],
        })
    structural, structural_critical, translation_regressions = _structural_summary(
        validations, translation_identities
    )
    translation = {
        "case_attempts": len(translation_items),
        "suite_wins": sum(outcome > 0 for outcome in outcomes),
        "normal_wins": sum(outcome < 0 for outcome in outcomes),
        "ties": sum(outcome == 0 for outcome in outcomes),
        "ordinal_sum": sum(outcomes),
        "non_tied_win_rate": non_tied_win_rate(outcomes),
        "bootstrap": bootstrap,
        "bootstrap_lower": bootstrap["lower_95"],
        "mqm_case_attempt_points": mqm_pairs,
        "mqm_points": mqm_points,
        "mqm_points_per_1000_words": {
            condition: 1000.0 * _rate(mqm_points[condition], words[condition])
            for condition in CONDITIONS
        },
        "mqm_reduction": (
            _rate(mqm_points["normal"] - mqm_points["suite"], mqm_points["normal"])
            if mqm_points["normal"] else 0.0
        ),
        "suite_critical": critical_mqm["suite"] + structural_critical["suite"],
        "normal_critical": critical_mqm["normal"] + structural_critical["normal"],
        "structural_pass_rate": structural,
        "suite_structural_pass_rate": structural["suite"],
        "critical_invariant_regressions": translation_regressions,
    }
    review = _review_metrics(
        evidence.get("review_mappings"),
        seeded_errors,
        validations,
        review_identities,
        validations_by_run,
    )
    return {
        "translation": translation,
        "review": review,
        "consistency": _consistency(items),
        "learned_metrics": _learned_metrics(
            evidence.get("learned_metrics"), validations_by_run
        ),
        "scorecards": _scorecards(items, cases, validations, seed),
        "operational": _operational_diagnostics(runs),
    }


_TRANSLATION_GATE_INPUTS = {
    "non_tied_win_rate", "bootstrap_lower", "mqm_reduction", "suite_critical",
    "normal_critical", "suite_structural_pass_rate",
    "critical_invariant_regressions",
}
_TRANSLATION_SCORE_FIELDS = _TRANSLATION_GATE_INPUTS | {
    "case_attempts", "suite_wins", "normal_wins", "ties", "ordinal_sum",
    "bootstrap", "mqm_case_attempt_points", "mqm_points",
    "mqm_points_per_1000_words", "structural_pass_rate",
}
_REVIEW_GATE_INPUTS = {
    "available", "required_error_recall", "false_positive_correction_rate",
    "correction_success_rate", "introduced_error_rate", "critical_misses",
    "structural_pass_rate", "critical_invariant_regressions",
}


def evaluate_gates(metrics: Mapping[str, object]) -> dict:
    """Apply the pre-registered gates exactly, with no override mechanism."""
    allowed_top = {
        "translation", "review", "consistency", "learned_metrics",
        "scorecards", "operational",
    }
    if (
        not isinstance(metrics, Mapping)
        or not {"translation", "review"} <= set(metrics)
        or not set(metrics) <= allowed_top
    ):
        raise BenchmarkError("gate metrics fields are invalid")
    translation = metrics.get("translation")
    if (
        not isinstance(translation, Mapping)
        or not _TRANSLATION_GATE_INPUTS <= set(translation)
        or not set(translation) <= _TRANSLATION_SCORE_FIELDS
    ):
        raise BenchmarkError("translation gate fields are invalid")
    values = {
        name: _finite(translation.get(name), f"translation {name}")
        for name in _TRANSLATION_GATE_INPUTS
    }
    translation_checks = {
        "non_tied_win_rate_at_least_60pct": values["non_tied_win_rate"] >= 0.60,
        "bootstrap_lower_above_50pct": values["bootstrap_lower"] > 0.50,
        "mqm_reduction_at_least_25pct": values["mqm_reduction"] >= 0.25,
        "no_critical_or_hard_failure_increase": (
            values["suite_critical"] <= values["normal_critical"]
        ),
        "suite_structural_at_least_99pct": (
            values["suite_structural_pass_rate"] >= 0.99
        ),
        "no_critical_invariant_regression": (
            values["critical_invariant_regressions"] == 0
        ),
    }
    translation_gate = {
        "passed": all(translation_checks.values()),
        "checks": translation_checks,
    }

    review = metrics.get("review")
    if not isinstance(review, Mapping) or type(review.get("available")) is not bool:
        raise BenchmarkError("review gate fields are invalid")
    if not review["available"]:
        if set(review) - {"available", "reason", "unresolved"}:
            raise BenchmarkError("unavailable review gate fields are invalid")
        review_gate = {
            "available": False,
            "passed": None,
            "checks": {},
            "reason": "review mappings unavailable",
        }
        overall = {"passed": None, "verdict": "unavailable"}
        return {"translation": translation_gate, "review": review_gate, "overall": overall}
    unresolved = review.get("unresolved", 0)
    if type(unresolved) is not int or unresolved < 0:
        raise BenchmarkError("review unresolved must be a non-negative integer")
    if unresolved:
        review_gate = {
            "available": False,
            "passed": None,
            "checks": {},
            "reason": "review mappings contain unresolved findings",
        }
        return {
            "translation": translation_gate,
            "review": review_gate,
            "overall": {"passed": None, "verdict": "unavailable"},
        }
    if not _REVIEW_GATE_INPUTS <= set(review):
        raise BenchmarkError("review gate fields are invalid")

    def condition_values(name: str) -> tuple[float, float]:
        value = review.get(name)
        if not isinstance(value, Mapping) or not {"suite", "normal"} <= set(value):
            raise BenchmarkError(f"review {name} must contain suite and normal")
        return (
            _finite(value["suite"], f"review {name} suite"),
            _finite(value["normal"], f"review {name} normal"),
        )

    suite_recall, normal_recall = condition_values("required_error_recall")
    suite_false_positive, normal_false_positive = condition_values(
        "false_positive_correction_rate"
    )
    suite_correction, normal_correction = condition_values("correction_success_rate")
    suite_introduced, normal_introduced = condition_values("introduced_error_rate")
    suite_misses, normal_misses = condition_values("critical_misses")
    suite_structural, normal_structural = condition_values("structural_pass_rate")
    regressions = _finite(
        review.get("critical_invariant_regressions"),
        "review critical_invariant_regressions",
    )
    review_checks = {
        "required_error_recall_improvement_at_least_15pp": (
            suite_recall - normal_recall >= 0.15
        ),
        "false_positive_correction_increase_at_most_5pp": (
            suite_false_positive - normal_false_positive <= 0.05
        ),
        "correction_success_strictly_improves": suite_correction > normal_correction,
        "introduced_error_rate_does_not_increase": suite_introduced <= normal_introduced,
        "critical_misses_are_fewer": suite_misses < normal_misses,
        "structural_checks_do_not_regress": (
            suite_structural >= normal_structural and regressions == 0
        ),
    }
    review_gate = {
        "available": True,
        "passed": all(review_checks.values()),
        "checks": review_checks,
    }
    passed = translation_gate["passed"] and review_gate["passed"]
    return {
        "translation": translation_gate,
        "review": review_gate,
        "overall": {"passed": passed, "verdict": "passed" if passed else "failed"},
    }


def _read_bounded(path: Path, description: str, *, limit: int) -> bytes:
    path = Path(path)
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise BenchmarkError(f"refusing symlink {description}: {path}")
        if not stat.S_ISREG(metadata.st_mode):
            raise BenchmarkError(f"{description} must be a regular file")
        if metadata.st_size > limit:
            raise BenchmarkError(f"{description} exceeds {limit} bytes")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise BenchmarkError(f"{description} must be a regular file")
            encoded = source.read(limit + 1)
        if len(encoded) > limit:
            raise BenchmarkError(f"{description} exceeds {limit} bytes")
        return encoded
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot read {description}: {error}") from error


def _canonical_json_file(path: Path, description: str, *, limit: int) -> object:
    return _parse_canonical_json(_read_bounded(path, description, limit=limit), description)


def _canonical_jsonl_file(path: Path, description: str, *, limit: int) -> list[dict]:
    return _parse_canonical_jsonl(_read_bounded(path, description, limit=limit), description)


def _load_runs(
    evidence_dir: Path,
    run_manifest: Mapping[str, object],
) -> list[dict]:
    schedule = run_manifest.get("schedule")
    if not isinstance(schedule, Mapping) or set(schedule) != {"run_ids", "sha256"}:
        raise BenchmarkError("run manifest has no canonical schedule")
    run_ids = schedule.get("run_ids")
    if not isinstance(run_ids, list):
        raise BenchmarkError("run manifest schedule run_ids must be a list")
    records = _canonical_jsonl_file(
        evidence_dir / "runs.jsonl", "runs.jsonl", limit=_MAX_DOCUMENT_INPUT_BYTES
    )
    by_id: dict[str, dict] = {}
    for record in records:
        try:
            run = RunResult.from_record(record)
        except (TypeError, ValueError) as error:
            raise BenchmarkError(f"malformed run record: {error}") from error
        run_id = run.run_id
        if run_id in by_id:
            raise BenchmarkError(f"duplicate run id: {run_id}")
        by_id[run_id] = record
    if set(by_id) != set(run_ids) or len(run_ids) != len(set(run_ids)):
        missing = sorted(set(run_ids) - set(by_id))
        unknown = sorted(set(by_id) - set(run_ids))
        raise BenchmarkError(f"complete run identities mismatch: missing={missing!r}, unknown={unknown!r}")
    result: list[dict] = []
    raw_cache: dict[str, str] = {}
    for run_id in run_ids:
        record = by_id[run_id]
        digest = record["output_sha256"]
        expected_path = f"raw/{digest}.txt"
        if record["raw_output_path"] != expected_path:
            raise BenchmarkError(f"run {run_id} raw output is not content-addressed")
        if digest not in raw_cache:
            encoded = _read_bounded(
                evidence_dir / expected_path,
                f"raw output {digest}",
                limit=_MAX_DOCUMENT_INPUT_BYTES,
            )
            if sha256_bytes(encoded) != digest:
                raise BenchmarkError(f"raw output hash mismatch: {expected_path}")
            try:
                raw_cache[digest] = encoded.decode("utf-8")
            except UnicodeDecodeError as error:
                raise BenchmarkError(f"raw output is not UTF-8: {expected_path}") from error
        complete = {**record, "output": raw_cache[digest]}
        _validate_run_record(complete)
        result.append(complete)
    return result


def _verify_annotation_paths(paths: ScorePaths) -> dict[str, dict]:
    annotations = Path(paths.annotations)
    annotation_lock = Path(paths.annotation_lock)
    if (
        annotations.name != "annotations.jsonl"
        or annotation_lock.name != "annotation-lock.json"
        or annotations.parent.resolve() != annotation_lock.parent.resolve()
    ):
        raise BenchmarkError("annotation paths must name canonical artifacts in one directory")
    _canonical_jsonl_file(annotations, "annotations", limit=_MAX_DOCUMENT_INPUT_BYTES)
    _canonical_json_file(annotation_lock, "annotation lock", limit=_MAX_SMALL_INPUT_BYTES)
    state_path = annotations.parent / "annotation-state.json"
    _canonical_json_file(state_path, "annotation state", limit=_MAX_DOCUMENT_INPUT_BYTES)
    try:
        store = ReviewStore(paths.review_bundle, annotations.parent)
    except BenchmarkError:
        raise
    except (ValueError, RecursionError, OverflowError, TypeError, UnicodeError) as error:
        raise BenchmarkError(f"invalid annotation artifacts: {error}") from error
    try:
        if not store.locked or len(store.latest) != len(store.bundle["items"]):
            raise BenchmarkError("annotation lock covers an incomplete queue")
        if store.events_path.resolve() != annotations.resolve():
            raise BenchmarkError("annotation event path mismatch")
        if store.lock_path.resolve() != annotation_lock.resolve():
            raise BenchmarkError("annotation lock path mismatch")
        return {item_id: dict(record) for item_id, record in store.latest.items()}
    finally:
        store.close()


def _load_verified_condition_key(
    paths: ScorePaths,
    *,
    bundle: Mapping[str, object],
    cases: Sequence[Mapping[str, object]],
    runs: Sequence[Mapping[str, object]],
    prepared_provenance: Mapping[str, object],
) -> dict:
    """Load private labels only after the caller has verified the annotation lock."""
    key = _canonical_json_file(
        paths.condition_key,
        "condition key",
        limit=_MAX_DOCUMENT_INPUT_BYTES,
    )
    if not isinstance(key, dict):
        raise BenchmarkError("condition key must be an object")
    provenance = key.get("provenance")
    if not isinstance(provenance, Mapping):
        raise BenchmarkError("condition key provenance is malformed")
    seed = provenance.get("blinding_seed")
    _exact_int(seed, "condition key blinding seed")
    expected_bundle, expected_key = build_blind_bundle(
        runs,
        cases,
        seed,
        prepared_provenance=prepared_provenance,
    )
    if canonical_bytes(bundle) != canonical_bytes(expected_bundle):
        raise BenchmarkError("review bundle differs from regenerated frozen evidence")
    _validate_condition_key(
        key,
        bundle,
        expected_key=expected_key,
        prepared_provenance=prepared_provenance,
    )
    return key


def _load_verified_inputs(paths: ScorePaths) -> dict:
    if not isinstance(paths, ScorePaths):
        raise BenchmarkError("score paths must be ScorePaths")
    dataset_dir = Path(paths.dataset_dir)
    evidence_dir = Path(paths.evidence_dir)
    # Strict bounded canonical parsing precedes every legacy semantic verifier.
    # It converts hostile integer/depth/encoding shapes into BenchmarkError at
    # the trust boundary instead of allowing implementation exceptions to leak.
    for path, description in (
        (dataset_dir / "dataset-manifest.json", "dataset manifest"),
        (dataset_dir / "seeded-errors.json", "seeded-errors.json"),
        (dataset_dir / "reference-signoff.json", "reference signoff"),
        (evidence_dir / "run-manifest.json", "run manifest"),
        (Path(paths.review_bundle), "review bundle"),
    ):
        _canonical_json_file(
            path, description, limit=_MAX_DOCUMENT_INPUT_BYTES
        )
    for path, description in (
        (dataset_dir / "cases.jsonl", "cases.jsonl"),
        (evidence_dir / "runs.jsonl", "runs.jsonl"),
    ):
        _canonical_jsonl_file(
            path, description, limit=_MAX_DOCUMENT_INPUT_BYTES
        )
    try:
        verify_dataset_manifest(dataset_dir)
        run_manifest, prepared = _validate_prepared_manifest(dataset_dir, evidence_dir)
    except BenchmarkError:
        raise
    except (ValueError, RecursionError, OverflowError, TypeError, UnicodeError) as error:
        raise BenchmarkError(f"invalid frozen benchmark inputs: {error}") from error
    cases = _canonical_jsonl_file(
        dataset_dir / "cases.jsonl", "cases.jsonl", limit=_MAX_DOCUMENT_INPUT_BYTES
    )
    seeded_errors = _canonical_json_file(
        dataset_dir / "seeded-errors.json",
        "seeded-errors.json",
        limit=_MAX_DOCUMENT_INPUT_BYTES,
    )
    runs = _load_runs(evidence_dir, run_manifest)
    bundle = _canonical_json_file(
        paths.review_bundle,
        "review bundle",
        limit=_MAX_DOCUMENT_INPUT_BYTES,
    )
    if not isinstance(bundle, dict):
        raise BenchmarkError("review bundle must be an object")
    scan_visible_bundle(bundle)

    # This is the trust boundary: lock, hashes, event history, latest revisions,
    # and queue completeness are verified before the condition key is opened.
    latest = _verify_annotation_paths(paths)
    key = _load_verified_condition_key(
        paths,
        bundle=bundle,
        cases=cases,
        runs=runs,
        prepared_provenance=prepared,
    )
    _canonical_json_file(
        dataset_dir / "dataset-manifest.json", "dataset manifest",
        limit=_MAX_DOCUMENT_INPUT_BYTES,
    )
    _canonical_jsonl_file(
        dataset_dir / "cases.jsonl", "cases.jsonl",
        limit=_MAX_DOCUMENT_INPUT_BYTES,
    )
    _canonical_json_file(
        evidence_dir / "run-manifest.json", "run manifest",
        limit=_MAX_DOCUMENT_INPUT_BYTES,
    )
    _canonical_jsonl_file(
        evidence_dir / "runs.jsonl", "runs.jsonl",
        limit=_MAX_DOCUMENT_INPUT_BYTES,
    )
    try:
        verify_dataset_manifest(dataset_dir)
        current_manifest, current_prepared = _validate_prepared_manifest(
            dataset_dir, evidence_dir
        )
    except BenchmarkError:
        raise
    except (ValueError, RecursionError, OverflowError, TypeError, UnicodeError) as error:
        raise BenchmarkError(f"invalid frozen benchmark inputs: {error}") from error
    if current_manifest != run_manifest or current_prepared != prepared:
        raise BenchmarkError("frozen inputs changed during scoring verification")
    return {
        "run_manifest": run_manifest,
        "prepared": prepared,
        "cases": cases,
        "seeded_errors": seeded_errors,
        "runs": runs,
        "bundle": bundle,
        "key": key,
        "latest": latest,
    }


def verify_locked_inputs(paths: ScorePaths) -> None:
    """Verify every frozen and locked artifact before condition decoding."""
    _load_verified_inputs(paths)


def _normalized_validations(
    evidence_dir: Path,
    runs: Sequence[Mapping[str, object]],
    cases: Sequence[Mapping[str, object]],
    *,
    encoded: bytes | None = None,
) -> list[dict]:
    fields = {
        "schema_version", "run_id", "case_id", "status", "output", "findings",
        "validator_errors", "applicable_checks", "passed_checks", "failed_checks",
        "validator_error_checks",
    }
    records = (
        _parse_canonical_jsonl(encoded, "validation.jsonl")
        if encoded is not None
        else _canonical_jsonl_file(
            evidence_dir / "validation.jsonl",
            "validation.jsonl",
            limit=_MAX_DOCUMENT_INPUT_BYTES,
        )
    )
    runs_by_id = {run["run_id"]: run for run in runs}
    invariants_by_case: dict[str, list[str]] = {}
    for case in cases:
        checks = case.get("automatic_checks")
        if not isinstance(checks, list):
            raise BenchmarkError(
                f"case {case.get('id')} automatic_checks must be a list"
            )
        invariants: list[str] = []
        for index, check in enumerate(checks):
            check_type = check.get("type") if isinstance(check, Mapping) else None
            if type(check_type) is not str or check_type not in CHECKS:
                raise BenchmarkError(
                    f"case {case.get('id')} automatic check {index} "
                    "has no scoreable invariant identity"
                )
            invariants.append(check_type)
        invariants_by_case[case["id"]] = invariants
    result: list[dict] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if set(record) != fields:
            raise BenchmarkError(f"validation source {index} fields are invalid")
        if type(record.get("schema_version")) is not int or record["schema_version"] != SCHEMA_VERSION:
            raise BenchmarkError(f"validation source {index} schema version mismatch")
        run_id = record.get("run_id")
        if type(run_id) is not str or run_id not in runs_by_id or run_id in seen:
            raise BenchmarkError(f"validation source {index} run id is invalid or duplicate")
        seen.add(run_id)
        run = runs_by_id[run_id]
        if record.get("case_id") != run["case_id"] or record.get("output") != run["output"]:
            raise BenchmarkError(f"validation source {index} does not match frozen run")
        result.append({
            "run_id": run_id,
            "case_id": record["case_id"],
            "condition": run["condition"],
            "attempt": run["attempt"],
            "status": record["status"],
            "findings": record["findings"],
            "validator_errors": record["validator_errors"],
            "applicable_checks": record["applicable_checks"],
            "passed_checks": record["passed_checks"],
            "failed_checks": record["failed_checks"],
            "validator_error_checks": record["validator_error_checks"],
            "applicable_invariants": list(invariants_by_case[record["case_id"]]),
        })
    if set(runs_by_id) != seen:
        missing = sorted(set(runs_by_id) - seen)
        raise BenchmarkError(f"validation identities mismatch: missing={missing!r}, unknown=[]")
    return result


def _optional_jsonl(
    evidence_dir: Path,
    name: str,
    *,
    encoded: bytes | None = None,
) -> list[dict] | None:
    if encoded is not None:
        return _parse_canonical_jsonl(encoded, name)
    path = evidence_dir / name
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise BenchmarkError(f"cannot inspect optional {name}: {error}") from error
    return _canonical_jsonl_file(path, name, limit=_MAX_DOCUMENT_INPUT_BYTES)


def _normalized_scoring_evidence(
    paths: ScorePaths,
    loaded: Mapping[str, object],
    derived_inputs: Mapping[str, bytes] | None = None,
) -> dict:
    held_derived_set = derived_inputs is not None
    derived_inputs = {} if derived_inputs is None else derived_inputs
    cases_by_id = {case["id"]: case for case in loaded["cases"]}
    bundle_by_id = {item["id"]: item for item in loaded["bundle"]["items"]}
    key_items = loaded["key"]["items"]
    latest = loaded["latest"]
    items: list[dict] = []
    if set(bundle_by_id) != set(key_items) or set(bundle_by_id) != set(latest):
        raise BenchmarkError("locked annotation item IDs do not match blinded evidence")
    for item_id in [item["id"] for item in loaded["bundle"]["items"]]:
        visible = bundle_by_id[item_id]
        hidden = key_items[item_id]
        annotation = latest[item_id]
        case_id = hidden["case_id"]
        if case_id not in cases_by_id:
            raise BenchmarkError(f"condition key has unknown case id: {case_id}")
        labels = dict(hidden["labels"])
        items.append({
            "item_id": item_id,
            "case_id": case_id,
            "attempt": hidden["attempt"],
            "task": cases_by_id[case_id]["task"],
            "repeat_of": hidden.get("repeat_of"),
            "labels": labels,
            "comparisons": dict(annotation["comparisons"]),
            "mqm": [
                {
                    "output": finding["output"],
                    "dimension": finding["dimension"],
                    "severity": finding["severity"],
                }
                for finding in annotation["mqm"]
            ],
            "major_or_worse": dict(annotation["major_or_worse"]),
            "word_counts": {
                label: len(visible["outputs"][label].split())
                for label in labels
            },
        })
    normalized_seeded = {
        case_id: [
            {
                "id": error["id"],
                "severity": error["severity"],
                "correction_required": error["correction_required"],
            }
            for error in errors
        ]
        for case_id, errors in loaded["seeded_errors"].items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "bootstrap_seed": loaded["run_manifest"]["bootstrap_seed"],
        "cases": [
            {
                "case_id": case["id"],
                "task": case["task"],
                "surface": case["surface"],
                "difficulty": case["difficulty"],
            }
            for case in loaded["cases"]
        ],
        "runs": [
            {
                "run_id": run["run_id"],
                "case_id": run["case_id"],
                "condition": run["condition"],
                "attempt": run["attempt"],
                "started_at": run["started_at"],
                "completed_at": run["completed_at"],
                "telemetry": run["telemetry"],
                "usage": run["usage"],
            }
            for run in loaded["runs"]
        ],
        "items": items,
        "validations": _normalized_validations(
            Path(paths.evidence_dir), loaded["runs"], loaded["cases"],
            encoded=derived_inputs.get("validation.jsonl"),
        ),
        "seeded_errors": normalized_seeded,
        "review_mappings": (
            None
            if held_derived_set and "review-mappings.jsonl" not in derived_inputs
            else _optional_jsonl(
                Path(paths.evidence_dir), "review-mappings.jsonl",
                encoded=derived_inputs.get("review-mappings.jsonl"),
            )
        ),
        "learned_metrics": (
            None
            if held_derived_set and "learned-metrics.jsonl" not in derived_inputs
            else _optional_jsonl(
                Path(paths.evidence_dir), "learned-metrics.jsonl",
                encoded=derived_inputs.get("learned-metrics.jsonl"),
            )
        ),
    }


def _open_held_derived_inputs(
    evidence_dir: Path,
) -> tuple[int, dict[str, tuple[int, bytes, tuple[int, int, int, int]]]]:
    parent_fd = -1
    held: dict[str, tuple[int, bytes, tuple[int, int, int, int]]] = {}
    try:
        parent_fd = os.open(
            evidence_dir,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        for name, required in (
            ("validation.jsonl", True),
            ("review-mappings.jsonl", False),
            ("learned-metrics.jsonl", False),
        ):
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                if required:
                    raise BenchmarkError(f"missing required scoring input: {name}")
                continue
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                os.close(descriptor)
                raise BenchmarkError(f"derived scoring input must be one regular file: {name}")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(65536, _MAX_DOCUMENT_INPUT_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_DOCUMENT_INPUT_BYTES:
                    os.close(descriptor)
                    raise BenchmarkError(f"{name} exceeds the input size limit")
            encoded = b"".join(chunks)
            after = os.fstat(descriptor)
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
            current = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            if identity != current or (named.st_dev, named.st_ino) != identity[:2]:
                os.close(descriptor)
                raise BenchmarkError(f"derived scoring input changed while acquired: {name}")
            held[name] = (descriptor, encoded, identity)
        return parent_fd, held
    except Exception:
        for descriptor, _encoded, _identity in held.values():
            os.close(descriptor)
        if parent_fd >= 0:
            os.close(parent_fd)
        raise


def _verify_held_derived_inputs(
    parent_fd: int,
    held: Mapping[str, tuple[int, bytes, tuple[int, int, int, int]]],
) -> None:
    for name, (descriptor, encoded, identity) in held.items():
        metadata = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        current = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        if (
            current != identity
            or (named.st_dev, named.st_ino) != identity[:2]
            or metadata.st_nlink != 1
        ):
            raise BenchmarkError(f"derived scoring input changed during scoring: {name}")
        os.lseek(descriptor, 0, os.SEEK_SET)
        current_bytes = b""
        while len(current_bytes) <= _MAX_DOCUMENT_INPUT_BYTES:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            current_bytes += chunk
        if current_bytes != encoded:
            raise BenchmarkError(f"derived scoring input bytes changed during scoring: {name}")


def _close_held_derived_inputs(
    parent_fd: int,
    held: Mapping[str, tuple[int, bytes, tuple[int, int, int, int]]],
) -> None:
    for descriptor, _encoded, _identity in held.values():
        try:
            os.close(descriptor)
        except OSError:
            pass
    if parent_fd >= 0:
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _input_fingerprint(paths: ScorePaths) -> tuple[tuple[str, int, int, int, int, str], ...]:
    candidates: set[Path] = {
        Path(paths.review_bundle),
        Path(paths.condition_key),
        Path(paths.annotations),
        Path(paths.annotation_lock),
        Path(paths.annotations).parent / "annotation-state.json",
    }
    for root in (Path(paths.dataset_dir), Path(paths.evidence_dir)):
        try:
            if root.is_symlink() or not root.is_dir():
                raise BenchmarkError(f"scoring input root must be a real directory: {root}")
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise BenchmarkError(f"scoring input tree must not contain symlinks: {path}")
                if path.is_file():
                    candidates.add(path)
        except BenchmarkError:
            raise
        except OSError as error:
            raise BenchmarkError(f"cannot enumerate scoring input tree {root}: {error}") from error
    result: list[tuple[str, int, int, int, int, str]] = []
    for path in sorted(candidates, key=lambda value: str(value)):
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise BenchmarkError(f"scoring input must be a regular file: {path}")
            result.append((
                str(path.resolve()),
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                sha256_file(path),
            ))
        except BenchmarkError:
            raise
        except OSError as error:
            raise BenchmarkError(f"cannot fingerprint scoring input {path}: {error}") from error
    return tuple(result)


def score_paths(paths: ScorePaths) -> dict:
    parent_fd, held = _open_held_derived_inputs(Path(paths.evidence_dir))
    try:
        before = _input_fingerprint(paths)
        loaded = _load_verified_inputs(paths)
        derived_bytes = {name: value[1] for name, value in held.items()}
        metrics = score_evidence(
            _normalized_scoring_evidence(paths, loaded, derived_bytes)
        )
        provenance = {
            "dataset_sha256": loaded["prepared"]["dataset_sha256"],
            "dataset_manifest_sha256": loaded["prepared"]["dataset_manifest_sha256"],
            "run_manifest_sha256": loaded["prepared"]["run_manifest_sha256"],
            "review_bundle_sha256": sha256_file(Path(paths.review_bundle)),
            "condition_key_sha256": sha256_file(Path(paths.condition_key)),
            "annotations_sha256": sha256_file(Path(paths.annotations)),
            "annotation_lock_sha256": sha256_file(Path(paths.annotation_lock)),
            "validation_sha256": sha256_bytes(derived_bytes["validation.jsonl"]),
            "bootstrap_seed": loaded["run_manifest"]["bootstrap_seed"],
            "claim_evidence": {
                "status": "unavailable",
                "reason": (
                    "independently attested execution receipt and sealed derivation "
                    "inputs are unavailable"
                ),
            },
        }
        for key, name in (
            ("review_mappings_sha256", "review-mappings.jsonl"),
            ("learned_metrics_sha256", "learned-metrics.jsonl"),
        ):
            provenance[key] = (
                sha256_bytes(derived_bytes[name]) if name in derived_bytes else None
            )
        gates = evaluate_gates(metrics)
        gates["overall"] = {"passed": None, "verdict": "unavailable"}
        document = {
            "schema_version": SCHEMA_VERSION,
            "provenance": provenance,
            "metrics": metrics,
            "gates": gates,
        }
        _verify_held_derived_inputs(parent_fd, held)
        if _input_fingerprint(paths) != before:
            raise BenchmarkError("scoring inputs changed while the document was calculated")
        return document
    finally:
        _close_held_derived_inputs(parent_fd, held)


def _validate_output_target(output: Path, paths: ScorePaths) -> Path:
    output = Path(output)
    try:
        try:
            output.lstat()
        except FileNotFoundError:
            pass
        else:
            raise BenchmarkError(f"score output already exists: {output}")
        parent = output.parent.resolve(strict=True)
        if not parent.is_dir():
            raise BenchmarkError(f"score output parent is not a directory: {output.parent}")
        resolved_output = parent / output.name
        protected_roots = (
            Path(paths.dataset_dir).resolve(strict=True),
            Path(paths.evidence_dir).resolve(strict=True),
            Path(paths.annotations).parent.resolve(strict=True),
        )
        for root in protected_roots:
            if resolved_output == root or resolved_output.is_relative_to(root):
                raise BenchmarkError(f"score output is inside consumed input tree: {root}")
        for consumed in (
            paths.review_bundle, paths.condition_key, paths.annotations,
            paths.annotation_lock,
        ):
            if resolved_output == Path(consumed).resolve(strict=True):
                raise BenchmarkError(f"score output aliases consumed input: {consumed}")
        return resolved_output
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot validate score output path: {error}") from error


def _unlink_created_output_if_same(parent_fd: int, output_fd: int, name: str) -> None:
    """Quarantine the current entry before deleting only the inode we created."""
    if parent_fd < 0 or output_fd < 0:
        return
    quarantine = f".{name}.rollback-{uuid4().hex}"
    try:
        created = os.fstat(output_fd)
        os.rename(
            name,
            quarantine,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        moved = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        if (created.st_dev, created.st_ino) != (moved.st_dev, moved.st_ino):
            try:
                os.link(
                    quarantine,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except OSError:
                pass
            else:
                os.unlink(quarantine, dir_fd=parent_fd)
            return
        os.unlink(quarantine, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError:
        return


def _publish_exclusive_json(output: Path, value: object) -> str:
    encoded = canonical_bytes(value)
    resolved_parent = output.parent.resolve(strict=True)
    parent_fd = -1
    output_fd = -1
    created = False
    try:
        parent_fd = os.open(resolved_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        parent_before = os.fstat(parent_fd)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        output_fd = os.open(output.name, flags, 0o600, dir_fd=parent_fd)
        created = True
        offset = 0
        while offset < len(encoded):
            written = os.write(output_fd, encoded[offset:])
            if written <= 0:
                raise OSError("short write while publishing score output")
            offset += written
        os.fsync(output_fd)
        metadata = os.fstat(output_fd)
        linked = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != len(encoded)
            or (metadata.st_dev, metadata.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            raise BenchmarkError("score output publication integrity check failed")
        current_parent = output.parent.stat()
        if (parent_before.st_dev, parent_before.st_ino) != (current_parent.st_dev, current_parent.st_ino):
            raise BenchmarkError("score output parent changed during publication")
        os.fsync(parent_fd)
        return sha256_bytes(encoded)
    except BenchmarkError:
        if created:
            _unlink_created_output_if_same(parent_fd, output_fd, output.name)
        raise
    except OSError as error:
        if created:
            _unlink_created_output_if_same(parent_fd, output_fd, output.name)
        if error.errno == getattr(os, "EEXIST", 17):
            raise BenchmarkError(f"score output already exists: {output}") from error
        raise BenchmarkError(f"cannot publish score output: {error}") from error
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score locked PT-PT benchmark evidence.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--review-bundle", required=True, type=Path)
    parser.add_argument("--condition-key", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--annotation-lock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    paths = ScorePaths(
        dataset_dir=arguments.dataset,
        evidence_dir=arguments.evidence,
        review_bundle=arguments.review_bundle,
        condition_key=arguments.condition_key,
        annotations=arguments.annotations,
        annotation_lock=arguments.annotation_lock,
    )
    try:
        output = _validate_output_target(arguments.output, paths)
        document = score_paths(paths)
        output_sha256 = _publish_exclusive_json(output, document)
        sys.stdout.buffer.write(canonical_bytes({
            "output": str(arguments.output),
            "sha256": output_sha256,
        }))
        return 0
    except (BenchmarkError, ValueError, RecursionError, OverflowError, TypeError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
