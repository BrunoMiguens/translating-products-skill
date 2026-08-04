from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .common import (
    BenchmarkError,
    append_jsonl_fsync,
    atomic_write_json,
    canonical_bytes,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_text,
    utc_now,
)
from .prepare import verify_dataset_manifest
from .prompts import render_prompt
from .schema import PRIMARY_ATTEMPTS, SCHEMA_VERSION


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MODES = {"fake", "cli", "manual"}
_PRIMARY_CONDITIONS = ("normal", "suite")
_SAFE_ENVIRONMENT_KEYS = ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR")


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    case_id: str
    condition: str
    attempt: int

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not _RUN_ID.fullmatch(self.run_id):
            raise BenchmarkError(f"invalid run id: {self.run_id!r}")
        if not isinstance(self.case_id, str) or not self.case_id:
            raise BenchmarkError("case id must be non-empty text")
        if self.condition not in {"normal", "suite", "context_only"}:
            raise BenchmarkError(f"invalid run condition: {self.condition!r}")
        if type(self.attempt) is not int or not 1 <= self.attempt <= PRIMARY_ATTEMPTS:
            raise BenchmarkError(f"invalid run attempt: {self.attempt!r}")


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    shell: bool
    process_started: bool
    started_at: str
    completed_at: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    refused: bool = False
    malformed_output: bool = False
    tool_misuse: bool = False
    reason: str | None = None
    telemetry: object = None

    @classmethod
    def from_completed_process(
        cls,
        argv: Sequence[str],
        started_at: str,
        completed_at: str,
        completed: subprocess.CompletedProcess[str],
    ) -> "Invocation":
        return cls(
            tuple(argv), False, True, started_at, completed_at,
            completed.returncode, completed.stdout, completed.stderr,
        )


@dataclass(frozen=True)
class RunResult:
    run_id: str
    case_id: str
    condition: str
    attempt: int
    runner_mode: str
    status: str
    failure_class: str
    process_started: bool
    started_at: str
    completed_at: str
    exit_code: int | None
    timed_out: bool
    refused: bool
    malformed_output: bool
    tool_misuse: bool
    reason: str | None
    output_sha256: str
    raw_output_path: str
    stderr: str
    telemetry: object
    redacted: bool
    project_fingerprint: str
    argv: tuple[str, ...]
    shell: bool

    def to_record(self) -> dict:
        record = asdict(self)
        record["schema_version"] = SCHEMA_VERSION
        record["argv"] = list(self.argv)
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "RunResult":
        values = dict(record)
        values.pop("schema_version", None)
        values["argv"] = tuple(values.get("argv", ()))
        try:
            return cls(**values)
        except (TypeError, ValueError) as error:
            raise BenchmarkError(f"malformed existing run record: {error}") from error


class LiteralSecretRedactor:
    def __init__(self, secrets: Sequence[str]):
        self._secrets = tuple(sorted({value for value in secrets if value}, key=len, reverse=True))

    def text(self, value: str) -> tuple[str, bool]:
        result = value
        for secret in self._secrets:
            result = result.replace(secret, "[REDACTED]")
        return result, result != value

    def value(self, value: object) -> tuple[object, bool]:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            changed = False
            result = []
            for item in value:
                redacted, item_changed = self.value(item)
                result.append(redacted)
                changed = changed or item_changed
            return result, changed
        if isinstance(value, tuple):
            redacted, changed = self.value(list(value))
            return tuple(redacted), changed
        if isinstance(value, Mapping):
            changed = False
            result = {}
            for key, item in value.items():
                redacted, item_changed = self.value(item)
                result[key] = redacted
                changed = changed or item_changed
            return result, changed
        return value, False


class FakeRunner:
    """A deterministic, offline runner used for pipeline tests and dry runs."""

    def __init__(
        self,
        output: str = "deterministic fake response",
        *,
        stderr: str = "",
        telemetry: object = None,
        exit_code: int = 0,
    ):
        self.output = output
        self.stderr = stderr
        self.telemetry = {} if telemetry is None else telemetry
        self.exit_code = exit_code
        self.prompts: list[str] = []

    def invoke(self, *, prompt: str, project_dir: Path, timeout_seconds: int) -> Invocation:
        del project_dir, timeout_seconds
        self.prompts.append(prompt)
        started_at = utc_now()
        return Invocation(
            ("deterministic-fake-agent",), False, True, started_at, utc_now(),
            self.exit_code, self.output, self.stderr, telemetry=self.telemetry,
        )


