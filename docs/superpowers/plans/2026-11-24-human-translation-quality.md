# Human translation quality and native delivery implementation plan

> **For agentic workers.** Use `superpowers:subagent-driven-development` to execute this plan task by task. Use `superpowers:writing-skills` and `superpowers:test-driven-development` for each change to an installed skill.

**Goal.** Reduce material errors that bilingual readers find in delivered product translations and improve complete delivery on the first request. Preserve good regional choices, appropriate requests for guidance, and existing caller contracts.

**Architecture.** The orchestrator owns release and routing. Each specialist owns its linguistic or platform decision. Review checks source meaning and the final artifact. The evaluator owns attribution, measurement and collector repairs. Keep source-specific examples on the evaluator side. The plan can be executed with fresh synthetic development cases from a clean checkout.

**Tech stack.** Markdown skills, Python standard library policy and review scripts, JSON fixtures, unittest and the consuming project’s existing build or lookup tools.

**Design and evidence.** Read the [sealed v0.4.0 baseline](../../benchmarks/native-quality-baseline.md), [suite architecture](../../architecture.md) and [contribution contract](../../../CONTRIBUTING.md). The earlier 100-entry pilot identified Arabic dual agreement and store-title specificity as leads. Tasks 5 and 9 retest them. The 25-project sweep is the delivery baseline.

## Global constraints

- Keep source, selected units, approved context, project revision, locale/register, model, effort, permissions and reviewer budget fixed across baseline and candidate arms. A new implementation agent or model is fine. If the *translation* model changes, rerun both arms on it. Attribute improvement only within that comparison.
- Use the ordinary project setup, routing, handoff and delivery. Preserve format, caller-visible output, offline operation, the installed-skill boundary, and review schema v2 compatibility. Each new check applies only when its inputs and surface make it meaningful.
- Keep historical targets, exact private strings, contributor identities, judge explanations and holdouts outside workers, prompts, skill knowledge and Git. New tracked examples must be independently authored synthetic examples. Report native release, quality judgments, human decisions and technical failures separately.
- For guidance changes capture the pinned baseline **without the proposed guidance**, including the agent’s actual decision and rationale. Micro-test candidate wording and a no-guidance control in fresh contexts at least five times per arm. Use a realistic combined pressure case for release decisions, then one untouched confirmation case. Read every flagged example. If the baseline already handles it, record the passing control and stop that edit.
- Make each change independently reversible. Implement the smallest owner change that turns RED to GREEN, remove duplicate or ambiguous instructions, then rerun positive and negative controls. If two instructions conflict, replace the ambiguous one at its owner and rerun the previously valid interpretation as a control. Keep each accepted task in its own commit with its test and evidence note. Park an unsuccessful experiment without holding up independent tasks.

## Review focus

| Situation | Required behavior | Owner |
|---|---|---|
| A resumed job finds a valid previous-locale export or duplicate review bundle | Authenticate the current unit, reviewed value and final resource before releasing it | 2 and 11 |
| Source-identical text is intentional | Preserve the quotation, token or code, and translate its surrounding prose | 3 and 4 |
| Plural forms compile and have the expected count, but select the wrong branch | Exercise the publisher’s selected form at distinguishing counts | 5 |
| A historical translation or judge misreads a UI state, field budget or regional term | Resolve the call site and scoped authority, then allow independently good wording | 8 and 9 |
| A rare locale lacks qualified wording while adjacent units are ready | Release independently verified, separable work only where the caller’s contract allows it | 10 and 11 |

## How to dispatch and verify the work

Start with Task 1. Run Task 2 before changing final delivery or attempting Task 11. Tasks 3–10 can be reviewed independently. Serialize edits to shared files such as `invariants.py`, `skills-manifest.json` and the orchestrator. Task 12 closes measurement. One implementer owns one failing mechanism and one fresh reviewer checks its diff, RED evidence and passing controls. Use an economical model for fixture work, deterministic replay and routine review. Reserve higher effort for a demonstrated ambiguity. Only a qualified human supplies a human label.

