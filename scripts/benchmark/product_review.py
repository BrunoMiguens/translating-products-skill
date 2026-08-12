from __future__ import annotations

import argparse
import ctypes
import csv
import errno
import io
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .common import BenchmarkError, canonical_bytes, sha256_bytes


REQUIRED_COLUMNS = (
    "locale",
    "key",
    "english_source",
    "current_translation",
    "status",
    "reason",
    "recommended_correction",
)
HUMAN_SOURCE_COLUMNS = (
    "locale",
    "key",
    "english_source",
    "current_translation",
)
HUMAN_COLUMNS = (
    "human_decision",
    "human_correction",
    "human_notes",
    "human_severity",
)

_AUTOMATED_STATUSES = {
    "blocked_by_source",
    "change",
    "change_recommended",
    "no_issue_detected",
    "unresolved",
}
_GIT_OBJECT = re.compile(r"[0-9a-fA-F]{7,64}\Z")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CONDITIONS = ("normal", "current_suite", "improved")
_METRICS = (
    "required_error_recall",
    "reported_error_precision",
    "false_positive_correction_rate",
    "correction_success_rate",
)
_HUMAN_DECISIONS = {"change_required", "no_issue_detected"}
_HUMAN_SEVERITIES = {"critical", "major", "minor"}
_MAX_CSV_BYTES = 64 * 1024 * 1024


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_directory_without_symlinks(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    if sys.platform == "darwin" and absolute.parts[:2] in {("/", "var"), ("/", "tmp")}:
        absolute = Path("/private").joinpath(*absolute.parts[1:])
    descriptor = os.open(absolute.anchor, _directory_flags())
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as error:
        os.close(descriptor)
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise BenchmarkError(f"refusing symlink directory component in {path}") from error
        raise BenchmarkError(f"cannot securely open directory {path}: {error}") from error


def _read_descriptor(descriptor: int, description: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, _MAX_CSV_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_CSV_BYTES:
            raise BenchmarkError(f"{description} exceeds the input size limit")
    return b"".join(chunks)


@dataclass
class _HeldFile:
    path: Path
    parent_fd: int
    descriptor: int
    identity: tuple[int, int, int, int]
    raw: bytes

    def verify(self) -> None:
        metadata = os.fstat(self.descriptor)
        try:
            named = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
        except OSError as error:
            raise BenchmarkError(f"input changed during scoring: {self.path}") from error
        current = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        if (
            current != self.identity
            or (named.st_dev, named.st_ino) != self.identity[:2]
            or not stat.S_ISREG(named.st_mode)
        ):
            raise BenchmarkError(f"input changed during scoring: {self.path}")
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        if _read_descriptor(self.descriptor, str(self.path)) != self.raw:
            raise BenchmarkError(f"input bytes changed during scoring: {self.path}")

    def close(self) -> None:
        os.close(self.descriptor)
        os.close(self.parent_fd)


def _hold_regular_file(path: Path, description: str) -> _HeldFile:
    path = Path(os.path.abspath(path))
    parent_fd = _open_directory_without_symlinks(path.parent)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
            or before.st_nlink != 1
        ):
            raise BenchmarkError(f"{description} must be one regular file")
        raw = _read_descriptor(descriptor, description)
        after = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise BenchmarkError(f"{description} changed while being read")
        return _HeldFile(path, parent_fd, descriptor, identity, raw)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
        if error.errno == errno.ELOOP:
            raise BenchmarkError(f"refusing symlink {description}: {path}") from error
        raise BenchmarkError(f"cannot securely read {description} {path}: {error}") from error
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
        raise


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    encoded_source = os.fsencode(source)
    encoded_destination = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(library, "renameatx_np"):
        function = library.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(parent_fd, encoded_source, parent_fd, encoded_destination, 0x4)
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(parent_fd, encoded_source, parent_fd, encoded_destination, 0x1)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), destination)


