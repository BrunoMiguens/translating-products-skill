from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .common import BenchmarkError
from . import product_runner


SETUP_APPS = ("claude", "codex")
PROPOSAL_FILES = (
    "project-brief.md",
    "locales.yaml",
    "glossary.csv",
    "style-guide.md",
    "protected-terms.txt",
)
SETUP_PROMPT = """Set up this exact product snapshot for reviewing every customer-facing email translation from en-US into European Portuguese (pt-PT).

Use the staged translating-products skill. Inspect the actual product files and ask me one focused setup question at a time. Prepare a formal but natural product-translation configuration. Do not review or change translations.

You may create or update only these files under .translation:
- project-brief.md
- locales.yaml
- glossary.csv
- style-guide.md
- protected-terms.txt

Show me the complete proposed configuration before ending the session. Never create setup-approval.json and never run the policy approve command. Approval is handled by the outer runner after this session exits.
"""


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


def interactive_command(
    stage: SetupStage,
    options: ProductSetupOptions,
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
    command = [executable]
    if options.model is not None:
        command.extend(("-m", options.model))
    command.extend(
        (
            "-C",
            str(stage.project),
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "on-request",
            "--no-alt-screen",
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


def launch_setup_session(
    stage: SetupStage,
    options: ProductSetupOptions,
) -> None:
    command = interactive_command(stage, options)
    with tempfile.TemporaryDirectory(prefix=f"product-setup-{options.app}-") as text:
        temporary = Path(text)
        environment = dict(os.environ)
        environment.pop("CLAUDE_CONFIG_DIR", None)
        if options.app == "codex":
            environment = _codex_environment(options, temporary)
        try:
            completed = subprocess.run(
                command,
                check=False,
                cwd=stage.project,
                env=environment,
            )
        except OSError as error:
            raise BenchmarkError(
                f"cannot start interactive {options.app} setup: {error}"
            ) from error
    if completed.returncode != 0:
        raise BenchmarkError(
            f"interactive {options.app} setup exited with status "
            f"{completed.returncode}"
        )

