from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from collections.abc import Callable
from typing import Mapping

from .common import BenchmarkError, utc_now
from . import product_runner


SETUP_APPS = ("claude", "codex")
PROPOSAL_FILES = (
    "project-brief.md",
    "locales.yaml",
    "glossary.csv",
    "style-guide.md",
    "protected-terms.txt",
)
SETUP_PROMPT = """Autonomously set up this exact product snapshot for reviewing every customer-facing email translation from en-US into European Portuguese (pt-PT).

Use the staged translating-products skill and inspect the actual product files. Do not ask questions or wait for user input. Make conservative, evidence-based decisions from the repository and prepare a formal but natural product-translation configuration. Record any unresolved assumption in the appropriate context file. Do not review or change translations.

You may create or update only these files under .translation:
- project-brief.md
- locales.yaml
- glossary.csv
- style-guide.md
- protected-terms.txt

Write all five complete files before ending. Do not create or modify any other file. Never create setup-approval.json and never run the policy approve command. Approval is handled by the outer runner after this process exits.
"""


def _reject_symlink_path(value: Path | str, label: str) -> None:
    path = Path(os.path.abspath(os.fspath(Path(value).expanduser())))
    for candidate in reversed((path, *path.parents)):
        if candidate == Path(candidate.anchor) or candidate.parent == Path(candidate.anchor):
            continue
        if candidate.is_symlink():
            raise BenchmarkError(f"{label} must not contain a symlink: {candidate}")


