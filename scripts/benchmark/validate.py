from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import uuid4

from .common import BenchmarkError, canonical_bytes, read_json, read_jsonl, sha256_bytes
from .run import RunResult
from .schema import SCHEMA_VERSION


_INVARIANTS_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills/reviewing-translations/scripts/invariants.py"
)
_INVARIANTS_SPEC = importlib.util.spec_from_file_location(
    "reviewing_translation_invariants", _INVARIANTS_PATH,
)
if _INVARIANTS_SPEC is None or _INVARIANTS_SPEC.loader is None:
    raise RuntimeError("unable to load reviewing-translations invariants")
_INVARIANTS = importlib.util.module_from_spec(_INVARIANTS_SPEC)
sys.modules[_INVARIANTS_SPEC.name] = _INVARIANTS
_INVARIANTS_SPEC.loader.exec_module(_INVARIANTS)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
OUTPUT_CONTRACT = "output_contract"
_REVIEW_FIELDS = {"corrected_translation", "issues"}
_ISSUE_FIELDS = {"category", "source_span", "candidate_span", "explanation"}


@dataclass(frozen=True)
class Finding:
    invariant: str
    severity: str
    expected: object
    observed: object
    affected_span: tuple[int, int] | None
    message: str


@dataclass(frozen=True)
class ValidationResult:
    case_id: str
    output: str
    status: str
    findings: tuple[Finding, ...]
    validator_errors: tuple[str, ...]
    applicable_checks: int
    passed_checks: int
    failed_checks: int
    skipped_checks: int
    validator_error_checks: int
    skipped_invariants: tuple[str, ...]
    run_id: str | None = None

    def to_record(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "status": self.status,
            "output": self.output,
            "findings": [asdict(finding) for finding in self.findings],
            "validator_errors": list(self.validator_errors),
            "applicable_checks": self.applicable_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "skipped_checks": self.skipped_checks,
            "validator_error_checks": self.validator_error_checks,
            "skipped_invariants": list(self.skipped_invariants),
        }


def applicable_invariants(case: Mapping[str, object]) -> tuple[str, ...]:
    checks = case.get("automatic_checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise BenchmarkError(f"case {case.get('id')} automatic_checks must be a list")
    declared = []
    for index, check in enumerate(checks):
        if not isinstance(check, Mapping) or not isinstance(check.get("type"), str):
            raise BenchmarkError(f"case {case.get('id')} automatic check {index} is invalid")
        declared.append(check["type"])
    return (OUTPUT_CONTRACT, *declared)


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _strict_json(text: str) -> object:
    return json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_json_constant,
    )


def _contract_failure(output: str, message: str) -> tuple[Finding, ...]:
    return (Finding(
        invariant=OUTPUT_CONTRACT,
        severity="critical",
        expected="exact caller-requested artifact",
        observed=output,
        affected_span=(0, len(output)),
        message=message,
    ),)


def _is_json_string(text: str) -> bool:
    try:
        return isinstance(_strict_json(text.strip()), str)
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


def _is_complete_fence(text: str) -> bool:
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return False
    opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})[^\n]*", lines[0])
    if opening is None:
        return False
    marker = opening.group(1)
    closing = re.fullmatch(
        rf" {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*",
        lines[-1],
    )
    return closing is not None


def _source(case: Mapping[str, object]) -> str:
    value = case.get("source")
    if not isinstance(value, str):
        raise ValueError("case source must be text")
    return value


def _output_contract(
    case: Mapping[str, object], output: str,
) -> tuple[str | None, tuple[Finding, ...]]:
    task = case.get("task", "translation")
    source = _source(case)
    if not output.strip():
        return None, _contract_failure(output, "candidate output is empty")
    if task == "review":
        try:
            payload = _strict_json(output)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            return None, _contract_failure(
                output, f"review output is not exact JSON: {error}",
            )
        valid = (
            isinstance(payload, dict)
            and set(payload) == _REVIEW_FIELDS
            and isinstance(payload.get("corrected_translation"), str)
            and isinstance(payload.get("issues"), list)
            and all(
                isinstance(issue, dict)
                and set(issue) == _ISSUE_FIELDS
                and all(isinstance(issue[field], str) for field in _ISSUE_FIELDS)
                for issue in payload.get("issues", [])
            )
        )
        if not valid:
            return None, _contract_failure(
                output, "review output does not match the exact schema",
            )
        return payload["corrected_translation"], ()
    if task != "translation":
        raise BenchmarkError(f"case {case.get('id')} has unknown task: {task!r}")
    if _is_complete_fence(output) and not _is_complete_fence(source):
        return None, _contract_failure(
            output, "translation has an added presentation fence",
        )
    if _is_json_string(output) and not _is_json_string(source):
        return None, _contract_failure(
            output, "translation has an added JSON-string wrapper",
        )
    return output, ()


