import json
import tempfile
import unittest
from pathlib import Path
import subprocess
import sys

from scripts.repo_model import Manifest, Selector, SkillRecord, SourceRecord
from scripts.render_catalog import (
    InventoryMarkerError,
    render_catalog,
    render_readme_inventory,
)
from scripts.validate_repo import (
    parse_frontmatter,
    validate_distribution,
    validate_fixture_separation,
    validate_repository,
    validate_skill,
    validate_workflow,
)


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_START = "<!-- skill-inventory:start -->"
INVENTORY_END = "<!-- skill-inventory:end -->"
MALFORMED_INVENTORIES = (
    (
        "missing-start",
        f"before\n{INVENTORY_END}\nafter\n",
        "skill inventory start marker is missing",
    ),
    (
        "missing-end",
        f"before\n{INVENTORY_START}\nafter\n",
        "skill inventory end marker is missing",
    ),
    (
        "reversed",
        f"before\n{INVENTORY_END}\ncontent\n{INVENTORY_START}\nafter\n",
        "skill inventory markers are reversed",
    ),
    (
        "duplicate-start",
        f"{INVENTORY_START}\n{INVENTORY_START}\n{INVENTORY_END}\n",
        "skill inventory start marker appears 2 times",
    ),
    (
        "duplicate-end",
        f"{INVENTORY_START}\n{INVENTORY_END}\n{INVENTORY_END}\n",
        "skill inventory end marker appears 2 times",
    ),
    (
        "duplicate-pair",
        f"{INVENTORY_START}\n{INVENTORY_END}\n"
        f"{INVENTORY_START}\n{INVENTORY_END}\n",
        "skill inventory start marker appears 2 times",
    ),
)
VALID_WORKFLOW = """name: Validate

on:
  push:
  pull_request:
  schedule:
    - cron: "23 6 * * 1"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python3 scripts/render_catalog.py --check
      - run: python3 scripts/validate_repo.py
      - run: python3 -m unittest discover -s tests -v

  verify-pinned-sources:
    if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python3 scripts/verify_sources.py
"""


def manifest_with(
    *,
    skills: tuple[SkillRecord, ...] = (),
    minimum_skill_versions: dict[str, str] | None = None,
    sources: tuple[SourceRecord, ...] = (),
) -> Manifest:
    return Manifest(
        schema_version=2,
        suite_version="0.2.0",
        orchestrator="translating-products",
        minimum_skill_versions=minimum_skill_versions or {},
        skills=skills,
        sources=sources,
    )


def skill(
    name: str,
    *,
    category: str = "core",
    capabilities: tuple[str, ...] = ("capability:test",),
    depends_on: tuple[str, ...] = (),
    selectors: tuple[Selector, ...] = (
        Selector((("capabilities", ("capability:test",)),)),
    ),
    phases: tuple[str, ...] = ("translate",),
    specificity: str = "universal",
    required_context: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    supersedes: tuple[str, ...] = (),
    ownership: tuple[tuple[str, tuple[str, ...]], ...] | None = None,
) -> SkillRecord:
    return SkillRecord(
        name=name,
        version="0.1.0",
        category=category,
        description="Use when testing repository validation.",
        capabilities=capabilities,
        depends_on=depends_on,
        selectors=selectors,
        phases=phases,
        specificity=specificity,
        required_context=required_context,
        conflicts=conflicts,
        supersedes=supersedes,
        ownership=ownership or tuple(
            (capability, phases) for capability in capabilities
        ),
    )


def write_skill(root: Path, name: str, body: str = "# Demo\n") -> Path:
    path = root / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "description: Use when testing repository validation.\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )
    return path