class CliRunner:
    """Invoke an agent with a trusted argv while sending untrusted prompts on stdin."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ):
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            raise BenchmarkError("runner command must be an array of strings")
        if not command or not all(isinstance(value, str) and value for value in command):
            raise BenchmarkError("CLI runner command must contain non-empty strings")
        self.command = tuple(command)
        if environment is None:
            environment = {
                key: os.environ[key]
                for key in _SAFE_ENVIRONMENT_KEYS
                if key in os.environ
            }
        self.safe_environment = dict(environment)

    def invoke(self, *, prompt: str, project_dir: Path, timeout_seconds: int) -> Invocation:
        argv = tuple(_replace_trusted_token(arg, project_dir) for arg in self.command)
        started_at = utc_now()
        try:
            completed = subprocess.run(
                argv,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=project_dir,
                timeout=timeout_seconds,
                check=False,
                shell=False,
                env=self.safe_environment,
            )
        except subprocess.TimeoutExpired as error:
            return Invocation(
                argv, False, True, started_at, utc_now(), None,
                _timeout_text(error.stdout), _timeout_text(error.stderr),
                timed_out=True, reason="timeout",
            )
        except OSError as error:
            return Invocation(
                argv, False, False, started_at, utc_now(), None, "", "",
                reason=error.__class__.__name__ + (f": {error}" if str(error) else ""),
            )
        return Invocation.from_completed_process(argv, started_at, utc_now(), completed)


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _replace_trusted_token(argument: str, project_dir: Path) -> str:
    return argument.replace("{project_dir}", str(project_dir))


def validate_runner_config(config: Mapping[str, object]) -> dict:
    if not isinstance(config, Mapping):
        raise BenchmarkError("runner config must be an object")
    required = {
        "schema_version", "agent", "host_version", "model", "mode", "command",
        "settings", "timeout_seconds", "suite_path", "scratch_root",
    }
    missing = required - set(config)
    if missing:
        raise BenchmarkError(f"runner config missing fields: {sorted(missing)!r}")
    if config["schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError("runner config schema version mismatch")
    for field in ("agent", "host_version", "model"):
        if not isinstance(config[field], str) or not config[field].strip():
            raise BenchmarkError(f"runner config {field} must be nonblank text")
    mode = config["mode"]
    if not isinstance(mode, str):
        raise BenchmarkError("runner config mode must be text")
    if mode not in _MODES:
        raise BenchmarkError(f"unsupported runner mode: {mode!r}")
    command = config["command"]
    if isinstance(command, (str, bytes)) or not isinstance(command, list):
        raise BenchmarkError("runner command must be an array of strings")
    if not all(isinstance(value, str) and value for value in command):
        raise BenchmarkError("runner command entries must be non-empty strings")
    if mode == "cli" and not command:
        raise BenchmarkError("CLI runner command must not be empty")
    if not isinstance(config["settings"], Mapping):
        raise BenchmarkError("runner settings must be an object")
    research_policy = config["settings"].get("research")
    if research_policy not in ("case-declared-only", "disabled"):
        raise BenchmarkError(
            "runner research policy must be case-declared-only or disabled"
        )
    if type(config["timeout_seconds"]) is not int or config["timeout_seconds"] <= 0:
        raise BenchmarkError("runner timeout_seconds must be a positive integer")
    for field in ("suite_path", "scratch_root"):
        if not isinstance(config[field], str) or not config[field]:
            raise BenchmarkError(f"runner {field} must be non-empty text")
    secret_env = config.get("secret_env", [])
    if (
        not isinstance(secret_env, list)
        or not all(isinstance(name, str) and name for name in secret_env)
        or len(secret_env) != len(set(secret_env))
    ):
        raise BenchmarkError("secret_env must be an array of unique non-empty names")
    env_allowlist = config.get("env_allowlist", [])
    if not isinstance(env_allowlist, list) or not all(
        isinstance(name, str) and name for name in env_allowlist
    ):
        raise BenchmarkError("env_allowlist must be an array of non-empty names")
    retries = config.get("prestart_retries", 2)
    if type(retries) is not int or retries < 0:
        raise BenchmarkError("prestart_retries must be a non-negative integer")
    _validate_primary_policy(config)
    return dict(config)


def _validate_primary_policy(config: Mapping[str, object]) -> None:
    condition_settings = config.get("condition_settings")
    if condition_settings is None:
        return
    if not isinstance(condition_settings, Mapping):
        raise BenchmarkError("condition_settings must be an object")
    if any(condition not in condition_settings for condition in _PRIMARY_CONDITIONS):
        raise BenchmarkError("primary condition settings require normal and suite")
    base = dict(config["settings"])
    normalized = []
    for condition in _PRIMARY_CONDITIONS:
        override = condition_settings[condition]
        if not isinstance(override, Mapping):
            raise BenchmarkError("primary condition settings must be objects")
        normalized.append(base | dict(override))
    if normalized[0] != normalized[1]:
        raise BenchmarkError("primary condition settings and policies must be identical")


def build_schedule(cases: list[dict], config: dict, seed: int) -> list[RunSpec]:
    validated = validate_runner_config(config)
    if type(seed) is not int:
        raise BenchmarkError("schedule seed must be an integer")
    seen: set[str] = set()
    specs: list[RunSpec] = []
    for case in sorted(cases, key=lambda value: value["id"]):
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise BenchmarkError("case id must be non-empty text")
        if case_id in seen:
            raise BenchmarkError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        conditions = ["normal", "suite"]
        if case.get("diagnostic") is True:
            conditions.append("context_only")
        for condition in conditions:
            for attempt in range(1, PRIMARY_ATTEMPTS + 1):
                identity = f"{case_id}:{condition}:{attempt}:{validated['model']}"
                specs.append(
                    RunSpec(sha256_text(identity)[:20], case_id, condition, attempt)
                )
    random.Random(seed).shuffle(specs)
    return specs


def classify_failure(record: Mapping[str, object]) -> str:
    if record.get("process_started") is not True:
        return "infrastructure"
    if (
        record.get("timed_out") is True
        or record.get("refused") is True
        or record.get("malformed_output") is True
        or record.get("tool_misuse") is True
        or record.get("exit_code") not in (0, None)
    ):
        return "model_outcome"
    return "success"


def _cases_by_id(cases: Sequence[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise BenchmarkError("case id must be non-empty text")
        if case_id in result:
            raise BenchmarkError(f"duplicate case id: {case_id}")
        result[case_id] = case
    return result


def _compact_context(case: Mapping[str, object]) -> dict:
    return {
        key: case[key]
        for key in ("audience", "glossary", "protected_terms", "style")
        if key in case
    }


def _render(spec: RunSpec, cases: Mapping[str, dict], templates: Mapping[str, str]) -> str:
    try:
        case = cases[spec.case_id]
    except KeyError as error:
        raise BenchmarkError(f"schedule references unknown case id: {spec.case_id}") from error
    return render_prompt(case, spec.condition, templates, _compact_context(case))


def _refuse_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise BenchmarkError(f"suite input must not be a symlink: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BenchmarkError(f"suite input must not contain symlinks: {path}")


def _prepare_project(project_dir: Path, spec: RunSpec, prompt: str, config: Mapping[str, object]) -> None:
    task = project_dir / "task.txt"
    descriptor = os.open(task, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        target.write(prompt)
    if stat_mode(task) != 0o600:
        raise BenchmarkError("task prompt permissions are not private")
    if spec.condition != "suite":
        return
    suite_path = Path(str(config["suite_path"]))
    if not suite_path.is_dir():
        raise BenchmarkError(f"suite path does not exist: {suite_path}")
    for name in ("skills", ".translation"):
        source = suite_path / name
        if source.exists():
            if not source.is_dir():
                raise BenchmarkError(f"approved suite input must be a directory: {source}")
            _refuse_symlinks(source)
            shutil.copytree(source, project_dir / name)


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _redactor(config: Mapping[str, object]) -> LiteralSecretRedactor:
    return LiteralSecretRedactor(
        [os.environ[name] for name in config.get("secret_env", []) if os.environ.get(name)]
    )


def _store_raw(evidence_dir: Path, output: str) -> tuple[str, str]:
    encoded = output.encode("utf-8")
    digest = sha256_bytes(encoded)
    raw_dir = evidence_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{digest}.txt"
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            if target.read_bytes() != encoded:
                raise BenchmarkError(f"raw output hash collision: {digest}")
        except OSError as error:
            raise BenchmarkError(f"cannot verify raw output {target}: {error}") from error
    else:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
    return digest, target.relative_to(evidence_dir).as_posix()


def _schedule_manifest(schedule: Sequence[RunSpec]) -> dict:
    run_ids = [spec.run_id for spec in schedule]
    if len(run_ids) != len(set(run_ids)):
        raise BenchmarkError("schedule contains duplicate run ids")
    return {"run_ids": run_ids, "sha256": sha256_bytes(canonical_bytes(run_ids))}


def _ensure_run_manifest(
    evidence_dir: Path, schedule: Sequence[RunSpec], config: Mapping[str, object]
) -> None:
    target = evidence_dir / "run-manifest.json"
    schedule_value = _schedule_manifest(schedule)
    config_digest = sha256_bytes(canonical_bytes(dict(config)))
    if target.exists():
        manifest = read_json(target)
        if not isinstance(manifest, dict):
            raise BenchmarkError("run manifest must be an object")
        existing = manifest.get("schedule")
        if existing is not None and existing != schedule_value:
            raise BenchmarkError("run manifest schedule mismatch")
        execution_config = manifest.get("execution_config_sha256")
        if execution_config is not None and execution_config != config_digest:
            raise BenchmarkError("run manifest execution config mismatch")
        manifest["schedule"] = schedule_value
        manifest.setdefault("runner_config_sha256", config_digest)
        manifest["execution_config_sha256"] = config_digest
    else:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "runner_config_sha256": config_digest,
            "execution_config_sha256": config_digest,
            "schedule": schedule_value,
        }
    atomic_write_json(target, manifest)


def _existing_results(evidence_dir: Path, schedule: Sequence[RunSpec]) -> dict[str, RunResult]:
    path = evidence_dir / "runs.jsonl"
    if not path.exists():
        return {}
    expected = {spec.run_id for spec in schedule}
    results: dict[str, RunResult] = {}
    for record in read_jsonl(path):
        run_id = record.get("run_id")
        if run_id not in expected:
            raise BenchmarkError(f"existing evidence has unknown run id: {run_id!r}")
        if run_id in results:
            raise BenchmarkError(f"existing evidence has duplicate run id: {run_id}")
        result = RunResult.from_record(record)
        results[result.run_id] = result
    return results


def _invoke_with_prestart_retries(
    runner: object,
    *,
    prompt: str,
    project_dir: Path,
    timeout_seconds: int,
    retries: int,
) -> Invocation:
    for retry in range(retries + 1):
        try:
            invocation = runner.invoke(
                prompt=prompt, project_dir=project_dir, timeout_seconds=timeout_seconds
            )
        except OSError as error:
            invocation = Invocation(
                (), False, False, utc_now(), utc_now(), None, "", "",
                reason=f"{error.__class__.__name__}: {error}",
            )
        if invocation.process_started or retry == retries:
            return invocation
    raise AssertionError("pre-start retry loop did not return")


def _result_from_invocation(
    spec: RunSpec,
    invocation: Invocation,
    *,
    runner_mode: str,
    evidence_dir: Path,
    project_fingerprint: str,
    redactor: LiteralSecretRedactor,
) -> RunResult:
    stdout, stdout_changed = redactor.text(invocation.stdout)
    stderr, stderr_changed = redactor.text(invocation.stderr)
    telemetry, telemetry_changed = redactor.value(invocation.telemetry)
    argv, argv_changed = redactor.value(invocation.argv)
    digest, raw_path = _store_raw(evidence_dir, stdout)
    failure_class = classify_failure(asdict(invocation))
    return RunResult(
        spec.run_id, spec.case_id, spec.condition, spec.attempt, runner_mode,
        "completed" if failure_class == "success" else failure_class,
        failure_class, invocation.process_started, invocation.started_at,
        invocation.completed_at, invocation.exit_code, invocation.timed_out,
        invocation.refused, invocation.malformed_output, invocation.tool_misuse,
        invocation.reason, digest, raw_path, stderr, telemetry,
        stdout_changed or stderr_changed or telemetry_changed or argv_changed,
        project_fingerprint, tuple(argv), invocation.shell,
    )


def execute_schedule(
    schedule: Sequence[RunSpec],
    runner: object,
    evidence_dir: Path,
    *,
    cases: Sequence[dict],
    config: Mapping[str, object],
    templates: Mapping[str, str],
) -> list[RunResult]:
    validated = validate_runner_config(config)
    evidence_dir = Path(evidence_dir)
    if evidence_dir.is_symlink():
        raise BenchmarkError(f"refusing symlink evidence directory: {evidence_dir}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    scratch_root = Path(str(validated["scratch_root"]))
    if scratch_root.is_symlink():
        raise BenchmarkError(f"refusing symlink scratch root: {scratch_root}")
    scratch_root.mkdir(parents=True, exist_ok=True)
    _ensure_run_manifest(evidence_dir, schedule, validated)
    completed = _existing_results(evidence_dir, schedule)
    case_map = _cases_by_id(cases)
    redactor = _redactor(validated)
    for spec in schedule:
        if spec.run_id in completed:
            continue
        prompt = _render(spec, case_map, templates)
        with tempfile.TemporaryDirectory(
            prefix=f"benchmark-{spec.run_id}-", dir=scratch_root
        ) as temporary:
            project_dir = Path(temporary)
            project_fingerprint = sha256_text(str(project_dir))
            _prepare_project(project_dir, spec, prompt, validated)
            invocation = _invoke_with_prestart_retries(
                runner,
                prompt=prompt,
                project_dir=project_dir,
                timeout_seconds=validated["timeout_seconds"],
                retries=validated.get("prestart_retries", 2),
            )
            result = _result_from_invocation(
                spec,
                invocation,
                runner_mode=str(validated["mode"]),
                evidence_dir=evidence_dir,
                project_fingerprint=project_fingerprint,
                redactor=redactor,
            )
            append_jsonl_fsync(evidence_dir / "runs.jsonl", result.to_record())
            completed[spec.run_id] = result
    return [completed[spec.run_id] for spec in schedule]


def _public_settings(config: Mapping[str, object]) -> dict:
    return {
        "agent": config["agent"],
        "host_version": config["host_version"],
        "model": config["model"],
        "settings": dict(config["settings"]),
        "timeout_seconds": config["timeout_seconds"],
    }


def export_manual_packages(
    schedule: Sequence[RunSpec],
    output_dir: Path,
    *,
    cases: Sequence[dict],
    config: Mapping[str, object],
    templates: Mapping[str, str],
) -> list[Path]:
    validated = validate_runner_config(config)
    output_dir = Path(output_dir)
    if output_dir.is_symlink():
        raise BenchmarkError(f"refusing symlink manual package directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise BenchmarkError("manual package directory must be empty")
    case_map = _cases_by_id(cases)
    _schedule_manifest(schedule)
    paths = []
    for spec in schedule:
        package = {
            "run_id": spec.run_id,
            "prompt": _render(spec, case_map, templates),
            "settings": _public_settings(validated),
        }
        path = output_dir / f"{spec.run_id}.json"
        atomic_write_json(path, package)
        paths.append(path)
    return paths


def _load_manual_responses(
    schedule: Sequence[RunSpec], responses_dir: Path
) -> dict[str, str]:
    expected = {spec.run_id for spec in schedule}
    if len(expected) != len(schedule):
        raise BenchmarkError("schedule contains duplicate run ids")
    if not responses_dir.is_dir():
        raise BenchmarkError(f"manual response directory does not exist: {responses_dir}")
    responses: dict[str, str] = {}
    for path in sorted(responses_dir.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise BenchmarkError(f"malformed manual response entry: {path.name}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise BenchmarkError(f"manual response is not UTF-8: {path.name}") from error
        except OSError as error:
            raise BenchmarkError(f"cannot read manual response {path.name}: {error}") from error
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise BenchmarkError(f"malformed manual response {path.name}: {error}") from error
        if not isinstance(value, dict) or set(value) != {"run_id", "response"}:
            raise BenchmarkError(f"malformed manual response {path.name}")
        run_id = value["run_id"]
        response = value["response"]
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise BenchmarkError(f"malformed manual response id in {path.name}")
        if run_id not in expected:
            raise BenchmarkError(f"unknown manual response id: {run_id}")
        if run_id in responses:
            raise BenchmarkError(f"duplicate manual response id: {run_id}")
        if not isinstance(response, str):
            raise BenchmarkError(f"malformed manual response text for {run_id}")
        responses[run_id] = response
    missing = expected - set(responses)
    if missing:
        raise BenchmarkError(f"missing manual response ids: {sorted(missing)!r}")
    return responses


def import_manual_responses(
    schedule: Sequence[RunSpec],
    responses_dir: Path,
    evidence_dir: Path,
    *,
    config: Mapping[str, object],
) -> list[RunResult]:
    validated = validate_runner_config(config)
    responses = _load_manual_responses(schedule, Path(responses_dir))
    evidence_dir = Path(evidence_dir)
    if evidence_dir.is_symlink():
        raise BenchmarkError(f"refusing symlink evidence directory: {evidence_dir}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    _ensure_run_manifest(evidence_dir, schedule, validated)
    if (evidence_dir / "runs.jsonl").exists():
        raise BenchmarkError("manual import evidence already contains run records")
    redactor = _redactor(validated)
    results = []
    for spec in schedule:
        now = utc_now()
        invocation = Invocation(
            (), False, False, now, now, 0, responses[spec.run_id], "",
            reason="manual_import", telemetry={},
        )
        result = _result_from_invocation(
            spec,
            invocation,
            runner_mode="manual",
            evidence_dir=evidence_dir,
            project_fingerprint="manual",
            redactor=redactor,
        )
        result = RunResult(
            **{
                **asdict(result),
                "status": "completed",
                "failure_class": "success",
            }
        )
        append_jsonl_fsync(evidence_dir / "runs.jsonl", result.to_record())
        results.append(result)
    return results


def _load_templates(dataset_dir: Path) -> dict[str, str]:
    prompt_dir = dataset_dir / "prompts"
    names = ("normal-translation", "normal-review", "treatment", "context-only")
    try:
        return {
            name: (prompt_dir / f"{name}.txt").read_text(encoding="utf-8")
            for name in names
        }
    except (OSError, UnicodeDecodeError) as error:
        raise BenchmarkError(f"cannot load prompt templates: {error}") from error


def _load_cli_inputs(dataset_dir: Path, config_path: Path, seed: int):
    verify_dataset_manifest(dataset_dir)
    cases = read_jsonl(dataset_dir / "cases.jsonl")
    config = validate_runner_config(read_json(config_path))
    schedule = build_schedule(cases, config, seed)
    return cases, config, schedule, _load_templates(dataset_dir)


def _cli_runner(config: Mapping[str, object]) -> object:
    if config["mode"] == "fake":
        return FakeRunner(str(config.get("fake_output", "deterministic fake response")))
    if config["mode"] != "cli":
        raise BenchmarkError("execute requires fake or cli runner mode")
    allowed = set(_SAFE_ENVIRONMENT_KEYS) | set(config.get("env_allowlist", [])) | set(
        config.get("secret_env", [])
    )
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    return CliRunner(config["command"], environment=environment)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run isolated PT-PT benchmark schedules.")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("execute", "export-manual", "import-manual"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", required=True, type=Path)
        command.add_argument("--config", required=True, type=Path)
        command.add_argument("--seed", required=True, type=int)
        if name == "execute":
            command.add_argument("--evidence", required=True, type=Path)
        elif name == "export-manual":
            command.add_argument("--output", required=True, type=Path)
        else:
            command.add_argument("--responses", required=True, type=Path)
            command.add_argument("--evidence", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        cases, config, schedule, templates = _load_cli_inputs(
            arguments.dataset, arguments.config, arguments.seed
        )
        if arguments.action == "execute":
            results = execute_schedule(
                schedule, _cli_runner(config), arguments.evidence,
                cases=cases, config=config, templates=templates,
            )
            output = {"runs": len(results), "evidence": str(arguments.evidence)}
        elif arguments.action == "export-manual":
            paths = export_manual_packages(
                schedule, arguments.output,
                cases=cases, config=config, templates=templates,
            )
            output = {"packages": len(paths), "output": str(arguments.output)}
        else:
            results = import_manual_responses(
                schedule, arguments.responses, arguments.evidence, config=config
            )
            output = {"runs": len(results), "evidence": str(arguments.evidence)}
        sys.stdout.buffer.write(canonical_bytes(output))
        return 0
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
