from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class SkillRecord:
    name: str
    version: str
    category: str
    description: str
    capabilities: tuple[str, ...]
    depends_on: tuple[str, ...]


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
