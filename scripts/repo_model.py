from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


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


def load_manifest(path: Path) -> Manifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    skills = tuple(
        SkillRecord(
            name=item["name"],
            version=item["version"],
            category=item["category"],
            description=item["description"],
            capabilities=tuple(item["capabilities"]),
            depends_on=tuple(item["depends_on"]),
            selectors=tuple(_load_selector(selector) for selector in item["selectors"]),
            phases=tuple(item["phases"]),
            specificity=item["specificity"],
            required_context=tuple(item["required_context"]),
            conflicts=tuple(item["conflicts"]),
            supersedes=tuple(item["supersedes"]),
        )
        for item in raw["skills"]
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
        skills=skills,
        sources=sources,
    )


def skill_by_name(manifest: Manifest, name: str) -> SkillRecord:
    for skill in manifest.skills:
        if skill.name == name:
            return skill
    raise KeyError(name)
