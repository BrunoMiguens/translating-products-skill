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


class SkillContractTests(unittest.TestCase):
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
