# Private product-review benchmark

Use this workflow to compare translation-review behavior against real product
resources without copying private strings into the repository. The runner
invokes Claude Code and Codex non-interactively and creates isolated `normal`,
`current_suite`, and `improved` reviews from exact Git objects.

The runner does not create human labels or claim human review.

## Inputs

Provide:

- an exact product repository and Git object;
- repository-relative source and target resource paths;
- exact source and target locales;
- an approved `.translation/` context or permission to propose one;
- exact baseline and candidate suite Git objects; and
- the selected CLI hosts and model aliases.

Resources must be flat JSON string maps. The runner reads both files from the
exact product Git object and writes their canonical values into every CSV.
Models return only keys and review decisions, so they cannot rewrite source
fields. Resource paths, locales, and Git objects are bound into the run
manifest.

## Prepare product context

If the requested `.translation/` context does not exist, the same command runs
Claude or Codex non-interactively against the exact product snapshot. The
agent inspects the repository, derives conservative configuration, and writes
only the five required context files in a disposable workspace.

The setup agent cannot approve its proposal and does not open an interactive
agent terminal. The runner prints every complete proposed file. The only user
input is the approval gate: Type `approve` exactly to bind your identity and
publish the context. Any other input stops without publishing context or
creating benchmark evidence.

The disposable workspace does not modify the product checkout. It stages the
candidate suite Git object instead of trusting globally installed skills. Both
suite revisions must accept the approved bytes before the first benchmark
model call.

An existing ready context skips setup. An incomplete or stale context requires
`--replace-context`; the previous directory is preserved unless the new
proposal is approved and passes both suite preflights.

## Run a probe

Keep all product-specific values in ignored private storage or outside this
repository. From the suite repository, run:

```bash
python3 -m scripts.benchmark.product_runner run \
  --root benchmark-private/<run-name>/runs \
  --product-repo /absolute/path/to/product-repository \
  --product-git-object <product-commit> \
  --source-resource <repository-relative-source.json> \
  --source-locale <source-locale> \
  --target-resource <repository-relative-target.json> \
  --target-locale <target-locale> \
  --translation-context benchmark-private/<context-name>/.translation \
  --suite-repo /absolute/path/to/translating-products-skill \
  --current-suite-git-object <baseline-suite-commit> \
  --improved-suite-git-object <candidate-suite-commit> \
  --setup-app codex \
  --approved-by <reviewer-name> \
  --setup-model <setup-model> \
  --app all \
  --timeout-seconds 600 \
  --probe
```

Use `--setup-app claude` to propose context through Claude Code. The matching
`--codex-executable` or `--claude-executable` selects a non-default binary.

If the probe succeeds, rerun the identical command after removing only
`--probe`. Do not add `--force`: evidence-bound successes resume automatically.
If one durable timeout or model failure must be deliberately replaced, filter
to its `--app` and `--condition`, add `--force`, and keep every provenance
argument unchanged.

Pin `--claude-model` and `--codex-model` on the first command when host aliases
are not immutable.

## Check the run

These commands do not invoke a model:

```bash
python3 -m scripts.benchmark.product_runner status \
  --root benchmark-private/<run-name>/runs \
  --app all

python3 -m scripts.benchmark.product_runner inspect \
  --root benchmark-private/<run-name>/runs \
  --app all
```

`inspect` must report three passes for every selected host. It also verifies
that locale, key, source, and current-translation rows match across conditions.

## Prepare blinded human review

Prepare one packet per host. For Codex:

```bash
python3 -m scripts.benchmark.product_review prepare \
  --input-csv benchmark-private/<run-name>/runs/codex/current-suite.csv \
  --output-dir benchmark-private/<run-name>/codex-packet \
  --suite-git-object <baseline-suite-commit>
```

The packet preserves the candidate bytes at
`conditions/current-suite.csv` and records their hash and suite Git object.
Its `human-review.csv` contains only:

```text
locale,key,english_source,current_translation,human_decision,
human_correction,human_notes,human_severity
```

The last four fields are blank. Automated statuses, reasons, and corrections
are excluded, and the tool never creates a human sign-off.

A qualified reviewer completes every row separately.

## Score the three conditions

```bash
python3 -m scripts.benchmark.product_review score \
  --packet-dir benchmark-private/<run-name>/codex-packet \
  --candidate normal=benchmark-private/<run-name>/runs/codex/normal.csv \
  --candidate current_suite=benchmark-private/<run-name>/codex-packet/conditions/current-suite.csv \
  --candidate improved=benchmark-private/<run-name>/runs/codex/improved.csv \
  --output benchmark-private/<run-name>/codex-packet/score.json
```

Repeat preparation and scoring with `claude` paths to measure Claude
separately. Controlled comparisons are within a host, never Claude versus
Codex.

The scorer reports the four measures documented in the
[benchmarking overview](../benchmarking.md#measures), plus percentage-point
changes for `improved - normal` and `improved - current_suite`. A candidate
correction that differs from the human correction remains an explicit
disagreement; no human preference is inferred.

The scorer refuses incomplete human rows, changed preserved bytes, mismatched
product identity or source fields, duplicate or aliased inputs, missing
conditions, symlinks, and overwrites.

## Privacy and regression use

Packets stored inside this repository must stay beneath ignored
`benchmark-private/`; external private paths are also supported. Never place
real product strings, model responses, or reviewer decisions under tracked
`benchmarks/`, docs, fixtures, or skills.

If a parent-directory durability check or final held-input verification fails
after publication, the command returns an error and leaves the public pathname
in place. Inspect and explicitly remove the failed output before retrying.

After human decisions influence skill changes, the reviewed cases are a
regression set rather than a fresh superiority holdout. Production skills never
read these packets. Encode general translation mechanisms in the suite; do not
copy private corrections into instructions.

When canonical evidence provides latency, token usage, or cost, use the
benchmark scorer's `latency_seconds`, `usage`, and `cost_usd` diagnostics rather
than deriving them from product-review CSVs.
