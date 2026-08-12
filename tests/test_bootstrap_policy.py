import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "skills/translating-products/scripts/policy.py"
ASSETS = ROOT / "skills/translating-products/assets/translation-project"
EVALS = ROOT / "evals/bootstrap-cases.json"
CONTEXT_FILES = (
    "project-brief.md",
    "locales.yaml",
    "glossary.csv",
    "style-guide.md",
    "protected-terms.txt",
)
DRAFT_TERMINOLOGY_HEADER = (
    "source_term,target_term,locale,domain,context,status,provenance,alternatives\n"
)

PROJECT_BRIEF = """# Translation project brief

Status: approved

## Product

- Name: Atlas
- Summary: Consumer account application
- Audience: Adults in France and Japan

## Scope

- Source content: Product interface and help content
- Surfaces: Web and mobile
- Domains: Consumer finance
- Constraints: Preserve placeholders and links

## Authority and approvals

- Explicit requirements: Use the approved product terminology
- Approved reviewers: Product localization owner
- Approved external specialists: not applicable

## Outputs

- Formats: JSON and Markdown
- Delivery location: locale resources
- Acceptance criteria: Meaning and structure preserved
"""

STYLE_GUIDE = """# Translation style guide

Status: approved

## Voice and tone

- Voice: Clear and reassuring
- Formality: Polite and concise
- Audience relationship: Helpful expert

## Conventions

- Capitalization: Follow target-locale conventions
- Punctuation: Follow target-locale conventions
- Numbers, dates, and currency: Format at runtime
- Inclusive language: Prefer inclusive neutral wording

## Surface constraints

- Length limits: Respect supplied field limits
- Accessibility: Translate labels by function
- Formatting: Preserve markup and placeholders
"""

LOCALES = """source_locale: en-US
target_locales: [fr-FR, ja-JP]
fallback_locale: en-US
neutral_variants_allowed: false
"""

GLOSSARY = """source_term,target_term,locale,context,status,notes
account,compte,fr-FR,product noun,approved,
account,\u30a2\u30ab\u30a6\u30f3\u30c8,ja-JP,product noun,approved,
"""

PROTECTED = """# One exact, untranslated term per line.
Atlas
"""


