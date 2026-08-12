# PT-PT benchmark operations

The PT-PT v1 comparison is not claim-bearing until a proficient PT-PT reviewer
has curated and explicitly approved the exact dataset bytes. Never create the
reference sign-off, dataset manifest, review attestation, or a PASS report on a
reviewer's behalf.

## Preflight and curation gate

1. Run `python3 -m unittest discover -s tests -v`,
   `python3 scripts/validate_repo.py`, `python3 scripts/render_catalog.py --check`,
   and `bash -n scripts/smoke_install.sh`.
2. Generate the curation packet and stop for the fluent PT-PT reviewer. The
   reviewer must complete every case and use the interactive exact approval
   phrase before `reference-signoff.json` and `dataset-manifest.json` are made.
3. Copy `benchmarks/pt-pt-v1/runner-config.example.json` to the ignored
   `benchmark-private/runner-config.json`. Record the exact agent, host and host
   version, provider/model revision, command, exposed settings, timeout, tool
   policy, research policy, suite Git object, and any hashed dirty diff.

## Calibration and primary run

Prepare the immutable run manifest with schedule seed `20260803`, bootstrap seed
`20260804`, and blinding seed `20260806`. Run the five calibration cases
`ui-t-s01`, `web-t-a01`, `marketing-r-c01`, `store-t-c01`, and `docs-r-a01`
through each applicable condition. Inspect transport only, record the discarded
calibration evidence hash, and never merge those outputs into the primary run.

For the prepared Claude/Codex diagnostic pack, validate and run all 26 calls
without desktop copy/paste:

```bash
python3 -m scripts.benchmark.cli_calibration status
python3 -m scripts.benchmark.cli_calibration run --app all --probe
python3 -m scripts.benchmark.cli_calibration run --app all
```

The probe makes one ordinary calibration call per selected CLI to verify login
and transport. After it succeeds, the full command skips those two completed
tasks and runs the remaining 24. The runner uses the existing CLI logins, starts
one non-persistent process per prompt, saves final answers in each task's
`RESPONSE.txt`, and appends ignored diagnostic evidence to
`benchmark-private/desktop-calibration/evidence.jsonl`.
It resumes only responses with matching success evidence and stops after the
first host or infrastructure failure. Pin a provider model with
`--claude-model MODEL` and `--codex-model MODEL`; use `--force` only when an
existing calibration response must be deliberately replaced. These convenience
runs remain diagnostic because they do not provide the sandbox-policy
attestation required by canonical CLI evidence.

Before a primary run, refresh only the ten suite-condition calibration tasks and
apply the deterministic output gate:

```bash
python3 -m scripts.benchmark.cli_calibration run \
  --app all --condition suite --force
python3 -m scripts.benchmark.cli_calibration inspect \
  --app all --condition suite
python3 -m scripts.benchmark.cli_calibration inspect --app all
```

The runner stages the repository's current `skills/` tree and binds its hash to
each suite response. The suite-only inspection must exit `0` before preparing
the primary run. The all-condition inspection is diagnostic: failures in normal
or context-only outputs are expected comparison outcomes and do not block the
primary run. `--condition` and `--case-id` are repeatable on `status`, `run`, and
`inspect`; a filter that names an unknown case or selects no task fails before a
CLI is invoked. Inspection never edits a response or evidence record.

Create a fresh ignored `benchmark-evidence/` directory and execute the exact
405-run schedule without changing prompts, provider/model/configuration, suite
snapshot, condition plan, or schedule. Ordinary model failures are outcomes,
not retry opportunities; only authenticated pre-start infrastructure failures
may be retried. Manual imports are diagnostic only and cannot enter canonical
blinding or a claim-bearing report.

## Blind review, score, and publication

1. Validate deterministic invariants. Keep optional learned metrics hidden and
   diagnostic.
2. Build the 198-presentation blind bundle and keep the condition key outside
   the reviewer directory.
3. Start the loopback reviewer, complete every presentation, resolve any
   three-way comparison cycle, and lock with the required PT-PT proficiency,
   independence/conflicts, continued-blindness, key-separation, no-automated-
   findings, and rubric-completion attestation.
4. Only after lock, adjudicate every review mapping. Any unresolved mapping makes
   the review gate and overall verdict unavailable.
5. Score once, render once, verify every dataset/run/prompt/config/suite/review/
   raw-output binding, and archive the immutable evidence bundle. A PASS needs
   exact coverage of all 405 frozen runs.

After unblinding, PT-PT v1 is a regression set. A new comparative claim requires
a fresh holdout. The benchmark says nothing about other locales or future product
outputs, and it never implies those outputs received human translation review.

## Private product-review regression packets

Real product review output must stay in ignored private storage. Prepare a
packet from an exact current-suite CSV without copying product strings into the
tracked `benchmarks/` tree:

```bash
python3 -m scripts.benchmark.product_review prepare \
  --input-csv /absolute/path/automated-review.csv \
  --output-dir benchmark-private/product-review-baseline \
  --suite-git-object 13cf73e
```

Preparation preserves the input bytes at
`conditions/current-suite.csv`, records their hash and the suite Git object,
and creates `human-review.csv`. The curation CSV contains only `locale`, `key`,
`english_source`, and `current_translation`, followed by blank
`human_decision`, `human_correction`, `human_notes`, and `human_severity`
fields. It never contains the automated status, reason, or recommendation and
never creates a human sign-off. A qualified human completes those fields
separately; the tool does not infer a decision, correction, severity, or
preference.

After human completion, score all three mandatory conditions against the same
completed human-review bytes:

```bash
python3 -m scripts.benchmark.product_review score \
  --packet-dir benchmark-private/product-review-baseline \
  --candidate normal=/absolute/path/normal-review.csv \
  --candidate current_suite=benchmark-private/product-review-baseline/conditions/current-suite.csv \
  --candidate improved=/absolute/path/improved-review.csv \
  --output benchmark-private/product-review-baseline/score.json
```

The scorer reports `required_error_recall`, `reported_error_precision`,
`false_positive_correction_rate`, and exact `correction_success_rate` for each
condition, plus the percentage-point differences `improved - normal` and
`improved - current_suite`. A candidate correction that differs from the human
correction remains an explicit disagreement with no inferred human preference.
The scorer refuses incomplete human rows, drifted preserved bytes, mismatched
product identities or source fields, duplicate or aliased inputs, missing
conditions, symlinks, and overwrite.

This current product run is baseline evidence. Once human decisions are added,
these reviewed cases form a regression set, not a fresh superiority holdout.
The three conditions provide a holistic diagnostic comparison only. Production
skills never read these packets, and product data never enters the tracked
benchmark dataset. Any comparative public claim requires a fresh, signed
holdout. When canonical run evidence supplies latency, token usage, or cost,
use the existing benchmark scorer's `latency_seconds`, `usage`, and `cost_usd`
diagnostics rather than deriving them from the product-review CSVs.