def _named_symlink(path: Path, description: str) -> None:
    try:
        if path.is_symlink():
            raise BenchmarkError(f"refusing symlink {description}: {path}")
    except OSError as error:
        raise BenchmarkError(f"cannot inspect {description} {path}: {error}") from error


def _resolved_future_path(path: Path) -> Path:
    return path.parent.resolve(strict=True) / path.name


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _private_output_path(path: Path) -> Path:
    _named_symlink(path, "output path")
    parent_fd = _open_directory_without_symlinks(path.parent)
    os.close(parent_fd)
    try:
        resolved = _resolved_future_path(path)
    except OSError as error:
        raise BenchmarkError(f"output parent must be an existing directory: {error}") from error
    tracked = (_REPOSITORY_ROOT / "benchmarks").resolve(strict=True)
    if resolved == tracked or resolved.is_relative_to(tracked):
        raise BenchmarkError("private product data must not be written under tracked benchmarks/")
    private_root = (_REPOSITORY_ROOT / "benchmark-private").resolve(strict=False)
    repository_root = _REPOSITORY_ROOT.resolve(strict=True)
    if (
        resolved == repository_root
        or resolved.is_relative_to(repository_root)
    ) and not (resolved == private_root or resolved.is_relative_to(private_root)):
        raise BenchmarkError(
            "in-repository product data must be written under ignored benchmark-private/"
        )
    return resolved


