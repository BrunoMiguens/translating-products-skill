# Runtime UI review policy design

## Goal

Make runtime UI review a deterministic, configurable part of product
translation without running expensive app, simulator, browser, screenshot, or
accessibility checks for every task.

The workflow must distinguish a successful runtime review from a file-only
review. It must also record when runtime review was disabled, unnecessary, or
unavailable.

## Non-goals

- Build a universal UI automation runner.
- Discover arbitrary application states without project instructions or an
  existing test path.
- Store screenshots or other large binary evidence in review JSON.
- Expand runtime UI review beyond the screens, states, locales, and resources
  affected by the translation request.
- Treat runtime UI review as human or native-speaker review.

## Baseline failure

The mobile and platform skills currently require screenshots, interaction,
device-size checks, text scaling, accessibility, and RTL checks. The
orchestrator and canonical review artifact do not resolve or record whether
those checks ran.

As a result:

- an explicit caller request or approved project requirement implies `run`
  through prose but is not enforced by policy;
- a runnable mobile task with no explicit requirement can reasonably produce
  `run`, `skip`, or `ask`;
- `no_issue_detected` can be emitted without distinguishing a successful
  runtime review from a runtime review that never happened.

## Intent and outcome are separate

Runtime UI review has a configuration intent and an execution outcome.

### Configuration intent

`runtime_ui_review` accepts exactly:

- `required`: runtime UI review must be attempted for the applicable scope;
  unavailable required coverage remains visible and prevents a runtime-safe or
  release-ready claim.
- `auto`: the policy resolves the decision from routed skill requirements,
  applicable UI surfaces, available project execution paths, and material
  ambiguity.
- `disabled`: do not run runtime UI review; retain file, linguistic, and
  structural QA and record that runtime UI review was not run.

### Execution outcome

The review artifact records exactly one target-locale outcome:

- `completed`: the scoped runtime review ran and has evidence references;
- `not_run`: policy disabled or did not select runtime review;
- `unavailable`: policy required or selected runtime review, but the app,
  state, environment, or execution path could not be reached;
- `not_applicable`: the routed work has no user-visible runtime surface.

An execution outcome never changes the linguistic or structural findings. It
controls only claims about runtime surface coverage.

## Authority and resolution

The resolver applies this precedence:

1. explicit caller intent;
2. approved project configuration;
3. routed skill verification requirements;
4. one focused question when the remaining choice is material and unresolved.

The explicit request and approved project configuration may use `required`,
`auto`, or `disabled`. `required` and `disabled` are terminal at their authority
level. `auto` delegates to the next level: caller `auto` consults the approved
project value, and project `auto` consults the routed skills. A missing caller
or legacy project value behaves as `auto`. Routed skills declare `required`,
`recommended`, or `none`. The router aggregates the strongest selected
declaration.

Resolution rules:

| Effective input | Result |
| --- | --- |
| Highest non-`auto` caller or project value is `required` | `run` |
| Highest non-`auto` caller or project value is `disabled` | `skip` |
| Effective intent is `auto`, route says `required` | `run` |
| Effective intent is `auto`, route says `recommended`, and a bounded execution path is available | `run` |
| Effective intent is `auto`, route says `recommended`, and availability or material cost is unresolved | `ask` |
| Effective intent is `auto`, route says `none`, with no applicable UI surface | `skip` as not applicable |

`run` means attempt the smallest relevant runtime review. It is not a promise
that the environment will succeed. A failed or blocked attempt becomes
`unavailable` with a concise reason.

The resolver returns a stable decision and reason list. Agents must not replace
it with an ad hoc token-saving choice.

## Project configuration

New project briefs add this optional approved field under acceptance criteria:

```markdown
- Runtime UI review: auto
```

The parser accepts `required`, `auto`, or `disabled`. Existing approved project
briefs without the field behave as `auto`, preserving their hashes and avoiding
a forced migration. New templates include `auto` so the choice is visible at
setup.

