from __future__ import annotations

import argparse
import subprocess
import sys
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


def _require_reviewer_signoff(signoff: object, cases: list[dict]) -> dict:
    if not isinstance(signoff, dict):
        raise BenchmarkError("PT-PT reviewer sign-off must be an object")
    reviewer = signoff.get("reviewer", signoff.get("reviewer_id"))
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise BenchmarkError("PT-PT reviewer sign-off requires reviewer")
    if not isinstance(signoff.get("approved_at"), str) or not signoff["approved_at"]:
        raise BenchmarkError("PT-PT reviewer sign-off requires approved_at")
    if signoff.get("human_reference_authored") is not True:
        raise BenchmarkError("PT-PT reviewer sign-off requires human_reference_authored")
    approved_case_ids = signoff.get("approved_case_ids")
    expected_case_ids = [case["id"] for case in cases]
    if (
        not isinstance(approved_case_ids, list)
        or not all(isinstance(case_id, str) and case_id for case_id in approved_case_ids)
    ):
        raise BenchmarkError("PT-PT reviewer sign-off approved case ids must be non-empty strings")
    if set(approved_case_ids) != set(expected_case_ids):
        raise BenchmarkError("PT-PT reviewer sign-off must approve every case id")
    if len(approved_case_ids) != len(set(approved_case_ids)):
        raise BenchmarkError("PT-PT reviewer sign-off contains duplicate case ids")
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
        if diff_artifact is None or not Path(diff_artifact).is_file():
            raise BenchmarkError("dirty suite tree requires a diff artifact")
    dataset_dir = Path(dataset_dir)
    cases = read_jsonl(dataset_dir / "cases.jsonl")
    seeded = read_json(dataset_dir / "seeded-errors.json")
    validate_cases(cases, seeded)
    try:
        signoff = read_json(dataset_dir / "reference-signoff.json")
    except BenchmarkError as error:
        raise BenchmarkError("PT-PT reviewer sign-off is required") from error
    _require_reviewer_signoff(signoff, cases)
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
            "diff_artifact": str(diff_artifact),
            "diff_sha256": sha256_file(Path(diff_artifact)),
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
        suite_commit, suite_dirty = _git_state()
        if arguments.command == "dataset":
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
