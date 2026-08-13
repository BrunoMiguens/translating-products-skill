# Human Translation Skills

Human Translation Skills is a portable Agent Skills suite for producing
native-sounding, culturally appropriate product translations while preserving
meaning, terminology, structure, and platform constraints. One orchestrator
selects the smallest useful combination from the manifest's independently
discoverable core, quality, surface, platform, writing-system, and language
skills.

> **AI disclosure:** Translations produced with this suite are AI-generated
> and have not been reviewed by a human translator. If your project adds human
> review, record that review in your own approval workflow rather than assuming
> it from use of these skills.

## Benchmark quality

The [PT-PT benchmark runbook](docs/benchmarking.md) defines a frozen, paired
comparison of the same exact agent/model/configuration under a normal prompt and
the translation suite. It combines blind human preference and MQM-lite review,
deterministic product-integrity checks, paired statistics, and diagnostic-only
learned/operational measures. No result is claimed until all 405 outputs, 198
blind presentations, the reviewer attestation and annotation lock, post-lock
adjudication, unblinding, and every predeclared gate are complete and bound to
the exact evidence bytes.

The skills use the open `SKILL.md` format and are designed for Claude Code,
Codex, Cursor, and universal Agent Skills hosts. They do not depend on a
host-specific invocation syntax.

## Install and discover

Run these commands from a clone of this repository to preview the exact
manifest-derived skills that will be installed or install the complete suite
locally:

```bash
npx skills add . --list
npx skills add . --all
```

In the remote examples below, `OWNER/REPOSITORY` means the GitHub `owner/repo`
slug of this repository's configured remote. It is a placeholder, not a claim
that a public remote already exists.

Install every repository skill for a specific host:

```bash
npx skills add OWNER/REPOSITORY --skill '*' --agent claude-code
npx skills add OWNER/REPOSITORY --skill '*' --agent codex
npx skills add OWNER/REPOSITORY --skill '*' --agent cursor
npx skills add OWNER/REPOSITORY --skill '*' --agent universal
```

Install every skill for all detected agents with `--all`:

```bash
npx skills add OWNER/REPOSITORY --all
```

Install one independently discoverable specialist instead:

```bash
npx skills add OWNER/REPOSITORY --skill translating-japanese --agent claude-code
```

`--skill '*'` selects all skills while `--agent` chooses explicit hosts.
`--all` accepts the CLI's all-skills/all-agents defaults. Individual skill
installation does not provide transitive dependency installation, so use the
full-suite command when you want automatic orchestration.

## How translation is orchestrated

Start with `translating-products`. For each exact target locale, it builds a
task profile from language, locale range, writing system, surface, platform,
format, domain, requested capability, audience, purpose, register, and
protected structural constraints. It declaratively matches that profile to the
catalog's selectors, expands declared dependencies, and runs the smallest
sufficient route. Routing does not depend on a hardcoded surface-to-skill or
language-product classification.

Each route executes `inspect → translate → refine → integrate → review`.
Surface, platform, and format skills inspect and integrate protected product
structure; core produces the first semantic draft; writing-system, language,
and locale guidance refines only its owned linguistic dimension; review checks
the completed result. Locale guidance refines language guidance, which refines
broader writing-system defaults, without overriding semantic fidelity,
approved terminology, or protected values.

A task can therefore use several skills without loading unrelated guidance.
Latin, CJK, and RTL are possible coherent writing-system capability modules,
not an exhaustive taxonomy. A request for Portuguese, Japanese, and Arabic
creates three independent linguistic branches: Portuguese applies its locale
guidance, Japanese applies its language guidance, and Arabic can add the RTL
module. Shared artifact inspection and approved context are reused, but drafts
and QA stay isolated until reintegration so one locale cannot leak vocabulary
or register into another.

If the project has no translation context, the orchestrator pauses before
translation and asks one setup question at a time. After approval it creates:

```text
.translation/
├── project-brief.md
├── locales.yaml
├── glossary.csv
├── style-guide.md
├── protected-terms.txt
├── setup-approval.json
├── decisions.md
├── translation-memory.csv
└── research-sources.md
```

The orchestrator inspects semantic values, not filenames. The approval record
contains the explicit approver and timestamp plus SHA-256 hashes of the five
context files above it; the approval file does not hash itself. Empty glossary
or protected-term collections must be explicitly approved. Changing any
context byte, including line endings, requires approval again, while optional
project-memory files do not invalidate the hashes. A source-locale mismatch or
an unconfigured requested target pauses translation for one focused question.

Existing approved configuration is reused. Research is capability-adaptive:
the agent browses only for a concrete unresolved current, market, or
terminology question, never for routine wording. Sub-agents are used only when
independent locales, scale, terminology work, or review provides a material
benefit; unsupported hosts run the same stages sequentially.

Reviewed external skills can be integrated through the documented adapter
contract when they are already installed and authorized by the user, project,
or compatibility registry. The orchestrator discovers their selectors,
dependencies, phases, specificity, context, conflicts, and authority metadata
through the same public contract it uses for bundled skills. It never downloads
an unknown skill during a translation task.

## Skill inventory