Paths below are repository-relative. For fixtures, **valid JSON is only the first check**. A scenario passes when its specified worker behavior, bilingual judgment or native consumer assertion passes. From the repository root, run a named test file with `python3 -m unittest discover -s tests -p 'test_routing.py' -v`, substituting the file named in the task. The full acceptance commands are at the end. Capture commands, versions, exit status and relevant output beside each private run.

### Task 1. Freeze comparable samples and classify the evidence

**Files.** Use `docs/benchmarking.md`, `docs/benchmarks/product-review.md` and `docs/benchmarks/pt-pt.md` for measurement conventions. Source-only native runs follow `docs/benchmarks/open-source-native.md` in the benchmark workspace. Save new packets and receipts under ignored `benchmark-private/`. The flat-JSON private product-review runner evaluates *review* behavior. Use native production entry points for generation and native-format trials.

- [ ] Record baseline Git object `b1810d5d5b5626673b0fe0d7cb39a65911adeae9`, candidate object when available, host/model/effort, input hashes, project, locale/register, surface and unit IDs. Split **known diagnostic**, **fresh development** and **unseen confirmation** by project or source family before dispatch. Keep sibling translations of a source family together. Predeclare a normal-prompt arm only for a claim about the skill’s added value.
- [ ] Start with the 3,682 material-review leads for diagnosis. Include cases favoring the skill, both acceptable, style-only, uncertain, and holds. Stratify by project, locale, surface and evidence density. Where private data are available, find exact rows in `final-analysis/quality/material-review-list.jsonl.gz` and all outcomes in `dataset/evaluated-results.zip` beneath the private audit. The sealed digest is in the baseline. A fresh checkout can reproduce the mechanisms below without those archives.
- [ ] Verify isolation with a synthetic target-only canary absent from the worker mount, prompt, history and logs. Give evaluators the target only after the candidate, native files and review receipts are frozen. Save one dry-run manifest with an attributable case, a case with no attributable reference and a held unit. Expect one eligible pair and three explicit dispositions.
- [ ] Give every lead one disposition in the private ledger: confirmed skill defect, acceptable variant, source/publisher defect, evaluator defect, technical interruption, missing authority or insufficient evidence. An automated quotation supports inspection. Historical preference alone is insufficient grounds for editing a skill.

### Task 2. Verify that the reviewed wording is the wording delivered

**Issue and impact.** Two French UI values changed between review and their final files. Earlier exports and duplicate bundles also produced misleading evidence. A good review can describe the wrong artifact.

**Owner and interface.** `skills/translating-products/SKILL.md` (Final delivery) and `skills/reviewing-translations/SKILL.md` (review handoff). Reuse `skills/reviewing-translations/scripts/validate_review_artifact.py` and its existing request/result schema. A successful v2 validation proves internal consistency. The final step must also reopen the requested resource. Use the consuming format’s parser to pair the reviewed unit with its resource key, locale/register, context or plural arm and decoded value.

- [ ] **RED.** Resume a synthetic two-locale job. Change one field after approval, reserialize an equal field, leave a previous locale’s export and an older review for the same key. Record which fields the original entry point calls ready. Include a corrected recommendation whose final re-review passed.
- [ ] **GREEN.** Select the current review by its request/result identity and actual release value. Accept the approved correction with its re-review, and equivalent escaped serialization. Reopen a changed unit for review. If two bundles cannot be disambiguated, withhold that unit. Preserve caller-requested return-only output and native-only contracts. For an existing v2 caller the check belongs in final handoff, outside its unchanged JSON payload.
- [ ] **Verify.** In `evals/orchestration-cases.json` add a synthetic handoff case, then exercise the normal saved-file and return-only paths. Assert exactly one changed unit returns for review, zero stale values are released, and unchanged nested/plural values still pass. Run `test_review_artifact.py`, `test_review_invariants.py` and `test_bootstrap_policy.py`. If a new validator is needed, test its actual CLI invocation from the orchestrator before accepting it.

### Task 3. Verify every reader-facing part of long documents

**Issue and impact.** Reviewed chapters retained English paragraphs, and two sections translated only their headings. Other chapters legitimately condensed prose or kept code. A source-overlap threshold alone would reject working translations.

