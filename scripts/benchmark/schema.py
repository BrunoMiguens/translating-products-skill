from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .common import BenchmarkError


SCHEMA_VERSION = 1
DATASET_VERSION = "pt-pt-v1"
SURFACES = ("ui-mobile", "web", "marketing", "app-store", "documentation")
TASKS = ("translation", "review")
DIFFICULTIES = ("simple", "contextual", "adversarial")
CONDITIONS = ("normal", "suite", "context_only")
PRIMARY_ATTEMPTS = 3
EXPECTED_TOTAL = 60
EXPECTED_BY_TASK = {"translation": 40, "review": 20}
EXPECTED_BY_SURFACE = {surface: 12 for surface in SURFACES}
EXPECTED_BY_DIFFICULTY = {difficulty: 20 for difficulty in DIFFICULTIES}
EXPECTED_DIAGNOSTIC = 15

CASE_REQUIRED = {
    "id", "dataset_version", "task", "surface", "content_type", "difficulty",
    "diagnostic", "source_locale", "target_locale", "source", "context",
    "audience", "register", "constraints", "glossary", "protected_terms",
    "invariants", "reference", "reference_notes", "automatic_checks",
}
_CASE_ALLOWED = CASE_REQUIRED | {"candidate"}
_SEEDED_ERROR_REQUIRED = {
    "id", "dimension", "severity", "candidate_span", "accepted_corrections",
    "correction_required", "notes",
}
_SEEDED_SEVERITIES = {"critical", "major", "minor", "neutral"}


def _require_equal(actual: object, expected: object, description: str) -> None:
    if actual != expected:
        raise BenchmarkError(f"invalid {description}: expected {expected!r}, got {actual!r}")


def _validate_seeded_errors(seeded_errors: Mapping[str, object], review_ids: set[str]) -> None:
    _require_equal(set(seeded_errors), review_ids, "seeded-error case ids")
    seeded_ids: set[str] = set()
    for case_id, inventory in seeded_errors.items():
        if not isinstance(inventory, list) or not inventory:
            raise BenchmarkError(f"review case {case_id} requires a non-empty seeded error inventory")
        for error in inventory:
            if not isinstance(error, Mapping):
                raise BenchmarkError(f"seeded error for {case_id} must be an object")
            missing = _SEEDED_ERROR_REQUIRED - set(error)
            if missing:
                raise BenchmarkError(
                    f"seeded error for {case_id} missing required fields: {sorted(missing)!r}"
                )
            error_id = error["id"]
            if not isinstance(error_id, str) or not error_id:
                raise BenchmarkError(f"seeded error for {case_id} has an invalid id")
            if error_id in seeded_ids:
                raise BenchmarkError(f"duplicate seeded error id: {error_id}")
            seeded_ids.add(error_id)
            if not isinstance(error["dimension"], str) or not error["dimension"]:
                raise BenchmarkError(f"seeded error {error_id} has an invalid dimension")
            if error["severity"] not in _SEEDED_SEVERITIES:
                raise BenchmarkError(f"seeded error {error_id} has an invalid severity")
            if not isinstance(error["candidate_span"], str) or not error["candidate_span"]:
                raise BenchmarkError(f"seeded error {error_id} has an invalid candidate span")
            corrections = error["accepted_corrections"]
            if (
                not isinstance(corrections, list)
                or not corrections
                or not all(isinstance(item, str) and item for item in corrections)
            ):
                raise BenchmarkError(f"seeded error {error_id} has invalid accepted corrections")
            if type(error["correction_required"]) is not bool:
                raise BenchmarkError(f"seeded error {error_id} must state correction_required")
            if not isinstance(error["notes"], str) or not error["notes"]:
                raise BenchmarkError(f"seeded error {error_id} requires reviewer notes")


def validate_cases(cases: Sequence[Mapping[str, object]], seeded_errors: Mapping[str, object]) -> None:
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        raise BenchmarkError("cases must be a list")
    if not isinstance(seeded_errors, Mapping):
        raise BenchmarkError("seeded errors must be an object keyed by review case id")

    ids: set[str] = set()
    review_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise BenchmarkError("case must be an object")
        fields = set(case)
        missing = CASE_REQUIRED - fields
        unknown = fields - _CASE_ALLOWED
        if missing:
            raise BenchmarkError(f"case missing required fields: {sorted(missing)!r}")
        if unknown:
            raise BenchmarkError(f"case has unknown fields: {sorted(unknown)!r}")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id:
            raise BenchmarkError("case id must be a non-empty string")
        if case_id in ids:
            raise BenchmarkError(f"duplicate case id: {case_id}")
        ids.add(case_id)
        _require_equal(case["dataset_version"], DATASET_VERSION, "dataset version")
        if case["task"] not in TASKS:
            raise BenchmarkError(f"invalid task: {case['task']!r}")
        if case["surface"] not in SURFACES:
            raise BenchmarkError(f"invalid surface: {case['surface']!r}")
        if case["difficulty"] not in DIFFICULTIES:
            raise BenchmarkError(f"invalid difficulty: {case['difficulty']!r}")
        if type(case["diagnostic"]) is not bool:
            raise BenchmarkError("diagnostic must be a boolean")
        if case["task"] == "review":
            if not isinstance(case.get("candidate"), str) or not case["candidate"]:
                raise BenchmarkError(f"review case {case_id} requires a candidate")
            review_ids.add(case_id)
        elif "candidate" in case:
            raise BenchmarkError(f"translation case {case_id} must not contain candidate")

    _require_equal(len(cases), EXPECTED_TOTAL, "case count")
    _require_equal(Counter(case["task"] for case in cases), EXPECTED_BY_TASK, "task balance")
    _require_equal(Counter(case["surface"] for case in cases), EXPECTED_BY_SURFACE, "surface balance")
    _require_equal(Counter(case["difficulty"] for case in cases), EXPECTED_BY_DIFFICULTY, "difficulty balance")
    _require_equal(sum(case["diagnostic"] for case in cases), EXPECTED_DIAGNOSTIC, "diagnostic count")
    _validate_seeded_errors(seeded_errors, review_ids)
