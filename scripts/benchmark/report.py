from __future__ import annotations

import argparse
import math
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .blind import _parse_canonical_json
from .common import BenchmarkError, canonical_bytes, sha256_bytes
from .review_app import MQM_DIMENSIONS
from .schema import CONDITIONS, DIFFICULTIES, SCHEMA_VERSION, SURFACES, TASKS
from .score import evaluate_gates


_MAX_SCORE_BYTES = 8 * 1024 * 1024
_MAX_CELL_CHARS = 200
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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
    "review_mappings_sha256", "learned_metrics_sha256",
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
            {"available", "applicable", "passed", "failures", "validator_errors",
             "pass_rate", "paired_case_attempts",
             "paired_failure_difference_normal_minus_suite", "paired_difference"},
            f"invariant scorecard {name}",
        )
        _boolean(record["available"], f"invariant scorecard {name} available")
        for field in ("applicable", "passed", "failures", "validator_errors", "pass_rate"):
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
    for name in sorted(_PROVENANCE_FIELDS - {"bootstrap_seed", "review_mappings_sha256", "learned_metrics_sha256"}):
        digest = _text(provenance[name], f"provenance {name}")
        if _SHA256.fullmatch(digest) is None:
            raise BenchmarkError(f"provenance {name} must be a SHA-256 digest")
    _integer(provenance["bootstrap_seed"], "provenance bootstrap seed")
    for name in ("review_mappings_sha256", "learned_metrics_sha256"):
        if provenance[name] is not None:
            digest = _text(provenance[name], f"provenance {name}")
            if _SHA256.fullmatch(digest) is None:
                raise BenchmarkError(f"provenance {name} must be a SHA-256 digest or null")
    metrics = _validate_metrics(document["metrics"])
    expected_gates = evaluate_gates(metrics)
    if canonical_bytes(document["gates"]) != canonical_bytes(expected_gates):
        raise BenchmarkError("score gates do not match canonical Task 7 evaluation")
    return document


def _cell(value: object) -> str:
    raw = str(value)
    truncated = len(raw) > _MAX_CELL_CHARS
    raw = raw[:_MAX_CELL_CHARS]
    result: list[str] = []
    for character in raw:
        codepoint = ord(character)
        if codepoint < 32 or codepoint == 127:
            result.append(f"\\u{codepoint:04X}")
        elif character == "|":
            result.append("\\|")
        elif character == "\\":
            result.append("\\\\")
        elif character == "<":
            result.append("&lt;")
        elif character == ">":
            result.append("&gt;")
        else:
            result.append(character)
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


