from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVARIANTS = ROOT / "skills/reviewing-translations/scripts/invariants.py"


def load_invariants():
    spec = importlib.util.spec_from_file_location("review_invariants", INVARIANTS)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load reviewing-translations invariants")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


invariants = load_invariants()
validate_check_declaration = invariants.validate_check_declaration
validate_invariants = invariants.validate_invariants


class ReviewInvariantTests(unittest.TestCase):
    def validate(self, *, source: str, candidate: str, checks: tuple[dict, ...], **overrides):
        return validate_invariants(
            source=source,
            candidate=candidate,
            source_locale=overrides.get("source_locale", "en"),
            target_locale=overrides.get("target_locale", "fr-FR"),
            protected_terms=overrides.get("protected_terms", ()),
            checks=checks,
        )

    def test_findings_preserve_declared_check_order_and_public_fields(self):
        """Break: installed validation could reorder checks or lose portable finding data."""
        findings = self.validate(
            source="Pay {amount} at [support](https://example.test/help)",
            candidate="Pague em [suporte](https://example.test/other)",
            checks=(
                {"type": "placeholder_multiset", "severity": "critical"},
                {"type": "url_multiset", "severity": "critical"},
            ),
        )

        self.assertEqual(
            [finding.check for finding in findings],
            ["placeholder_multiset", "url_multiset"],
        )
        self.assertEqual([finding.severity for finding in findings], ["critical", "critical"])
        self.assertEqual(
            [finding.message for finding in findings],
            [
                "placeholder_multiset differs from the declared source invariant",
                "url_multiset differs from the declared source invariant",
            ],
        )
        self.assertEqual(findings[0].span, None)
        self.assertEqual(findings[1].span, (19, 45))
        self.assertEqual(
            tuple(field.name for field in fields(invariants.ValidationFinding)),
            ("check", "severity", "message", "span"),
        )

    def test_scalar_and_limit_declarations_run_through_installed_engine(self):
        """Break: extracting scalar or limit checks could leave a declared family undispatched."""
        fixtures = (
            ("placeholder_multiset", "Keep {name}", "Keep {other}", {}),
            ("format_specifier_multiset", "Keep %1$s", "Keep %s", {}),
            ("url_multiset", "See https://example.test/a", "See https://example.test/b", {}),
            ("email_multiset", "Mail team@example.test", "Mail help@example.test", {}),
            ("code_span_multiset", "Run `tool sync`", "Run `tool push`", {}),
            (
                "command_multiset", "Run tool sync", "Run tool push",
                {"values": ["tool sync"]},
            ),
            (
                "identifier_multiset", "Open AccountID", "Ouvrez accountId",
                {"values": ["AccountID"]},
            ),
            ("protected_term_multiset", "Use API", "Use Api", {}),
            (
                "number_multiset", "Value 1,234.50", "Value 1 235,50",
                {
                    "source_decimal_separator": ".",
                    "source_grouping_separator": ",",
                    "target_decimal_separator": ",",
                    "target_grouping_separator": " ",
                },
            ),
            ("character_limit", "Short", "123456789", {"max": 8}),
            ("line_count", "One line", "One\nTwo", {"count": 1}),
        )
        for check_type, source, candidate, options in fixtures:
            with self.subTest(check=check_type):
                findings = self.validate(
                    source=source,
                    candidate=candidate,
                    protected_terms=("API",),
                    target_locale="pt-PT" if check_type == "number_multiset" else "fr-FR",
                    checks=({"type": check_type, "severity": "major", **options},),
                )
                self.assertEqual([finding.check for finding in findings], [check_type])

    def test_number_multiset_uses_explicit_language_neutral_formats(self):
        """Break: number equivalence could depend on hard-coded language locale tables."""
        formats = (
            (
                "fr-FR",
                "de-DE",
                "Total 1\u202f234,50",
                "Summe 1.234,50",
                {
                    "source_decimal_separator": ",",
                    "source_grouping_separator": "\u202f",
                    "target_decimal_separator": ",",
                    "target_grouping_separator": ".",
                },
            ),
            (
                "de-DE",
                "fr-FR",
                "Summe 1.234,50",
                "Total 1\u202f234,50",
                {
                    "source_decimal_separator": ",",
                    "source_grouping_separator": ".",
                    "target_decimal_separator": ",",
                    "target_grouping_separator": "\u202f",
                },
            ),
            (
                "zz-Latn-ZZ",
                "qaa-Zzzz-001",
                "Value 1'234.50",
                "Value 1_234:50",
                {
                    "source_decimal_separator": ".",
                    "source_grouping_separator": "'",
                    "target_decimal_separator": ":",
                    "target_grouping_separator": "_",
                },
            ),
        )
        for source_locale, target_locale, source, candidate, declaration in formats:
            with self.subTest(source_locale=source_locale, target_locale=target_locale):
                try:
                    findings = self.validate(
                        source=source,
                        candidate=candidate,
                        source_locale=source_locale,
                        target_locale=target_locale,
                        checks=({
                            "type": "number_multiset",
                            "severity": "critical",
                            **declaration,
                        },),
                    )
                except ValueError as error:
                    self.fail(str(error))
                self.assertEqual(findings, ())

    def test_number_multiset_defaults_only_for_unambiguous_forms(self):
        """Break: an unknown locale could fail safe integer/date checks or guess punctuation."""
        try:
            findings = self.validate(
                source="Due 01/11/2026 at 30%",
                candidate="Due 01/11/2026 at 30 %",
                source_locale="fr-FR",
                target_locale="de-DE",
                checks=({"type": "number_multiset", "severity": "critical"},),
            )
        except ValueError as error:
            self.fail(str(error))
        self.assertEqual(findings, ())

        with self.assertRaisesRegex(ValueError, "explicit separators.*ambiguous"):
            self.validate(
                source="Value 1,234",
                candidate="Value 1.234",
                source_locale="fr-FR",
                target_locale="de-DE",
                checks=({"type": "number_multiset", "severity": "critical"},),
            )

    def test_number_multiset_explicit_format_declaration_is_exact(self):
        """Break: partial or ambiguous numeric format declarations could be guessed."""
        valid = {
            "type": "number_multiset",
            "severity": "critical",
            "source_decimal_separator": ".",
            "source_grouping_separator": ",",
            "target_decimal_separator": ",",
            "target_grouping_separator": ".",
        }
        try:
            validate_check_declaration(valid)
        except ValueError as error:
            self.fail(str(error))

        invalid = (
            {key: value for key, value in valid.items() if key != "target_grouping_separator"},
            {**valid, "source_decimal_separator": 1},
            {**valid, "source_decimal_separator": ".", "source_grouping_separator": "."},
            {**valid, "target_grouping_separator": ".."},
        )
        for declaration in invalid:
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                validate_check_declaration(declaration)

    def test_declared_literal_line_and_embedded_contracts_run(self):
        """Break: extracting configured contracts could drop their declared options or dispatch."""
        fixtures = (
            (
                "literal_multiset", "PREFIX: keep", "Keep",
                {"values": ["PREFIX:"]},
            ),
            (
                "literal_mapping_multiset", "Use source-token", "Use source-token",
                {
                    "mappings": [{"source": "source-token", "targets": ["target-token"]}],
                    "require_source_absence": True,
                },
            ),
            (
                "line_character_limits", "12345\n12345678901", "123456\n123456789012",
                {"maxima": [5, 11]},
            ),
            (
                "delimited_fields", "one,two,three", "one,two,two",
                {"delimiter": ",", "count": 3, "allow_whitespace": False, "unique": True},
            ),
            (
                "json_line_contract",
                'Response:\n{"status":"ready","message":"Welcome"}',
                'Response:\n{"state":"ready","message":"Target"}',
                {"line": 2, "translatable_keys": ["message"]},
            ),
        )
        for check_type, source, candidate, options in fixtures:
            with self.subTest(check=check_type):
                findings = self.validate(
                    source=source,
                    candidate=candidate,
                    checks=({"type": check_type, "severity": "critical", **options},),
                )
                self.assertEqual([finding.check for finding in findings], [check_type])

    def test_structured_and_locale_declarations_run_through_installed_engine(self):
        """Break: extracting parsers could stop structural or declared locale-form checks."""
        fixtures = (
            ("json_structure", '{"items":[{"id":1}]}', '{"items":{"id":1}}', {}),
            ("xml_structure", "<screen><title>Source</title></screen>", "<screen>Target</screen>", {}),
            ("html_structure", "<section><strong>Source</strong></section>", "<section>Target</section>", {}),
            ("markdown_structure", "# Heading\n\n- Item\n", "## Heading\n\nItem\n", {}),
            (
                "fenced_block_exact", "Run:\n```sh\ntool sync\n```",
                "Run:\n```sh\ntool push\n```", {},
            ),
            ("csv_shape", "name,status\nAda,Active\n", "nom,état\nAda,Active,extra\n", {}),
            (
                "icu_topology", "{count, plural, one {item} other {items}}",
                "{count, plural, other {items}}", {},
            ),
            (
                "forbidden_locale_form", "Use allowed-form", "Use blocked-form",
                {"forms": ["blocked-form"]},
            ),
        )
        for check_type, source, candidate, options in fixtures:
            with self.subTest(check=check_type):
                findings = self.validate(
                    source=source,
                    candidate=candidate,
                    checks=({"type": check_type, "severity": "critical", **options},),
                )
                self.assertEqual([finding.check for finding in findings], [check_type])

    def test_declaration_errors_remain_public_and_exact(self):
        """Break: callers could silently accept unknown checks after the validator moved."""
        with self.assertRaisesRegex(ValueError, "unknown automatic check: 'placeholders'"):
            validate_check_declaration({"type": "placeholders", "severity": "critical"})


if __name__ == "__main__":
    unittest.main()
