# Capability catalog

Suite version: `0.1.0`

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

- Version: `0.1.0`
- Capabilities: `orchestrator`, `project-bootstrap`, `skill-routing`, `subagent-routing`
- Depends on: `translating-core`, `reviewing-translations`

## core

### translating-core

- Version: `0.1.0`
- Capabilities: `core-translation`, `terminology`, `cultural-adaptation`, `structural-fidelity`
- Depends on: `reviewing-translations`

## quality

### reviewing-translations

- Version: `0.1.0`
- Capabilities: `translation-qa`, `terminology-qa`, `structural-qa`
- Depends on: none

## surface

### translating-web

- Version: `0.1.0`
- Capabilities: `surface:web`, `html`, `markdown`, `accessibility`, `hreflang`
- Depends on: `translating-core`, `reviewing-translations`

### localizing-software

- Version: `0.1.0`
- Capabilities: `surface:software`, `icu`, `placeholders`, `plurals`, `locale-formatting`
- Depends on: `translating-core`, `reviewing-translations`

### translating-mobile

- Version: `0.1.0`
- Capabilities: `surface:mobile`, `mobile-ui`, `accessibility`, `pseudo-localization`
- Depends on: `translating-core`, `reviewing-translations`

### translating-app-stores

- Version: `0.1.0`
- Capabilities: `surface:app-store`, `surface:play-store`, `aso`, `store-metadata`
- Depends on: `translating-core`, `reviewing-translations`

### translating-marketing

- Version: `0.1.0`
- Capabilities: `surface:marketing`, `transcreation`, `brand-voice`, `calls-to-action`
- Depends on: `translating-core`, `reviewing-translations`

### translating-documentation

- Version: `0.1.0`
- Capabilities: `surface:documentation`, `code-preservation`, `technical-terminology`
- Depends on: `translating-core`, `reviewing-translations`

## platform

### translating-ios

- Version: `0.1.0`
- Capabilities: `platform:ios`, `xcstrings`, `swiftui`, `apple-locales`
- Depends on: `translating-mobile`, `reviewing-translations`

### translating-android

- Version: `0.1.0`
- Capabilities: `platform:android`, `string-resources`, `compose`, `android-locales`
- Depends on: `translating-mobile`, `reviewing-translations`

### translating-flutter

- Version: `0.1.0`
- Capabilities: `platform:flutter`, `arb`, `flutter-localizations`, `icu`
- Depends on: `translating-mobile`, `reviewing-translations`

## script

### translating-rtl

- Version: `0.1.0`
- Capabilities: `script:rtl`, `bidi`, `mirroring`, `mixed-direction`
- Depends on: `translating-core`, `reviewing-translations`

## language

### translating-arabic

- Version: `0.1.0`
- Capabilities: `language:arabic`, `locale:ar`
- Depends on: `translating-core`, `translating-rtl`, `reviewing-translations`

### translating-hebrew

- Version: `0.1.0`
- Capabilities: `language:hebrew`, `locale:he`
- Depends on: `translating-core`, `translating-rtl`, `reviewing-translations`

### translating-japanese

- Version: `0.1.0`
- Capabilities: `language:japanese`, `locale:ja`
- Depends on: `translating-core`, `reviewing-translations`

### translating-chinese

- Version: `0.1.0`
- Capabilities: `language:chinese`, `locale:zh-Hans`, `locale:zh-Hant`
- Depends on: `translating-core`, `reviewing-translations`

### translating-korean

- Version: `0.1.0`
- Capabilities: `language:korean`, `locale:ko`
- Depends on: `translating-core`, `reviewing-translations`

### translating-portuguese

- Version: `0.1.0`
- Capabilities: `language:portuguese`, `locale:pt-BR`, `locale:pt-PT`
- Depends on: `translating-core`, `reviewing-translations`

### translating-spanish

- Version: `0.1.0`
- Capabilities: `language:spanish`, `locale:es-ES`, `locale:es-419`
- Depends on: `translating-core`, `reviewing-translations`

### translating-french

- Version: `0.1.0`
- Capabilities: `language:french`, `locale:fr-FR`, `locale:fr-CA`
- Depends on: `translating-core`, `reviewing-translations`

### translating-german

- Version: `0.1.0`
- Capabilities: `language:german`, `locale:de-DE`, `locale:de-AT`, `locale:de-CH`
- Depends on: `translating-core`, `reviewing-translations`
