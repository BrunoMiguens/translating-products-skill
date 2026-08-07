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
