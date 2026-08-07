import base64
import copy
from datetime import date, timedelta
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
ROUTER = ROOT / "skills/translating-products/scripts/route_capabilities.py"


def request(*, authorized=()):
    return {
        "schema_version": 2,
        "source_locale": "en-GB",
        "targets": [{"locale": "pt-PT", "register": "neutral"}],
        "surfaces": [],
        "platforms": [],
        "formats": [],
        "domains": ["external-demo"],
        "capabilities": [],
        "audience": "Adults",
        "purpose": "Product copy",
        "register": "neutral",
        "authorized_external_skills": list(authorized),
    }


def external_skill(name: str) -> dict:
    return {
        "name": name,
        "version": "1.0.0",
        "category": "language",
        "description": "Use when refining an installed external translation capability.",
        "capabilities": [f"external:{name}"],
        "depends_on": [],
        "selectors": [{"domains": ["external-demo"]}],
        "phases": ["refine"],
        "specificity": "language",
        "required_context": ["target_locale"],
        "conflicts": [],
        "supersedes": [],
        "ownership": {f"external:{name}": ["refine"]},
    }


def relationship_skill(
    name: str,
    *,
    depends_on: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    supersedes: tuple[str, ...] = (),
    category: str = "language",
    specificity: str = "language",
    phases: tuple[str, ...] = ("refine",),
    selected: bool = True,
) -> dict:
    skill = external_skill(name)
    capability = f"external:{name}"
    skill.update(
        {
            "category": category,
            "depends_on": list(depends_on),
            "conflicts": list(conflicts),
            "supersedes": list(supersedes),
            "specificity": specificity,
            "phases": list(phases),
            "selectors": [
                {"domains": ["external-demo" if selected else "not-requested"]}
            ],
            "ownership": {capability: list(phases)},
        }
    )
    return skill


def installed_skill_text(skill: dict) -> str:
    return (
        "---\n"
        f"name: {skill['name']}\n"
        f"description: {skill['description']}\n"
        "---\n\n"
        "# Installed specialist\n"
    )


def write_installed_skill(
    root: Path,
    skill: dict,
    *,
    skill_text: str | None = None,
    extra_files: dict[str, bytes] | None = None,
    symlinks: dict[str, str] | None = None,
) -> None:
    skill_root = root / skill["name"]
    skill_root.mkdir(parents=True)
    skill_root.joinpath("SKILL.md").write_text(
        skill_text if skill_text is not None else installed_skill_text(skill),
        encoding="utf-8",
    )
    skill_root.joinpath("capability-manifest.json").write_bytes(
        json.dumps({"schema_version": 2, "skill": skill}).encode("utf-8")
    )
    for relative, content in (extra_files or {}).items():
        path = skill_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for relative, target in (symlinks or {}).items():
        path = skill_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