class ValidatorTests(unittest.TestCase):
    def test_validation_rejects_benchmark_text_copied_into_a_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copied = "This exact benchmark sentence is deliberately long enough to detect reuse."
            write_skill(root, "translating-demo", copied + "\n")
            cases = root / "benchmarks" / "demo-v1" / "cases.jsonl"
            cases.parent.mkdir(parents=True)
            cases.write_text(
                json.dumps({"id": "case-1", "source": copied}) + "\n",
                encoding="utf-8",
            )

            errors = validate_fixture_separation(root)

        self.assertEqual(
            errors,
            [
                "skills/translating-demo/SKILL.md: contains normalized benchmark text "
                "from benchmarks/demo-v1/cases.jsonl case case-1 field source"
            ],
        )

    def test_fixture_separation_normalizes_case_and_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = "This benchmark sentence has enough unique words to detect normalized copying."
            write_skill(
                root,
                "translating-demo",
                "THIS  BENCHMARK sentence has enough unique words\n"
                "to detect normalized copying.\n",
            )
            cases = root / "benchmarks" / "demo-v1" / "cases.jsonl"
            cases.parent.mkdir(parents=True)
            cases.write_text(
                json.dumps({"id": "case-2", "source": source}) + "\n",
                encoding="utf-8",
            )

            errors = validate_fixture_separation(root)

        self.assertEqual(len(errors), 1)
        self.assertIn("case case-2 field source", errors[0])

    def test_fixture_separation_allows_short_phrases_and_unrelated_principles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(
                root,
                "translating-demo",
                "Preserve meaning, infer register from context, and verify terminology.\n"
                "A compact example may say Save changes.\n",
            )
            cases = root / "benchmarks" / "demo-v1" / "cases.jsonl"
            cases.parent.mkdir(parents=True)
            cases.write_text(
                json.dumps({"id": "case-3", "source": "Save changes"}) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_fixture_separation(root), [])

    def test_repository_validation_enforces_fixture_separation_in_both_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copied = "This exact benchmark sentence is deliberately long enough to detect reuse."
            write_skill(root, "translating-demo", copied + "\n")
            cases = root / "benchmarks" / "demo-v1" / "cases.jsonl"
            cases.parent.mkdir(parents=True)
            cases.write_text(
                json.dumps({"id": "case-1", "source": copied}) + "\n",
                encoding="utf-8",
            )
            manifest = manifest_with(
                skills=(skill("translating-demo"),),
                minimum_skill_versions={"translating-demo": "0.1.0"},
            )
            expected = [
                "skills/translating-demo/SKILL.md: contains normalized benchmark text "
                "from benchmarks/demo-v1/cases.jsonl case case-1 field source"
            ]

            self.assertEqual(validate_repository(root, manifest), expected)
            self.assertEqual(validate_repository(root, manifest, partial=True), expected)

    def test_cli_partial_mode_validates_the_current_manifest(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_repo.py"), "--partial"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(
            result.stdout,
            r"^validated [0-9]+ implemented skills \(partial manifest\)\n$",
        )

    def test_parse_frontmatter_reads_name_and_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(
                "---\nname: translating-demo\n"
                "description: Translates demo content. Use for demo translation.\n"
                "---\n\n# Demo\n",
                encoding="utf-8",
            )
            self.assertEqual(
                parse_frontmatter(path),
                {
                    "name": "translating-demo",
                    "description": "Translates demo content. Use for demo translation.",
                },
            )

    def test_parse_frontmatter_rejects_missing_closing_delimiter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text("---\nname: translating-demo\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing closing frontmatter delimiter"):
                parse_frontmatter(path)

    def test_parse_frontmatter_rejects_fields_other_than_name_and_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(
                "---\nname: translating-demo\n"
                "description: Use when testing repository validation.\n"
                "version: 1.0.0\n---\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unexpected frontmatter field: version"):
                parse_frontmatter(path)

    def test_parse_frontmatter_rejects_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(
                "---\nname: translating-demo\nname: another-demo\n"
                "description: Use when testing repository validation.\n---\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate frontmatter field: name"):
                parse_frontmatter(path)

    def test_validate_skill_rejects_host_specific_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "translating-demo"
            folder.mkdir()
            path = folder / "SKILL.md"
            path.write_text(
                "---\nname: translating-demo\n"
                "description: Translates demo content. Use for demo translation.\n"
                "---\n\nRun $other-skill before translating.\n",
                encoding="utf-8",
            )
            self.assertIn("host-specific invocation", "\n".join(validate_skill(path)))

    def test_validate_skill_rejects_slash_prefixed_host_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_skill(Path(tmp), "translating-demo", "/other-skill\n")

            self.assertEqual(validate_skill(path), ["host-specific invocation"])

    def test_validate_skill_rejects_invalid_name_and_directory_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translating-demo" / "SKILL.md"
            path.parent.mkdir()
            path.write_text(
                "---\nname: Translating_demo\n"
                "description: Use when testing repository validation.\n---\n",
                encoding="utf-8",
            )

            self.assertEqual(
                validate_skill(path),
                ["invalid name", "frontmatter name does not match directory"],
            )

    def test_validate_skill_enforces_description_bounds_and_line_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translating-demo" / "SKILL.md"
            path.parent.mkdir()
            path.write_text(
                "---\nname: translating-demo\ndescription: \n---\n\n"
                + "\n".join("content" for _ in range(496)),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_skill(path),
                [
                    "description must contain 1 through 1024 characters",
                    "SKILL.md exceeds 500 lines",
                ],
            )

            path.write_text(
                "---\nname: translating-demo\n"
                f"description: {'x' * 1025}\n---\n",
                encoding="utf-8",
            )
            self.assertEqual(
                validate_skill(path),
                ["description must contain 1 through 1024 characters"],
            )

    def test_strict_validation_reports_missing_declared_artifacts(self):
        declared_skill = skill("translating-demo")
        source = SourceRecord(
            id="demo-source",
            repository="example/demo",
            ref="a" * 40,
            path="SKILL.md",
            license="MIT",
            sha256="b" * 64,
            mode="adapted",
            capabilities=("capability:test",),
            adapter="adapters/demo.md",
        )
        manifest = manifest_with(
            skills=(declared_skill,),
            minimum_skill_versions={"translating-demo": "0.1.0"},
            sources=(source,),
        )

        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_repository(Path(tmp), manifest)

        self.assertEqual(
            errors,
            [
                "skills/translating-demo: manifest skill directory is missing",
                "adapters/demo.md: source adapter is missing",
            ],
        )

    def test_partial_validation_defers_absent_artifacts_but_validates_present_skills(self):
        declared_skill = skill("translating-demo")
        manifest = manifest_with(
            skills=(declared_skill,),
            minimum_skill_versions={"translating-demo": "0.1.0"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(validate_repository(root, manifest, partial=True), [])

            write_skill(root, "translating-demo", "Run @other-agent before translating.\n")
            self.assertEqual(
                validate_repository(root, manifest, partial=True),
                ["skills/translating-demo/SKILL.md: host-specific invocation"],
            )

    def test_repository_rejects_root_skill_and_unknown_skill_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text("not a portable skill", encoding="utf-8")
            (root / "skills" / "undeclared").mkdir(parents=True)

            self.assertEqual(
                validate_repository(root, manifest_with(), partial=True),
                [
                    "SKILL.md: root skill is not allowed",
                    "skills/undeclared: directory is not declared in manifest",
                ],
            )

    def test_source_validation_requires_adapters_only_in_strict_mode(self):
        declared_skill = skill("translating-demo")
        source = SourceRecord(
            id="demo-source",
            repository="example/demo",
            ref="a" * 40,
            path="SKILL.md",
            license="MIT",
            sha256="b" * 64,
            mode="copied",
            capabilities=("capability:missing",),
            adapter="adapters/demo.md",
        )
        manifest = manifest_with(
            skills=(declared_skill,),
            minimum_skill_versions={"translating-demo": "0.1.0"},
            sources=(source,),
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                validate_repository(root, manifest),
                [
                    "skills/translating-demo: manifest skill directory is missing",
                    "skills-manifest.json: demo-source has unsupported mode copied",
                    "skills-manifest.json: demo-source references missing capability capability:missing",
                    "adapters/demo.md: source adapter is missing",
                ],
            )
            self.assertEqual(
                validate_repository(root, manifest, partial=True),
                [
                    "skills-manifest.json: demo-source has unsupported mode copied",
                    "skills-manifest.json: demo-source references missing capability capability:missing",
                ],
            )

    def test_validation_reports_unknown_dependencies_cycles_and_invalid_metadata(self):
        first = skill("translating-first", depends_on=("translating-second", "missing"))
        second = skill("translating-second", depends_on=("translating-first",))
        manifest = manifest_with(
            skills=(first, second),
            minimum_skill_versions={"translating-first": "not-a-version"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_repository(Path(tmp), manifest, partial=True)

        self.assertEqual(
            errors,
            [
                "skills-manifest.json: translating-first has unknown dependency missing",
                "skills-manifest.json: dependency cycle at translating-first",
                "skills-manifest.json: minimum_skill_versions must name every non-orchestrator skill exactly once",
                "skills-manifest.json: invalid minimum version for translating-first",
            ],
        )

    def test_routing_metadata_rejects_unknown_references_and_invalid_enums(self):
        bad = skill(
            "translating-bad",
            phases=("guess",),
            specificity="regional-ish",
            required_context=("unknown_field",),
            conflicts=("missing-conflict",),
            supersedes=("missing-superior",),
        )
        errors = validate_repository(
            Path("/nonexistent"),
            manifest_with(
                skills=(bad,),
                minimum_skill_versions={"translating-bad": "0.1.0"},
            ),
            partial=True,
        )

        self.assertIn(
            "skills-manifest.json: translating-bad has invalid phase guess", errors
        )
        self.assertIn(
            "skills-manifest.json: translating-bad has invalid specificity regional-ish",
            errors,
        )
        self.assertIn(
            "skills-manifest.json: translating-bad requires unknown context unknown_field",
            errors,
        )
        self.assertIn(
            "skills-manifest.json: translating-bad conflicts with unknown skill missing-conflict",
            errors,
        )
        self.assertIn(
            "skills-manifest.json: translating-bad supersedes unknown skill missing-superior",
            errors,
        )

    def test_routing_metadata_allows_universal_scope_and_rejects_self_and_supersedes_cycles(self):
        first = skill(
            "translating-first",
            selectors=(),
            conflicts=("translating-first",),
            supersedes=("translating-second",),
        )
        second = skill(
            "translating-second",
            supersedes=("translating-first", "translating-second"),
        )
        errors = validate_repository(
            Path("/nonexistent"),
            manifest_with(
                skills=(first, second),
                minimum_skill_versions={
                    "translating-first": "0.1.0",
                    "translating-second": "0.1.0",
                },
            ),
            partial=True,
        )

        self.assertNotIn(
            "skills-manifest.json: translating-first must declare at least one selector",
            errors,
        )
        self.assertIn(
            "skills-manifest.json: translating-first cannot conflict with itself", errors
        )
        self.assertIn(
            "skills-manifest.json: translating-second cannot supersede itself", errors
        )
        self.assertIn(
            "skills-manifest.json: supersedes cycle at translating-first", errors
        )

    def test_bundled_relationship_rules_match_external_admission(self):
        cross_category = skill(
            "translating-cross-category",
            category="language",
            specificity="locale",
            supersedes=("translating-base",),
        )
        base = skill("translating-base")
        non_narrowing = skill(
            "translating-non-narrowing",
            category="language",
            specificity="language",
            supersedes=("translating-language",),
        )
        language = skill(
            "translating-language",
            category="language",
            specificity="language",
        )
        unowned = skill(
            "translating-unowned",
            category="language",
            capabilities=("capability:other",),
            specificity="locale",
            supersedes=("translating-language",),
        )
        mixed_a = skill(
            "translating-mixed-a", depends_on=("translating-mixed-b",)
        )
        mixed_b = skill(
            "translating-mixed-b", supersedes=("translating-mixed-a",)
        )
        skills = (
            base,
            cross_category,
            language,
            non_narrowing,
            unowned,
            mixed_a,
            mixed_b,
        )

        errors = validate_repository(
            Path("/nonexistent"),
            manifest_with(
                skills=skills,
                minimum_skill_versions={record.name: "0.1.0" for record in skills},
            ),
            partial=True,
        )

        self.assertIn(
            "skills-manifest.json: translating-cross-category cannot supersede "
            "translating-base across categories",
            errors,
        )
        self.assertIn(
            "skills-manifest.json: translating-non-narrowing must be more specific "
            "than superseded skill translating-language",
            errors,
        )
        self.assertIn(
            "skills-manifest.json: translating-unowned supersedes translating-language "
            "without shared ownership",
            errors,
        )
        self.assertIn(
            "skills-manifest.json: relationship cycle at translating-mixed-a", errors
        )

    def test_bundled_relationship_rules_permit_cross_phase_dependencies(self):
        review = skill("review", phases=("review",), specificity="quality")
        surface = skill(
            "surface",
            depends_on=("review",),
            phases=("inspect", "integrate"),
            specificity="surface",
        )
        skills = (review, surface)

        errors = validate_repository(
            Path("/nonexistent"),
            manifest_with(
                skills=skills,
                minimum_skill_versions={record.name: "0.1.0" for record in skills},
            ),
            partial=True,
        )

        self.assertEqual(errors, [])

    def test_routing_metadata_rejects_unknown_axes_self_dependencies_and_overlap(self):
        base = skill("translating-base")
        invalid_selector = skill(
            "translating-invalid-selector",
            selectors=(Selector((("audience", ("consumer",)),)),),
        )
        self_dependency = skill(
            "translating-self", depends_on=("translating-self",)
        )
        overlap = skill(
            "translating-overlap",
            depends_on=("translating-base",),
            supersedes=("translating-base",),
        )
        skills = (base, invalid_selector, self_dependency, overlap)
        errors = validate_repository(
            Path("/nonexistent"),
            manifest_with(
                skills=skills,
                minimum_skill_versions={skill.name: "0.1.0" for skill in skills},
            ),
            partial=True,
        )

        self.assertIn(
            "skills-manifest.json: translating-invalid-selector has unknown selector axis audience",
            errors,
        )
        self.assertIn(
            "skills-manifest.json: translating-self cannot depend on itself", errors
        )
        self.assertIn(
            "skills-manifest.json: translating-overlap cannot both depend on and "
            "supersede translating-base",
            errors,
        )


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.skills = (
            skill("translating-products"),
            skill("translating-japanese"),
        )
        self.manifest = manifest_with(
            skills=self.skills,
            minimum_skill_versions={"translating-japanese": "0.1.0"},
        )

    def write_valid_distribution(self, root: Path) -> None:
        for record in self.skills:
            (root / "skills" / record.name).mkdir(parents=True)
        (root / "docs").mkdir()
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / "README.md").write_text(
            """# Demo

<!-- skill-inventory:start -->
- [translating-products](skills/translating-products/)
- [translating-japanese](skills/translating-japanese/)
<!-- skill-inventory:end -->

```bash
npx skills add . --list
npx skills add . --all
npx skills add OWNER/REPOSITORY --skill '*' --agent claude-code
npx skills add OWNER/REPOSITORY --skill '*' --agent codex
npx skills add OWNER/REPOSITORY --skill '*' --agent cursor
npx skills add OWNER/REPOSITORY --skill '*' --agent universal
npx skills add OWNER/REPOSITORY --skill translating-japanese --agent claude-code
npx skills add OWNER/REPOSITORY --all
```

[Architecture](docs/architecture.md)
""",
            encoding="utf-8",
        )
        (root / "CONTRIBUTING.md").write_text(
            "[Manifest](skills-manifest.json)\n", encoding="utf-8"
        )
        (root / "skills-manifest.json").write_text("{}\n", encoding="utf-8")
        (root / "docs" / "architecture.md").write_text(
            "[README](../README.md)\n", encoding="utf-8"
        )
        (root / ".github" / "workflows" / "validate.yml").write_text(
            VALID_WORKFLOW, encoding="utf-8"
        )

    def test_distribution_requires_publication_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_distribution(Path(tmp), self.manifest)

        self.assertEqual(
            errors,
            [
                ".github/workflows/validate.yml: file is missing",
                "CONTRIBUTING.md: file is missing",
                "README.md: file is missing",
                "docs/architecture.md: file is missing",
            ],
        )

    def test_distribution_accepts_manifest_inventory_commands_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_valid_distribution(root)

            self.assertEqual(validate_distribution(root, self.manifest), [])

    def test_distribution_rejects_inventory_drift_bad_commands_and_broken_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_valid_distribution(root)
            readme = (root / "README.md").read_text(encoding="utf-8")
            readme = readme.replace(
                "- [translating-japanese](skills/translating-japanese/)\n", ""
            ).replace(
                "--agent cursor", "--agent unsupported-host"
            ).replace(
                "[Architecture](docs/architecture.md)",
                "[Architecture](docs/missing.md)",
            )
            (root / "README.md").write_text(readme, encoding="utf-8")

            errors = validate_distribution(root, self.manifest)

        self.assertIn(
            "README.md: skill inventory does not match skills-manifest.json",
            errors,
        )
        self.assertIn(
            "README.md: unsupported --agent value unsupported-host",
            errors,
        )
        self.assertIn("README.md: broken link docs/missing.md", errors)

    def test_distribution_rejects_absolute_escaping_and_symlink_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            root = sandbox / "repo"
            root.mkdir()
            self.write_valid_distribution(root)
            external = sandbox / "outside.txt"
            external.write_text("outside\n", encoding="utf-8")
            (root / "escape-link").symlink_to(external)
            with (root / "README.md").open("a", encoding="utf-8") as readme:
                readme.write(
                    "[Absolute](/etc/passwd)\n"
                    "[Parent](../outside.txt)\n"
                    "[Symlink](escape-link)\n"
                )

            errors = validate_distribution(root, self.manifest)

        self.assertEqual(
            [error for error in errors if "link" in error],
            [
                "README.md: absolute local link is not allowed: /etc/passwd",
                "README.md: local link escapes repository: ../outside.txt",
                "README.md: local link escapes repository: escape-link",
            ],
        )

    def test_distribution_accepts_in_repository_relative_and_fragment_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_valid_distribution(root)
            with (root / "README.md").open("a", encoding="utf-8") as readme:
                readme.write(
                    "[Architecture section](docs/architecture.md#routing)\n"
                    "[Same document](#demo)\n"
                )

            self.assertEqual(validate_distribution(root, self.manifest), [])

    def test_distribution_accepts_decoded_angle_query_and_external_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_valid_distribution(root)
            (root / "docs" / "My File.md").write_text(
                "# Spaced file\n", encoding="utf-8"
            )
            with (root / "README.md").open("a", encoding="utf-8") as readme:
                readme.write(
                    "[Encoded space](docs/My%20File.md)\n"
                    "[Angle destination](<docs/My File.md>)\n"
                    "[Query](docs/architecture.md?view=raw)\n"
                    "[Query fragment](docs/architecture.md?view=raw#routing)\n"
                    "[Protocol relative](//example.com/reference)\n"
                )

            self.assertEqual(validate_distribution(root, self.manifest), [])

    def test_distribution_rejects_encoded_path_controls_and_nul(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            root = sandbox / "repo"
            root.mkdir()
            self.write_valid_distribution(root)
            external = sandbox / "outside.txt"
            external.write_text("outside\n", encoding="utf-8")
            residual = root / "%2e%2e"
            residual.mkdir()
            (residual / "outside.txt").write_text("decoy\n", encoding="utf-8")
            with (root / "README.md").open("a", encoding="utf-8") as readme:
                readme.write(
                    "[Encoded parent](%2e%2e/outside.txt)\n"
                    "[Encoded slash](..%2foutside.txt)\n"
                    "[Encoded absolute](%2fetc%2fpasswd)\n"
                    "[Encoded Windows absolute](C%3a%5cWindows%5cSystem32)\n"
                    "[Encoded backslash](docs%5c..%5c..%5coutside.txt)\n"
                    "[Double encoded parent](%252e%252e/outside.txt)\n"
                    "[Decoded NUL](docs%00/file.md)\n"
                )

            errors = validate_distribution(root, self.manifest)

        self.assertEqual(
            [error for error in errors if "link" in error or "NUL" in error],
            [
                "README.md: local link escapes repository: %2e%2e/outside.txt",
                "README.md: local link escapes repository: ..%2foutside.txt",
                "README.md: absolute local link is not allowed: %2fetc%2fpasswd",
                "README.md: absolute local link is not allowed: "
                "C%3a%5cWindows%5cSystem32",
                "README.md: local link escapes repository: "
                "docs%5c..%5c..%5coutside.txt",
                "README.md: local link retains encoded path control after "
                "one decode: %252e%252e/outside.txt",
                "README.md: decoded local link contains NUL: docs%00/file.md",
            ],
        )

    def test_distribution_reports_every_malformed_inventory_without_crashing(self):
        for label, malformed, expected in MALFORMED_INVENTORIES:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.write_valid_distribution(root)
                (root / "README.md").write_text(malformed, encoding="utf-8")

                errors = validate_distribution(root, self.manifest)

                self.assertIn(f"README.md: {expected}", errors)


class WorkflowContractTests(unittest.TestCase):
    def validate_text(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = Path(tmp) / "validate.yml"
            workflow.write_text(text, encoding="utf-8")
            return validate_workflow(workflow)

    def test_valid_workflow_has_no_contract_errors(self):
        self.assertEqual(self.validate_text(VALID_WORKFLOW), [])

    def test_workflow_requires_every_trigger_and_read_only_permissions(self):
        trigger_mutations = {
            "push": VALID_WORKFLOW.replace("  push:\n", ""),
            "pull_request": VALID_WORKFLOW.replace("  pull_request:\n", ""),
            "schedule": VALID_WORKFLOW.replace(
                '  schedule:\n    - cron: "23 6 * * 1"\n', ""
            ),
            "workflow_dispatch": VALID_WORKFLOW.replace(
                "  workflow_dispatch:\n", ""
            ),
        }
        for trigger, mutated in trigger_mutations.items():
            with self.subTest(trigger=trigger):
                self.assertIn(
                    f"workflow: missing trigger {trigger}",
                    self.validate_text(mutated),
                )

        self.assertIn(
            "workflow: top-level permissions must be exactly contents: read",
            self.validate_text(
                VALID_WORKFLOW.replace("contents: read", "contents: write")
            ),
        )
        empty_schedules = (
            VALID_WORKFLOW.replace('    - cron: "23 6 * * 1"\n', ""),
            VALID_WORKFLOW.replace('cron: "23 6 * * 1"', 'cron: ""'),
        )
        for mutated in empty_schedules:
            with self.subTest(schedule=mutated):
                self.assertIn(
                    "workflow: schedule trigger must contain a nonempty cron entry",
                    self.validate_text(mutated),
                )

    def test_validate_job_requires_python_and_all_offline_commands(self):
        self.assertIn(
            "workflow: validate job must use Python 3.11",
            self.validate_text(
                VALID_WORKFLOW.replace(
                    'python-version: "3.11"', 'python-version: "3.12"', 1
                )
            ),
        )
        commands = (
            "python3 scripts/render_catalog.py --check",
            "python3 scripts/validate_repo.py",
            "python3 -m unittest discover -s tests -v",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(
                    f"workflow: validate job is missing required command: {command}",
                    self.validate_text(
                        VALID_WORKFLOW.replace(f"      - run: {command}\n", "")
                    ),
                )

    def test_source_verification_is_separate_gated_and_not_a_pr_dependency(self):
        verify_command = "python3 scripts/verify_sources.py"
        in_validate = VALID_WORKFLOW.replace(
            "\n  verify-pinned-sources:\n",
            f"      - run: {verify_command}\n\n  verify-pinned-sources:\n",
        )
        self.assertIn(
            "workflow: source verification must not run in validate job",
            self.validate_text(in_validate),
        )
        self.assertIn(
            "workflow: expected exactly one separate source verification job",
            self.validate_text(
                VALID_WORKFLOW.replace(f"      - run: {verify_command}\n", "")
            ),
        )
        self.assertIn(
            "workflow: source verification job must be gated to schedule and workflow_dispatch only",
            self.validate_text(
                VALID_WORKFLOW.replace(
                    "    if: github.event_name == 'schedule' || "
                    "github.event_name == 'workflow_dispatch'\n",
                    "",
                )
            ),
        )
        self.assertIn(
            "workflow: validate job must not depend on source verification job",
            self.validate_text(
                VALID_WORKFLOW.replace(
                    "  validate:\n", "  validate:\n    needs: verify-pinned-sources\n"
                )
            ),
        )
        self.assertIn(
            "workflow: validate job must not depend on source verification job",
            self.validate_text(
                VALID_WORKFLOW.replace(
                    "  validate:\n",
                    "  validate:\n    needs:\n      - verify-pinned-sources\n",
                )
            ),
        )
        first_python = VALID_WORKFLOW.index('python-version: "3.11"')
        second_python = VALID_WORKFLOW.index(
            'python-version: "3.11"', first_python + 1
        )
        mutated = (
            VALID_WORKFLOW[:second_python]
            + 'python-version: "3.12"'
            + VALID_WORKFLOW[second_python + len('python-version: "3.11"') :]
        )
        self.assertIn(
            "workflow: source verification job must use Python 3.11",
            self.validate_text(mutated),
        )

    def test_source_gate_rejects_pr_access_and_permission_elevation(self):
        self.assertIn(
            "workflow: source verification job must be gated to schedule and workflow_dispatch only",
            self.validate_text(
                VALID_WORKFLOW.replace(
                    "github.event_name == 'workflow_dispatch'",
                    "github.event_name == 'pull_request'",
                )
            ),
        )
        self.assertIn(
            "workflow: job validate must not override top-level permissions",
            self.validate_text(
                VALID_WORKFLOW.replace(
                    "  validate:\n",
                    "  validate:\n    permissions:\n      contents: write\n",
                )
            ),
        )
        inline_permissions = (
            "permissions: write-all",
            "permissions: read-all",
            "permissions: {contents: write}",
            '"permissions": write-all',
            "'permissions': write-all",
        )
        for declaration in inline_permissions:
            with self.subTest(declaration=declaration):
                self.assertIn(
                    "workflow: job validate must not override top-level permissions",
                    self.validate_text(
                        VALID_WORKFLOW.replace(
                            "  validate:\n",
                            f"  validate:\n    {declaration}\n",
                        )
                    ),
                )

        quoted_benign_key = VALID_WORKFLOW.replace(
            "    runs-on: ubuntu-latest\n",
            '    "runs-on": ubuntu-latest\n',
            1,
        )
        self.assertEqual(self.validate_text(quoted_benign_key), [])

    def test_source_gate_requires_semantic_or_with_both_allowed_events(self):
        condition = (
            "github.event_name == 'schedule' || "
            "github.event_name == 'workflow_dispatch'"
        )
        invalid = (
            condition.replace(" || ", " && "),
            "github.event_name == 'schedule'",
            "github.event_name == 'workflow_dispatch'",
        )
        for gate in invalid:
            with self.subTest(gate=gate):
                self.assertIn(
                    "workflow: source verification job must be gated to "
                    "schedule and workflow_dispatch only",
                    self.validate_text(VALID_WORKFLOW.replace(condition, gate)),
                )

        canonical = (
            "${{ (github.event_name == \"workflow_dispatch\") || "
            "(github.event_name == 'schedule') }}"
        )
        self.assertEqual(
            self.validate_text(VALID_WORKFLOW.replace(condition, canonical)),
            [],
        )


class InventoryMarkerTests(unittest.TestCase):
    def test_renderer_rejects_malformed_markers_without_rewriting(self):
        for label, malformed, expected in MALFORMED_INVENTORIES:
            for check in (False, True):
                with (
                    self.subTest(case=label, check=check),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    readme = Path(tmp) / "README.md"
                    readme.write_text(malformed, encoding="utf-8")

                    with self.assertRaisesRegex(InventoryMarkerError, expected):
                        render_readme_inventory(
                            ROOT / "skills-manifest.json", readme, check=check
                        )

                    self.assertEqual(readme.read_text(encoding="utf-8"), malformed)

    def test_renderer_check_accepts_valid_inventory_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                f"before\n{INVENTORY_START}\nstale\n{INVENTORY_END}\nafter\n",
                encoding="utf-8",
            )
            self.assertTrue(
                render_readme_inventory(ROOT / "skills-manifest.json", readme)
            )
            expected = readme.read_text(encoding="utf-8")

            self.assertTrue(
                render_readme_inventory(
                    ROOT / "skills-manifest.json", readme, check=True
                )
            )
            self.assertEqual(readme.read_text(encoding="utf-8"), expected)

    def test_catalog_cli_reports_marker_error_without_rewriting_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "skills-manifest.json"
            manifest_path.write_text(
                (ROOT / "skills-manifest.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            references = root / "skills/translating-products/references"
            references.mkdir(parents=True)
            render_catalog(
                manifest_path,
                references / "capability-catalog.json",
                references / "capability-catalog.md",
            )
            malformed = (
                f"before\n{INVENTORY_END}\ncontent\n{INVENTORY_START}\nafter\n"
            )
            readme = root / "README.md"
            readme.write_text(malformed, encoding="utf-8")

            for check_args in ([], ["--check"]):
                with self.subTest(check=bool(check_args)):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "scripts/render_catalog.py"),
                            "--root",
                            str(root),
                            *check_args,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(
                        result.stderr,
                        "README.md: skill inventory markers are reversed\n",
                    )
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(
                        readme.read_text(encoding="utf-8"), malformed
                    )


if __name__ == "__main__":
    unittest.main()
