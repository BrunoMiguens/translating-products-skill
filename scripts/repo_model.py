from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path


_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/translating-products/scripts/metadata_contract.py"
)
_CONTRACT_SPEC = importlib.util.spec_from_file_location(
    "translation_metadata_contract", _CONTRACT_PATH
)
if _CONTRACT_SPEC is None or _CONTRACT_SPEC.loader is None:
    raise RuntimeError("unable to load translation metadata contract")
_CONTRACT = importlib.util.module_from_spec(_CONTRACT_SPEC)
_CONTRACT_SPEC.loader.exec_module(_CONTRACT)
validate_manifest_document = _CONTRACT.validate_manifest_document
parse_frontmatter_text = _CONTRACT.parse_frontmatter_text


ROUTING_AXES = (
    "languages",
    "locales",
    "scripts",
    "surfaces",
    "platforms",
    "formats",
    "domains",
    "capabilities",
)
ROUTING_PHASES = ("inspect", "translate", "refine", "integrate", "review")
SPECIFICITIES = (
    "universal",
    "writing-system",
    "language",
    "locale",
    "surface",
    "platform",
    "format",
    "domain",
    "quality",
)

@dataclass(frozen=True)
class Selector:
    criteria: tuple[tuple[str, tuple[str, ...]], ...]

    def as_dict(self) -> dict[str, tuple[str, ...]]:
        return dict(self.criteria)


@dataclass(frozen=True)
class SkillRecord:
    name: str
    version: str
    category: str
    description: str
    capabilities: tuple[str, ...]
    depends_on: tuple[str, ...]
    selectors: tuple[Selector, ...]
    phases: tuple[str, ...]
    specificity: str
    required_context: tuple[str, ...]
    conflicts: tuple[str, ...]
    supersedes: tuple[str, ...]
    ownership: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class SourceRecord:
    id: str
    repository: str
    ref: str
    path: str
    license: str
    sha256: str
    mode: str
    capabilities: tuple[str, ...]
    adapter: str


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    suite_version: str
    orchestrator: str
    minimum_skill_versions: dict[str, str]
    skills: tuple[SkillRecord, ...]
    sources: tuple[SourceRecord, ...]


def _load_selector(raw: dict[str, object]) -> Selector:
    if not isinstance(raw, dict):
        raise ValueError("selector must be an object")
    unknown = sorted(set(raw) - set(ROUTING_AXES))
    if unknown:
        raise ValueError("unknown selector axes: " + ", ".join(unknown))
    criteria = []
    for axis in ROUTING_AXES:
        if axis not in raw:
            continue
        values = raw[axis]
        if not isinstance(values, list) or not values or not all(
            isinstance(value, str) and value.strip() for value in values
        ):
            raise ValueError(f"selector {axis} must be a non-empty string list")
        criteria.append((axis, tuple(values)))
    if not criteria:
        raise ValueError("selector must contain at least one routing axis")
    return Selector(tuple(criteria))


def _load_string_list(
    raw: object, field: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if (
        not isinstance(raw, list)
        or (not allow_empty and not raw)
        or not all(isinstance(value, str) and value.strip() for value in raw)
    ):
        qualifier = "string list" if allow_empty else "non-empty string list"
        raise ValueError(f"{field} must be a {qualifier}")
    return tuple(raw)


def _load_phases(raw: object) -> tuple[str, ...]:
    phases = _load_string_list(raw, "phases")
    unknown = sorted(set(phases) - set(ROUTING_PHASES))
    if unknown:
        raise ValueError("unknown routing phases: " + ", ".join(unknown))
    return phases


def _load_specificity(raw: object) -> str:
    if not isinstance(raw, str) or raw not in SPECIFICITIES:
        raise ValueError("specificity must be one of: " + ", ".join(SPECIFICITIES))
    return raw


def _load_selectors(raw: object) -> tuple[Selector, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("selectors must be a non-empty selector list")
    return tuple(_load_selector(selector) for selector in raw)


def _load_ownership(
    item: dict[str, object],
    capabilities: tuple[str, ...],
    phases: tuple[str, ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    raw = item.get("ownership")
    if raw is None:
        return tuple((capability, phases) for capability in capabilities)
    if not isinstance(raw, dict) or set(raw) != set(capabilities):
        raise ValueError("ownership must map every declared capability")
    ownership = []
    for capability in capabilities:
        values = _load_string_list(raw[capability], "ownership phases")
        if not set(values).issubset(phases):
            raise ValueError("ownership phases must be declared phases")
        ownership.append((capability, values))
    return tuple(ownership)


def load_manifest(path: Path) -> Manifest:
    raw = validate_manifest_document(
        json.loads(path.read_text(encoding="utf-8"))
    )
    skills = []
    for item in raw["skills"]:
        capabilities = _load_string_list(item["capabilities"], "capabilities")
        phases = _load_phases(item["phases"])
        skills.append(
            SkillRecord(
                name=item["name"],
                version=item["version"],
                category=item["category"],
                description=item["description"],
                capabilities=capabilities,
                depends_on=tuple(item["depends_on"]),
                selectors=(
                    _load_selectors(item["selectors"])
                    if "selectors" in item
                    else ()
                ),
                phases=phases,
                specificity=_load_specificity(item["specificity"]),
                required_context=_load_string_list(
                    item["required_context"], "required_context", allow_empty=True
                ),
                conflicts=_load_string_list(
                    item["conflicts"], "conflicts", allow_empty=True
                ),
                supersedes=_load_string_list(
                    item["supersedes"], "supersedes", allow_empty=True
                ),
                ownership=_load_ownership(
                    item,
                    capabilities,
                    phases,
                ),
            )
        )
    sources = tuple(
        SourceRecord(
            id=item["id"],
            repository=item["repository"],
            ref=item["ref"],
            path=item["path"],
            license=item["license"],
            sha256=item["sha256"],
            mode=item["mode"],
            capabilities=tuple(item["capabilities"]),
            adapter=item["adapter"],
        )
        for item in raw["sources"]
    )
    return Manifest(
        schema_version=raw["schema_version"],
        suite_version=raw["suite_version"],
        orchestrator=raw["orchestrator"],
        minimum_skill_versions=raw["minimum_skill_versions"],
        skills=tuple(skills),
        sources=sources,
    )


def skill_by_name(manifest: Manifest, name: str) -> SkillRecord:
    for skill in manifest.skills:
        if skill.name == name:
            return skill
    raise KeyError(name)
