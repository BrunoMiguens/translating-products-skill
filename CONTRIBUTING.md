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
2. Update its manifest version, capabilities, dependencies, and the suite
   compatibility metadata when required.
3. Add positive and negative routing cases that demonstrate both selection and
   non-selection.
4. Add representative language, script, platform, or product-surface fixtures.
   Preserve placeholders, links, code, markup, keys, and other protected
   structure in expected decisions.
5. Verify every adapted source's compatible license, immutable commit, path,
   checksum, attribution, and local adapter.
6. Render the manifest-derived catalog and README inventory.
7. Run strict validation and the full offline test suite.

Work on one specialist at a time. For instruction changes, evaluate a fresh
agent without the skill first, then repeat with the skill loaded and record the
behavioral difference. Automated tests should verify routing, schemas,
commands, structure, and other observable behavior—not freeze documentation
wording.

## External skill contract

An external specialist must already be installed and must declare, without
ambiguity:

- a unique name and version plus the minimum orchestrator version;
- capabilities and their authority scope;
- supported languages, locales, surfaces, and domains;
- required inputs and produced outputs;
- dependencies, conflicts, and precedence;
- a compatible license;
- an immutable commit and source path;
- a SHA-256 checksum; and
- an adapter mapping with routing, negative-routing, security, structural,
  quality, and host-compatibility evaluations.

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