CHECKS = _INVARIANTS.CHECKS
_validate_invariants_with_evidence = _INVARIANTS._validate_invariants_with_evidence
SUPPORTED_INVARIANTS = frozenset((OUTPUT_CONTRACT, *CHECKS))


def validate_output(case: Mapping[str, object], output: str) -> ValidationResult:
    if not isinstance(case, Mapping):
        raise BenchmarkError("case must be an object")
    if not isinstance(output, str):
        raise BenchmarkError("candidate output must be text")
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise BenchmarkError("case id must be non-empty text")
    checks = case.get("automatic_checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise BenchmarkError(f"case {case_id} automatic_checks must be a list")
    findings: list[Finding] = []
    errors: list[str] = []
    selected_output, contract_findings = _output_contract(case, output)
    findings.extend(contract_findings)
    passed = 0 if contract_findings else 1
    failed = 1 if contract_findings else 0
    validator_errors = 0
    skipped_invariants: tuple[str, ...] = ()
    if selected_output is None:
        skipped_invariants = tuple(
            check.get("type", f"check[{index}]")
            if isinstance(check, Mapping)
            else f"check[{index}]"
            for index, check in enumerate(checks)
        )
        status = "failed"
        return ValidationResult(
            case_id=case_id,
            output=output,
            status=status,
            findings=tuple(findings),
            validator_errors=(),
            applicable_checks=1 + len(checks),
            passed_checks=passed,
            failed_checks=failed,
            skipped_checks=len(skipped_invariants),
            validator_error_checks=0,
            skipped_invariants=skipped_invariants,
        )
    for index, check in enumerate(checks):
        if not isinstance(check, Mapping):
            errors.append(f"check[{index}]: TypeError: check declaration must be an object")
            validator_errors += 1
            continue
        check_type = check.get("type")
        try:
            evidence_findings = _validate_invariants_with_evidence(
                source=case.get("source"),
                candidate=selected_output,
                source_locale=case.get("source_locale"),
                target_locale=case.get("target_locale"),
                protected_terms=case.get("protected_terms", ()),
                checks=(check,),
                _context=case,
            )
            check_findings = tuple(
                Finding(
                    invariant=finding.check,
                    severity=finding.severity,
                    expected=finding.expected,
                    observed=finding.observed,
                    affected_span=finding.span,
                    message=finding.message,
                )
                for finding in evidence_findings
            )
        except Exception as error:
            errors.append(f"{check_type or f'check[{index}]'}: {type(error).__name__}: {error}")
            validator_errors += 1
            continue
        findings.extend(check_findings)
        if check_findings:
            failed += 1
        else:
            passed += 1
    status = "validator_error" if errors else ("failed" if findings else "passed")
    return ValidationResult(
        case_id=case_id,
        output=output,
        status=status,
        findings=tuple(findings),
        validator_errors=tuple(errors),
        applicable_checks=1 + len(checks),
        passed_checks=passed,
        failed_checks=failed,
        skipped_checks=0,
        validator_error_checks=validator_errors,
        skipped_invariants=(),
    )


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
        or not run_ids
        or not all(isinstance(run_id, str) and run_id for run_id in run_ids)
    ):
        raise BenchmarkError("run manifest schedule has invalid run ids")
    if len(run_ids) != len(set(run_ids)):
        raise BenchmarkError("run manifest schedule has duplicate run ids")
    digest = schedule.get("sha256")
    expected_digest = sha256_bytes(canonical_bytes(run_ids))
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise BenchmarkError("run manifest schedule hash is required")
    if digest != expected_digest:
        raise BenchmarkError("run manifest schedule hash mismatch")
    return run_ids


def _cases_by_id(dataset_dir: Path) -> dict[str, dict]:
    records = read_jsonl(dataset_dir / "cases.jsonl")
    result: dict[str, dict] = {}
    for record in records:
        case_id = record.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise BenchmarkError("dataset case id must be non-empty text")
        if case_id in result:
            raise BenchmarkError(f"duplicate dataset case id: {case_id}")
        result[case_id] = record
    return result


def _runs_by_id(evidence_dir: Path, expected_ids: Sequence[str]) -> dict[str, RunResult]:
    records = read_jsonl(evidence_dir / "runs.jsonl")
    result: dict[str, RunResult] = {}
    for record in records:
        run = RunResult.from_record(record)
        run_id = run.run_id
        if not isinstance(run_id, str) or not run_id:
            raise BenchmarkError("run record id must be non-empty text")
        if run_id in result:
            raise BenchmarkError(f"duplicate run id: {run_id}")
        result[run_id] = run
    expected = set(expected_ids)
    actual = set(result)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise BenchmarkError(
            f"incomplete run set: missing={missing!r}, unknown={unknown!r}"
        )
    return result


