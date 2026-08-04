from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
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
from .schema import (
    DIFFICULTIES,
    EXPECTED_BY_DIFFICULTY,
    EXPECTED_BY_SURFACE,
    EXPECTED_BY_TASK,
    EXPECTED_DIAGNOSTIC,
    EXPECTED_TOTAL,
    PRIMARY_ATTEMPTS,
    SCHEMA_VERSION,
    SURFACES,
    TASKS,
)


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MODES = {"fake", "cli", "manual"}
_PRIMARY_CONDITIONS = ("normal", "suite")
_SAFE_ENVIRONMENT_KEYS = ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CONTEXT_FILES = (
    "project-brief.md",
    "locales.yaml",
    "glossary.csv",
    "style-guide.md",
    "protected-terms.txt",
)
_APPROVAL_FIELDS = {
    "status", "approved_by", "approved_at", "context_sha256", "approved_empty",
}
_PROBE_FIELDS = {
    "schema_version", "probe_version", "nonce", "outside_read", "suite_read",
    "context_read", "home", "network_attempt", "tool_attempt", "research_attempt",
}
_ENVELOPE_FIELDS = {
    "schema_version", "process_started", "exit_code", "started_at", "completed_at",
    "stdout", "stderr", "timed_out", "refused", "malformed_output", "tool_misuse",
    "reason", "usage", "telemetry", "applied_policy_sha256",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROBE_VERSION = 1
_PROBE_PROGRAM = r'''from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess
from pathlib import Path

def read_observation(path: str) -> str:
    try:
        Path(path).read_bytes()
        return "readable"
    except PermissionError:
        return "denied"
    except Exception as error:
        return f"not-denied:{type(error).__name__}"

def network_observation() -> str:
    try:
        connection = socket.socket()
        try:
            connection.settimeout(0.1)
            connection.connect(("203.0.113.1", 9))
        finally:
            connection.close()
        return "allowed"
    except PermissionError:
        return "denied"
    except Exception as error:
        return f"not-denied:{type(error).__name__}"

def tool_observation() -> str:
    try:
        subprocess.run(["true"], check=False, capture_output=True)
        return "allowed"
    except PermissionError:
        return "denied"
    except Exception as error:
        return f"not-denied:{type(error).__name__}"

parser = argparse.ArgumentParser()
parser.add_argument("--nonce", required=True)
parser.add_argument("--outside", required=True)
parser.add_argument("--suite", required=True)
parser.add_argument("--context", required=True)
arguments = parser.parse_args()
result = {
    "schema_version": 1,
    "probe_version": 1,
    "nonce": arguments.nonce,
    "outside_read": read_observation(arguments.outside),
    "suite_read": read_observation(arguments.suite),
    "context_read": read_observation(arguments.context),
    "home": str(Path(os.environ.get("HOME", "")).resolve()),
    "network_attempt": network_observation(),
    "tool_attempt": tool_observation(),
    "research_attempt": network_observation(),
}
print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
'''
_PROBE_PROGRAM_BYTES = _PROBE_PROGRAM.encode("utf-8")
_PROBE_PROGRAM_SHA256 = sha256_bytes(_PROBE_PROGRAM_BYTES)


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
    usage: object = None
    expected_policy_sha256: str | None = None
    applied_policy_sha256: str | None = None
    policy_integrity: str = "not_required"

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
    usage: object
    expected_policy_sha256: str | None
    applied_policy_sha256: str | None
    policy_integrity: str
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
        if values.pop("schema_version", None) != SCHEMA_VERSION:
            raise BenchmarkError("existing run record schema version mismatch")
        values.setdefault("usage", {})
        policy_fields = {
            "expected_policy_sha256", "applied_policy_sha256", "policy_integrity",
        }
        if values.get("runner_mode") == "cli" and not policy_fields <= values.keys():
            raise BenchmarkError("existing CLI run policy integrity binding is missing")
        if values.get("runner_mode") != "cli":
            values.setdefault("expected_policy_sha256", None)
            values.setdefault("applied_policy_sha256", None)
            values.setdefault("policy_integrity", "not_required")
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
                redacted_key, key_changed = self.value(key)
                redacted, item_changed = self.value(item)
                result[redacted_key] = redacted
                changed = changed or key_changed or item_changed
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
    """Invoke an agent only through one trusted host-provided sandbox adapter."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        sandbox_adapter: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ):
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            raise BenchmarkError("runner command must be an array of strings")
        if not command or not all(isinstance(value, str) and value for value in command):
            raise BenchmarkError("CLI runner command must contain non-empty strings")
        if (
            isinstance(sandbox_adapter, (str, bytes))
            or not isinstance(sandbox_adapter, Sequence)
            or not sandbox_adapter
            or not all(isinstance(value, str) and value for value in sandbox_adapter)
        ):
            raise BenchmarkError("sandbox adapter must be a non-empty argv array")
        self.command = tuple(command)
        self.sandbox_adapter = tuple(sandbox_adapter)
        if environment is None:
            environment = {
                key: os.environ[key]
                for key in _SAFE_ENVIRONMENT_KEYS
                if key in os.environ
            }
        self.safe_environment = dict(environment)

    def invoke(
        self,
        *,
        prompt: str,
        project_dir: Path,
        home_dir: Path,
        policy_path: Path,
        timeout_seconds: int,
    ) -> Invocation:
        agent = tuple(_replace_trusted_token(arg, project_dir) for arg in self.command)
        return self.invoke_command(
            agent,
            prompt=prompt,
            project_dir=project_dir,
            home_dir=home_dir,
            policy_path=policy_path,
            timeout_seconds=timeout_seconds,
        )

    def invoke_command(
        self,
        command: Sequence[str],
        *,
        prompt: str,
        project_dir: Path,
        home_dir: Path,
        policy_path: Path,
        timeout_seconds: int,
    ) -> Invocation:
        agent = tuple(command)
        expected_policy_hash = sha256_bytes(policy_path.read_bytes())
        argv = self.sandbox_adapter + (
            "run", "--project", str(project_dir), "--home", str(home_dir),
            "--policy", str(policy_path), "--",
        ) + agent
        started_at = utc_now()
        completed = self._run_adapter(
            argv, input_text=prompt, project_dir=project_dir, home_dir=home_dir,
            timeout_seconds=timeout_seconds,
        )
        if isinstance(completed, Invocation):
            return replace(
                completed,
                expected_policy_sha256=expected_policy_hash,
                policy_integrity="failed",
            )
        envelope = None
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError:
            pass
        reported_policy_hash = (
            envelope.get("applied_policy_sha256")
            if isinstance(envelope, Mapping)
            and isinstance(envelope.get("applied_policy_sha256"), str)
            else None
        )
        policy_integrity = (
            "verified" if reported_policy_hash == expected_policy_hash else "failed"
        )
        if completed.returncode != 0:
            return Invocation(
                argv, False, True, started_at, utc_now(), completed.returncode,
                "", completed.stderr, malformed_output=True,
                reason="sandbox_adapter_failed", telemetry={"adapter_stdout": completed.stdout},
                expected_policy_sha256=expected_policy_hash,
                applied_policy_sha256=reported_policy_hash,
                policy_integrity=policy_integrity,
            )
        try:
            if envelope is None:
                raise BenchmarkError("adapter invocation envelope is not valid JSON")
            return _invocation_from_envelope(
                envelope, argv, expected_policy_sha256=expected_policy_hash
            )
        except BenchmarkError as error:
            return Invocation(
                argv, False, True, started_at, utc_now(), 0, "", completed.stderr,
                malformed_output=True, reason=f"invalid_adapter_envelope: {error}",
                telemetry={"adapter_stdout": completed.stdout},
                expected_policy_sha256=expected_policy_hash,
                applied_policy_sha256=reported_policy_hash,
                policy_integrity=policy_integrity,
            )

    def _run_adapter(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str,
        project_dir: Path,
        home_dir: Path,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str] | Invocation:
        environment = dict(self.safe_environment)
        environment["HOME"] = str(home_dir)
        try:
            return subprocess.run(
                argv, input=input_text, text=True, capture_output=True, cwd=project_dir,
                timeout=timeout_seconds, check=False, shell=False, env=environment,
            )
        except subprocess.TimeoutExpired as error:
            return Invocation(
                argv, False, True, utc_now(), utc_now(), None,
                _timeout_text(error.stdout), _timeout_text(error.stderr),
                timed_out=True, reason="sandbox_adapter_timeout",
            )
        except OSError as error:
            return Invocation(
                argv, False, False, utc_now(), utc_now(), None, "", "",
                reason=error.__class__.__name__ + (f": {error}" if str(error) else ""),
            )


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _replace_trusted_token(argument: str, project_dir: Path) -> str:
    return argument.replace("{project_dir}", str(project_dir))


def _validate_probe(value: object, *, home_dir: Path, nonce: str) -> dict:
    if not isinstance(value, dict) or set(value) != _PROBE_FIELDS:
        raise BenchmarkError("sandbox probe has malformed fields")
    if value["schema_version"] != SCHEMA_VERSION or value["probe_version"] != _PROBE_VERSION:
        raise BenchmarkError("sandbox probe version mismatch")
    if value["nonce"] != nonce:
        raise BenchmarkError("sandbox probe challenge nonce mismatch")
    for field in (
        "outside_read", "suite_read", "context_read", "network_attempt",
        "tool_attempt", "research_attempt",
    ):
        if value[field] != "denied":
            raise BenchmarkError(f"sandbox probe observation did not prove denial: {field}")
    if value["home"] != str(home_dir.resolve()):
        raise BenchmarkError("sandbox probe did not use the isolated HOME")
    return value


def _invocation_from_envelope(
    value: object,
    argv: tuple[str, ...],
    *,
    expected_policy_sha256: str,
) -> Invocation:
    if not isinstance(value, dict) or set(value) != _ENVELOPE_FIELDS:
        raise BenchmarkError("adapter invocation envelope has malformed fields")
    if value["schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError("adapter invocation envelope schema version mismatch")
    for field in ("process_started", "timed_out", "refused", "malformed_output", "tool_misuse"):
        if type(value[field]) is not bool:
            raise BenchmarkError(f"adapter invocation envelope {field} must be boolean")
    if value["exit_code"] is not None and type(value["exit_code"]) is not int:
        raise BenchmarkError("adapter invocation envelope exit_code must be integer or null")
    for field in ("started_at", "completed_at", "stdout", "stderr"):
        if not isinstance(value[field], str) or (
            field in ("started_at", "completed_at") and not value[field]
        ):
            raise BenchmarkError(f"adapter invocation envelope {field} must be text")
    if value["reason"] is not None and not isinstance(value["reason"], str):
        raise BenchmarkError("adapter invocation envelope reason must be text or null")
    if not isinstance(value["usage"], Mapping) or not isinstance(value["telemetry"], Mapping):
        raise BenchmarkError("adapter invocation envelope usage and telemetry must be objects")
    if value["applied_policy_sha256"] != expected_policy_sha256:
        raise BenchmarkError("adapter invocation envelope applied policy hash mismatch")
    if value["process_started"] and value["exit_code"] is None and not value["timed_out"]:
        raise BenchmarkError("started adapter invocation requires exit_code unless timed out")
    return Invocation(
        argv=argv,
        shell=False,
        process_started=value["process_started"],
        started_at=value["started_at"],
        completed_at=value["completed_at"],
        exit_code=value["exit_code"],
        stdout=value["stdout"],
        stderr=value["stderr"],
        timed_out=value["timed_out"],
        refused=value["refused"],
        malformed_output=value["malformed_output"],
        tool_misuse=value["tool_misuse"],
        reason=value["reason"],
        telemetry=value["telemetry"],
        usage=value["usage"],
        expected_policy_sha256=expected_policy_sha256,
        applied_policy_sha256=value["applied_policy_sha256"],
        policy_integrity="verified",
    )


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
    adapter = config.get("sandbox_adapter")
    if mode == "cli" and adapter is None:
        raise BenchmarkError(
            "CLI mode requires a host sandbox_adapter; configure one or use manual mode"
        )
    if adapter is not None and (
        isinstance(adapter, (str, bytes))
        or not isinstance(adapter, list)
        or not adapter
        or not all(isinstance(value, str) and value for value in adapter)
    ):
        raise BenchmarkError("sandbox_adapter must be a non-empty argv array")
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
    _validate_schedule_cohort(cases)
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
    distribution = Counter(spec.condition for spec in specs)
    if distribution != {"normal": 180, "suite": 180, "context_only": 45}:
        raise BenchmarkError(f"invalid schedule condition distribution: {dict(distribution)!r}")
    if len(specs) != 405 or len({spec.run_id for spec in specs}) != 405:
        raise BenchmarkError("schedule must contain exactly 405 unique runs")
    return specs


def _validate_schedule_cohort(cases: object) -> None:
    if not isinstance(cases, list) or len(cases) != EXPECTED_TOTAL:
        raise BenchmarkError(f"schedule cohort must contain exactly {EXPECTED_TOTAL} cases")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise BenchmarkError("schedule cohort cases must be mappings")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise BenchmarkError("schedule cohort case ids must be unique non-empty text")
        ids.add(case_id)
        if case.get("task") not in TASKS:
            raise BenchmarkError(f"invalid schedule task for {case_id}")
        if case.get("surface") not in SURFACES:
            raise BenchmarkError(f"invalid schedule surface for {case_id}")
        if case.get("difficulty") not in DIFFICULTIES:
            raise BenchmarkError(f"invalid schedule difficulty for {case_id}")
        if type(case.get("diagnostic")) is not bool:
            raise BenchmarkError(f"schedule diagnostic must be boolean for {case_id}")
    if Counter(case["task"] for case in cases) != EXPECTED_BY_TASK:
        raise BenchmarkError("invalid schedule task distribution")
    if Counter(case["surface"] for case in cases) != EXPECTED_BY_SURFACE:
        raise BenchmarkError("invalid schedule surface distribution")
    if Counter(case["difficulty"] for case in cases) != EXPECTED_BY_DIFFICULTY:
        raise BenchmarkError("invalid schedule difficulty distribution")
    if sum(case["diagnostic"] for case in cases) != EXPECTED_DIAGNOSTIC:
        raise BenchmarkError("invalid schedule diagnostic distribution")


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


def _prepare_project(
    project_dir: Path,
    spec: RunSpec,
    prompt: str,
    snapshot_path: Path | None,
) -> None:
    task = project_dir / "task.txt"
    descriptor = os.open(task, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        target.write(prompt)
    if stat_mode(task) != 0o600:
        raise BenchmarkError("task prompt permissions are not private")
    if spec.condition != "suite":
        return
    if snapshot_path is None:
        raise BenchmarkError("suite run requires a frozen input snapshot")
    for name in ("skills", ".translation"):
        source = snapshot_path / name
        if source.exists():
            if not source.is_dir():
                raise BenchmarkError(f"approved suite input must be a directory: {source}")
            _refuse_symlinks(source)
            shutil.copytree(source, project_dir / name)


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _tree_hash(root: Path) -> str:
    _refuse_symlinks(root)
    entries = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        if path.is_file():
            entries.append([
                path.relative_to(root).as_posix(),
                sha256_bytes(path.read_bytes()),
            ])
    if not entries:
        raise BenchmarkError(f"snapshot tree must contain files: {root}")
    return sha256_bytes(canonical_bytes(entries))


def _validate_signed_context(translation: Path) -> tuple[str, ...]:
    _refuse_symlinks(translation)
    missing = [name for name in (*_CONTEXT_FILES, "setup-approval.json") if not (translation / name).is_file()]
    if missing:
        raise BenchmarkError(f"signed project context missing files: {missing!r}")
    approval = read_json(translation / "setup-approval.json")
    if not isinstance(approval, dict) or set(approval) != _APPROVAL_FIELDS:
        raise BenchmarkError("project context approval is malformed")
    if approval["status"] != "approved":
        raise BenchmarkError("project context is not approved")
    if not isinstance(approval["approved_by"], str) or not approval["approved_by"].strip():
        raise BenchmarkError("project context approval requires approved_by")
    if not isinstance(approval["approved_at"], str) or not approval["approved_at"].strip():
        raise BenchmarkError("project context approval requires approved_at")
    hashes = approval["context_sha256"]
    if (
        not isinstance(hashes, dict)
        or set(hashes) != set(_CONTEXT_FILES)
        or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in hashes.values())
    ):
        raise BenchmarkError("project context approval hashes are malformed")
    approved_empty = approval["approved_empty"]
    if (
        not isinstance(approved_empty, list)
        or not all(isinstance(name, str) for name in approved_empty)
        or len(approved_empty) != len(set(approved_empty))
        or any(name not in ("glossary.csv", "protected-terms.txt") for name in approved_empty)
    ):
        raise BenchmarkError("project context approved_empty is malformed")
    for name in _CONTEXT_FILES:
        actual = sha256_bytes((translation / name).read_bytes())
        if hashes[name] != actual:
            raise BenchmarkError(f"project context approval hash mismatch: {name}")
    return tuple(hashes[name] for name in _CONTEXT_FILES)


def _snapshot_manifest(snapshot_path: Path) -> dict:
    if not snapshot_path.is_dir() or snapshot_path.is_symlink():
        raise BenchmarkError("frozen input snapshot is unsafe")
    _validate_signed_context(snapshot_path / ".translation")
    trees = {
        "skills": _tree_hash(snapshot_path / "skills"),
        ".translation": _tree_hash(snapshot_path / ".translation"),
    }
    return {
        "trees": trees,
        "sha256": sha256_bytes(canonical_bytes(trees)),
    }


def _set_snapshot_permissions(snapshot_path: Path, *, readonly: bool) -> None:
    directory_mode, file_mode = ((0o555, 0o444) if readonly else (0o700, 0o600))
    for path in sorted(snapshot_path.rglob("*"), reverse=True):
        path.chmod(directory_mode if path.is_dir() else file_mode)
    snapshot_path.chmod(directory_mode)


def _sandbox_probe_manifest(
    adapter_command: Sequence[str],
    policy_sha256: Sequence[str],
    snapshot_sha256: str,
) -> dict:
    binding = {
        "schema_version": SCHEMA_VERSION,
        "probe_version": _PROBE_VERSION,
        "adapter_command_sha256": sha256_bytes(canonical_bytes(list(adapter_command))),
        "probe_program_sha256": _PROBE_PROGRAM_SHA256,
        "policy_sha256": sorted(set(policy_sha256)),
        "snapshot_sha256": snapshot_sha256,
    }
    return {**binding, "sha256": sha256_bytes(canonical_bytes(binding))}


def _freeze_input_snapshot(
    evidence_dir: Path,
    config: Mapping[str, object],
    *,
    required: bool,
    required_integrity_digests: Sequence[str],
    probe_policy_sha256: Sequence[str] = (),
) -> tuple[Path | None, dict | None]:
    if not required:
        _reject_secret_integrity_collisions(config, required_integrity_digests)
        return None, None
    target = evidence_dir / "input-snapshot"
    target_preexists = target.exists()
    if target_preexists:
        _snapshot_manifest(target)
        skills = target / "skills"
        translation = target / ".translation"
    else:
        suite_path = Path(str(config["suite_path"])).resolve()
        skills = suite_path / "skills"
        translation = suite_path / ".translation"
        if not skills.is_dir():
            raise BenchmarkError("suite runs require a skills directory")
        if not translation.is_dir():
            raise BenchmarkError("suite runs require signed .translation project context")
        _refuse_symlinks(skills)
        _validate_signed_context(translation)
    evidence_parent = evidence_dir.parent
    evidence_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{evidence_dir.name}-snapshot-", dir=evidence_parent
    ) as temporary:
        staged_evidence = Path(temporary) / "evidence"
        staged_evidence.mkdir(mode=0o700)
        staged = staged_evidence / "input-snapshot"
        staged.mkdir(mode=0o700)
        shutil.copytree(skills, staged / "skills")
        staged_translation = staged / ".translation"
        staged_translation.mkdir(mode=0o700)
        for name in (*_CONTEXT_FILES, "setup-approval.json"):
            shutil.copy2(translation / name, staged_translation / name)
        _set_snapshot_permissions(staged, readonly=False)
        snapshot_manifest = _snapshot_manifest(staged)
        context_sha256 = _validate_signed_context(staged_translation)
        integrity_digests = [
            *required_integrity_digests,
            *context_sha256,
            snapshot_manifest["sha256"],
            *snapshot_manifest["trees"].values(),
        ]
        if config["mode"] == "cli":
            probe_manifest = _sandbox_probe_manifest(
                config["sandbox_adapter"],
                probe_policy_sha256,
                snapshot_manifest["sha256"],
            )
            integrity_digests.append(probe_manifest["sha256"])
        _reject_secret_integrity_collisions(config, integrity_digests)

        if target_preexists:
            if _snapshot_manifest(target) != snapshot_manifest:
                raise BenchmarkError("frozen input snapshot changed during private staging")
            return target, snapshot_manifest
        if target.exists():
            raise BenchmarkError("frozen input snapshot appeared during private staging")
        _set_snapshot_permissions(staged, readonly=True)
        if evidence_dir.exists():
            if not evidence_dir.is_dir():
                raise BenchmarkError(f"evidence path is not a directory: {evidence_dir}")
            os.replace(staged, target)
        else:
            os.replace(staged_evidence, evidence_dir)
        if _snapshot_manifest(target) != snapshot_manifest:
            raise BenchmarkError("published input snapshot does not match private staging")
        return target, snapshot_manifest


def _configured_literal_secrets(config: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        os.environ[name] for name in config.get("secret_env", []) if os.environ.get(name)
    )


def _redactor(config: Mapping[str, object]) -> LiteralSecretRedactor:
    return LiteralSecretRedactor(_configured_literal_secrets(config))


def _reject_secret_integrity_collisions(
    config: Mapping[str, object], digests: Sequence[str]
) -> None:
    if set(_configured_literal_secrets(config)).intersection(digests):
        raise BenchmarkError(
            "configured literal secret conflicts with required immutable integrity digest"
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
    evidence_dir: Path,
    schedule: Sequence[RunSpec],
    config: Mapping[str, object],
    *,
    input_snapshot: dict | None,
    sandbox_probe: dict | None,
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
        if manifest.get("input_snapshot") not in (None, input_snapshot):
            raise BenchmarkError("run manifest input snapshot mismatch")
        if manifest.get("sandbox_probe") not in (None, sandbox_probe):
            raise BenchmarkError("run manifest sandbox probe mismatch")
        manifest.setdefault("runner_config_sha256", config_digest)
        manifest["execution_config_sha256"] = config_digest
    else:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "runner_config_sha256": config_digest,
            "execution_config_sha256": config_digest,
            "schedule": schedule_value,
        }
    if input_snapshot is not None:
        manifest["input_snapshot"] = input_snapshot
    if sandbox_probe is not None:
        manifest["sandbox_probe"] = sandbox_probe
    atomic_write_json(target, manifest)


def _existing_results(
    evidence_dir: Path,
    schedule: Sequence[RunSpec],
    *,
    expected_policy_sha256_by_run: Mapping[str, str] | None = None,
) -> dict[str, RunResult]:
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
        if result.runner_mode == "cli":
            expected_policy_sha256 = (
                expected_policy_sha256_by_run.get(result.run_id)
                if expected_policy_sha256_by_run is not None
                else None
            )
            if (
                expected_policy_sha256 is None
                or result.expected_policy_sha256 != expected_policy_sha256
                or result.applied_policy_sha256 != expected_policy_sha256
                or result.policy_integrity != "verified"
            ):
                raise BenchmarkError(
                    f"existing CLI run policy integrity is not verified: {result.run_id}"
                )
        results[result.run_id] = result
    return results


def _invoke_with_prestart_retries(
    runner: object,
    *,
    prompt: str,
    project_dir: Path,
    home_dir: Path | None,
    policy_path: Path | None,
    timeout_seconds: int,
    retries: int,
) -> Invocation:
    for retry in range(retries + 1):
        try:
            if isinstance(runner, CliRunner):
                if home_dir is None or policy_path is None:
                    raise BenchmarkError("CLI invocation requires isolated HOME and policy file")
                invocation = runner.invoke(
                    prompt=prompt, project_dir=project_dir, home_dir=home_dir,
                    policy_path=policy_path, timeout_seconds=timeout_seconds,
                )
            else:
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


def _research_question(case: Mapping[str, object]) -> str | None:
    direct = case.get("unresolved_question")
    if direct is not None:
        if not isinstance(direct, str) or not direct.strip():
            raise BenchmarkError("case unresolved_question must be exact nonblank text")
        return direct
    context = case.get("context")
    if isinstance(context, Mapping) and "unresolved_question" in context:
        value = context["unresolved_question"]
        if not isinstance(value, str) or not value.strip():
            raise BenchmarkError("case context unresolved_question must be exact nonblank text")
        return value
    return None


def _effective_settings(config: Mapping[str, object], condition: str) -> dict:
    settings = dict(config["settings"])
    condition_settings = config.get("condition_settings")
    if isinstance(condition_settings, Mapping):
        override = condition_settings.get(condition)
        if isinstance(override, Mapping):
            settings.update(override)
    return settings


def _policy_for_case(
    config: Mapping[str, object], case: Mapping[str, object], condition: str
) -> dict:
    settings = _effective_settings(config, condition)
    return {
        "schema_version": SCHEMA_VERSION,
        "tools": settings.get("tools", []),
        "network": settings.get("network", "disabled"),
        "research": {
            "mode": settings["research"],
            "unresolved_question": _research_question(case),
        },
    }


def _write_private_policy(path: Path, policy: Mapping[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(canonical_bytes(policy))


def _preflight_cli(
    runner: object,
    schedule: Sequence[RunSpec],
    cases: Mapping[str, dict],
    config: Mapping[str, object],
    evidence_dir: Path,
    scratch_root: Path,
    snapshot_path: Path | None,
) -> dict | None:
    if config["mode"] != "cli":
        return None
    if not isinstance(runner, CliRunner):
        raise BenchmarkError("CLI mode requires CliRunner with the configured sandbox adapter")
    if snapshot_path is None:
        raise BenchmarkError("CLI preflight requires a frozen suite/context snapshot")
    policies: dict[str, dict] = {}
    for spec in schedule:
        policy = _policy_for_case(config, cases[spec.case_id], spec.condition)
        digest = sha256_bytes(canonical_bytes(policy))
        policies[digest] = policy
    probes_dir = evidence_dir / "probes"
    probes_dir.mkdir(parents=True, exist_ok=True)
    records = []
    suite_root = Path(str(config["suite_path"])).resolve()
    suite_source = _first_regular_file(suite_root / "skills")
    context_source = _first_regular_file(suite_root / ".translation")
    for policy_digest, policy in sorted(policies.items()):
        with tempfile.TemporaryDirectory(prefix="benchmark-probe-", dir=scratch_root) as temporary:
            attempt = Path(temporary)
            project = attempt / "project"
            home = attempt / "home"
            project.mkdir()
            home.mkdir()
            policy_path = attempt / "policy.json"
            _write_private_policy(policy_path, policy)
            sentinel = attempt / "outside-sentinel.txt"
            sentinel.write_text("sandbox must deny this\n", encoding="utf-8")
            probe_program = project / "harness-probe-v1.py"
            descriptor = os.open(
                probe_program, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o500
            )
            with os.fdopen(descriptor, "wb") as target:
                target.write(_PROBE_PROGRAM_BYTES)
            nonce = secrets.token_hex(32)
            probe_command = (
                sys.executable, str(probe_program), "--nonce", nonce,
                "--outside", str(sentinel), "--suite", str(suite_source),
                "--context", str(context_source),
            )
            invocation = runner.invoke_command(
                probe_command,
                prompt="",
                project_dir=project,
                home_dir=home,
                policy_path=policy_path,
                timeout_seconds=config["timeout_seconds"],
            )
            if classify_failure(asdict(invocation)) != "success":
                raise BenchmarkError(
                    f"sandbox probe invocation failed: {invocation.reason or invocation.stderr}"
                )
            try:
                observation = json.loads(invocation.stdout)
            except json.JSONDecodeError as error:
                raise BenchmarkError(f"sandbox probe returned malformed observations: {error}") from error
            observation = _validate_probe(observation, home_dir=home, nonce=nonce)
            record = {
                "schema_version": SCHEMA_VERSION,
                "probe_version": _PROBE_VERSION,
                "nonce": nonce,
                "observations": observation,
                "bindings": {
                    "adapter_command_sha256": sha256_bytes(canonical_bytes(list(runner.sandbox_adapter))),
                    "probe_program_sha256": _PROBE_PROGRAM_SHA256,
                    "policy_sha256": policy_digest,
                    "snapshot_sha256": _snapshot_hash(snapshot_path),
                },
                "invocation": {
                    "exit_code": invocation.exit_code,
                    "applied_policy_sha256": invocation.applied_policy_sha256,
                },
            }
            record, _ = _redactor(config).value(record)
            if not isinstance(record, dict):
                raise AssertionError("redacted probe record must remain a mapping")
            record_path = probes_dir / f"{policy_digest}-{nonce}.json"
            atomic_write_json(record_path, record)
            record_hash = sha256_bytes(record_path.read_bytes())
            index_entry = {
                "policy_sha256": policy_digest,
                "path": record_path.relative_to(evidence_dir).as_posix(),
                "record_sha256": record_hash,
            }
            append_jsonl_fsync(evidence_dir / "probe-runs.jsonl", index_entry)
            records.append(index_entry)
    return _sandbox_probe_manifest(
        runner.sandbox_adapter,
        list(policies),
        _snapshot_hash(snapshot_path),
    )


def _first_regular_file(root: Path) -> Path:
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        if path.is_file() and not path.is_symlink():
            return path.resolve()
    raise BenchmarkError(f"sandbox probe source tree has no regular file: {root}")


def _snapshot_hash(snapshot_path: Path) -> str:
    return _snapshot_manifest(snapshot_path)["sha256"]


def _require_snapshot_bound_resume(evidence_dir: Path, *, required: bool) -> None:
    if not required:
        return
    artifacts = any(
        path.exists()
        for path in (
            evidence_dir / "runs.jsonl",
            evidence_dir / "raw",
            evidence_dir / "probes",
            evidence_dir / "probe-runs.jsonl",
            evidence_dir / "input-snapshot",
        )
    )
    if not artifacts:
        return
    manifest_path = evidence_dir / "run-manifest.json"
    if not manifest_path.is_file():
        raise BenchmarkError("existing evidence has no input snapshot manifest binding")
    manifest = read_json(manifest_path)
    binding = manifest.get("input_snapshot") if isinstance(manifest, dict) else None
    if (
        not isinstance(binding, dict)
        or set(binding) != {"trees", "sha256"}
        or not isinstance(binding.get("trees"), dict)
        or set(binding["trees"]) != {"skills", ".translation"}
        or any(
            not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            for digest in binding["trees"].values()
        )
        or not isinstance(binding.get("sha256"), str)
        or not _SHA256.fullmatch(binding["sha256"])
    ):
        raise BenchmarkError("existing evidence has no valid input snapshot binding")


def _result_from_invocation(
    spec: RunSpec,
    invocation: Invocation,
    *,
    runner_mode: str,
    evidence_dir: Path,
    project_fingerprint: str,
    redactor: LiteralSecretRedactor,
) -> RunResult:
    failure_class = classify_failure(asdict(invocation))
    complete_record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": spec.run_id,
        "case_id": spec.case_id,
        "condition": spec.condition,
        "attempt": spec.attempt,
        "runner_mode": runner_mode,
        "status": "completed" if failure_class == "success" else failure_class,
        "failure_class": failure_class,
        "process_started": invocation.process_started,
        "started_at": invocation.started_at,
        "completed_at": invocation.completed_at,
        "exit_code": invocation.exit_code,
        "timed_out": invocation.timed_out,
        "refused": invocation.refused,
        "malformed_output": invocation.malformed_output,
        "tool_misuse": invocation.tool_misuse,
        "reason": invocation.reason,
        "stdout": invocation.stdout,
        "output_sha256": "",
        "raw_output_path": "",
        "stderr": invocation.stderr,
        "telemetry": invocation.telemetry,
        "usage": invocation.usage,
        "expected_policy_sha256": invocation.expected_policy_sha256,
        "applied_policy_sha256": invocation.applied_policy_sha256,
        "policy_integrity": invocation.policy_integrity,
        "redacted": False,
        "project_fingerprint": project_fingerprint,
        "argv": list(invocation.argv),
        "shell": invocation.shell,
    }
    redacted_record, changed = redactor.value(complete_record)
    if not isinstance(redacted_record, dict):
        raise AssertionError("redacted run record must remain a mapping")
    stdout = redacted_record.pop("stdout")
    if not isinstance(stdout, str):
        raise AssertionError("redacted stdout must remain text")
    digest, raw_path = _store_raw(evidence_dir, stdout)
    redacted_record["output_sha256"] = digest
    redacted_record["raw_output_path"] = raw_path
    redacted_record["redacted"] = changed
    return RunResult.from_record(redacted_record)


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
    if validated["mode"] == "cli":
        if not isinstance(runner, CliRunner):
            raise BenchmarkError("CLI mode requires the configured CliRunner")
        if runner.command != tuple(validated["command"]):
            raise BenchmarkError("CliRunner command does not match configured command")
        if runner.sandbox_adapter != tuple(validated["sandbox_adapter"]):
            raise BenchmarkError("CliRunner adapter does not match configured sandbox_adapter")
    case_map = _cases_by_id(cases)
    expected_policy_sha256_by_run = None
    required_integrity_digests = [
        _schedule_manifest(schedule)["sha256"],
        sha256_bytes(canonical_bytes(dict(validated))),
    ]
    if validated["mode"] == "cli":
        expected_policy_sha256_by_run = {
            spec.run_id: sha256_bytes(canonical_bytes(
                _policy_for_case(validated, case_map[spec.case_id], spec.condition)
            ))
            for spec in schedule
        }
        required_integrity_digests.extend(expected_policy_sha256_by_run.values())
        required_integrity_digests.extend((
            sha256_bytes(canonical_bytes(list(runner.sandbox_adapter))),
            _PROBE_PROGRAM_SHA256,
        ))
    _reject_secret_integrity_collisions(validated, required_integrity_digests)
    evidence_dir = Path(evidence_dir)
    if evidence_dir.is_symlink():
        raise BenchmarkError(f"refusing symlink evidence directory: {evidence_dir}")
    scratch_root = Path(str(validated["scratch_root"]))
    if scratch_root.is_symlink():
        raise BenchmarkError(f"refusing symlink scratch root: {scratch_root}")
    if validated["mode"] == "cli":
        resolved_scratch = scratch_root.resolve()
        suite_root = Path(str(validated["suite_path"])).resolve()
        for protected_root in (_REPOSITORY_ROOT, suite_root):
            try:
                resolved_scratch.relative_to(protected_root)
            except ValueError:
                continue
            raise BenchmarkError(
                f"CLI scratch_root must resolve outside repository/suite root: {protected_root}"
            )
    snapshot_required = validated["mode"] == "cli" or any(
        spec.condition == "suite" for spec in schedule
    )
    _require_snapshot_bound_resume(evidence_dir, required=snapshot_required)
    snapshot_path, snapshot_manifest = _freeze_input_snapshot(
        evidence_dir,
        validated,
        required=snapshot_required,
        required_integrity_digests=required_integrity_digests,
        probe_policy_sha256=(
            tuple(expected_policy_sha256_by_run.values())
            if expected_policy_sha256_by_run is not None
            else ()
        ),
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    probe_manifest = _preflight_cli(
        runner, schedule, case_map, validated, evidence_dir, scratch_root, snapshot_path
    )
    _ensure_run_manifest(
        evidence_dir, schedule, validated,
        input_snapshot=snapshot_manifest, sandbox_probe=probe_manifest,
    )
    completed = _existing_results(
        evidence_dir,
        schedule,
        expected_policy_sha256_by_run=expected_policy_sha256_by_run,
    )
    redactor = _redactor(validated)
    for spec in schedule:
        if spec.run_id in completed:
            continue
        prompt = _render(spec, case_map, templates)
        with tempfile.TemporaryDirectory(
            prefix=f"benchmark-{spec.run_id}-", dir=scratch_root
        ) as temporary:
            attempt_dir = Path(temporary)
            project_dir = attempt_dir / "project"
            home_dir = attempt_dir / "home"
            project_dir.mkdir()
            home_dir.mkdir()
            project_fingerprint = sha256_text(str(project_dir))
            _prepare_project(project_dir, spec, prompt, snapshot_path)
            policy_path = None
            if validated["mode"] == "cli":
                policy_path = attempt_dir / "policy.json"
                _write_private_policy(
                    policy_path,
                    _policy_for_case(validated, case_map[spec.case_id], spec.condition),
                )
            invocation = _invoke_with_prestart_retries(
                runner,
                prompt=prompt,
                project_dir=project_dir,
                home_dir=home_dir if validated["mode"] == "cli" else None,
                policy_path=policy_path,
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
            if validated["mode"] == "cli" and result.policy_integrity != "verified":
                raise BenchmarkError(
                    f"CLI run policy integrity failed: {spec.run_id}; experiment aborted"
                )
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
    _ensure_run_manifest(
        evidence_dir, schedule, validated,
        input_snapshot=None, sandbox_probe=None,
    )
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
    return CliRunner(
        config["command"],
        sandbox_adapter=config["sandbox_adapter"],
        environment=environment,
    )


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
