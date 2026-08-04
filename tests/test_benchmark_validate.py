from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from scripts.benchmark.common import BenchmarkError, canonical_bytes, sha256_bytes
from scripts.benchmark.run import RunResult
from scripts.benchmark.validate import CHECKS, validate_output, validate_runs


def case_with_checks(*, source: str, checks: list[dict], **overrides: object) -> dict:
    case = {
        "id": "case-1",
        "source": source,
        "automatic_checks": checks,
        "protected_terms": [],
        "source_locale": "en-GB",
        "target_locale": "pt-PT",
    }
    case.update(overrides)
    return case


def complete_run_record(
    *, run_id: str, case_id: str, digest: str, raw_output_path: str
) -> dict:
    return RunResult(
        run_id=run_id,
        case_id=case_id,
        condition="normal",
        attempt=1,
        runner_mode="fake",
        status="completed",
        failure_class="success",
        process_started=True,
        started_at="2026-08-03T00:00:00Z",
        completed_at="2026-08-03T00:00:01Z",
        exit_code=0,
        timed_out=False,
        refused=False,
        malformed_output=False,
        tool_misuse=False,
        reason=None,
        output_sha256=digest,
        raw_output_path=raw_output_path,
        stderr="",
        telemetry={},
        usage={},
        expected_policy_sha256=None,
        applied_policy_sha256=None,
        policy_integrity="not_required",
        redacted=False,
        project_fingerprint=f"project-{run_id}",
        argv=("deterministic-fake-agent",),
        shell=False,
    ).to_record()


@dataclass(frozen=True)
class StructuredFixture:
    name: str
    case: dict
    good: str
    bad: str


def structured_validation_fixtures() -> tuple[StructuredFixture, ...]:
    return (
        StructuredFixture(
            "json",
            case_with_checks(
                source='{"title":"Hello","items":[{"id":1,"label":"One"}]}',
                checks=[{"type": "json_structure", "severity": "critical"}],
            ),
            '{"title":"Olá","items":[{"id":1,"label":"Um"}]}',
            '{"title":"Olá","items":{"id":1,"label":"Um"}}',
        ),
        StructuredFixture(
            "xml",
            case_with_checks(
                source='<screen id="home"><title>Hello</title><body><b>Now</b></body></screen>',
                checks=[{"type": "xml_structure", "severity": "critical"}],
            ),
            '<screen id="home"><title>Olá</title><body><b>Agora</b></body></screen>',
            '<screen id="home"><title>Olá</title><b>Agora</b></screen>',
        ),
        StructuredFixture(
            "html",
            case_with_checks(
                source='<section class="hero"><h1>Hello</h1><p>Read <strong>this</strong>.</p></section>',
                checks=[{"type": "html_structure", "severity": "critical"}],
            ),
            '<section class="hero"><h1>Olá</h1><p>Leia <strong>isto</strong>.</p></section>',
            '<section><h2>Olá</h2><p>Leia isto.</p></section>',
        ),
        StructuredFixture(
            "markdown",
            case_with_checks(
                source='# Help\n\n- First\n- Second\n\n[Docs](https://lume.example/docs)\n',
                checks=[{"type": "markdown_structure", "severity": "critical"}],
            ),
            '# Ajuda\n\n- Primeiro\n- Segundo\n\n[Documentação](https://lume.example/docs)\n',
            '## Ajuda\n\nPrimeiro\n\n[Documentação](https://evil.example)\n',
        ),
        StructuredFixture(
            "csv",
            case_with_checks(
                source='name,status\nAda,Active\nLin,Paused\n',
                checks=[{"type": "csv_shape", "severity": "critical"}],
            ),
            'nome,estado\nAda,Ativa\nLin,Em pausa\n',
            'nome,estado\nAda,Ativa,extra\nLin\n',
        ),
        StructuredFixture(
            "icu",
            case_with_checks(
                source='{count, plural, =0 {None} one {# item} other {{owner} has # items}}',
                checks=[{"type": "icu_topology", "severity": "critical"}],
            ),
            '{count, plural, =0 {Nenhum} one {# item} other {{owner} tem # itens}}',
            '{count, plural, =0 {Nenhum} other {# itens para owner}}',
        ),
    )


