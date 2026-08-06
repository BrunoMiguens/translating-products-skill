# Capability catalog

Suite version: `0.2.0`

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
- Capabilities: `orchestrator`, `project-bootstrap`, `skill-routing`, `subagent-routing`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `capabilities`: `orchestrator`
- Phases: `inspect`
- Specificity: `universal`
- Required context: none
- Conflicts: none
- Supersedes: none

## core

### translating-core

- Version: `0.1.0`
- Capabilities: `core-translation`, `terminology`, `cultural-adaptation`, `structural-fidelity`
- Depends on: `reviewing-translations`
- Selectors: `capabilities`: `core-translation`
- Phases: `translate`
- Specificity: `universal`
- Required context: `source_locale`, `target_locale`
- Conflicts: none
- Supersedes: none

## quality

### reviewing-translations

- Version: `0.1.0`
- Capabilities: `translation-qa`, `terminology-qa`, `structural-qa`
- Depends on: none
- Selectors: `capabilities`: `translation-qa`
- Phases: `review`
- Specificity: `quality`
- Required context: `source_locale`, `target_locale`
- Conflicts: none
- Supersedes: none

## surface

### translating-web

- Version: `0.1.0`
- Capabilities: `surface:web`, `html`, `markdown`, `accessibility`, `hreflang`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `web` OR `formats`: `html`, `markdown`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

### localizing-software

- Version: `0.1.0`
- Capabilities: `surface:software`, `icu`, `placeholders`, `plurals`, `locale-formatting`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `software` OR `formats`: `icu`, `po`, `xliff`
- Phases: `inspect`, `integrate`
- Specificity: `format`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

### translating-mobile

- Version: `0.1.0`
- Capabilities: `surface:mobile`, `mobile-ui`, `accessibility`, `pseudo-localization`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `mobile`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

## platform

### translating-ios

- Version: `0.1.0`
- Capabilities: `platform:ios`, `xcstrings`, `swiftui`, `apple-locales`
- Depends on: `translating-mobile`, `reviewing-translations`
- Selectors: `platforms`: `ios` OR `formats`: `xcstrings`
- Phases: `inspect`, `integrate`
- Specificity: `platform`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

### translating-android

- Version: `0.1.0`
- Capabilities: `platform:android`, `string-resources`, `compose`, `android-locales`
- Depends on: `translating-mobile`, `reviewing-translations`
- Selectors: `platforms`: `android` OR `formats`: `android-xml`
- Phases: `inspect`, `integrate`
- Specificity: `platform`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

### translating-flutter

- Version: `0.1.0`
- Capabilities: `platform:flutter`, `arb`, `flutter-localizations`, `icu`
- Depends on: `translating-mobile`, `reviewing-translations`
- Selectors: `platforms`: `flutter` OR `formats`: `arb`
- Phases: `inspect`, `integrate`
- Specificity: `platform`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

## surface

### translating-app-stores

- Version: `0.1.0`
- Capabilities: `surface:app-store`, `surface:play-store`, `aso`, `store-metadata`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `app-store`, `play-store`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

### translating-marketing

- Version: `0.1.0`
- Capabilities: `surface:marketing`, `transcreation`, `brand-voice`, `calls-to-action`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `marketing`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`, `audience`, `purpose`
- Conflicts: none
- Supersedes: none

### translating-documentation

- Version: `0.1.0`
- Capabilities: `surface:documentation`, `code-preservation`, `technical-terminology`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `surfaces`: `documentation`
- Phases: `inspect`, `integrate`
- Specificity: `surface`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

## script

### translating-rtl

- Version: `0.1.0`
- Capabilities: `script:rtl`, `bidi`, `mirroring`, `mixed-direction`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `scripts`: `rtl` OR `capabilities`: `bidi`
- Phases: `refine`, `integrate`
- Specificity: `writing-system`
- Required context: `target_locale`
- Conflicts: none
- Supersedes: none

## language

### translating-arabic

- Version: `0.1.0`
- Capabilities: `language:arabic`, `locale:ar`
- Depends on: `translating-core`, `translating-rtl`, `reviewing-translations`
- Selectors: `languages`: `ar`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none

### translating-hebrew

- Version: `0.1.0`
- Capabilities: `language:hebrew`, `locale:he`
- Depends on: `translating-core`, `translating-rtl`, `reviewing-translations`
- Selectors: `languages`: `he`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none

### translating-japanese

- Version: `0.1.0`
- Capabilities: `language:japanese`, `locale:ja`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `ja`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none

### translating-chinese

- Version: `0.1.0`
- Capabilities: `language:chinese`, `locale:zh-Hans`, `locale:zh-Hant`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `zh`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none

### translating-korean

- Version: `0.1.0`
- Capabilities: `language:korean`, `locale:ko`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `ko`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none

### translating-portuguese

- Version: `0.1.0`
- Capabilities: `language:portuguese`, `locale:pt-BR`, `locale:pt-PT`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `pt`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none

### translating-spanish

- Version: `0.1.0`
- Capabilities: `language:spanish`, `locale:es-ES`, `locale:es-419`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `es`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none

### translating-french

- Version: `0.1.0`
- Capabilities: `language:french`, `locale:fr-FR`, `locale:fr-CA`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `fr`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none

### translating-german

- Version: `0.1.0`
- Capabilities: `language:german`, `locale:de-DE`, `locale:de-AT`, `locale:de-CH`
- Depends on: `translating-core`, `reviewing-translations`
- Selectors: `languages`: `de`
- Phases: `refine`
- Specificity: `language`
- Required context: `target_locale`, `register`
- Conflicts: none
- Supersedes: none