**Owner.** `skills/translating-documentation/SKILL.md`, with `evals/translation-quality-cases.json` for portable examples. Use the existing structural review in `skills/reviewing-translations/scripts/invariants.py` for the formats it supports.

- [ ] **RED.** Use a fresh Markdown chapter and an AsciiDoc confirmation section with ordinary paragraphs, an exception in a table, a heading, wrapped links, code and a quotation. Under a resumed large-batch handoff, record a copied paragraph or omitted condition that passed review.
- [ ] **GREEN.** Have the document reviewer check reader-facing source sections against the final selection, including notes and conditions. Use unchanged prose to locate suspicious passages. Permit faithful compression, changed line wrapping, and verbatim code or approved labels.
- [ ] **Verify.** A reviewer must locate and correct or explicitly hold each seeded omission. A faithful shorter rendering and code survive. Test both an oversized unit reviewed in chunks and the reassembled final section. Run `test_eval_fixtures.py` and the real blind document trial. Record the omitted fact and its final location for the evaluator.

### Task 4. Preserve operational literals, link scope and credit identity

**Issue and impact.** Inline refs, URLs, a composed issue-tracker keyword and visible contact text changed in translated prose. One consent link included words outside its label. A compiled help catalog stored translator credits in the wrong context. Some drafts supplied personal details absent from the approved inputs.

**Owners.** For literal/markup review use `skills/reviewing-translations/scripts/invariants.py`, `evals/structural-fidelity-cases.json` and `tests/test_review_invariants.py`. For contextual resource identity and credit provenance use `skills/localizing-software/SKILL.md`. Split literal and credit changes into separate RED/GREEN commits.

- [ ] **RED, literals.** Build a synthetic composed keyword, inline ref, visible contact label, fixed destination and localizable caption. Preserve placeholder bytes while changing the assembled keyword or swallowing consent wording inside the link. The diagnosis must identify the rendered token or linked span. Include a project-approved localized URL.
- [ ] **GREEN and verify.** Compare protected operational text and link targets after assembly. Check the meaning of link text at its call site. Permit approved destination substitutions, reordering and benign emphasis changes. Run `test_review_invariants.py` and render the fresh fixture. Correct every seeded break and pass every positive control.
- [ ] **RED, credits.** Give a help entry and an About entry the same source key under different gettext contexts. Supply approved fictional attribution for only one. Compile the catalog and look up both contexts. Include missing, fuzzy and wrong-context entries.
- [ ] **GREEN and verify.** Resolve credit identity using the actual consumer context. Insert only approved metadata. Preserve the project’s established handling for an uncredited field and identify unresolved provenance when required. Verify with GNU `msgfmt` and contextual lookup, and check that no unsupplied identity is emitted. A publicly discoverable historical identity provides no authority to credit a new file.

### Task 5. Check plurals against the publisher and the rendered language

**Issue and impact.** Ten checked scopes used the wrong plural arity. Thirteen selected the wrong form at a witnessed count despite having the expected arity. The Arabic pilot also raised a separate linguistic question about numeral and noun agreement.

**Owners.** `skills/localizing-software/SKILL.md` for publisher contracts, `skills/translating-arabic/SKILL.md` only for a reproduced linguistic issue. Use `evals/structural-fidelity-cases.json` and `evals/translation-quality-cases.json` for separate fixtures.

- [ ] **RED.** Build fresh cases with a missing plural form, equal arity but different selection at zero, a periodic boundary, an equivalent selector expression and a file without selected plurals. Identify the approved rule for this project version, locale, register and format.
- [ ] **GREEN and verify.** Compile each fixture with the native tool and query distinct rendered form markers at zero, one and the counts separating the expressions. Fail incorrect selection and missing required forms. Label sampled agreement as sampled. Where project authority is unavailable, record that dependency for the affected resource. Preserve valid gettext, ICU and Flutter contracts independently.
- [ ] For Arabic, render the same new noun at 0, 1, 2, 3, 11 and 100, plus another noun with a mandatory numeric argument. Have an Arabic reviewer evaluate grammar and argument retention before editing specialist guidance. A passing baseline closes this pilot lead. A failing baseline gets its own blind confirmation and commit.

