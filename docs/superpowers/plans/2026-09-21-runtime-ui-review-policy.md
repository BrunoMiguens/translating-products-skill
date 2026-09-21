# Runtime UI Review Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cross-platform, configurable runtime UI review policy that resolves explicit, approved-project, and routed-skill requirements without confusing file review with runtime coverage.

**Architecture:** Add a deterministic resolver to the existing translation policy module, extend capability verification metadata with a platform-neutral runtime declaration, and carry one compact runtime record per target locale through canonical review artifacts. Platform skills supply checks and evidence scope; the resolver owns run/skip/ask decisions.

**Tech Stack:** Python 3 standard library, JSON Schema vocabulary, Markdown Agent Skills, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-21-runtime-ui-review-policy-design.md`

## Global Constraints

- Caller intent outranks approved project configuration, which outranks routed skill requirements.
- Intent values are exactly `required`, `auto`, and `disabled`.
- Routed requirement values are exactly `required`, `recommended`, and `none`.
- Runtime outcomes are exactly `completed`, `not_run`, `unavailable`, and `not_applicable`.
- Existing approved project briefs without the new field behave as `auto`.
- Evidence is recorded once per target locale as references; never embed screenshots or repeat evidence per string.
- Runtime UI review remains platform-neutral; the initial automatic recommendations are mobile, iOS, Android, and Flutter.
- An unavailable required review must never become a runtime-safe or release-ready claim.

## Review Focus

- Caller `auto` must delegate to project configuration rather than accidentally override it.
- A legacy project brief without the field must resolve to `auto` without invalidating its existing approval.
- External or older capability records without runtime metadata must safely default to `none`.
- A `completed` result without evidence must fail validation.
- Required runtime review recorded as `not_run` must fail validation while preserving linguistic results for `unavailable`.

---

### Task 1: Project configuration and deterministic resolver

**Files:**
- Modify: `tests/test_bootstrap_policy.py`
- Modify: `skills/translating-products/scripts/policy.py`
- Modify: `skills/translating-products/assets/translation-project/project-brief.md`

**Interfaces:**
- Consumes: caller intent, approved project brief, routed runtime requirement, applicable-surface flag, and tri-state bounded-path availability.
- Produces: `RuntimeUiReviewDecision(decision: str, required: bool, reasons: tuple[str, ...])`, `project_runtime_ui_review(project_root) -> str`, and the `runtime-ui-review` CLI command.

- [ ] **Step 1: Add characterization tests for legacy project briefs**

```python
def test_runtime_ui_review_declaration_is_optional_and_strict(self):
    cases = {
        "absent": ("", "auto", None),
        "required": ("\n- Runtime UI review: required\n", "required", None),
        "auto": ("\n- Runtime UI review: auto\n", "auto", None),
        "disabled": ("\n- Runtime UI review: disabled\n", "disabled", None),
        "invalid": ("\n- Runtime UI review: sometimes\n", None, "malformed:project-brief.md"),
    }
```

The absent case is protected behavior: it must pass before and after implementation.

- [ ] **Step 2: Add resolver tests before implementation**

```python
def test_runtime_ui_review_resolution_uses_authority_and_auto_delegation(self):
    cases = [
        ({"caller_intent": "disabled", "project_intent": "required",
          "route_requirement": "required", "applicable_surface": True,
          "bounded_execution_path": True}, "skip", "caller-disabled"),
        ({"caller_intent": "auto", "project_intent": "required",
          "route_requirement": "none", "applicable_surface": True,
          "bounded_execution_path": None}, "run", "project-required"),
        ({"caller_intent": None, "project_intent": "auto",
          "route_requirement": "recommended", "applicable_surface": True,
          "bounded_execution_path": True}, "run", "route-recommended-path-available"),
        ({"caller_intent": None, "project_intent": "auto",
          "route_requirement": "recommended", "applicable_surface": True,
          "bounded_execution_path": None}, "ask", "runtime-path-unresolved"),
        ({"caller_intent": None, "project_intent": "auto",
          "route_requirement": "none", "applicable_surface": False,
          "bounded_execution_path": None}, "skip", "no-applicable-runtime-surface"),
    ]
