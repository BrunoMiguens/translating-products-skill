from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from typing import TextIO
from pathlib import Path
from collections.abc import Mapping

from .common import (
    BenchmarkError,
    atomic_write_json,
    canonical_bytes,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .schema import DATASET_VERSION, SCHEMA_VERSION, validate_cases


_MANIFEST_NAME = "dataset-manifest.json"
_SIGNOFF_NAME = "reference-signoff.json"
_MAX_CURATION_BYTES = 1_000_000
_CONFIRMATION_PHRASE = "APPROVE PT-PT V1 HUMAN-AUTHORED REFERENCES"
_CONTEXT_FILES = (
    "project-brief.md",
    "locales.yaml",
    "glossary.csv",
    "style-guide.md",
    "protected-terms.txt",
    "setup-approval.json",
    "decisions.md",
    "translation-memory.csv",
    "research-sources.md",
)
_APPROVED_CONTEXT_FILES = _CONTEXT_FILES[:5]
_SIGNOFF_FIELDS = {
    "schema_version",
    "dataset_version",
    "reviewer_id",
    "approved_at",
    "human_reference_authored",
    "approved_case_ids",
    "dataset_sha256",
    "context_sha256",
    "curation_packet_sha256",
}


def _diff_artifact_path(value: object) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise BenchmarkError("dirty suite tree requires a non-empty diff artifact path")
    path = Path(value)
    if not path.is_file():
        raise BenchmarkError("dirty suite tree requires a diff artifact")
    return path


def dataset_files(dataset_dir: Path) -> list[Path]:
    dataset_dir = Path(dataset_dir)
    if not dataset_dir.is_dir():
        raise BenchmarkError(f"dataset directory does not exist: {dataset_dir}")
    files: list[Path] = []
    for path in dataset_dir.rglob("*"):
        if path.is_symlink():
            raise BenchmarkError(f"dataset must not contain symlinks: {path}")
        if path.is_file() and path.name != _MANIFEST_NAME:
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(dataset_dir).as_posix())


def _content_files(dataset_dir: Path) -> list[Path]:
    return [
        path
        for path in dataset_files(dataset_dir)
        if path.name != _SIGNOFF_NAME and path.suffix != ".diff"
    ]


def _file_hashes(dataset_dir: Path, files: list[Path]) -> dict[str, str]:
    return {
        path.relative_to(dataset_dir).as_posix(): sha256_file(path)
        for path in files
    }


def _inventory_sha256(file_hashes: Mapping[str, str]) -> str:
    return sha256_bytes(canonical_bytes([[name, digest] for name, digest in file_hashes.items()]))


def _context_identity(dataset_dir: Path) -> tuple[dict[str, str], str]:
    context_dir = dataset_dir / "project-context"
    hashes: dict[str, str] = {}
    for name in _CONTEXT_FILES:
        path = context_dir / name
        if path.is_symlink() or not path.is_file():
            raise BenchmarkError(f"project context requires a regular {name}")
        hashes[name] = sha256_file(path)
    extras = sorted(
        path.name for path in context_dir.iterdir()
        if path.is_file() and path.name not in _CONTEXT_FILES
    )
    if extras:
        raise BenchmarkError(f"project context has unknown files: {extras!r}")
    return hashes, _inventory_sha256(hashes)


def _verify_setup_approval(dataset_dir: Path, context_hashes: Mapping[str, str]) -> None:
    approval = read_json(dataset_dir / "project-context/setup-approval.json")
    if not isinstance(approval, dict):
        raise BenchmarkError("project context setup approval must be an object")
    if approval.get("status") != "approved":
        raise BenchmarkError("project context setup approval is not approved")
    for field in ("approved_by", "approved_at"):
        if not isinstance(approval.get(field), str) or not approval[field].strip():
            raise BenchmarkError(f"project context setup approval requires {field}")
    recorded = approval.get("context_sha256")
    expected = {name: context_hashes[name] for name in _APPROVED_CONTEXT_FILES}
    if recorded != expected:
        raise BenchmarkError("project context setup approval hash mismatch")
    if approval.get("approved_empty") != []:
        raise BenchmarkError("Lume project context does not approve empty terminology files")