def load_policy():
    spec = importlib.util.spec_from_file_location("bootstrap_policy", POLICY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["bootstrap_policy"] = module
    spec.loader.exec_module(module)
    return module


def write_context(translation_dir: Path) -> None:
    translation_dir.mkdir(parents=True)
    contents = {
        "project-brief.md": PROJECT_BRIEF,
        "locales.yaml": LOCALES,
        "glossary.csv": GLOSSARY,
        "style-guide.md": STYLE_GUIDE,
        "protected-terms.txt": PROTECTED,
    }
    for name, text in contents.items():
        (translation_dir / name).write_text(text, encoding="utf-8")


def approval_record(
    translation_dir: Path,
    *,
    status: str = "approved",
    approved_empty: tuple[str, ...] = (),
) -> dict:
    return {
        "status": status,
        "approved_by": "translation-owner",
        "approved_at": "2026-08-03T10:00:00Z",
        "context_sha256": {
            name: hashlib.sha256((translation_dir / name).read_bytes()).hexdigest()
            for name in CONTEXT_FILES
        },
        "approved_empty": list(approved_empty),
    }


def approve(
    translation_dir: Path,
    *,
    status: str = "approved",
    approved_empty: tuple[str, ...] = (),
) -> None:
    record = approval_record(
        translation_dir,
        status=status,
        approved_empty=approved_empty,
    )
    (translation_dir / "setup-approval.json").write_text(
        json.dumps(record, indent=2) + "\n",
        encoding="utf-8",
    )


def build_fixture(root: Path, fixture: str):
    required_names = set(CONTEXT_FILES)
    if fixture == "legacy-filenames":
        return required_names
    if fixture == "missing-directory":
        return root

    translation_dir = root / ".translation"
    if fixture == "blank-templates":
        shutil.copytree(ASSETS, translation_dir)
        return root

    write_context(translation_dir)
    if fixture == "partial-values":
        brief = PROJECT_BRIEF.replace(
            "- Audience: Adults in France and Japan", "- Audience:"
        )
        (translation_dir / "project-brief.md").write_text(brief, encoding="utf-8")
        approve(translation_dir)
    elif fixture == "draft-context":
        style = STYLE_GUIDE.replace("Status: approved", "Status: draft")
        (translation_dir / "style-guide.md").write_text(style, encoding="utf-8")
        approve(translation_dir)
    elif fixture == "unapproved-context":
        approve(translation_dir, status="draft")
    elif fixture == "malformed-approval":
        (translation_dir / "setup-approval.json").write_text(
            "{not json\n", encoding="utf-8"
        )
    elif fixture == "hash-mismatch":
        approve(translation_dir)
        (translation_dir / "style-guide.md").write_text(
            STYLE_GUIDE.replace("Clear and reassuring", "Direct and reassuring"),
            encoding="utf-8",
        )
    elif fixture in {"empty-unapproved", "empty-approved"}:
        (translation_dir / "glossary.csv").write_text(
            "source_term,target_term,locale,context,status,notes\n",
            encoding="utf-8",
        )
        (translation_dir / "protected-terms.txt").write_text(
            "# No protected terms for this project.\n",
            encoding="utf-8",
        )
        approved_empty = (
            ("glossary.csv", "protected-terms.txt")
            if fixture == "empty-approved"
            else ()
        )
        approve(translation_dir, approved_empty=approved_empty)
    elif fixture == "complete-approved":
        approve(translation_dir)
    elif fixture == "multiple-issues":
        (translation_dir / "project-brief.md").write_text(
            PROJECT_BRIEF.replace("Status: approved", "Status: draft"),
            encoding="utf-8",
        )
        (translation_dir / "locales.yaml").write_text(
            LOCALES.replace("neutral_variants_allowed: false", "neutral_variants_allowed: maybe"),
            encoding="utf-8",
        )
        approve(translation_dir)
    else:
        raise AssertionError(f"unknown fixture {fixture}")
    return root


class BootstrapPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def test_bootstrap_evaluations_inspect_real_content_and_request(self):
        cases = json.loads(EVALS.read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]), tempfile.TemporaryDirectory() as tmp:
                project = build_fixture(Path(tmp), case["fixture"])
                self.assertEqual(
                    self.policy.bootstrap_issue(project, case["request"]),
                    case["expected_issue"],
                )
                self.assertEqual(
                    self.policy.bootstrap_action(project, case["request"]),
                    case["expected_action"],
                )

    def test_approval_writer_binds_exact_bytes_of_only_context_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            optional = translation_dir / "decisions.md"
            optional.write_text("draft decision\n", encoding="utf-8")

            record = self.policy.write_setup_approval(
                root,
                approved_by="translation-owner",
                approved_at="2026-08-03T10:00:00Z",
                approved_empty=(),
            )

            self.assertEqual(set(record["context_sha256"]), set(CONTEXT_FILES))
            self.assertEqual(
                record["context_sha256"]["project-brief.md"],
                hashlib.sha256((translation_dir / "project-brief.md").read_bytes()).hexdigest(),
            )
            optional.write_text("changed optional decision\n", encoding="utf-8")
            self.assertIsNone(self.policy.bootstrap_issue(root, {}))

    def test_draft_terminology_requires_complete_draft_rows(self):
        """Break: an incomplete or approved draft term could be accepted as a record."""
        valid = (
            DRAFT_TERMINOLOGY_HEADER
            + "source,target,fr-FR,interface,short label,draft,review run,\n"
        )
        cases = {
            "valid": (valid, ()),
            "invalid-header": ("source,target\nterm,value\n", (
                "draft terminology has an invalid header",
            )),
            "blank-required": (
                DRAFT_TERMINOLOGY_HEADER
                + "source,target,fr-FR,,short label,draft,review run,\n",
                ("draft terminology row 2 has blank domain",),
            ),
            "non-draft-status": (
                DRAFT_TERMINOLOGY_HEADER
                + "source,target,fr-FR,interface,short label,approved,review run,\n",
                ("draft terminology row 2 must use draft status",),
            ),
            "extra-column": (
                DRAFT_TERMINOLOGY_HEADER
                + "source,target,fr-FR,interface,short label,draft,review run,,extra\n",
                ("draft terminology row 2 has extra columns",),
            ),
        }
        for name, (text, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.policy.validate_draft_terminology(text), expected)

    def test_draft_terminology_template_is_an_empty_valid_record(self):
        """Break: bootstrap could distribute a draft template review cannot validate."""
        template = (ASSETS / "draft-terminology.csv").read_text(encoding="utf-8")
        self.assertEqual(self.policy.validate_draft_terminology(template), ())

    def test_draft_terminology_is_optional_and_outside_approval_hashes(self):
        """Break: draft records could change a valid setup into a reapproval gate."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            approve(translation_dir)
            approval_path = translation_dir / "setup-approval.json"
            before = json.loads(approval_path.read_text(encoding="utf-8"))

            self.assertFalse((translation_dir / "draft-terminology.csv").exists())
            self.assertEqual(self.policy.bootstrap_action(root, {}), "translate")

            draft = translation_dir / "draft-terminology.csv"
            draft.write_text(
                DRAFT_TERMINOLOGY_HEADER
                + "source,target,fr-FR,interface,short label,draft,review run,\n",
                encoding="utf-8",
            )
            self.assertEqual(self.policy.bootstrap_action(root, {}), "translate")
            draft.write_text(
                DRAFT_TERMINOLOGY_HEADER
                + "source,alternative,fr-FR,interface,short label,draft,review run,\n",
                encoding="utf-8",
            )
            self.assertEqual(self.policy.bootstrap_action(root, {}), "translate")
            self.assertEqual(
                json.loads(approval_path.read_text(encoding="utf-8")), before
            )
            self.assertEqual(set(before["context_sha256"]), set(CONTEXT_FILES))

    def test_draft_terminology_does_not_promote_to_glossary_without_reapproval(self):
        """Break: a draft term could become approved glossary authority automatically."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            approve(translation_dir)
            (translation_dir / "draft-terminology.csv").write_text(
                DRAFT_TERMINOLOGY_HEADER
                + "source,target,fr-FR,interface,short label,draft,review run,\n",
                encoding="utf-8",
            )

            self.assertEqual(self.policy.bootstrap_action(root, {}), "translate")
            (translation_dir / "glossary.csv").write_text(
                "source_term,target_term,locale,context,status,notes\n"
                "source,target,fr-FR,short label,approved,manual review\n",
                encoding="utf-8",
            )
            self.assertEqual(
                self.policy.bootstrap_issue(root, {}),
                "approval-hash-mismatch:glossary.csv",
            )
            approve(translation_dir)
            self.assertEqual(self.policy.bootstrap_action(root, {}), "translate")

    def test_approval_writer_rejects_unapproved_empty_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            (translation_dir / "glossary.csv").write_text(
                "source_term,target_term,locale,context,status,notes\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "glossary.csv"):
                self.policy.write_setup_approval(
                    root,
                    approved_by="translation-owner",
                    approved_at="2026-08-03T10:00:00Z",
                    approved_empty=(),
                )

    def test_nonapproved_or_unusable_glossary_entry_fails_closed(self):
        for row in (
            "account,compte,fr-FR,product noun,draft,\n",
            ",compte,fr-FR,product noun,approved,\n",
        ):
            with self.subTest(row=row), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                translation_dir = root / ".translation"
                write_context(translation_dir)
                (translation_dir / "glossary.csv").write_text(
                    "source_term,target_term,locale,context,status,notes\n" + row,
                    encoding="utf-8",
                )
                approve(translation_dir)
                self.assertEqual(
                    self.policy.bootstrap_issue(root, {}),
                    "incomplete:glossary.csv:row-2",
                )

    def test_duplicate_approval_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            record = approval_record(translation_dir)
            payload = json.dumps(record).replace(
                '"status": "approved"',
                '"status": "approved", "status": "approved"',
                1,
            )
            (translation_dir / "setup-approval.json").write_text(
                payload, encoding="utf-8"
            )

            self.assertEqual(
                self.policy.bootstrap_issue(root, {}),
                "malformed:setup-approval.json",
            )

    def test_invalid_locales_boolean_and_missing_targets_fail_closed(self):
        mutations = {
            "invalid-boolean": (
                LOCALES.replace(
                    "neutral_variants_allowed: false",
                    "neutral_variants_allowed: maybe",
                ),
                "malformed:locales.yaml",
            ),
            "missing-targets": (
                LOCALES.replace(
                    "target_locales: [fr-FR, ja-JP]", "target_locales: []"
                ),
                "incomplete:locales.yaml:target-locales",
            ),
        }
        for name, (locales, expected) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                translation_dir = root / ".translation"
                write_context(translation_dir)
                (translation_dir / "locales.yaml").write_text(
                    locales, encoding="utf-8"
                )
                approve(translation_dir)
                self.assertEqual(self.policy.bootstrap_issue(root, {}), expected)

    def test_required_file_symlink_cannot_escape_translation_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            outside = root / "outside.md"
            outside.write_text(PROJECT_BRIEF, encoding="utf-8")
            (translation_dir / "project-brief.md").unlink()
            (translation_dir / "project-brief.md").symlink_to(outside)

            self.assertEqual(
                self.policy.bootstrap_issue(root, {}),
                "unsafe:project-brief.md",
            )

    def test_issue_question_is_one_focused_question(self):
        question = self.policy.bootstrap_question(
            "conflict:target-locale:de-DE"
        )
        self.assertEqual(question.count("?"), 1)
        self.assertIn("de-DE", question)

    def test_bootstrap_cli_inspects_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            approve(translation_dir)
            request = root / "request.json"
            request.write_text(
                json.dumps({"source_locale": "en-US", "target_locale": "fr-FR"}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(POLICY),
                    "bootstrap",
                    "--project-root",
                    str(root),
                    "--request-json",
                    str(request),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout),
                {"action": "translate", "issue": None, "question": None},
            )

    def test_independent_review_declaration_is_optional_and_strict(self):
        declarations = {
            "absent": ("", False, None),
            "true": ("\n- Independent review required: true\n", True, None),
            "false": ("\n- Independent review required: false\n", False, None),
            "duplicate": (
                "\n- Independent review required: true\n"
                "- Independent review required: false\n",
                None,
                "malformed:project-brief.md",
            ),
            "invalid": (
                "\n- Independent review required: sometimes\n",
                None,
                "malformed:project-brief.md",
            ),
        }
        for name, (declaration, expected_requirement, expected_issue) in declarations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                translation_dir = root / ".translation"
                write_context(translation_dir)
                (translation_dir / "project-brief.md").write_text(
                    PROJECT_BRIEF + declaration, encoding="utf-8"
                )
                approve(translation_dir)

                self.assertEqual(self.policy.bootstrap_issue(root, {}), expected_issue)
                if expected_issue is None:
                    requirement = self.policy.project_independent_review_required(root)
                    self.assertEqual(requirement, expected_requirement)

    def test_review_depth_cli_uses_approved_project_and_route_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            (translation_dir / "project-brief.md").write_text(
                PROJECT_BRIEF + "\n- Independent review required: true\n",
                encoding="utf-8",
            )
            approve(translation_dir)
            request = root / "review-depth-request.json"
            request.write_text(
                json.dumps(
                    {
                        "task_kind": "translation",
                        "source_units": 1,
                        "route_independent_review_required": False,
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(POLICY),
                    "review-depth",
                    "--project-root",
                    str(root),
                    "--request-json",
                    str(request),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout),
                {
                    "review_depth": "full_challenge",
                    "reasons": ["capability-requires-independent-review"],
                },
            )

    def test_review_depth_cli_rejects_unknown_request_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            approve(translation_dir)
            request = root / "review-depth-request.json"
            request.write_text(
                json.dumps(
                    {"task_kind": "translation", "source_units": 1, "extra": True}
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(POLICY),
                    "review-depth",
                    "--project-root",
                    str(root),
                    "--request-json",
                    str(request),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown review-depth request fields", result.stderr)

    def test_review_depth_cli_checks_approval_before_optional_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation_dir = root / ".translation"
            write_context(translation_dir)
            (translation_dir / "project-brief.md").write_text(
                PROJECT_BRIEF + "\n- Independent review required: sometimes\n",
                encoding="utf-8",
            )
            approve(translation_dir, status="draft")
            request = root / "review-depth-request.json"
            request.write_text(
                json.dumps({"task_kind": "translation", "source_units": 1}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(POLICY),
                    "review-depth",
                    "--project-root",
                    str(root),
                    "--request-json",
                    str(request),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unapproved:setup-approval.json", result.stderr)
            self.assertNotIn("malformed:project-brief.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
