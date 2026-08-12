from __future__ import annotations

import argparse
import ctypes
import errno
import math
import os
import re
import secrets
import stat
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .blind import _parse_canonical_json
from .common import BenchmarkError, canonical_bytes, sha256_bytes, sha256_file
from .review_app import MQM_DIMENSIONS
from .schema import CONDITIONS, DIFFICULTIES, SCHEMA_VERSION, SURFACES, TASKS
from .score import evaluate_gates


_MAX_SCORE_BYTES = 8 * 1024 * 1024
_MAX_PROVENANCE_BYTES = 8 * 1024 * 1024
_MAX_CELL_CHARS = 200
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_REPORT_CODE_VERSION = "report-v1"
_SCORING_CODE_VERSION = "score-v1"
_PROVENANCE_TOP_FIELDS = {
    "schema", "scores_sha256", "benchmark", "git", "input_hashes",
    "execution", "seeds", "attempt_history", "raw_outputs",
    "result_bindings", "review_bindings", "code_versions",
}
_INPUT_HASH_FIELDS = {
    "dataset_sha256", "dataset_manifest_sha256", "run_manifest_sha256",
    "source_sha256", "prompt_sha256", "context_sha256", "rubric_sha256",
    "configuration_sha256",
}
_ATTEMPT_FIELDS = {
    "run_id", "attempt", "outcome", "retry_of", "retry_reason",
    "started_at", "completed_at",
}
_ATTEMPT_OUTCOMES = {
    "success", "refusal", "timeout", "malformed_output", "tool_misuse",
    "infrastructure_failure",
}
_HEADINGS = (
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
)
_PROVENANCE_FIELDS = {
    "dataset_sha256", "dataset_manifest_sha256", "run_manifest_sha256",
    "review_bundle_sha256", "condition_key_sha256", "annotations_sha256",
    "annotation_lock_sha256", "validation_sha256", "bootstrap_seed",
    "review_mappings_sha256", "learned_metrics_sha256", "claim_evidence",
}
_TRANSLATION_FIELDS = {
    "case_attempts", "suite_wins", "normal_wins", "ties", "ordinal_sum",
    "non_tied_win_rate", "bootstrap", "bootstrap_lower",
    "mqm_case_attempt_points", "mqm_points", "mqm_points_per_1000_words",
    "mqm_reduction", "suite_critical", "normal_critical",
    "structural_pass_rate", "suite_structural_pass_rate",
    "critical_invariant_regressions",
}
_REVIEW_AVAILABLE_FIELDS = {
    "available", "unresolved", "unresolved_reported", "unresolved_corrected",
    "introduced_error_aggregation", "introduced_error_runs", "response_runs",
    "required_error_recall", "reported_error_precision",
    "correction_success_rate", "false_positive_correction_rate",
    "introduced_error_rate", "critical_misses", "structural_pass_rate",
    "critical_invariant_regressions",
}
_PREFERENCE_CARD_FIELDS = {
    "available", "case_attempts", "suite_wins", "normal_wins", "ties",
    "ordinal_sum", "non_tied_win_rate", "paired_bootstrap",
    "paired_bootstrap_estimand",
}
_SURFACE_NAMES = {
    "ui-mobile": "UI and mobile",
    "web": "Web",
    "marketing": "Marketing",
    "app-store": "App Store",
    "documentation": "Documentation",
}


