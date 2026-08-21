# Release checklist

## Current state

The suite remains a prerelease at
`<!-- suite-version:start -->0.4.0<!-- suite-version:end -->` with 23 manifest
skills and 7 pinned adapted sources. The canonical repository is
[`BrunoMiguens/translating-products-skill`](https://github.com/BrunoMiguens/translating-products-skill).

Do not promote to `1.0.0` until every gate below is complete for the exact
release commit. Automated checks validate packaging, routing, schemas, and
structural behavior; they do not replace proficient bilingual review or imply
that runtime translations were reviewed by a human.

## Release gates

| Gate | Status | Required evidence |
| --- | --- | --- |
| Canonical repository configured | Complete | `origin` points to `BrunoMiguens/translating-products-skill`. |
| Manifest and generated catalog agree | Repeat for release | `render_catalog.py --check` passes and reports the release version and exact skill set. |
| Repository validation passes | Repeat for release | `validate_repo.py` passes on the exact release commit. |
| Unit and evaluation tests pass | Repeat for release | Full offline test suite passes on the exact release commit. |
| Source checksums verified | Repeat for release | Networked source verification confirms all immutable source bytes. |
| Licenses and notices reviewed | Repeat for release | Every adapted source has compatible licensing and matching notice text. |
| Cross-agent installation passes | Repeat for release | Disposable Claude Code, Codex, Cursor, and universal targets match the exact manifest inventory. |
| Host-level fixtures recorded | Blocked | Record all translation-quality and structural-fidelity fixtures on supported hosts. |
| Bilingual review recorded | Blocked | Record reviewer, locales, fixture revision, date, and findings for every language specialist proposed as stable. |
| Remote skills preview reviewed | Repeat for release | The canonical repository listing contains the exact release inventory. |
| Release worktree clean | Repeat for release | No tracked or untracked release artifacts remain after the final commit. |

## Verification commands

Run from the repository root:

```bash
python3 scripts/render_catalog.py
python3 scripts/render_catalog.py --check
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
npx skills add . --list
bash scripts/smoke_install.sh
git diff --check
git status --short
```

Run the networked immutable-source check separately:

```bash
python3 scripts/verify_sources.py
```

Before publication, preview the canonical repository rather than only the local
checkout:

```bash
npx skills add BrunoMiguens/translating-products-skill --list
```

## Evidence rules

- Bind results to the exact release commit and record the command, date, host,
  and relevant versions.
- Keep private product strings and review packets outside tracked files.
- Do not reuse a regression set as a fresh comparative holdout.
- Do not create reviewer attestations, linguistic sign-offs, or human labels on
  someone else's behalf.
- Treat a prior checkpoint as historical evidence only; repeat every mutable
  check for the release candidate.

## Historical checkpoint

On 2026-08-07, schema `2` declared 22 skills. Catalog rendering, repository
validation, the then-current unittest suite, local discovery, source
verification, and disposable cross-agent installation passed for that snapshot.
That evidence predates the 23-skill suite and cannot release the current tree.

## Promotion rule

Promote to `1.0.0` only when every release-gate row is Complete for the same
commit. Update the suite version and manifest assertions, render generated
files, rerun the complete checklist, review the remote listing, and preserve the
evidence with the release.
