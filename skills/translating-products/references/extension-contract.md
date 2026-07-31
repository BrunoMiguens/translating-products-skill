# External specialist extension contract

An external specialist is optional and must already be installed. A registry entry records reviewed compatibility; it never installs, downloads, or enables a skill.

## Eligibility metadata

Require installed metadata to declare every field below without ambiguity:

- unique installed skill name
- version
- minimum compatible orchestrator version
- capabilities
- supported languages and locales
- supported surfaces and domains
- required inputs
- produced outputs
- authority scope
- dependencies
- conflicts

Ignore an external specialist when any field is missing or ambiguous.

## Runtime authorization

Use an eligible specialist only when one of these channels authorizes it:

1. The user explicitly selects it.
2. `.translation/project-brief.md` lists it.
3. `compatibility-registry.json` contains a reviewed entry for it.

If an authorized external specialist is unavailable, use the bundled specialist. Never install a specialist at runtime.

## Reviewed registry entries

Contributors add an entry only after review. Each entry records:

- installed skill name
- version constraint
- capabilities
- authority scope
- conflicts
- compatible orchestrator version
- evaluation case IDs
- review date

Reviewers verify all eligibility metadata, dependency and conflict behavior, authority boundaries, representative evaluation outputs, and continued host neutrality before accepting an entry.
