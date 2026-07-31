import tempfile
import unittest
from pathlib import Path
import subprocess
import sys

from scripts.repo_model import Manifest, SkillRecord, SourceRecord
from scripts.validate_repo import parse_frontmatter, validate_repository, validate_skill


ROOT = Path(__file__).resolve().parents[1]


def manifest_with(
    *,
    skills: tuple[SkillRecord, ...] = (),
    minimum_skill_versions: dict[str, str] | None = None,
    sources: tuple[SourceRecord, ...] = (),
) -> Manifest:
    return Manifest(
        schema_version=1,
        suite_version="0.1.0",
        orchestrator="translating-products",
        minimum_skill_versions=minimum_skill_versions or {},
        skills=skills,
        sources=sources,
    )


def skill(name: str, *, depends_on: tuple[str, ...] = ()) -> SkillRecord:
    return SkillRecord(
        name=name,
        version="0.1.0",
        category="core",
        description="Use when testing repository validation.",
        capabilities=("capability:test",),
        depends_on=depends_on,
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


if __name__ == "__main__":
    unittest.main()
