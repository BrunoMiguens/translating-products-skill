from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class BenchmarkError(ValueError):
    """A benchmark input or reproducibility contract is invalid."""


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise BenchmarkError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def read_json(path: Path) -> object:
    try:
        with Path(path).open("r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"invalid JSON {path}: {error}") from error


def _refuse_output_symlink(path: Path) -> None:
    try:
        if path.is_symlink():
            raise BenchmarkError(f"refusing symlink output path: {path}")
    except OSError as error:
        raise BenchmarkError(f"cannot inspect output path {path}: {error}") from error


def atomic_write_json(path: Path, value: object) -> None:
    path = Path(path)
    _refuse_output_symlink(path)
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            target.write(canonical_bytes(value))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise BenchmarkError(f"cannot atomically write {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def append_jsonl_fsync(path: Path, value: object) -> None:
    try:
        with Path(path).open("ab") as target:
            target.write(canonical_bytes(value))
            target.flush()
            os.fsync(target.fileno())
    except OSError as error:
        raise BenchmarkError(f"cannot append JSONL {path}: {error}") from error


def read_jsonl(path: Path) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise BenchmarkError(f"cannot read JSONL {path}: {error}") from error
    records: list[dict] = []
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise BenchmarkError(f"empty JSONL line {line_number} in {path}")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise BenchmarkError(f"invalid JSONL line {line_number} in {path}: {error}") from error
        if not isinstance(record, dict):
            raise BenchmarkError(f"JSONL line {line_number} in {path} must be an object")
        records.append(record)
    return records


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