```

- [ ] **Step 3: Run the new policy tests and verify RED**

Run:

```sh
python3 -m unittest tests.test_bootstrap_policy.BootstrapPolicyTests.test_runtime_ui_review_declaration_is_optional_and_strict -v
python3 -m unittest tests.test_bootstrap_policy.BootstrapPolicyTests.test_runtime_ui_review_resolution_uses_authority_and_auto_delegation -v
```

Expected: the legacy absence assertion passes only after the test uses the existing parser seam; declaration/resolver assertions fail because the parser, accessor, and resolver do not exist.

- [ ] **Step 4: Implement the optional project field and resolver**

Add strict parsing parallel to `_parse_independent_review_requirement`, defaulting absence to `auto`. Add:

```python
@dataclass(frozen=True)
class RuntimeUiReviewDecision:
    decision: str
    required: bool
    reasons: tuple[str, ...]

def select_runtime_ui_review(
    *,
    caller_intent: str | None,
    project_intent: str,
    route_requirement: str,
    applicable_surface: bool,
    bounded_execution_path: bool | None,
) -> RuntimeUiReviewDecision:
    intents = {"required", "auto", "disabled"}
    requirements = {"required", "recommended", "none"}
    if caller_intent is not None and caller_intent not in intents:
        raise ValueError("caller_intent is invalid")
    if project_intent not in intents:
        raise ValueError("project_intent is invalid")
    if route_requirement not in requirements:
        raise ValueError("route_requirement is invalid")
    if type(applicable_surface) is not bool:
        raise ValueError("applicable_surface must be boolean")
    if bounded_execution_path is not None and type(bounded_execution_path) is not bool:
        raise ValueError("bounded_execution_path must be boolean or null")

    configured = caller_intent if caller_intent not in {None, "auto"} else project_intent
    required = configured == "required" or (
        configured == "auto" and route_requirement == "required"
    )
    if not applicable_surface:
        return RuntimeUiReviewDecision(
            "skip", required, ("no-applicable-runtime-surface",)
        )
    if configured == "disabled":
        source = "caller" if caller_intent == "disabled" else "project"
        return RuntimeUiReviewDecision("skip", False, (f"{source}-disabled",))
    if required:
        source = "caller" if caller_intent == "required" else (
            "project" if project_intent == "required" else "route"
        )
        return RuntimeUiReviewDecision("run", True, (f"{source}-required",))
    if route_requirement == "recommended" and bounded_execution_path is True:
        return RuntimeUiReviewDecision(
            "run", False, ("route-recommended-path-available",)
        )
    return RuntimeUiReviewDecision(
        "ask", False, ("runtime-path-unresolved",)
    )
```

Validate exact enums and real booleans. `required` and `disabled` terminate at their authority level; `auto` delegates. Add the CLI request parser with only these five fields and emit `decision` plus ordered `reasons`.

- [ ] **Step 5: Add the visible default to new project templates**

Under `## Outputs`, add:

```markdown
- Runtime UI review: auto
```

Do not make the field mandatory for existing briefs.

- [ ] **Step 6: Run Task 1 tests and verify GREEN**

Run:

```sh
python3 -m unittest tests.test_bootstrap_policy -v
```

Expected: PASS with no warnings.

- [ ] **Step 7: Commit Task 1**

```sh
git add tests/test_bootstrap_policy.py skills/translating-products/scripts/policy.py skills/translating-products/assets/translation-project/project-brief.md
git commit -m "feat: resolve runtime UI review policy"
```

### Task 2: Capability metadata and route aggregation

**Files:**
- Modify: `tests/test_catalog.py`
- Modify: `tests/test_manifest.py`
- Modify: `tests/test_routing.py`
- Modify: `skills/translating-products/scripts/metadata_contract.py`
- Modify: `scripts/repo_model.py`
- Modify: `skills/translating-products/scripts/route_capabilities.py`
- Modify: `scripts/render_catalog.py`
- Modify: `skills-manifest.json`
- Regenerate: `skills/translating-products/references/capability-catalog.json`
- Regenerate: `skills/translating-products/references/capability-catalog.md`

**Interfaces:**
- Consumes: each selected skill's `verification.runtime_ui_review` declaration.
- Produces: `verification_requirements.runtime_ui_review` and `runtime_ui_review_declared_by` in selected load order.