def _require_valid_curated_output(
    case: Mapping[str, object], output: str, *, description: str,
) -> None:
    from .validate import validate_output

    # Review references and accepted corrections are the corrected translation
    # itself, not the runtime review agent's JSON delivery envelope.
    validation_case = (
        {**case, "task": "translation"}
        if case.get("task") == "review"
        else case
    )
    result = validate_output(validation_case, output)
    if result.status == "passed":
        return
    failures = [finding.invariant for finding in result.findings]
    details = failures + list(result.validator_errors)
    raise BenchmarkError(
        f"case {case['id']} {description} fails declared automatic checks: {details!r}"
    )


def _corrected_review_outputs(
    case: Mapping[str, object], inventory: list[Mapping[str, object]],
) -> tuple[str, ...]:
    outputs = {case["candidate"]}
    for error in sorted(inventory, key=lambda item: (-len(item["candidate_span"]), item["id"])):
        span = error["candidate_span"]
        corrected: set[str] = set()
        for output in outputs:
            for correction in error["accepted_corrections"]:
                if span in output:
                    corrected.add(output.replace(span, correction, 1))
                elif correction in output:
                    corrected.add(output)
                else:
                    raise BenchmarkError(
                        f"seeded error {error['id']} accepted correction cannot be applied"
                    )
        outputs = corrected
        if len(outputs) > 256:
            raise BenchmarkError(f"case {case['id']} has too many accepted correction combinations")
    return tuple(sorted(outputs))


def _validate_curated_content(
    cases: list[dict], seeded: Mapping[str, object],
) -> None:
    for case in cases:
        _require_valid_curated_output(case, case["reference"], description="reference")
        if case["task"] == "review":
            for index, output in enumerate(_corrected_review_outputs(case, seeded[case["id"]])):
                _require_valid_curated_output(
                    case, output, description=f"accepted correction combination {index + 1}",
                )


def build_curation_packet(dataset_dir: Path) -> dict:
    dataset_dir = Path(dataset_dir)
    cases = read_jsonl(dataset_dir / "cases.jsonl")
    seeded = read_json(dataset_dir / "seeded-errors.json")
    validate_cases(cases, seeded)
    _validate_curated_content(cases, seeded)
    content_hashes = _file_hashes(dataset_dir, _content_files(dataset_dir))
    context_hashes, context_sha256 = _context_identity(dataset_dir)
    _verify_setup_approval(dataset_dir, context_hashes)
    project_context = {}
    for name in _CONTEXT_FILES:
        path = dataset_dir / "project-context" / name
        project_context[name] = {
            "sha256": context_hashes[name],
            "content": path.read_text(encoding="utf-8"),
        }
    decisions = []
    for case in cases:
        entry = {
            "id": case["id"],
            "task": case["task"],
            "source_locale": case["source_locale"],
            "target_locale": case["target_locale"],
            "surface": case["surface"],
            "difficulty": case["difficulty"],
            "diagnostic": case["diagnostic"],
            "source": case["source"],
            "context": case["context"],
            "audience": case["audience"],
            "register": case["register"],
            "constraints": case["constraints"],
            "glossary": case["glossary"],
            "protected_terms": case["protected_terms"],
            "invariants": case["invariants"],
            "automatic_checks": case["automatic_checks"],
            "reference": case["reference"],
            "reference_notes": case["reference_notes"],
            "curation_status": "pending-human-curation",
        }
        if case["task"] == "review":
            entry["candidate"] = case["candidate"]
            entry["seeded_errors"] = seeded[case["id"]]
        decisions.append(entry)
    packet = {
        "schema_version": SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "curation_status": "pending-human-curation",
        "human_reference_authored": False,
        "dataset_sha256": _inventory_sha256(content_hashes),
        "context_sha256": context_sha256,
        "dataset_file_sha256": content_hashes,
        "case_ids": [case["id"] for case in cases],
        "project_context": project_context,
        "cases": decisions,
        "review_requirements": [
            "Review every source, constraint, invariant, and project-context decision.",
            "For every translation case, author or substantively edit reference_notes and author or approve the final reference wording.",
            "For every review case, author or substantively edit seeded-error notes and accepted corrections.",
            "Approve all 60 case IDs and the complete project context before interactive sign-off.",
        ],
    }
    if len(canonical_bytes(packet)) > _MAX_CURATION_BYTES:
        raise BenchmarkError("curation packet exceeds the bounded size limit")
    return packet