### Task 6. Validate the files through the real consumer

**Issue and impact.** Android Hebrew lookups fell back to English. Formatter values passed in trusted wrappers while all 85 native locale deliveries failed a consumer typecheck. A Flutter discrepancy also occurred in the publisher control.

**Owners.** `skills/translating-android/SKILL.md` for resource qualifiers, `skills/localizing-software/SKILL.md` for executable resources. Consult `skills/translating-flutter/SKILL.md` and `skills/translating-ios/SKILL.md` for controls. Keep the two positive fixes independently reviewable.

- [ ] **Android RED/GREEN.** In a disposable Android fixture, compile a default and target string and quantity, including a Hebrew qualifier and a known-good locale. Use distinct markers and change the device locale. Establish unexpected fallback, follow the publisher’s supported resource naming, then repeat the *actual lookup*. Verify intended fallback for a genuinely missing key. Avoid renaming unrelated projects’ resource trees.
- [ ] **Formatter RED/GREEN.** Put correct translated values behind a broken callable export. Import the actual delivered module using the pinned consumer and run its typecheck, then invoke dates and numeric timestamps. Preserve the publisher’s export shape, argument types and formatting tokens. Both the unchanged working locale and the corrected candidate must pass the same consumer.
- [ ] **Verify boundaries.** Compare fresh Flutter getters with a base-plus-region pair and publisher data, and an Apple table with a missing-key control. Label actions as compiled, looked up, rendered or unavailable. Follow the existing runtime UI policy. Do not turn host tool absence into a translation error or a mandatory SDK install. Run `test_skill_contracts.py` and the applicable native fixtures.

### Task 7. Route right-to-left work from its explicit script

**Issue and impact.** Valid right-to-left scripts missed the direction helper unless a separate capability token was supplied. Selecting a helper also cannot establish correct rendering or qualified wording.

**Owner.** `skills-manifest.json` owns declared selectors. `skills/translating-products/scripts/route_capabilities.py` consumes them. Keep selection declarative. Update generated catalogs with `scripts/render_catalog.py`. Test in `evals/routing-cases.json` and `tests/test_routing.py`.

- [ ] **RED.** Pass a target with an explicit ISO script through the shipped router. Cover Arabic-script Urdu alongside N’Ko, Syriac and Thaana. Include Punjabi Gurmukhi, a Latin-script sibling and the existing `rtl` and `bidi` routes. Assert the exact presence or absence of `translating-rtl` per target.
- [ ] **GREEN.** Add the evidenced script selectors/aliases at the existing selection boundary. Keep locale-specific specialists and per-target branch ownership intact. Avoid language-only guesses where script matters.
- [ ] **Verify.** Run `test_routing.py`, `test_catalog.py` and the catalog drift check. Render a separate mixed-direction number, placeholder and punctuation example where a renderer is available. Record routing, visual behavior and unresolved language expertise separately.

The first router RED can run directly from a clean checkout. This new request keeps the script explicit and the capability list empty. Inspect the returned branch’s selected skill names, then repeat with the same request using the existing RTL marker as the positive control.

```sh
packet=$(mktemp)
cat > "$packet" <<'JSON'
{"schema_version":2,"source_locale":"en","targets":[{"locale":"nqo","scripts":["Nkoo"]}],"surfaces":["software"],"platforms":[],"formats":["gettext"],"domains":[],"capabilities":[],"audience":"Application users","purpose":"Translate account settings","register":"neutral"}
JSON
python3 skills/translating-products/scripts/route_capabilities.py "$packet"
```

### Task 8. Preserve what the user is being told

**Issue and impact.** Fluent wording changed an empty state into an instruction, a time into a destination, and an explicitly disabled setting into an unavailable feature. Other cases lost device-only scope, moved a qualification onto the wrong object, or blurred an enablement condition. These errors survive placeholder and grammar checks.

**Owners.** `skills/translating-core/SKILL.md` (Context Analysis and Fidelity Invariants) and `skills/reviewing-translations/SKILL.md`. Use `evals/translation-quality-cases.json` and `tests/test_eval_fixtures.py`. Make a locale specialist edit only if the shared repair passes and a distinct language-specific defect still reproduces.