def _parse_automated_csv(raw: bytes, path: Path) -> list[dict[str, str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BenchmarkError(f"cannot read UTF-8 CSV input {path}: {error}") from error
    try:
        reader = csv.DictReader(text.splitlines(keepends=True), strict=True)
        if tuple(reader.fieldnames or ()) != REQUIRED_COLUMNS:
            raise BenchmarkError(
                f"CSV columns must be exactly {REQUIRED_COLUMNS!r}"
            )
        rows = list(reader)
    except csv.Error as error:
        raise BenchmarkError(f"malformed CSV input {path}: {error}") from error
    if not rows:
        raise BenchmarkError("malformed CSV input: at least one data row is required")

    identities: set[tuple[str, str]] = set()
    checked: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise BenchmarkError(f"malformed CSV row {index}")
        record = {column: row[column] for column in REQUIRED_COLUMNS}
        for column in HUMAN_SOURCE_COLUMNS:
            if not record[column].strip():
                raise BenchmarkError(f"malformed CSV row {index}: {column} is blank")
        identity = (record["locale"], record["key"])
        if identity in identities:
            raise BenchmarkError(f"duplicate locale/key identity: {identity!r}")
        identities.add(identity)
        if record["status"] not in _AUTOMATED_STATUSES:
            raise BenchmarkError(f"malformed CSV row {index}: invalid status")
        if record["status"] in {"change", "change_recommended"}:
            if not record["recommended_correction"].strip():
                raise BenchmarkError(
                    f"malformed CSV row {index}: reported change requires a correction"
                )
        elif record["recommended_correction"]:
            raise BenchmarkError(
                f"malformed CSV row {index}: non-change status has a correction"
            )
        checked.append(record)
    return checked


def _read_automated_csv(path: Path) -> tuple[bytes, list[dict[str, str]]]:
    held = _hold_regular_file(Path(path), "CSV input")
    try:
        return held.raw, _parse_automated_csv(held.raw, Path(path))
    finally:
        held.close()


def _write_bytes_at(directory_fd: int, name: str, value: bytes) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            target.write(value)
            target.flush()
            os.fsync(target.fileno())
    except OSError as error:
        raise BenchmarkError(f"cannot write private packet file {name}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _human_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.DictWriter(
        target,
        fieldnames=HUMAN_SOURCE_COLUMNS + HUMAN_COLUMNS,
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **{column: row[column] for column in HUMAN_SOURCE_COLUMNS},
                **{column: "" for column in HUMAN_COLUMNS},
            }
        )
    return target.getvalue().encode("utf-8")


def prepare_packet(
    input_csv: Path | str,
    output_dir: Path | str,
    *,
    suite_git_object: str,
) -> dict:
    input_path = Path(input_csv)
    output_path = Path(output_dir)
    if type(suite_git_object) is not str or not _GIT_OBJECT.fullmatch(suite_git_object):
        raise BenchmarkError("suite Git object must be a 7-64 character hexadecimal object name")
    if _same_existing_file(input_path, output_path):
        raise BenchmarkError("output path must not alias the CSV input")
    _private_output_path(output_path)
    if output_path.exists() or output_path.is_symlink():
        raise BenchmarkError(f"output directory already exists: {output_path}")

    baseline_bytes, rows = _read_automated_csv(input_path)
    resolved_output = _private_output_path(output_path)
    parent_fd = _open_directory_without_symlinks(output_path.parent)
    temporary_name = f".{output_path.name}.tmp-{uuid4().hex}"
    temporary = resolved_output.parent / temporary_name
    temporary_fd = -1
    conditions_fd = -1
    try:
        os.mkdir(temporary_name, 0o700, dir_fd=parent_fd)
        temporary_fd = os.open(temporary_name, _directory_flags(), dir_fd=parent_fd)
        os.mkdir("conditions", 0o700, dir_fd=temporary_fd)
        conditions_fd = os.open("conditions", _directory_flags(), dir_fd=temporary_fd)
        _write_bytes_at(conditions_fd, "current-suite.csv", baseline_bytes)
        human_bytes = _human_csv_bytes(rows)
        _write_bytes_at(temporary_fd, "human-review.csv", human_bytes)
        manifest = {
            "schema_version": 1,
            "packet_kind": "private-product-review",
            "row_count": len(rows),
            "locales": sorted({row["locale"] for row in rows}),
            "source_columns": list(HUMAN_SOURCE_COLUMNS),
            "suite_git_object": suite_git_object,
            "human_status": "pending",
            "files": {
                "conditions/current-suite.csv": sha256_bytes(baseline_bytes),
                "human-review.csv": sha256_bytes(human_bytes),
            },
        }
        _write_bytes_at(temporary_fd, "packet-manifest.json", canonical_bytes(manifest))
        os.fsync(conditions_fd)
        os.fsync(temporary_fd)
        os.fsync(parent_fd)
        _rename_noreplace(parent_fd, temporary_name, output_path.name)
        return manifest
    except (OSError, BenchmarkError) as error:
        if isinstance(error, OSError):
            if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                error = BenchmarkError(f"output directory already exists: {output_path}")
            else:
                error = BenchmarkError(f"cannot publish private packet: {error}")
        raise error
    finally:
        if conditions_fd >= 0:
            os.close(conditions_fd)
        if temporary_fd >= 0:
            os.close(temporary_fd)
        os.close(parent_fd)
        shutil.rmtree(temporary, ignore_errors=True)


def _parse_json_bytes(raw: bytes, description: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"invalid {description}: {error}") from error
    if not isinstance(value, dict) or canonical_bytes(value) != raw:
        raise BenchmarkError(f"invalid {description}: expected a canonical JSON object")
    return value


def _validate_manifest(manifest: dict) -> None:
    expected_fields = {
        "schema_version",
        "packet_kind",
        "row_count",
        "locales",
        "source_columns",
        "suite_git_object",
        "human_status",
        "files",
    }
    if set(manifest) != expected_fields:
        raise BenchmarkError("packet manifest fields are invalid")
    if (
        manifest["schema_version"] != 1
        or manifest["packet_kind"] != "private-product-review"
        or type(manifest["row_count"]) is not int
        or manifest["row_count"] < 1
        or manifest["source_columns"] != list(HUMAN_SOURCE_COLUMNS)
        or manifest["human_status"] != "pending"
        or type(manifest["suite_git_object"]) is not str
        or not _GIT_OBJECT.fullmatch(manifest["suite_git_object"])
    ):
        raise BenchmarkError("packet manifest values are invalid")
    locales = manifest["locales"]
    if (
        not isinstance(locales, list)
        or not locales
        or any(type(locale) is not str or not locale for locale in locales)
        or locales != sorted(set(locales))
    ):
        raise BenchmarkError("packet manifest locales are invalid")
    files = manifest["files"]
    if not isinstance(files, dict) or set(files) != {
        "conditions/current-suite.csv",
        "human-review.csv",
    }:
        raise BenchmarkError("packet manifest file inventory is invalid")
    if any(
        type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for digest in files.values()
    ):
        raise BenchmarkError("packet manifest file hashes are invalid")


def _parse_human_csv(raw: bytes) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(raw.decode("utf-8").splitlines(keepends=True), strict=True)
        if tuple(reader.fieldnames or ()) != HUMAN_SOURCE_COLUMNS + HUMAN_COLUMNS:
            raise BenchmarkError("human review CSV columns are invalid")
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as error:
        raise BenchmarkError(f"malformed human review CSV: {error}") from error
    if not rows:
        raise BenchmarkError("human review CSV has no rows")
    seen: set[tuple[str, str]] = set()
    checked: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise BenchmarkError(f"malformed human review row {index}")
        record = {column: row[column] for column in HUMAN_SOURCE_COLUMNS + HUMAN_COLUMNS}
        if any(not record[column].strip() for column in HUMAN_SOURCE_COLUMNS):
            raise BenchmarkError(f"human review row {index} has blank source fields")
        identity = (record["locale"], record["key"])
        if identity in seen:
            raise BenchmarkError(f"duplicate human locale/key identity: {identity!r}")
        seen.add(identity)
        decision = record["human_decision"]
        if decision not in _HUMAN_DECISIONS:
            raise BenchmarkError(f"human review row {index} decision is blank or invalid")
        correction = record["human_correction"]
        severity = record["human_severity"]
        if decision == "change_required":
            if not correction.strip():
                raise BenchmarkError(
                    f"human review row {index} change_required needs a correction"
                )
            if severity not in _HUMAN_SEVERITIES:
                raise BenchmarkError(f"human review row {index} severity is invalid")
        elif correction or severity:
            raise BenchmarkError(
                f"human review row {index} no_issue_detected must not have correction or severity"
            )
        checked.append(record)
    return checked


def _read_human_csv(path: Path) -> tuple[bytes, list[dict[str, str]]]:
    held = _hold_regular_file(path, "human review")
    try:
        return held.raw, _parse_human_csv(held.raw)
    finally:
        held.close()


def _condition_paths(
    candidates: Mapping[str, Path | str] | Sequence[str],
) -> dict[str, Path]:
    values: list[tuple[str, Path]] = []
    if isinstance(candidates, Mapping):
        values = [(str(name), Path(path)) for name, path in candidates.items()]
    elif isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
        for specification in candidates:
            if type(specification) is not str or "=" not in specification:
                raise BenchmarkError("candidate must use CONDITION=CSV syntax")
            name, path = specification.split("=", 1)
            if not name or not path:
                raise BenchmarkError("candidate must use CONDITION=CSV syntax")
            values.append((name, Path(path)))
    else:
        raise BenchmarkError("candidates must be a mapping or sequence")

    parsed: dict[str, Path] = {}
    for name, path in values:
        if name in parsed:
            raise BenchmarkError(f"duplicate condition name: {name}")
        parsed[name] = path
    if set(parsed) != set(_CONDITIONS):
        raise BenchmarkError(
            f"required conditions are {', '.join(_CONDITIONS)}"
        )
    paths = [parsed[name] for name in _CONDITIONS]
    for path in paths:
        _named_symlink(path, "candidate CSV")
        if not path.is_file():
            raise BenchmarkError(f"candidate CSV is not a regular file: {path}")
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if _same_existing_file(left, right):
                raise BenchmarkError("candidate condition files must not alias one another")
    return parsed


def _packet_files(packet_dir: Path) -> tuple[Path, Path, Path]:
    _named_symlink(packet_dir, "packet directory")
    if not packet_dir.is_dir():
        raise BenchmarkError("packet directory is missing")
    try:
        for entry in packet_dir.rglob("*"):
            if entry.is_symlink():
                raise BenchmarkError(f"private packet must not contain symlinks: {entry}")
    except OSError as error:
        raise BenchmarkError(f"cannot inspect private packet: {error}") from error
    return (
        packet_dir / "packet-manifest.json",
        packet_dir / "conditions" / "current-suite.csv",
        packet_dir / "human-review.csv",
    )


def _verify_sources(
    reference_rows: list[dict[str, str]],
    rows: list[dict[str, str]],
    description: str,
) -> dict[tuple[str, str], dict[str, str]]:
    reference = {
        (row["locale"], row["key"]): row
        for row in reference_rows
    }
    observed = {(row["locale"], row["key"]): row for row in rows}
    if set(observed) != set(reference):
        raise BenchmarkError(f"{description} locale/key identities do not match")
    for identity, row in observed.items():
        expected = reference[identity]
        if any(row[column] != expected[column] for column in HUMAN_SOURCE_COLUMNS):
            raise BenchmarkError(f"{description} source fields do not match for {identity!r}")
    return observed


def _rate(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _score_condition(
    human_rows: list[dict[str, str]],
    candidate_rows: dict[tuple[str, str], dict[str, str]],
) -> dict:
    true_positive = false_positive = true_negative = false_negative = 0
    corrected_required = false_positive_corrections = 0
    disagreements: list[dict[str, object]] = []
    for human in human_rows:
        identity = (human["locale"], human["key"])
        candidate = candidate_rows[identity]
        required = human["human_decision"] == "change_required"
        reported = candidate["status"] in {"change", "change_recommended"}
        if required and reported:
            true_positive += 1
        elif required:
            false_negative += 1
        elif reported:
            false_positive += 1
        else:
            true_negative += 1
        if required and reported:
            exact = candidate["recommended_correction"] == human["human_correction"]
            corrected_required += int(exact)
            if not exact:
                disagreements.append(
                    {
                        "locale": human["locale"],
                        "key": human["key"],
                        "candidate_correction": candidate["recommended_correction"],
                        "human_correction": human["human_correction"],
                        "human_preference": None,
                    }
                )
        if not required and reported:
            false_positive_corrections += 1
    required_count = true_positive + false_negative
    negative_count = false_positive + true_negative
    reported_count = true_positive + false_positive
    return {
        "confusion_matrix": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
        },
        "required_error_recall": _rate(true_positive, required_count),
        "reported_error_precision": _rate(true_positive, reported_count),
        "false_positive_correction_rate": _rate(
            false_positive_corrections, negative_count
        ),
        "correction_success_rate": _rate(corrected_required, required_count),
        "correction_disagreements": disagreements,
    }


def _publish_exclusive_json(path: Path, value: dict) -> None:
    _named_symlink(path, "score output")
    if path.exists():
        raise BenchmarkError(f"score output already exists: {path}")
    parent_fd = _open_directory_without_symlinks(path.parent)
    temporary = f".{path.name}.tmp-{uuid4().hex}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(descriptor, "wb") as target:
            descriptor = -1
            target.write(canonical_bytes(value))
            target.flush()
            os.fsync(target.fileno())
        os.fsync(parent_fd)
        os.link(
            temporary,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError as error:
        raise BenchmarkError(f"score output already exists: {path}") from error
    except OSError as error:
        raise BenchmarkError(f"cannot publish score output {path}: {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass
        os.close(parent_fd)


def score_packet(
    packet_dir: Path | str,
    candidates: Mapping[str, Path | str] | Sequence[str],
    output: Path | str,
) -> dict:
    packet_path = Path(packet_dir)
    output_path = Path(output)
    manifest_path, current_path, human_path = _packet_files(packet_path)
    _private_output_path(output_path)
    _named_symlink(output_path, "score output")
    if output_path.exists():
        raise BenchmarkError(f"score output already exists: {output_path}")
    condition_paths = _condition_paths(candidates)
    if (
        Path(os.path.abspath(condition_paths["current_suite"]))
        != Path(os.path.abspath(current_path))
        or not _same_existing_file(condition_paths["current_suite"], current_path)
    ):
        raise BenchmarkError("current_suite candidate must be the preserved packet baseline")

    held: dict[str, _HeldFile] = {}
    try:
        held["manifest"] = _hold_regular_file(manifest_path, "packet manifest")
        held["current_suite"] = _hold_regular_file(
            current_path, "preserved current suite"
        )
        held["human"] = _hold_regular_file(human_path, "human review")
        for condition in ("normal", "improved"):
            held[condition] = _hold_regular_file(
                condition_paths[condition], f"{condition} candidate"
            )
        candidate_identities = [
            held[condition].identity[:2] for condition in _CONDITIONS
        ]
        if len(set(candidate_identities)) != len(candidate_identities):
            raise BenchmarkError("candidate condition files must not alias one another")

        manifest_bytes = held["manifest"].raw
        manifest = _parse_json_bytes(manifest_bytes, "packet manifest")
        _validate_manifest(manifest)
        baseline_bytes = held["current_suite"].raw
        baseline_rows = _parse_automated_csv(baseline_bytes, current_path)
        if sha256_bytes(baseline_bytes) != manifest["files"]["conditions/current-suite.csv"]:
            raise BenchmarkError("preserved current-suite hash mismatch")
        if manifest["row_count"] != len(baseline_rows):
            raise BenchmarkError("packet row count does not match preserved current suite")
        if manifest["locales"] != sorted({row["locale"] for row in baseline_rows}):
            raise BenchmarkError("packet locales do not match preserved current suite")

        human_bytes = held["human"].raw
        human_rows = _parse_human_csv(human_bytes)
        _verify_sources(baseline_rows, human_rows, "human review")
        if len(human_rows) != manifest["row_count"]:
            raise BenchmarkError("human review row count does not match packet")

        scored: dict[str, dict] = {}
        condition_hashes: dict[str, str] = {}
        for condition in _CONDITIONS:
            candidate_bytes = held[condition].raw
            candidate_rows = _parse_automated_csv(
                candidate_bytes, condition_paths[condition]
            )
            by_identity = _verify_sources(
                human_rows,
                candidate_rows,
                f"{condition} candidate",
            )
            scored[condition] = _score_condition(human_rows, by_identity)
            condition_hashes[condition] = sha256_bytes(candidate_bytes)

        differences = {}
        for name, comparison in (
            ("improved_minus_normal", "normal"),
            ("improved_minus_current_suite", "current_suite"),
        ):
            differences[name] = {
                metric: (scored["improved"][metric] - scored[comparison][metric]) * 100.0
                for metric in _METRICS
            }
        result = {
            "schema_version": 1,
            "packet_manifest_sha256": sha256_bytes(manifest_bytes),
            "human_review_sha256": sha256_bytes(human_bytes),
            "row_count": len(human_rows),
            "condition_sha256": condition_hashes,
            "conditions": scored,
            "differences_percentage_points": differences,
            "human_preference_status": "not_inferred",
            "evidence_role": "regression_diagnostic",
        }
        for value in held.values():
            value.verify()
        _publish_exclusive_json(output_path, result)
        return result
    finally:
        for value in held.values():
            value.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and score private product reviews")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="prepare a private human curation packet")
    prepare.add_argument("--input-csv", required=True, type=Path)
    prepare.add_argument("--output-dir", required=True, type=Path)
    prepare.add_argument("--suite-git-object", required=True)
    score = commands.add_parser("score", help="score three candidates against human rows")
    score.add_argument("--packet-dir", required=True, type=Path)
    score.add_argument("--candidate", action="append", required=True)
    score.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            prepare_packet(
                args.input_csv,
                args.output_dir,
                suite_git_object=args.suite_git_object,
            )
            return 0
        if args.command == "score":
            score_packet(args.packet_dir, args.candidate, args.output)
            return 0
        raise BenchmarkError(f"unknown command: {args.command}")
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