def verify_curation_packet(dataset_dir: Path, packet_path: Path) -> dict:
    packet_path = Path(packet_path)
    if packet_path.is_symlink() or not packet_path.is_file():
        raise BenchmarkError("curation packet must be a regular file")
    try:
        size = packet_path.stat().st_size
    except OSError as error:
        raise BenchmarkError(f"cannot inspect curation packet: {error}") from error
    if size > _MAX_CURATION_BYTES:
        raise BenchmarkError("curation packet exceeds the bounded size limit")
    packet = read_json(packet_path)
    if not isinstance(packet, dict):
        raise BenchmarkError("curation packet must be an object")
    if packet_path.read_bytes() != canonical_bytes(packet):
        raise BenchmarkError("curation packet must use canonical JSON bytes")
    if packet != build_curation_packet(dataset_dir):
        raise BenchmarkError("curation packet is stale or changed")
    return packet


def write_reference_signoff(
    dataset_dir: Path,
    *,
    reviewer_id: str,
    curation_packet: Path,
    target: Path,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stderr,
) -> dict:
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise BenchmarkError("reviewer id is required")
    packet = verify_curation_packet(dataset_dir, curation_packet)
    if not input_stream.isatty() or not output_stream.isatty():
        raise BenchmarkError("human reference sign-off requires an interactive terminal")
    output_stream.write(
        "After completing every curation decision, type this exact phrase:\n"
        f"{_CONFIRMATION_PHRASE}\n> "
    )
    output_stream.flush()
    confirmation = input_stream.readline(256).rstrip("\r\n")
    if confirmation != _CONFIRMATION_PHRASE:
        raise BenchmarkError("human reference confirmation refused")
    packet = verify_curation_packet(dataset_dir, curation_packet)
    signoff = {
        "schema_version": SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "reviewer_id": reviewer_id.strip(),
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "human_reference_authored": True,
        "approved_case_ids": packet["case_ids"],
        "dataset_sha256": packet["dataset_sha256"],
        "context_sha256": packet["context_sha256"],
        "curation_packet_sha256": sha256_file(curation_packet),
    }
    atomic_write_json(target, signoff)
    return signoff


