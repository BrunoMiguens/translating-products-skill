---
name: reviewing-translations
description: Use when reviewing AI-generated translations for meaning, naturalness, terminology, locale, and structure after translation or when auditing localized content.
---

# Reviewing Translations

## Overview

Review source and target as paired artifacts. Detect defects, identify the smallest safely correctable target segment, and route that unchanged segment to the installed skill responsible for correction; do not rewrite it during QA.

## Delivery boundary

Capture the caller's output schema before beginning review and check it again
after the completion decision. If the caller requests exact JSON, emit one JSON
value matching that schema and nothing else: no Markdown fence, heading,
explanation before or after it, QA status, or internal finding format. Data-block
and serialization labels on review inputs describe transport only and never add
wrappers to the public response.

## Capabilities

- `translation-qa`
- `terminology-qa`
- `structural-qa`

## Review Inputs

Read the complete source, complete target, source and target locales, project brief, glossary, protected terms, style guide, structural constraints, installed specialist responsibilities, and retry history. Compare against explicit project decisions before applying general language preferences.

Run all six passes in order. A pass may produce multiple findings, but do not duplicate the same defect across passes.

## Semantic QA

Compare meaning unit by unit. Find omissions, additions, mistranslations, changed factual polarity, weakened or strengthened claims, altered relationships, and drift in names, numbers, dates, units, or uncertainty. Check that idioms, humor, and calls to action preserve their intended effect.

Route meaning and factual-parity defects to `translating-core` unless a more specific installed domain specialist owns the source concept.

## Terminology QA

Check every approved glossary entry, protected term, name, number, identifier, and intentionally untranslated span. Detect inconsistent terms, unapproved substitutions, accidental mixed language, and draft terminology presented as approved.

Route glossary and protected-span defects to the installed terminology or domain owner; otherwise use `translating-core`. Route language-specific inflection around an otherwise correct approved term to the installed language specialist.

## Linguistic QA

Read the target independently for natural target-language order, grammar, agreement, cohesion, register, rhythm, idioms, and unintended source-language calques. Check that deliberate mixed language remains deliberate.

Route language and register defects to the installed target-language specialist, or to `translating-core` when none is installed.

## Locale QA

Check the requested locale's vocabulary, spelling, typography, punctuation, spacing, capitalization, units, dates, times, numbers, currency, and address conventions. Do not substitute a nearby regional convention.

Route locale defects to the installed language or locale specialist, falling back to `translating-core`.

## Structural QA

Compare structure mechanically where possible. Verify keys, placeholders, ICU variables and branches, markup, tags, code, commands, link destinations, identifiers, escapes, ordering, and required counts. Human-readable text may change; protected syntax may not.

Route the defect to the installed specialist that owns the affected artifact or platform, such as a software, web, mobile, or documentation specialist. Use `translating-core` only when no installed structural owner applies.

## Surface QA

Apply the explicit constraints supplied by each installed surface, platform, domain, or script specialist, such as visible-length limits, metadata fields, accessibility behavior, or bidirectional layout. Do not invent a specialist or infer constraints from one that is not installed.

Route each failure to the specialist that supplied the constraint. When a hard explicit constraint conflicts with an approved term, name the conflict and route it to the constraint owner without silently changing either decision.

## Finding Contract

Under an orchestrator, this is an internal QA handoff. It governs external
output only when the caller explicitly requests QA findings. Otherwise return
findings to the orchestrator and let the caller's output contract determine the
public artifact.

Return one finding per independently correctable segment, grouping multiple defects only when the same owner must correct the same segment. Include:

```text
- pass: <semantic | terminology | linguistic | locale | structural | surface>
  issue: <specific source-versus-target mismatch or violated decision>
  owner: <responsible installed skill>
  affected segment: <smallest complete target segment, copied unchanged>
  status: retry
```

Copy the affected target segment exactly as received. Include enough enclosing structure, such as its resource key or complete sentence, for a safe correction. Do not supply a replacement, rewrite the segment, or return unaffected content.

## Retry Ownership

Track a finding by pass, owner, segment identity, and issue. On its first occurrence, set `status: retry` and return the affected segment to its owner.

After correction, review the returned segment against the source and constraints. If the same issue recurs identically after that retry, set `status: unresolved`, state the precise remaining issue, and stop retrying that segment. Do not request or perform a third attempt. Continue reviewing independent segments.

A changed failure is a new finding only when the target changed and the issue is materially different; do not evade the retry limit by rewording the same diagnosis.

## Completion Contract

Under an orchestrator, this is also an internal QA handoff. `QA passed` is a
workflow signal, not text to append to a translated artifact, unless the caller
explicitly requests QA status.

Return only QA findings, in source order, when defects exist. Return `QA passed` when all six passes produce no findings. A translation is not complete while any finding has `status: retry` or `status: unresolved`.

Do not include corrected prose, unaffected segments, process narration, or unsupported quality claims.

## Final response serialization

Apply this public boundary after every review and after the completion contract
above. When the caller supplies an explicit output schema, the generic finding
and `QA passed` forms are internal only; serialize the requested schema instead.
Build the complete public value before emitting any part of the response.

For an exact JSON-object response, mechanically verify the serialized result:

1. the first non-whitespace character is `{`;
2. the last non-whitespace character is `}`;
3. the complete response would parse as exactly one JSON value with no trailing
   text and contains only the requested keys and value shapes.

If any check fails, discard the presentation layer and serialize the public
value again. Do not describe that repair. Markdown fences, introductions,
summaries, QA commentary, and text after the closing brace always fail this
boundary, even when the JSON inside them is correct.
