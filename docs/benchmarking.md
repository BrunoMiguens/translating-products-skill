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
