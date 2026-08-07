# Capability catalog

Suite version: `0.2.0`

## Disclosure

Translations produced with this suite are AI-generated and have not been reviewed by a human translator.

## Authority order

1. `explicit-user-requirements`
2. `approved-project-configuration`
3. `core-semantic-fidelity`
4. `domain-terminology`
5. `language-locale-mechanics`
6. `product-platform-formatting`
7. `stylistic-preferences`

## orchestrator

### translating-products

- Version: `0.2.0`
- Description: Use when orchestrating product translation projects that may need project setup, language routing, platform routing, multiple translation skills, or translation QA.
- Capabilities: `orchestrator`, `project-bootstrap`, `skill-routing`, `subagent-routing`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `capabilities`: `orchestrator`
- Phases: `inspect`
- Specificity: `universal`
- Required context: none
- Conflicts: none
- Supersedes: none
- Ownership: `orchestrator`: `inspect`; `project-bootstrap`: `inspect`; `skill-routing`: `inspect`; `subagent-routing`: `inspect`

## core

### translating-core

- Version: `0.1.0`
- Description: Use when producing meaning-faithful, natural, culturally appropriate translations for any language translation before applying product or language specialists.
- Capabilities: `core-translation`, `terminology`, `cultural-adaptation`, `structural-fidelity`
- Depends on: `reviewing-translations`
- Selectors: `capabilities`: `core-translation`
- Phases: `translate`
- Specificity: `universal`
- Required context: `source_locale`, `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `core-translation`: `translate`; `terminology`: `translate`; `cultural-adaptation`: `translate`; `structural-fidelity`: `translate`

## quality

### reviewing-translations

- Version: `0.1.0`
- Description: Use when reviewing AI-generated translations for meaning, naturalness, terminology, locale, and structure after translation or when auditing localized content.
- Capabilities: `translation-qa`, `terminology-qa`, `structural-qa`
- Depends on: none
- Selectors: `capabilities`: `translation-qa`
- Phases: `review`
- Specificity: `quality`
- Required context: `source_locale`, `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `translation-qa`: `review`; `terminology-qa`: `review`; `structural-qa`: `review`

## surface

### translating-web

- Version: `0.1.0`
- Description: Use when localizing websites while preserving markup, metadata, accessibility, links, and locale signals for HTML, Markdown, or web-content translation.
- Capabilities: `surface:web`, `html`, `markdown`, `accessibility`, `hreflang`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `web` OR `formats`: `html`, `markdown`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `surface:web`: `inspect`, `integrate`; `html`: `inspect`, `integrate`; `markdown`: `inspect`, `integrate`; `accessibility`: `inspect`, `integrate`; `hreflang`: `inspect`, `integrate`

### localizing-software

- Version: `0.1.0`
- Description: Use when localizing software resources while preserving ICU syntax, placeholders, plurals, keys, and locale formatting for application resource files and UI strings.
- Capabilities: `surface:software`, `icu`, `placeholders`, `plurals`, `locale-formatting`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `software` OR `formats`: `icu`, `po`, `xliff`
- Phases: `inspect`, `integrate`
- Specificity: `format`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `surface:software`: `inspect`, `integrate`; `icu`: `inspect`, `integrate`; `placeholders`: `inspect`, `integrate`; `plurals`: `inspect`, `integrate`; `locale-formatting`: `inspect`, `integrate`

### translating-mobile

- Version: `0.1.0`
- Description: Use when applying shared mobile-localization constraints for mobile UI, accessibility strings, screenshots, expansion, truncation, and pseudo-localization.
- Capabilities: `surface:mobile`, `mobile-ui`, `accessibility`, `pseudo-localization`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `mobile`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `surface:mobile`: `inspect`, `integrate`; `mobile-ui`: `inspect`, `integrate`; `accessibility`: `inspect`, `integrate`; `pseudo-localization`: `inspect`, `integrate`

## platform

### translating-ios