def _require_reviewer_signoff(signoff: object, cases: list[dict], dataset_dir: Path) -> dict:
    if not isinstance(signoff, dict):
        raise BenchmarkError("PT-PT reviewer sign-off must be an object")
    if set(signoff) != _SIGNOFF_FIELDS:
        raise BenchmarkError("PT-PT reviewer sign-off has invalid fields")
    if signoff.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("PT-PT reviewer sign-off schema version mismatch")
    if signoff.get("dataset_version") != DATASET_VERSION:
        raise BenchmarkError("PT-PT reviewer sign-off dataset version mismatch")
    reviewer = signoff.get("reviewer_id")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise BenchmarkError("PT-PT reviewer sign-off requires reviewer")
    approved_at = signoff.get("approved_at")
    if not isinstance(approved_at, str) or not approved_at:
        raise BenchmarkError("PT-PT reviewer sign-off requires approved_at")
    try:
        parsed_approved_at = datetime.strptime(approved_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise BenchmarkError("PT-PT reviewer sign-off approved_at must be canonical UTC") from error
    if parsed_approved_at.strftime("%Y-%m-%dT%H:%M:%SZ") != approved_at:
        raise BenchmarkError("PT-PT reviewer sign-off approved_at must be canonical UTC")
    if signoff.get("human_reference_authored") is not True:
        raise BenchmarkError("PT-PT reviewer sign-off requires human_reference_authored")
    approved_case_ids = signoff.get("approved_case_ids")
    packet = build_curation_packet(dataset_dir)
    expected_case_ids = packet["case_ids"]
    if (
        not isinstance(approved_case_ids, list)
        or not all(isinstance(case_id, str) and case_id for case_id in approved_case_ids)
    ):
        raise BenchmarkError("PT-PT reviewer sign-off approved case ids must be non-empty strings")
    if approved_case_ids != expected_case_ids:
        raise BenchmarkError("PT-PT reviewer sign-off must approve every case id")
    if len(approved_case_ids) != len(set(approved_case_ids)):
        raise BenchmarkError("PT-PT reviewer sign-off contains duplicate case ids")
    if signoff.get("dataset_sha256") != packet["dataset_sha256"]:
        raise BenchmarkError("PT-PT reviewer sign-off dataset hash mismatch")
    if signoff.get("context_sha256") != packet["context_sha256"]:
        raise BenchmarkError("PT-PT reviewer sign-off context hash mismatch")
    curation_hash = signoff.get("curation_packet_sha256")
    if (
        not isinstance(curation_hash, str)
        or len(curation_hash) != 64
        or any(character not in "0123456789abcdef" for character in curation_hash)
    ):
        raise BenchmarkError("PT-PT reviewer sign-off curation hash is invalid")
    expected_curation_hash = sha256_bytes(canonical_bytes(packet))
    if curation_hash != expected_curation_hash:
        raise BenchmarkError("PT-PT reviewer sign-off curation hash mismatch")
    return signoff


def build_dataset_manifest(
    dataset_dir: Path,
    *,
    suite_commit: str,
    suite_dirty: bool,
    snapshot_id: str | None = None,
    diff_artifact: Path | None = None,
) -> dict:
    if not isinstance(suite_commit, str) or not suite_commit:
        raise BenchmarkError("suite commit is required")
    if type(suite_dirty) is not bool:
        raise BenchmarkError("suite dirty state must be a boolean")
    if suite_dirty:
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise BenchmarkError("dirty suite tree requires an explicit snapshot id")
        diff_path = _diff_artifact_path(diff_artifact)
    dataset_dir = Path(dataset_dir)
    cases = read_jsonl(dataset_dir / "cases.jsonl")
    seeded = read_json(dataset_dir / "seeded-errors.json")
    validate_cases(cases, seeded)
    try:
        signoff = read_json(dataset_dir / "reference-signoff.json")
    except BenchmarkError as error:
        raise BenchmarkError("PT-PT reviewer sign-off is required") from error
    _require_reviewer_signoff(signoff, cases, dataset_dir)
    files = dataset_files(dataset_dir)
    file_hashes = {
        path.relative_to(dataset_dir).as_posix(): sha256_file(path)
        for path in files
    }
    hash_entries = [[name, digest] for name, digest in file_hashes.items()]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "suite_commit": suite_commit,
        "suite_dirty": suite_dirty,
        "files": file_hashes,
        "dataset_sha256": sha256_bytes(canonical_bytes(hash_entries)),
        "reviewer_signoff": signoff,
    }
    if suite_dirty:
        manifest["suite"] = {
            "snapshot_id": snapshot_id,
            "diff_artifact": str(diff_path),
            "diff_sha256": sha256_file(diff_path),
        }
    return manifest


def verify_dataset_manifest(dataset_dir: Path) -> None:
    dataset_dir = Path(dataset_dir)
    manifest = read_json(dataset_dir / _MANIFEST_NAME)
    if not isinstance(manifest, dict):
        raise BenchmarkError("dataset manifest must be an object")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("dataset manifest schema version mismatch")
    if manifest.get("dataset_version") != DATASET_VERSION:
        raise BenchmarkError("dataset manifest dataset version mismatch")
    try:
        provenance = manifest.get("suite", {})
        if not isinstance(provenance, dict):
            raise BenchmarkError("dataset manifest suite provenance must be an object")
        expected = build_dataset_manifest(
            dataset_dir,
            suite_commit=manifest["suite_commit"],
            suite_dirty=manifest["suite_dirty"],
            snapshot_id=provenance.get("snapshot_id"),
            diff_artifact=provenance.get("diff_artifact"),
        )
    except KeyError as error:
        raise BenchmarkError(f"dataset manifest missing {error.args[0]}") from error
    if manifest != expected:
        raise BenchmarkError("dataset manifest hash mismatch")


def _config_sha256(config: object) -> str:
    if isinstance(config, (str, Path)):
        return sha256_file(Path(config))
    if isinstance(config, Mapping):
        return sha256_bytes(canonical_bytes(config))
    raise BenchmarkError("runner config must be a path or object")


