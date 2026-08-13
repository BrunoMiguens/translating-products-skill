from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.benchmark.common import BenchmarkError
from scripts.benchmark import product_review
from scripts.benchmark.product_review import (
    HUMAN_COLUMNS,
    HUMAN_SOURCE_COLUMNS,
    REQUIRED_COLUMNS,
    prepare_packet,
)


AUTOMATED_ROWS = (
    ("locale-a", "key.one", "Source one", "Current one", "change_recommended", "Reason one", "Correction one"),
    ("locale-b", "key.two", "Source two", "Current two", "change_recommended", "Reason two", "Correction two"),
    ("locale-a", "key.three", "Source three", "Current three", "no_issue_detected", "", ""),
)


def write_csv(path: Path, columns: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> bytes:
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)
    return path.read_bytes()


class ProductReviewPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.input_csv = self.root / "automated-review.csv"
        self.input_bytes = write_csv(self.input_csv, REQUIRED_COLUMNS, AUTOMATED_ROWS)
        self.output_dir = self.root / "private-packet"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepare_preserves_baseline_and_creates_blank_human_curation(self):
        """Break: preparation could leak automated judgments or invent human sign-off."""
        manifest = prepare_packet(
            self.input_csv,
            self.output_dir,
            suite_git_object="13cf73e",
        )

        baseline = self.output_dir / "conditions" / "current-suite.csv"
        human = self.output_dir / "human-review.csv"
        self.assertEqual(baseline.read_bytes(), self.input_bytes)
        with human.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            human_rows = list(reader)
            self.assertEqual(
                tuple(reader.fieldnames or ()),
                HUMAN_SOURCE_COLUMNS + HUMAN_COLUMNS,
            )
        self.assertEqual(
            [tuple(row[column] for column in HUMAN_SOURCE_COLUMNS) for row in human_rows],
            [row[:4] for row in AUTOMATED_ROWS],
        )
        self.assertTrue(all(not row[column] for row in human_rows for column in HUMAN_COLUMNS))
        self.assertTrue(
            all(
                forbidden not in (human_rows[0] if human_rows else {})
                for forbidden in ("status", "reason", "recommended_correction")
            )
        )
        self.assertEqual(manifest["human_status"], "pending")
        self.assertNotIn("human_signoff", manifest)
        self.assertEqual(manifest["suite_git_object"], "13cf73e")
        self.assertEqual(manifest["row_count"], 3)
        self.assertEqual(manifest["locales"], ["locale-a", "locale-b"])
        self.assertEqual(manifest["source_columns"], list(HUMAN_SOURCE_COLUMNS))
        self.assertEqual(
            manifest["files"]["conditions/current-suite.csv"],
            hashlib.sha256(self.input_bytes).hexdigest(),
        )
        self.assertEqual(
            manifest["files"]["human-review.csv"],
            hashlib.sha256(human.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            json.loads((self.output_dir / "packet-manifest.json").read_text(encoding="utf-8")),
            manifest,
        )

    def test_prepare_refuses_duplicate_identities_and_malformed_rows(self):
        """Break: duplicate or shifted CSV rows could make identities ambiguous."""
        duplicate = self.root / "duplicate.csv"
        write_csv(duplicate, REQUIRED_COLUMNS, AUTOMATED_ROWS + (AUTOMATED_ROWS[0],))
        with self.assertRaisesRegex(BenchmarkError, "duplicate"):
            prepare_packet(duplicate, self.output_dir, suite_git_object="13cf73e")

        malformed = self.root / "malformed.csv"
        malformed.write_text(
            ",".join(REQUIRED_COLUMNS) + "\n" + ",".join(AUTOMATED_ROWS[0][:-1]) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BenchmarkError, "malformed"):
            prepare_packet(malformed, self.output_dir, suite_git_object="13cf73e")

        whitespace = self.root / "whitespace.csv"
        write_csv(
            whitespace,
            REQUIRED_COLUMNS,
            (("locale-a", "   ", *AUTOMATED_ROWS[0][2:]),),
        )
        with self.assertRaisesRegex(BenchmarkError, "blank"):
            prepare_packet(whitespace, self.output_dir, suite_git_object="13cf73e")

    def test_prepare_refuses_existing_output_symlinks_aliases_and_overwrite(self):
        """Break: output aliases could overwrite inputs or redirect private product bytes."""
        self.output_dir.mkdir()
        with self.assertRaisesRegex(BenchmarkError, "already exists"):
            prepare_packet(self.input_csv, self.output_dir, suite_git_object="13cf73e")

        self.output_dir.rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        os.symlink(outside, self.output_dir)
        with self.assertRaisesRegex(BenchmarkError, "symlink"):
            prepare_packet(self.input_csv, self.output_dir, suite_git_object="13cf73e")

        self.output_dir.unlink()
        alias = self.root / "alias.csv"
        os.link(self.input_csv, alias)
        with self.assertRaisesRegex(BenchmarkError, "alias"):
            prepare_packet(alias, alias, suite_git_object="13cf73e")

        tracked = Path(__file__).resolve().parents[1] / "benchmarks" / "private-product-data"
        with self.assertRaisesRegex(BenchmarkError, "tracked benchmarks"):
            prepare_packet(self.input_csv, tracked, suite_git_object="13cf73e")

        tracked_docs = Path(__file__).resolve().parents[1] / "docs" / "private-product-data"
        with self.assertRaisesRegex(BenchmarkError, "benchmark-private"):
            prepare_packet(self.input_csv, tracked_docs, suite_git_object="13cf73e")

        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        parent_link = self.root / "parent-link"
        os.symlink(real_parent, parent_link)
        with self.assertRaisesRegex(BenchmarkError, "symlink"):
            prepare_packet(
                self.input_csv,
                parent_link / "packet",
                suite_git_object="13cf73e",
            )

        input_link = self.root / "input-link"
        os.symlink(self.root, input_link)
        with self.assertRaisesRegex(BenchmarkError, "symlink"):
            prepare_packet(
                input_link / self.input_csv.name,
                self.output_dir,
                suite_git_object="13cf73e",
            )

    def test_prepare_does_not_replace_a_concurrent_output_directory(self):
        """Break: publication could replace an output claimed after validation."""
        real_rename = product_review._rename_noreplace

        def claim_then_rename(parent_fd, source, destination):
            os.mkdir(destination, dir_fd=parent_fd)
            return real_rename(parent_fd, source, destination)

        with mock.patch(
            "scripts.benchmark.product_review._rename_noreplace",
            claim_then_rename,
        ):
            with self.assertRaisesRegex(BenchmarkError, "already exists"):
                prepare_packet(
                    self.input_csv,
                    self.output_dir,
                    suite_git_object="13cf73e",
                )
        self.assertTrue(self.output_dir.is_dir())
        self.assertEqual(list(self.output_dir.iterdir()), [])

    def test_prepare_creates_only_the_missing_ignored_private_root(self):
        """Break: the documented fresh-checkout command could require manual mkdir."""
        repository = self.root / "repository"
        repository.mkdir()
        (repository / "benchmarks").mkdir()
        output = repository / "benchmark-private" / "packet"

        with mock.patch.object(product_review, "_REPOSITORY_ROOT", repository):
            prepare_packet(self.input_csv, output, suite_git_object="13cf73e")
            self.assertTrue(output.is_dir())

            nested = repository / "benchmark-private" / "missing" / "packet"
            with self.assertRaisesRegex(BenchmarkError, "directory"):
                prepare_packet(self.input_csv, nested, suite_git_object="13cf73e")
        self.assertFalse((repository / "benchmark-private" / "missing").exists())

    def test_prepare_fails_closed_if_post_publish_directory_fsync_fails(self):
        """Break: a visible but non-durable packet could be reported as successful."""
        real_fsync = os.fsync
        real_publish = product_review._rename_noreplace
        published = False

        def publish(parent_fd, source, destination):
            nonlocal published
            real_publish(parent_fd, source, destination)
            published = True

        def fail_after_publish(descriptor):
            if published:
                raise OSError("injected post-publish fsync failure")
            return real_fsync(descriptor)

        with mock.patch(
            "scripts.benchmark.product_review._rename_noreplace", side_effect=publish
        ), mock.patch(
            "scripts.benchmark.product_review.os.fsync", side_effect=fail_after_publish
        ):
            with self.assertRaisesRegex(BenchmarkError, "publish"):
                prepare_packet(
                    self.input_csv,
                    self.output_dir,
                    suite_git_object="13cf73e",
                )
        self.assertTrue(self.output_dir.is_dir())

    def test_prepare_rollback_never_moves_a_replacement_after_identity_check(self):
        """Break: rollback could move and delete a directory installed after stat."""
        real_fsync = os.fsync
        real_publish = product_review._rename_noreplace
        real_rename = os.rename
        real_stat = os.stat
        published = False
        output_stats = 0
        replacement_installed = False
        displaced = self.root / "displaced-packet"

        def publish(parent_fd, source, destination):
            nonlocal published
            real_publish(parent_fd, source, destination)
            published = True

        def fail_after_publish(descriptor):
            if published:
                raise OSError("injected post-publish fsync failure")
            return real_fsync(descriptor)

        def replace_after_rollback_observation(path, *args, **kwargs):
            nonlocal output_stats, replacement_installed
            metadata = real_stat(path, *args, **kwargs)
            if published and path == self.output_dir.name:
                output_stats += 1
                if output_stats == 2:
                    real_rename(self.output_dir, displaced)
                    self.output_dir.mkdir()
                    (self.output_dir / "replacement-marker").write_text(
                        "replacement", encoding="utf-8"
                    )
                    replacement_installed = True
            return metadata

        with mock.patch(
            "scripts.benchmark.product_review._rename_noreplace", side_effect=publish
        ), mock.patch(
            "scripts.benchmark.product_review.os.fsync", side_effect=fail_after_publish
        ), mock.patch(
            "scripts.benchmark.product_review.os.stat",
            side_effect=replace_after_rollback_observation,
        ):
            with self.assertRaisesRegex(BenchmarkError, "publish"):
                prepare_packet(
                    self.input_csv,
                    self.output_dir,
                    suite_git_object="13cf73e",
                )

        self.assertTrue(replacement_installed)
        self.assertEqual(
            (self.output_dir / "replacement-marker").read_text(encoding="utf-8"),
            "replacement",
        )

    def test_prepare_close_failure_after_publication_does_not_negate_success(self):
        """Break: descriptor cleanup could raise after a valid packet became visible."""
        real_close = os.close

        def close_then_fail(descriptor):
            real_close(descriptor)
            if self.output_dir.exists():
                raise OSError("injected close failure")

        with mock.patch(
            "scripts.benchmark.product_review.os.close", side_effect=close_then_fail
        ):
            manifest = prepare_packet(
                self.input_csv,
                self.output_dir,
                suite_git_object="13cf73e",
            )
        self.assertEqual(
            json.loads((self.output_dir / "packet-manifest.json").read_text()),
            manifest,
        )

    def test_prepare_cli_creates_no_signoff_file(self):
        """Break: the convenience CLI could silently extend preparation authority."""
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.benchmark.product_review",
                "prepare",
                "--input-csv",
                str(self.input_csv),
                "--output-dir",
                str(self.output_dir),
                "--suite-git-object",
                "13cf73e",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(any("signoff" in path.name.lower() for path in self.output_dir.rglob("*")))


class ProductReviewScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.input_csv = self.root / "automated-review.csv"
        write_csv(self.input_csv, REQUIRED_COLUMNS, AUTOMATED_ROWS)
        self.packet = self.root / "packet"
        prepare_packet(self.input_csv, self.packet, suite_git_object="13cf73e")
        self.human = self.packet / "human-review.csv"
        self.human_rows = (
            AUTOMATED_ROWS[0][:4] + ("change_required", "Correction one", "Human note one", "critical"),
            AUTOMATED_ROWS[1][:4] + ("change_required", "Human correction two", "Human note two", "minor"),
            AUTOMATED_ROWS[2][:4] + ("no_issue_detected", "", "", ""),
        )
        write_csv(self.human, HUMAN_SOURCE_COLUMNS + HUMAN_COLUMNS, self.human_rows)

        self.normal = self.root / "normal.csv"
        self.improved = self.root / "improved.csv"
        self.current = self.packet / "conditions" / "current-suite.csv"
        normal_rows = (
            AUTOMATED_ROWS[0],
            AUTOMATED_ROWS[1][:4] + ("no_issue_detected", "", ""),
            AUTOMATED_ROWS[2][:4] + ("change_recommended", "False positive", "Unwanted correction"),
        )
        improved_rows = (
            AUTOMATED_ROWS[0],
            AUTOMATED_ROWS[1][:4] + ("change_recommended", "Better reason", "Human correction two"),
            AUTOMATED_ROWS[2],
        )
        write_csv(self.normal, REQUIRED_COLUMNS, normal_rows)
        write_csv(self.improved, REQUIRED_COLUMNS, improved_rows)
        self.output = self.packet / "score.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def candidate_specs(self) -> list[str]:
        return [
            f"normal={self.normal}",
            f"current_suite={self.current}",
            f"improved={self.improved}",
        ]

    def test_score_uses_one_human_file_for_three_condition_confusion_matrices(self):
        """Break: conditions could use inconsistent references or incompatible metric meanings."""
        result = product_review.score_packet(
            self.packet,
            self.candidate_specs(),
            self.output,
        )

        normal = result["conditions"]["normal"]
        current = result["conditions"]["current_suite"]
        improved = result["conditions"]["improved"]
        self.assertEqual(
            normal["confusion_matrix"],
            {"true_positive": 1, "false_positive": 1, "true_negative": 0, "false_negative": 1},
        )
        self.assertEqual(
            current["confusion_matrix"],
            {"true_positive": 2, "false_positive": 0, "true_negative": 1, "false_negative": 0},
        )
        self.assertEqual(improved["confusion_matrix"], current["confusion_matrix"])
        self.assertEqual(normal["required_error_recall"], 0.5)
        self.assertEqual(normal["reported_error_precision"], 0.5)
        self.assertEqual(normal["false_positive_correction_rate"], 1.0)
        self.assertEqual(normal["correction_success_rate"], 0.5)
        self.assertEqual(current["correction_success_rate"], 0.5)
        self.assertEqual(improved["correction_success_rate"], 1.0)
        self.assertEqual(
            result["differences_percentage_points"]["improved_minus_normal"],
            {
                "required_error_recall": 50.0,
                "reported_error_precision": 50.0,
                "false_positive_correction_rate": -100.0,
                "correction_success_rate": 50.0,
            },
        )
        self.assertEqual(
            result["differences_percentage_points"]["improved_minus_current_suite"],
            {
                "required_error_recall": 0.0,
                "reported_error_precision": 0.0,
                "false_positive_correction_rate": 0.0,
                "correction_success_rate": 50.0,
            },
        )
        self.assertEqual(current["correction_disagreements"][0]["human_preference"], None)
        self.assertNotIn("preferred_correction", current["correction_disagreements"][0])
        self.assertEqual(
            result["human_review_sha256"],
            hashlib.sha256(self.human.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            json.loads(self.output.read_text(encoding="utf-8")),
            result,
        )

    def test_score_rejects_incomplete_or_invalid_human_rows(self):
        """Break: blank, contradictory, or severity-free human rows could become labels."""
        invalid_rows = (
            ("blank decision", self.human_rows[:2] + (AUTOMATED_ROWS[2][:4] + ("", "", "", ""),), "decision"),
            ("unknown decision", self.human_rows[:2] + (AUTOMATED_ROWS[2][:4] + ("maybe", "", "", ""),), "decision"),
            ("missing correction", (AUTOMATED_ROWS[0][:4] + ("change_required", "", "", "critical"),) + self.human_rows[1:], "correction"),
            ("blank correction", (AUTOMATED_ROWS[0][:4] + ("change_required", "   ", "", "critical"),) + self.human_rows[1:], "correction"),
            ("unknown severity", (AUTOMATED_ROWS[0][:4] + ("change_required", "Correction one", "", "cosmetic"),) + self.human_rows[1:], "severity"),
            ("negative has correction", self.human_rows[:2] + (AUTOMATED_ROWS[2][:4] + ("no_issue_detected", "Not blank", "", ""),), "no_issue_detected"),
        )
        for label, rows, message in invalid_rows:
            with self.subTest(label):
                write_csv(self.human, HUMAN_SOURCE_COLUMNS + HUMAN_COLUMNS, rows)
                with self.assertRaisesRegex(BenchmarkError, message):
                    product_review.score_packet(self.packet, self.candidate_specs(), self.output)

    def test_score_rejects_changed_packet_bytes_and_source_mismatch(self):
        """Break: a prepared baseline or product identity could drift after curation."""
        original = self.current.read_bytes()
        self.current.write_bytes(original + b"\n")
        with self.assertRaisesRegex(BenchmarkError, "hash mismatch"):
            product_review.score_packet(self.packet, self.candidate_specs(), self.output)

        self.current.write_bytes(original)
        mismatched = list(csv.reader(self.improved.read_text(encoding="utf-8").splitlines()))
        mismatched[1][2] = "Different source"
        with self.improved.open("w", encoding="utf-8", newline="") as target:
            csv.writer(target, lineterminator="\n").writerows(mismatched)
        with self.assertRaisesRegex(BenchmarkError, "source fields"):
            product_review.score_packet(self.packet, self.candidate_specs(), self.output)

    def test_score_rejects_baseline_mutation_between_validation_and_scoring(self):
        """Break: a second baseline read could score bytes that were never hash-validated."""
        original_parser = product_review._parse_automated_csv
        calls = 0

        def mutate_after_baseline(raw, path):
            nonlocal calls
            result = original_parser(raw, path)
            if Path(path) == self.current:
                calls += 1
                if calls == 1:
                    changed = (
                        AUTOMATED_ROWS[0][:4] + ("no_issue_detected", "", ""),
                        AUTOMATED_ROWS[1],
                        AUTOMATED_ROWS[2],
                    )
                    write_csv(self.current, REQUIRED_COLUMNS, changed)
            return result

        with mock.patch(
            "scripts.benchmark.product_review._parse_automated_csv",
            side_effect=mutate_after_baseline,
        ):
            with self.assertRaisesRegex(BenchmarkError, "changed during scoring"):
                product_review.score_packet(self.packet, self.candidate_specs(), self.output)

    def test_score_requires_exact_unique_conditions_files_and_output(self):
        """Break: missing, duplicated, aliased, or overwritten conditions could be mislabeled."""
        with self.assertRaisesRegex(BenchmarkError, "required conditions"):
            product_review.score_packet(self.packet, self.candidate_specs()[:-1], self.output)
        with self.assertRaisesRegex(BenchmarkError, "duplicate condition"):
            product_review.score_packet(
                self.packet,
                self.candidate_specs() + [f"normal={self.normal}"],
                self.output,
            )

        alias = self.root / "improved-alias.csv"
        os.link(self.normal, alias)
        specs = self.candidate_specs()
        specs[-1] = f"improved={alias}"
        with self.assertRaisesRegex(BenchmarkError, "alias"):
            product_review.score_packet(self.packet, specs, self.output)

        current_alias = self.root / "current-suite-alias.csv"
        os.link(self.current, current_alias)
        specs = self.candidate_specs()
        specs[1] = f"current_suite={current_alias}"
        with self.assertRaisesRegex(BenchmarkError, "preserved packet baseline"):
            product_review.score_packet(self.packet, specs, self.output)

        symlink = self.root / "improved-link.csv"
        os.symlink(self.improved, symlink)
        specs[-1] = f"improved={symlink}"
        with self.assertRaisesRegex(BenchmarkError, "symlink"):
            product_review.score_packet(self.packet, specs, self.output)

        self.output.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(BenchmarkError, "already exists"):
            product_review.score_packet(self.packet, self.candidate_specs(), self.output)

    def test_score_refuses_repository_packet_and_candidates_outside_private_root(self):
        """Break: scoring inputs under tracked repository paths could ingest product data."""
        repository = self.root / "repository"
        (repository / "benchmarks").mkdir(parents=True)
        (repository / "docs").mkdir()
        tracked_packet = repository / "benchmarks" / "packet"
        shutil.copytree(self.packet, tracked_packet)
        tracked_specs = [
            f"normal={self.normal}",
            f"current_suite={tracked_packet / 'conditions' / 'current-suite.csv'}",
            f"improved={self.improved}",
        ]
        with mock.patch.object(product_review, "_REPOSITORY_ROOT", repository):
            with self.assertRaisesRegex(BenchmarkError, "benchmark-private"):
                product_review.score_packet(tracked_packet, tracked_specs, self.output)

            tracked_normal = repository / "docs" / "normal.csv"
            shutil.copyfile(self.normal, tracked_normal)
            candidate_specs = self.candidate_specs()
            candidate_specs[0] = f"normal={tracked_normal}"
            with self.assertRaisesRegex(BenchmarkError, "benchmark-private"):
                product_review.score_packet(self.packet, candidate_specs, self.output)

    def test_score_fails_closed_if_post_publish_fsync_or_final_input_check_fails(self):
        """Break: score publication could accept non-durable output or mutated input bytes."""
        real_fsync = os.fsync
        real_link = os.link
        published = False

        def link_then_mark(*args, **kwargs):
            nonlocal published
            real_link(*args, **kwargs)
            published = True

        def fail_after_publish(descriptor):
            if published:
                raise OSError("injected post-publish fsync failure")
            return real_fsync(descriptor)

        with mock.patch(
            "scripts.benchmark.product_review.os.link", side_effect=link_then_mark
        ), mock.patch(
            "scripts.benchmark.product_review.os.fsync", side_effect=fail_after_publish
        ):
            with self.assertRaisesRegex(BenchmarkError, "publish"):
                product_review.score_packet(
                    self.packet, self.candidate_specs(), self.output
                )
        self.assertTrue(self.output.is_file())
        self.output.unlink()

        published = False

        def link_then_mutate(*args, **kwargs):
            nonlocal published
            real_link(*args, **kwargs)
            published = True
            self.human.write_bytes(self.human.read_bytes() + b"\n")

        with mock.patch(
            "scripts.benchmark.product_review.os.link", side_effect=link_then_mutate
        ):
            with self.assertRaisesRegex(BenchmarkError, "changed during scoring"):
                product_review.score_packet(
                    self.packet, self.candidate_specs(), self.output
                )
        self.assertTrue(self.output.is_file())

    def test_score_rollback_never_unlinks_a_replacement_after_identity_check(self):
        """Break: rollback could unlink a score installed after its inode stat."""
        real_fsync = os.fsync
        real_link = os.link
        real_stat = os.stat
        published = False
        output_stats = 0
        replacement_installed = False
        displaced = self.root / "displaced-score.json"

        def link_then_mark(*args, **kwargs):
            nonlocal published
            real_link(*args, **kwargs)
            published = True

        def fail_after_publish(descriptor):
            if published:
                raise OSError("injected post-publish fsync failure")
            return real_fsync(descriptor)

        def replace_after_rollback_observation(path, *args, **kwargs):
            nonlocal output_stats, replacement_installed
            metadata = real_stat(path, *args, **kwargs)
            if published and path == self.output.name:
                output_stats += 1
                if output_stats == 2:
                    os.replace(self.output, displaced)
                    self.output.write_text("replacement", encoding="utf-8")
                    replacement_installed = True
            return metadata

        with mock.patch(
            "scripts.benchmark.product_review.os.link", side_effect=link_then_mark
        ), mock.patch(
            "scripts.benchmark.product_review.os.fsync", side_effect=fail_after_publish
        ), mock.patch(
            "scripts.benchmark.product_review.os.stat",
            side_effect=replace_after_rollback_observation,
        ):
            with self.assertRaisesRegex(BenchmarkError, "publish"):
                product_review.score_packet(
                    self.packet, self.candidate_specs(), self.output
                )

        self.assertTrue(replacement_installed)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "replacement")

    def test_score_close_failure_after_publication_does_not_negate_success(self):
        """Break: descriptor cleanup could raise after a valid score became visible."""
        real_close = os.close

        def close_then_fail(descriptor):
            real_close(descriptor)
            if self.output.exists():
                raise OSError("injected close failure")

        with mock.patch(
            "scripts.benchmark.product_review.os.close", side_effect=close_then_fail
        ):
            result = product_review.score_packet(
                self.packet,
                self.candidate_specs(),
                self.output,
            )
        self.assertEqual(json.loads(self.output.read_text()), result)

    def test_score_cli_requires_all_three_named_conditions(self):
        """Break: CLI parsing could bypass the same mandatory-condition boundary."""
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.benchmark.product_review",
                "score",
                "--packet-dir",
                str(self.packet),
                *(argument for spec in self.candidate_specs() for argument in ("--candidate", spec)),
                "--output",
                str(self.output),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(self.output.is_file())

    def test_score_cli_rejects_malformed_manifest_without_traceback(self):
        """Break: hostile manifest locale types could escape as an uncaught TypeError."""
        manifest_path = self.packet / "packet-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["locales"] = [{}]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.benchmark.product_review",
                "score",
                "--packet-dir",
                str(self.packet),
                *(argument for spec in self.candidate_specs() for argument in ("--candidate", spec)),
                "--output",
                str(self.output),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("manifest locales", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_score_cleanup_failure_does_not_negate_exclusive_publication(self):
        """Break: staging cleanup failure could report failure after score publication."""
        real_unlink = os.unlink

        def fail_staging_cleanup(path, *args, **kwargs):
            if str(path).startswith(".score.json.tmp-"):
                raise PermissionError("injected staging cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch(
            "scripts.benchmark.product_review.os.unlink",
            side_effect=fail_staging_cleanup,
        ):
            result = product_review.score_packet(
                self.packet,
                self.candidate_specs(),
                self.output,
            )

        self.assertEqual(
            json.loads(self.output.read_text(encoding="utf-8")),
            result,
        )


if __name__ == "__main__":
    unittest.main()
