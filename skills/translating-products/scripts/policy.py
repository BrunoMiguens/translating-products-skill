REQUIRED_PROJECT_FILES = {
    "project-brief.md",
    "locales.yaml",
    "glossary.csv",
    "style-guide.md",
    "protected-terms.txt",
}


def bootstrap_action(existing_files: set[str]) -> str:
    return (
        "translate"
        if REQUIRED_PROJECT_FILES.issubset(existing_files)
        else "setup-one-question-at-a-time"
    )


def should_research(
    question: str,
    bundled_knowledge_sufficient: bool,
) -> bool:
    return bool(question.strip()) and not bundled_knowledge_sufficient


def should_use_subagents(
    *,
    host_supports_subagents: bool,
    target_locales: int,
    source_units: int,
    separable_sections: int,
    terminology_pass: bool,
    independent_review: bool,
) -> bool:
    if not host_supports_subagents:
        return False
    return (
        (target_locales > 1 and source_units >= 20)
        or (separable_sections > 1 and source_units >= 100)
        or terminology_pass
        or independent_review
    )


def missing_specialist_action(
    *,
    core_can_cover: bool,
    bundled_available: bool,
) -> str:
    if bundled_available:
        return "use-bundled-specialist"
    if core_can_cover:
        return "use-core"
    return "report-missing-capability"


AUTHORITY_ORDER = (
    "explicit-user-requirements",
    "approved-project-configuration",
    "core-semantic-fidelity",
    "domain-terminology",
    "language-locale-mechanics",
    "product-platform-formatting",
    "stylistic-preferences",
)
