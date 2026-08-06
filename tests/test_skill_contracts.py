import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillContractTests(unittest.TestCase):
    def test_orchestrator_declares_profile_phase_and_reason_contract(self):
        text = (ROOT / "skills/translating-products/SKILL.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "one task profile per target locale",
            "inspect",
            "translate",
            "refine",
            "integrate",
            "review",
            "route reasons",
            "Do not combine linguistic branches",
        ):
            self.assertIn(required, text)

    def test_extension_contract_uses_the_same_declarative_metadata(self):
        text = (
            ROOT
            / "skills/translating-products/references/extension-contract.md"
        ).read_text(encoding="utf-8")
        for field in (
            "selectors",
            "phases",
            "specificity",
            "required_context",
            "conflicts",
            "supersedes",
        ):
            self.assertIn(f"`{field}`", text)

    def test_compatibility_registry_uses_schema_two(self):
        registry = json.loads(
            (
                ROOT
                / "skills/translating-products/references/compatibility-registry.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(registry, {"schema_version": 2, "skills": []})


if __name__ == "__main__":
    unittest.main()
