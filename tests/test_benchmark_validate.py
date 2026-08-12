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
        "task": "translation",
        "source": source,
        "automatic_checks": checks,
        "protected_terms": [],
        "source_locale": "en-GB",
        "target_locale": "pt-PT",
    }
    case.update(overrides)
    return case


def review_case() -> dict:
    return case_with_checks(
        id="review-1",
        task="review",
        source="Run `lume fetch` with Lume.",
        checks=[
            {"type": "code_span_multiset", "severity": "critical"},
            {"type": "protected_term_multiset", "severity": "critical"},
        ],
        protected_terms=["Lume"],
    )


def review_output(corrected: str) -> str:
    return json.dumps({
        "corrected_translation": corrected,
        "issues": [{
            "category": "accuracy",
            "source_span": "`lume fetch` and Lume",
            "candidate_span": "`lume send` and Lume",
            "explanation": "Restore `lume fetch`; keep Lume exactly.",
        }],
    }, ensure_ascii=False)


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
    def test_serialized_findings_preserve_expected_and_observed_evidence(self):
        """Break: the installed-engine adapter could discard benchmark evidence payloads."""
        result = validate_output(
            case_with_checks(
                source="Pay {amount}",
                checks=[{"type": "placeholder_multiset", "severity": "critical"}],
            ),
            "Payez {total}",
        )

        serialized = json.loads(json.dumps(result.to_record()))["findings"]
        self.assertEqual(serialized, [{
            "invariant": "placeholder_multiset",
            "severity": "critical",
            "expected": {"{amount}": 1},
            "observed": {"{total}": 1},
            "affected_span": [6, 13],
            "message": "placeholder_multiset differs from the declared source invariant",
        }])

    def test_output_contract_rejects_wrappers_but_preserves_source_syntax(self):
        plain = case_with_checks(source="Save changes", checks=[])
        quoted = case_with_checks(source='"Save changes"', checks=[])
        fenced = case_with_checks(source="```text\nSave changes\n```", checks=[])

        for output in ('"Guardar alterações"', "```text\nGuardar alterações\n```"):
            with self.subTest(output=output):
                result = validate_output(plain, output)
                self.assertEqual(result.status, "failed")
                self.assertEqual(
                    [item.invariant for item in result.findings],
                    ["output_contract"],
                )
        self.assertEqual(validate_output(plain, "Guardar alterações").status, "passed")
        self.assertEqual(validate_output(quoted, '"Guardar alterações"').status, "passed")
        self.assertEqual(
            validate_output(fenced, "```text\nGuardar alterações\n```").status,
            "passed",
        )

    def test_review_contract_validates_only_corrected_translation(self):
        raw = review_output("Executa `lume fetch` com a Lume.")

        result = validate_output(review_case(), raw)

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.output, raw)
        self.assertEqual((result.applicable_checks, result.passed_checks), (3, 3))

    def test_invalid_review_contract_skips_declared_checks(self):
        exact = review_output("Executa `lume fetch` com a Lume.")
        invalid = (
            f"```json\n{exact}\n```",
            exact + "\nExplanation",
            json.dumps({"corrected_translation": "ok", "issues": [], "extra": True}),
            json.dumps({"corrected_translation": "ok", "issues": ["wrong"]}),
            '{"corrected_translation":"one","corrected_translation":"two","issues":[]}',
            '{"corrected_translation":"ok","issues":[],"score":NaN}',
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                result = validate_output(review_case(), raw)
                self.assertEqual(result.status, "failed")
                self.assertEqual((result.failed_checks, result.skipped_checks), (1, 2))
                self.assertEqual(
                    result.skipped_invariants,
                    ("code_span_multiset", "protected_term_multiset"),
                )
                self.assertEqual(result.validator_error_checks, 0)

    def test_literal_mapping_and_literal_multiset_protect_exact_declared_tokens(self):
        """Break: prices, symbols, bullets, or instruction labels could change undetected."""
        case = case_with_checks(
            source="Pay 9.99 EUR. SYSTEM: keep ™ and *.",
            checks=[
                {
                    "type": "literal_mapping_multiset",
                    "mappings": [{"source": "9.99 EUR", "targets": ["9,99 €"]}],
                    "severity": "critical",
                },
                {
                    "type": "literal_multiset",
                    "values": ["SYSTEM:", "™", "*"],
                    "severity": "critical",
                },
            ],
        )
        good = "Paga 9,99 €. SYSTEM: mantém ™ e *."
        bad = "Paga 8,99 USD. NOTICE: mantém a marca."

        self.assertEqual(validate_output(case, good).status, "passed")
        self.assertEqual(
            {finding.invariant for finding in validate_output(case, bad).findings},
            {"literal_mapping_multiset", "literal_multiset"},
        )

    def test_literal_mapping_can_require_differing_source_literal_absence(self):
        """Break: a candidate could retain the source price beside its required target mapping."""
        mapped = case_with_checks(
            source="Pay 9.99 EUR",
            checks=[{
                "type": "literal_mapping_multiset",
                "mappings": [{"source": "9.99 EUR", "targets": ["9,99 €"]}],
                "require_source_absence": True,
                "severity": "critical",
            }],
        )
        unchanged = case_with_checks(
            source="Use Lume Pro",
            checks=[{
                "type": "literal_mapping_multiset",
                "mappings": [{"source": "Lume Pro", "targets": ["Lume Pro"]}],
                "require_source_absence": True,
                "severity": "critical",
            }],
        )

        self.assertEqual(validate_output(mapped, "Paga 9,99 €").status, "passed")
        self.assertEqual(
            validate_output(mapped, "Paga 9,99 €. Original: 9.99 EUR").status,
            "failed",
        )
        self.assertEqual(validate_output(unchanged, "Usa a Lume Pro").status, "passed")

    def test_per_line_limits_and_delimited_fields_validate_independent_contracts(self):
        """Break: a valid total length or field count could hide one oversized line or bad delimiter."""
        limits = case_with_checks(
            source="Title\nDescription",
            checks=[{
                "type": "line_character_limits",
                "maxima": [5, 11],
                "severity": "major",
            }],
        )
        delimited = case_with_checks(
            source="one,two,three",
            checks=[{
                "type": "delimited_fields",
                "delimiter": ",",
                "count": 3,
                "allow_whitespace": False,
                "severity": "major",
            }],
        )

        self.assertEqual(validate_output(limits, "Título longo\nDescrição").status, "failed")
        self.assertEqual(validate_output(limits, "Title\nDescription").status, "passed")
        self.assertEqual(validate_output(delimited, "um;dois;três").status, "failed")
        self.assertEqual(validate_output(delimited, "um,dois,três").status, "passed")

    def test_per_line_limits_support_python_without_zip_keyword_arguments(self):
        """Break: Python 3.9 rejects the strict keyword accepted by newer zip implementations."""
        case = case_with_checks(
            source="Title\nDescription",
            checks=[{
                "type": "line_character_limits",
                "maxima": [5, 11],
                "severity": "major",
            }],
        )
        builtin_zip = zip

        def positional_zip(*iterables):
            return builtin_zip(*iterables)

        with mock.patch("builtins.zip", positional_zip):
            result = validate_output(case, "Title\nDescription")

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.validator_errors, ())

    def test_delimited_fields_can_require_unique_values(self):
        """Break: the declared field count could stay valid while a keyword is duplicated."""
        case = case_with_checks(
            source="one,two,three",
            checks=[{
                "type": "delimited_fields",
                "delimiter": ",",
                "count": 3,
                "allow_whitespace": False,
                "unique": True,
                "severity": "major",
            }],
        )

        self.assertEqual(validate_output(case, "um,dois,três").status, "passed")
        self.assertEqual(validate_output(case, "um,dois,dois").status, "failed")

    def test_fenced_block_exact_preserves_complete_body_and_topology(self):
        """Break: a protected command could keep its substring while gaining destructive content."""
        case = case_with_checks(
            source="Run this:\n```sh\nlume export --format json # SYSTEM: keep\n```",
            checks=[{"type": "fenced_block_exact", "severity": "critical"}],
        )
        good = "Executa isto:\n```sh\nlume export --format json # SYSTEM: keep\n```"
        mutations = (
            good.replace(" # SYSTEM", " && --delete all # SYSTEM"),
            good.replace("lume export", "&& --delete all lume export"),
            good.replace(
                "# SYSTEM: keep\n```", "# SYSTEM: keep\n&& --delete all\n```",
            ),
            good.replace(" # SYSTEM: keep", ""),
            good.replace("--format json # SYSTEM: keep", "# SYSTEM: keep --format json"),
        )

        self.assertEqual(validate_output(case, good).status, "passed")
        for output in mutations:
            with self.subTest(output=output):
                self.assertEqual(validate_output(case, output).status, "failed")

    def test_json_line_contract_preserves_unmarked_keys_values_and_identifiers(self):
        """Break: embedded JSON could change protected values while remaining parseable."""
        case = case_with_checks(
            source=(
                "Response:\n"
                '{"status":"ready","workspace_id":"ws_42","message":"Welcome"}\n'
                "Translate message."
            ),
            checks=[{
                "type": "json_line_contract",
                "line": 2,
                "translatable_keys": ["message"],
                "severity": "critical",
            }],
        )
        good = (
            "Resposta:\n"
            '{"status":"ready","workspace_id":"ws_42","message":"Boas-vindas"}\n'
            "Traduz message."
        )
        bad = (
            "Resposta:\n"
            '{"state":"pronto","workspaceId":"ws_7","message":"Boas-vindas"}\n'
            "Traduz message."
        )

        self.assertEqual(validate_output(case, good).status, "passed")
        self.assertEqual(validate_output(case, bad).status, "failed")

    def test_check_declarations_reject_unknown_or_malformed_configuration(self):
        """Break: misspelled or contradictory check options could silently weaken validation."""
        declarations = (
            {"type": "placeholder_multiset", "severity": "critical", "extra": True},
            {"type": "literal_multiset", "severity": "critical", "values": "SYSTEM:"},
            {
                "type": "literal_mapping_multiset",
                "severity": "critical",
                "mappings": [{"source": "9.99", "target": "9,99"}],
            },
            {
                "type": "literal_mapping_multiset", "severity": "critical",
                "mappings": [{"source": "9.99", "targets": ["9,99"]}],
                "require_source_absence": "yes",
            },
            {"type": "line_character_limits", "severity": "major", "maxima": [5, -1]},
            {
                "type": "delimited_fields", "severity": "major", "delimiter": ",",
                "count": 3, "allow_whitespace": "no",
            },
            {
                "type": "delimited_fields", "severity": "major", "delimiter": ",",
                "count": 3, "allow_whitespace": False, "unique": "yes",
            },
            {"type": "fenced_block_exact", "severity": "critical", "extra": True},
            {
                "type": "json_line_contract", "severity": "critical", "line": 0,
                "translatable_keys": ["message"],
            },
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                result = validate_output(
                    case_with_checks(source="Source", checks=[declaration]), "Target"
                )
                self.assertEqual(result.status, "validator_error")
                self.assertEqual(result.validator_error_checks, 1)

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
        self.assertEqual(result.applicable_checks, 6)
        self.assertEqual(result.failed_checks, 4)
        self.assertEqual(result.passed_checks, 2)

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
        self.assertEqual(result.applicable_checks, 2)

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

    def test_icu_offsets_reject_non_ascii_digits_and_non_finite_doubles(self):
        """Break: offsets outside the finite ASCII Double grammar could be accepted."""
        check = [{"type": "icu_topology", "severity": "critical"}]
        valid_source = "{count, plural, offset:1 one {One} other {Other}}"
        valid_case = case_with_checks(source=valid_source, checks=check)

        for malformed_offset in ("١", "1e309", "1.7976931348623159e308"):
            with self.subTest(domain="candidate", offset=malformed_offset):
                candidate = (
                    f"{{count, plural, offset:{malformed_offset} "
                    "one {Um} other {Outros}}"
                )
                result = validate_output(valid_case, candidate)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.validator_errors, ())
                self.assertEqual(result.output, candidate)
                self.assertIn("parse_error", result.findings[0].observed)

            with self.subTest(domain="source", offset=malformed_offset):
                malformed_source = (
                    f"{{count, plural, offset:{malformed_offset} "
                    "one {One} other {Other}}"
                )
                result = validate_output(
                    case_with_checks(source=malformed_source, checks=check),
                    valid_source,
                )
                self.assertEqual(result.status, "validator_error")
                self.assertEqual(result.findings, ())

        maximum_finite = validate_output(
            case_with_checks(
                source=(
                    "{count, plural, offset:1.7976931348623157e308 "
                    "one {One} other {Other}}"
                ),
                checks=check,
            ),
            (
                "{count, plural, offset:17976931348623157e292 "
                "one {Um} other {Outros}}"
            ),
        )
        self.assertEqual(maximum_finite.status, "passed")
        self.assertEqual(maximum_finite.findings, ())

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

    def test_markdown_reference_labels_allow_escaped_closing_brackets(self):
        """Break: escaped closing brackets could hide full, collapsed, or shortcut uses."""
        check = [{"type": "markdown_structure", "severity": "critical"}]
        source = (
            "Full [text][ref\\]], collapsed [ref\\]][], shortcut [ref\\]].\n\n"
            "[  REF\\]  ]: https://lume.example/docs\n"
        )
        equivalent = (
            "Completa [texto][ref\\]], recolhida [ref\\]][], atalho [ref\\]].\n\n"
            "[ref\\]]: https://lume.example/docs\n"
        )
        case = case_with_checks(source=source, checks=check)

        valid = validate_output(case, equivalent)
        corrupted_candidates = (
            equivalent.replace("[texto][ref\\]]", "[texto][missing]", 1),
            equivalent.replace("[ref\\]][]", "[missing][]", 1),
            equivalent.replace("atalho [ref\\]]", "atalho [missing]", 1),
        )

        self.assertEqual(valid.status, "passed")
        self.assertEqual(valid.findings, ())
        for candidate in corrupted_candidates:
            with self.subTest(candidate=candidate.splitlines()[0]):
                result = validate_output(case, candidate)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.validator_errors, ())

        even_escape_pair = validate_output(
            case_with_checks(
                source=(
                    r"Open [ref\\]." + "\n\n"
                    r"[ref\\]: https://lume.example/even" + "\n"
                ),
                checks=check,
            ),
            (
                r"Abra [REF\\]." + "\n\n"
                r"[ REF\\ ]: https://lume.example/even" + "\n"
            ),
        )
        self.assertEqual(even_escape_pair.status, "passed")

    def test_markdown_next_line_reference_titles_are_not_shortcut_uses(self):
        """Break: bracket text in a valid next-line definition title could become a link."""
        check = [{"type": "markdown_structure", "severity": "critical"}]
        title_pairs = (
            ('  "source [meta]"', ' "tradução [alterado]"'),
            (" 'source [meta]'", "   'tradução [alterado]'"),
            ("(source [meta])", "  (tradução [alterado])"),
        )
        for source_title, candidate_title in title_pairs:
            with self.subTest(title=source_title[0]):
                source = (
                    "Open [id].\n\n[id]: https://lume.example/docs\n"
                    f"{source_title}\n\n[meta]: https://lume.example/meta\n"
                )
                candidate = (
                    "Abra [id].\n\n[id]: https://lume.example/docs\n"
                    f"{candidate_title}\n\n[meta]: https://lume.example/meta\n"
                )
                result = validate_output(
                    case_with_checks(source=source, checks=check),
                    candidate,
                )
                self.assertEqual(result.status, "passed")
                self.assertEqual(result.findings, ())

        separated_title = validate_output(
            case_with_checks(
                source=(
                    "Open [id].\n\n[id]: https://lume.example/docs\n\n"
                    '"ordinary [meta]"\n\n[meta]: https://lume.example/meta\n'
                ),
                checks=check,
            ),
            (
                "Abra [id].\n\n[id]: https://lume.example/docs\n\n"
                '"ordinary [changed]"\n\n[meta]: https://lume.example/meta\n'
            ),
        )
        self.assertEqual(separated_title.status, "failed")

        intervening_fence = validate_output(
            case_with_checks(
                source=(
                    "Open [id].\n\n[id]: https://lume.example/docs\n"
                    "```text\nliteral\n```\n"
                    '"ordinary [meta]"\n\n[meta]: https://lume.example/meta\n'
                ),
                checks=check,
            ),
            (
                "Abra [id].\n\n[id]: https://lume.example/docs\n"
                "```text\nliteral traduzido\n```\n"
                '"ordinary [changed]"\n\n[meta]: https://lume.example/meta\n'
            ),
        )
        self.assertEqual(intervening_fence.status, "failed")

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

    def test_markdown_container_fences_exclude_literal_reference_syntax(self):
        """Break: list or blockquote fences could expose literal references or remain unclosed."""
        check = [{"type": "markdown_structure", "severity": "critical"}]
        pairs = (
            (
                "- ```text\n"
                "  [id]: https://inside-source.example\n"
                "  ```not-a-close\n"
                "  [id]\n"
                "  ```\n\n"
                "[id]: https://active.example\nOpen [id].\n",
                "- ```text\n"
                "  [changed]: https://inside-candidate.example\n"
                "  ```not-a-close\n"
                "  [changed]\n"
                "  ```\n\n"
                "[id]: https://active.example\nAbra [id].\n",
            ),
            (
                "> ~~~ language=`literal`\n"
                "> [id]: https://inside-source.example\n"
                "> [id]\n"
                "> ~~~\n\n"
                "[id]: https://active.example\nOpen [id].\n",
                "> ~~~ language=`literal`\n"
                "> [changed]: https://inside-candidate.example\n"
                "> [changed]\n"
                "> ~~~\n\n"
                "[id]: https://active.example\nAbra [id].\n",
            ),
            (
                "- > ````text\n"
                "  > [id]\n"
                "  > ```\n"
                "  > [id]: https://inside-source.example\n"
                "  > ````\n\n"
                "[id]: https://active.example\nOpen [id].\n",
                "- > ````text\n"
                "  > [changed]\n"
                "  > ```\n"
                "  > [changed]: https://inside-candidate.example\n"
                "  > ````\n\n"
                "[id]: https://active.example\nAbra [id].\n",
            ),
            (
                "> ````text\n"
                "> - ````\n"
                "> [id]: https://inside-source.example\n"
                "> ````\n\n"
                "[id]: https://active.example\nOpen [id].\n",
                "> ````text\n"
                "> - ````\n"
                "> [changed]: https://inside-candidate.example\n"
                "> ````\n\n"
                "[id]: https://active.example\nAbra [id].\n",
            ),
        )
        for source, candidate in pairs:
            with self.subTest(container=source.splitlines()[0]):
                case = case_with_checks(source=source, checks=check)
                result = validate_output(case, candidate)
                corrupted = validate_output(
                    case,
                    candidate.replace(
                        "[id]: https://active.example",
                        "[id]: https://evil.example",
                    ),
                )
                self.assertEqual(result.status, "passed")
                self.assertEqual(result.findings, ())
                self.assertEqual(corrupted.status, "failed")

    def test_markdown_list_indented_code_does_not_invent_fences(self):
        """Break: a five-space list/code prefix could become a truncated fence prefix."""
        check = [{"type": "markdown_structure", "severity": "critical"}]
        unclosed_literal = "-     ```text\n      [id]: /inside\n"
        closed_literal = unclosed_literal + "      ```\n"

        valid_source = validate_output(
            case_with_checks(source=unclosed_literal, checks=check),
            unclosed_literal,
        )
        translated_literal = validate_output(
            case_with_checks(source=closed_literal, checks=check),
            closed_literal.replace("```text", "```translated", 1),
        )

        self.assertEqual(valid_source.status, "passed")
        self.assertEqual(valid_source.validator_errors, ())
        self.assertEqual(translated_literal.status, "passed")
        self.assertEqual(translated_literal.findings, ())

    def test_markdown_list_fence_boundary_covers_markers_and_space_columns(self):
        """Break: list whitespace could be truncated instead of classified before fence discovery."""
        check = [{"type": "markdown_structure", "severity": "critical"}]
        markers = ("-", "+", "*", "1.", "2)", "123456789.")
        for marker in markers:
            for spaces in range(1, 9):
                with self.subTest(marker=marker, spaces=spaces):
                    gap = " " * spaces
                    continuation = " " * (len(marker) + spaces)
                    source = (
                        f"{marker}{gap}```text\n"
                        f"{continuation}[inside]: https://source.example\n"
                        f"{continuation}```not-a-close\n"
                        f"{continuation}[inside]\n"
                        f"{continuation}```\n\n"
                        "[active]: https://active.example\nOpen [active].\n"
                    )
                    candidate_info = "text" if spaces <= 4 else "translated"
                    candidate = (
                        f"{marker}{gap}```{candidate_info}\n"
                        f"{continuation}[changed]: https://candidate.example\n"
                        f"{continuation}```still-not-a-close\n"
                        f"{continuation}[changed]\n"
                        f"{continuation}```\n\n"
                        "[active]: https://active.example\nAbra [active].\n"
                    )

                    result = validate_output(
                        case_with_checks(source=source, checks=check),
                        candidate,
                    )
                    corrupted = validate_output(
                        case_with_checks(source=source, checks=check),
                        candidate.replace(
                            "[active]: https://active.example",
                            "[active]: https://evil.example",
                        ),
                    )

                    self.assertEqual(result.status, "passed")
                    self.assertEqual(result.findings, ())
                    self.assertEqual(result.validator_errors, ())
                    self.assertEqual(corrupted.status, "failed")

    def test_markdown_list_fence_boundary_handles_tabs_and_nested_containers(self):
        """Break: tab columns or nested containers could expose literal code as Markdown."""
        check = [{"type": "markdown_structure", "severity": "critical"}]
        tab_cases = (
            ("bullet-tab", "-", "\t", 4, True),
            ("bullet-tab-space", "-", "\t ", 5, True),
            ("bullet-tab-two-spaces", "-", "\t  ", 6, False),
            ("bullet-double-tab", "-", "\t\t", 8, False),
            ("bullet-two-spaces-tab-space", "-", "  \t ", 5, True),
            ("bullet-two-spaces-tab-two-spaces", "-", "  \t  ", 6, False),
            ("bullet-three-spaces-tab", "-", "   \t", 8, False),
            ("ordered-tab", "1.", "\t", 4, True),
            ("ordered-tab-two-spaces", "1.", "\t  ", 6, True),
            ("ordered-tab-three-spaces", "1.", "\t   ", 7, False),
            ("ordered-space-tab-two-spaces", "1.", " \t  ", 6, True),
            ("ordered-space-tab-three-spaces", "1.", " \t   ", 7, False),
            ("ordered-two-spaces-tab", "1.", "  \t", 8, False),
        )
        for name, marker, gap, continuation_column, is_fence in tab_cases:
            with self.subTest(case=name):
                continuation = " " * continuation_column
                source = (
                    f"{marker}{gap}```text\n"
                    f"{continuation}[inside]: https://source.example\n"
                    f"{continuation}```not-a-close\n"
                    f"{continuation}```\n\n"
                    "[active]: https://active.example\nOpen [active].\n"
                )
                candidate_info = "text" if is_fence else "translated"
                candidate = (
                    f"{marker}{gap}```{candidate_info}\n"
                    f"{continuation}[changed]: https://candidate.example\n"
                    f"{continuation}```still-not-a-close\n"
                    f"{continuation}```\n\n"
                    "[active]: https://active.example\nAbra [active].\n"
                )

                result = validate_output(
                    case_with_checks(source=source, checks=check),
                    candidate,
                )
                self.assertEqual(result.status, "passed")
                self.assertEqual(result.findings, ())
                self.assertEqual(result.validator_errors, ())

        nested_cases = (
            ("> -     ", ">       "),
            ("- > -     ", "  >       "),
            ("> > 1.     ", "> >        "),
            ("- 1.     ", "         "),
        )
        for opening_prefix, continuation in nested_cases:
            with self.subTest(nested=opening_prefix):
                source = (
                    f"{opening_prefix}```text\n"
                    f"{continuation}[inside]: https://source.example\n"
                    f"{continuation}```not-a-close\n"
                    f"{continuation}[inside]\n"
                    f"{continuation}```\n\n"
                    "[inside]: https://shared.example\n"
                    "[active]: https://active.example\nOpen [active].\n"
                )
                candidate = (
                    f"{opening_prefix}```translated\n"
                    f"{continuation}[changed]: https://candidate.example\n"
                    f"{continuation}```still-not-a-close\n"
                    f"{continuation}[changed]\n"
                    f"{continuation}```\n\n"
                    "[inside]: https://shared.example\n"
                    "[active]: https://active.example\nAbra [active].\n"
                )

                result = validate_output(
                    case_with_checks(source=source, checks=check),
                    candidate,
                )
                self.assertEqual(result.status, "passed")
                self.assertEqual(result.findings, ())
                self.assertEqual(result.validator_errors, ())

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
        self.assertTrue(all(record["applicable_checks"] == 2 for record in records))
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