- [ ] **RED.** Pair a state with an action using the same noun, time with place using the same placeholder, and a privacy claim with an exclusive qualifier. Include an ambiguous source without its call site. Record the data available to the worker at decision time.
- [ ] **GREEN.** Use the approved call site, resource comment or source context to establish actor, object, condition, UI role, substitution type and scope where consequential. Render a representative argument. Review the *meaning of the complete sentence*. Keep the existing request/result schema valid. Ask a focused question if evidence cannot resolve a consequential difference.
- [ ] **Verify.** Bilingual reviewers must recover the intended role, argument, object and restriction from the delivered wording. Include cases in which a compact label or a publisher’s narrower reference is legitimately different. Use the private ten-specialist casebook to choose confirmation strata. Include Chinese regional terminology, French object identity, German link scope, Hebrew state, Japanese exclusivity, Korean action, Polish modifier attachment, Portuguese terminology, Spanish condition and Arabic placeholder role. The QR-specific Chinese, Canadian French title and Chilean timing objections need call-site verification before any rule is proposed. Pass the schema check and the blind linguistic trial.

### Task 9. Apply regional decisions, punctuation and field budgets at the right scope

**Issue and impact.** The skill could reproduce a spelling distinction yet reuse formal address in an informal context. A strict punctuation policy conflicted with natural prose. A store title could win a wording judgment and still exceed its real field limit.

**Owners.** `skills/translating-products/SKILL.md` and its existing approved project context own the scope of decisions. Apply a reproduced punctuation issue in `skills/localizing-software/SKILL.md`; a store issue in `skills/translating-app-stores/SKILL.md`. Retest linguistic cases in the relevant regional specialist. Take each of the following through a separate RED/GREEN cycle.

- [ ] **Regional terminology and address.** Give two sibling locales/registers different approved address or spelling rules, one shared neutral sentence and one ambiguous term. Preserve both differences and valid shared wording. Use exact publisher locale, script and variant identity for routing. Resolve slug collisions against approved source metadata. A generic language and an audience’s register are separate facts. Test the same-language generic case both with a publisher-default decision and without that authority.
- [ ] **Prose punctuation.** Seed a prose apostrophe entity beside markup, a functional wrapping or dimension entity, a placeholder and a URL. Reproduce the conflict under the original brief, then apply a decision restricted to one locale/surface. Diff the entire delivered file. Expect natural punctuation in the approved prose and byte/semantic preservation wherever the contract requires it. Reuse of that exception by a sibling fails.
- [ ] **Store and marketing.** Use a fresh title with a product category, claim and versioned field budget alongside a roomy field. Count the *assembled upload field* using the platform’s counting rule. If source and reference are already over budget, identify the precise owner choice. Preserve category meaning under compression. As confirmations, sample regionally distinct onboarding, marketing and store cases corresponding to Saudi Arabic, Egyptian Arabic, Austrian German and Latin American Spanish. A passing regional example remains a control.
- [ ] **Verify.** Record each approved decision’s scope and rendered field privately. Run `test_skill_contracts.py`, targeted linguistic fixtures and a fresh native export. Require bilingual acceptance for the register/term claim and a deterministic pass for each measurable limit. Avoid hardcoding publisher terms or historical store policy in the skill.

### Task 10. Deliver verified subsets and handle dependencies honestly

**Issue and impact.** Some runs withheld usable work after a small set of unresolved questions. Others produced English scaffolding or the wrong locale. A worker can also correctly decline a locale whose required expertise is unavailable. All three behaviors affect first-attempt delivery differently.

**Owners.** `skills/translating-products/SKILL.md` (Final delivery, Runtime failures and Specialist routing), `skills/reviewing-translations/SKILL.md` and `evals/orchestration-cases.json`. Reuse existing `blocked_by_source`, `unresolved` and recommendation QA dispositions.

