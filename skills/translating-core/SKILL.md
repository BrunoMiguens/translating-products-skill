---
name: translating-core
description: Use when producing meaning-faithful, natural, culturally appropriate translations for any language translation before applying product or language specialists.
---

# Translating Core

## Overview

Translate the source's meaning and intended effect, not its syntax. Treat facts, protected spans, approved terminology, locale choices, and structural constraints as invariants while making the target read as original writing.

## Capabilities

- `core-translation`
- `terminology`
- `cultural-adaptation`
- `structural-fidelity`

## Context Analysis

Read the complete source and neighboring context before drafting. Establish:

- audience, purpose, medium, and desired action
- source and target language plus full locale when known
- register, formality, voice, and relationship to the reader
- glossary, style guide, protected terms, and project decisions
- required structure and non-translatable spans

Resolve a missing choice from context when the evidence is strong. Ask one focused question when locale, audience, or register ambiguity would materially change the translation; otherwise state the narrow assumption in decision notes.

## Fidelity Invariants

Preserve every claim, fact, relationship, emphasis, qualifier, obligation, and uncertainty. Add no explanation or promise that the source does not contain. Do not omit repeated content merely because it seems redundant.

Before delivery, compare source and target for:

- names, numbers, dates, currency, units, and factual polarity
- placeholders, identifiers, links, markup, code, and commands
- quoted or protected text and deliberately mixed-language content
- headings, lists, paragraphs, and other required counts or order

Keep protected terms exactly unchanged, including capitalization. Keep non-translatable spans byte-for-byte unchanged when the brief requires exact preservation.

## Terminology

Apply approved glossary entries consistently, using any grammatical treatment the glossary permits. Resolve conflicts by following explicit user requirements, then the approved project brief, glossary, and style guide.

Choose a coherent target term when no approved entry exists. Record only newly inferred choices as `draft`; never present them as approved or silently add them to the glossary. Include the source term, chosen target, scope, and a short rationale when the choice is not self-evident. Do not repeat approved glossary entries as new decisions.

## Naturalness

Write idiomatic target-language sentences with natural information order, rhythm, cohesion, and register. Recreate idioms, humor, metaphors, and calls to action by their intended effect rather than word-for-word form. Adapt cultural references only as far as needed to preserve that effect, without changing facts or brand intent.

Use the requested locale's vocabulary, grammar, spelling, punctuation, and conventions. Avoid source-language calques and accidental mixed language unless a span is protected or the locale normally retains it.

## Structure Preservation

Preserve the source topology unless the brief explicitly permits restructuring: the same headings, paragraphs, list items, table cells, link targets, tags, placeholders, code spans, and identifiers. Translate only human-readable text inside a structured artifact. Never repair, rename, normalize, or execute protected syntax while translating.

If natural target grammar conflicts with an exact structural constraint, satisfy the constraint and flag the affected segment in concise notes rather than corrupting the artifact.

## Output Contract

Return the requested translated artifact first, without commentary inside it.
The caller's artifact-only or exact-schema request suppresses decision notes,
alternatives, headings, quotation wrappers, presentation fences, and other
explanatory material. Preserve wrappers that belong to the source or requested
artifact; do not add wrappers merely to present the answer.

Add concise decision notes only when they are requested or the caller permits
supporting notes and there are draft terminology choices, material assumptions,
or unresolved constraints. Keep approved input decisions out of the notes.

Use this compact form when notes are needed:

```text
Decision notes
- draft terminology: <source> → <target> — <scope or rationale>
- assumption: <narrow assumption and affected segment>
- unresolved: <constraint and affected segment>
```

Do not add process narration, unsolicited alternatives, or unrelated advice.
