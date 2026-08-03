# Kostja Translation Adapter

## Source contract

- Source ID: `kostja-translation`
- Repository: https://github.com/kostja94/marketing-skills
- Path: `skills/content/translation/SKILL.md`
- Immutable commit: `70987bad4ebe9dce1f74858c1c64f3f8810f18e4`
- License: `MIT`
- Adaptation mode: concepts are restated in suite-owned skills; the upstream
  skill is not invoked or installed at runtime.

## Included concepts

- Establish content purpose, audience, locale, register, constraints, glossary,
  style guide, and protected terminology before translating.
- Keep terminology and regional choices consistent and record unresolved
  choices instead of silently treating them as approved.
- Preserve intent while adapting idioms, calls to action, locale formatting,
  and culturally dependent phrasing.
- Keep search and market terminology as a separately evidenced decision rather
  than translating a source-language keyword list literally.

## Excluded host-specific behavior

- Do not read `.claude/` or `.cursor/` project files; the suite uses the
  portable `.translation/` contract.
- Do not invoke upstream related skills, emit host commands, install a TMS, or
  reproduce the upstream first-use introduction.
- Do not adopt categorical human-versus-machine workflow recommendations or
  claim that native-speaker or human review occurred. The suite produces AI
  translations and records only review that actually happened.

## Suite capability mapping

| Capability | Adapted authority |
| --- | --- |
| `core-translation` | `translating-core` owns contextual, meaning-faithful target copy. |
| `terminology` | `translating-core` owns glossary use and draft terminology decisions. |
| `cultural-adaptation` | `translating-core` adapts intended effect without changing facts or brand intent. |

## Authority and conflicts

This adapter is provenance, not runtime authority. Explicit user requirements
and approved project configuration outrank adapted guidance; core semantic
fidelity outranks cultural or stylistic preferences. Surface and language
specialists may refine format and mechanics without changing facts, protected
terms, or approved terminology.

## Attribution and license

Adapted from the pinned Kostja94 source above under the MIT License. Copyright
(c) 2025 kostja94. The applicable license notice is retained in
`THIRD_PARTY_NOTICES.md`.

## Adaptation evaluation cases

1. A regional marketing translation receives audience, locale, glossary, and
   brand constraints before drafting; an unapproved term is reported as draft.
2. A localized call to action preserves conversion intent without inventing a
   claim, and protected product names remain byte-for-byte unchanged.
3. A market keyword question triggers narrow current-market research only when
   project knowledge cannot resolve it; routine wording does not trigger web
   research.
