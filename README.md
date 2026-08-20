# Human Translation Skills

Human Translation Skills is a portable suite of Agent Skills for translating
product content so it sounds natural in the target locale while preserving
meaning, terminology, structure, and platform constraints.

Install the suite once and start with `translating-products`. The orchestrator
selects only the language, writing-system, product-surface, platform, and
review skills needed for the request. It works with Claude Code, Codex, Cursor,
and other hosts that support the open `SKILL.md` format.

> **AI disclosure:** Translations produced with this suite are AI-generated
> and have not been reviewed by a human translator. Record any human review in
> your own approval workflow.

## Install

Install every skill globally for all detected agents:

```bash
npx skills add BrunoMiguens/translating-products-skill --all --global
```

Restart the agent applications after installation. To preview the skills
without installing them:

```bash
npx skills add BrunoMiguens/translating-products-skill --list
```

<details>
<summary>Other installation and discovery commands</summary>

Preview or install from a local checkout:

```bash
npx skills add . --list
npx skills add . --all
```

Install the complete suite for one host:

```bash
npx skills add BrunoMiguens/translating-products-skill --skill '*' --agent claude-code
npx skills add BrunoMiguens/translating-products-skill --skill '*' --agent codex
npx skills add BrunoMiguens/translating-products-skill --skill '*' --agent cursor
npx skills add BrunoMiguens/translating-products-skill --skill '*' --agent universal
```

Install one independently discoverable specialist:

```bash
npx skills add BrunoMiguens/translating-products-skill --skill translating-japanese --agent claude-code
```

</details>

See the [getting-started guide](docs/getting-started.md) for project-local and
host-specific installation, updates, verification, setup, and example prompts.

## Use it

Ask for the product outcome in ordinary language. You do not need to choose
the specialist skills yourself:

```text
Review all Polish translations of the questions and emails in this project.
Preserve placeholders and formatting, use the product's existing terminology,
and report anything that sounds unnatural or changes the meaning.
```

You can name the orchestrator when a host needs an explicit skill reference:

```text
Use translating-products to translate this iOS onboarding flow into Japanese
and Arabic for Japan and Saudi Arabia.
```

If the repository has no approved translation context, the agent first helps
you create `.translation/` configuration and asks for approval before it
translates anything.

## What the suite handles

| Need | Selected guidance |
| --- | --- |
| Meaning, naturalness, tone, and terminology | Core translation and review |
| Web, documentation, marketing, or store listings | Product-surface specialist |
| iOS, Android, Flutter, or localization resource files | Platform and format specialist |
| Arabic or Hebrew interfaces | Language specialist plus RTL guidance |
| Portuguese, Japanese, Polish, and other covered locales | Matching language and locale specialist |
| Several target locales | Isolated per-locale routes, optionally using sub-agents when beneficial |

The workflow is always:

```text
inspect → translate → refine → integrate → review
```

Project evidence comes first: approved glossary and style guidance, translation
memory, verified existing copy, related strings, and protected values. Web
research is used only for a concrete unresolved question. Unknown skills are
never downloaded during a translation task.

## Documentation

- [Getting started](docs/getting-started.md) — install, update, verify, and run
  your first translation.
- [Architecture](docs/architecture.md) — routing, authority, project context,
  research, sub-agents, external skills, and review.
- [Benchmarking](docs/benchmarking.md) — choose between the controlled PT-PT
  benchmark and private product-review regression workflows.
- [Contributing](CONTRIBUTING.md) — add a language, surface, platform, adapter,
  source, or evaluation.
- [Release checklist](docs/release-checklist.md) — prerelease gates and evidence.

## Included skills

The inventory below is generated from [`skills-manifest.json`](skills-manifest.json).
The [capability catalog](skills/translating-products/references/capability-catalog.md)
contains the machine-oriented routing metadata.

<details>
<summary>Show all bundled skills</summary>

<!-- skill-inventory:start -->
Suite version: `0.4.0`

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
| [`translating-polish`](skills/translating-polish/) | `language` | Use when refining Polish translations for Poland, including address strategy, register, aspect, case government, collocations, agreement, and natural product or financial wording. |
<!-- skill-inventory:end -->

</details>

## Provenance and reproducibility

Adapted sources are pinned in the manifest by repository, commit, path,
license, and SHA-256 checksum. [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
contains their attribution and license terms. Source updates require a reviewed
diff, compatible license, new immutable pin and checksum, updated adapter
evidence, and passing evaluations.
