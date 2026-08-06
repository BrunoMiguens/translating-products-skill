# External specialist extension contract

An external specialist is optional and must already be installed. It joins the
same manifest-derived capability catalog as bundled skills; a catalog record
never installs, downloads, enables, or grants authority to a skill.

## Required catalog metadata

An installed external specialist must provide the same record fields as a
bundled specialist: `name`, `version`, `category`, `description`,
`capabilities`, `depends_on`, `selectors`, `phases`, `specificity`,
`required_context`, `conflicts`, and `supersedes`. The catalog itself uses
schema version `2`.

`selectors` is a non-empty list of alternative matching rules. Each populated
axis within one selector must match the task profile; values within an axis are
alternatives; multiple selectors are alternative routes into the skill. The
only selector axes are `languages`, `locales`, `scripts`, `surfaces`,
`platforms`, `formats`, `domains`, and `capabilities`. Locale values use
normalized BCP 47 ranges.

`phases` contains one or more of `inspect`, `translate`, `refine`,
`integrate`, and `review`. `specificity` declares the scope (universal,
writing-system, language, locale, surface, platform, format, domain, or
quality). `required_context` lists profile fields that must be resolved before
the skill runs. `conflicts` lists incompatible specialists. `supersedes` is
only for an explicit replacement of broader guidance; narrower wording remains
within its declared ownership and does not remove unrelated capabilities.

Ignore an external candidate when any required metadata is missing, ambiguous,
invalid, or conflicts cannot be resolved deterministically.

## Authorization and catalog-only admission

Use an eligible external specialist only when one of these channels authorizes
it:

1. The user explicitly selects it.
2. `.translation/project-brief.md` lists it.
3. `compatibility-registry.json` contains a reviewed entry for it.

An authorized specialist must already be installed before routing. If it is
unavailable, use compatible bundled guidance. Never install a specialist at
runtime. A catalog-only test must prove that a compatible third-party record is
selected without modifying the router.

## Specialist authoring checklist

Every production external specialist documents its scope and non-scope,
context signals, reusable procedure, relevant principles, protected invariants
and authority boundaries, failure classes, and QA evidence or handoff. Examples
may illustrate principles but are non-exhaustive. Direct reuse of benchmark
prompts, expected answers, or disguised answer tables fails validation.

## Reviewed registry entries

Contributors add an entry only after review. Each entry records the installed
skill name, version constraint, compatible orchestrator version, capabilities,
authority scope, dependencies, conflicts, evaluation evidence, and review
date. Reviewers verify metadata validity, catalog-only selection, dependency
and conflict behavior, authority boundaries, representative evaluation output,
and host neutrality before accepting an entry.