This section is rendered from [`skills-manifest.json`](skills-manifest.json)
by [`scripts/render_catalog.py`](scripts/render_catalog.py). The generated
[capability catalog](skills/translating-products/references/capability-catalog.md)
is the machine-oriented routing view of the same manifest records.

<!-- skill-inventory:start -->
Suite version: `0.3.0`

| Skill | Category | When to use |
| --- | --- | --- |
| [`translating-products`](skills/translating-products/) | `orchestrator` | Use when orchestrating product translation projects that may need project setup, language routing, platform routing, multiple translation skills, or translation QA. |
| [`translating-core`](skills/translating-core/) | `core` | Use when producing meaning-faithful, natural, culturally appropriate translations for any language translation before applying product or language specialists. |
| [`reviewing-translations`](skills/reviewing-translations/) | `quality` | Use when reviewing AI-generated translations for meaning, naturalness, terminology, locale, and structure after translation or when auditing localized content. |
| [`translating-web`](skills/translating-web/) | `surface` | Use when localizing websites while preserving markup, metadata, accessibility, links, and locale signals for HTML, Markdown, or web-content translation. |
| [`localizing-software`](skills/localizing-software/) | `surface` | Use when localizing software resources while preserving ICU syntax, placeholders, plurals, keys, and locale formatting for application resource files and UI strings. |
| [`translating-mobile`](skills/translating-mobile/) | `surface` | Use when applying shared mobile-localization constraints for mobile UI, accessibility strings, screenshots, expansion, truncation, and pseudo-localization. |
| [`translating-ios`](skills/translating-ios/) | `platform` | Use when localizing Apple-platform resources and UI copy for String Catalogs, SwiftUI, UIKit, VoiceOver, and Apple locale behavior. |
| [`translating-android`](skills/translating-android/) | `platform` | Use when localizing Android resources and UI copy for strings.xml, plurals, Compose, views, TalkBack, pseudo-locales, and bidirectional layouts. |
| [`translating-flutter`](skills/translating-flutter/) | `platform` | Use when localizing Flutter applications for ARB, generated localization, ICU messages, flutter_localizations, accessibility, and responsive layout checks. |
| [`translating-app-stores`](skills/translating-app-stores/) | `surface` | Use when localizing App Store and Play Store product listings for store metadata, screenshots, keywords, market adaptation, and listing constraints. |
| [`translating-marketing`](skills/translating-marketing/) | `surface` | Use when transcreating marketing content while preserving brand voice and conversion intent for campaigns, landing pages, calls to action, and cultural adaptation. |
| [`translating-documentation`](skills/translating-documentation/) | `surface` | Use when localizing technical documentation while preserving code, commands, identifiers, diagrams, references, and technical terminology for guides, API references, READMEs, and developer documentation. |
| [`translating-rtl`](skills/translating-rtl/) | `script` | Use when applying right-to-left script and bidirectional UI rules for Arabic, Hebrew, Persian, Urdu, or mixed RTL/LTR product content. |
| [`translating-arabic`](skills/translating-arabic/) | `language` | Use when refining Arabic translations for the requested locale and register, including Arabic wording, agreement, terminology, punctuation, and product naturalness. |
| [`translating-hebrew`](skills/translating-hebrew/) | `language` | Use when refining Hebrew translations for the requested locale and register, including Hebrew wording, gender, number, construct forms, punctuation, and transliteration. |
| [`translating-japanese`](skills/translating-japanese/) | `language` | Use when refining Japanese translations for the requested locale and register, including politeness, honorifics, omission, counters, loanwords, punctuation, and concise UI copy. |
| [`translating-chinese`](skills/translating-chinese/) | `language` | Use when refining Chinese translations for script and region, including Simplified or Traditional Chinese, classifiers, terminology, punctuation, and product conventions. |
| [`translating-korean`](skills/translating-korean/) | `language` | Use when refining Korean translations for the requested locale and register, including speech level, honorifics, spacing, counters, loanwords, and concise UI copy. |
| [`translating-portuguese`](skills/translating-portuguese/) | `language` | Use when refining Portuguese translations for Brazil or Portugal, including regional grammar, vocabulary, spelling, formality, and product register. |
| [`translating-spanish`](skills/translating-spanish/) | `language` | Use when refining Spanish translations for Spain, Latin America, or a named region, including regional vocabulary, pronouns, formality, and product register. |
| [`translating-french`](skills/translating-french/) | `language` | Use when refining French translations for France or Canada, including regional vocabulary, formality, typography, spacing, anglicisms, and product register. |
| [`translating-german`](skills/translating-german/) | `language` | Use when refining German translations for Germany, Austria, or Switzerland, including formality, terminology, compounds, capitalization, expansion, and UI constraints. |
<!-- skill-inventory:end -->

## Sources and reproducibility

The manifest records every adapted source with its upstream repository,
immutable commit, path, license, SHA-256 checksum, capabilities, and local
adapter. [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) preserves
attribution and license terms. The scheduled source verifier downloads only
those immutable files and checks their contents; ordinary pull requests remain
offline and deterministic.

External sources do not update releases automatically. A source change needs
a reviewed diff, a compatible license, a new immutable pin and checksum,
updated adapter evidence, and passing evaluations.

See [the architecture](docs/architecture.md) for trust and routing boundaries
and [the contribution guide](CONTRIBUTING.md) for adding or updating a
specialist.
