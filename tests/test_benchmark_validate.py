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
from scripts.benchmark.validate import CHECKS, validate_output, validate_runs


def case_with_checks(*, source: str, checks: list[dict], **overrides: object) -> dict:
    case = {
        "id": "case-1",
        "source": source,
        "automatic_checks": checks,
        "protected_terms": [],
    }
    case.update(overrides)
    return case


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
            records.append({
                "schema_version": 1,
                "run_id": run_id,
                "case_id": case_id,
                "output_sha256": digest,
                "raw_output_path": f"raw/{digest}.txt",
            })
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
