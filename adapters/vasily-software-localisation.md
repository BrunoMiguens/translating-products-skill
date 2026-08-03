# Vasily Software Localisation Adapter

## Source contract

- Source ID: `vasily-software-localisation`
- Repository: https://github.com/vasilyu1983/AI-Agents-public
- Path: `frameworks/shared-skills/skills/software-localisation/SKILL.md`
- Immutable commit: `6a223ba13c311c09b41c1dc09c14ab75e703894b`
- License: `MIT`
- Adaptation mode: stable concepts are restated in suite-owned surface skills;
  the upstream skill is not invoked or installed at runtime.

## Included concepts

- Preserve UTF-8 resource content, keys, placeholders, ICU selectors and
  branches, and locale-aware runtime formatting.
- Treat locale fallback, mixed-language output, key completeness, and
  hardcoded user-visible strings as explicit product and CI concerns.
- Keep distinct UI purposes in distinct resource keys and provide contextual
  information for translators.
- Exercise plural/select branches, pseudo-localization, RTL behavior, layout,
  and accessibility before release.

## Excluded host-specific behavior

- Do not copy the upstream library-selection matrix, package commands, version
  snapshot, framework setup recipes, temporary-file commands, or unrelated
  domain-specific engine examples.
- Do not install packages, choose an i18n framework, or run host-specific tools
  unless the user's implementation task independently asks for that work.
- Volatile framework behavior is researched only for a concrete unresolved
  project question and is never treated as bundled current truth.

## Suite capability mapping

| Capability | Adapted authority |
| --- | --- |
| `surface:web` | `translating-web` owns localized-page coherence, markup, metadata, and fallback-language release gates. |
| `surface:software` | `localizing-software` owns resource contracts, fallbacks, and CI handoff. |
| `icu` | `localizing-software` preserves ICU topology while target-locale specialists own linguistic branches. |
| `placeholders` | `localizing-software` preserves placeholder identity, type, scope, and representative execution. |

## Authority and conflicts

Engineering owns executable resource and runtime contracts; product owns
message intent and business values. `translating-core` owns target prose, and
language/script specialists own linguistic and bidirectional mechanics. If a
runtime constraint conflicts with approved wording, preserve both inputs and
report the exact key and constraint.

## Attribution and license

Adapted from the pinned Vasiliy Uvarov source above under the MIT License.
Copyright (c) 2025 Vasiliy Uvarov. The applicable license notice is retained in
`THIRD_PARTY_NOTICES.md`.

## Adaptation evaluation cases

1. An ICU catalog translation retains keys, arguments, fixed selectors,
   exact-number selectors, and `other` while adding target-required plural
   categories.
2. An indexable locale route containing an unintended source-language fallback
   is blocked and reported with the affected key rather than silently shipped.
3. CI compiles the real catalog, executes representative plural/select values,
   verifies locale formatting, and runs pseudo-localized RTL/layout cases.
