from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from .common import (
    BenchmarkError,
    append_jsonl_fsync,
    read_json,
    read_jsonl,
    sha256_bytes,
    utc_now,
)


_APPS = ("claude", "codex")
_CONDITIONS = {"normal", "context_only", "suite"}
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ROOT = _REPOSITORY_ROOT / "benchmark-private" / "desktop-calibration"
_EVIDENCE_KIND = "diagnostic_cli_calibration"


class CalibrationRunError(RuntimeError):
    """A host invocation failed after the calibration batch started."""


@dataclass(frozen=True)
class CalibrationTask:
    app: str
    number: int
    run_id: str
    case_id: str
    condition: str
    skills: bool
    task_dir: Path
    prompt_path: Path
    response_path: Path
    project_source: Path
    prompt_sha256: str
    prompt: str


@dataclass(frozen=True)
class CalibrationPack:
    root: Path
    tasks: tuple[CalibrationTask, ...]
    evidence_path: Path
    evidence: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class TaskState:
    task: CalibrationTask
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class RunOptions:
    apps: set[str] | frozenset[str]
    force: bool = False
    probe: bool = False
    timeout_seconds: float = 300
    claude_executable: str = "claude"
    codex_executable: str = "codex"
    claude_model: str | None = None
    codex_model: str | None = None
    source_home: Path = field(default_factory=Path.home)

    def __post_init__(self) -> None:
        apps = frozenset(self.apps)
        if not apps or not apps <= set(_APPS):
            raise BenchmarkError(f"apps must be one or more of: {', '.join(_APPS)}")
        if isinstance(self.timeout_seconds, bool) or self.timeout_seconds <= 0:
            raise BenchmarkError("timeout_seconds must be greater than zero")
        for name, value in (
            ("claude_executable", self.claude_executable),
            ("codex_executable", self.codex_executable),
        ):
            if not isinstance(value, str) or not value:
                raise BenchmarkError(f"{name} must be non-empty text")
        for name, value in (("claude_model", self.claude_model), ("codex_model", self.codex_model)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise BenchmarkError(f"{name} must be non-empty text when provided")
        object.__setattr__(self, "apps", apps)
        object.__setattr__(self, "source_home", Path(self.source_home).expanduser().resolve())


@dataclass(frozen=True)
class RunSummary:
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(frozen=True)
class HostOutcome:
    process_started: bool
    started_at: str
    completed_at: str
    duration_seconds: float
    exit_code: int | None
    timed_out: bool
    response: str
    stderr: str
    command: tuple[str, ...]
    cli_version: str
    model_requested: str | None
    model_observed: str | None
    usage: object
    reason: str | None = None
    stdout: str = ""


Progress = Callable[[str], None]


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise BenchmarkError(f"{label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BenchmarkError(f"{label} must be non-empty text")
    return value


def _path_beneath(root: Path, relative_value: object, label: str) -> Path:
    relative_text = _text(relative_value, label)
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise BenchmarkError(f"{label} must stay inside the calibration root")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise BenchmarkError(f"{label} must stay inside the calibration root") from error
    return candidate


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise BenchmarkError(f"{label} must be a regular file: {path}")


def load_pack(root: Path) -> CalibrationPack:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise BenchmarkError(f"calibration root does not exist: {root}")
    manifest_path = root / "manifest.json"
    _regular_file(manifest_path, "manifest")
    manifest = _object(read_json(manifest_path), "manifest")
    if manifest.get("schema_version") != 1:
        raise BenchmarkError("calibration manifest schema_version must be 1")
    raw_tasks = manifest.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise BenchmarkError("calibration manifest tasks must be a non-empty array")

    tasks: list[CalibrationTask] = []
    run_ids: set[str] = set()
    app_numbers: set[tuple[str, int]] = set()
    for index, raw_task in enumerate(raw_tasks, start=1):
        task = _object(raw_task, f"task {index}")
        app = _text(task.get("app"), f"task {index} app")
        if app not in _APPS:
            raise BenchmarkError(f"task {index} app must be claude or codex")
        number = task.get("number")
        if type(number) is not int or number < 1:
            raise BenchmarkError(f"task {index} number must be a positive integer")
        run_id = _text(task.get("run_id"), f"task {index} run_id")
        if not _RUN_ID.fullmatch(run_id):
            raise BenchmarkError(f"task {index} run_id is invalid: {run_id!r}")
        if run_id in run_ids:
            raise BenchmarkError(f"duplicate run id: {run_id}")
        run_ids.add(run_id)
        if (app, number) in app_numbers:
            raise BenchmarkError(f"duplicate app/number: {app} {number}")
        app_numbers.add((app, number))
        case_id = _text(task.get("case_id"), f"task {index} case_id")
        condition = _text(task.get("condition"), f"task {index} condition")
        if condition not in _CONDITIONS:
            raise BenchmarkError(f"task {index} condition is invalid: {condition!r}")
        skills = task.get("skills")
        if type(skills) is not bool or skills != (condition == "suite"):
            raise BenchmarkError(f"task {index} skills must be true only for suite")
        task_dir = _path_beneath(root, task.get("task_dir"), "task_dir")
        expected_prefix = root / "tasks" / app
        try:
            task_dir.relative_to(expected_prefix)
        except ValueError as error:
            raise BenchmarkError(f"task_dir must stay inside tasks/{app}") from error
        prompt_path = task_dir / "PROMPT.txt"
        response_path = task_dir / "RESPONSE.txt"
        _regular_file(prompt_path, f"prompt for {run_id}")
        _regular_file(response_path, f"response for {run_id}")
        try:
            prompt_bytes = prompt_path.read_bytes()
            prompt = prompt_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise BenchmarkError(f"cannot read UTF-8 prompt for {run_id}: {error}") from error
        prompt_sha256 = _text(task.get("prompt_sha256"), f"task {index} prompt_sha256")
        if not _SHA256.fullmatch(prompt_sha256):
            raise BenchmarkError(f"task {index} prompt_sha256 is invalid")
        actual_prompt_sha256 = sha256_bytes(prompt_bytes)
        if actual_prompt_sha256 != prompt_sha256:
            raise BenchmarkError(
                f"prompt hash mismatch for {run_id}: expected {prompt_sha256}, "
                f"got {actual_prompt_sha256}"
            )
        project_source = root / "projects" / f"{app}-{'with' if skills else 'without'}-skills"
        if project_source.is_symlink() or not project_source.is_dir():
            raise BenchmarkError(f"prepared project is missing for {run_id}: {project_source}")
        tasks.append(
            CalibrationTask(
                app=app,
                number=number,
                run_id=run_id,
                case_id=case_id,
                condition=condition,
                skills=skills,
                task_dir=task_dir,
                prompt_path=prompt_path,
                response_path=response_path,
                project_source=project_source,
                prompt_sha256=prompt_sha256,
                prompt=prompt,
            )
        )

    evidence_path = root / "evidence.jsonl"
    evidence: tuple[Mapping[str, object], ...] = ()
    if evidence_path.exists():
        _regular_file(evidence_path, "calibration evidence")
        evidence = tuple(read_jsonl(evidence_path))
    return CalibrationPack(root, tuple(tasks), evidence_path, evidence)


def _successful_evidence(pack: CalibrationPack, run_id: str) -> Mapping[str, object] | None:
    successful = [
        record
        for record in pack.evidence
        if record.get("run_id") == run_id
        and record.get("status") == "success"
        and record.get("evidence_kind") == _EVIDENCE_KIND
        and record.get("schema_version") == 1
    ]
    return successful[-1] if successful else None


def task_states(pack: CalibrationPack, apps: set[str] | frozenset[str]) -> list[TaskState]:
    selected = frozenset(apps)
    if not selected or not selected <= set(_APPS):
        raise BenchmarkError(f"apps must be one or more of: {', '.join(_APPS)}")
    states: list[TaskState] = []
    for task in pack.tasks:
        if task.app not in selected:
            continue
        try:
            response_bytes = task.response_path.read_bytes()
        except OSError as error:
            raise BenchmarkError(f"cannot read response for {task.run_id}: {error}") from error
        if not response_bytes:
            states.append(TaskState(task, "pending"))
            continue
        evidence = _successful_evidence(pack, task.run_id)
        if evidence is None:
            states.append(TaskState(task, "invalid", "response has no successful evidence"))
            continue
        output_sha256 = sha256_bytes(response_bytes)
        if (
            evidence.get("prompt_sha256") != task.prompt_sha256
            or evidence.get("output_sha256") != output_sha256
        ):
            states.append(TaskState(task, "invalid", "response/evidence hash mismatch"))
            continue
        states.append(TaskState(task, "completed"))
    return states


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise BenchmarkError(f"staging source must be a directory: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob("*"), key=lambda value: value.as_posix()):
        if path.is_symlink():
            raise BenchmarkError(f"refusing symlink in prepared project: {path}")
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        else:
            raise BenchmarkError(f"refusing non-regular prepared project entry: {path}")


def _stage_project(task: CalibrationTask, destination: Path) -> None:
    destination.mkdir(parents=True)
    readme = task.project_source / "README.md"
    if readme.is_file() and not readme.is_symlink():
        shutil.copy2(readme, destination / "README.md")
    if not task.skills:
        return
    translation = task.project_source / ".translation"
    _copy_tree(translation, destination / ".translation")
    if task.app == "claude":
        source = task.project_source / ".claude" / "skills"
        target = destination / ".claude" / "skills"
    else:
        source = task.project_source / ".agents" / "skills"
        target = destination / ".agents" / "skills"
    target.parent.mkdir(parents=True)
    _copy_tree(source, target)


def _atomic_write_text(path: Path, value: str) -> None:
    if path.is_symlink():
        raise BenchmarkError(f"refusing symlink output path: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            target.write(value.encode("utf-8"))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except (OSError, UnicodeEncodeError) as error:
        raise BenchmarkError(f"cannot atomically write {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _resolve_executable(value: str) -> str:
    if os.path.sep in value:
        candidate = Path(value).expanduser().resolve()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise CalibrationRunError(f"CLI executable is not runnable: {candidate}")
        return str(candidate)
    resolved = shutil.which(value)
    if resolved is None:
        raise CalibrationRunError(f"CLI executable was not found: {value}")
    return resolved


def _cli_version(executable: str, timeout_seconds: float) -> str:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=max(2.0, min(timeout_seconds, 30)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CalibrationRunError(f"cannot read CLI version for {executable}: {error}") from error
    version = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0 or not version:
        raise CalibrationRunError(f"cannot read CLI version for {executable}")
    return version.splitlines()[0]


def _isolated_environment(task: CalibrationTask, temporary_root: Path, options: RunOptions) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("CLAUDE_CONFIG_DIR", None)
    if task.app == "claude":
        # Claude's OAuth/keychain login is bound to its real config home. The
        # project-only setting source and restricted tool list keep user
        # settings out of the run while preserving that supported login path.
        environment["HOME"] = str(options.source_home)
    else:
        isolated_home = temporary_root / "home"
        isolated_home.mkdir(mode=0o700)
        environment["HOME"] = str(isolated_home)
        codex_home = temporary_root / "codex-home"
        codex_home.mkdir(mode=0o700)
        environment["CODEX_HOME"] = str(codex_home)
        source_auth = options.source_home / ".codex" / "auth.json"
        if source_auth.is_file() and not source_auth.is_symlink():
            target_auth = codex_home / "auth.json"
            shutil.copyfile(source_auth, target_auth)
            target_auth.chmod(0o600)
    return environment


def _claude_command(executable: str, model: str | None) -> list[str]:
    command = [
        executable,
        "--print",
        "--no-session-persistence",
        "--input-format",
        "text",
        "--output-format",
        "json",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--tools",
        "Read,Glob,Grep,Skill",
        "--permission-mode",
        "dontAsk",
        "--no-chrome",
    ]
    if model is not None:
        command.extend(("--model", model))
    return command


def _codex_command(
    executable: str,
    project: Path,
    output_path: Path,
    model: str | None,
) -> list[str]:
    command = [
        executable,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--output-last-message",
        str(output_path),
        "-C",
        str(project),
    ]
    if model is not None:
        command.extend(("--model", model))
    command.append("-")
    return command


def _safe_command(command: Sequence[str], project: Path, output_path: Path) -> tuple[str, ...]:
    replacements = {str(project): "<PROJECT>", str(output_path): "<LAST_MESSAGE>"}
    return tuple(replacements.get(value, value) for value in command)


def _parse_claude(stdout: str) -> tuple[str, str | None, object]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise BenchmarkError(f"Claude returned malformed JSON: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), str):
        raise BenchmarkError("Claude JSON did not contain a result string")
    model_usage = payload.get("modelUsage")
    observed = None
    if isinstance(model_usage, dict) and model_usage:
        observed = sorted(str(name) for name in model_usage)[0]
    usage = {
        "model_usage": model_usage if isinstance(model_usage, dict) else {},
        "total_cost_usd": payload.get("total_cost_usd"),
        "usage": payload.get("usage", {}),
    }
    return payload["result"], observed, usage


def _parse_codex(stdout: str, output_path: Path) -> tuple[str, str | None, object]:
    if not output_path.is_file() or output_path.is_symlink():
        raise BenchmarkError("Codex did not create its final-message file")
    try:
        response = output_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BenchmarkError(f"cannot read Codex final message: {error}") from error
    observed = None
    usage: object = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if observed is None and isinstance(event.get("model"), str):
            observed = event["model"]
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
    return response, observed, usage


def _invoke_task(
    task: CalibrationTask,
    options: RunOptions,
    executable: str,
    cli_version: str,
) -> HostOutcome:
    with tempfile.TemporaryDirectory(prefix=f"translation-calibration-{task.app}-") as temporary:
        temporary_root = Path(temporary)
        project = temporary_root / "project"
        _stage_project(task, project)
        environment = _isolated_environment(task, temporary_root, options)
        output_path = temporary_root / "last-message.txt"
        model_requested = options.claude_model if task.app == "claude" else options.codex_model
        command = (
            _claude_command(executable, model_requested)
            if task.app == "claude"
            else _codex_command(executable, project, output_path, model_requested)
        )
        started_at = utc_now()
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                input=task.prompt,
                text=True,
                capture_output=True,
                check=False,
                cwd=project,
                env=environment,
                timeout=options.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            duration = time.monotonic() - started
            stderr = error.stderr if isinstance(error.stderr, str) else ""
            stdout = error.stdout if isinstance(error.stdout, str) else ""
            return HostOutcome(
                True,
                started_at,
                utc_now(),
                duration,
                None,
                True,
                "",
                stderr,
                _safe_command(command, project, output_path),
                cli_version,
                model_requested,
                None,
                {},
                f"timed out after {options.timeout_seconds:g} seconds",
                stdout,
            )
        except OSError as error:
            return HostOutcome(
                False,
                started_at,
                utc_now(),
                time.monotonic() - started,
                None,
                False,
                "",
                str(error),
                _safe_command(command, project, output_path),
                cli_version,
                model_requested,
                None,
                {},
                f"could not start process: {error}",
            )
        duration = time.monotonic() - started
        if completed.returncode != 0:
            return HostOutcome(
                True,
                started_at,
                utc_now(),
                duration,
                completed.returncode,
                False,
                "",
                completed.stderr,
                _safe_command(command, project, output_path),
                cli_version,
                model_requested,
                None,
                {},
                f"host exited with status {completed.returncode}",
                completed.stdout,
            )
        try:
            if task.app == "claude":
                response, observed, usage = _parse_claude(completed.stdout)
            else:
                response, observed, usage = _parse_codex(completed.stdout, output_path)
        except BenchmarkError as error:
            return HostOutcome(
                True,
                started_at,
                utc_now(),
                duration,
                completed.returncode,
                False,
                "",
                completed.stderr,
                _safe_command(command, project, output_path),
                cli_version,
                model_requested,
                None,
                {},
                str(error),
                completed.stdout,
            )
        if not response.strip():
            return HostOutcome(
                True,
                started_at,
                utc_now(),
                duration,
                completed.returncode,
                False,
                "",
                completed.stderr,
                _safe_command(command, project, output_path),
                cli_version,
                model_requested,
                observed,
                usage,
                "host returned an empty final answer",
                completed.stdout,
            )
        if not response.endswith("\n"):
            response += "\n"
        return HostOutcome(
            True,
            started_at,
            utc_now(),
            duration,
            completed.returncode,
            False,
            response,
            completed.stderr,
            _safe_command(command, project, output_path),
            cli_version,
            model_requested,
            observed,
            usage,
        )


def _evidence_record(task: CalibrationTask, outcome: HostOutcome) -> dict:
    success = outcome.reason is None
    return {
        "schema_version": 1,
        "evidence_kind": _EVIDENCE_KIND,
        "claim_bearing": False,
        "run_id": task.run_id,
        "case_id": task.case_id,
        "app": task.app,
        "condition": task.condition,
        "skills": task.skills,
        "status": "success" if success else "failed",
        "process_started": outcome.process_started,
        "started_at": outcome.started_at,
        "completed_at": outcome.completed_at,
        "duration_seconds": round(outcome.duration_seconds, 6),
        "exit_code": outcome.exit_code,
        "timed_out": outcome.timed_out,
        "reason": outcome.reason,
        "stderr": outcome.stderr,
        "stdout": outcome.stdout[:65536] if not success else "",
        "prompt_sha256": task.prompt_sha256,
        "output_sha256": sha256_bytes(outcome.response.encode("utf-8")) if success else None,
        "response_path": task.response_path.relative_to(task.task_dir.parents[2]).as_posix(),
        "command": list(outcome.command),
        "shell": False,
        "cli_version": outcome.cli_version,
        "model_requested": outcome.model_requested,
        "model_observed": outcome.model_observed,
        "usage": outcome.usage,
    }


def run_tasks(
    pack: CalibrationPack,
    options: RunOptions,
    *,
    progress: Progress | None = None,
) -> RunSummary:
    states = task_states(pack, options.apps)
    invalid = [state for state in states if state.status == "invalid"]
    if invalid and not options.force:
        names = ", ".join(state.task.run_id for state in invalid)
        raise BenchmarkError(f"orphaned or mismatched response; use --force to replace: {names}")
    pending = [state.task for state in states if state.status != "completed" or options.force]
    skipped = sum(state.status == "completed" and not options.force for state in states)
    if options.probe:
        first_per_app: list[CalibrationTask] = []
        for app in _APPS:
            first = next((task for task in pending if task.app == app), None)
            if first is not None:
                first_per_app.append(first)
        pending = first_per_app
    if not pending:
        return RunSummary(0, skipped, 0)

    executable_by_app: dict[str, str] = {}
    version_by_app: dict[str, str] = {}
    for app in _APPS:
        if any(task.app == app for task in pending):
            configured = options.claude_executable if app == "claude" else options.codex_executable
            first_task = next(task for task in pending if task.app == app)
            try:
                executable = _resolve_executable(configured)
                cli_version = _cli_version(executable, options.timeout_seconds)
            except CalibrationRunError as error:
                timestamp = utc_now()
                requested_model = (
                    options.claude_model if app == "claude" else options.codex_model
                )
                outcome = HostOutcome(
                    False,
                    timestamp,
                    timestamp,
                    0.0,
                    None,
                    False,
                    "",
                    "",
                    (configured,),
                    "",
                    requested_model,
                    None,
                    {},
                    str(error),
                )
                append_jsonl_fsync(pack.evidence_path, _evidence_record(first_task, outcome))
                raise CalibrationRunError(f"{first_task.run_id}: {error}") from error
            executable_by_app[app] = executable
            version_by_app[app] = cli_version

    succeeded = 0
    total = len(pending)
    for index, task in enumerate(pending, start=1):
        if progress is not None:
            progress(f"[{index}/{total}] {task.run_id}: starting")
        outcome = _invoke_task(
            task,
            options,
            executable_by_app[task.app],
            version_by_app[task.app],
        )
        record = _evidence_record(task, outcome)
        if outcome.reason is not None:
            append_jsonl_fsync(pack.evidence_path, record)
            raise CalibrationRunError(f"{task.run_id}: {outcome.reason}")
        _atomic_write_text(task.response_path, outcome.response)
        append_jsonl_fsync(pack.evidence_path, record)
        succeeded += 1
        if progress is not None:
            progress(f"[{index}/{total}] {task.run_id}: saved")
    return RunSummary(succeeded, skipped, 0)


def _apps_argument(value: str) -> frozenset[str]:
    if value == "all":
        return frozenset(_APPS)
    return frozenset((value,))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the diagnostic Claude/Codex PT-PT calibration pack without copy/paste."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="validate the pack and show response state")
    status.add_argument("--root", type=Path, default=_DEFAULT_ROOT)
    status.add_argument("--app", choices=("all",) + _APPS, default="all")

    run = subparsers.add_parser("run", help="execute pending calibration tasks")
    run.add_argument("--root", type=Path, default=_DEFAULT_ROOT)
    run.add_argument("--app", choices=("all",) + _APPS, default="all")
    run.add_argument("--force", action="store_true")
    run.add_argument(
        "--probe",
        action="store_true",
        help="run only the first pending task for each selected CLI",
    )
    run.add_argument("--timeout-seconds", type=float, default=300)
    run.add_argument("--claude-executable", default="claude")
    run.add_argument("--codex-executable", default="codex")
    run.add_argument("--claude-model")
    run.add_argument("--codex-model")
    return parser


def _print_status(pack: CalibrationPack, apps: frozenset[str]) -> int:
    states = task_states(pack, apps)
    for app in _APPS:
        if app not in apps:
            continue
        app_states = [state for state in states if state.task.app == app]
        counts = {
            name: sum(state.status == name for state in app_states)
            for name in ("pending", "completed", "invalid")
        }
        print(
            f"{app}: pending={counts['pending']} completed={counts['completed']} "
            f"invalid={counts['invalid']}"
        )
    totals = {
        name: sum(state.status == name for state in states)
        for name in ("pending", "completed", "invalid")
    }
    print(
        f"total: pending={totals['pending']} completed={totals['completed']} "
        f"invalid={totals['invalid']}"
    )
    return 1 if totals["invalid"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        pack = load_pack(arguments.root)
        apps = _apps_argument(arguments.app)
        if arguments.command == "status":
            return _print_status(pack, apps)
        options = RunOptions(
            apps=apps,
            force=arguments.force,
            probe=arguments.probe,
            timeout_seconds=arguments.timeout_seconds,
            claude_executable=arguments.claude_executable,
            codex_executable=arguments.codex_executable,
            claude_model=arguments.claude_model,
            codex_model=arguments.codex_model,
        )
        summary = run_tasks(pack, options, progress=print)
        print(
            f"completed={summary.succeeded} skipped={summary.skipped} failed={summary.failed}"
        )
        return 0
    except CalibrationRunError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
