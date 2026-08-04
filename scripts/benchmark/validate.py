from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from .common import BenchmarkError, canonical_bytes, read_json, read_jsonl, sha256_bytes
from .schema import SCHEMA_VERSION


_SEVERITIES = {"critical", "major", "minor", "neutral"}
_PLACEHOLDER = re.compile(r"\{\{\s*[A-Za-z_][\w.-]*\s*\}\}|\{\s*[A-Za-z_][\w.-]*\s*\}")
_FORMAT_SPECIFIER = re.compile(
    r"%%|%(?:\d+\$)?[-+0 #']*(?:\d+|\*)?(?:\.(?:\d+|\*))?"
    r"(?:hh|h|ll|l|L|z|j|t|q)?[diuoxXfFeEgGaAcsp@]"
)
_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
_CODE_SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_NUMBER = re.compile(
    r"(?<![\w])[-+]?(?:\d{1,3}(?:[.,\s]\d{3})+|\d+)(?:[.,]\d+)?(?:\s?%)?(?![\w])"
)
_IDENTIFIER = re.compile(
    r"(?<![\w])(?:[A-Za-z]+(?:[A-Z][A-Za-z0-9]*)+|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|"
    r"[A-Z][A-Z0-9_]{1,}|[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_]+)+)(?![\w])"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Finding:
    invariant: str
    severity: str
    expected: object
    observed: object
    affected_span: tuple[int, int] | None
    message: str


@dataclass(frozen=True)
class ValidationResult:
    case_id: str
    output: str
    status: str
    findings: tuple[Finding, ...]
    validator_errors: tuple[str, ...]
    applicable_checks: int
    passed_checks: int
    failed_checks: int
    validator_error_checks: int
    run_id: str | None = None

    def to_record(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "status": self.status,
            "output": self.output,
            "findings": [asdict(finding) for finding in self.findings],
            "validator_errors": list(self.validator_errors),
            "applicable_checks": self.applicable_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "validator_error_checks": self.validator_error_checks,
        }


class _CandidateStructureError(ValueError):
    pass


Check = Callable[[Mapping[str, object], str, Mapping[str, object]], tuple[Finding, ...]]


def _severity(check: Mapping[str, object]) -> str:
    value = check.get("severity")
    if not isinstance(value, str) or value not in _SEVERITIES:
        raise ValueError(f"invalid check severity: {value!r}")
    return value


def _source(case: Mapping[str, object]) -> str:
    value = case.get("source")
    if not isinstance(value, str):
        raise ValueError("case source must be text")
    return value


def _counter_payload(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _first_unexpected_span(output: str, expected: Counter[str], observed: Counter[str]) -> tuple[int, int] | None:
    for value in sorted(observed):
        if observed[value] > expected[value]:
            start = output.find(value)
            if start >= 0:
                return start, start + len(value)
    return None


def _multiset_finding(
    invariant: str,
    severity: str,
    source_values: Sequence[str],
    output_values: Sequence[str],
    output: str,
) -> tuple[Finding, ...]:
    expected = Counter(source_values)
    observed = Counter(output_values)
    if expected == observed:
        return ()
    return (Finding(
        invariant,
        severity,
        _counter_payload(expected),
        _counter_payload(observed),
        _first_unexpected_span(output, expected, observed),
        f"{invariant} differs from the declared source invariant",
    ),)


def _trim_url(value: str) -> str:
    while value and value[-1] in ".,;:!?":
        value = value[:-1]
    while value.endswith(")") and value.count("(") < value.count(")"):
        value = value[:-1]
    while value.endswith("]") and value.count("[") < value.count("]"):
        value = value[:-1]
    return value


def _configured_values(
    check: Mapping[str, object],
    *,
    keys: Sequence[str],
) -> tuple[str, ...] | None:
    value: object = None
    for key in keys:
        if key in check:
            value = check[key]
            break
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{check.get('type')} values must be a list of non-empty strings")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{check.get('type')} values must be a list of non-empty strings")
    return tuple(value)


def _literal_occurrences(text: str, values: Sequence[str]) -> list[str]:
    matches: list[tuple[int, str]] = []
    for value in values:
        matches.extend((match.start(), value) for match in re.finditer(re.escape(value), text))
    return [value for _, value in sorted(matches)]


def check_placeholders(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    return _multiset_finding(
        "placeholder_multiset", _severity(check),
        _PLACEHOLDER.findall(_source(case)), _PLACEHOLDER.findall(output), output,
    )


def check_format_specifiers(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    return _multiset_finding(
        "format_specifier_multiset", _severity(check),
        _FORMAT_SPECIFIER.findall(_source(case)), _FORMAT_SPECIFIER.findall(output), output,
    )


def check_urls(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    extract = lambda text: [_trim_url(match.group(0)) for match in _URL.finditer(text)]
    return _multiset_finding("url_multiset", _severity(check), extract(_source(case)), extract(output), output)


def check_emails(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    extract = lambda text: [match.group(0) for match in _EMAIL.finditer(text)]
    return _multiset_finding("email_multiset", _severity(check), extract(_source(case)), extract(output), output)


def check_code_spans(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    extract = lambda text: [match.group(1) for match in _CODE_SPAN.finditer(text)]
    return _multiset_finding("code_span_multiset", _severity(check), extract(_source(case)), extract(output), output)


def check_commands(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    values = _configured_values(check, keys=("values", "commands"))
    if values is None:
        extract = lambda text: [match.group(1) for match in _CODE_SPAN.finditer(text)]
        expected_values = extract(_source(case))
        observed_values = extract(output)
    else:
        expected_values = _literal_occurrences(_source(case), values)
        observed_values = _literal_occurrences(output, values)
    return _multiset_finding(
        "command_multiset", _severity(check), expected_values, observed_values, output,
    )


def check_identifiers(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    values = _configured_values(check, keys=("values", "identifiers"))
    if values is None:
        extract = lambda text: [match.group(0) for match in _IDENTIFIER.finditer(text)]
        expected_values = extract(_source(case))
        observed_values = extract(output)
    else:
        expected_values = _literal_occurrences(_source(case), values)
        observed_values = _literal_occurrences(output, values)
    return _multiset_finding(
        "identifier_multiset", _severity(check), expected_values, observed_values, output,
    )


def check_protected_terms(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    values = _configured_values(check, keys=("values", "terms"))
    if values is None:
        protected = case.get("protected_terms", ())
        if isinstance(protected, str) or not isinstance(protected, Sequence):
            raise ValueError("case protected_terms must be a list of non-empty strings")
        if not all(isinstance(item, str) and item for item in protected):
            raise ValueError("case protected_terms must be a list of non-empty strings")
        values = tuple(protected)
    return _multiset_finding(
        "protected_term_multiset",
        _severity(check),
        _literal_occurrences(_source(case), values),
        _literal_occurrences(output, values),
        output,
    )


def check_numbers(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    extract = lambda text: [_normalize_number(match.group(0)) for match in _NUMBER.finditer(text)]
    return _multiset_finding("number_multiset", _severity(check), extract(_source(case)), extract(output), output)


def _normalize_number(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    suffix = "%" if compact.endswith("%") else ""
    if suffix:
        compact = compact[:-1]
    sign = ""
    if compact[:1] in {"+", "-"}:
        sign, compact = compact[0], compact[1:]
    separators = [separator for separator in (",", ".") if separator in compact]
    if len(separators) == 2:
        decimal_separator = max(separators, key=compact.rfind)
        grouping_separator = "," if decimal_separator == "." else "."
        compact = compact.replace(grouping_separator, "").replace(decimal_separator, ".")
    elif separators:
        separator = separators[0]
        parts = compact.split(separator)
        grouping = len(parts) > 2 and all(len(part) == 3 for part in parts[1:])
        grouping = grouping or (
            len(parts) == 2 and len(parts[1]) == 3 and 1 <= len(parts[0]) <= 3
        )
        compact = "".join(parts) if grouping else compact.replace(separator, ".")
    try:
        normalized = format(Decimal(f"{sign}{compact}").normalize(), "f")
    except InvalidOperation:
        return f"{sign}{compact}{suffix}"
    if normalized == "-0":
        normalized = "0"
    return normalized + suffix


def check_character_limit(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    maximum = check.get("max")
    if maximum is None:
        constraints = case.get("constraints", {})
        if isinstance(constraints, Mapping):
            maximum = constraints.get("character_limit")
    if type(maximum) is not int or maximum < 0:
        raise ValueError("character_limit requires a non-negative integer max")
    if len(output) <= maximum:
        return ()
    return (Finding(
        "character_limit", _severity(check), {"max": maximum}, {"length": len(output)},
        (maximum, len(output)), f"output exceeds the {maximum}-character limit",
    ),)


def check_line_count(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    expected = check.get("count", check.get("lines"))
    if expected is None:
        expected = len(_source(case).splitlines())
    if type(expected) is not int or expected < 0:
        raise ValueError("line_count requires a non-negative integer count")
    observed = len(output.splitlines())
    if observed == expected:
        return ()
    return (Finding(
        "line_count", _severity(check), expected, observed, None,
        f"expected {expected} lines but observed {observed}",
    ),)


def _json_topology(value: object) -> object:
    if isinstance(value, dict):
        return {key: _json_topology(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_json_topology(item) for item in value]
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) in (int, float):
        return "number"
    if isinstance(value, str):
        return "string"
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


def _parse_json_topology(text: str) -> object:
    try:
        return _json_topology(json.loads(text))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _CandidateStructureError(str(error)) from error


def _xml_element_topology(element: ElementTree.Element) -> object:
    return (
        element.tag,
        tuple(sorted(element.attrib)),
        tuple(_xml_element_topology(child) for child in element),
    )


def _parse_xml_topology(text: str) -> object:
    try:
        return _xml_element_topology(ElementTree.fromstring(text))
    except ElementTree.ParseError as error:
        raise _CandidateStructureError(str(error)) from error


_VOID_HTML_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _TopologyHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: list[object] = []
        self.stack: list[tuple[str, list[object]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        children: list[object] = []
        node = (tag, tuple(sorted(name for name, _ in attrs)), children)
        (self.stack[-1][1] if self.stack else self.root).append(node)
        if tag not in _VOID_HTML_TAGS:
            self.stack.append((tag, children))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = (tag, tuple(sorted(name for name, _ in attrs)), [])
        (self.stack[-1][1] if self.stack else self.root).append(node)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1][0] != tag:
            raise _CandidateStructureError(f"unexpected closing HTML tag: {tag}")
        self.stack.pop()

    def topology(self) -> object:
        if self.stack:
            raise _CandidateStructureError(f"unclosed HTML tag: {self.stack[-1][0]}")
        return _freeze_lists(self.root)


def _freeze_lists(value: object) -> object:
    if isinstance(value, list):
        return tuple(_freeze_lists(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_lists(item) for item in value)
    return value


def _parse_html_topology(text: str) -> object:
    parser = _TopologyHTMLParser()
    try:
        parser.feed(text)
        parser.close()
        return parser.topology()
    except _CandidateStructureError:
        raise
    except Exception as error:
        raise _CandidateStructureError(str(error)) from error


_MARKDOWN_LINK = re.compile(r"(!?)\[[^\]\n]*\]\(([^\s)]+)(?:\s+[^)]*)?\)")
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+")
_MARKDOWN_LIST = re.compile(r"^(\s*)([-+*]|\d+[.)])\s+")
_MARKDOWN_QUOTE = re.compile(r"^(\s*(?:>\s*)+)")
_MARKDOWN_FENCE = re.compile(r"^\s*(`{3,}|~{3,})([^`]*)$")


def _parse_markdown_topology(text: str) -> object:
    headings: list[int] = []
    lists: list[tuple[int, str]] = []
    quotes: list[int] = []
    fences: list[str] = []
    open_fence: tuple[str, int] | None = None
    for line in text.splitlines():
        fence = _MARKDOWN_FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if open_fence is None:
                open_fence = (marker[0], len(marker))
                fences.append(fence.group(2).strip())
            elif marker[0] == open_fence[0] and len(marker) >= open_fence[1]:
                open_fence = None
            continue
        if open_fence is not None:
            continue
        heading = _MARKDOWN_HEADING.match(line)
        if heading:
            headings.append(len(heading.group(1)))
        item = _MARKDOWN_LIST.match(line)
        if item:
            lists.append((len(item.group(1).expandtabs(4)), "ordered" if item.group(2)[0].isdigit() else "unordered"))
        quote = _MARKDOWN_QUOTE.match(line)
        if quote:
            quotes.append(quote.group(1).count(">"))
    if open_fence is not None:
        raise _CandidateStructureError("unclosed Markdown code fence")
    links = tuple(
        ("image" if match.group(1) else "link", match.group(2))
        for match in _MARKDOWN_LINK.finditer(text)
    )
    return {
        "headings": tuple(headings),
        "lists": tuple(lists),
        "quotes": tuple(quotes),
        "fences": tuple(fences),
        "links": links,
    }


def _csv_dialect(text: str, configured: object = None) -> str:
    if configured is not None:
        if not isinstance(configured, str) or len(configured) != 1:
            raise ValueError("csv_shape delimiter must be one character")
        return configured
    try:
        return csv.Sniffer().sniff(text, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _parse_csv_topology(text: str, delimiter: str) -> object:
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
    except csv.Error as error:
        raise _CandidateStructureError(str(error)) from error
    if not rows:
        raise _CandidateStructureError("CSV contains no rows")
    return delimiter, tuple(len(row) for row in rows)


class _ICUParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0

    def parse(self) -> object:
        topology = self._message(stop=False)
        if self.position != len(self.text):
            raise _CandidateStructureError("unexpected ICU parser remainder")
        return topology

    def _message(self, *, stop: bool) -> tuple[object, ...]:
        arguments: list[object] = []
        while self.position < len(self.text):
            character = self.text[self.position]
            if character == "'":
                self._quoted_text()
            elif character == "{":
                arguments.append(self._argument())
            elif character == "}":
                if not stop:
                    raise _CandidateStructureError("unexpected ICU closing brace")
                self.position += 1
                return tuple(sorted(arguments, key=repr))
            else:
                self.position += 1
        if stop:
            raise _CandidateStructureError("unclosed ICU argument")
        return tuple(sorted(arguments, key=repr))

    def _quoted_text(self) -> None:
        self.position += 1
        if self.position >= len(self.text):
            return
        if self.text[self.position] == "'":
            self.position += 1
            return
        if self.text[self.position] not in "{}#":
            return
        while self.position < len(self.text):
            if self.text[self.position] == "'":
                self.position += 1
                if self.position < len(self.text) and self.text[self.position] == "'":
                    self.position += 1
                    continue
                return
            self.position += 1

    def _argument(self) -> object:
        self.position += 1
        name = self._token({",", "}"})
        if not name:
            raise _CandidateStructureError("ICU argument name is empty")
        delimiter = self._current()
        if delimiter == "}":
            self.position += 1
            return name, "argument", (), ()
        self.position += 1
        formatter = self._token({",", "}"}).lower()
        if not formatter:
            raise _CandidateStructureError(f"ICU argument {name} has no formatter")
        delimiter = self._current()
        if delimiter == "}":
            self.position += 1
            return name, formatter, (), ()
        self.position += 1
        if formatter in {"plural", "selectordinal", "select"}:
            return self._select_argument(name, formatter)
        self._consume_formatter_style()
        return name, formatter, (), ()

    def _select_argument(self, name: str, formatter: str) -> object:
        branches: dict[str, object] = {}
        offset: str | None = None
        while True:
            self._skip_space()
            if self._current() == "}":
                self.position += 1
                if not branches:
                    raise _CandidateStructureError(f"ICU {formatter} argument has no selectors")
                return name, formatter, (() if offset is None else (("offset", offset),)), tuple(sorted(branches.items()))
            selector = self._token({"{", "}", " "})
            if selector.startswith("offset:"):
                if formatter == "select" or offset is not None:
                    raise _CandidateStructureError("invalid ICU plural offset")
                offset = selector.split(":", 1)[1]
                if not offset.isdigit():
                    raise _CandidateStructureError("invalid ICU plural offset")
                continue
            self._skip_space()
            if not selector or self._current() != "{":
                raise _CandidateStructureError(f"invalid ICU selector in {name}")
            if selector in branches:
                raise _CandidateStructureError(f"duplicate ICU selector: {selector}")
            self.position += 1
            branches[selector] = self._message(stop=True)

    def _consume_formatter_style(self) -> None:
        depth = 0
        while self.position < len(self.text):
            character = self.text[self.position]
            if character == "'":
                self._quoted_text()
            elif character == "{":
                depth += 1
                self.position += 1
            elif character == "}":
                if depth == 0:
                    self.position += 1
                    return
                depth -= 1
                self.position += 1
            else:
                self.position += 1
        raise _CandidateStructureError("unclosed ICU formatter")

    def _token(self, delimiters: set[str]) -> str:
        self._skip_space()
        start = self.position
        while self.position < len(self.text) and self.text[self.position] not in delimiters:
            self.position += 1
        return self.text[start:self.position].strip()

    def _skip_space(self) -> None:
        while self.position < len(self.text) and self.text[self.position].isspace():
            self.position += 1

    def _current(self) -> str:
        if self.position >= len(self.text):
            raise _CandidateStructureError("unexpected end of ICU message")
        return self.text[self.position]


def _structured_check(
    invariant: str,
    case: Mapping[str, object],
    output: str,
    check: Mapping[str, object],
    parser: Callable[[str], object],
) -> tuple[Finding, ...]:
    try:
        expected = parser(_source(case))
    except _CandidateStructureError as error:
        raise ValueError(f"invalid source {invariant}: {error}") from error
    try:
        observed = parser(output)
    except _CandidateStructureError as error:
        return (Finding(
            invariant, _severity(check), expected, {"parse_error": str(error)}, None,
            f"candidate is not valid for {invariant}",
        ),)
    if expected == observed:
        return ()
    return (Finding(
        invariant, _severity(check), expected, observed, None,
        f"candidate changes the declared {invariant} topology",
    ),)


def check_json_structure(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    return _structured_check("json_structure", case, output, check, _parse_json_topology)


def check_xml_structure(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    return _structured_check("xml_structure", case, output, check, _parse_xml_topology)


def check_html_structure(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    return _structured_check("html_structure", case, output, check, _parse_html_topology)


def check_markdown_structure(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    return _structured_check("markdown_structure", case, output, check, _parse_markdown_topology)


def check_csv_shape(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    delimiter = _csv_dialect(_source(case), check.get("delimiter"))
    parser = lambda text: _parse_csv_topology(text, delimiter)
    return _structured_check("csv_shape", case, output, check, parser)


def check_icu_topology(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    return _structured_check("icu_topology", case, output, check, lambda text: _ICUParser(text).parse())


def check_forbidden_locale_form(case: Mapping[str, object], output: str, check: Mapping[str, object]) -> tuple[Finding, ...]:
    configured = check.get("forms", check.get("forbidden"))
    if isinstance(configured, Mapping):
        forms = tuple(configured)
    elif isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)):
        forms = tuple(configured)
    else:
        raise ValueError("forbidden_locale_form requires forms")
    if not all(isinstance(form, str) and form for form in forms):
        raise ValueError("forbidden locale forms must be non-empty strings")
    flags = 0 if check.get("case_sensitive", False) else re.IGNORECASE
    observed: list[str] = []
    first_span: tuple[int, int] | None = None
    for form in forms:
        for match in re.finditer(rf"(?<!\w){re.escape(form)}(?!\w)", output, flags):
            observed.append(match.group(0))
            if first_span is None:
                first_span = match.span()
    if not observed:
        return ()
    return (Finding(
        "forbidden_locale_form", _severity(check), {"absent": sorted(forms)},
        {"present": observed}, first_span, "candidate contains a declared forbidden locale form",
    ),)


CHECKS: dict[str, Check] = {
    "placeholder_multiset": check_placeholders,
    "format_specifier_multiset": check_format_specifiers,
    "url_multiset": check_urls,
    "email_multiset": check_emails,
    "code_span_multiset": check_code_spans,
    "command_multiset": check_commands,
    "identifier_multiset": check_identifiers,
    "protected_term_multiset": check_protected_terms,
    "number_multiset": check_numbers,
    "character_limit": check_character_limit,
    "line_count": check_line_count,
    "json_structure": check_json_structure,
    "xml_structure": check_xml_structure,
    "html_structure": check_html_structure,
    "markdown_structure": check_markdown_structure,
    "csv_shape": check_csv_shape,
    "icu_topology": check_icu_topology,
    "forbidden_locale_form": check_forbidden_locale_form,
}


def validate_output(case: Mapping[str, object], output: str) -> ValidationResult:
    if not isinstance(case, Mapping):
        raise BenchmarkError("case must be an object")
    if not isinstance(output, str):
        raise BenchmarkError("candidate output must be text")
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise BenchmarkError("case id must be non-empty text")
    checks = case.get("automatic_checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise BenchmarkError(f"case {case_id} automatic_checks must be a list")
    findings: list[Finding] = []
    errors: list[str] = []
    passed = failed = validator_errors = 0
    for index, check in enumerate(checks):
        if not isinstance(check, Mapping):
            errors.append(f"check[{index}]: TypeError: check declaration must be an object")
            validator_errors += 1
            continue
        check_type = check.get("type")
        try:
            if not isinstance(check_type, str) or check_type not in CHECKS:
                raise ValueError(f"unknown automatic check: {check_type!r}")
            check_findings = CHECKS[check_type](case, output, check)
            if not isinstance(check_findings, tuple) or not all(
                isinstance(finding, Finding) for finding in check_findings
            ):
                raise TypeError("validator returned an invalid finding collection")
        except Exception as error:
            errors.append(f"{check_type or f'check[{index}]'}: {type(error).__name__}: {error}")
            validator_errors += 1
            continue
        findings.extend(check_findings)
        if check_findings:
            failed += 1
        else:
            passed += 1
    status = "validator_error" if errors else ("failed" if findings else "passed")
    return ValidationResult(
        case_id=case_id,
        output=output,
        status=status,
        findings=tuple(findings),
        validator_errors=tuple(errors),
        applicable_checks=len(checks),
        passed_checks=passed,
        failed_checks=failed,
        validator_error_checks=validator_errors,
    )


def _manifest_run_ids(evidence_dir: Path) -> list[str]:
    manifest = read_json(evidence_dir / "run-manifest.json")
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("run manifest schema version mismatch")
    schedule = manifest.get("schedule")
    if not isinstance(schedule, Mapping):
        raise BenchmarkError("run manifest has no schedule")
    run_ids = schedule.get("run_ids")
    if (
        not isinstance(run_ids, list)
        or not run_ids
        or not all(isinstance(run_id, str) and run_id for run_id in run_ids)
    ):
        raise BenchmarkError("run manifest schedule has invalid run ids")
    if len(run_ids) != len(set(run_ids)):
        raise BenchmarkError("run manifest schedule has duplicate run ids")
    digest = schedule.get("sha256")
    expected_digest = sha256_bytes(canonical_bytes(run_ids))
    if digest is not None and digest != expected_digest:
        raise BenchmarkError("run manifest schedule hash mismatch")
    return run_ids


def _cases_by_id(dataset_dir: Path) -> dict[str, dict]:
    records = read_jsonl(dataset_dir / "cases.jsonl")
    result: dict[str, dict] = {}
    for record in records:
        case_id = record.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise BenchmarkError("dataset case id must be non-empty text")
        if case_id in result:
            raise BenchmarkError(f"duplicate dataset case id: {case_id}")
        result[case_id] = record
    return result


def _runs_by_id(evidence_dir: Path, expected_ids: Sequence[str]) -> dict[str, dict]:
    records = read_jsonl(evidence_dir / "runs.jsonl")
    result: dict[str, dict] = {}
    for record in records:
        run_id = record.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise BenchmarkError("run record id must be non-empty text")
        if run_id in result:
            raise BenchmarkError(f"duplicate run id: {run_id}")
        result[run_id] = record
    expected = set(expected_ids)
    actual = set(result)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise BenchmarkError(
            f"incomplete run set: missing={missing!r}, unknown={unknown!r}"
        )
    return result


def _raw_output(evidence_dir: Path, record: Mapping[str, object]) -> str:
    relative = record.get("raw_output_path")
    digest = record.get("output_sha256")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise BenchmarkError("run record has invalid raw output path")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise BenchmarkError("run record has invalid output hash")
    raw_directory = evidence_dir / "raw"
    if raw_directory.is_symlink():
        raise BenchmarkError("refusing symlink raw evidence directory")
    raw_root = raw_directory.resolve()
    path = evidence_dir / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(raw_root)
    except (OSError, ValueError) as error:
        raise BenchmarkError(f"raw output path escapes raw evidence: {relative}") from error
    if path.is_symlink():
        raise BenchmarkError(f"refusing symlink raw output: {relative}")
    try:
        encoded = path.read_bytes()
    except OSError as error:
        raise BenchmarkError(f"cannot read raw output {relative}: {error}") from error
    if sha256_bytes(encoded) != digest:
        raise BenchmarkError(f"raw output hash mismatch: {relative}")
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BenchmarkError(f"raw output is not UTF-8: {relative}") from error


def _refuse_raw_destination(evidence_dir: Path, destination: Path) -> None:
    raw_root = (evidence_dir / "raw").resolve()
    try:
        destination.resolve().relative_to(raw_root)
    except ValueError:
        return
    raise BenchmarkError("validation output must not be written inside raw evidence")


def _atomic_write_jsonl(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    path = Path(path)
    if path.is_symlink():
        raise BenchmarkError(f"refusing symlink output path: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise BenchmarkError(f"cannot create validation output directory: {error}") from error
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            for record in records:
                target.write(canonical_bytes(record))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise BenchmarkError(f"cannot atomically write validation JSONL {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validate_runs(
    dataset_dir: Path,
    evidence_dir: Path,
    output_path: Path | None = None,
) -> list[ValidationResult]:
    dataset_dir = Path(dataset_dir)
    evidence_dir = Path(evidence_dir)
    destination = Path(output_path) if output_path is not None else evidence_dir / "validation.jsonl"
    if evidence_dir.is_symlink():
        raise BenchmarkError(f"refusing symlink evidence directory: {evidence_dir}")
    _refuse_raw_destination(evidence_dir, destination)
    run_ids = _manifest_run_ids(evidence_dir)
    cases = _cases_by_id(dataset_dir)
    runs = _runs_by_id(evidence_dir, run_ids)
    results: list[ValidationResult] = []
    for run_id in run_ids:
        run = runs[run_id]
        case_id = run.get("case_id")
        if not isinstance(case_id, str) or case_id not in cases:
            raise BenchmarkError(f"run {run_id} has unknown case id: {case_id!r}")
        output = _raw_output(evidence_dir, run)
        results.append(replace(validate_output(cases[case_id], output), run_id=run_id))
    _atomic_write_jsonl(destination, [result.to_record() for result in results])
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate PT-PT benchmark product integrity.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        results = validate_runs(arguments.dataset, arguments.evidence, arguments.output)
        sys.stdout.buffer.write(canonical_bytes({"runs": len(results), "output": str(arguments.output)}))
        return 0
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
