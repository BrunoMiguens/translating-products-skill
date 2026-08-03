# John Rogers Apple Localization Adapter

## Source contract

- Source ID: `johnrogers-apple-localization`
- Repository: https://github.com/johnrogers/claude-swift-engineering
- Path: `plugins/swift-engineering/skills/localization/SKILL.md`
- Immutable commit: `1dc2cf4d020bd524168f20bec95104da6cb2888c`
- License: `MIT`
- Adaptation mode: concepts are restated in `translating-ios`; the upstream
  skill and its private reference tree are not loaded at runtime.

## Included concepts

- Prefer String Catalog resource ownership and locale-aware Apple APIs over
  hardcoded user-visible strings.
- Preserve catalog keys, variation axes, plural behavior, substitutions, and
  contextual translator comments.
- Distinguish SwiftUI localized literals and deferred localized resources from
  runtime interpolation that bypasses extraction.
- Validate with pseudo-localization, accessibility, and RTL/device rendering.

## Excluded host-specific behavior

- Do not follow the upstream instruction to load every possible reference; the
  referenced files are not bundled by this adapter.
- Do not copy Xcode menu steps, build settings, build-phase assertions, or
  exact API availability without checking the target project's Xcode and OS
  deployment contract.
- Do not mutate an Xcode project, export localizations, or install a plugin for
  a translation-only request.

## Suite capability mapping

| Capability | Adapted authority |
| --- | --- |
| `platform:ios` | `translating-ios` owns Apple resource, API, accessibility, device, and build constraints. |
| `xcstrings` | `translating-ios` owns String Catalog identity, substitutions, variation axes, and compile/export checks. |
| `swiftui` | `translating-ios` owns extraction-safe SwiftUI/UIKit localization boundaries. |

## Authority and conflicts

The target project's deployment, catalog, and build configuration outrank
upstream examples. Engineering owns Apple resource and API contracts;
`translating-core` owns target prose; language/script specialists own grammar
and directionality; `translating-mobile` owns shared mobile constraints. When
API availability is unresolved and material, use narrow authoritative research.

## Attribution and license

Adapted from the pinned John Rogers source above under the MIT License.
Copyright (c) 2024-2026 John Rogers. The applicable license notice is retained
in `THIRD_PARTY_NOTICES.md`.

## Adaptation evaluation cases

1. A `.xcstrings` translation preserves catalog identity, substitutions,
   plural/device variations, and translator context while changing only
   target-language values.
2. A SwiftUI view using runtime interpolation is classified against the
   project's extraction contract instead of being assumed localizable.
3. The built app is checked with pseudo-localization, VoiceOver, RTL, Dynamic
   Type, and representative Apple devices; hardcoded customer text blocks
   release.
