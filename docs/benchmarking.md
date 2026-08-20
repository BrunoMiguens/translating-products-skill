# Benchmarking

Use benchmarks to compare the translation suite against the same agent and
model working from a normal translation prompt. Choose the workflow that
matches the evidence you need.

## Choose a workflow

| Goal | Workflow |
| --- | --- |
| Make a controlled, public quality claim on the frozen PT-PT dataset | [Controlled PT-PT benchmark](benchmarks/pt-pt.md) |
| Test the suite against private strings from a real product | [Private product review](benchmarks/product-review.md) |
| Check that routing, schemas, structure, and commands still work | Repository tests in [Contributing](../CONTRIBUTING.md#run-the-checks) |

The controlled benchmark is deliberately expensive: it requires a frozen
schedule, complete evidence, blind human review, and predeclared gates. The
private product workflow is the practical regression loop for ongoing skill
development.

## What to compare

Keep the host, model, settings, source material, and output contract fixed.
Change only the translation guidance:

1. `normal` — the agent receives an ordinary product translation or review
   request with no translation skills;
2. `current_suite` — the same request uses a pinned baseline suite Git object;
3. `improved` — the same request uses a pinned candidate suite Git object.

Comparisons are within one host. Do not combine Claude and Codex scores as if
they were one model.

## Measures

Deterministic validation checks observable integrity such as placeholders,
resource keys, markup, required fields, and exact input identity. Human review
measures linguistic quality that structural checks cannot determine.

The private product scorer reports:

- required-error recall;
- reported-error precision;
- false-positive correction rate; and
- exact correction success rate.

An automated candidate never creates its own human labels. A reviewer completes
the blinded review file separately, and the tools refuse incomplete, drifted,
aliased, or mismatched inputs.

## Privacy and relearning boundary

Real product resources, model outputs, human decisions, and score packets stay
under the ignored `benchmark-private/` directory or another private path. The
tracked repository contains only synthetic fixtures and generic commands.

Production skills never read private packets. Human-reviewed cases may become a
private regression set, but their strings and answers must not be copied into
skill instructions, tracked fixtures, documentation, or public benchmark data.
Improvements should encode transferable translation mechanisms rather than a
reviewer's specific correction.

## Claims

A passing private regression run is useful engineering evidence, not a public
superiority claim. Once reviewed cases guide an improvement, they are no longer
a fresh holdout. Public comparative claims require a new, signed holdout and
the complete controlled workflow.
