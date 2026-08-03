# Eronred App Store Adapter

## Source contract

- Source ID: `eronred-app-store`
- Repository: https://github.com/Eronred/aso-skills
- Path: `skills/localization/SKILL.md`
- Immutable commit: `f97c943d44481dd3e29e2deaf672fc3c7ee83fa9`
- License: `MIT`
- Adaptation mode: concepts are restated in `translating-app-stores`; the
  upstream skill is not invoked or installed at runtime.

## Included concepts

- Separate store-listing localization from application-UI localization and
  record the target storefront, locale, product state, and supplied limits.
- Translate descriptions and release text with product context while treating
  store keywords as market research rather than literal translations.
- Localize screenshot overlays and verify that imagery, dates, currency,
  number formats, tone, features, and social proof fit the approved market.
- Preserve persuasive structure and product truth while measuring each field
  against the current configured store constraint.

## Excluded host-specific behavior

- Do not invoke upstream keyword, metadata, screenshot, or competitor skills,
  and do not require an App ID unless the actual task needs repository/store
  inspection.
- Do not adopt hardcoded market tiers, scoring weights, ROI/ARPU claims,
  character limits, launch cadence, or expected-impact estimates as current
  facts.
- Do not claim professional, native-speaker, or human ASO review. Record only
  review that actually occurred.

## Suite capability mapping

| Capability | Adapted authority |
| --- | --- |
| `surface:app-store` | `translating-app-stores` owns App Store field classification and supplied/current constraints. |
| `surface:play-store` | `translating-app-stores` owns Play Store field classification and supplied/current constraints. |
| `aso` | `translating-app-stores` separates evidence-based market keyword decisions from translation. |
| `store-metadata` | `translating-app-stores` preserves field identity, limits, claims, and listing coherence. |

## Authority and conflicts

Current official store configuration and explicit product requirements outrank
upstream example limits or market advice. Product and legal owners own claims,
features, prices, and proof; `translating-core` owns target copy; market
research may answer only a concrete unresolved keyword or store-rule question.

## Attribution and license

Adapted from the pinned Eronred source above under the MIT License. Copyright
(c) 2026 Erencan. The applicable license notice is retained in
`THIRD_PARTY_NOTICES.md`.

## Adaptation evaluation cases

1. A listing task distinguishes iOS, Play, and app-UI deliverables and validates
   every metadata field against the project-supplied or currently verified
   storefront limit.
2. A German keyword decision uses current German market evidence rather than a
   literal English keyword translation, then records the narrow decision.
3. Localized descriptions and screenshots preserve all claims and product
   state; an invented local press quote, price, feature, or performance claim
   blocks delivery.
