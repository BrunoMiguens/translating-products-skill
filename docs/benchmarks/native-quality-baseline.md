# Translation quality baseline for v0.4.0

On 24 November 2026 we finished a blind benchmark of `translating-products` at `b1810d5d5b5626673b0fe0d7cb39a65911adeae9`. The skill often produced competitive wording. Its review still approved incomplete documents, wording that changed a UI action, and files that selected the wrong translation at runtime. This is the baseline for the [implementation plan](../superpowers/plans/2026-11-24-human-translation-quality.md).

## What the run measured

Agents used the ordinary skill entry point, with an approved English-side context for each of 25 open-source projects. We selected up to 200 challenging keys per project. The workers had no access to the evaluator’s published translations or contributor records. Public material may have appeared in model training.

| Measure | Sealed result |
|---|---|
| Requests | 4,642 selected units across 1,519 project, locale and register runs, yielding 288,874 entries. Three publisher inputs labeled English contained German. |
| First attempt | 1,019 full deliveries (67.1%), 296 partial, 110 held or stopped during handoff, 61 clarification requests and 33 technical failures. |
| Verified native delivery including recoveries | 269,234 entries (93.2%). The main table verifies 269,035. A separate publisher-path replay verifies 199 more. |
| Pairs with an attributable published counterpart | 76,519, including 11,351 exact matches. Of 321 target codes, 92 yielded no eligible pair. |
| Differing wording | 61,760 primary blind model judgments had supporting quotations and 3,408 lacked them. The latter received separate evidence checks. |
| Supported primary judgments | Material preference for the skill in 21,356 and for the publisher in 3,490. Style preference for the skill in 16,264 and for the publisher in 3,631. Both acceptable in 16,095, both materially flawed in 192, uncertain in 732. |

Published credits range from an individual editing a string to a team credited for a language pack. Bilingual humans have yet to adjudicate these comparisons. Project coverage is uneven. One project supplied 19,627 pairs and another supplied three. The 3,682 cases where the model materially preferred the publisher or found both versions flawed are leads for review. Exact wording agreement, delivery and bilingual quality need separate measures.

## Findings worth acting on

- **Release verification.** Two final UI strings differed from their review inputs. Long documents passed review with English body text below translated headings. Other documents correctly retained code or compressed prose without dropping facts.
- **Runtime behavior.** In 23 of 142 checked WordPress Core locale and register combinations, delivered catalogs had the wrong number of plural forms or selected the wrong form for a witnessed count. Consumer typechecks found failures in 340 native date-fns modules across 85 locales, including 339 export failures and one method type error. Separately, 8,051 values worked in trusted wrappers. Android Hebrew resources fell back to English for 520 lookups across three deliveries.
- **Language in context.** A state became an instruction, a placeholder for a time read like a destination, and an exclusivity qualification disappeared. Regional spelling and address sometimes matched the correct variant and sometimes crossed between siblings. A historical store title and its generated alternative both exceeded the platform limit.
- **Evidence quality.** A missed native locale alias hid a valid delivery. Some model objections disappeared when checked against the call site. A service refusal, a missing source-side decision and a wrong translation require different dispositions.

The definitive assessed ZIP has SHA-256 `eccfca623d9825a6a2e2114ffc0cb9ca9f86fb1c8c348e737890ca66526b745d`. Custodians can locate it under `benchmark-private/open-source-project-audit/dataset/evaluated-results.zip`. The private report, source/reference archive, attribution links and receipts sit beside that dataset. This page carries aggregate evidence only. [Repository benchmark guide](../benchmarking.md)
