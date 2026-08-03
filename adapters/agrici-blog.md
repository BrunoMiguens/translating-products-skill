# Agrici Blog Translation and Audit Adapter

## Source contract

- Source ID: `agrici-blog-translate`
- Repository: https://github.com/AgriciDaniel/claude-blog
- Path: `skills/blog-translate/SKILL.md`
- Immutable commit: `aec971ac511370c6216cd93776c9cf2fec97b32a`
- License: `MIT`
- Source ID: `agrici-blog-locale-audit`
- Repository: https://github.com/AgriciDaniel/claude-blog
- Path: `skills/blog-locale-audit/SKILL.md`
- Immutable commit: `aec971ac511370c6216cd93776c9cf2fec97b32a`
- License: `MIT`
- Adaptation mode: concepts from both pinned files are restated in suite-owned
  web, documentation, orchestration, and QA skills; neither upstream skill is
  invoked or installed at runtime.

## Included concepts

- Preserve Markdown/HTML/frontmatter topology, code, URLs, identifiers,
  embeds, structured-data identities, citations, images, and diagrams while
  translating their confirmed user-facing text.
- Keep page copy, metadata, accessibility text, and structured-data strings in
  one coherent target locale and treat market keywords as localized decisions.
- Audit coverage, section/media/reference parity, locale tags, canonicals,
  reciprocal `hreflang`, schema language, mixed-language defects, and source
  drift where the project supplies reliable provenance.
- Coordinate multiple substantial locale deliverables independently only when
  subagents materially help, then apply shared glossary and final paired QA.

## Excluded host-specific behavior

- Do not use slash commands, `Task`, Claude-specific metadata, upstream agents,
  companion plugin skills, or upstream filesystem/output conventions.
- Do not copy exact language expansion ratios, age thresholds, health scores,
  Google-specific locale claims, or SEO severities as universal truth.
- Do not perform path mutation, report generation, automatic file writes, or
  parallel delegation unless the current user request and suite policy
  authorize them.
- Do not claim native-quality or human review; flag observable issues and state
  only review that actually occurred.

## Suite capability mapping

| Source ID | Capability | Adapted authority |
| --- | --- | --- |
| `agrici-blog-translate` | `surface:web` | `translating-web` owns markup, page-locale coherence, metadata, accessibility, structured data, and locale links. |
| `agrici-blog-translate` | `surface:documentation` | `translating-documentation` owns technical structure, code, references, diagrams, and executable examples. |
| `agrici-blog-translate` | `subagent-routing` | `translating-products` applies the host-neutral benefit threshold and final composition. |
| `agrici-blog-locale-audit` | `translation-qa` | `reviewing-translations` owns paired meaning, language, locale, and surface findings. |
| `agrici-blog-locale-audit` | `terminology-qa` | `reviewing-translations` checks glossary, protected-term, and mixed-language consistency. |
| `agrici-blog-locale-audit` | `structural-qa` | `reviewing-translations` checks protected syntax and supplied surface invariants mechanically. |

## Authority and conflicts

Approved route maps, canonical destinations, structured-data identities, and
technical contracts outrank adapted SEO examples. `translating-core` owns
meaning; web/documentation specialists own structure; the orchestrator owns
delegation; QA reports the smallest unchanged defective segment to its owner.
No adapter may invent missing destinations, locale relationships, evidence, or
claims.

## Attribution and license

Adapted from the two pinned AgriciDaniel sources above under the MIT License.
Copyright (c) 2025-2026 AgriciDaniel. The pinned skill files also attribute
their own conceptual antecedent; this suite adapts only the pinned AgriciDaniel
files and makes no claim to separately vendoring that antecedent. The
applicable license notice is retained in `THIRD_PARTY_NOTICES.md`.

## Adaptation evaluation cases

1. A translated Markdown page retains frontmatter keys, heading order, code,
   links, images, JSON-LD identities, and citations while localizing visible
   copy, metadata, captions, and meaningful alt text.
2. An audit detects a missing reciprocal `hreflang`, source-locale fallback in
   an indexable page, glossary drift, and changed section count, routing each
   smallest segment to the correct owner without rewriting it.
3. Two short locales stay sequential; several substantial independent locale
   deliverables may use subagents only after shared project context is loaded,
   with the parent responsible for cross-locale terminology and final QA.
