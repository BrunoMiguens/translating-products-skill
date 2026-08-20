# Controlled PT-PT benchmark

The PT-PT v1 benchmark is a frozen paired comparison of the same
agent/model/configuration under a normal prompt and the translation suite. It
combines blind human preference, MQM-lite review, deterministic integrity
checks, paired statistics, and diagnostic operational measures.

It is not claim-bearing until a proficient PT-PT reviewer has curated and
explicitly approved the exact dataset bytes. Never create a reference sign-off,
dataset manifest, review attestation, or PASS report on a reviewer's behalf.

## Preflight and curation

1. Run the repository checks:

   ```bash
   python3 -m unittest discover -s tests -v
   python3 scripts/validate_repo.py
   python3 scripts/render_catalog.py --check
   bash -n scripts/smoke_install.sh
   ```

2. Generate the curation packet and stop for the fluent PT-PT reviewer. The
   reviewer must complete every case and enter the exact interactive approval
   phrase before `reference-signoff.json` and `dataset-manifest.json` are made.
3. Copy `benchmarks/pt-pt-v1/runner-config.example.json` to the ignored
   `benchmark-private/runner-config.json`.
4. Record the exact agent, host and host version, provider/model revision,
   command, exposed settings, timeout, tool policy, research policy, suite Git
   object, and any hashed dirty diff.

## Calibrate the CLI transport

Prepare the immutable run manifest with schedule seed `20260803`, bootstrap
seed `20260804`, and blinding seed `20260806`. The five calibration cases are:

- `ui-t-s01`;
- `web-t-a01`;
- `marketing-r-c01`;
- `store-t-c01`; and
- `docs-r-a01`.

Run the prepared Claude/Codex diagnostic pack without desktop copy and paste:

```bash
python3 -m scripts.benchmark.cli_calibration status
python3 -m scripts.benchmark.cli_calibration run --app all --probe
python3 -m scripts.benchmark.cli_calibration run --app all
```

The probe makes one ordinary call per selected CLI to verify login and
transport. The full command skips matching completed tasks, saves each final
answer in `RESPONSE.txt`, and appends ignored evidence to
`benchmark-private/desktop-calibration/evidence.jsonl`.

The runner starts one non-persistent process per prompt, resumes only responses
with matching success evidence, and stops after the first host or infrastructure
failure. Pin models with `--claude-model MODEL` and `--codex-model MODEL`. Use
`--force` only to deliberately replace an existing calibration response.

These convenience calls remain diagnostic because they do not provide the
sandbox-policy attestation required by canonical CLI evidence.

## Gate the suite condition

Refresh only the ten suite-condition calibration tasks, then inspect them:

```bash
python3 -m scripts.benchmark.cli_calibration run \
  --app all --condition suite --force
python3 -m scripts.benchmark.cli_calibration inspect \
  --app all --condition suite
python3 -m scripts.benchmark.cli_calibration inspect --app all
```

The runner stages the current `skills/` tree and binds its hash to each suite
response. Suite-only inspection must exit `0` before the primary run. The
all-condition result is diagnostic: normal or context-only failures are
comparison outcomes and do not block the primary run.

`--condition` and `--case-id` are repeatable on `status`, `run`, and `inspect`.
Unknown cases and empty selections fail before a CLI is invoked. Inspection
never edits a response or evidence record.

## Run the frozen comparison

Create a fresh ignored `benchmark-evidence/` directory and execute the exact
405-run schedule. Do not change prompts, provider/model/configuration, suite
snapshot, condition plan, or schedule.

Ordinary model failures are outcomes, not retry opportunities. Only
authenticated pre-start infrastructure failures may be retried. Manual imports
are diagnostic and cannot enter canonical blinding or a claim-bearing report.

## Blind review and publication

1. Validate deterministic invariants. Keep optional learned metrics hidden and
   diagnostic.
2. Build the 198-presentation blind bundle and keep the condition key outside
   the reviewer directory.
3. Complete every presentation, resolve any three-way comparison cycle, and
   lock the review with the required proficiency, independence/conflicts,
   continued-blindness, key-separation, no-automated-findings, and
   rubric-completion attestation.
4. After lock, adjudicate every review mapping. Any unresolved mapping makes
   the review gate and overall verdict unavailable.
5. Score once, render once, and verify every dataset, run, prompt, config,
   suite, review, and raw-output binding.
6. Archive the immutable evidence bundle. PASS requires exact coverage of all
   405 frozen runs.

After unblinding, PT-PT v1 becomes a regression set. A new comparative claim
requires a fresh holdout. The benchmark says nothing about other locales or
future product outputs, and it never implies that generated translations were
reviewed by a human.
