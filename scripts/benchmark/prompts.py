from __future__ import annotations

import json
from collections.abc import Mapping

from .common import BenchmarkError
from .schema import CONDITIONS


HIDDEN_CASE_FIELDS = {
    "reference",
    "reference_notes",
    "seeded_errors",
    "automatic_findings",
    "automatic_checks",
}
_DATA_BLOCK_FIELDS = {"source", "candidate"}
_COMPACT_CONTEXT_FIELDS = {"audience", "glossary", "protected_terms", "style"}
_BASE_TEMPLATE_BY_TASK = {
    "translation": "normal-translation",
    "review": "normal-review",
}


def canonical_display(value: object) -> str:
    """Return a stable display form for structured, non-template prompt values."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def visible_case(case: dict) -> dict:
    """Return case metadata that is safe to expose outside delimited data blocks."""
    return {
        field: value
        for field, value in case.items()
        if field not in HIDDEN_CASE_FIELDS | _DATA_BLOCK_FIELDS
    }


def assert_no_hidden_fields(text: str, case: dict) -> None:
    """Reject prompts containing serialized hidden case values."""
    for field in HIDDEN_CASE_FIELDS:
        if field not in case:
            continue
        value = case[field]
        if value is None or value == "" or value == [] or value == {}:
            continue
        rendered = canonical_display(value)
        if rendered and rendered in text:
            raise BenchmarkError(f"prompt contains hidden case field: {field}")
        if isinstance(value, str) and value and value in text:
            raise BenchmarkError(f"prompt contains hidden case field: {field}")


def _require_template(templates: Mapping[str, str], name: str) -> str:
    try:
        template = templates[name]
    except KeyError as error:
        raise BenchmarkError(f"missing prompt template: {name}") from error
    if not isinstance(template, str):
        raise BenchmarkError(f"prompt template {name} must be text")
    return template


def _base_template_name(case: Mapping[str, object]) -> str:
    task = case.get("task")
    try:
        return _BASE_TEMPLATE_BY_TASK[task]
    except KeyError as error:
        raise BenchmarkError(f"unsupported benchmark task: {task!r}") from error


def _base_instruction(case: Mapping[str, object], templates: Mapping[str, str]) -> str:
    source_locale = case.get("source_locale")
    if not isinstance(source_locale, str) or not source_locale:
        raise BenchmarkError("case source_locale must be non-empty text")
    template = _require_template(templates, _base_template_name(case))
    return template.replace("{source_locale}", source_locale).rstrip()


def _validate_condition(case: Mapping[str, object], condition: str) -> None:
    if condition not in CONDITIONS:
        raise BenchmarkError(f"unsupported benchmark condition: {condition!r}")
    if condition == "context_only" and case.get("diagnostic") is not True:
        raise BenchmarkError("context_only condition is not diagnostic")


def _validated_compact_context(compact_context: object) -> dict:
    if not isinstance(compact_context, Mapping):
        raise BenchmarkError("compact context must be an object")
    unknown = set(compact_context) - _COMPACT_CONTEXT_FIELDS
    if unknown:
        raise BenchmarkError(f"compact context has unapproved fields: {sorted(unknown)!r}")
    return dict(compact_context)


def _data_block(name: str, value: str) -> list[str]:
    encoded = canonical_display(value).replace(f"</{name}>", f"<\\/{name}>")
    return [f'<{name} encoding="json-string">', encoded, f"</{name}>"]


def render_prompt(
    case: dict,
    condition: str,
    templates: Mapping[str, str],
    compact_context: dict,
) -> str:
    """Render a condition-bounded benchmark prompt from trusted templates and literal data."""
    _validate_condition(case, condition)
    source = case.get("source")
    if not isinstance(source, str):
        raise BenchmarkError("case source must be text")

    sections: list[str] = []
    if condition == "suite":
        sections.append(_require_template(templates, "treatment").rstrip())
    elif condition == "context_only":
        sections.append(_require_template(templates, "context-only").rstrip())
    sections.append(_base_instruction(case, templates))
    if condition == "context_only":
        sections.extend(
            [
                "<project-context>",
                canonical_display(_validated_compact_context(compact_context)),
                "</project-context>",
            ]
        )
    sections.extend(
        [
            "<task-context>",
            canonical_display(visible_case(case)),
            "</task-context>",
        ]
    )
    assert_no_hidden_fields("\n".join(sections), case)
    sections.extend(_data_block("source-data", source))
    if case.get("task") == "review":
        candidate = case.get("candidate")
        if not isinstance(candidate, str):
            raise BenchmarkError("review case candidate must be text")
        sections.extend(_data_block("candidate-data", candidate))
    return "\n".join(sections) + "\n"