- [ ] **Step 1: Add manifest contract tests before metadata changes**

```python
def test_skill_verification_defaults_runtime_ui_review_to_none(self):
    contract = load_contract()
    skill = json.loads(
        (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
    )["skills"][0]
    skill.pop("verification", None)
    validated = contract.validate_skill_record(skill)
    self.assertEqual(validated["verification"], {
        "independent_review_required": False,
        "runtime_ui_review": "none",
    })

def test_skill_verification_rejects_unknown_runtime_ui_review_value(self):
    contract = load_contract()
    skill = json.loads(
        (ROOT / "skills-manifest.json").read_text(encoding="utf-8")
    )["skills"][0]
    skill["verification"] = {
        "independent_review_required": False,
        "runtime_ui_review": "sometimes",
    }
    with self.assertRaisesRegex(ValueError, "runtime_ui_review"):
        contract.validate_skill_record(skill)
```

- [ ] **Step 2: Add route aggregation tests before router changes**

Extend `synthetic_skill` with `runtime_ui_review="none"`, then assert:

```python
self.assertEqual(result["verification_requirements"], {
    "independent_review_required": True,
    "required_by": ["translate-first", "review-last"],
    "runtime_ui_review": "required",
    "runtime_ui_review_declared_by": ["review-last"],
})
```

Add a separate `recommended` + `none` case and verify strongest-value aggregation in selected load order.

- [ ] **Step 3: Run targeted tests and verify RED**

Run:

```sh
python3 -m unittest tests.test_catalog.CatalogTests.test_skill_verification_defaults_runtime_ui_review_to_none -v
python3 -m unittest tests.test_routing.RoutingTests.test_route_aggregates_runtime_ui_review_requirements -v
```

Expected: FAIL because verification accepts only `independent_review_required` and the route emits no runtime fields.

- [ ] **Step 4: Implement normalized metadata and aggregation**

Change `validate_verification` to accept only:

```python
{
    "independent_review_required": bool,
    "runtime_ui_review": "required" | "recommended" | "none",
}
```

Missing verification still normalizes to false/none for external and older records. Update the `SkillRecord.verification` value type so it can contain a string. Aggregate using the stable rank `none < recommended < required`.

- [ ] **Step 5: Declare initial cross-platform recommendations**

Set `runtime_ui_review` to `recommended` for `translating-mobile`, `translating-ios`, `translating-android`, and `translating-flutter`; set `none` everywhere else. Do not add web or generic desktop recommendation in this release.

- [ ] **Step 6: Render and verify generated catalogs**

Run:

```sh
python3 scripts/render_catalog.py
python3 scripts/render_catalog.py --check
```

Expected: both commands succeed, and Markdown includes the normalized runtime declaration.

- [ ] **Step 7: Run Task 2 tests and verify GREEN**

Run:

```sh
python3 -m unittest tests.test_catalog tests.test_manifest tests.test_routing -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```sh
git add tests/test_catalog.py tests/test_manifest.py tests/test_routing.py skills/translating-products/scripts/metadata_contract.py scripts/repo_model.py skills/translating-products/scripts/route_capabilities.py scripts/render_catalog.py skills-manifest.json skills/translating-products/references/capability-catalog.json skills/translating-products/references/capability-catalog.md
git commit -m "feat: route runtime UI review requirements"
```

### Task 3: Canonical runtime coverage record

**Files:**
- Modify: `tests/test_review_artifact.py`
- Modify: `skills/reviewing-translations/references/review-artifact-schema.json`
- Modify: `skills/reviewing-translations/scripts/validate_review_artifact.py`

**Interfaces:**
- Consumes request target record `runtime_ui_review: {decision, required, reasons}`.
- Produces result locale record `runtime_ui_review: {status, reason, evidence}`.

- [ ] **Step 1: Upgrade fixture helpers to schema version 2**

Add request target data:

```python
"runtime_ui_review": {
    "decision": "skip",
    "required": False,
    "reasons": ["no-applicable-runtime-surface"],
},
```

Add result locale data:

```python
"runtime_ui_review": {
    "status": "not_applicable",
    "reason": None,
    "evidence": [],
},
```

Change only the canonical review artifact schema and its tests to `schema_version: 2`; unrelated benchmark schemas remain version 1.

- [ ] **Step 2: Add outcome invariant tests before validator changes**

```python
def runtime_required_fixtures() -> tuple[dict, dict]:
    request = request_fixture()
    request["targets"][0]["runtime_ui_review"] = {
        "decision": "run",
        "required": True,
        "reasons": ["caller-required"],
    }
    result = result_fixture()
    result["locales"][0]["runtime_ui_review"] = {
        "status": "completed",
        "reason": None,
        "evidence": ["artifacts/signup-fr-FR-compact.png"],
    }
    return request, result