def _raw_output(evidence_dir: Path, record: RunResult) -> str:
    relative = record.raw_output_path
    digest = record.output_sha256
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise BenchmarkError("run record has invalid raw output path")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise BenchmarkError("run record has invalid output hash")
    raw_directory = evidence_dir / "raw"
    if raw_directory.is_symlink():
        raise BenchmarkError("refusing symlink raw evidence directory")
    raw_root = raw_directory.resolve()
    path = evidence_dir / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(raw_root)
    except (OSError, ValueError) as error:
        raise BenchmarkError(f"raw output path escapes raw evidence: {relative}") from error
    if path.is_symlink():
        raise BenchmarkError(f"refusing symlink raw output: {relative}")
    try:
        encoded = path.read_bytes()
    except OSError as error:
        raise BenchmarkError(f"cannot read raw output {relative}: {error}") from error
    if sha256_bytes(encoded) != digest:
        raise BenchmarkError(f"raw output hash mismatch: {relative}")
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BenchmarkError(f"raw output is not UTF-8: {relative}") from error


def _refuse_raw_destination(evidence_dir: Path, destination: Path) -> None:
    raw_root = (evidence_dir / "raw").resolve()
    try:
        destination.resolve().relative_to(raw_root)
    except ValueError:
        return
    raise BenchmarkError("validation output must not be written inside raw evidence")


def _refuse_input_destination(destination: Path, inputs: Sequence[Path]) -> None:
    destination = Path(destination)
    destination_resolved = destination.resolve()
    for input_path in inputs:
        input_path = Path(input_path)
        try:
            aliases = destination_resolved == input_path.resolve()
            if not aliases and destination.exists() and input_path.exists():
                aliases = os.path.samefile(destination, input_path)
        except OSError as error:
            raise BenchmarkError(f"cannot compare validation output with input {input_path}: {error}") from error
        if aliases:
            raise BenchmarkError(f"validation output aliases consumed input: {input_path}")


def _atomic_write_jsonl(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    path = Path(path)
    if path.is_symlink():
        raise BenchmarkError(f"refusing symlink output path: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise BenchmarkError(f"cannot create validation output directory: {error}") from error
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            for record in records:
                target.write(canonical_bytes(record))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise BenchmarkError(f"cannot atomically write validation JSONL {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validate_runs(
    dataset_dir: Path,
    evidence_dir: Path,
    output_path: Path | None = None,
) -> list[ValidationResult]:
    dataset_dir = Path(dataset_dir)
    evidence_dir = Path(evidence_dir)
    destination = Path(output_path) if output_path is not None else evidence_dir / "validation.jsonl"
    if evidence_dir.is_symlink():
        raise BenchmarkError(f"refusing symlink evidence directory: {evidence_dir}")
    manifest_path = evidence_dir / "run-manifest.json"
    runs_path = evidence_dir / "runs.jsonl"
    cases_path = dataset_dir / "cases.jsonl"
    _refuse_input_destination(destination, (manifest_path, runs_path, cases_path))
    _refuse_raw_destination(evidence_dir, destination)
    run_ids = _manifest_run_ids(evidence_dir)
    cases = _cases_by_id(dataset_dir)
    runs = _runs_by_id(evidence_dir, run_ids)
    raw_inputs: list[Path] = []
    for run in runs.values():
        if (
            not isinstance(run.raw_output_path, str)
            or not run.raw_output_path
            or Path(run.raw_output_path).is_absolute()
        ):
            raise BenchmarkError("run record has invalid raw output path")
        raw_inputs.append(evidence_dir / run.raw_output_path)
    _refuse_input_destination(
        destination,
        raw_inputs,
    )
    results: list[ValidationResult] = []
    for run_id in run_ids:
        run = runs[run_id]
        case_id = run.case_id
        if not isinstance(case_id, str) or case_id not in cases:
            raise BenchmarkError(f"run {run_id} has unknown case id: {case_id!r}")
        output = _raw_output(evidence_dir, run)
        results.append(replace(validate_output(cases[case_id], output), run_id=run_id))
    _atomic_write_jsonl(destination, [result.to_record() for result in results])
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate PT-PT benchmark product integrity.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        results = validate_runs(arguments.dataset, arguments.evidence, arguments.output)
        sys.stdout.buffer.write(canonical_bytes({"runs": len(results), "output": str(arguments.output)}))
        return 0
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