class ValidatorTests(unittest.TestCase):
    def test_placeholder_url_code_number_and_limit_checks(self):
        """Break: damaged protected scalars could pass product-integrity validation."""
        case = case_with_checks(
            source="Pay {amount} at https://lume.example using `lume pay --id 42`",
            checks=[
                {"type": "placeholder_multiset", "severity": "critical"},
                {"type": "url_multiset", "severity": "critical"},
                {"type": "code_span_multiset", "severity": "critical"},
                {"type": "number_multiset", "severity": "major"},
                {"type": "character_limit", "max": 80, "severity": "major"},
            ],
        )

        result = validate_output(
            case,
            "Pague {total} em https://evil.example com `lume pay --id 7`",
        )

        self.assertEqual(
            {finding.invariant for finding in result.findings},
            {"placeholder_multiset", "url_multiset", "code_span_multiset", "number_multiset"},
        )
        self.assertTrue(all(finding.expected is not None for finding in result.findings))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.applicable_checks, 5)
        self.assertEqual(result.failed_checks, 4)
        self.assertEqual(result.passed_checks, 1)

    def test_declared_scalar_check_families_compare_semantic_multisets(self):
        """Break: declared protected tokens could be dropped, duplicated, or replaced silently."""
        case = case_with_checks(
            source="Send %1$s to team@example.com with `lume sync` for AccountID and Lume at 25%.",
            checks=[
                {"type": "format_specifier_multiset", "severity": "critical"},
                {"type": "email_multiset", "severity": "critical"},
                {"type": "command_multiset", "values": ["lume sync"], "severity": "critical"},
                {"type": "identifier_multiset", "values": ["AccountID"], "severity": "critical"},
                {"type": "protected_term_multiset", "severity": "critical"},
                {"type": "line_count", "count": 1, "severity": "major"},
                {
                    "type": "forbidden_locale_form",
                    "forms": ["usuário", "celular"],
                    "severity": "major",
                },
            ],
            protected_terms=["Lume"],
        )
        good = "Envie %1$s para team@example.com com `lume sync` para AccountID e Lume a 25%."
        bad = "Envie %s para outra@example.com com `lume push` para accountId e LUME.\nusuário"

        self.assertEqual(validate_output(case, good).findings, ())
        self.assertEqual(
            {finding.invariant for finding in validate_output(case, bad).findings},
            {
                "format_specifier_multiset",
                "email_multiset",
                "command_multiset",
                "identifier_multiset",
                "protected_term_multiset",
                "line_count",
                "forbidden_locale_form",
            },
        )

    def test_only_declared_checks_run(self):
        """Break: undeclared validators could reject valid alternative prose."""
        case = case_with_checks(
            source="Keep {name} and https://lume.example",
            checks=[{"type": "placeholder_multiset", "severity": "critical"}],
        )

        result = validate_output(case, "Mantenha {name} e remova o endereço")

        self.assertEqual(result.findings, ())
        self.assertEqual(result.applicable_checks, 1)

    def test_numbers_are_compared_by_normalized_locale_value(self):
        """Break: locale punctuation changes could be mistaken for changed numeric meaning."""
        case = case_with_checks(
            source="Total: 1,234.50 (25%)",
            checks=[{"type": "number_multiset", "severity": "critical"}],
            source_locale="en-US",
            target_locale="pt-PT",
        )

        result = validate_output(case, "Total: 1.234,50 (25 %)")

        self.assertEqual(result.findings, ())

    def test_ambiguous_number_separators_follow_each_declared_locale(self):
        """Break: identical punctuation could hide different locale-specific numeric values."""
        case = case_with_checks(
            source="Value: 1,234",
            checks=[{"type": "number_multiset", "severity": "critical"}],
            source_locale="en-US",
            target_locale="pt-PT",
        )

        corrupted = validate_output(case, "Valor: 1,234")
        equivalent = validate_output(case, "Valor: 1.234")

        self.assertEqual(corrupted.status, "failed")
        self.assertEqual({finding.invariant for finding in corrupted.findings}, {"number_multiset"})
        self.assertEqual(equivalent.findings, ())

    def test_icu_sibling_arguments_may_reorder_without_changing_topology(self):
        """Break: natural target-language argument order could be rejected as structural damage."""
        case = case_with_checks(
            source="{first} then {second}",
            checks=[{"type": "icu_topology", "severity": "critical"}],
        )

        result = validate_output(case, "{second} e depois {first}")

        self.assertEqual(result.findings, ())

    def test_icu_plain_apostrophes_do_not_hide_later_arguments(self):
        """Break: ordinary apostrophes could make the brace-aware parser skip real arguments."""
        case = case_with_checks(
            source="You're viewing {count, number} files",
            checks=[{"type": "icu_topology", "severity": "critical"}],
        )

        result = validate_output(case, "Está a ver {count, number} ficheiros")

        self.assertEqual(result.findings, ())

    def test_icu_selector_formatters_require_valid_selectors_and_other(self):
        """Break: malformed selector grammar could pass or be blamed on candidate output."""
        check = [{"type": "icu_topology", "severity": "critical"}]
        malformed_sources = (
            "{gender, select, male {He}}",
            "{count, plural}",
            "{count, plural, banana {Wrong} other {Other}}",
        )
        for source in malformed_sources:
            with self.subTest(source=source):
                result = validate_output(case_with_checks(source=source, checks=check), source)
                self.assertEqual(result.status, "validator_error")
                self.assertEqual(result.findings, ())

        valid_case = case_with_checks(
            source="{count, plural, one {One} other {Other}}",
            checks=check,
        )
        for candidate in (
            "{count, plural}",
            "{count, plural, one {Um}}",
            "{count, plural, banana {Errado} other {Outro}}",
        ):
            with self.subTest(candidate=candidate):
                result = validate_output(valid_case, candidate)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.validator_errors, ())
                self.assertEqual(result.findings[0].invariant, "icu_topology")

    def test_icu_selector_grammar_accepts_multiline_offset_and_categories(self):
        """Break: legal selector whitespace could be folded into a token and rejected."""
        source = (
            "{count, plural,\n"
            " offset:1\n"
            " =0 {None}\n"
            " one {One}\n"
            " other {Other}}"
        )
        candidate = (
            "{count, plural,\n"
            " offset:1\n"
            " =0 {Nenhum}\n"
            " one {Um}\n"
            " other {Outros}}"
        )
        case = case_with_checks(
            source=source,
            checks=[{"type": "icu_topology", "severity": "critical"}],
        )

        result = validate_output(case, candidate)

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.findings, ())

    def test_icu_pattern_whitespace_and_decimal_offsets_follow_grammar(self):
        """Break: valid ICU whitespace/offset numbers could fail while non-pattern spaces pass."""
        source = (
            "{count, plural, offset:1.50\u200e=0 {None}\u2028one {One} other {Other}}"
        )
        candidate = (
            "{count, plural,\toffset:1.5\r\n=0 {Nenhum}\vone {Um}\fother {Outros}}"
        )
        case = case_with_checks(
            source=source,
            checks=[{"type": "icu_topology", "severity": "critical"}],
        )

        valid = validate_output(case, candidate)
        malformed_candidate = validate_output(
            case,
            "{count, plural, offset:. =0 {Nenhum} one {Um} other {Outros}}",
        )
        non_pattern_source = validate_output(
            case_with_checks(
                source="{count, plural, offset:1\u00a0one {One} other {Other}}",
                checks=[{"type": "icu_topology", "severity": "critical"}],
            ),
            "{count, plural, offset:1 one {Um} other {Outros}}",
        )

        self.assertEqual(valid.status, "passed")
        self.assertEqual(valid.findings, ())
        self.assertEqual(malformed_candidate.status, "failed")
        self.assertEqual(malformed_candidate.validator_errors, ())
        self.assertEqual(non_pattern_source.status, "validator_error")

    def test_icu_offsets_accept_double_forms_and_preserve_error_domains(self):
        """Break: valid ICU Double offsets could fail or malformed offsets change domains."""
        check = [{"type": "icu_topology", "severity": "critical"}]
        for source_offset, candidate_offset in (
            (" 1", "1.0"),
            (".5", "0.50"),
            ("+1", "1e0"),
            ("1e2", "100"),
            ("1E-1", ".1"),
        ):
            with self.subTest(source_offset=source_offset, candidate_offset=candidate_offset):
                case = case_with_checks(
                    source=(
                        f"{{count, plural, offset:{source_offset} "
                        "one {One} other {Other}}"
                    ),
                    checks=check,
                )
                result = validate_output(
                    case,
                    f"{{count, plural, offset:{candidate_offset} one {{Um}} other {{Outros}}}}",
                )
                self.assertEqual(result.status, "passed")
                self.assertEqual(result.findings, ())

        valid_case = case_with_checks(
            source="{count, plural, offset:1 one {One} other {Other}}",
            checks=check,
        )
        for malformed in (
            "{count, plural, offset: one {Um} other {Outros}}",
            "{count, plural, offset:nope one {Um} other {Outros}}",
            "{count, plural, offset:1 offset:2 one {Um} other {Outros}}",
            "{count, plural, one {Um} offset:1 other {Outros}}",
        ):
            with self.subTest(malformed=malformed):
                result = validate_output(valid_case, malformed)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.validator_errors, ())

        invalid_source = validate_output(
            case_with_checks(
                source="{count, plural, offset: one {One} other {Other}}",
                checks=check,
            ),
            "{count, plural, offset:1 one {Um} other {Outros}}",
        )
        self.assertEqual(invalid_source.status, "validator_error")

    def test_html_optional_end_tags_preserve_implied_topology(self):
        """Break: valid HTML with implied li closures could be rejected as malformed."""
        case = case_with_checks(
            source="<ul><li>One<li>Two</ul>",
            checks=[{"type": "html_structure", "severity": "critical"}],
        )

        valid = validate_output(case, "<ul><li>Um<li>Dois</ul>")
        corrupted = validate_output(case, "<ul><li>Um<li>Dois<li>Três</ul>")

        self.assertEqual(valid.findings, ())
        self.assertEqual(valid.status, "passed")
        self.assertEqual(corrupted.status, "failed")

    def test_html_block_start_implies_p_end_through_inline_descendants(self):
        """Break: optional p closure could fail when inline elements remain open."""
        case = case_with_checks(
            source="<p><em>One<div>Two</div>",
            checks=[{"type": "html_structure", "severity": "critical"}],
        )

        result = validate_output(case, "<p><em>Um<div>Dois</div>")

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.findings, ())

    def test_html_table_section_implies_colgroup_end_without_unrelated_closure(self):
        """Break: implicit colgroup closure could differ from equivalent explicit HTML."""
        case = case_with_checks(
            source="<table><colgroup><col><tbody><tr><td>A</table>",
            checks=[{"type": "html_structure", "severity": "critical"}],
        )

        equivalent = validate_output(
            case,
            "<table><colgroup><col></colgroup><tbody><tr><td>Um</table>",
        )
        corrupted = validate_output(
            case,
            "<table><colgroup><col></colgroup><tbody><tr><td>Um<td>Extra</table>",
        )

        self.assertEqual(equivalent.status, "passed")
        self.assertEqual(equivalent.findings, ())
        self.assertEqual(corrupted.status, "failed")

    def test_html_rows_and_consecutive_colgroups_imply_scoped_colgroup_ends(self):
        """Break: valid omitted colgroup ends could alter table or nested-table topology."""
        check = [{"type": "html_structure", "severity": "critical"}]
        cases = (
            (
                "<table><colgroup><col><tr><td>A</table>",
                "<table><colgroup><col></colgroup><tr><td>Um</table>",
            ),
            (
                "<table><colgroup><col><colgroup><col><tbody><tr><td>A</table>",
                "<table><colgroup><col></colgroup><colgroup><col>"
                "</colgroup><tbody><tr><td>Um</table>",
            ),
            (
                "<table><tbody><tr><td><table><colgroup><col><tr><td>Inner</table>"
                "<td>Outer</table>",
                "<table><tbody><tr><td><table><colgroup><col></colgroup>"
                "<tr><td>Interior</table><td>Exterior</table>",
            ),
        )
        for source, candidate in cases:
            with self.subTest(source=source):
                result = validate_output(
                    case_with_checks(source=source, checks=check),
                    candidate,
                )
                self.assertEqual(result.status, "passed")
                self.assertEqual(result.findings, ())

        corrupted = validate_output(
            case_with_checks(source=cases[2][0], checks=check),
            cases[2][1].replace("<td>Exterior", "<td>Extra<td>Exterior"),
        )
        self.assertEqual(corrupted.status, "failed")

    def test_markdown_reference_links_preserve_uses_definitions_and_destinations(self):
        """Break: reference-link destination corruption could be invisible to topology checks."""
        case = case_with_checks(
            source="Read [the docs][help].\n\n[help]: https://lume.example/docs\n",
            checks=[{"type": "markdown_structure", "severity": "critical"}],
        )

        valid = validate_output(
            case,
            "Leia [a documentação][help].\n\n[help]: https://lume.example/docs\n",
        )
        corrupted = validate_output(
            case,
            "Leia [a documentação][help].\n\n[help]: https://evil.example\n",
        )

        self.assertEqual(valid.findings, ())
        self.assertEqual(corrupted.status, "failed")
        self.assertEqual(corrupted.findings[0].invariant, "markdown_structure")

    def test_markdown_shortcut_references_resolve_normalized_labels(self):
        """Break: changing a shortcut use could leave a definition falsely counted as sufficient."""
        case = case_with_checks(
            source="Read [Docs].\n\n[  docs ]: https://lume.example/docs\n",
            checks=[{"type": "markdown_structure", "severity": "critical"}],
        )

        equivalent = validate_output(
            case,
            "Leia [DOCS].\n\n[docs]: https://lume.example/docs\n",
        )
        broken = validate_output(
            case,
            "Leia [documentação].\n\n[docs]: https://lume.example/docs\n",
        )

        self.assertEqual(equivalent.status, "passed")
        self.assertEqual(broken.status, "failed")

    def test_markdown_shortcuts_respect_escapes_code_spans_and_definition_titles(self):
        """Break: literal bracket text could be counted as a shortcut reference use."""
        source = (
            "Open [id], then inspect ``[id] with `tick``.\n\n"
            "[id]: https://lume.example/docs \"title [id]\"\n"
        )
        case = case_with_checks(
            source=source,
            checks=[{"type": "markdown_structure", "severity": "critical"}],
        )
        equivalent = (
            "Abra [ID], depois veja ``[changed] com `marca``.\n\n"
            "[ id ]: https://lume.example/docs \"título [changed]\"\n"
        )

        valid = validate_output(case, equivalent)
        escaped_real_use = validate_output(case, equivalent.replace("Abra [ID]", r"Abra \[ID]"))
        even_escape_pair = validate_output(
            case_with_checks(
                source=r"Open \\[id]." + "\n\n[id]: https://lume.example/docs\n",
                checks=[{"type": "markdown_structure", "severity": "critical"}],
            ),
            "Abra [id].\n\n[id]: https://lume.example/docs\n",
        )

        self.assertEqual(valid.status, "passed")
        self.assertEqual(valid.findings, ())
        self.assertEqual(escaped_real_use.status, "failed")
        self.assertEqual(even_escape_pair.status, "passed")

    def test_markdown_definitions_ignore_literals_and_first_duplicate_wins(self):
        """Break: literal or later duplicate definitions could alter active link topology."""
        source = (
            "```text\n[id]: https://inside-source.example\n```\n\n"
            "    [id]: https://indented-source.example\n\n"
            "[id]: https://first.example\n"
            "[ID]: https://ignored-source.example\n\n"
            "Open [id].\n"
        )
        case = case_with_checks(
            source=source,
            checks=[{"type": "markdown_structure", "severity": "critical"}],
        )
        equivalent = (
            "```text\n[id]: https://inside-candidate.example\n```\n\n"
            "    [id]: https://indented-candidate.example\n\n"
            "[ ID ]: https://first.example\n"
            "[id]: https://ignored-candidate.example\n\n"
            "Abra [id].\n"
        )
        changed_first = equivalent.replace(
            "[ ID ]: https://first.example",
            "[ ID ]: https://changed.example",
        )

        valid = validate_output(case, equivalent)
        corrupted = validate_output(case, changed_first)

        self.assertEqual(valid.status, "passed")
        self.assertEqual(valid.findings, ())
        self.assertEqual(corrupted.status, "failed")

    def test_markdown_fences_require_valid_closers_and_allow_tilde_backtick_info(self):
        """Break: incorrect fence transitions could expose literals or reject valid Markdown."""
        check = [{"type": "markdown_structure", "severity": "critical"}]
        cases = (
            (
                "```text\n[id]: https://inside-one.example\n```not-a-close\n"
                "[id]: https://inside-two.example\n```\n\n"
                "[id]: https://active.example\nOpen [id].\n",
                "```text\n[id]: https://changed-one.example\n```not-a-close\n"
                "[id]: https://changed-two.example\n```\n\n"
                "[id]: https://active.example\nAbra [id].\n",
            ),
            (
                "~~~ language=`literal`\n[id]: https://inside.example\n~~~ \t\n\n"
                "[id]: https://active.example\nOpen [id].\n",
                "~~~ language=`literal`\n[id]: https://changed.example\n~~~\t\n\n"
                "[id]: https://active.example\nAbra [id].\n",
            ),
            (
                "```language=`not-a-fence`\n[id]: https://active.example\nOpen [id].\n",
                "```language=`not-a-fence`\n[id]: https://active.example\nAbra [id].\n",
            ),
        )
        for source, candidate in cases:
            with self.subTest(source=source.splitlines()[0]):
                result = validate_output(
                    case_with_checks(source=source, checks=check),
                    candidate,
                )
                self.assertEqual(result.status, "passed")
                self.assertEqual(result.findings, ())

    def test_numeric_placeholders_and_multi_backtick_code_spans_are_protected(self):
        """Break: valid numeric and delimiter-aware scalar syntax could be changed silently."""
        placeholder_case = case_with_checks(
            source="Hello {0}",
            checks=[{"type": "placeholder_multiset", "severity": "critical"}],
        )
        code_case = case_with_checks(
            source="Use ``a`b`` now",
            checks=[{"type": "code_span_multiset", "severity": "critical"}],
        )

        placeholder_result = validate_output(placeholder_case, "Olá {1}")
        code_result = validate_output(code_case, "Use ``changed`` agora")

        self.assertEqual({item.invariant for item in placeholder_result.findings}, {"placeholder_multiset"})
        self.assertEqual({item.invariant for item in code_result.findings}, {"code_span_multiset"})

    def test_code_spans_normalize_crlf_cr_and_lf_before_content_comparison(self):
        """Break: platform line endings could make identical CommonMark code spans differ."""
        case = case_with_checks(
            source="Use ``a\r\nb\rc`` now",
            checks=[{"type": "code_span_multiset", "severity": "critical"}],
        )

        equivalent = validate_output(case, "Use ``a\nb\nc`` agora")
        corrupted = validate_output(case, "Use ``a\nb\nchanged`` agora")

        self.assertEqual(equivalent.status, "passed")
        self.assertEqual(equivalent.findings, ())
        self.assertEqual(corrupted.status, "failed")

    def test_json_xml_html_markdown_csv_and_icu_are_semantically_checked(self):
        """Break: merely parseable output with changed declared topology could pass."""
        for fixture in structured_validation_fixtures():
            with self.subTest(fixture=fixture.name):
                self.assertEqual(validate_output(fixture.case, fixture.good).findings, ())
                findings = validate_output(fixture.case, fixture.bad).findings
                self.assertGreater(len(findings), 0)
                self.assertEqual(findings[0].invariant, fixture.case["automatic_checks"][0]["type"])

    def test_malformed_structured_outputs_are_candidate_failures(self):
        """Break: parser rejection could be mislabeled as a validator implementation fault."""
        malformed = {
            "json_structure": "{",
            "xml_structure": "<screen>",
            "html_structure": "<section><strong></section>",
            "markdown_structure": "```python\nprint('open')\n",
            "csv_shape": 'name,status\n"Ada,Active\n',
            "icu_topology": "{count, plural, one {item}",
        }
        sources = {fixture.name: fixture.case["source"] for fixture in structured_validation_fixtures()}
        names = {
            "json_structure": "json",
            "xml_structure": "xml",
            "html_structure": "html",
            "markdown_structure": "markdown",
            "csv_shape": "csv",
            "icu_topology": "icu",
        }
        for check_type, output in malformed.items():
            with self.subTest(check=check_type):
                result = validate_output(
                    case_with_checks(
                        source=sources[names[check_type]],
                        checks=[{"type": check_type, "severity": "critical"}],
                    ),
                    output,
                )
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.validator_errors, ())
                self.assertEqual(len(result.findings), 1)

    def test_validator_exception_is_distinct_from_output_failure_and_output_is_unchanged(self):
        """Break: a validator bug could condemn or rewrite an otherwise preserved candidate."""
        original = "texto {count}"
        case = case_with_checks(
            source="text {count}",
            checks=[{"type": "icu_topology", "severity": "critical"}],
        )

        with mock.patch.dict(CHECKS, {"icu_topology": mock.Mock(side_effect=RuntimeError("bug"))}):
            result = validate_output(case, original)

        self.assertEqual(result.status, "validator_error")
        self.assertEqual(result.output, original)
        self.assertEqual(result.findings, ())
        self.assertEqual(len(result.validator_errors), 1)
        self.assertIn("RuntimeError", result.validator_errors[0])


class BatchValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = self.root / "dataset"
        self.evidence = self.root / "evidence"
        self.dataset.mkdir()
        (self.evidence / "raw").mkdir(parents=True)
        cases = [
            case_with_checks(
                source="Hello {name}",
                checks=[{"type": "placeholder_multiset", "severity": "critical"}],
                id="case-a",
            ),
            case_with_checks(
                source='{"title":"Hello"}',
                checks=[{"type": "json_structure", "severity": "critical"}],
                id="case-b",
            ),
        ]
        (self.dataset / "cases.jsonl").write_bytes(b"".join(canonical_bytes(case) for case in cases))
        self.outputs = {"run-a": "Olá {name}", "run-b": '{"title":"Olá"}'}
        records = []
        for run_id, case_id in (("run-a", "case-a"), ("run-b", "case-b")):
            encoded = self.outputs[run_id].encode("utf-8")
            digest = sha256_bytes(encoded)
            raw_path = self.evidence / "raw" / f"{digest}.txt"
            raw_path.write_bytes(encoded)
            records.append(complete_run_record(
                run_id=run_id,
                case_id=case_id,
                digest=digest,
                raw_output_path=f"raw/{digest}.txt",
            ))
        (self.evidence / "runs.jsonl").write_bytes(
            b"".join(canonical_bytes(record) for record in records)
        )
        (self.evidence / "run-manifest.json").write_bytes(canonical_bytes({
            "schema_version": 1,
            "schedule": {
                "run_ids": ["run-b", "run-a"],
                "sha256": sha256_bytes(canonical_bytes(["run-b", "run-a"])),
            },
        }))

    def raw_snapshot(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in (self.evidence / "raw").iterdir()}

    def test_batch_writes_one_canonical_record_per_manifest_run_without_touching_raw(self):
        """Break: validation could reorder evidence, duplicate records, or mutate raw outputs."""
        destination = self.evidence / "validation.jsonl"
        raw_before = self.raw_snapshot()

        results = validate_runs(self.dataset, self.evidence, destination)

        self.assertEqual([result.run_id for result in results], ["run-b", "run-a"])
        records = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["run_id"] for record in records], ["run-b", "run-a"])
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["applicable_checks"] == 1 for record in records))
        self.assertEqual(destination.read_bytes(), b"".join(canonical_bytes(record) for record in records))
        self.assertEqual(self.raw_snapshot(), raw_before)

    def test_batch_refuses_incomplete_run_sets_before_replacing_output(self):
        """Break: partial evidence could be published as a complete validation cohort."""
        destination = self.evidence / "validation.jsonl"
        destination.write_text("preserve-existing\n", encoding="utf-8")
        lines = (self.evidence / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        (self.evidence / "runs.jsonl").write_text(lines[0] + "\n", encoding="utf-8")

        with self.assertRaisesRegex(BenchmarkError, "incomplete run set"):
            validate_runs(self.dataset, self.evidence, destination)

        self.assertEqual(destination.read_text(encoding="utf-8"), "preserve-existing\n")

    def test_batch_rejects_raw_hash_mismatch_and_output_inside_raw(self):
        """Break: validators could consume substituted evidence or write into the immutable raw area."""
        first_raw = next((self.evidence / "raw").iterdir())
        first_raw.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(BenchmarkError, "hash mismatch"):
            validate_runs(self.dataset, self.evidence, self.evidence / "validation.jsonl")
        with self.assertRaisesRegex(BenchmarkError, "raw"):
            validate_runs(self.dataset, self.evidence, self.evidence / "raw" / "validation.jsonl")

    def test_batch_rejects_a_symlinked_raw_evidence_directory(self):
        """Break: a symlinked raw root could make validation trust evidence outside the bundle."""
        alternate = self.root / "alternate-evidence"
        alternate.mkdir()
        (alternate / "raw").symlink_to(self.evidence / "raw", target_is_directory=True)
        (alternate / "runs.jsonl").write_bytes((self.evidence / "runs.jsonl").read_bytes())
        (alternate / "run-manifest.json").write_bytes(
            (self.evidence / "run-manifest.json").read_bytes()
        )

        with self.assertRaisesRegex(BenchmarkError, "symlink raw"):
            validate_runs(self.dataset, alternate, alternate / "validation.jsonl")

    def test_batch_requires_canonical_schedule_hash_and_shared_run_record_schema(self):
        """Break: non-canonical or malformed runner evidence could be published as validated."""
        destination = self.evidence / "validation.jsonl"
        original_manifest = (self.evidence / "run-manifest.json").read_bytes()
        original_runs = (self.evidence / "runs.jsonl").read_bytes()

        manifest = json.loads(original_manifest)
        manifest["schedule"].pop("sha256")
        (self.evidence / "run-manifest.json").write_bytes(canonical_bytes(manifest))
        with self.assertRaisesRegex(BenchmarkError, "schedule hash"):
            validate_runs(self.dataset, self.evidence, destination)

        (self.evidence / "run-manifest.json").write_bytes(original_manifest)
        malformed = [json.loads(line) for line in original_runs.splitlines()]
        malformed[0]["schema_version"] = 999
        (self.evidence / "runs.jsonl").write_bytes(
            b"".join(canonical_bytes(record) for record in malformed)
        )
        with self.assertRaisesRegex(BenchmarkError, "schema version"):
            validate_runs(self.dataset, self.evidence, destination)

        malformed = [json.loads(line) for line in original_runs.splitlines()]
        malformed[0].pop("condition")
        (self.evidence / "runs.jsonl").write_bytes(
            b"".join(canonical_bytes(record) for record in malformed)
        )
        with self.assertRaisesRegex(BenchmarkError, "malformed existing run record"):
            validate_runs(self.dataset, self.evidence, destination)

        malformed = [json.loads(line) for line in original_runs.splitlines()]
        malformed[0]["raw_output_path"] = ["raw", "not-text"]
        (self.evidence / "runs.jsonl").write_bytes(
            b"".join(canonical_bytes(record) for record in malformed)
        )
        with self.assertRaisesRegex(BenchmarkError, "invalid raw output path"):
            validate_runs(self.dataset, self.evidence, destination)
        self.assertFalse(destination.exists())

    def test_batch_refuses_output_aliases_to_every_consumed_input(self):
        """Break: atomic publication could replace the manifest, run index, or dataset cases."""
        consumed = (
            self.evidence / "run-manifest.json",
            self.evidence / "runs.jsonl",
            self.dataset / "cases.jsonl",
        )
        before = {path: path.read_bytes() for path in consumed}

        for destination in consumed:
            with self.subTest(destination=destination):
                try:
                    with self.assertRaisesRegex(BenchmarkError, "input"):
                        validate_runs(self.dataset, self.evidence, destination)
                finally:
                    for path, content in before.items():
                        path.write_bytes(content)

        self.assertEqual({path: path.read_bytes() for path in consumed}, before)

    def test_module_cli_validates_complete_evidence(self):
        """Break: the documented module entry point could diverge from the batch API."""
        destination = self.evidence / "validation.jsonl"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.benchmark.validate",
                "--dataset",
                str(self.dataset),
                "--evidence",
                str(self.evidence),
                "--output",
                str(destination),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"runs": 2, "output": str(destination)})
        self.assertEqual(len(destination.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