@dataclass(frozen=True)
class ProductSetupOptions:
    product_repo: Path
    product_git_object: str
    translation_context: Path
    suite_repo: Path
    current_suite_git_object: str
    improved_suite_git_object: str
    app: str
    approved_by: str
    replace_context: bool = False
    executable: str | None = None
    model: str | None = None
    timeout_seconds: float = 600
    source_home: Path = field(default_factory=Path.home)

    def __post_init__(self) -> None:
        if self.app not in SETUP_APPS:
            raise BenchmarkError(
                f"setup app must be one of: {', '.join(SETUP_APPS)}"
            )
        for name in (
            "product_git_object",
            "current_suite_git_object",
            "improved_suite_git_object",
            "approved_by",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise BenchmarkError(f"{name} must be non-empty text")
        for name in ("executable", "model"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise BenchmarkError(f"{name} must be non-empty text when provided")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            raise BenchmarkError("timeout_seconds must be greater than zero")
        _reject_symlink_path(self.translation_context, "translation context path")
        product_repo = product_runner._resolved(self.product_repo)
        suite_repo = product_runner._resolved(self.suite_repo)
        context = product_runner._resolved(self.translation_context)
        source_home = product_runner._resolved(self.source_home)
        for path, label in (
            (product_repo, "product repository"),
            (suite_repo, "suite repository"),
        ):
            if path.is_symlink() or not path.is_dir():
                raise BenchmarkError(f"{label} must be a directory: {path}")
        try:
            context.relative_to(product_repo)
        except ValueError:
            pass
        else:
            raise BenchmarkError(
                "translation context must be outside the product repository"
            )
        product_runner._validate_output_root(context)
        object.__setattr__(self, "product_repo", product_repo)
        object.__setattr__(self, "suite_repo", suite_repo)
        object.__setattr__(self, "translation_context", context)
        object.__setattr__(self, "source_home", source_home)
        object.__setattr__(self, "approved_by", self.approved_by.strip())


@dataclass(frozen=True)
class SetupStage:
    root: Path
    project: Path
    policy: Path
    product_commit: str
    suite_commit: str


def stage_setup_project(
    options: ProductSetupOptions,
    destination: Path | str,
) -> SetupStage:
    root = Path(destination)
    product_commit = product_runner._commit(
        options.product_repo, options.product_git_object
    )
    suite_commit = product_runner._commit(
        options.suite_repo, options.improved_suite_git_object
    )
    project = root / "project"
    product_runner._extract_archive(
        product_runner._archive(options.product_repo, product_commit),
        project,
    )
    extracted_suite = root / "suite"
    product_runner._extract_archive(
        product_runner._archive(options.suite_repo, suite_commit, "skills"),
        extracted_suite,
    )
    skill_root = (
        project / (".claude" if options.app == "claude" else ".agents") / "skills"
    )
    skill_root.parent.mkdir(parents=True, exist_ok=True)
    product_runner._copy_tree(extracted_suite / "skills", skill_root)
    if options.translation_context.exists():
        if not options.replace_context:
            raise BenchmarkError(
                "translation context already exists; use --replace-context to change it"
            )
        product_runner._copy_tree(
            options.translation_context, project / ".translation"
        )
    policy = (
        skill_root
        / "translating-products"
        / "scripts"
        / "policy.py"
    )
    if policy.is_symlink() or not policy.is_file():
        raise BenchmarkError(
            f"improved suite is missing translating-products policy: {suite_commit}"
        )
    return SetupStage(root, project, policy, product_commit, suite_commit)


def noninteractive_command(
    stage: SetupStage,
    options: ProductSetupOptions,
    output_path: Path,
) -> tuple[str, ...]:
    executable = product_runner._resolve_executable(
        options.executable or options.app
    )
    if options.app == "claude":
        command = [executable]
        if options.model is not None:
            command.extend(("--model", options.model))
        command.extend(
            (
                "--print",
                "--no-session-persistence",
                "--setting-sources",
                "project",
                "--permission-mode",
                "acceptEdits",
                "--no-chrome",
                "--tools",
                "Read,Glob,Grep,Edit,Write,Bash",
                SETUP_PROMPT,
            )
        )
        return tuple(command)
    command = [executable, "exec"]
    if options.model is not None:
        command.extend(("-m", options.model))
    command.extend(
        (
            "-C",
            str(stage.project),
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--ignore-user-config",
            "--output-last-message",
            str(output_path),
            SETUP_PROMPT,
        )
    )
    return tuple(command)


def _codex_environment(
    options: ProductSetupOptions,
    temporary: Path,
) -> dict[str, str]:
    environment = dict(os.environ)
    home = temporary / "home"
    codex_home = temporary / "codex-home"
    home.mkdir(mode=0o700)
    codex_home.mkdir(mode=0o700)
    environment["HOME"] = str(home)
    environment["CODEX_HOME"] = str(codex_home)
    auth = options.source_home / ".codex" / "auth.json"
    if auth.is_file() and not auth.is_symlink():
        target = codex_home / "auth.json"
        shutil.copyfile(auth, target)
        target.chmod(0o600)
    return environment


def _host_diagnostic(completed: subprocess.CompletedProcess[str]) -> str:
    detail = completed.stderr.strip() or completed.stdout.strip()
    return detail[-4096:]


def run_setup_agent(
    stage: SetupStage,
    options: ProductSetupOptions,
) -> None:
    with tempfile.TemporaryDirectory(prefix=f"product-setup-{options.app}-") as text:
        temporary = Path(text)
        command = noninteractive_command(
            stage,
            options,
            temporary / "last-message.txt",
        )
        environment = dict(os.environ)
        environment.pop("CLAUDE_CONFIG_DIR", None)
        if options.app == "codex":
            environment = _codex_environment(options, temporary)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                cwd=stage.project,
                env=environment,
                timeout=options.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise BenchmarkError(
                f"autonomous {options.app} setup timed out after "
                f"{options.timeout_seconds:g} seconds"
            ) from error
        except OSError as error:
            raise BenchmarkError(
                f"cannot start autonomous {options.app} setup: {error}"
            ) from error
        if completed.returncode != 0:
            detail = _host_diagnostic(completed)
            suffix = f": {detail}" if detail else ""
            raise BenchmarkError(
                f"autonomous {options.app} setup exited with status "
                f"{completed.returncode}{suffix}"
            )


def _resolved_suite_commits(options: ProductSetupOptions) -> tuple[str, str]:
    return (
        product_runner._commit(
            options.suite_repo, options.current_suite_git_object
        ),
        product_runner._commit(
            options.suite_repo, options.improved_suite_git_object
        ),
    )


def context_requires_setup(options: ProductSetupOptions) -> bool:
    context = options.translation_context
    if context.is_symlink():
        raise BenchmarkError(f"translation context must not be a symlink: {context}")
    if not context.exists():
        return True
    if not context.is_dir():
        raise BenchmarkError(f"translation context must be a directory: {context}")
    try:
        product_runner._validate_context(context)
        for commit in _resolved_suite_commits(options):
            product_runner._preflight_context_path(
                context, options.suite_repo, commit
            )
    except BenchmarkError as error:
        message = str(error)
        if (
            "missing required files" in message
            or "not ready for suite" in message
        ):
            return True
        raise
    return False


def _proposal(
    translation: Path,
) -> tuple[tuple[str, bytes], ...]:
    if translation.is_symlink() or not translation.is_dir():
        raise BenchmarkError("autonomous setup did not create a translation proposal")
    files = product_runner._tree_files(translation)
    names = tuple(path.relative_to(translation).as_posix() for path in files)
    if "setup-approval.json" in names:
        raise BenchmarkError("the setup agent must not create setup-approval.json")
    if names != tuple(sorted(PROPOSAL_FILES)):
        missing = sorted(set(PROPOSAL_FILES) - set(names))
        extra = sorted(set(names) - set(PROPOSAL_FILES))
        raise BenchmarkError(
            f"translation proposal must contain exactly five context files; "
            f"missing={missing} extra={extra}"
        )
    proposal: list[tuple[str, bytes]] = []
    for name in sorted(PROPOSAL_FILES):
        raw = (translation / name).read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BenchmarkError(
                f"translation proposal is not UTF-8: {name}"
            ) from error
        proposal.append((name, raw))
    return tuple(proposal)


def _empty_collections(proposal: tuple[tuple[str, bytes], ...]) -> tuple[str, ...]:
    text = {name: raw.decode("utf-8") for name, raw in proposal}
    empty: list[str] = []
    glossary_lines = [line for line in text["glossary.csv"].splitlines() if line.strip()]
    if glossary_lines == ["source,target,locale,status"]:
        empty.append("glossary.csv")
    protected = [
        line
        for line in text["protected-terms.txt"].splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not protected:
        empty.append("protected-terms.txt")
    return tuple(empty)


def _run_policy_approval(
    stage: SetupStage,
    options: ProductSetupOptions,
    approved_empty: tuple[str, ...],
) -> None:
    command = [
        sys.executable,
        str(stage.policy),
        "approve",
        "--project-root",
        str(stage.project),
        "--approved-by",
        options.approved_by,
        "--approved-at",
        utc_now(),
    ]
    for name in approved_empty:
        command.extend(("--approved-empty", name))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            cwd=stage.project,
        )
    except OSError as error:
        raise BenchmarkError(f"cannot run setup approval policy: {error}") from error
    if completed.returncode != 0:
        raise BenchmarkError(
            f"setup approval policy failed: {completed.stderr.strip()}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise BenchmarkError("setup approval policy returned invalid JSON") from error
    if not isinstance(result, Mapping) or result.get("status") != "approved":
        raise BenchmarkError("setup approval policy did not approve the context")


def _publish_context(
    source: Path,
    target: Path,
    *,
    replace: bool,
) -> None:
    parent = target.parent
    if parent.is_symlink():
        raise BenchmarkError(f"translation context parent must not be a symlink: {parent}")
    parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.setup-", dir=parent)
    )
    staged = temporary_root / "context"
    backup = temporary_root / "previous"
    installed = False
    backed_up = False
    try:
        product_runner._copy_tree(source, staged)
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_dir():
                raise BenchmarkError(
                    f"translation context must be a regular directory: {target}"
                )
            if not replace:
                raise BenchmarkError(
                    "translation context already exists; use --replace-context to change it"
                )
            os.rename(target, backup)
            backed_up = True
        os.rename(staged, target)
        installed = True
        descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if backed_up:
            shutil.rmtree(backup)
            backed_up = False
    except Exception:
        if installed and target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        if backed_up:
            os.rename(backup, target)
        raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def approve_and_publish(
    stage: SetupStage,
    options: ProductSetupOptions,
    *,
    confirm: Callable[[str], str] = input,
    emit: Callable[[str], None] = print,
) -> None:
    translation = stage.project / ".translation"
    proposal = _proposal(translation)
    empty = _empty_collections(proposal)
    for name, raw in proposal:
        emit(f"--- {name} ---")
        emit(raw.decode("utf-8"))
        if name in empty:
            emit(f"{name}: empty")
    if confirm("Type approve to bind and publish this configuration: ") != "approve":
        raise BenchmarkError("exact approval token was not provided; context was not published")
    _run_policy_approval(stage, options, empty)
    product_runner._validate_context(translation)
    for commit in _resolved_suite_commits(options):
        product_runner._preflight_context_path(
            translation, options.suite_repo, commit
        )
    _publish_context(
        translation,
        options.translation_context,
        replace=options.replace_context,
    )


def ensure_translation_context(
    options: ProductSetupOptions,
    *,
    confirm: Callable[[str], str] = input,
    emit: Callable[[str], None] = print,
) -> bool:
    if not context_requires_setup(options):
        return False
    if options.translation_context.exists() and not options.replace_context:
        raise BenchmarkError(
            "translation context is incomplete; use --replace-context to change it"
        )
    with tempfile.TemporaryDirectory(prefix="product-context-setup-") as text:
        stage = stage_setup_project(options, Path(text) / "workspace")
        run_setup_agent(stage, options)
        approve_and_publish(
            stage,
            options,
            confirm=confirm,
            emit=emit,
        )
    return True