def _object(value: object, fields: set[str], description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise BenchmarkError(f"{description} fields are invalid")
    return value


def _boolean(value: object, description: str) -> bool:
    if type(value) is not bool:
        raise BenchmarkError(f"{description} must be a boolean")
    return value


def _integer(value: object, description: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise BenchmarkError(f"{description} must be an integer")
    return value


def _number(value: object, description: str) -> float:
    if type(value) not in (int, float):
        raise BenchmarkError(f"{description} must be a finite number")
    try:
        if not math.isfinite(value):
            raise BenchmarkError(f"{description} must be a finite number")
    except (OverflowError, ValueError) as error:
        raise BenchmarkError(f"{description} must be a finite number") from error
    return float(value)


def _text(value: object, description: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise BenchmarkError(f"{description} must be text")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise BenchmarkError(f"{description} must contain valid Unicode") from error
    return value


def _condition_numbers(value: object, description: str, *, required: set[str] | None = None) -> None:
    if not isinstance(value, Mapping) or not set(value) <= set(CONDITIONS):
        raise BenchmarkError(f"{description} condition fields are invalid")
    if required is not None and not required <= set(value):
        raise BenchmarkError(f"{description} required conditions are missing")
    for condition, number in value.items():
        _number(number, f"{description} {condition}")


def _bootstrap(value: object, description: str) -> None:
    if isinstance(value, Mapping) and value.get("available") is False:
        record = _object(value, {"available", "reason"}, description)
        _boolean(record["available"], f"{description} available")
        _text(record["reason"], f"{description} reason")
        return
    record = _object(
        value, {"estimate", "lower_95", "upper_95", "seed", "draws"},
        description,
    )
    for name in ("estimate", "lower_95", "upper_95"):
        _number(record[name], f"{description} {name}")
    _integer(record["seed"], f"{description} seed")
    _integer(record["draws"], f"{description} draws", minimum=0)


def _preference_card(value: object, description: str) -> None:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{description} must be an object")
    if value.get("available") is False:
        _object(value, {"available"}, description)
        _boolean(value["available"], f"{description} available")
        return
    record = _object(value, _PREFERENCE_CARD_FIELDS, description)
    _boolean(record["available"], f"{description} available")
    for name in ("case_attempts", "suite_wins", "normal_wins", "ties"):
        _integer(record[name], f"{description} {name}", minimum=0)
    _number(record["ordinal_sum"], f"{description} ordinal_sum")
    _number(record["non_tied_win_rate"], f"{description} non_tied_win_rate")
    _bootstrap(record["paired_bootstrap"], f"{description} paired_bootstrap")
    if record["paired_bootstrap_estimand"] != "non_tied_win_rate":
        raise BenchmarkError(f"{description} bootstrap estimand is invalid")


def _validate_scorecards(value: object) -> None:
    cards = _object(
        value,
        {"overall", "task", "surface", "difficulty", "error_dimension", "invariant"},
        "scorecards",
    )
    _preference_card(cards["overall"], "overall scorecard")
    for field, names in (("task", TASKS), ("surface", SURFACES), ("difficulty", DIFFICULTIES)):
        records = _object(cards[field], set(names), f"{field} scorecards")
        for name in names:
            _preference_card(records[name], f"{field} scorecard {name}")

    dimensions = _object(
        cards["error_dimension"], set(MQM_DIMENSIONS), "error-dimension scorecards"
    )
    for name, value in dimensions.items():
        record = _object(
            value, {"available", "mqm_points", "paired_difference"},
            f"error-dimension scorecard {name}",
        )
        _boolean(record["available"], f"error-dimension scorecard {name} available")
        _condition_numbers(record["mqm_points"], f"error-dimension {name}", required=set(CONDITIONS))
        _bootstrap(record["paired_difference"], f"error-dimension {name} paired difference")

    invariants = cards["invariant"]
    if not isinstance(invariants, Mapping):
        raise BenchmarkError("invariant scorecards must be an object")
    for name, value in invariants.items():
        _text(name, "invariant scorecard name")
        record = _object(
            value,
            {"available", "applicable", "passed", "failures", "skipped",
             "validator_errors",
             "pass_rate", "paired_case_attempts",
             "paired_failure_difference_normal_minus_suite", "paired_difference"},
            f"invariant scorecard {name}",
        )
        _boolean(record["available"], f"invariant scorecard {name} available")
        for field in (
            "applicable", "passed", "failures", "skipped",
            "validator_errors", "pass_rate",
        ):
            _condition_numbers(record[field], f"invariant {name} {field}", required=set(CONDITIONS))
        _integer(record["paired_case_attempts"], f"invariant {name} paired cases", minimum=0)
        _bootstrap(record["paired_failure_difference_normal_minus_suite"], f"invariant {name} paired failures")
        _bootstrap(record["paired_difference"], f"invariant {name} paired difference")


def _summary_metric(value: object, description: str) -> None:
    record = _object(value, {"available", "by_condition"}, description)
    _boolean(record["available"], f"{description} available")
    conditions = _object(record["by_condition"], set(CONDITIONS), f"{description} conditions")
    for condition, summary in conditions.items():
        if isinstance(summary, Mapping) and summary.get("available") is False:
            _object(summary, {"available"}, f"{description} {condition}")
            continue
        values = _object(summary, {"count", "mean", "total"}, f"{description} {condition}")
        _integer(values["count"], f"{description} {condition} count", minimum=1)
        _number(values["mean"], f"{description} {condition} mean")
        _number(values["total"], f"{description} {condition} total")


def _validate_operational(value: object) -> None:
    operational = _object(
        value, {"latency_seconds", "usage", "cost_usd", "tools", "research_calls"},
        "operational diagnostics",
    )
    _summary_metric(operational["latency_seconds"], "latency seconds")
    _summary_metric(operational["cost_usd"], "cost USD")
    _summary_metric(operational["research_calls"], "research calls")
    usage = _object(
        operational["usage"], {"input_tokens", "output_tokens", "total_tokens"},
        "usage diagnostics",
    )
    for name, metric in usage.items():
        _summary_metric(metric, f"usage {name}")
    tools = _object(operational["tools"], {"available", "by_condition"}, "tool diagnostics")
    _boolean(tools["available"], "tool diagnostics available")
    by_condition = _object(tools["by_condition"], set(CONDITIONS), "tool conditions")
    for condition, value in by_condition.items():
        if isinstance(value, Mapping) and value.get("available") is False:
            _object(value, {"available"}, f"tools {condition}")
            continue
        record = _object(value, {"calls", "runs", "unique"}, f"tools {condition}")
        _integer(record["calls"], f"tools {condition} calls", minimum=0)
        _integer(record["runs"], f"tools {condition} runs", minimum=1)
        if not isinstance(record["unique"], list):
            raise BenchmarkError(f"tools {condition} unique must be a list")
        names = [_text(name, f"tools {condition} name") for name in record["unique"]]
        if names != sorted(set(names)):
            raise BenchmarkError(f"tools {condition} names must be unique and sorted")


def _validate_metrics(value: object) -> Mapping[str, object]:
    metrics = _object(
        value,
        {"translation", "review", "consistency", "learned_metrics", "scorecards", "operational"},
        "metrics",
    )
    translation = _object(metrics["translation"], _TRANSLATION_FIELDS, "translation metrics")
    for name in ("case_attempts", "suite_wins", "normal_wins", "ties", "suite_critical",
                 "normal_critical", "critical_invariant_regressions"):
        _integer(translation[name], f"translation {name}", minimum=0)
    for name in ("ordinal_sum", "non_tied_win_rate", "bootstrap_lower", "mqm_reduction",
                 "suite_structural_pass_rate"):
        _number(translation[name], f"translation {name}")
    _bootstrap(translation["bootstrap"], "translation bootstrap")
    pairs = translation["mqm_case_attempt_points"]
    if not isinstance(pairs, list):
        raise BenchmarkError("translation MQM case-attempt points must be a list")
    identities: set[tuple[str, int]] = set()
    for index, value in enumerate(pairs):
        record = _object(value, {"case_id", "attempt", "suite", "normal"}, f"translation MQM pair {index}")
        identity = (
            _text(record["case_id"], f"translation MQM pair {index} case_id"),
            _integer(record["attempt"], f"translation MQM pair {index} attempt", minimum=1),
        )
        if identity in identities:
            raise BenchmarkError(f"duplicate translation MQM pair: {identity!r}")
        identities.add(identity)
        _number(record["suite"], f"translation MQM pair {index} suite")
        _number(record["normal"], f"translation MQM pair {index} normal")
    for name in ("mqm_points", "mqm_points_per_1000_words", "structural_pass_rate"):
        _condition_numbers(translation[name], f"translation {name}", required=set(CONDITIONS))

    review = metrics["review"]
    if not isinstance(review, Mapping) or type(review.get("available")) is not bool:
        raise BenchmarkError("review metrics fields are invalid")
    if not review["available"]:
        record = _object(review, {"available", "reason", "unresolved"}, "unavailable review metrics")
        _text(record["reason"], "unavailable review reason")
        _integer(record["unresolved"], "unavailable review unresolved", minimum=0)
    else:
        record = _object(review, _REVIEW_AVAILABLE_FIELDS, "review metrics")
        for name in ("unresolved", "unresolved_reported", "unresolved_corrected",
                     "critical_invariant_regressions"):
            _integer(record[name], f"review {name}", minimum=0)
        if record["introduced_error_aggregation"] != "any_mapping_per_run":
            raise BenchmarkError("review introduced-error aggregation is invalid")
        for name in (
            "introduced_error_runs", "response_runs", "required_error_recall",
            "reported_error_precision", "correction_success_rate",
            "false_positive_correction_rate", "introduced_error_rate",
            "critical_misses", "structural_pass_rate",
        ):
            _condition_numbers(record[name], f"review {name}", required={"normal", "suite"})

    consistency = _object(
        metrics["consistency"],
        {"repeats", "exact_five_level_agreement", "quadratic_weighted_agreement",
         "quadratic_weighted_agreement_method", "major_or_worse_agreement"},
        "reviewer consistency",
    )
    _integer(consistency["repeats"], "reviewer consistency repeats", minimum=0)
    for name in ("exact_five_level_agreement", "quadratic_weighted_agreement", "major_or_worse_agreement"):
        if consistency[name] is not None:
            _number(consistency[name], f"reviewer consistency {name}")
    if consistency["quadratic_weighted_agreement_method"] != "direct_mean_not_chance_corrected_kappa":
        raise BenchmarkError("reviewer consistency method is invalid")

    learned = metrics["learned_metrics"]
    if not isinstance(learned, Mapping) or type(learned.get("available")) is not bool:
        raise BenchmarkError("learned metrics fields are invalid")
    if learned["available"]:
        record = _object(learned, {"available", "means"}, "learned metrics")
        if not isinstance(record["means"], Mapping):
            raise BenchmarkError("learned metric means must be an object")
        for name, values in record["means"].items():
            if name not in {"comet", "xcomet", "chrf"}:
                raise BenchmarkError(f"learned metric name is invalid: {name}")
            _condition_numbers(values, f"learned metric {name}")
    else:
        record = _object(learned, {"available", "reason"}, "unavailable learned metrics")
        _text(record["reason"], "unavailable learned metric reason")
    _validate_scorecards(metrics["scorecards"])
    _validate_operational(metrics["operational"])
    return metrics


def _validate_score_document(value: object) -> Mapping[str, object]:
    document = _object(value, {"schema_version", "provenance", "metrics", "gates"}, "score document")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError("score document schema version mismatch")
    provenance = _object(document["provenance"], _PROVENANCE_FIELDS, "score provenance")
    for name in sorted(_PROVENANCE_FIELDS - {
        "bootstrap_seed", "review_mappings_sha256", "learned_metrics_sha256",
        "claim_evidence",
    }):
        digest = _text(provenance[name], f"provenance {name}")
        if _SHA256.fullmatch(digest) is None:
            raise BenchmarkError(f"provenance {name} must be a SHA-256 digest")
    _integer(provenance["bootstrap_seed"], "provenance bootstrap seed")
    for name in ("review_mappings_sha256", "learned_metrics_sha256"):
        if provenance[name] is not None:
            digest = _text(provenance[name], f"provenance {name}")
            if _SHA256.fullmatch(digest) is None:
                raise BenchmarkError(f"provenance {name} must be a SHA-256 digest or null")
    claim_evidence = _unavailable(
        provenance["claim_evidence"], "claim-bearing provenance"
    )
    metrics = _validate_metrics(document["metrics"])
    expected_gates = evaluate_gates(metrics)
    expected_gates["overall"] = {"passed": None, "verdict": "unavailable"}
    if canonical_bytes(document["gates"]) != canonical_bytes(expected_gates):
        raise BenchmarkError(
            "claim-bearing provenance unavailable; overall gate must be unavailable"
        )
    return document


def _digest(value: object, description: str) -> str:
    digest = _text(value, description)
    if _SHA256.fullmatch(digest) is None:
        raise BenchmarkError(f"{description} must be a SHA-256 digest")
    return digest


def _unavailable(value: object, description: str) -> Mapping[str, object]:
    record = _object(value, {"status", "reason"}, description)
    if record["status"] != "unavailable":
        raise BenchmarkError(f"{description} status must be unavailable")
    _text(record["reason"], f"{description} reason")
    return record


def _seed_binding(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{description} must be an object")
    if value.get("status") == "unavailable":
        _unavailable(value, description)
        raise BenchmarkError(f"{description} must be available for a canonical report")
    record = _object(value, {"status", "value"}, description)
    if record["status"] != "available":
        raise BenchmarkError(f"{description} status is invalid")
    _integer(record["value"], f"{description} value")
    return record


def _digest_binding(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{description} must be an object")
    if value.get("status") == "unavailable":
        return _unavailable(value, description)
    record = _object(value, {"status", "sha256"}, description)
    if record["status"] != "available":
        raise BenchmarkError(f"{description} status is invalid")
    _digest(record["sha256"], f"{description} sha256")
    return record


def _timestamp_binding(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{description} must be an object")
    if value.get("status") == "unavailable":
        return _unavailable(value, description)
    record = _object(value, {"status", "value"}, description)
    if record["status"] != "available":
        raise BenchmarkError(f"{description} status is invalid")
    timestamp = _text(record["value"], f"{description} value")
    if _UTC_TIMESTAMP.fullmatch(timestamp) is None:
        raise BenchmarkError(f"{description} value must be canonical UTC")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise BenchmarkError(f"{description} value must be canonical UTC") from error
    return record


def _execution_identity(
    value: object,
    description: str,
    available_fields: set[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{description} must be an object")
    if value.get("status") == "unavailable":
        _unavailable(value, description)
        raise BenchmarkError(f"{description} must be available for a canonical report")
    record = _object(value, {"status"} | available_fields, description)
    if record["status"] != "available":
        raise BenchmarkError(f"{description} status is invalid")
    for field in sorted(available_fields):
        _text(record[field], f"{description} {field}")
    return record


def _unavailable_execution_identity(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or value.get("status") != "unavailable":
        raise BenchmarkError(
            f"{description} must be unavailable without a sealed execution receipt"
        )
    return _unavailable(value, description)


def _validate_report_provenance(
    value: object,
    score_document: Mapping[str, object],
    score_bytes: bytes,
) -> Mapping[str, object]:
    provenance = _object(value, _PROVENANCE_TOP_FIELDS, "report provenance")
    if provenance["schema"] != "report-provenance-v1":
        raise BenchmarkError("report provenance schema mismatch")
    if _digest(provenance["scores_sha256"], "report provenance scores SHA-256") != sha256_bytes(score_bytes):
        raise BenchmarkError("report provenance scores SHA-256 does not match exact score bytes")

    benchmark = _object(
        provenance["benchmark"], {"benchmark_schema_version", "dataset_version"},
        "report provenance benchmark",
    )
    if (
        type(benchmark["benchmark_schema_version"]) is not int
        or benchmark["benchmark_schema_version"] != score_document["schema_version"]
    ):
        raise BenchmarkError("report provenance benchmark schema version mismatch")
    if benchmark["dataset_version"] != "pt-pt-v1":
        raise BenchmarkError("report provenance dataset version mismatch")

    git = _object(
        provenance["git"], {"commit_sha", "tree_state", "diff_snapshot"},
        "report provenance git",
    )
    commit = _text(git["commit_sha"], "report provenance git commit")
    if _GIT_COMMIT.fullmatch(commit) is None:
        raise BenchmarkError("report provenance git commit is invalid")
    if git["tree_state"] not in {"clean", "snapshot"}:
        raise BenchmarkError("report provenance git tree_state is invalid")
    if git["tree_state"] == "clean":
        _unavailable(git["diff_snapshot"], "clean-tree diff snapshot")
    else:
        _digest_binding(git["diff_snapshot"], "dirty-tree diff snapshot")
        if git["diff_snapshot"]["status"] != "available":
            raise BenchmarkError("snapshot tree state requires a diff snapshot digest")

    input_hashes = _object(
        provenance["input_hashes"], _INPUT_HASH_FIELDS,
        "report provenance input hashes",
    )
    for name in sorted(_INPUT_HASH_FIELDS):
        _digest(input_hashes[name], f"report provenance input hash {name}")
    score_provenance = score_document["provenance"]
    for name in ("dataset_sha256", "dataset_manifest_sha256", "run_manifest_sha256"):
        if input_hashes[name] != score_provenance[name]:
            raise BenchmarkError(f"report provenance {name} does not match score provenance")

    execution = _object(
        provenance["execution"], {"host", "runner", "model", "generation_settings"},
        "report provenance execution",
    )
    claim_available = score_provenance["claim_evidence"].get("status") == "available"
    if claim_available:
        _execution_identity(execution["host"], "execution host", {"name", "version"})
        _execution_identity(
            execution["runner"], "execution runner",
            {"name", "version", "invocation_mode"},
        )
        _execution_identity(
            execution["model"], "execution model",
            {"provider", "name", "version"},
        )
    else:
        _unavailable_execution_identity(execution["host"], "execution host")
        _unavailable_execution_identity(execution["runner"], "execution runner")
        _unavailable_execution_identity(execution["model"], "execution model")
    settings = execution["generation_settings"]
    if not isinstance(settings, list):
        raise BenchmarkError("generation settings must be a list")
    if not claim_available and settings:
        raise BenchmarkError(
            "generation settings must be unavailable without claim-bearing provenance"
        )
    setting_names: list[str] = []
    for index, setting in enumerate(settings):
        record = _object(
            setting, {"name", "value"}, f"generation setting {index}"
        )
        setting_names.append(_text(record["name"], f"generation setting {index} name"))
        setting_value = record["value"]
        if setting_value is None or type(setting_value) is bool:
            pass
        elif type(setting_value) in (int, float):
            _number(setting_value, f"generation setting {index} value")
        elif type(setting_value) is str:
            _text(setting_value, f"generation setting {index} value", nonempty=False)
        else:
            raise BenchmarkError(f"generation setting {index} value must be a JSON scalar")
    if setting_names != sorted(set(setting_names)):
        raise BenchmarkError("generation settings must have unique sorted names")

    seeds = _object(
        provenance["seeds"], {"schedule", "blinding", "bootstrap"},
        "report provenance seeds",
    )
    for name in ("schedule", "blinding", "bootstrap"):
        _seed_binding(seeds[name], f"report provenance {name} seed")
    if (
        seeds["bootstrap"].get("status") != "available"
        or seeds["bootstrap"].get("value") != score_provenance["bootstrap_seed"]
    ):
        raise BenchmarkError("report provenance bootstrap seed does not match score provenance")

    attempts = provenance["attempt_history"]
    if not isinstance(attempts, list):
        raise BenchmarkError("report provenance attempt_history must be a list")
    attempt_ids: list[str] = []
    attempt_records: dict[str, Mapping[str, object]] = {}
    retried_predecessors: set[str] = set()
    for index, attempt in enumerate(attempts):
        record = _object(attempt, _ATTEMPT_FIELDS, f"attempt history {index}")
        run_id = _text(record["run_id"], f"attempt history {index} run_id")
        if run_id in attempt_records:
            raise BenchmarkError("attempt history run IDs must be unique")
        attempt_ids.append(run_id)
        attempt_number = _integer(
            record["attempt"], f"attempt history {index} attempt", minimum=1
        )
        if record["outcome"] not in _ATTEMPT_OUTCOMES:
            raise BenchmarkError(f"attempt history {index} outcome is invalid")
        retry_of = record["retry_of"]
        retry_reason = record["retry_reason"]
        if retry_of is None:
            if retry_reason is not None:
                raise BenchmarkError(f"attempt history {index} retry reason has no retry target")
            if attempt_number != 1:
                raise BenchmarkError(
                    f"attempt history {index} root attempt must be one"
                )
        else:
            retry_of = _text(retry_of, f"attempt history {index} retry_of")
            _text(retry_reason, f"attempt history {index} retry_reason")
            if retry_of == run_id:
                raise BenchmarkError(f"attempt history {index} cannot retry itself")
            predecessor = attempt_records.get(retry_of)
            if predecessor is None:
                raise BenchmarkError(
                    f"attempt history {index} retry target must be an earlier record"
                )
            if predecessor["outcome"] == "success":
                raise BenchmarkError(
                    f"attempt history {index} cannot retry a successful predecessor"
                )
            if attempt_number != predecessor["attempt"] + 1:
                raise BenchmarkError(
                    f"attempt history {index} attempt does not follow its predecessor"
                )
            if retry_of in retried_predecessors:
                raise BenchmarkError(
                    f"attempt history {index} predecessor already has a retry"
                )
            retried_predecessors.add(retry_of)
        started = _timestamp_binding(record["started_at"], f"attempt history {index} started_at")
        completed = _timestamp_binding(record["completed_at"], f"attempt history {index} completed_at")
        if started.get("status") == "available" and completed.get("status") == "available":
            start_value = datetime.strptime(started["value"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            end_value = datetime.strptime(completed["value"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if end_value < start_value:
                raise BenchmarkError(f"attempt history {index} completion precedes start")
        attempt_records[run_id] = record
    if attempt_ids != sorted(attempt_ids):
        raise BenchmarkError("attempt history run IDs must be sorted")

    raw_outputs = provenance["raw_outputs"]
    if not isinstance(raw_outputs, list):
        raise BenchmarkError("report provenance raw_outputs must be a list")
    raw_ids: list[str] = []
    for index, raw in enumerate(raw_outputs):
        record = _object(raw, {"run_id", "sha256"}, f"raw output binding {index}")
        raw_ids.append(_text(record["run_id"], f"raw output binding {index} run_id"))
        _digest(record["sha256"], f"raw output binding {index} sha256")
    if raw_ids != sorted(set(raw_ids)):
        raise BenchmarkError("raw output bindings must have unique sorted run IDs")
    if any(run_id not in set(attempt_ids) for run_id in raw_ids):
        raise BenchmarkError("raw output binding has no attempt history record")
    if score_document["gates"]["overall"]["passed"] is True and (
        len(attempt_ids) != 405 or set(raw_ids) != set(attempt_ids)
    ):
        raise BenchmarkError(
            "a PASS report requires attempt and raw-output coverage for all 405 frozen runs"
        )
    if not claim_available and (attempt_ids or raw_ids):
        raise BenchmarkError(
            "attempt and raw-output provenance must be unavailable without a sealed receipt"
        )

    result_bindings = _object(
        provenance["result_bindings"],
        {"structural_sha256", "learned_metrics", "review_mappings"},
        "report provenance result bindings",
    )
    if _digest(result_bindings["structural_sha256"], "structural result binding") != score_provenance["validation_sha256"]:
        raise BenchmarkError("structural result binding does not match score provenance")
    for field, score_field in (
        ("learned_metrics", "learned_metrics_sha256"),
        ("review_mappings", "review_mappings_sha256"),
    ):
        binding = _digest_binding(result_bindings[field], f"{field} result binding")
        expected = score_provenance[score_field]
        if expected is None:
            if binding.get("status") != "unavailable":
                raise BenchmarkError(f"{field} binding must be unavailable")
        elif binding.get("status") != "available" or binding.get("sha256") != expected:
            raise BenchmarkError(f"{field} binding does not match score provenance")

    review_bindings = _object(
        provenance["review_bindings"],
        {
            "blind_bundle_sha256", "condition_key_sha256",
            "annotations_sha256", "annotation_lock_sha256",
        },
        "report provenance review bindings",
    )
    for field, score_field in (
        ("blind_bundle_sha256", "review_bundle_sha256"),
        ("condition_key_sha256", "condition_key_sha256"),
        ("annotations_sha256", "annotations_sha256"),
        ("annotation_lock_sha256", "annotation_lock_sha256"),
    ):
        if _digest(review_bindings[field], f"review binding {field}") != score_provenance[score_field]:
            raise BenchmarkError(f"report provenance {field} does not match score provenance")

    code_versions = _object(
        provenance["code_versions"], {"scoring", "report"},
        "report provenance code versions",
    )
    score_module = Path(__file__).with_name("score.py")
    for field, version, source in (
        ("scoring", _SCORING_CODE_VERSION, score_module),
        ("report", _REPORT_CODE_VERSION, Path(__file__)),
    ):
        record = _object(
            code_versions[field], {"version", "sha256"},
            f"{field} code version",
        )
        if record["version"] != version:
            raise BenchmarkError(f"{field} code version mismatch")
        if _digest(record["sha256"], f"{field} code sha256") != sha256_file(source):
            raise BenchmarkError(f"{field} code SHA-256 does not match executing code")
    return provenance


def _cell(value: object) -> str:
    raw = str(value)
    truncated = len(raw) > _MAX_CELL_CHARS
    raw = raw[:_MAX_CELL_CHARS]
    result: list[str] = []
    for index, character in enumerate(raw):
        codepoint = ord(character)
        category = unicodedata.category(character)
        underscore_inside_word = (
            character == "_"
            and index > 0
            and index + 1 < len(raw)
            and raw[index - 1].isalnum()
            and raw[index + 1].isalnum()
        )
        if character in {" ", "-", ",", "%", "="} or underscore_inside_word:
            result.append(character)
        elif category[0] in {"L", "N"}:
            result.append(character)
        else:
            width = 4 if codepoint <= 0xFFFF else 8
            result.append(f"U+{codepoint:0{width}X}")
    if truncated:
        result.append("…")
    return "".join(result)


def _format_number(value: object) -> str:
    number = _number(value, "report number")
    return f"{number:.6g}"


def _format_percent(value: object) -> str:
    return f"{100.0 * _number(value, 'report percentage'):.2f}%"


def _verdict(value: object) -> str:
    return "UNAVAILABLE" if value is None else ("PASS" if value is True else "FAIL")


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    rendered = ["| " + " | ".join(_cell(header) for header in headers) + " |"]
    rendered.append("| " + " | ".join("---" for _ in headers) + " |")
    rendered.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(rendered)


def _preference_rows(cards: Mapping[str, object], names: Sequence[str], labels: Mapping[str, str] | None = None) -> list[list[object]]:
    rows: list[list[object]] = []
    for name in names:
        card = cards[name]
        label = labels[name] if labels else name
        if not card["available"]:
            rows.append([label, "unavailable", "—", "—", "—", "—"])
        else:
            rows.append([
                label, card["case_attempts"], card["suite_wins"], card["normal_wins"],
                card["ties"], _format_percent(card["non_tied_win_rate"]),
            ])
    return rows


def _identity_summary(value: Mapping[str, object], fields: Sequence[str]) -> str:
    if value["status"] == "unavailable":
        return f"unavailable — {_cell(value['reason'])}"
    return ", ".join(f"{field}={_cell(value[field])}" for field in fields)


def _binding_summary(value: Mapping[str, object]) -> str:
    if value["status"] == "unavailable":
        return f"unavailable — {_cell(value['reason'])}"
    if "sha256" in value:
        return _cell(value["sha256"])
    return _cell(value["value"])


def _render_markdown(
    document: Mapping[str, object],
    report_provenance: Mapping[str, object],
    result_bytes: bytes,
) -> bytes:
    metrics = document["metrics"]
    gates = document["gates"]
    translation = metrics["translation"]
    review = metrics["review"]
    cards = metrics["scorecards"]
    operational = metrics["operational"]
    execution = report_provenance["execution"]

    sections: list[str] = [_HEADINGS[0]]
    sections.append(
        _HEADINGS[1]
        + f"\n\nOverall verdict: {_verdict(gates['overall']['passed'])}.\n\n"
        + "Scope: the frozen PT-PT v1 benchmark only; these results do not establish quality "
          "for other locales, datasets, models, or configurations."
    )
    sections.append(
        _HEADINGS[2]
        + f"\n\n- Dataset: {_cell(report_provenance['benchmark']['dataset_version'])}"
        + "\n- tested model: "
        + _identity_summary(execution["model"], ("provider", "name", "version"))
        + "\n- Model: "
        + _identity_summary(execution["model"], ("provider", "name", "version"))
        + "\n- Host: "
        + _identity_summary(execution["host"], ("name", "version"))
        + "\n- Runner configuration: "
        + _identity_summary(
            execution["runner"], ("name", "version", "invocation_mode")
        )
        + "\n- Generation settings: "
        + (
            ", ".join(
                f"{_cell(setting['name'])}={_cell(setting['value'])}"
                for setting in execution["generation_settings"]
            )
            or "none recorded"
        )
        + f"\n- Bootstrap seed: {report_provenance['seeds']['bootstrap']['value']}"
    )

    gate_rows: list[list[object]] = []
    for group in ("translation", "review"):
        gate = gates[group]
        if gate.get("available") is False:
            gate_rows.append([group, "overall", "UNAVAILABLE"])
        for name in sorted(gate["checks"]):
            gate_rows.append([group, name, _verdict(gate["checks"][name])])
    sections.append(
        _HEADINGS[3]
        + f"\n\nTranslation gate: {_verdict(gates['translation']['passed'])}. "
          f"Review gate: {_verdict(gates['review']['passed'])}.\n\n"
        + _table(("Gate", "Check", "Verdict"), gate_rows)
    )
    sections.append(
        _HEADINGS[4]
        + "\n\n"
        + _table(
            ("Case-attempts", "Suite wins", "Normal wins", "Ties", "Non-tied suite win rate", "95% paired interval"),
            [[translation["case_attempts"], translation["suite_wins"], translation["normal_wins"],
              translation["ties"], _format_percent(translation["non_tied_win_rate"]),
              f"{_format_percent(translation['bootstrap']['lower_95'])}–{_format_percent(translation['bootstrap']['upper_95'])}"]],
        )
    )
    sections.append(
        _HEADINGS[5]
        + "\n\n"
        + _table(
            ("Condition", "MQM-lite points", "Points per 1,000 words"),
            [[condition, _format_number(translation["mqm_points"][condition]),
              _format_number(translation["mqm_points_per_1000_words"][condition])]
             for condition in CONDITIONS],
        )
        + f"\n\nMQM reduction: {_format_percent(translation['mqm_reduction'])}.\n\n"
        + "Error-dimension scorecard\n\n"
        + _table(
            ("Dimension", "Normal points", "Suite points", "Paired estimate"),
            [[name, value["mqm_points"]["normal"], value["mqm_points"]["suite"],
              _format_number(value["paired_difference"]["estimate"])]
             for name, value in sorted(cards["error_dimension"].items())],
        )
    )
    invariant_rows = [
        [name, value["applicable"]["suite"], value["passed"]["suite"],
         value["failures"]["suite"], value["skipped"]["suite"],
         value["validator_errors"]["suite"],
         _format_percent(value["pass_rate"]["suite"])]
        for name, value in sorted(cards["invariant"].items())
    ]
    sections.append(
        _HEADINGS[6]
        + f"\n\nSuite structural pass rate: {_format_percent(translation['suite_structural_pass_rate'])}."
        + "\n\nInvariant scorecard\n\n"
        + (_table(("Invariant", "Applicable", "Passed", "Failures", "Skipped", "Validator errors", "Suite pass rate"), invariant_rows)
           if invariant_rows else "Unavailable: no applicable structural invariant scorecards.")
    )
    if not review["available"]:
        review_body = f"Unavailable: {_cell(review['reason'])}. Unresolved mappings: {review['unresolved']}."
    else:
        names = (
            "required_error_recall", "reported_error_precision", "correction_success_rate",
            "false_positive_correction_rate", "introduced_error_rate", "critical_misses",
            "structural_pass_rate",
        )
        review_body = _table(
            ("Measure", "Normal", "Suite"),
            [[name, _format_number(review[name]["normal"]), _format_number(review[name]["suite"])] for name in names],
        )
    sections.append(_HEADINGS[7] + "\n\n" + review_body)

    overall_rows = _preference_rows({"overall": cards["overall"]}, ("overall",), {"overall": "Overall scorecard"})
    task_rows = _preference_rows(cards["task"], TASKS)
    surface_rows = _preference_rows(cards["surface"], SURFACES, _SURFACE_NAMES)
    difficulty_rows = _preference_rows(cards["difficulty"], DIFFICULTIES)
    sections.append(
        _HEADINGS[8]
        + "\n\nOverall scorecard\n\n"
        + _table(("Stratum", "Cases", "Suite wins", "Normal wins", "Ties", "Suite win rate"), overall_rows)
        + "\n\nTask scorecard\n\n"
        + _table(("Task", "Cases", "Suite wins", "Normal wins", "Ties", "Suite win rate"), task_rows)
        + "\n\nSurface scorecard\n\n"
        + _table(("Surface", "Cases", "Suite wins", "Normal wins", "Ties", "Suite win rate"), surface_rows)
        + "\n\nDifficulty scorecard\n\n"
        + _table(("Difficulty", "Cases", "Suite wins", "Normal wins", "Ties", "Suite win rate"), difficulty_rows)
    )
    context_points = translation["mqm_points"]["context_only"]
    context_latency = operational["latency_seconds"]["by_condition"]["context_only"]
    latency_text = "unavailable" if context_latency.get("available") is False else _format_number(context_latency["mean"])
    sections.append(
        _HEADINGS[9]
        + f"\n\nContext-only MQM-lite points: {_format_number(context_points)}. "
          f"Context-only mean latency seconds: {latency_text}. "
          "Context-only is diagnostic and does not independently determine the verdict."
    )
    learned = metrics["learned_metrics"]
    if not learned["available"]:
        learned_body = f"Unavailable: {_cell(learned['reason'])}."
    else:
        learned_body = _table(
            ("Metric", "Condition", "Mean"),
            [[metric, condition, _format_number(score)]
             for metric, conditions in sorted(learned["means"].items())
             for condition, score in sorted(conditions.items())],
        )
    sections.append(_HEADINGS[10] + "\n\n" + learned_body + " Learned metrics are diagnostic only.")

    operational_rows: list[list[object]] = []
    scalar_metrics = [
        ("Latency seconds", operational["latency_seconds"]),
        ("Cost USD", operational["cost_usd"]),
        ("Research calls", operational["research_calls"]),
    ] + [(f"Usage {name}", value) for name, value in sorted(operational["usage"].items())]
    for label, metric in scalar_metrics:
        for condition in CONDITIONS:
            summary = metric["by_condition"][condition]
            operational_rows.append([
                label, condition,
                "unavailable" if summary.get("available") is False else _format_number(summary["mean"]),
                "unavailable" if summary.get("available") is False else summary["count"],
            ])
    tool_lines: list[str] = []
    for condition in CONDITIONS:
        value = operational["tools"]["by_condition"][condition]
        if value.get("available") is False:
            tool_lines.append(f"- {condition}: unavailable")
        else:
            tool_lines.append(
                f"- {condition}: {value['calls']} calls across {value['runs']} runs; "
                + ", ".join(_cell(name) for name in value["unique"])
            )
    sections.append(
        _HEADINGS[11] + "\n\n"
        + _table(("Measure", "Condition", "Mean", "Count"), operational_rows)
        + "\n\nTools:\n\n" + "\n".join(tool_lines)
        + "\n\nOperational measures are diagnostic only."
    )
    consistency = metrics["consistency"]
    if not consistency["repeats"]:
        consistency_body = "Unavailable: no consistency repeats were present."
    else:
        consistency_body = _table(
            ("Repeats", "Exact five-level", "Quadratic weighted", "Major-or-worse"),
            [[consistency["repeats"], _format_percent(consistency["exact_five_level_agreement"]),
              _format_percent(consistency["quadratic_weighted_agreement"]),
              _format_percent(consistency["major_or_worse_agreement"])]],
        ) + "\n\nThis is intra-reviewer consistency, not inter-reviewer agreement."
    sections.append(_HEADINGS[12] + "\n\n" + consistency_body)

    example_rows = [
        [pair["case_id"], pair["attempt"], pair["normal"], pair["suite"], "Preference outcome unavailable"]
        for pair in sorted(
            translation["mqm_case_attempt_points"], key=lambda value: (value["case_id"], value["attempt"])
        )
    ]
    sections.append(
        _HEADINGS[13]
        + "\n\nOnly publication-safe numeric score records are shown; source text, outputs, "
          "private condition labels, reviewer notes, and hidden metadata are excluded. "
          "Representative win, tie, and loss classification is unavailable because the "
          "canonical Task 7 score document has no publication-safe per-item preference record.\n\n"
        + (_table(("Stable case ID", "Attempt", "Normal MQM", "Suite MQM", "Preference"), example_rows)
           if example_rows else "Unavailable: no publication-safe item-level records are present.")
    )
    hard_rows = [
        [name, value["failures"]["normal"], value["failures"]["suite"],
         value["validator_errors"]["normal"], value["validator_errors"]["suite"]]
        for name, value in sorted(cards["invariant"].items())
        if value["failures"]["normal"] or value["failures"]["suite"]
        or value["validator_errors"]["normal"] or value["validator_errors"]["suite"]
    ]
    sections.append(
        _HEADINGS[14]
        + f"\n\nCritical findings — normal: {translation['normal_critical']}; suite: {translation['suite_critical']}. "
          f"Critical invariant regressions: {translation['critical_invariant_regressions']}.\n\n"
        + (_table(("Invariant", "Normal failures", "Suite failures", "Normal validator errors", "Suite validator errors"), hard_rows)
           if hard_rows else "No invariant failures or validator errors are represented in the canonical score document.")
    )
    input_rows = [
        [name, value]
        for name, value in sorted(report_provenance["input_hashes"].items())
    ]
    seed_rows = [
        [f"{name.title()} seed", _binding_summary(report_provenance["seeds"][name])]
        for name in ("schedule", "blinding", "bootstrap")
    ]
    attempt_rows = [
        [
            value["run_id"], value["attempt"], value["outcome"],
            "none" if value["retry_of"] is None else value["retry_of"],
            "none" if value["retry_reason"] is None else value["retry_reason"],
            _binding_summary(value["started_at"]),
            _binding_summary(value["completed_at"]),
        ]
        for value in report_provenance["attempt_history"]
    ]
    raw_rows = [
        [value["run_id"], value["sha256"]]
        for value in report_provenance["raw_outputs"]
    ]
    result_rows = [
        ["Structural results", report_provenance["result_bindings"]["structural_sha256"]],
        ["Learned metrics", _binding_summary(report_provenance["result_bindings"]["learned_metrics"])],
        ["Review mappings", _binding_summary(report_provenance["result_bindings"]["review_mappings"])],
        ["Blind review bundle", report_provenance["review_bindings"]["blind_bundle_sha256"]],
        ["Condition-key artifact", report_provenance["review_bindings"]["condition_key_sha256"]],
        ["Annotations", report_provenance["review_bindings"]["annotations_sha256"]],
        ["Annotation lock", report_provenance["review_bindings"]["annotation_lock_sha256"]],
    ]
    code_rows = [
        [name, report_provenance["code_versions"][name]["version"],
         report_provenance["code_versions"][name]["sha256"]]
        for name in ("scoring", "report")
    ]
    sections.append(
        _HEADINGS[15]
        + f"\n\nCanonical results SHA-256: {sha256_bytes(result_bytes)}."
        + f"\n\nScore bytes SHA-256: {report_provenance['scores_sha256']}."
        + f"\n\nBenchmark schema version: {report_provenance['benchmark']['benchmark_schema_version']}; "
          f"dataset version: {_cell(report_provenance['benchmark']['dataset_version'])}."
        + f"\n\nGit commit: {_cell(report_provenance['git']['commit_sha'])}; "
          f"tree state: {_cell(report_provenance['git']['tree_state'])}; "
          f"diff snapshot: {_binding_summary(report_provenance['git']['diff_snapshot'])}."
        + "\n\nFrozen input hashes\n\n"
        + _table(("Input", "SHA-256"), input_rows)
        + "\n\nRandomization and bootstrap seeds\n\n"
        + _table(("Seed", "Value"), seed_rows)
        + "\n\nAttempt and retry history\n\n"
        + (
            _table(
                ("Run ID", "Attempt", "Outcome", "Retry of", "Retry reason", "Started", "Completed"),
                attempt_rows,
            )
            if attempt_rows else "No attempts recorded."
        )
        + "\n\nRaw-output content hashes\n\n"
        + f"Raw-output count: {len(raw_rows)}.\n\n"
        + (_table(("Run ID", "SHA-256"), raw_rows) if raw_rows else "No raw outputs recorded.")
        + "\n\nFrozen result and review bindings\n\n"
        + _table(("Artifact", "Binding"), result_rows)
        + "\n\nScoring and report code versions\n\n"
        + _table(("Component", "Version", "SHA-256"), code_rows)
        + "\n\nReproduce with:\n\n```text\npython3 -m scripts.benchmark.report "
          "--scores <canonical-scores.json> --provenance <report-provenance.json> "
          "--results <results.json> --markdown <report.md>\n```"
    )
    sections.append(
        _HEADINGS[16]
        + "\n\nBenchmark artifacts are AI-generated. Human review covered only the frozen "
          "benchmark outputs. No human translator reviewed future translations. Human review "
          "of the frozen benchmark does not imply human review of future translations; these "
          "results do not imply such review."
    )
    return ("\n\n".join(sections) + "\n").encode("utf-8")


def render_report(
    score_document: Mapping[str, object],
    report_provenance: Mapping[str, object],
    *,
    score_bytes: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Validate and render deterministic machine and Markdown benchmark results."""
    document = _validate_score_document(score_document)
    canonical_score = canonical_bytes(document)
    if score_bytes is None:
        score_bytes = canonical_score
    elif score_bytes != canonical_score:
        raise BenchmarkError("exact score bytes are not canonical Task 7 output")
    provenance = _validate_report_provenance(
        report_provenance, document, score_bytes
    )
    result_document = {
        "report_schema_version": 1,
        "report_provenance": provenance,
        "score_document": document,
    }
    result_bytes = canonical_bytes(result_document)
    return result_bytes, _render_markdown(document, provenance, result_bytes)


@dataclass
class _HeldInput:
    path: Path
    description: str
    descriptor: int
    identity: tuple[int, int]
    metadata: tuple[int, int, int, int]
    encoded: bytes
    value: Mapping[str, object]

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


@dataclass
class _HeldOutput:
    path: Path
    description: str
    descriptor: int
    parent_descriptor: int
    identity: tuple[int, int]
    parent_identity: tuple[int, int]
    metadata: tuple[int, int, int, int, int, int, int]
    expected: bytes

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def _file_metadata(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_descriptor(descriptor: int, limit: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    blocks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        block = os.read(descriptor, min(1024 * 1024, remaining))
        if not block:
            break
        blocks.append(block)
        remaining -= len(block)
    return b"".join(blocks)


def _open_held_input(
    path: Path,
    description: str,
    *,
    limit: int,
) -> _HeldInput:
    path = Path(os.path.abspath(path))
    descriptor = -1
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise BenchmarkError(f"{description} must be a real regular file")
        if metadata.st_nlink != 1:
            raise BenchmarkError(f"{description} must have exactly one link")
        if metadata.st_size > limit:
            raise BenchmarkError(f"{description} exceeds {limit} bytes")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        encoded = _read_descriptor(descriptor, limit)
        after = os.fstat(descriptor)
        if len(encoded) > limit:
            raise BenchmarkError(f"{description} exceeds {limit} bytes")
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise BenchmarkError(f"{description} changed while reading")
        if (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise BenchmarkError(f"{description} identity changed while opening")
    except BenchmarkError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise BenchmarkError(f"cannot read {description}: {error}") from error
    try:
        value = _parse_canonical_json(encoded, description)
        if not isinstance(value, Mapping):
            raise BenchmarkError(f"{description} must be an object")
        return _HeldInput(
            path=path,
            description=description,
            descriptor=descriptor,
            identity=(before.st_dev, before.st_ino),
            metadata=(before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
            encoded=encoded,
            value=value,
        )
    except Exception:
        os.close(descriptor)
        raise


def _verify_held_input(value: _HeldInput) -> None:
    try:
        held = os.fstat(value.descriptor)
        linked = value.path.lstat()
        current = (held.st_dev, held.st_ino, held.st_size, held.st_mtime_ns)
        if (
            current != value.metadata
            or not stat.S_ISREG(linked.st_mode)
            or linked.st_nlink != 1
            or (linked.st_dev, linked.st_ino) != value.identity
            or _read_descriptor(value.descriptor, len(value.encoded)) != value.encoded
        ):
            raise BenchmarkError(f"{value.description} identity or bytes changed during reporting")
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot reverify {value.description}: {error}") from error


def _symlink_ancestors(path: Path) -> set[Path]:
    absolute = Path(os.path.abspath(path))
    result: set[Path] = set()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                result.add(current)
        except FileNotFoundError:
            break
        except OSError as error:
            raise BenchmarkError(f"cannot inspect path component {current}: {error}") from error
    return result


def _resolved_target(path: Path, *, allowed_symlinks: set[Path]) -> Path:
    path = Path(os.path.abspath(path))
    unexpected = _symlink_ancestors(path.parent) - allowed_symlinks
    if unexpected:
        raise BenchmarkError(f"output parent contains a symlink: {sorted(map(str, unexpected))[0]}")
    try:
        parent = path.parent.resolve(strict=True)
        if not parent.is_dir():
            raise BenchmarkError(f"output parent is not a directory: {path.parent}")
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot resolve output parent {path.parent}: {error}") from error
    return parent / path.name


def _validate_targets(
    consumed: Sequence[_HeldInput],
    results: Path,
    markdown: Path,
    *,
    verify_existing: bool,
) -> tuple[Path, Path, dict[Path, tuple[int, int]]]:
    allowed_symlinks: set[Path] = set()
    for value in consumed:
        allowed_symlinks.update(_symlink_ancestors(value.path.parent))
    results_target = _resolved_target(results, allowed_symlinks=allowed_symlinks)
    markdown_target = _resolved_target(markdown, allowed_symlinks=allowed_symlinks)
    if (
        results_target == markdown_target
        or str(results_target).casefold() == str(markdown_target).casefold()
    ):
        raise BenchmarkError("results and Markdown outputs must be distinct paths")
    for value in consumed:
        if (
            value.path.resolve(strict=True) in {results_target, markdown_target}
            or str(value.path.resolve(strict=True)).casefold()
            in {str(results_target).casefold(), str(markdown_target).casefold()}
        ):
            raise BenchmarkError(f"output path aliases consumed {value.description}")
    consumed_identities = {value.identity: value.description for value in consumed}
    identities: set[tuple[int, int]] = set()
    output_identities: dict[Path, tuple[int, int]] = {}
    for target, description in ((results_target, "results output"), (markdown_target, "Markdown output")):
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            if verify_existing:
                raise BenchmarkError(f"{description} is missing for verification")
            continue
        except OSError as error:
            raise BenchmarkError(f"cannot inspect {description}: {error}") from error
        if not verify_existing:
            raise BenchmarkError(f"{description} already exists: {target}")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise BenchmarkError(f"{description} must be an exclusive regular file")
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in consumed_identities:
            raise BenchmarkError(
                f"{description} aliases consumed {consumed_identities[identity]}"
            )
        if identity in identities:
            raise BenchmarkError("results and Markdown outputs alias one file")
        identities.add(identity)
        output_identities[target] = identity
    return results_target, markdown_target, output_identities


def _write_fd(descriptor: int, encoded: bytes) -> None:
    offset = 0
    while offset < len(encoded):
        written = os.write(descriptor, encoded[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _verify_held_output(value: _HeldOutput) -> None:
    try:
        encoded = _read_descriptor(value.descriptor, len(value.expected))
        held = os.fstat(value.descriptor)
        held_parent = os.fstat(value.parent_descriptor)
        linked = os.stat(
            value.path.name,
            dir_fd=value.parent_descriptor,
            follow_symlinks=False,
        )
        linked_parent = value.path.parent.stat()
        if (
            encoded != value.expected
            or _file_metadata(held) != value.metadata
            or _file_metadata(linked) != value.metadata
            or not stat.S_ISREG(held.st_mode)
            or held.st_nlink != 1
            or held.st_size != len(value.expected)
            or (held.st_dev, held.st_ino) != value.identity
            or (held_parent.st_dev, held_parent.st_ino) != value.parent_identity
            or (linked_parent.st_dev, linked_parent.st_ino) != value.parent_identity
        ):
            raise BenchmarkError(
                f"{value.description} identity, metadata, or bytes changed during acceptance"
            )
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(
            f"cannot reverify {value.description}: {error}"
        ) from error


def _verify_output_set(values: Sequence[_HeldOutput]) -> None:
    for value in values:
        _verify_held_output(value)


def _accept_output_set(
    values: Sequence[_HeldOutput],
    accept_inputs: Callable[[], None] | None,
) -> None:
    # The first pass binds exact bytes and names before potentially lengthy input
    # acceptance. The final pass is the filesystem acceptance boundary: no later
    # operation in this function uses an output pathname.
    _verify_output_set(values)
    if accept_inputs is not None:
        accept_inputs()
    _verify_output_set(values)


def _rename_no_replace(
    source_descriptor: int,
    source_name: str,
    destination_descriptor: int,
    destination_name: str,
) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    destination = os.fsencode(destination_name)
    if sys.platform == "darwin" and hasattr(library, "renameatx_np"):
        function = library.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            source_descriptor,
            source,
            destination_descriptor,
            destination,
            0x00000004,  # RENAME_EXCL
        )
    elif hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            source_descriptor,
            source,
            destination_descriptor,
            destination,
            0x00000001,  # RENAME_NOREPLACE
        )
    else:
        raise OSError(errno.ENOTSUP, "exclusive rename is unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _move_public_output_to_recovery(output: _HeldOutput) -> Path:
    for _ in range(64):
        name = f".report-recovery-{secrets.token_hex(16)}"
        try:
            _rename_no_replace(
                output.parent_descriptor,
                output.path.name,
                output.parent_descriptor,
                name,
            )
            # This atomic move is the recovery acceptance boundary. Do not
            # inspect, restore, or otherwise use either pathname afterward.
            return output.path.parent / name
        except FileExistsError:
            continue
        except OSError:
            return output.path
    return output.path


def _rollback_created_outputs(outputs: Sequence[_HeldOutput]) -> list[Path]:
    recoverable: list[Path] = []
    for output in reversed(outputs):
        try:
            recovered = _move_public_output_to_recovery(output)
        except Exception:
            # Rollback is best-effort and output-local. Preserve and report the
            # requested name if cleanup itself fails, then attempt its sibling.
            recovered = output.path
        recoverable.append(recovered)
    return sorted(set(recoverable), key=str)


def _rollback_error(error: Exception, outputs: Sequence[_HeldOutput]) -> BenchmarkError:
    recoverable = _rollback_created_outputs(outputs)
    message = str(error)
    if recoverable:
        locations = ", ".join(str(path) for path in recoverable)
        message += f"; recoverable rollback path(s): {locations}"
    return BenchmarkError(message)


def _publish_pair(
    results: Path,
    result_bytes: bytes,
    markdown: Path,
    markdown_bytes: bytes,
    *,
    accept: Callable[[], None] | None = None,
) -> None:
    targets = ((Path(results), result_bytes), (Path(markdown), markdown_bytes))
    parents: list[int] = []
    outputs: list[_HeldOutput] = []
    parent_metadata: list[tuple[int, int]] = []
    try:
        for target, _ in targets:
            parent_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            parents.append(parent_fd)
            metadata = os.fstat(parent_fd)
            parent_metadata.append((metadata.st_dev, metadata.st_ino))
        for index, ((target, encoded), parent_fd) in enumerate(zip(targets, parents)):
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            output_fd = os.open(target.name, flags, 0o600, dir_fd=parent_fd)
            opened = os.fstat(output_fd)
            identity = (opened.st_dev, opened.st_ino)
            output = _HeldOutput(
                path=target,
                description=("results output" if index == 0 else "Markdown output"),
                descriptor=output_fd,
                parent_descriptor=parent_fd,
                identity=identity,
                parent_identity=parent_metadata[index],
                metadata=_file_metadata(opened),
                expected=encoded,
            )
            outputs.append(output)
            _write_fd(output_fd, encoded)
            os.fsync(output_fd)
            metadata = os.fstat(output_fd)
            linked = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size != len(encoded)
                or (metadata.st_dev, metadata.st_ino) != (linked.st_dev, linked.st_ino)
            ):
                raise BenchmarkError("report output publication integrity check failed")
            output.metadata = _file_metadata(metadata)
        for index, ((target, _), parent_fd) in enumerate(zip(targets, parents)):
            current = target.parent.stat()
            if (current.st_dev, current.st_ino) != parent_metadata[index]:
                raise BenchmarkError("report output parent changed during publication")
            os.fsync(parent_fd)
        _accept_output_set(outputs, accept)
    except BenchmarkError as error:
        raise _rollback_error(error, outputs) from error
    except OSError as error:
        publication_error = OSError(f"cannot publish report output pair: {error}")
        raise _rollback_error(publication_error, outputs) from error
    finally:
        for value in outputs:
            try:
                value.close()
            except OSError:
                pass
        for descriptor in parents:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_exact(
    path: Path,
    expected: bytes,
    description: str,
    *,
    expected_identity: tuple[int, int],
) -> _HeldOutput:
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        parent = os.fstat(parent_descriptor)
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        linked = os.stat(
            path.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != len(expected)
            or (metadata.st_dev, metadata.st_ino) != expected_identity
            or (linked.st_dev, linked.st_ino) != expected_identity
        ):
            raise BenchmarkError(f"{description} must be an exclusive regular file")
        value = _HeldOutput(
            path=path,
            description=description,
            descriptor=descriptor,
            parent_descriptor=parent_descriptor,
            identity=expected_identity,
            parent_identity=(parent.st_dev, parent.st_ino),
            metadata=_file_metadata(metadata),
            expected=expected,
        )
        _verify_held_output(value)
        return value
    except BenchmarkError:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise BenchmarkError(f"cannot verify {description}: {error}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render canonical PT-PT benchmark reports.")
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--verify-existing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    held: list[_HeldInput] = []
    held_outputs: list[_HeldOutput] = []
    try:
        scores_input = _open_held_input(
            arguments.scores, "scores input", limit=_MAX_SCORE_BYTES
        )
        held.append(scores_input)
        provenance_input = _open_held_input(
            arguments.provenance,
            "report provenance input",
            limit=_MAX_PROVENANCE_BYTES,
        )
        held.append(provenance_input)
        if scores_input.identity == provenance_input.identity:
            raise BenchmarkError("scores and report provenance inputs alias one file")
        document = _validate_score_document(scores_input.value)
        result_bytes, markdown_bytes = render_report(
            document,
            provenance_input.value,
            score_bytes=scores_input.encoded,
        )
        results, markdown, output_identities = _validate_targets(
            held, arguments.results, arguments.markdown,
            verify_existing=arguments.verify_existing,
        )
        def accept_inputs() -> None:
            for value in held:
                _verify_held_input(value)
        if arguments.verify_existing:
            held_outputs.append(
                _read_exact(
                    results, result_bytes, "results output",
                    expected_identity=output_identities[results],
                )
            )
            held_outputs.append(
                _read_exact(
                    markdown, markdown_bytes, "Markdown output",
                    expected_identity=output_identities[markdown],
                )
            )
            _accept_output_set(held_outputs, accept_inputs)
        else:
            _publish_pair(
                results, result_bytes, markdown, markdown_bytes,
                accept=accept_inputs,
            )
        sys.stdout.buffer.write(canonical_bytes({
            "results": str(arguments.results),
            "markdown": str(arguments.markdown),
        }))
        return 0
    except (BenchmarkError, ValueError, RecursionError, OverflowError, TypeError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    finally:
        for value in reversed(held_outputs):
            try:
                value.close()
            except OSError:
                pass
            try:
                os.close(value.parent_descriptor)
            except OSError:
                pass
        for value in reversed(held):
            value.close()


if __name__ == "__main__":
    raise SystemExit(main())
