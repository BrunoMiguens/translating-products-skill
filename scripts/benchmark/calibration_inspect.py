from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .cli_calibration import (
    CalibrationPack,
    TaskFilter,
    select_tasks,
    task_states,
)
from .common import BenchmarkError, read_jsonl
from .validate import validate_output


@dataclass(frozen=True)
class InspectionResult:
    exit_code: int
    lines: tuple[str, ...]


def inspect_pack(
    pack: CalibrationPack,
    dataset_dir: Path,
    task_filter: TaskFilter,
) -> InspectionResult:
    cases = {}
    for case in read_jsonl(Path(dataset_dir) / "cases.jsonl"):
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in cases:
            raise BenchmarkError("dataset contains an invalid or duplicate case id")
        cases[case_id] = case

    selected = select_tasks(pack, task_filter)
    states = {
        state.task.run_id: state
        for state in task_states(pack, task_filter)
    }
    counts: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    failures: list[str] = []
    exit_code = 0
    for task in selected:
        state = states[task.run_id]
        group = (task.app, task.condition)
        if state.status != "completed":
            counts[group][state.status] += 1
            failures.append(
                f"{task.run_id} evidence {state.status}: {state.reason or ''}".rstrip()
            )
            exit_code = 2
            continue
        case = cases.get(task.case_id)
        if case is None:
            counts[group]["invalid"] += 1
            failures.append(f"{task.run_id} unknown case {task.case_id}")
            exit_code = 2
            continue
        try:
            output = task.response_path.read_text(encoding="utf-8")
            validation = validate_output(case, output)
        except (OSError, UnicodeError, BenchmarkError) as error:
            counts[group]["invalid"] += 1
            failures.append(f"{task.run_id} inspection error: {error}")
            exit_code = 2
            continue
        counts[group][validation.status] += 1
        for finding in validation.findings:
            failures.append(
                f"{task.run_id} {finding.invariant}: {finding.message}"
            )
        for error in validation.validator_errors:
            failures.append(f"{task.run_id} validator_error: {error}")
        if validation.status == "validator_error":
            exit_code = 2
        elif validation.status == "failed" and exit_code == 0:
            exit_code = 1

    statuses = ("passed", "failed", "validator_error", "pending", "invalid")
    lines = tuple(
        f"{app}/{condition}: "
        + " ".join(
            f"{name}={counts[(app, condition)][name]}" for name in statuses
        )
        for app in ("claude", "codex")
        for condition in ("normal", "context_only", "suite")
        if (app, condition) in counts
    )
    return InspectionResult(exit_code, (*lines, *failures))