- [ ] **RED.** Give a core-capable locale without a bundled specialist a separable batch with two blocked keys. Repeat with a genuine speaker/terminology dependency and with an explicitly atomic output contract. Include a wrong-locale draft and an interruption after validation but before release.
- [ ] **GREEN.** Release verified, independently usable units when the output contract permits it. Record the exact unit, reason and requested input for each hold. For an atomic contract, retain staged work until the release condition is met. Record the original turn and any assisted recovery separately. Respect the actual reviewer policy, including a required independent challenge.
- [ ] **Verify.** Published plus held keys reconcile to the selected input with no duplicates. Read released values back at their keys. Reject source scaffolds and stale artifacts. An unavailable speaker remains an explicit dependency. A service refusal remains a technical disposition. Rerun after a scoped owner reply and verify that unrelated accepted entries remain valid. Run `test_bootstrap_policy.py` and the normal entry point.

### Task 11. Test order effects and keep collector defects out of the skill

**Issue and impact.** Resumed sessions reused previous files. One collector missed a publisher’s native script alias. Others failed to recognize valid reviews among duplicate bundles or inside compound commands. Incorrect denominators can make a correct translation look like a regression.

**Owners.** Test project order through the orchestrator from Task 2. Collector changes belong in the benchmarking workspace under `scripts/benchmark/` and `tests/test_native_evaluation_contract.py`. Treat its uncommitted harness files as an existing dependency to verify when moving worktrees. Use the installed production skill independently of those collectors.

- [ ] Counterbalance project A then B and B then A in isolated project sessions. Reverse the order of two locale/register siblings within each. Repeat one path after a clean restart. Include a permitted shared physical slot, a protected slot and a stale export. Compare approval identity, final native values, reviewed keys and release status. A confirmed order-dependent defect gets a fresh RED case and an isolated fix at its owner.
- [ ] Replay an authentic locale alias, equal plural serialization, duplicate review candidates and previous-locale files. Join by receipt, source identity, locale and final typed value. Preserve contextual key identity and positive proof. The diagnostic Serbian Latin replay added 199 native bindings and zero pairs. Use a fresh alias fixture with a missing contextual credit to test both outcomes.
- [ ] Retain the sealed baseline. Version corrected evidence with a digest and an explanation of which counts change. Keep source-language contamination, disputed attribution, publisher defects, service interruptions and assistance separate. A changed collector must reconcile old and new totals before it is used to judge a production change.

### Task 12. Run blind human acceptance and decide what ships

**Owners.** The evaluator prepares masked packets and keeps the answer key. Two independently qualified reviewers for the target locale make linguistic judgments. The release owner adjudicates disagreements with reasons and applies the agreed thresholds. A translating or coding agent cannot sign as a human reviewer.

- [ ] Before candidate generation, freeze the two sampled populations: a diagnostic sample enriched for known failures, and a fresh project/locale-stratified confirmation sample including passing controls, sparse locales and appropriate holds. Record planned sample counts, strata, severity rubric, minimal worthwhile improvement and maximum tolerable regression with the release owner. Use the same held-out sources for both arms. Keep known translations and expected answers outside both worker environments.
- [ ] Show English, the approved source-side context and randomly ordered, anonymous outputs. First judge each output’s fidelity, omissions, role/argument, locale/register, naturalness and publishability. Then record pair preference, severity, rationale, permissible alternatives and abstentions. Include an anonymously labeled published human version where credit and context allow it. Counterbalance order and conceal previous model judgments. Score before revealing authorship.
- [ ] Report adjudicated material errors per rated delivered unit in each arm, style-only differences separately, and acceptability compared with the human version. Report paired wins, losses and ties, reviewer agreement before adjudication, abstentions and denominators. Break down by locale, surface and project. Report uncertainty on paired differences with project/source-family clustering where support permits. Sparse strata remain descriptive. Keep comparison eligibility, complete first attempts, held units and technical failures alongside linguistic rates so withholding difficult units cannot inflate quality.
- [ ] Accept a mechanism only when the reproduced case is fixed, its positive controls pass, and the untouched confirmation supports the predeclared criterion. Revert a change that introduces a confirmed material error in a passing control, breaks contracts, or exceeds the agreed delivery regression margin. If qualified reviewers or sample precision are insufficient, record the linguistic conclusion as open. Mechanical fixes can still have their own acceptance decisions.
- [ ] Run the following integration checks from the repository root, record environment and skips, and inspect the final changed files. Render generated files only when their source changed. Test ordinary installed discovery and entry points in disposable environments as described in the [release checklist](../../release-checklist.md).

