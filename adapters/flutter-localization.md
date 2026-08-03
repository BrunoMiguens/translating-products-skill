# Flutter Localization Adapter

## Source contract

- Source ID: `flutter-localization`
- Repository: https://github.com/flutter/agent-plugins
- Path: `skills/flutter-setup-localization/SKILL.md`
- Immutable commit: `8aaa41d87b0d36cdd70dcc219049b6df5bcf124c`
- License: `BSD-3-Clause`
- Adaptation mode: concepts are restated in `translating-flutter`; the pinned
  upstream skill is not executed or installed at runtime.

## Included concepts

- Treat ARB files and their contextual metadata as the editable localization
  source for generated, typed `AppLocalizations` access.
- Preserve placeholder names and types, ICU plural/select structure, and the
  required `other` branch.
- Keep delegates and supported locales consistent with generated localization
  output and compile generated resources as a validation step.
- Provide descriptions and representative placeholder examples so target copy
  has sufficient UI and runtime context.

## Excluded host-specific behavior

- Do not adopt upstream model metadata, terminal commands, checklist state, or
  assume a specific dependency edit, generated import path, or
  `synthetic-package` setting.
- Do not mutate `pubspec.yaml`, `l10n.yaml`, ARB files, or generated output when
  the user requested translation only.
- Do not freeze example locales or tool behavior; inspect the project's pinned
  Flutter generator and configuration.

## Suite capability mapping

| Capability | Adapted authority |
| --- | --- |
| `platform:flutter` | `translating-flutter` owns Flutter resource, generator, widget, accessibility, and layout contracts. |
| `arb` | `translating-flutter` owns ARB keys, metadata, JSON validity, and generated-output boundaries. |
| `icu` | `translating-flutter` preserves plural/select mechanics while language specialists own target grammar. |

## Authority and conflicts

Project configuration and semantic fidelity outrank example setup. Engineering
owns ARB/generator and runtime contracts; `translating-core` owns prose;
language and script specialists own linguistic mechanics; `translating-mobile`
owns shared mobile constraints. Generated files never become the translation
source merely because generator convenience conflicts with this boundary.

## Attribution and license

Adapted from the pinned Flutter Authors source above under the BSD 3-Clause
License. Copyright 2026 The Flutter Authors. The applicable license notice and
conditions are retained in `THIRD_PARTY_NOTICES.md`.

## Adaptation evaluation cases

1. A plural ARB message retains typed placeholder metadata, exact selectors,
   and `other`, compiles under the pinned generator, and exercises locale
   boundary values.
2. A target ARB change is rejected when it edits a key or generated Dart file,
   omits required metadata, or introduces invalid ICU/JSON syntax.
3. Delegate and supported-locale wiring is checked against generated inventory,
   with fallback, accessibility, long-text, and RTL rendering cases.
