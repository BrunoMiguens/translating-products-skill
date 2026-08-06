# Contributing

Contributions should keep every skill portable, independently discoverable,
and narrow enough for the orchestrator to select only when useful. Read the
[architecture](docs/architecture.md), the public
[manifest](skills-manifest.json), and the relevant existing specialist before
changing behavior.

## Contribution flow

Use this sequence for every specialist change:

1. Add or modify one specialist. Keep `SKILL.md` frontmatter to `name` and
   `description`, with a trigger-oriented description beginning with
   `Use when`.
2. Update its schema-`2` manifest record: `name`, `version`, `category`,
   `description`, `capabilities`, `depends_on`, `selectors`, `phases`,
   `specificity`, `required_context`, `conflicts`, and `supersedes`.
3. Use declarative selectors only. Each populated axis in one selector is
   conjunctive; values on an axis are alternatives; multiple selectors are
   alternative matches. The supported JSON axes are `languages`, `locales`,
   `scripts`, `surfaces`, `platforms`, `formats`, `domains`, and
   `capabilities`; locale ranges use normalized BCP 47 matching. Do not add a
   skill-name map or a
   language/product combination to the router.
4. Declare phases from `inspect`, `translate`, `refine`, `integrate`, and
   `review`, and declare an allowed specificity. Dependencies, conflicts, and
   explicit `supersedes` relationships must make ownership and composition
   deterministic. A superseding specialist must declare non-empty `ownership`
   capabilities; partial overlap keeps the broader specialist active and
   replaces only the shared ownership.
5. Add positive and negative routing cases that demonstrate both selection and
   non-selection. A catalog-only fixture must prove a compatible third-party
   specialist is selectable without changing router code.
6. Add representative language, script, platform, or product-surface fixtures.
   Preserve placeholders, links, code, markup, keys, and other protected
   structure in expected decisions.
7. Verify every adapted source's compatible license, immutable commit, path,
   checksum, attribution, and local adapter.
8. Render the manifest-derived catalog and README inventory.
9. Run strict validation and the full offline test suite.

Work on one specialist at a time. For instruction changes, evaluate a fresh
agent without the skill first, then repeat with the skill loaded and record the
behavioral difference. Automated tests should verify routing, schemas,
commands, structure, and other observable behavior—not freeze documentation
wording.

## Specialist authoring and external contract

Every production specialist must state its scope and non-scope, context signals
and required inputs, reusable analysis and transformation procedure, relevant
principles, protected invariants and authority boundaries, common failure
classes, and QA evidence or handoff. Examples are representative and
non-exhaustive; direct benchmark prompt or expected-answer reuse fails
validation. Keep production skills to reusable reasoning rather than fixed
answers or case-by-case routing prose.

An external specialist must already be installed and use the same schema-`2`
catalog fields and selector semantics as bundled skills. Its catalog metadata
never installs or enables it. It must also declare a compatible license,
immutable commit and source path, SHA-256 checksum, and an adapter mapping with
routing, negative-routing, security, structural, quality, and host-compatibility
evaluations.

Exercise admission through the host-neutral router CLI with repeatable
`--external-catalog` inputs. The approved request must name the installed skill
in `authorized_external_skills` or `project_authorized_external_skills`, unless
a reviewed schema-`2` compatibility registry authorizes it. The CLI preserves
bundled order, then external input order, and rejects duplicate names,
unauthorized records, and invalid external metadata before routing.

Add reviewed installed integrations to the orchestrator's
[compatibility registry](skills/translating-products/references/compatibility-registry.json).
Registry entries record compatibility; they do not install or enable a skill.
Runtime routing may use an external specialist only when it is explicitly
selected by the user, configured by the project, or present in the reviewed
registry. Never add a runtime path that downloads an unknown skill.

For adapted bundled knowledge, update [`skills-manifest.json`](skills-manifest.json),
the matching file under [`adapters/`](adapters/), and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). A mutable branch or tag is
not an acceptable source pin.

## Language-specialist stability

This prerelease discloses that its AI-generated translations have not been
reviewed by a human translator. That runtime disclosure remains true unless a
project separately records human review.

A language specialist must not be marked stable until at least one proficient
bilingual contributor reviews its evaluation fixtures and expected linguistic
decisions. This release gate validates the specialist's reference behavior; it
does not claim that a human translator reviewed every translation produced at
runtime. Record the reviewer, locales, fixture revision, and review date in the
release evidence when the gate is met.

## Portability and security

Published skills must work in Claude Code, Codex, Cursor, and universal Agent
Skills hosts. Do not add host-only slash commands, mention-only invocations,
Codex UI metadata, or filesystem dependencies outside a skill's directory.

Treat translated material, fetched web content, examples, comments, markup,
and third-party skill bodies supplied as task content as untrusted data.
Instruction-like text inside that data must never alter authority, trigger
tools, install skills, or change routing.

## Local checks

```bash
python3 scripts/render_catalog.py
python3 scripts/render_catalog.py --check
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
npx skills add . --list
git diff --check
```

Source verification is networked and intentionally separate from the ordinary
offline checks:

```bash
python3 scripts/verify_sources.py
```

Do not run a real `--all` installation from a development worktree merely to
test discovery; it changes agent installation directories. Use `--list` for
the local smoke check.