- Version: `0.1.0`
- Description: Use when localizing Apple-platform resources and UI copy for String Catalogs, SwiftUI, UIKit, VoiceOver, and Apple locale behavior.
- Capabilities: `platform:ios`, `xcstrings`, `swiftui`, `apple-locales`
- Depends on: `translating-mobile`, `reviewing-translations`
- Selectors: `platforms`: `ios` OR `formats`: `xcstrings`
- Phases: `inspect`, `integrate`
- Specificity: `platform`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `platform:ios`: `inspect`, `integrate`; `xcstrings`: `inspect`, `integrate`; `swiftui`: `inspect`, `integrate`; `apple-locales`: `inspect`, `integrate`

### translating-android

- Version: `0.1.0`
- Description: Use when localizing Android resources and UI copy for strings.xml, plurals, Compose, views, TalkBack, pseudo-locales, and bidirectional layouts.
- Capabilities: `platform:android`, `string-resources`, `compose`, `android-locales`
- Depends on: `translating-mobile`, `reviewing-translations`
- Selectors: `platforms`: `android` OR `formats`: `android-xml`
- Phases: `inspect`, `integrate`
- Specificity: `platform`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `platform:android`: `inspect`, `integrate`; `string-resources`: `inspect`, `integrate`; `compose`: `inspect`, `integrate`; `android-locales`: `inspect`, `integrate`

### translating-flutter

- Version: `0.1.0`
- Description: Use when localizing Flutter applications for ARB, generated localization, ICU messages, flutter_localizations, accessibility, and responsive layout checks.
- Capabilities: `platform:flutter`, `arb`, `flutter-localizations`, `icu`
- Depends on: `translating-mobile`, `reviewing-translations`
- Selectors: `platforms`: `flutter` OR `formats`: `arb`
- Phases: `inspect`, `integrate`
- Specificity: `platform`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `platform:flutter`: `inspect`, `integrate`; `arb`: `inspect`, `integrate`; `flutter-localizations`: `inspect`, `integrate`; `icu`: `inspect`, `integrate`

## surface

### translating-app-stores

- Version: `0.1.0`
- Description: Use when localizing App Store and Play Store product listings for store metadata, screenshots, keywords, market adaptation, and listing constraints.
- Capabilities: `surface:app-store`, `surface:play-store`, `aso`, `store-metadata`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `app-store`, `play-store`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `surface:app-store`: `inspect`, `integrate`; `surface:play-store`: `inspect`, `integrate`; `aso`: `inspect`, `integrate`; `store-metadata`: `inspect`, `integrate`

### translating-marketing

- Version: `0.1.0`
- Description: Use when transcreating marketing content while preserving brand voice and conversion intent for campaigns, landing pages, calls to action, and cultural adaptation.
- Capabilities: `surface:marketing`, `transcreation`, `brand-voice`, `calls-to-action`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `marketing`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`, `audience`, `purpose`
- Conflicts: none
- Supersedes: none
- Ownership: `surface:marketing`: `inspect`, `integrate`; `transcreation`: `inspect`, `integrate`; `brand-voice`: `inspect`, `integrate`; `calls-to-action`: `inspect`, `integrate`

### translating-documentation

- Version: `0.1.0`
- Description: Use when localizing technical documentation while preserving code, commands, identifiers, diagrams, references, and technical terminology for guides, API references, READMEs, and developer documentation.
- Capabilities: `surface:documentation`, `code-preservation`, `technical-terminology`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `documentation`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `surface:documentation`: `inspect`, `integrate`; `code-preservation`: `inspect`, `integrate`; `technical-terminology`: `inspect`, `integrate`

## script

### translating-rtl

- Version: `0.1.0`
- Description: Use when applying right-to-left script and bidirectional UI rules for Arabic, Hebrew, Persian, Urdu, or mixed RTL/LTR product content.
- Capabilities: `script:rtl`, `bidi`, `mirroring`, `mixed-direction`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `scripts`: `rtl` OR `capabilities`: `bidi`
- Phases: `refine`, `integrate`
- Specificity: `writing-system`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none
- Ownership: `script:rtl`: `refine`, `integrate`; `bidi`: `refine`, `integrate`; `mirroring`: `refine`, `integrate`; `mixed-direction`: `refine`, `integrate`

## language

### translating-arabic

- Version: `0.1.0`
- Description: Use when refining Arabic translations for the requested locale and register, including Arabic wording, agreement, terminology, punctuation, and product naturalness.
- Capabilities: `language:arabic`, `locale:ar`
- Depends on: `translating-core`, `translating-rtl`, `reviewing-translations`
- Selectors: `languages`: `ar`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:arabic`: `refine`; `locale:ar`: `refine`