def run_public_cli(
    skills: list[dict],
    *,
    authorized: tuple[str, ...] = (),
    registry: dict | None = None,
    singular: bool = False,
    skill_texts: dict[str, str] | None = None,
    extra_files: dict[str, dict[str, bytes]] | None = None,
    symlinks: dict[str, dict[str, str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = root / "request.json"
        catalog_path = root / "external.json"
        installed_root = root / "installed"
        request_path.write_text(
            json.dumps(request(authorized=authorized)), encoding="utf-8"
        )
        payload = (
            {"schema_version": 2, "skill": skills[0]}
            if singular
            else {"schema_version": 2, "skills": skills}
        )
        catalog_path.write_text(json.dumps(payload), encoding="utf-8")
        for skill in skills:
            write_installed_skill(
                installed_root,
                skill,
                skill_text=(skill_texts or {}).get(skill["name"]),
                extra_files=(extra_files or {}).get(skill["name"]),
                symlinks=(symlinks or {}).get(skill["name"]),
            )
        command = [
            sys.executable,
            str(ROUTER),
            str(request_path),
            "--external-catalog",
            str(catalog_path),
            "--installed-root",
            str(installed_root),
            "--snapshot-root",
            str(root / "snapshots"),
        ]
        if registry is not None:
            registry_path = root / "registry.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            command.extend(("--compatibility-registry", str(registry_path)))
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )


def reviewed_registry_entry(
    skill: dict,
    *,
    skill_text: str | None = None,
    reviewer: str = "https://github.com/translation-reviewer",
    attested_reviewer: str | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> dict:
    ownership = skill.get("ownership") or {
        capability: list(skill["phases"])
        for capability in skill["capabilities"]
    }
    claims = {
        "name": skill["name"],
        "version_constraint": f">={skill['version']}",
        "compatible_orchestrator_version": ">=0.2.0",
        "capabilities": list(skill["capabilities"]),
        "authority_scope": {
            "phases": list(skill["phases"]),
            "ownership": copy.deepcopy(ownership),
            "selectors": copy.deepcopy(skill.get("selectors")),
        },
        "dependencies": list(skill["depends_on"]),
        "conflicts": list(skill["conflicts"]),
        "supersedes": list(skill["supersedes"]),
        "reviewer": reviewer,
        "review_date": date.today().isoformat(),
    }
    attested_claims = copy.deepcopy(claims)
    if attested_reviewer is not None:
        attested_claims["reviewer"] = attested_reviewer
    skill_bytes = (
        skill_text if skill_text is not None else installed_skill_text(skill)
    ).encode("utf-8")
    manifest_bytes = json.dumps({"schema_version": 2, "skill": skill}).encode("utf-8")
    installed_files = {
        "SKILL.md": skill_bytes,
        "capability-manifest.json": manifest_bytes,
        **(extra_files or {}),
    }
    file_inventory = [
        {
            "path": path,
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        for path, content in sorted(installed_files.items())
    ]
    tree_canonical = json.dumps(
        file_inventory,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    canonical = json.dumps(
        {
            "admitted_skill": skill,
            "installed_skill": {
                "name": skill["name"],
                "tree_sha256": "sha256:" + hashlib.sha256(tree_canonical).hexdigest(),
                "files": file_inventory,
            },
            "registry_claims": attested_claims,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        **claims,
        "evaluation_evidence": [f"sha256:{hashlib.sha256(canonical).hexdigest()}"],
    }


class SkillContractTests(unittest.TestCase):
    def test_route_embeds_verified_external_bytes_independent_of_advisory_snapshot(self):
        spec = importlib.util.spec_from_file_location("router_snapshot", ROUTER)
        router = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(router)
        candidate = external_skill("external-snapshot-bound")
        reviewed_bytes = b"reviewed rules\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed_root = root / "installed"
            write_installed_skill(
                installed_root,
                candidate,
                extra_files={"references/rules.md": reviewed_bytes},
            )
            installed = installed_root / candidate["name"]
            reviewed_contents = {
                path.relative_to(installed).as_posix(): path.read_bytes()
                for path in sorted(installed.rglob("*"))
                if path.is_file()
            }
            payload = request(authorized=(candidate["name"],))
            bundled = json.loads(
                (ROOT / "skills/translating-products/references/capability-catalog.json")
                .read_text(encoding="utf-8")
            )

            admitted = router.merge_external_catalogs(
                bundled,
                [{"schema_version": 2, "skills": [candidate]}],
                payload,
                None,
                [installed_root],
            )
            (installed / "references/rules.md").write_bytes(
                b"substituted after admission\n"
            )
            result = router.route(payload, admitted)

            external_load = result["routes"][0]["external_loads"][0]
            snapshot = Path(external_load["load_path"])
            snapshot_parent = snapshot.parent
            try:
                snapshot_parent.chmod(0o700)
                snapshot.chmod(0o700)
                for path in snapshot.rglob("*"):
                    path.chmod(0o700 if path.is_dir() else 0o600)
                (snapshot / "SKILL.md").write_bytes(b"substituted snapshot skill\n")
                (snapshot / "references/rules.md").write_bytes(
                    b"substituted snapshot rules\n"
                )

                self.assertEqual(external_load["name"], candidate["name"])
                self.assertRegex(
                    external_load["tree_sha256"], r"^sha256:[0-9a-f]{64}$"
                )
                self.assertIn("files", external_load)
                embedded_files = external_load["files"]
                self.assertEqual(
                    [item["path"] for item in embedded_files],
                    sorted(reviewed_contents),
                )
                for item in embedded_files:
                    expected = reviewed_contents[item["path"]]
                    actual = base64.b64decode(
                        item["content_base64"], validate=True
                    )
                    self.assertEqual(actual, expected)
                    self.assertEqual(item["size"], len(expected))
                    self.assertEqual(
                        item["sha256"],
                        f"sha256:{hashlib.sha256(expected).hexdigest()}",
                    )
                json.dumps(result)
            finally:
                snapshot_parent.chmod(0o700)
                snapshot.chmod(0o700)
                for path in snapshot.rglob("*"):
                    path.chmod(0o700 if path.is_dir() else 0o600)
                shutil.rmtree(snapshot_parent)

    def test_frontmatter_rejects_yaml_implicit_and_comment_plain_scalars(self):
        spec = importlib.util.spec_from_file_location("router_yaml", ROUTER)
        router = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(router)

        for scalar in (
            "",
            "# host sees a comment",
            "- sequence-like value",
            "? mapping-like value",
            ": mapping-like value",
            ",flow-like value",
            "Use when translating # host strips this comment",
            "null",
            "true",
            "123",
            "2026-08-07",
            ".inf",
            "-.Inf",
            ".NaN",
            "0x10",
            "0b101",
            "0o17",
            "1e3",
            "1:20",
            "2026-08-07T12:30:00Z",
            "2026-08-07 12:30:00+01:00",
            "foo:",
            "foo:\tbar",
            "'foo'bar'",
        ):
            with self.subTest(scalar=scalar):
                content = (
                    "---\nname: translating-demo\n"
                    f"description: {scalar}\n---\n"
                )
                with self.assertRaisesRegex(
                    ValueError, "unsupported frontmatter scalar"
                ):
                    router.parse_frontmatter_text(content)

    def test_external_metadata_uses_the_portable_bundled_contract(self):
        spec = importlib.util.spec_from_file_location("router_metadata", ROUTER)
        router = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(router)
        cases = (
            ("name", lambda skill: skill.__setitem__("name", "External Skill"), "invalid name"),
            ("version", lambda skill: skill.__setitem__("version", "v1"), "invalid version"),
            ("category", lambda skill: skill.__setitem__("category", "misc"), "invalid category"),
            ("duplicate", lambda skill: skill["capabilities"].append(skill["capabilities"][0]), "unique capabilities"),
            ("context", lambda skill: skill.__setitem__("required_context", ["target_local"]), "unknown required_context"),
            ("locale", lambda skill: skill.__setitem__("selectors", [{"locales": ["not_a_locale_"]}]), "invalid selector locale"),
        )
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                candidate = external_skill("external-portable")
                mutate(candidate)
                with self.assertRaisesRegex(ValueError, expected):
                    router.validate_external_catalog({"schema_version": 2, "skills": [candidate]})

    def test_installed_frontmatter_accepts_quoted_scalars_but_rejects_ambiguity(self):
        candidate = external_skill("external-quoted-frontmatter")
        quoted = (
            "---\n"
            f"name: \"{candidate['name']}\"\n"
            f"description: '{candidate['description']}'\n"
            "---\n\n# Installed specialist\n"
        )
        valid = run_public_cli(
            [candidate],
            authorized=(candidate["name"],),
            skill_texts={candidate["name"]: quoted},
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)

        for label, inserted, expected in (
            ("duplicate", f"name: {candidate['name']}\n", "duplicate frontmatter field: name"),
            ("unknown", "version: 1.0.0\n", "unexpected frontmatter field: version"),
            ("multiline", "description: |\n", "unsupported frontmatter scalar"),
        ):
            with self.subTest(label=label):
                text = installed_skill_text(candidate).replace(
                    f"description: {candidate['description']}\n", inserted
                    + f"description: {candidate['description']}\n"
                )
                result = run_public_cli(
                    [candidate],
                    authorized=(candidate["name"],),
                    skill_texts={candidate["name"]: text},
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stderr)

    def test_public_cli_rejects_host_incompatible_frontmatter_scalars(self):
        candidate = external_skill("external-host-frontmatter")
        for scalar in ("foo:", "foo:\tbar", "'foo'bar'"):
            with self.subTest(scalar=scalar):
                text = installed_skill_text(candidate).replace(
                    f"description: {candidate['description']}\n",
                    f"description: {scalar}\n",
                )
                result = run_public_cli(
                    [candidate],
                    authorized=(candidate["name"],),
                    skill_texts={candidate["name"]: text},
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("unsupported frontmatter scalar", result.stderr)

    def test_public_cli_attestation_binds_the_complete_portable_skill_tree(self):
        candidate = external_skill("external-tree-bound")
        reviewed_files = {
            "scripts/check.py": b"print('reviewed')\n",
            "references/rules.md": b"reviewed rules\n",
            "assets/nested/example.txt": b"reviewed asset\n",
        }
        registry = {
            "schema_version": 2,
            "skills": [reviewed_registry_entry(candidate, extra_files=reviewed_files)],
        }

        for _location in range(2):
            unchanged = run_public_cli(
                [candidate],
                registry=registry,
                extra_files={candidate["name"]: reviewed_files},
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stderr)

        mutations = (
            {**reviewed_files, "scripts/check.py": b"print('changed')\n"},
            {**reviewed_files, "references/rules.md": b"changed rules\n"},
            {**reviewed_files, "assets/nested/example.txt": b"changed asset\n"},
            {**reviewed_files, "references/added.md": b"added\n"},
            {key: value for key, value in reviewed_files.items() if key != "references/rules.md"},
        )
        for installed_files in mutations:
            with self.subTest(files=sorted(installed_files)):
                result = run_public_cli(
                    [candidate],
                    registry=registry,
                    extra_files={candidate["name"]: installed_files},
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("compatibility registry evidence mismatch", result.stderr)

        symlinked = run_public_cli(
            [candidate],
            registry=registry,
            extra_files={candidate["name"]: {
                key: value for key, value in reviewed_files.items()
                if key != "references/rules.md"
            }},
            symlinks={candidate["name"]: {"references/rules.md": "../SKILL.md"}},
        )
        self.assertEqual(symlinked.returncode, 2)
        self.assertIn("unsafe installed skill tree", symlinked.stderr)

    def test_public_cli_uses_only_submitted_entries_from_a_reusable_registry(self):
        active = external_skill("external-active-entry")
        inactive = external_skill("external-inactive-entry")
        registry = {
            "schema_version": 2,
            "skills": [
                reviewed_registry_entry(active),
                reviewed_registry_entry(inactive),
            ],
        }

        result = run_public_cli([active], registry=registry)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(active["name"], json.loads(result.stdout)["routes"][0]["selected"])

    def test_public_cli_attestation_rejects_installed_skill_content_substitution(self):
        candidate = external_skill("external-content-bound")
        original = installed_skill_text(candidate)
        reviewed = reviewed_registry_entry(candidate, skill_text=original)
        registry = {"schema_version": 2, "skills": [reviewed]}
        changed_body = original + "\nUnreviewed replacement instructions.\n"
        changed_frontmatter = original.replace(
            "---\n\n# Installed specialist",
            "review-status: substituted\n---\n\n# Installed specialist",
        )

        first_location = run_public_cli(
            [candidate], registry=registry, skill_texts={candidate["name"]: original}
        )
        second_location = run_public_cli(
            [candidate], registry=registry, skill_texts={candidate["name"]: original}
        )

        self.assertEqual(first_location.returncode, 0, first_location.stderr)
        self.assertEqual(second_location.returncode, 0, second_location.stderr)
        for label, substituted, expected in (
            ("body", changed_body, "compatibility registry evidence mismatch"),
            ("frontmatter", changed_frontmatter, "unexpected frontmatter field"),
            ("newline-bytes", original.replace("\n", "\r\n"), "compatibility registry evidence mismatch"),
        ):
            with self.subTest(label=label):
                result = run_public_cli(
                    [candidate],
                    registry=registry,
                    skill_texts={candidate["name"]: substituted},
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(
                    expected,
                    result.stderr,
                )

        regenerated = run_public_cli(
            [candidate],
            registry={
                "schema_version": 2,
                "skills": [
                    reviewed_registry_entry(candidate, skill_text=changed_body)
                ],
            },
            skill_texts={candidate["name"]: changed_body},
        )
        self.assertEqual(regenerated.returncode, 0, regenerated.stderr)

    def test_public_cli_reviewer_identity_is_strict_and_canonical(self):
        candidate = external_skill("external-reviewer-identity")
        invalid_reviewers = (
            "http://example.com/reviewers/alice",
            "https://example.com",
            "https://example.com/",
            "https:///reviewers/alice",
            "https://alice@example.com/reviewers/alice",
            "https://example.com/reviewers/alice?source=registry",
            "https://example.com/reviewers/alice?",
            "https://example.com/reviewers/alice#profile",
            "https://example.com/reviewers/alice#",
            "https://localhost/reviewers/alice",
            "https://bad_host.example/reviewers/alice",
            "https://example.com./reviewers/alice",
            "https://example.com:99999/reviewers/alice",
            "https://example.com:not-a-port/reviewers/alice",
            "https://example.com/reviewers/%ZZ",
            "https://example.com/reviewers/alice\u0000",
            "https://example.com/reviewers/%00alice",
        )
        for reviewer in invalid_reviewers:
            with self.subTest(reviewer=repr(reviewer)):
                entry = reviewed_registry_entry(candidate, reviewer=reviewer)
                result = run_public_cli(
                    [candidate],
                    registry={"schema_version": 2, "skills": [entry]},
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("invalid reviewer", result.stderr)

        valid_reviewers = (
            (
                "https://github.com/translation-reviewer",
                "https://github.com/translation-reviewer",
            ),
            (
                "HTTPS://EXAMPLE.COM:443/reviewers/%7ealice",
                "https://example.com/reviewers/~alice",
            ),
            (
                "https://review.example:8443/people/alice%2fprofile",
                "https://review.example:8443/people/alice%2Fprofile",
            ),
        )
        for reviewer, canonical_reviewer in valid_reviewers:
            with self.subTest(reviewer=reviewer):
                entry = reviewed_registry_entry(
                    candidate,
                    reviewer=reviewer,
                    attested_reviewer=canonical_reviewer,
                )
                result = run_public_cli(
                    [candidate],
                    registry={"schema_version": 2, "skills": [entry]},
                )

                self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_cli_binds_every_reviewed_registry_claim(self):
        candidate = external_skill("external-reviewed")
        reviewed = reviewed_registry_entry(candidate)
        valid = run_public_cli(
            [candidate],
            registry={"schema_version": 2, "skills": [reviewed]},
        )

        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn(
            candidate["name"], json.loads(valid.stdout)["routes"][0]["selected"]
        )

        def changed_phase(skill: dict, _entry: dict) -> None:
            skill["phases"] = ["inspect"]
            skill["ownership"] = {skill["capabilities"][0]: ["inspect"]}

        def changed_selector(skill: dict, _entry: dict) -> None:
            skill["selectors"] = [{"domains": ["another-domain"]}]

        def changed_capability(skill: dict, _entry: dict) -> None:
            skill["capabilities"] = ["external:mutated"]
            skill["ownership"] = {"external:mutated": ["refine"]}

        def changed_dependency(skill: dict, _entry: dict) -> None:
            skill["depends_on"] = ["reviewing-translations"]

        def changed_version(skill: dict, _entry: dict) -> None:
            skill["version"] = "0.9.0"

        def changed_compatibility(_skill: dict, entry: dict) -> None:
            entry["compatible_orchestrator_version"] = ">=9.0.0"

        def placeholder_evidence(_skill: dict, entry: dict) -> None:
            entry["evaluation_evidence"] = ["unverifiable-placeholder"]

        def changed_digest(_skill: dict, entry: dict) -> None:
            entry["evaluation_evidence"] = ["sha256:" + "0" * 64]

        def changed_reviewer(_skill: dict, entry: dict) -> None:
            entry["reviewer"] = "https://example.com/different-reviewer"

        def invalid_reviewer(_skill: dict, entry: dict) -> None:
            entry["reviewer"] = "anonymous"

        def changed_date(_skill: dict, entry: dict) -> None:
            entry["review_date"] = (date.today() - timedelta(days=1)).isoformat()

        def invalid_date(_skill: dict, entry: dict) -> None:
            entry["review_date"] = "2026-1-1"

        def future_date(_skill: dict, entry: dict) -> None:
            entry["review_date"] = (date.today() + timedelta(days=1)).isoformat()

        cases = (
            ("phase", changed_phase, "compatibility registry authority mismatch"),
            ("selector", changed_selector, "compatibility registry authority mismatch"),
            ("capability", changed_capability, "compatibility registry capabilities mismatch"),
            ("dependency", changed_dependency, "compatibility registry relationships mismatch"),
            ("version", changed_version, "compatibility registry version mismatch"),
            ("compatibility", changed_compatibility, "compatibility registry suite mismatch"),
            ("evidence", placeholder_evidence, "invalid evaluation_evidence"),
            ("digest", changed_digest, "compatibility registry evidence mismatch"),
            ("reviewer", changed_reviewer, "compatibility registry evidence mismatch"),
            ("reviewer-format", invalid_reviewer, "invalid reviewer"),
            ("date", changed_date, "compatibility registry evidence mismatch"),
            ("date-format", invalid_date, "review date is invalid"),
            ("future-date", future_date, "review date is in the future"),
        )
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                mutated_skill = copy.deepcopy(candidate)
                mutated_entry = copy.deepcopy(reviewed)
                mutate(mutated_skill, mutated_entry)
                result = run_public_cli(
                    [mutated_skill],
                    registry={"schema_version": 2, "skills": [mutated_entry]},
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)

    def test_public_cli_rejects_unsupported_registry_constraint_syntax(self):
        candidate = external_skill("external-unsupported-constraint")
        reviewed = reviewed_registry_entry(candidate)
        reviewed["version_constraint"] = "^1.0.0"

        result = run_public_cli(
            [candidate],
            registry={"schema_version": 2, "skills": [reviewed]},
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "unsupported version constraint for external-unsupported-constraint: ^1.0.0",
            result.stderr,
        )

    def test_public_cli_rejects_invalid_external_relationships_before_selection(self):
        invalid_cases = (
            (
                "unknown-dependency",
                [relationship_skill("relation-a", depends_on=("missing",))],
                "external skill relation-a has unknown depends_on: missing",
            ),
            (
                "self-dependency",
                [relationship_skill("relation-a", depends_on=("relation-a",))],
                "external skill relation-a cannot depend on itself",
            ),
            (
                "unknown-conflict",
                [relationship_skill("relation-a", conflicts=("missing",))],
                "external skill relation-a has unknown conflicts: missing",
            ),
            (
                "self-conflict",
                [relationship_skill("relation-a", conflicts=("relation-a",))],
                "external skill relation-a cannot conflict with itself",
            ),
            (
                "unknown-supersedes",
                [relationship_skill("relation-a", supersedes=("missing",))],
                "external skill relation-a has unknown supersedes: missing",
            ),
            (
                "self-supersedes",
                [relationship_skill("relation-a", supersedes=("relation-a",))],
                "external skill relation-a cannot supersede itself",
            ),
            (
                "dependency-cycle",
                [
                    relationship_skill("relation-a", depends_on=("relation-b",)),
                    relationship_skill("relation-b", depends_on=("relation-a",)),
                ],
                "dependency cycle at relation-a",
            ),
            (
                "supersedes-cycle",
                [
                    relationship_skill("relation-a", supersedes=("relation-b",)),
                    relationship_skill("relation-b", supersedes=("relation-a",)),
                ],
                "supersedes cycle at relation-a",
            ),
            (
                "mixed-cycle",
                [
                    relationship_skill("relation-a", depends_on=("relation-b",)),
                    relationship_skill("relation-b", supersedes=("relation-a",)),
                ],
                "relationship cycle at relation-a",
            ),
            (
                "depends-and-supersedes",
                [
                    relationship_skill(
                        "relation-a",
                        depends_on=("relation-b",),
                        supersedes=("relation-b",),
                        specificity="locale",
                        selected=False,
                    ),
                    relationship_skill("relation-b"),
                ],
                "external skill relation-a cannot both depend on and supersede relation-b",
            ),
            (
                "cross-category-supersedes",
                [
                    relationship_skill(
                        "relation-a",
                        supersedes=("relation-b",),
                        category="surface",
                        specificity="locale",
                        selected=False,
                    ),
                    relationship_skill("relation-b"),
                ],
                "external skill relation-a cannot supersede relation-b across categories",
            ),
            (
                "non-narrowing-supersedes",
                [
                    relationship_skill(
                        "relation-a",
                        supersedes=("relation-b",),
                        selected=False,
                    ),
                    relationship_skill("relation-b"),
                ],
                "external skill relation-a must be more specific than "
                "superseded skill: relation-b",
            ),
            (
                "unowned-supersedes",
                [
                    relationship_skill(
                        "relation-a",
                        supersedes=("relation-b",),
                        specificity="locale",
                        selected=False,
                    ),
                    relationship_skill("relation-b"),
                ],
                "external skill relation-a supersedes relation-b without shared ownership",
            ),
        )
        for label, candidates, expected in invalid_cases:
            with self.subTest(label=label):
                result = run_public_cli(
                    candidates,
                    authorized=tuple(candidate["name"] for candidate in candidates),
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)

    def test_public_cli_permits_cross_phase_dependencies(self):
        candidate = relationship_skill(
            "external-surface",
            depends_on=("translating-core", "reviewing-translations"),
            category="surface",
            specificity="surface",
            phases=("inspect", "integrate"),
        )

        result = run_public_cli(
            [candidate], authorized=(candidate["name"],)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        route = json.loads(result.stdout)["routes"][0]
        self.assertIn(candidate["name"], route["phases"]["inspect"])
        self.assertIn(candidate["name"], route["phases"]["integrate"])
        self.assertLess(
            route["selected"].index("reviewing-translations"),
            route["selected"].index("translating-core"),
        )
        self.assertLess(
            route["selected"].index("translating-core"),
            route["selected"].index(candidate["name"]),
        )

    def test_public_cli_rejects_actionless_external_scopes(self):
        cases = (
            (
                "capabilities",
                lambda skill: skill.__setitem__("capabilities", []),
                "external skill external-empty-capabilities requires non-empty capabilities",
            ),
            (
                "phases",
                lambda skill: skill.__setitem__("phases", []),
                "external skill external-empty-phases requires non-empty phases",
            ),
            (
                "ownership",
                lambda skill: skill.__setitem__("ownership", {}),
                "external skill external-empty-ownership ownership must map "
                "every declared capability",
            ),
            (
                "ownership-slice",
                lambda skill: skill.__setitem__(
                    "ownership", {skill["capabilities"][0]: []}
                ),
                "external skill external-empty-ownership-slice has invalid "
                "ownership phases: expected non-empty string list",
            ),
            (
                "selector-list",
                lambda skill: skill.__setitem__("selectors", []),
                "external skill external-empty-selector-list must declare selectors",
            ),
            (
                "selector-null",
                lambda skill: skill.__setitem__("selectors", None),
                "external skill external-empty-selector-null must declare selectors",
            ),
            (
                "selector-object",
                lambda skill: skill.__setitem__("selectors", [{}]),
                "external skill external-empty-selector-object has invalid selector",
            ),
            (
                "selector-values",
                lambda skill: skill.__setitem__(
                    "selectors", [{"domains": []}]
                ),
                "external skill external-empty-selector-values has invalid selector "
                "domains: expected non-empty string list",
            ),
        )
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                candidate = external_skill(f"external-empty-{label}")
                mutate(candidate)
                result = run_public_cli(
                    [candidate], authorized=(candidate["name"],)
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)

    def test_public_cli_accepts_universal_singular_manifest_with_derived_ownership(self):
        candidate = external_skill("external-singular")
        del candidate["ownership"]
        del candidate["selectors"]

        result = run_public_cli(
            [candidate], authorized=(candidate["name"],), singular=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        route = json.loads(result.stdout)["routes"][0]
        self.assertIn(candidate["name"], route["selected"])
        self.assertEqual(route["reasons"][candidate["name"]], ["selector:universal"])

    def test_public_cli_routes_two_authorized_external_catalogs_in_input_order(self):
        first = external_skill("external-first")
        second = external_skill("external-second")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_path = root / "request.json"
            first_path = root / "first.json"
            second_path = root / "second.json"
            request_path.write_text(
                json.dumps(
                    request(authorized=(first["name"], second["name"]))
                ),
                encoding="utf-8",
            )
            first_path.write_text(
                json.dumps({"schema_version": 2, "skills": [first]}),
                encoding="utf-8",
            )
            second_path.write_text(
                json.dumps({"schema_version": 2, "skills": [second]}),
                encoding="utf-8",
            )

            missing_installation = subprocess.run(
                [
                    sys.executable,
                    str(ROUTER),
                    str(request_path),
                    "--external-catalog",
                    str(first_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            installed_root = root / "installed"
            write_installed_skill(installed_root, first)
            write_installed_skill(installed_root, second)

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROUTER),
                    str(request_path),
                    "--external-catalog",
                    str(first_path),
                    "--external-catalog",
                    str(second_path),
                    "--installed-root",
                    str(installed_root),
                    "--snapshot-root",
                    str(root / "snapshots"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(missing_installation.returncode, 2)
        self.assertIn("external skill is not installed: external-first", missing_installation.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        route = json.loads(result.stdout)["routes"][0]
        self.assertEqual(
            route["phases"]["refine"][-2:],
            ["external-first", "external-second"],
        )
        self.assertEqual(route["reasons"]["external-first"], ["selector:0"])

    def test_public_cli_rejects_unauthorized_or_malformed_external_metadata(self):
        candidate = external_skill("external-untrusted")
        malformed = external_skill("external-missing-description")
        del malformed["description"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_path = root / "request.json"
            candidate_path = root / "candidate.json"
            malformed_path = root / "malformed.json"
            request_path.write_text(json.dumps(request()), encoding="utf-8")
            candidate_path.write_text(
                json.dumps({"schema_version": 2, "skills": [candidate]}),
                encoding="utf-8",
            )
            malformed_path.write_text(
                json.dumps({"schema_version": 2, "skills": [malformed]}),
                encoding="utf-8",
            )

            unauthorized = subprocess.run(
                [
                    sys.executable,
                    str(ROUTER),
                    str(request_path),
                    "--external-catalog",
                    str(candidate_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            invalid = subprocess.run(
                [
                    sys.executable,
                    str(ROUTER),
                    str(request_path),
                    "--external-catalog",
                    str(malformed_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(unauthorized.returncode, 2)
        self.assertEqual(unauthorized.stdout, "")
        self.assertIn("unauthorized external skill: external-untrusted", unauthorized.stderr)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        self.assertIn(
            "external skill external-missing-description is missing fields: description",
            invalid.stderr,
        )


if __name__ == "__main__":
    unittest.main()