def build_run_manifest(
    dataset_dir: Path,
    config: object,
    *,
    suite_commit: str | None = None,
    suite_dirty: bool = False,
    snapshot_id: str | None = None,
    diff_artifact: Path | None = None,
    evidence: Path | None = None,
    schedule_seed: int | None = None,
    bootstrap_seed: int | None = None,
) -> dict:
    if type(suite_dirty) is not bool:
        raise BenchmarkError("suite dirty state must be a boolean")
    verify_dataset_manifest(dataset_dir)
    dataset_manifest = read_json(Path(dataset_dir) / _MANIFEST_NAME)
    if suite_dirty:
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise BenchmarkError("dirty suite tree requires an explicit snapshot id")
        if diff_artifact is None or not Path(diff_artifact).is_file():
            raise BenchmarkError("dirty suite tree requires a diff artifact")
    suite = {
        "commit": suite_commit or dataset_manifest["suite_commit"],
        "dirty": suite_dirty,
    }
    if suite_dirty:
        suite["snapshot_id"] = snapshot_id
        suite["diff_sha256"] = sha256_file(Path(diff_artifact))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset": {
            "dataset_sha256": dataset_manifest["dataset_sha256"],
            "manifest_sha256": sha256_file(Path(dataset_dir) / _MANIFEST_NAME),
        },
        "suite": suite,
        "runner_config_sha256": _config_sha256(config),
        "schedule_seed": schedule_seed,
        "bootstrap_seed": bootstrap_seed,
    }
    if evidence is not None:
        manifest["evidence"] = str(evidence)
    return manifest


def _git_state() -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout)
    except (OSError, subprocess.CalledProcessError) as error:
        raise BenchmarkError(f"cannot determine suite Git state: {error}") from error
    if not commit:
        raise BenchmarkError("cannot determine suite Git commit")
    return commit, dirty


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare reproducible PT-PT benchmark manifests.")
    commands = parser.add_subparsers(dest="command", required=True)
    dataset = commands.add_parser("dataset")
    dataset.add_argument("--dataset", required=True, type=Path)
    dataset.add_argument("--write-manifest", type=Path)
    dataset.add_argument("--snapshot-id")
    dataset.add_argument("--diff-artifact", type=Path)
    curation = commands.add_parser("curation")
    curation.add_argument("--dataset", required=True, type=Path)
    curation.add_argument("--output", required=True, type=Path)
    signoff = commands.add_parser("signoff")
    signoff.add_argument("--dataset", required=True, type=Path)
    signoff.add_argument("--reviewer-id", required=True)
    signoff.add_argument("--curation-packet", required=True, type=Path)
    signoff.add_argument("--write-signoff", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("--dataset", required=True, type=Path)
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--evidence", required=True, type=Path)
    run.add_argument("--schedule-seed", required=True, type=int)
    run.add_argument("--bootstrap-seed", required=True, type=int)
    run.add_argument("--snapshot-id")
    run.add_argument("--diff-artifact", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "curation":
            manifest = build_curation_packet(arguments.dataset)
            atomic_write_json(arguments.output, manifest)
        elif arguments.command == "signoff":
            manifest = write_reference_signoff(
                arguments.dataset,
                reviewer_id=arguments.reviewer_id,
                curation_packet=arguments.curation_packet,
                target=arguments.write_signoff,
            )
        elif arguments.command == "dataset":
            suite_commit, suite_dirty = _git_state()
            manifest = build_dataset_manifest(
                arguments.dataset,
                suite_commit=suite_commit,
                suite_dirty=suite_dirty,
                snapshot_id=arguments.snapshot_id,
                diff_artifact=arguments.diff_artifact,
            )
            target = arguments.write_manifest or arguments.dataset / _MANIFEST_NAME
            atomic_write_json(target, manifest)
        else:
            suite_commit, suite_dirty = _git_state()
            evidence = arguments.evidence
            if evidence.is_symlink():
                raise BenchmarkError(f"refusing symlink evidence directory: {evidence}")
            evidence.mkdir(parents=True, exist_ok=True)
            manifest = build_run_manifest(
                arguments.dataset,
                arguments.config,
                suite_commit=suite_commit,
                suite_dirty=suite_dirty,
                snapshot_id=arguments.snapshot_id,
                diff_artifact=arguments.diff_artifact,
                evidence=evidence,
                schedule_seed=arguments.schedule_seed,
                bootstrap_seed=arguments.bootstrap_seed,
            )
            atomic_write_json(evidence / "run-manifest.json", manifest)
        sys.stdout.buffer.write(canonical_bytes(manifest))
        return 0
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