def _render_markdown(document: Mapping[str, object], result_bytes: bytes) -> bytes:
    metrics = document["metrics"]
    gates = document["gates"]
    translation = metrics["translation"]
    review = metrics["review"]
    cards = metrics["scorecards"]
    operational = metrics["operational"]

    sections: list[str] = [_HEADINGS[0]]
    sections.append(
        _HEADINGS[1]
        + f"\n\nOverall verdict: {_verdict(gates['overall']['passed'])}.\n\n"
        + "Scope: the frozen PT-PT v1 benchmark only; these results do not establish quality "
          "for other locales, datasets, models, or configurations."
    )
    sections.append(
        _HEADINGS[2]
        + "\n\n- Dataset: PT-PT v1\n- tested model: unavailable in the canonical Task 7 score document"
          "\n- Model: unavailable\n- Runner configuration: unavailable"
          f"\n- Bootstrap seed: `{document['provenance']['bootstrap_seed']}`"
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
         value["failures"]["suite"], value["validator_errors"]["suite"],
         _format_percent(value["pass_rate"]["suite"])]
        for name, value in sorted(cards["invariant"].items())
    ]
    sections.append(
        _HEADINGS[6]
        + f"\n\nSuite structural pass rate: {_format_percent(translation['suite_structural_pass_rate'])}."
        + "\n\nInvariant scorecard\n\n"
        + (_table(("Invariant", "Applicable", "Passed", "Failures", "Validator errors", "Suite pass rate"), invariant_rows)
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
    provenance_labels = {
        "dataset_sha256": "Dataset SHA-256",
        "dataset_manifest_sha256": "Dataset manifest SHA-256",
        "run_manifest_sha256": "Run manifest SHA-256",
        "review_bundle_sha256": "Review bundle SHA-256",
        "condition_key_sha256": "Private condition-key artifact SHA-256",
        "annotations_sha256": "Annotations SHA-256",
        "annotation_lock_sha256": "Annotation lock SHA-256",
        "validation_sha256": "Validation SHA-256",
        "review_mappings_sha256": "Review mappings SHA-256",
        "learned_metrics_sha256": "Learned metrics SHA-256",
    }
    provenance_rows = [
        [label, "unavailable" if document["provenance"][name] is None else document["provenance"][name]]
        for name, label in provenance_labels.items()
    ]
    sections.append(
        _HEADINGS[15]
        + f"\n\nCanonical results SHA-256: `{sha256_bytes(result_bytes)}`.\n\n"
        + _table(("Artifact", "Frozen digest"), provenance_rows)
        + "\n\nReproduce with:\n\n```text\npython3 -m scripts.benchmark.report "
          "--scores <canonical-scores.json> --results <results.json> --markdown <report.md>\n```"
    )
    sections.append(
        _HEADINGS[16]
        + "\n\nBenchmark artifacts are AI-generated. Human review covered only the frozen "
          "benchmark outputs. No human translator reviewed future translations. Human review "
          "of the frozen benchmark does not imply human review of future translations; these "
          "results do not imply such review."
    )
    return ("\n\n".join(sections) + "\n").encode("utf-8")


def render_report(score_document: Mapping[str, object]) -> tuple[bytes, bytes]:
    """Validate and render deterministic machine and Markdown benchmark results."""
    document = _validate_score_document(score_document)
    result_bytes = canonical_bytes(document)
    return result_bytes, _render_markdown(document, result_bytes)


def _read_score(path: Path) -> Mapping[str, object]:
    path = Path(path)
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise BenchmarkError("scores input must be a real regular file")
        if metadata.st_size > _MAX_SCORE_BYTES:
            raise BenchmarkError(f"scores input exceeds {_MAX_SCORE_BYTES} bytes")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            encoded = source.read(_MAX_SCORE_BYTES + 1)
            after = os.fstat(source.fileno())
        if len(encoded) > _MAX_SCORE_BYTES:
            raise BenchmarkError(f"scores input exceeds {_MAX_SCORE_BYTES} bytes")
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise BenchmarkError("scores input changed while reading")
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot read scores input: {error}") from error
    value = _parse_canonical_json(encoded, "scores input")
    return _validate_score_document(value)


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


def _validate_targets(scores: Path, results: Path, markdown: Path, *, verify_existing: bool) -> tuple[Path, Path]:
    allowed_symlinks = _symlink_ancestors(Path(scores).parent)
    results_target = _resolved_target(results, allowed_symlinks=allowed_symlinks)
    markdown_target = _resolved_target(markdown, allowed_symlinks=allowed_symlinks)
    scores_target = Path(scores).resolve(strict=True)
    if results_target == markdown_target:
        raise BenchmarkError("results and Markdown outputs must be distinct paths")
    if scores_target in {results_target, markdown_target}:
        raise BenchmarkError("output path aliases consumed scores input")
    identities: set[tuple[int, int]] = set()
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
        if identity in identities:
            raise BenchmarkError("results and Markdown outputs alias one file")
        identities.add(identity)
    return results_target, markdown_target


def _write_fd(descriptor: int, encoded: bytes) -> None:
    offset = 0
    while offset < len(encoded):
        written = os.write(descriptor, encoded[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _publish_pair(results: Path, result_bytes: bytes, markdown: Path, markdown_bytes: bytes) -> None:
    targets = ((Path(results), result_bytes), (Path(markdown), markdown_bytes))
    parents: list[int] = []
    outputs: list[int] = []
    created: list[tuple[int, str]] = []
    parent_metadata: list[tuple[int, int]] = []
    try:
        for target, _ in targets:
            parent_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            parents.append(parent_fd)
            metadata = os.fstat(parent_fd)
            parent_metadata.append((metadata.st_dev, metadata.st_ino))
        for index, ((target, encoded), parent_fd) in enumerate(zip(targets, parents)):
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            output_fd = os.open(target.name, flags, 0o600, dir_fd=parent_fd)
            outputs.append(output_fd)
            created.append((parent_fd, target.name))
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
        for index, ((target, _), parent_fd) in enumerate(zip(targets, parents)):
            current = target.parent.stat()
            if (current.st_dev, current.st_ino) != parent_metadata[index]:
                raise BenchmarkError("report output parent changed during publication")
            os.fsync(parent_fd)
    except BenchmarkError:
        for parent_fd, name in reversed(created):
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    except OSError as error:
        for parent_fd, name in reversed(created):
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
        raise BenchmarkError(f"cannot publish report output pair: {error}") from error
    finally:
        for descriptor in outputs:
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in parents:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_exact(path: Path, expected: bytes, description: str) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise BenchmarkError(f"{description} must be an exclusive regular file")
            encoded = source.read(len(expected) + 1)
        if encoded != expected:
            raise BenchmarkError(f"{description} does not match deterministic rendering")
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot verify {description}: {error}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render canonical PT-PT benchmark reports.")
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--verify-existing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        document = _read_score(arguments.scores)
        result_bytes, markdown_bytes = render_report(document)
        results, markdown = _validate_targets(
            arguments.scores, arguments.results, arguments.markdown,
            verify_existing=arguments.verify_existing,
        )
        if arguments.verify_existing:
            _read_exact(results, result_bytes, "results output")
            _read_exact(markdown, markdown_bytes, "Markdown output")
        else:
            _publish_pair(results, result_bytes, markdown, markdown_bytes)
        sys.stdout.buffer.write(canonical_bytes({
            "results": str(arguments.results),
            "markdown": str(arguments.markdown),
        }))
        return 0
    except (BenchmarkError, ValueError, RecursionError, OverflowError, TypeError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
