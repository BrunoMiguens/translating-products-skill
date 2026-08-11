# Blind PT-PT benchmark review rubric

## Review boundary

Judge only the anonymous outputs shown with the source, approved context, and case constraints. Do not infer a condition from style, fluency, formatting, or apparent tool use. Do not consult condition keys, model logs, automated findings, learned metrics, references, seeded errors, or file paths before annotations are locked.

## Pairwise outcome

Choose exactly one outcome for the primary anonymous pair:

1. `left_clearly_better` — the left output needs materially less correction and the difference is decisive.
2. `left_slightly_better` — both may be usable, but the left output has a modest quality advantage.
3. `tie` — differences are preferences or the outputs require comparable correction.
4. `right_slightly_better` — both may be usable, but the right output has a modest quality advantage.
5. `right_clearly_better` — the right output needs materially less correction and the difference is decisive.

Judge anonymous outputs only. Do not map left/right or A/B/C to a benchmark condition.

## Confidence

- `high`: the decision follows from a clear meaning, locale, terminology, or product-integrity difference.
- `medium`: the preference is well supported but admits a plausible alternative reading.
- `low`: limited context or two defensible variants make the distinction uncertain.

Confidence records certainty and never changes the primary win calculation.

## MQM-lite dimensions

- `accuracy`: mistranslation, omission, addition, reversed state, altered claim, or changed modal meaning.
- `terminology`: wrong or inconsistent approved product/domain term or protected name.
- `linguistic-quality`: grammar, spelling, fluency, idiom, cohesion, or unnatural calque.
- `style-register`: voice, tone, formality, person, address, or cross-string inconsistency.
- `locale-audience`: PT-BR leakage, Portuguese convention, cultural mismatch, or audience unsuitability.
- `product-integrity`: placeholder, format specifier, ICU, markup, URL, code, identifier, number, date, price, line shape, or length damage.

## Severities and weights

- `critical` — weight **25**: unusable output, severe meaning failure, wrong language/locale, material claim corruption, reversed legal or safety meaning, or structural damage that can break the product. Examples: removing `not`, changing a recurring price, corrupting `%2$d`, changing a URL target, or breaking ICU topology.
- `major` — weight **5**: substantive correction required before publication. Examples: PT-BR wording in required PT-PT copy, inconsistent address, wrong approved term, exceeded store limit, or culturally mistimed campaign.
- `minor` — weight **1**: localised issue that does not change the core meaning or make the output unusable. Examples: a small punctuation, collocation, or rhythm defect that still requires editing.
- `neutral` — weight **0**: acceptable variant, preference, explanatory note, or deliberate false-positive bait that needs no correction.

Flag an output as `major_or_worse` when it contains at least one major or critical annotation. Neutral and minor annotations do not set that flag.

## Span selection

Select the smallest exact contiguous output span that proves the issue. Include a missing element by selecting the nearest affected phrase and explaining the omission. Use separate annotations for independent errors; use one span when a single edit necessarily corrects the whole issue. Never select unchanged surrounding prose merely to make a span easier to find.

## Acceptable variants and overcorrection

The reference is diagnostic, not the only valid wording. Accept any PT-PT rendering that preserves meaning, approved terminology, register, claim scope, locale conventions, and structural constraints. Do not penalise a defensible synonym, word order, contraction, punctuation choice, or established technical loanword solely because it differs from a reference. Mark a preference as neutral when no correction is required. Penalise unnecessary rewriting only when it introduces an error, violates a constraint, or damages otherwise acceptable copy.

Complete pairwise preference, confidence, MQM spans, severities, and the major-or-worse flag from the anonymous text. Automated findings remain hidden until the review is locked.