Changing the field changes approved project-context bytes and therefore uses
the existing reapproval mechanism.

## Caller and routing inputs

The orchestrator request may include `runtime_ui_review` with the same three
intent values. The value remains an internal workflow input unless the caller
asks for it in public output.

Capability records gain a verification declaration:

```json
"runtime_ui_review": "recommended"
```

The initial declarations are:

- `recommended` for `translating-mobile`, `translating-ios`,
  `translating-android`, and `translating-flutter`;
- `none` for skills without a runtime UI contract.

The router exposes the strongest selected declaration in
`verification_requirements`. It does not copy screenshot instructions or
device matrices into the route.

## Bounded execution path

For `auto`, a bounded execution path exists when the repository supplies enough
information to run the affected state without exploratory application-wide
navigation. Examples include:

- an existing UI, snapshot, screenshot, or accessibility test;
- a documented fixture, deep link, launch argument, preview, or deterministic
  navigation path;
- explicit reproduction instructions in the request or approved project
  context.

The agent may inspect the repository to determine whether one exists. It asks
one focused question when the route recommends runtime UI review but the path or
acceptable cost remains materially ambiguous.

## Runtime scope and evidence

Runtime review is scoped to affected resources and representative states. It
does not expand to the whole application unless explicitly required.

Each completed run records compact evidence references for the applicable
locale. Evidence stays outside the JSON and may point to screenshots, test
results, logs, or existing artifact paths. The result records:

- `status`;
- `reason`;
- `evidence` as a list of nonblank path or artifact references.

`completed` requires at least one evidence reference. `unavailable` and
`not_run` require a reason and no fabricated evidence. `not_applicable` needs no
evidence.

The canonical review request records the resolved decision and reasons. The
canonical review result records the execution outcome. Both live at the target
locale level rather than being repeated for every string.

## Completion semantics

`no_issue_detected` remains a per-string translation classification. It cannot
be used as evidence that runtime UI review completed.

When runtime UI review is required:

- `completed` permits runtime-surface claims within the recorded scope;
- `unavailable` preserves completed linguistic and structural findings but
  blocks runtime-safe and release-ready claims;
- `not_run` is invalid;
- `not_applicable` is valid only when the selected route has no applicable UI
  surface.

When runtime UI review is disabled, the result must say it was not run. The
agent must not attach a generic warning to every translated unit.

## Skill behavior

The orchestrator resolves runtime review after routing and before the primary
review. It asks only when the resolver returns `ask`.

The mobile and platform skills provide the runtime checks and smallest relevant
scope. They do not independently decide whether to spend the runtime-review
budget after policy resolution.

The reviewing skill consumes the resolved decision and outcome, applies the
surface constraints to available evidence, and keeps runtime coverage separate
from linguistic and structural classification.

## Compatibility and token budget

- Existing project briefs default to `auto`.
- Existing capability records without the new declaration default to `none`.
- Runtime records are target-level and compact.
- Screenshots and logs are referenced, never embedded.
- Runtime checks cover affected screens and states rather than the whole app.
- `disabled` provides an explicit file-only path.

## Verification

Tests must cover:

1. caller intent overriding project and route inputs;
2. approved project configuration overriding route inputs;
3. invalid project and request values failing closed;
4. backward-compatible `auto` for project briefs without the field;
5. aggregation of `required`, `recommended`, and `none` route declarations;
6. resolver outputs for run, skip, and ask, including stable reasons;
7. canonical request/result validation for all four outcomes;
8. rejection of completed outcomes without evidence;
9. rejection of required review recorded as `not_run`;
10. a narrow mobile signup screen with a validation-error state as a realistic
    behavioral evaluation;
11. repository validation, generated catalog validation, and the full test
    suite.

## Review focus

The final review should check precedence, backward compatibility, false
runtime-safe claims, duplicate per-string evidence, and any path that can turn
an unavailable required review into a successful completion.
