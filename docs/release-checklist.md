# Release checklist

## Current decision

The suite remains a prerelease at `<!-- suite-version:start -->0.3.0<!-- suite-version:end -->`. Do not promote
`skills-manifest.json` or its manifest test to `1.0.0` until every gate below
is complete and its evidence is recorded.

Automated repository, installation, source, and discovery checks pass. The
first stable release is still blocked by the missing public remote,
host-evaluation record, and proficient bilingual reviews. No human translator
review is claimed.

## Task 7 verification — 2026-08-07

The schema `2` manifest declared 22 skills on this date. The following commands
were run from the repository worktree and exited `0`:

```bash
python3 scripts/render_catalog.py
python3 scripts/render_catalog.py --check
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
npx skills add . --list
git diff --check
bash scripts/smoke_install.sh
```

The validator reported `validated 22 skills and 7 sources`; the full unittest
suite passed; the discovery preview listed the 22 manifest skills; and the
smoke installer copied and compared the exact 22-skill inventory for
`claude-code`, `codex`, `cursor`, and `universal` in disposable marked
temporary directories before removing them. This installation evidence checks
suite packaging, discovery, and host portability. It does not pass or claim the
separate PT-PT human-curated benchmark or any human translator review.

## Gates

| Gate | Status | Current evidence or required record |
| --- | --- | --- |
| Clean Git status | Complete for this checkpoint | `git status --short` must produce no output after the Task 12 commit; repeat this check for the eventual release commit. |
| Rendered catalog current | Complete | `python3 -B scripts/render_catalog.py --check` passed on 2026-08-03. |
| Repository validator passing | Complete | `python3 -B scripts/validate_repo.py` reported 22 skills and 7 sources on 2026-08-03. |
| Unit and evaluation tests passing | Complete | `python3 -B -m unittest discover -s tests -v` passed 74 tests on 2026-08-03. Automated checks validate schemas and mechanics; they do not replace linguistic review. |
| Source checksums verified | Complete | An approved-network run of `python3 scripts/verify_sources.py` reported `verified 7 pinned sources` on 2026-08-03. The restricted-network attempt failed closed before that run. |
| License and notices reviewed | Complete | Task 9 reviewed every pinned license and attribution; `THIRD_PARTY_NOTICES.md` contains all seven records and the applicable license texts. |
| Cross-agent smoke tests passing | Complete | An approved-network run of `bash scripts/smoke_install.sh` copied and verified exactly 22 skills for `claude-code`, `codex`, `cursor`, and `universal`, then removed its scratch directory on 2026-08-03. |
| Host-level runs recorded for all fixtures | Blocked | Record results for all 22 translation-quality and all 12 structural-fidelity fixtures on supported hosts. Schema-only Python checks are insufficient. |
| Proficient bilingual review for every stable language skill | Blocked | Record reviewer, locales, fixture revision, date, and findings for every language specialist proposed as stable. No such review is currently recorded. |
| Git remote owner/repository confirmed | Blocked | `git remote -v` produced no output on 2026-08-03. Configure and confirm the intended public GitHub owner/repository before release. |
| skills.sh list preview reviewed | Complete | The approved-network Task 11 run of `npx skills add . --list` found all 22 skills and returned successfully. Repeat against the configured public remote before publishing. |

## Promotion rule

Promote to `1.0.0` only when every row is Complete, the evidence applies to
the exact release commit, and a final clean-status check passes. Then update
the suite version and its manifest assertion, render the catalog again, rerun
the full checklist, and review the skills.sh listing for the public remote.
