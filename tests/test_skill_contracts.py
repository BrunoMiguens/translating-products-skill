import copy
from datetime import date, timedelta
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "skills/translating-products/scripts/route_capabilities.py"


def request(*, authorized=()):
    return {
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


def write_installed_skill(root: Path, skill: dict) -> None:
    skill_root = root / skill["name"]
    skill_root.mkdir(parents=True)
    skill_root.joinpath("SKILL.md").write_text(
        "---\n"
        f"name: {skill['name']}\n"
        f"description: {skill['description']}\n"
        "---\n\n"
        "# Installed specialist\n",
        encoding="utf-8",
    )
    skill_root.joinpath("capability-manifest.json").write_text(
        json.dumps({"schema_version": 2, "skill": skill}), encoding="utf-8"
    )


def run_public_cli(
    skills: list[dict],
    *,
    authorized: tuple[str, ...] = (),
    registry: dict | None = None,
    singular: bool = False,
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
            write_installed_skill(installed_root, skill)
        command = [
            sys.executable,
            str(ROUTER),
            str(request_path),
            "--external-catalog",
            str(catalog_path),
            "--installed-root",
            str(installed_root),
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


def reviewed_registry_entry(skill: dict) -> dict:
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
        "reviewer": "https://github.com/translation-reviewer",
        "review_date": date.today().isoformat(),
    }
    canonical = json.dumps(
        {"admitted_skill": skill, "registry_claims": claims},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        **claims,
        "evaluation_evidence": [f"sha256:{hashlib.sha256(canonical).hexdigest()}"],
    }


class SkillContractTests(unittest.TestCase):
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
