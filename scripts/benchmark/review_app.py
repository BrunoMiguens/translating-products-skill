from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .blind import scan_visible_bundle
from .common import BenchmarkError, canonical_bytes, utc_now
from .schema import SCHEMA_VERSION


COMPARISON_VALUES = frozenset({
    "left_clear", "left_slight", "tie", "right_slight", "right_clear",
})
CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
MQM_DIMENSIONS = frozenset({
    "accuracy", "terminology", "linguistic_quality", "style_register",
    "locale_audience", "product_integrity",
})
MQM_SEVERITIES = frozenset({"critical", "major", "minor", "neutral"})
MAJOR_SEVERITIES = frozenset({"critical", "major"})
MAX_BODY_BYTES = 1024 * 1024
MAX_JSON_INTEGER_DIGITS = 4096
MAX_JSON_NESTING = 256
CSP = "default-src 'self'; connect-src 'self'; img-src 'none'; object-src 'none'"
UI_DIRECTORY = Path(__file__).with_name("review_ui")

_ANNOTATION_FIELDS = frozenset({
    "item_id", "revision", "comparisons", "confidence", "mqm",
    "major_or_worse", "note",
})
_STORED_FIELDS = _ANNOTATION_FIELDS | {"sequence", "saved_at"}
_MQM_FIELDS = frozenset({
    "output", "dimension", "severity", "start", "end", "note",
})
_ITEM_ID = re.compile(r"^item-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_HOST = re.compile(r"^(127\.0\.0\.1|localhost):([1-9][0-9]{0,4})$")
_ASCII_LENGTH = re.compile(r"^[0-9]+$")
_LOCK_FIELDS = frozenset({
    "schema_version", "reviewer_id", "locked_at", "bundle_sha256",
    "annotations_sha256", "state_sha256",
})
_ARTIFACT_NAMES = (
    "annotations.jsonl", "annotation-state.json", "annotation-lock.json",
)
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


def _is_exact_int(value: object) -> bool:
    return type(value) is int


def _require_text(value: object, description: str, *, allow_empty: bool = True) -> str:
    if type(value) is not str or (not allow_empty and not value.strip()):
        qualifier = "non-empty text" if not allow_empty else "text"
        raise BenchmarkError(f"{description} must be {qualifier}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise BenchmarkError(f"{description} must contain valid Unicode text") from error
    return value


def _require_reviewer_id(value: object) -> str:
    reviewer_id = _require_text(value, "reviewer_id", allow_empty=False)
    if reviewer_id != reviewer_id.strip() or len(reviewer_id) > 200:
        raise BenchmarkError("reviewer_id must be non-empty trimmed text")
    return reviewer_id


def _require_utc_timestamp(value: object, description: str) -> str:
    timestamp = _require_text(value, description, allow_empty=False)
    if _UTC_TIMESTAMP.fullmatch(timestamp) is None:
        raise BenchmarkError(f"{description} must be a canonical UTC timestamp")
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise BenchmarkError(f"{description} must be a canonical UTC timestamp") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != timestamp:
        raise BenchmarkError(f"{description} must be a canonical UTC timestamp")
    return timestamp


def _require_sha256(value: object, description: str) -> str:
    digest = _require_text(value, description, allow_empty=False)
    if _SHA256.fullmatch(digest) is None:
        raise BenchmarkError(f"{description} must be a SHA-256 digest")
    return digest


def _output_labels(item: Mapping[str, object]) -> tuple[str, ...]:
    outputs = item.get("outputs")
    if not isinstance(outputs, Mapping) or set(outputs) not in ({"A", "B"}, {"A", "B", "C"}):
        raise BenchmarkError("review item has invalid anonymous outputs")
    labels = tuple(label for label in ("A", "B", "C") if label in outputs)
    for label in labels:
        _require_text(outputs[label], f"review item output {label}")
    return labels


def _comparison_keys(labels: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        f"{left}:{right}"
        for index, left in enumerate(labels)
        for right in labels[index + 1 :]
    )


def validate_annotation(item: Mapping[str, object], event: Mapping[str, object]) -> None:
    """Validate one reviewer-authored revision against its anonymous item."""
    if not isinstance(item, Mapping):
        raise BenchmarkError("review item must be an object")
    if not isinstance(event, Mapping) or set(event) != _ANNOTATION_FIELDS:
        raise BenchmarkError("invalid annotation fields")
    item_id = _require_text(item.get("id"), "review item id", allow_empty=False)
    if event.get("item_id") != item_id:
        raise BenchmarkError("annotation item_id does not match review item")
    revision = event.get("revision")
    if not _is_exact_int(revision) or revision < 1:
        raise BenchmarkError("annotation revision must be a positive integer")

    labels = _output_labels(item)
    comparisons = event.get("comparisons")
    if not isinstance(comparisons, Mapping):
        raise BenchmarkError("comparisons must be an object")
    expected_comparisons = _comparison_keys(labels)
    for key in expected_comparisons:
        if key not in comparisons:
            raise BenchmarkError(f"missing comparison {key}")
    unknown_comparisons = set(comparisons) - set(expected_comparisons)
    if unknown_comparisons:
        raise BenchmarkError(f"unknown comparison {next(iter(unknown_comparisons))!r}")
    for key in expected_comparisons:
        comparison = _require_text(comparisons[key], f"comparison {key}")
        if comparison not in COMPARISON_VALUES:
            raise BenchmarkError(f"comparison {key} has invalid value")

    confidence = _require_text(event.get("confidence"), "confidence")
    if confidence not in CONFIDENCE_VALUES:
        raise BenchmarkError("confidence must be high, medium, or low")
    _require_text(event.get("note"), "annotation note")

    major_or_worse = event.get("major_or_worse")
    if not isinstance(major_or_worse, Mapping) or set(major_or_worse) != set(labels):
        raise BenchmarkError("major_or_worse fields must exactly match anonymous outputs")
    for label in labels:
        if type(major_or_worse[label]) is not bool:
            raise BenchmarkError(f"major_or_worse {label} must be a boolean")

    mqm = event.get("mqm")
    if not isinstance(mqm, list):
        raise BenchmarkError("mqm must be a list")
    observed_major = {label: False for label in labels}
    outputs = item["outputs"]
    for index, finding in enumerate(mqm):
        prefix = f"mqm entry {index + 1}"
        if not isinstance(finding, Mapping) or set(finding) != _MQM_FIELDS:
            raise BenchmarkError(f"{prefix} has invalid fields")
        output = _require_text(finding.get("output"), f"{prefix} output")
        if output not in labels:
            raise BenchmarkError(f"{prefix} output must name an anonymous output")
        dimension = _require_text(finding.get("dimension"), f"{prefix} dimension")
        if dimension not in MQM_DIMENSIONS:
            raise BenchmarkError(f"{prefix} has invalid dimension")
        severity = _require_text(finding.get("severity"), f"{prefix} severity")
        if severity not in MQM_SEVERITIES:
            raise BenchmarkError(f"{prefix} has invalid severity")
        start = finding.get("start")
        end = finding.get("end")
        output_text = outputs[output]
        if (
            not _is_exact_int(start)
            or not _is_exact_int(end)
            or start < 0
            or end <= start
            or end > len(output_text)
        ):
            raise BenchmarkError(f"{prefix} span is outside output {output}")
        _require_text(finding.get("note"), f"{prefix} note")
        if severity in MAJOR_SEVERITIES:
            observed_major[output] = True
    for label in labels:
        if major_or_worse[label] is not observed_major[label]:
            raise BenchmarkError(
                f"major_or_worse {label} must match critical or major MQM entries"
            )


def _validate_bundle_shape(value: object, *, public_contract: bool) -> dict:
    if public_contract:
        if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int:
            raise BenchmarkError("review bundle schema version must be an integer")
        scan_visible_bundle(value)
        return dict(value)
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "items"}:
        raise BenchmarkError("review bundle has invalid top-level fields")
    if type(value.get("schema_version")) is not int or value.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("review bundle schema version mismatch")
    items = value.get("items")
    if not isinstance(items, list) or not items:
        raise BenchmarkError("review bundle must contain at least one presentation")
    seen: set[str] = set()
    for index, review_item in enumerate(items):
        if not isinstance(review_item, Mapping):
            raise BenchmarkError(f"review item {index} must be an object")
        required = {"id", "source", "context", "constraints", "outputs"}
        if not required <= set(review_item) or not set(review_item) <= required | {"candidate"}:
            raise BenchmarkError(f"review item {index} has invalid fields")
        item_id = _require_text(review_item.get("id"), f"review item {index} id", allow_empty=False)
        if item_id in seen:
            raise BenchmarkError(f"duplicate review item id {item_id}")
        seen.add(item_id)
        _require_text(review_item.get("source"), f"review item {item_id} source")
        if "candidate" in review_item:
            _require_text(review_item["candidate"], f"review item {item_id} candidate")
        _output_labels(review_item)
        try:
            canonical_bytes(review_item["context"])
            canonical_bytes(review_item["constraints"])
        except (TypeError, ValueError) as error:
            raise BenchmarkError(f"review item {item_id} metadata is not JSON: {error}") from error
    return dict(value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_follow_flag() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _open_directory(path: Path) -> tuple[Path, int]:
    path = Path(path)
    try:
        if path.is_symlink():
            raise BenchmarkError(f"refusing symlink annotation directory: {path}")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise BenchmarkError(f"annotation directory is not a directory: {path}")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _no_follow_flag()
        descriptor = os.open(resolved, flags)
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot open annotation directory {path}: {error}") from error
    return resolved, descriptor


def _artifact_stat(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise BenchmarkError(f"cannot inspect artifact {name}: {error}") from error
    if stat.S_ISLNK(info.st_mode):
        raise BenchmarkError(f"refusing symlink artifact: {name}")
    if not stat.S_ISREG(info.st_mode):
        raise BenchmarkError(f"artifact must be a regular file: {name}")
    if info.st_nlink != 1:
        raise BenchmarkError(f"refusing multiply linked artifact: {name}")
    return info


def _read_artifact(directory_fd: int, name: str) -> bytes:
    _artifact_stat(directory_fd, name)
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NONBLOCK | _no_follow_flag(), dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise BenchmarkError(f"artifact changed while opening: {name}")
            return source.read()
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot read artifact {name}: {error}") from error


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        offset += os.write(descriptor, data[offset:])


def _exclusive_artifact(directory_fd: int, name: str, data: bytes) -> None:
    _artifact_stat(directory_fd, name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow_flag()
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
        try:
            _write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory_fd)
    except OSError as error:
        raise BenchmarkError(f"cannot create artifact {name}: {error}") from error


def _replace_artifact(directory_fd: int, name: str, data: bytes) -> None:
    _artifact_stat(directory_fd, name)
    temporary = f".{name}.tmp-{os.getpid()}-{id(data):x}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow_flag()
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        _write_all(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except OSError as error:
        raise BenchmarkError(f"cannot replace artifact {name}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _append_artifact(directory_fd: int, name: str, data: bytes) -> None:
    _artifact_stat(directory_fd, name)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | _no_follow_flag()
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise BenchmarkError(f"artifact changed while opening: {name}")
            _write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory_fd)
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot append artifact {name}: {error}") from error


def _bounded_json_integer(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > MAX_JSON_INTEGER_DIGITS:
        raise ValueError(f"JSON integer exceeds {MAX_JSON_INTEGER_DIGITS} digits")
    return int(token)


def _reject_json_constant(token: str) -> object:
    raise ValueError(f"invalid JSON constant {token!r}")


def _validate_json_nesting(value: object) -> None:
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_NESTING:
            raise ValueError(f"JSON nesting exceeds {MAX_JSON_NESTING} levels")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _decode_json_bytes(data: bytes, description: str) -> object:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_int=_bounded_json_integer,
            parse_constant=_reject_json_constant,
        )
        _validate_json_nesting(value)
    except (ValueError, RecursionError, OverflowError, UnicodeError) as error:
        raise BenchmarkError(f"invalid {description}: {error}") from error
    return value


def _parse_json_bytes(data: bytes, description: str) -> object:
    value = _decode_json_bytes(data, description)
    try:
        encoded = canonical_bytes(value)
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError) as error:
        raise BenchmarkError(f"invalid {description}: {error}") from error
    if encoded != data:
        raise BenchmarkError(f"{description} is not canonical JSON")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise BenchmarkError(f"duplicate JSON field {key!r}")
        value[key] = item
    return value


def _parse_jsonl_bytes(data: bytes, description: str) -> list[dict]:
    if not data:
        return []
    records: list[dict] = []
    for line_number, line in enumerate(data.splitlines(keepends=True), start=1):
        if not line.endswith(b"\n"):
            raise BenchmarkError(f"{description} line {line_number} has no newline")
        value = _parse_json_bytes(line, f"{description} line {line_number}")
        if not isinstance(value, dict):
            raise BenchmarkError(f"{description} line {line_number} must be an object")
        records.append(value)
    return records


class ReviewStore:
    """Append-only reviewer annotations backed by fixed local artifacts."""

    def __init__(self, bundle: Mapping[str, object] | Path | str, directory: Path | str):
        self.directory, self._directory_fd = _open_directory(Path(directory))
        self.events_path = self.directory / "annotations.jsonl"
        self.state_path = self.directory / "annotation-state.json"
        self.lock_path = self.directory / "annotation-lock.json"
        self.lock_record: dict | None = None
        self.latest: dict[str, dict] = {}
        self._history: list[dict] = []

        for artifact_name in _ARTIFACT_NAMES:
            _artifact_stat(self._directory_fd, artifact_name)

        if isinstance(bundle, Mapping):
            validated_bundle = _validate_bundle_shape(bundle, public_contract=False)
            self.bundle_path = self.directory / "bundle.json"
            bundle_bytes = canonical_bytes(validated_bundle)
            self.bundle = _validate_bundle_shape(
                _parse_json_bytes(bundle_bytes, "review bundle"),
                public_contract=False,
            )
            existing = _artifact_stat(self._directory_fd, "bundle.json")
            if existing is None:
                _exclusive_artifact(self._directory_fd, "bundle.json", bundle_bytes)
            elif _read_artifact(self._directory_fd, "bundle.json") != bundle_bytes:
                raise BenchmarkError("pre-existing bundle.json does not match review bundle")
            self._bundle_bytes = bundle_bytes
        else:
            requested_bundle = Path(bundle)
            try:
                if requested_bundle.is_symlink():
                    raise BenchmarkError(f"refusing symlink review bundle: {requested_bundle}")
                self.bundle_path = requested_bundle.resolve(strict=True)
                artifact_paths = {
                    (self.directory / artifact_name).resolve(strict=False)
                    for artifact_name in _ARTIFACT_NAMES
                }
                if self.bundle_path in artifact_paths:
                    raise BenchmarkError("review bundle must not alias an annotation artifact")
                descriptor = os.open(
                    self.bundle_path,
                    os.O_RDONLY | os.O_NONBLOCK | _no_follow_flag(),
                )
                with os.fdopen(descriptor, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise BenchmarkError("review bundle must be a regular file")
                    self._bundle_bytes = source.read()
            except BenchmarkError:
                raise
            except OSError as error:
                raise BenchmarkError(f"cannot read review bundle: {error}") from error
            parsed = _parse_json_bytes(self._bundle_bytes, "review bundle")
            self.bundle = _validate_bundle_shape(parsed, public_contract=True)

        self._items = {review_item["id"]: review_item for review_item in self.bundle["items"]}
        self._bundle_sha256 = _sha256(self._bundle_bytes)
        self._load_and_recover()

    def close(self) -> None:
        descriptor = getattr(self, "_directory_fd", None)
        if descriptor is not None:
            os.close(descriptor)
            self._directory_fd = None

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    @property
    def locked(self) -> bool:
        return self.lock_record is not None

    def item(self, item_id: str) -> dict:
        try:
            return self._items[item_id]
        except (KeyError, TypeError) as error:
            raise BenchmarkError(f"unknown item {item_id!r}") from error

    def _assert_bundle_unchanged(self) -> None:
        if self.bundle_path == self.directory / "bundle.json":
            current = _read_artifact(self._directory_fd, "bundle.json")
        else:
            try:
                if self.bundle_path.is_symlink():
                    raise BenchmarkError("review bundle was replaced by a symlink")
                descriptor = os.open(
                    self.bundle_path,
                    os.O_RDONLY | os.O_NONBLOCK | _no_follow_flag(),
                )
                with os.fdopen(descriptor, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise BenchmarkError("review bundle is no longer a regular file")
                    current = source.read()
            except BenchmarkError:
                raise
            except OSError as error:
                raise BenchmarkError(f"cannot re-read review bundle: {error}") from error
        if current != self._bundle_bytes:
            raise BenchmarkError("review bundle bytes changed during annotation")

    def _assert_history_unchanged(self) -> None:
        expected = b"".join(canonical_bytes(record) for record in self._history)
        if _artifact_stat(self._directory_fd, "annotations.jsonl") is None:
            current = b""
        else:
            current = _read_artifact(self._directory_fd, "annotations.jsonl")
        if current != expected:
            raise BenchmarkError("annotation event history changed outside the review store")

    def _read_lock_first(self) -> dict | None:
        if _artifact_stat(self._directory_fd, "annotation-lock.json") is None:
            return None
        value = _parse_json_bytes(
            _read_artifact(self._directory_fd, "annotation-lock.json"),
            "annotation lock",
        )
        if not isinstance(value, dict) or set(value) != _LOCK_FIELDS:
            raise BenchmarkError("annotation lock has invalid fields")
        if type(value.get("schema_version")) is not int or value.get("schema_version") != SCHEMA_VERSION:
            raise BenchmarkError("annotation lock schema version mismatch")
        _require_reviewer_id(value.get("reviewer_id"))
        _require_utc_timestamp(value.get("locked_at"), "annotation lock locked_at")
        if _require_sha256(value.get("bundle_sha256"), "annotation lock bundle_sha256") != self._bundle_sha256:
            raise BenchmarkError("annotation lock bundle hash mismatch")
        for field, artifact_name, message in (
            ("annotations_sha256", "annotations.jsonl", "annotations hash mismatch"),
            ("state_sha256", "annotation-state.json", "state hash mismatch"),
        ):
            _require_sha256(value.get(field), f"annotation lock {field}")
            if _artifact_stat(self._directory_fd, artifact_name) is None:
                raise BenchmarkError(f"locked {artifact_name} is missing")
            if value.get(field) != _sha256(_read_artifact(self._directory_fd, artifact_name)):
                raise BenchmarkError(f"annotation lock {message}")
        return value

    def _validate_state_record(self, state: object) -> dict:
        fields = {
            "schema_version", "bundle_sha256", "total", "completed", "remaining",
            "last_sequence", "latest",
        }
        if not isinstance(state, dict) or set(state) != fields:
            raise BenchmarkError("annotation state has invalid fields")
        if type(state.get("schema_version")) is not int or state["schema_version"] != SCHEMA_VERSION:
            raise BenchmarkError("annotation state schema_version must be integer 1")
        bundle_digest = _require_sha256(
            state.get("bundle_sha256"), "annotation state bundle_sha256"
        )
        if bundle_digest != self._bundle_sha256:
            raise BenchmarkError("annotation state bundle hash mismatch")
        for field in ("total", "completed", "remaining", "last_sequence"):
            if type(state.get(field)) is not int or state[field] < 0:
                raise BenchmarkError(f"annotation state {field} must be a non-negative integer")
        latest = state.get("latest")
        if not isinstance(latest, dict):
            raise BenchmarkError("annotation state latest must be an object")
        if state["completed"] != len(latest) or state["completed"] + state["remaining"] != state["total"]:
            raise BenchmarkError("annotation state counts are inconsistent")
        if state["total"] != len(self._items):
            raise BenchmarkError("annotation state total does not match review bundle")
        if state["last_sequence"] > len(self._history):
            raise BenchmarkError("annotation state sequence is ahead of event history")
        for item_id, record in latest.items():
            _require_text(item_id, "annotation state item id", allow_empty=False)
            if not isinstance(record, dict) or set(record) != _STORED_FIELDS:
                raise BenchmarkError("annotation state latest record has invalid fields")
            if record.get("item_id") != item_id:
                raise BenchmarkError("annotation state latest item id mismatch")
            if type(record.get("sequence")) is not int or record["sequence"] < 1:
                raise BenchmarkError("annotation state latest sequence must be a positive integer")
            _require_utc_timestamp(record.get("saved_at"), "annotation state latest saved_at")
            validate_annotation(self.item(item_id), {
                key: record[key] for key in _ANNOTATION_FIELDS
            })
        prefix_latest: dict[str, dict] = {}
        for record in self._history[:state["last_sequence"]]:
            prefix_latest[record["item_id"]] = record
        expected_prefix = {
            item_id: prefix_latest[item_id] for item_id in sorted(prefix_latest)
        }
        if latest != expected_prefix:
            raise BenchmarkError("annotation state is not a valid event-history prefix")
        return state

    def _load_and_recover(self) -> None:
        existing_lock = self._read_lock_first()
        events_bytes = (
            _read_artifact(self._directory_fd, "annotations.jsonl")
            if _artifact_stat(self._directory_fd, "annotations.jsonl") is not None
            else b""
        )
        records = _parse_jsonl_bytes(events_bytes, "annotations")
        latest: dict[str, dict] = {}
        for sequence, record in enumerate(records, start=1):
            if set(record) != _STORED_FIELDS:
                raise BenchmarkError(f"annotation sequence {sequence} has invalid stored fields")
            if type(record.get("sequence")) is not int or record.get("sequence") != sequence:
                raise BenchmarkError(
                    f"annotation history expected sequence {sequence}, got {record.get('sequence')!r}"
                )
            _require_utc_timestamp(
                record.get("saved_at"), f"annotation sequence {sequence} saved_at"
            )
            item_id = record.get("item_id")
            review_item = self.item(item_id)
            annotation = {key: record[key] for key in _ANNOTATION_FIELDS}
            validate_annotation(review_item, annotation)
            expected_revision = 1 if item_id not in latest else latest[item_id]["revision"] + 1
            if record["revision"] != expected_revision:
                raise BenchmarkError(
                    f"annotation history expected revision {expected_revision} for {item_id}"
                )
            latest[item_id] = record
        self._history = records
        self.latest = latest
        expected_state = self.public_state()
        expected_state_bytes = canonical_bytes(expected_state)

        state_exists = _artifact_stat(self._directory_fd, "annotation-state.json") is not None
        if existing_lock is not None:
            if not state_exists:
                raise BenchmarkError("locked annotation-state.json is missing")
            state = _parse_json_bytes(
                _read_artifact(self._directory_fd, "annotation-state.json"),
                "annotation state",
            )
            self._validate_state_record(state)
            if state != expected_state:
                raise BenchmarkError("locked annotation state does not match event history")
            if len(self.latest) != len(self._items):
                raise BenchmarkError("annotation lock covers an incomplete queue")
            self.lock_record = existing_lock
            return

        if state_exists:
            state = _parse_json_bytes(
                _read_artifact(self._directory_fd, "annotation-state.json"),
                "annotation state",
            )
            self._validate_state_record(state)
        if not state_exists or state != expected_state:
            _replace_artifact(self._directory_fd, "annotation-state.json", expected_state_bytes)

    def public_state(self) -> dict:
        completed = len(self.latest)
        total = len(self._items)
        return {
            "schema_version": SCHEMA_VERSION,
            "bundle_sha256": self._bundle_sha256,
            "total": total,
            "completed": completed,
            "remaining": total - completed,
            "last_sequence": len(self._history),
            "latest": {item_id: self.latest[item_id] for item_id in sorted(self.latest)},
        }

    def api_state(self) -> dict:
        state = self.public_state()
        state["locked"] = self.locked
        return {"bundle": self.bundle, "state": state}

    def append(self, event: Mapping[str, object]) -> dict:
        if self.locked or _artifact_stat(self._directory_fd, "annotation-lock.json") is not None:
            raise BenchmarkError("annotations are locked")
        self._assert_bundle_unchanged()
        self._assert_history_unchanged()
        if not isinstance(event, Mapping):
            raise BenchmarkError("annotation must be an object")
        item_id = event.get("item_id")
        review_item = self.item(item_id)
        validate_annotation(review_item, event)
        current = self.latest.get(item_id)
        expected_revision = 1 if current is None else current["revision"] + 1
        if event["revision"] != expected_revision:
            raise BenchmarkError(f"expected revision {expected_revision}")
        stored = {
            **dict(event),
            "sequence": len(self._history) + 1,
            "saved_at": utc_now(),
        }
        _append_artifact(
            self._directory_fd, "annotations.jsonl", canonical_bytes(stored),
        )
        self._history.append(stored)
        self.latest[item_id] = stored
        _replace_artifact(
            self._directory_fd, "annotation-state.json", canonical_bytes(self.public_state()),
        )
        return dict(stored)

    def lock(self, *, reviewer_id: str) -> dict:
        if self.locked or _artifact_stat(self._directory_fd, "annotation-lock.json") is not None:
            raise BenchmarkError("annotations are already locked")
        self._assert_bundle_unchanged()
        self._assert_history_unchanged()
        reviewer_id = _require_reviewer_id(reviewer_id)
        remaining = len(self._items) - len(self.latest)
        if remaining:
            noun = "presentation remains" if remaining == 1 else "presentations remain"
            raise BenchmarkError(f"{remaining} {noun}")
        state_bytes = canonical_bytes(self.public_state())
        _replace_artifact(self._directory_fd, "annotation-state.json", state_bytes)
        events_bytes = _read_artifact(self._directory_fd, "annotations.jsonl")
        lock = {
            "schema_version": SCHEMA_VERSION,
            "reviewer_id": reviewer_id,
            "locked_at": utc_now(),
            "bundle_sha256": self._bundle_sha256,
            "annotations_sha256": _sha256(events_bytes),
            "state_sha256": _sha256(state_bytes),
        }
        _exclusive_artifact(
            self._directory_fd, "annotation-lock.json", canonical_bytes(lock),
        )
        verified = self._read_lock_first()
        if verified != lock:
            raise BenchmarkError("annotation lock verification failed")
        self.lock_record = lock
        return dict(lock)


def _read_static(name: str) -> bytes:
    path = UI_DIRECTORY / name
    try:
        if path.is_symlink():
            raise BenchmarkError("review UI asset may not be a symlink")
        resolved = path.resolve(strict=True)
        if resolved.parent != UI_DIRECTORY.resolve(strict=True):
            raise BenchmarkError("review UI asset is outside the static directory")
        descriptor = os.open(
            resolved, os.O_RDONLY | os.O_NONBLOCK | _no_follow_flag(),
        )
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise BenchmarkError("review UI asset is not a regular file")
            return source.read()
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError(f"cannot read review UI asset {name}: {error}") from error


class _ReviewHandler(BaseHTTPRequestHandler):
    review_store: ReviewStore
    server_version = "BenchmarkReview/1"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        return

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        self._respond(status, canonical_bytes(value), "application/json; charset=utf-8")

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _host_value(self) -> str | None:
        values = self.headers.get_all("Host", failobj=[])
        if len(values) != 1:
            return None
        host_value = values[0]
        matched = _HOST.fullmatch(host_value)
        if matched is None:
            return None
        port_text = matched.group(2)
        if int(port_text) != self.server.server_address[1]:
            return None
        return host_value

    def _valid_host(self) -> bool:
        return self._host_value() is not None

    def _origin_matches(self) -> bool:
        origins = self.headers.get_all("Origin", failobj=[])
        host = self._host_value()
        if len(origins) != 1 or host is None:
            return False
        origin = origins[0]
        if origin != f"http://{host}":
            return False
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "http"
            and parsed.netloc == host
            and parsed.path == ""
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.username is None
            and parsed.password is None
        )

    def _route_path(self) -> tuple[str | None, bool]:
        if not self.path.startswith("/") or self.path.startswith("//"):
            return None, False
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc:
            return None, False
        if parsed.query or parsed.fragment:
            return None, True
        return parsed.path, True

    def do_GET(self) -> None:
        if not self._valid_host():
            self._error(403, "invalid_host", "request host is not this loopback review server")
            return
        path, origin_form = self._route_path()
        if not origin_form:
            self._error(400, "invalid_target", "request target must use origin-form")
            return
        if path == "/api/state":
            self._json(200, self.review_store.api_state())
            return
        static = _STATIC.get(path) if path is not None else None
        if static is None:
            self._error(404, "not_found", "resource not found")
            return
        name, content_type = static
        try:
            body = _read_static(name)
        except BenchmarkError as error:
            self._error(500, "static_error", str(error))
            return
        self._respond(200, body, content_type)

    def _read_json_body(self) -> object | None:
        if self.headers.get_all("Transfer-Encoding", failobj=[]):
            self._error(400, "invalid_headers", "Transfer-Encoding is not accepted")
            return None
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if not lengths:
            self._error(411, "length_required", "one Content-Length header is required")
            return None
        if len(lengths) != 1:
            self._error(400, "invalid_headers", "duplicate Content-Length headers are not accepted")
            return None
        if _ASCII_LENGTH.fullmatch(lengths[0]) is None:
            self._error(400, "invalid_body", "Content-Length must contain ASCII digits only")
            return None
        normalized_length = lengths[0].lstrip("0") or "0"
        if len(normalized_length) > len(str(MAX_BODY_BYTES)):
            self.close_connection = True
            self._error(413, "body_too_large", "request body exceeds 1 MiB")
            return None
        length = int(normalized_length, 10)
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            self._error(413, "body_too_large", "request body exceeds 1 MiB")
            return None
        content_types = self.headers.get_all("Content-Type", failobj=[])
        if len(content_types) != 1:
            self._error(400, "invalid_headers", "one Content-Type header is required")
            return None
        content_type = content_types[0].split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(415, "unsupported_media_type", "Content-Type must be application/json")
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            self._error(400, "invalid_body", "request body ended early")
            return None
        try:
            return _decode_json_bytes(body, "request JSON")
        except BenchmarkError:
            self._error(400, "invalid_json", "request body must be valid UTF-8 JSON")
            return None

    def do_POST(self) -> None:
        if not self._valid_host():
            self._error(403, "invalid_host", "request host is not this loopback review server")
            return
        path, origin_form = self._route_path()
        if not origin_form:
            self._error(400, "invalid_target", "request target must use origin-form")
            return
        if path not in {"/api/annotations", "/api/lock"}:
            self._error(404, "not_found", "resource not found")
            return
        if not self._origin_matches():
            self._error(403, "origin_mismatch", "request origin does not match this review server")
            return
        payload = self._read_json_body()
        if payload is None:
            return
        if not isinstance(payload, Mapping):
            self._error(400, "invalid_request", "request JSON must be an object")
            return
        try:
            if path == "/api/annotations":
                stored = self.review_store.append(payload)
                self._json(201, {"annotation": stored, "state": self.review_store.api_state()["state"]})
            else:
                if set(payload) != {"reviewer_id"}:
                    raise BenchmarkError("lock request must contain only reviewer_id")
                lock = self.review_store.lock(reviewer_id=payload["reviewer_id"])
                self._json(201, {"lock": lock, "state": self.review_store.api_state()["state"]})
        except BenchmarkError as error:
            self._error(400, "validation_error", str(error))

    def do_PUT(self) -> None:
        self._error(405, "method_not_allowed", "method not allowed")

    do_PATCH = do_PUT
    do_DELETE = do_PUT
    do_OPTIONS = do_PUT
    do_TRACE = do_PUT
    do_CONNECT = do_PUT

    def __getattr__(self, name: str):
        if name.startswith("do_"):
            return self.do_PUT
        raise AttributeError(name)

    def do_HEAD(self) -> None:
        body = canonical_bytes({
            "error": {"code": "method_not_allowed", "message": "method not allowed"},
        })
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


def create_server(
    store: ReviewStore, *, host: str = "127.0.0.1", port: int = 8765,
) -> HTTPServer:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise BenchmarkError("review server host must be a numeric loopback address") from error
    if not address.is_loopback or address.version != 4:
        raise BenchmarkError("review server host must be an IPv4 loopback address")
    if type(port) is not int or not 0 <= port <= 65535:
        raise BenchmarkError("review server port must be between 0 and 65535")

    class Handler(_ReviewHandler):
        review_store = store

    try:
        return HTTPServer((host, port), Handler)
    except OSError as error:
        raise BenchmarkError(f"cannot start review server: {error}") from error


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the offline benchmark reviewer")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--annotations-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        store = ReviewStore(args.bundle, args.annotations_dir)
        server = create_server(store, host=args.host, port=args.port)
    except BenchmarkError as error:
        raise SystemExit(f"review app: {error}") from error
    host, port = server.server_address
    print(f"Offline benchmark reviewer: http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