def test_completed_runtime_ui_review_requires_evidence(self):
    request, result = runtime_required_fixtures()
    result["locales"][0]["runtime_ui_review"] = {
        "status": "completed", "reason": None, "evidence": []
    }
    self.assert_invalid(result, "completed runtime UI review requires evidence", request)

def test_required_runtime_ui_review_rejects_not_run_but_accepts_unavailable(self):
    request, result = runtime_required_fixtures()
    result["locales"][0]["runtime_ui_review"] = {
        "status": "not_run", "reason": "skipped", "evidence": []
    }
    self.assert_invalid(result, "required runtime UI review cannot be not_run", request)
    result["locales"][0]["runtime_ui_review"] = {
        "status": "unavailable", "reason": "validation state has no reproducible path", "evidence": []
    }
    self.assertEqual(self.validate(request, result), ())
```

Also test reason/evidence shape, request/result target matching, and `not_applicable` rejection when the request decision is `run`.

- [ ] **Step 3: Run targeted tests and verify RED**

Run:

```sh
python3 -m unittest tests.test_review_artifact.ReviewArtifactTests.test_completed_runtime_ui_review_requires_evidence -v
python3 -m unittest tests.test_review_artifact.ReviewArtifactTests.test_required_runtime_ui_review_rejects_not_run_but_accepts_unavailable -v
```

Expected: FAIL because runtime fields are unknown and version 2 is unsupported.

- [ ] **Step 4: Extend schema and validator minimally**

Add target-level request and result definitions with `additionalProperties: false`. Validate:

- `completed`: nonempty evidence, null reason;
- `not_run`: nonblank reason, empty evidence, request decision `skip`;
- `unavailable`: nonblank reason, empty or real partial evidence, request decision `run`;
- `not_applicable`: empty evidence and request decision `skip` for a no-surface reason;
- request `required: true`: result cannot be `not_run`.

Keep per-unit classifications unchanged.

- [ ] **Step 5: Run Task 3 tests and verify GREEN**

Run:

```sh
python3 -m unittest tests.test_review_artifact -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```sh
git add tests/test_review_artifact.py skills/reviewing-translations/references/review-artifact-schema.json skills/reviewing-translations/scripts/validate_review_artifact.py
git commit -m "feat: record runtime UI review coverage"
```

### Task 4: Skill instructions and behavioral regression case

**Files:**
- Modify: `skills/translating-products/SKILL.md`
- Modify: `skills/reviewing-translations/SKILL.md`
- Modify: `skills/translating-mobile/SKILL.md`
- Modify: `skills/translating-ios/SKILL.md`
- Modify: `skills/translating-android/SKILL.md`
- Modify: `skills/translating-flutter/SKILL.md`
- Modify: `docs/architecture.md`
- Modify: `docs/getting-started.md`
- Modify: `tests/test_skill_contracts.py`
- Create: `evals/runtime-ui-review-cases.json`
- Modify: `tests/test_eval_fixtures.py`

**Interfaces:**
- Consumes: the resolver decision and the routed platform checks.
- Produces: a bounded runtime attempt or a focused question, then a compact canonical outcome.

- [ ] **Step 1: Add structural and fixture tests before editing skill prose**

Add three eval records:

```json
[
  {"id":"explicit-required","caller_intent":"required","project_intent":"disabled","route_requirement":"recommended","bounded_execution_path":false,"applicable_surface":true,"expected_decision":"run"},
  {"id":"project-required","caller_intent":"auto","project_intent":"required","route_requirement":"none","bounded_execution_path":null,"applicable_surface":true,"expected_decision":"run"},
  {"id":"mobile-validation-state-ask","caller_intent":null,"project_intent":"auto","route_requirement":"recommended","bounded_execution_path":null,"applicable_surface":true,"screen":"signup","state":"validation-error","expected_decision":"ask"}
]
```

