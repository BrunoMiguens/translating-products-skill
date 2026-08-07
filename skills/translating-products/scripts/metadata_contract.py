from __future__ import annotations

import json
import re


ROUTING_AXES = (
    "languages", "locales", "scripts", "surfaces", "platforms",
    "formats", "domains", "capabilities",
)
ROUTING_PHASES = ("inspect", "translate", "refine", "integrate", "review")
SPECIFICITIES = (
    "universal", "writing-system", "language", "locale", "surface",
    "platform", "format", "domain", "quality",
)
CATEGORIES = (
    "orchestrator", "core", "quality", "surface", "platform", "format",
    "script", "language", "locale", "domain",
)
PROFILE_FIELDS = frozenset(
    (
        "source_locale", "target_locale", "language", "scripts", "surfaces",
        "platforms", "formats", "domains", "capabilities", "audience",
        "purpose", "register",
    )
)
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)$"
)
SKILL_REQUIRED_FIELDS = frozenset(
    (
        "name", "version", "category", "description", "capabilities",
        "depends_on", "phases", "specificity", "required_context",
        "conflicts", "supersedes",
    )
)
SKILL_OPTIONAL_FIELDS = frozenset(("selectors", "ownership"))
MANIFEST_FIELDS = frozenset(
    (
        "schema_version", "suite_version", "orchestrator",
        "minimum_skill_versions", "skills", "sources",
    )
)
SOURCE_FIELDS = frozenset(
    (
        "id", "repository", "ref", "path", "license", "sha256", "mode",
        "capabilities", "adapter",
    )
)
FRONTMATTER_FIELDS = frozenset(("name", "description"))
YAML_IMPLICIT_SCALAR = re.compile(
    r"""
    (?:
        ~|null|true|false|yes|no|on|off|<<|=
        |
        [-+]?(?:
            \.(?:inf|nan)
            |0b[01_]+
            |0o[0-7_]+
            |0x[0-9a-f_]+
            |[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?
            |(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9_]+)
             (?:e[-+]?[0-9]+)?
        )
        |
        [0-9]{4}-[0-9]{1,2}-[0-9]{1,2}
        (?:
            (?:t|[ \t]+)
            [0-9]{1,2}:[0-9]{2}:[0-9]{2}
            (?:\.[0-9_]*)?
            (?:[ \t]*(?:z|[-+][0-9]{1,2}(?::[0-9]{2})?))?
        )?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_semver(value: object, field: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ValueError(f"invalid {field}")
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid {field}: {value}")
    return tuple(int(match[group]) for group in ("major", "minor", "patch"))


def validate_name(value: object, label: str) -> str:
    if not isinstance(value, str) or NAME.fullmatch(value) is None:
        raise ValueError(f"{label} has invalid name")
    return value


def _string(value: object, field: str, label: str, *, max_length: int | None = None) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        or (max_length is not None and len(value) > max_length)
    ):
        raise ValueError(f"{label} has invalid {field}")
    return value


def _string_list(
    value: object,
    field: str,
    label: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        if not allow_empty and field in {"capabilities", "phases"}:
            raise ValueError(f"{label} requires non-empty {field}")
        qualifier = "non-empty string list" if not allow_empty else "string list"
        raise ValueError(f"{label} has invalid {field}: expected {qualifier}")
    result = tuple(_string(item, field, label) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} requires unique {field}")
    return result


def _validate_locale(value: str, label: str) -> None:
    candidate = value.rstrip("*-")
    parts = candidate.replace("_", "-").split("-")
    if (
        not parts
        or not parts[0].isalpha()
        or not 2 <= len(parts[0]) <= 8
        or any(not part or not part.isalnum() or len(part) > 8 for part in parts)
    ):
        raise ValueError(f"{label} has invalid selector locale: {value}")


def validate_skill_record(item: object, *, label_prefix: str = "skill") -> dict:
    if not isinstance(item, dict):
        raise ValueError(f"{label_prefix} must be an object")
    name_value = item.get("name")
    label = f"{label_prefix} {name_value}" if isinstance(name_value, str) else label_prefix
    missing = sorted(SKILL_REQUIRED_FIELDS - set(item))
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")
    unknown = sorted(set(item) - SKILL_REQUIRED_FIELDS - SKILL_OPTIONAL_FIELDS)
    if unknown:
        raise ValueError(f"{label} has unknown skill fields: {', '.join(unknown)}")
    name = validate_name(name_value, label_prefix)
    label = f"{label_prefix} {name}"
    parse_semver(item["version"], "version")
    if item["category"] not in CATEGORIES:
        raise ValueError(f"{label} has invalid category")
    _string(item["description"], "description", label, max_length=1024)
    capabilities = _string_list(item["capabilities"], "capabilities", label, allow_empty=False)
    phases = _string_list(item["phases"], "phases", label, allow_empty=False)
    unknown_phases = sorted(set(phases) - set(ROUTING_PHASES))
    if unknown_phases:
        raise ValueError(f"{label} has unknown phases: {', '.join(unknown_phases)}")
    if item["specificity"] not in SPECIFICITIES:
        raise ValueError(f"{label} has invalid specificity")
    for field in ("depends_on", "required_context", "conflicts", "supersedes"):
        values = _string_list(item[field], field, label, allow_empty=True)
        if field == "required_context":
            unknown_context = sorted(set(values) - PROFILE_FIELDS)
            if unknown_context:
                raise ValueError(
                    f"{label} has unknown required_context: {', '.join(unknown_context)}"
                )
    if "selectors" in item:
        selectors = item["selectors"]
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(f"{label} must declare selectors")
        canonical_selectors = []
        for selector in selectors:
            if not isinstance(selector, dict) or not selector:
                raise ValueError(f"{label} has invalid selector")
            unknown_axes = sorted(set(selector) - set(ROUTING_AXES))
            if unknown_axes:
                raise ValueError(f"{label} has unknown selector axis: {unknown_axes[0]}")
            canonical = []
            for axis, values in selector.items():
                parsed = _string_list(values, f"selector {axis}", label, allow_empty=False)
                if axis == "locales":
                    for value in parsed:
                        _validate_locale(value, label)
                canonical.append((axis, parsed))
            canonical_selectors.append(tuple(sorted(canonical)))
        if len(canonical_selectors) != len(set(canonical_selectors)):
            raise ValueError(f"{label} requires unique selectors")
    ownership = item.get("ownership")
    if ownership is not None:
        if not isinstance(ownership, dict) or set(ownership) != set(capabilities):
            raise ValueError(f"{label} ownership must map every declared capability")
        for capability, owned_phases in ownership.items():
            parsed = _string_list(owned_phases, "ownership phases", label, allow_empty=False)
            if not set(parsed).issubset(phases):
                raise ValueError(f"{label} ownership phases must be declared phases")
    return item


def validate_manifest_document(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("manifest must be an object")
    missing = sorted(MANIFEST_FIELDS - set(raw))
    unknown = sorted(set(raw) - MANIFEST_FIELDS)
    if missing:
        raise ValueError("manifest is missing fields: " + ", ".join(missing))
    if unknown:
        raise ValueError("unknown manifest fields: " + ", ".join(unknown))
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 2:
        raise ValueError("manifest must use schema_version 2")
    suite_version = parse_semver(raw["suite_version"], "suite_version")
    orchestrator = validate_name(raw["orchestrator"], "manifest orchestrator")
    if not isinstance(raw["skills"], list) or not raw["skills"]:
        raise ValueError("manifest skills must be a non-empty list")
    skills = [validate_skill_record(item, label_prefix="skill") for item in raw["skills"]]
    names = [item["name"] for item in skills]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError("duplicate skill name: " + ", ".join(duplicates))
    by_name = {item["name"]: item for item in skills}
    if orchestrator not in by_name:
        raise ValueError("manifest orchestrator is not a declared skill")
    orchestrators = [item["name"] for item in skills if item["category"] == "orchestrator"]
    if orchestrators != [orchestrator]:
        raise ValueError("manifest must declare exactly one orchestrator category")
    if suite_version != parse_semver(by_name[orchestrator]["version"], "orchestrator version"):
        raise ValueError("suite_version must equal orchestrator version")
    minimums = raw["minimum_skill_versions"]
    if not isinstance(minimums, dict) or any(not isinstance(key, str) for key in minimums):
        raise ValueError("minimum_skill_versions must be an object")
    expected_minimums = set(names) - {orchestrator}
    if set(minimums) != expected_minimums:
        raise ValueError("minimum_skill_versions must name every non-orchestrator skill exactly once")
    for name, minimum in minimums.items():
        parsed = parse_semver(minimum, f"minimum version for {name}")
        declared = parse_semver(by_name[name]["version"], f"version for {name}")
        if parsed > declared:
            raise ValueError(f"minimum version exceeds declared version: {name}")
    if not isinstance(raw["sources"], list):
        raise ValueError("manifest sources must be a list")
    source_ids = []
    for source in raw["sources"]:
        if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
            raise ValueError("manifest source has invalid fields")
        source_ids.append(_string(source["id"], "id", "manifest source"))
        _string_list(source["capabilities"], "capabilities", f"source {source['id']}", allow_empty=False)
        for field in SOURCE_FIELDS - {"id", "capabilities"}:
            _string(source[field], field, f"source {source['id']}")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("duplicate manifest source id")
    return raw


def _parse_scalar(value: str) -> str:
    if not value:
        raise ValueError("unsupported frontmatter scalar")
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError("unsupported frontmatter scalar")
        inner = value[1:-1]
        if "'" in inner.replace("''", ""):
            raise ValueError("unsupported frontmatter scalar")
        return inner.replace("''", "'")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("unsupported frontmatter scalar") from None
        if not isinstance(parsed, str):
            raise ValueError("unsupported frontmatter scalar")
        return parsed
    if (
        value[0] in "-?:,|>[]{}&*!`#%@"
        or value.endswith(("'", '"'))
        or re.search(r"(^|[ \t])#", value)
        or re.search(r":[ \t]|:$", value)
        or YAML_IMPLICIT_SCALAR.fullmatch(value)
    ):
        raise ValueError("unsupported frontmatter scalar")
    return value


def parse_frontmatter_text(content: str, *, label: str = "SKILL.md") -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing opening frontmatter delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("missing closing frontmatter delimiter") from None
    result: dict[str, str] = {}
    for line in lines[1:end]:
        if not line or line != line.lstrip() or ":" not in line:
            raise ValueError(f"invalid frontmatter line: {line}")
        key, raw_value = line.split(":", 1)
        if key not in FRONTMATTER_FIELDS:
            raise ValueError(f"unexpected frontmatter field: {key}")
        if key in result:
            raise ValueError(f"duplicate frontmatter field: {key}")
        if raw_value and not raw_value.startswith(" "):
            raise ValueError(f"invalid frontmatter line: {line}")
        result[key] = _parse_scalar(raw_value.strip())
    if set(result) != FRONTMATTER_FIELDS:
        missing = sorted(FRONTMATTER_FIELDS - set(result))
        raise ValueError("missing frontmatter field: " + ", ".join(missing))
    return result