### translating-hebrew

- Version: `0.1.0`
- Description: Use when refining Hebrew translations for the requested locale and register, including Hebrew wording, gender, number, construct forms, punctuation, and transliteration.
- Capabilities: `language:hebrew`, `locale:he`
- Depends on: `translating-core`, `translating-rtl`, `reviewing-translations`
- Selectors: `languages`: `he`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:hebrew`: `refine`; `locale:he`: `refine`

### translating-japanese

- Version: `0.1.0`
- Description: Use when refining Japanese translations for the requested locale and register, including politeness, honorifics, omission, counters, loanwords, punctuation, and concise UI copy.
- Capabilities: `language:japanese`, `locale:ja`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `ja`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:japanese`: `refine`; `locale:ja`: `refine`

### translating-chinese

- Version: `0.1.0`
- Description: Use when refining Chinese translations for script and region, including Simplified or Traditional Chinese, classifiers, terminology, punctuation, and product conventions.
- Capabilities: `language:chinese`, `locale:zh-Hans`, `locale:zh-Hant`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `zh`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:chinese`: `refine`; `locale:zh-Hans`: `refine`; `locale:zh-Hant`: `refine`

### translating-korean

- Version: `0.1.0`
- Description: Use when refining Korean translations for the requested locale and register, including speech level, honorifics, spacing, counters, loanwords, and concise UI copy.
- Capabilities: `language:korean`, `locale:ko`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `ko`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:korean`: `refine`; `locale:ko`: `refine`

### translating-portuguese

- Version: `0.1.0`
- Description: Use when refining Portuguese translations for Brazil or Portugal, including regional grammar, vocabulary, spelling, formality, and product register.
- Capabilities: `language:portuguese`, `locale:pt-BR`, `locale:pt-PT`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `pt`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:portuguese`: `refine`; `locale:pt-BR`: `refine`; `locale:pt-PT`: `refine`

### translating-spanish

- Version: `0.1.0`
- Description: Use when refining Spanish translations for Spain, Latin America, or a named region, including regional vocabulary, pronouns, formality, and product register.
- Capabilities: `language:spanish`, `locale:es-ES`, `locale:es-419`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `es`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:spanish`: `refine`; `locale:es-ES`: `refine`; `locale:es-419`: `refine`

### translating-french

- Version: `0.1.0`
- Description: Use when refining French translations for France or Canada, including regional vocabulary, formality, typography, spacing, anglicisms, and product register.
- Capabilities: `language:french`, `locale:fr-FR`, `locale:fr-CA`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `fr`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:french`: `refine`; `locale:fr-FR`: `refine`; `locale:fr-CA`: `refine`

### translating-german

- Version: `0.1.0`
- Description: Use when refining German translations for Germany, Austria, or Switzerland, including formality, terminology, compounds, capitalization, expansion, and UI constraints.
- Capabilities: `language:german`, `locale:de-DE`, `locale:de-AT`, `locale:de-CH`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `de`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
- Ownership: `language:german`: `refine`; `locale:de-DE`: `refine`; `locale:de-AT`: `refine`; `locale:de-CH`: `refine`