Add contract assertions that the orchestrator invokes the runtime resolver after routing, the reviewing skill separates runtime coverage from per-unit classifications, and platform skills obey the resolved decision rather than independently choosing the budget.

- [ ] **Step 2: Run tests and verify RED**

Run:

```sh
python3 -m unittest tests.test_skill_contracts tests.test_eval_fixtures -v
```

Expected: FAIL because the new fixture and required decision language are absent.

- [ ] **Step 3: Write the minimal skill guidance**

Update the orchestrator with the resolver inputs, command, run/skip/ask branch, and outcome handoff. Update the review skill with the compact runtime record and claim boundary. Update mobile/platform skills so they supply scope and checks after a `run` decision and do not independently expand scope or spend tokens.

Keep the platform-specific device, accessibility, RTL, and state checks where they already live; do not duplicate them in the orchestrator.

- [ ] **Step 4: Document configuration and portability**

Document the optional project-brief value, precedence, cross-platform capability declaration, bounded execution path, and evidence references. State that execution depends on host/project tooling while the policy and record format remain portable.

- [ ] **Step 5: Run Task 4 tests and verify GREEN**

Run:

```sh
python3 -m unittest tests.test_skill_contracts tests.test_eval_fixtures -v
python3 scripts/validate_repo.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```sh
git add skills/translating-products/SKILL.md skills/reviewing-translations/SKILL.md skills/translating-mobile/SKILL.md skills/translating-ios/SKILL.md skills/translating-android/SKILL.md skills/translating-flutter/SKILL.md docs/architecture.md docs/getting-started.md tests/test_skill_contracts.py evals/runtime-ui-review-cases.json tests/test_eval_fixtures.py
git commit -m "docs: configure runtime UI review behavior"
```

### Task 5: Forward evaluation and full verification

**Files:**
- Modify only if a verified failure requires a scoped repair under the review-loop policy.

**Interfaces:**
- Consumes: the completed integrated branch and the baseline scenarios.
- Produces: behavioral evidence that run/skip/ask decisions converge and the repository remains valid.

- [ ] **Step 1: Run the baseline scenario against the updated skills**

Use a fresh agent context with the same three baseline requests and no intended answer. Verify:

- explicit `required` resolves to run;
- approved project `required` resolves to run when caller is absent or `auto`;
- unresolved recommended mobile coverage resolves to one focused question;
- each path records or prescribes a target-level runtime outcome rather than inferring coverage from `no_issue_detected`.

- [ ] **Step 2: Run targeted verification**

```sh
python3 scripts/render_catalog.py --check
python3 scripts/validate_repo.py
python3 -m unittest tests.test_bootstrap_policy tests.test_catalog tests.test_manifest tests.test_routing tests.test_review_artifact tests.test_skill_contracts tests.test_eval_fixtures -v
```

Expected: PASS.

- [ ] **Step 3: Run the full offline suite**

```sh
python3 -m unittest discover -s tests -v
```

Expected: PASS with no unexplained failures.

- [ ] **Step 4: Run skill package validation where available**

Run the installed skill creator validator separately for each changed skill directory:

```sh
python3 /Users/brunomiguens/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/translating-products
python3 /Users/brunomiguens/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/reviewing-translations
python3 /Users/brunomiguens/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/translating-mobile
python3 /Users/brunomiguens/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/translating-ios
python3 /Users/brunomiguens/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/translating-android
python3 /Users/brunomiguens/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/translating-flutter
```

Expected: each command succeeds. If the installed validator path differs or is unavailable, record that exact limitation and rely on repository validation.

- [ ] **Step 5: Record the verified final checkpoint and request one whole-branch review**

Use the unloop checkpoint helper to record the final SHA, exact verification commands, and requirement evidence. Reserve the final internal review gate before invoking one reviewer over the complete base-to-head diff. Triage every finding and apply at most the permitted repair wave and scoped re-review.

- [ ] **Step 6: Commit any final generated or evidence-backed repair changes**

```sh
git status --short
git diff --check
```

Only commit tracked implementation or generated catalog changes. Keep temporary evaluation artifacts outside the repository.