```sh
python3 scripts/render_catalog.py --check
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
git diff --check
```

- [ ] Deliver an acceptance table with one row per task: baseline failure, candidate result, passing control, unseen confirmation, evidence location, owner and ship/revert/defer decision. Link the new revision to its own evidence overview. The benchmarked v0.4.0 record keeps its original identity.

## Evaluator evidence map

These are private paths relative to `benchmark-private/open-source-project-audit/native-evaluations/`. The executor can work from the synthetic probes above. The evaluator uses the retained evidence to verify scope or adjudicate a disputed diagnosis.

| Tasks | Evidence to inspect |
|---|---|
| 1, 11, 12 | `original-project-cohorts/`, `judgment-position/`, `gbrainy-serbian-latin/result.json`; the final analysis and review list named in Task 1 |
| 2, 3 | `specialist-language-casebook/`, `long-document-completeness-audit/`, `released-long-documents-copied-english/` |
| 4 | `doc-literal-audit-e0f92b4f5f115482/`, `frozen-quality-analysis/firefox-nightly-keyword/`, `gbrainy-translator-credits-audit/` |
| 5, 6 | `wordpress-plural-authority/4a6b3bba836fce9f/`, `android-device-resource-runtime/`, `date-fns-pinned-import-typecheck.json`, `ubuntu-flutter-runtime-4ca0d375d39dcaa6/` |
| 7, 8 | `rtl-router-replay/`, `linguistic-dependency-verification/`, `specialist-language-casebook/casebook-3d844b1d8023c39b/` |
| 9, 10 | `wordpress-final-variants-audit/`, `wordpress-prose-punctuation-cohorts/`, `store-field-length-audit.json`, `firefox-site-fallback-cluster/`, `release-integrity-verification/` |

## Coverage work that still needs inputs

The audit catalogued 42 behavior groups. Some had only routing or partial, isolated runtime evidence. Commission these tests when the named input is available. A new production rule needs its own reproduced defect.

| Question | Input needed | Verify through the normal workflow |
|---|---|---|
| Can the agent resolve terminology, a current rule or conflicting authorities? | Approved source hierarchy, one genuinely unresolved fact, an available research source, a stale/conflicting source and a resolved control | Check the precise question, source revision, scoped decision and absence of gratuitous research on the resolved control. Exercise admission of an approved external specialist and rejection of an unapproved one. |
| Is the shipped interface readable and accessible? | Complete disposable app target, real layout, assistive labels, mixed-script values, pseudo-locale and intentional fallback | Render constrained and expanded layouts, verify accessibility labels, bidi order and correct fallback. Check extraction/generation in the actual build. Record device and OS. |
| Will web and store assets publish correctly? | Responsive site with reciprocal locale links and navigation, editable screenshot artwork, an actual exporter and versioned store metadata | Exercise rendered navigation, canonical/hreflang relationships, screenshot line breaks and assembled field limits. Separate wording preference, visual fit and policy rejection. |
| Does the public response satisfy the request and preserve claim authority? | Return-only requests in multiple formats, a claim requiring an owner, and a recorded approval or absence of it | Compare exact caller output including escaping. Verify that a claim awaiting formal approval remains held. |
| Does the skill improve thinly evidenced languages and markets? | Fresh attributable material or qualified bilingual reviewers in the uncovered strata | Record locale, script, register and reviewer qualifications. Evaluate generic or regional choices in their actual context and report their own denominators. |

The private audit directory is `benchmark-private/open-source-project-audit/`. Within it, `RESULTS.md`, `FEATURE-BEHAVIOR-COVERAGE.md`, `REGIONAL-RESULTS.md` and `IMPROVEMENT-PLAN.md` index the mechanisms and their retained proofs. `final-analysis/all-project-locale-runs.csv` distinguishes original outcomes from assisted runs. Use those artifacts for evaluator diagnosis; build fresh development and acceptance cases outside the historical answer set.
