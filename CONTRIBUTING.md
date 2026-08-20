# Contributing

Contributions should keep every skill portable, independently discoverable,
and narrow enough for `translating-products` to select only when useful.

Before changing behavior, read the [architecture](docs/architecture.md),
[`skills-manifest.json`](skills-manifest.json), and the closest existing
specialist.

## Choose the contribution path

| Change | Main files |
| --- | --- |
| Add or improve a language specialist | `skills/<name>/`, manifest record, routing and linguistic fixtures |
| Add a surface or platform | `skills/<name>/`, manifest record, format and structural fixtures |
| Adapt reviewed external knowledge | Specialist, `adapters/`, manifest source record, `THIRD_PARTY_NOTICES.md` |
| Register an installed third-party skill | Extension-contract metadata, compatibility registry, routing and security evaluations |
| Improve translation behavior | Owning specialist plus fresh behavioral evaluation |
| Change routing or review policy | Orchestrator references, policy scripts, positive and negative tests |

Work on one coherent capability at a time. Do not mix unrelated skill changes
into the same contribution.

## Rules every skill follows

- Store each discoverable skill at `skills/<name>/SKILL.md`.
- Keep frontmatter to `name` and a trigger-oriented `description` beginning
  with `Use when`.
- State scope, non-scope, required context, reusable procedure, principles,
  protected invariants, authority boundaries, common failures, and QA handoff.
- Keep examples representative and non-exhaustive. Do not embed benchmark
  prompts, private product strings, or expected reviewed corrections.
- Treat existing target copy and human suggestions as evidence. Verify meaning,
  structure, participant roles, terminology, and locale fit before accepting
  them.
- Preserve placeholders, links, code, markup, keys, numbers, and other
  protected product structure.
- Avoid host-specific slash commands, mention syntax, UI metadata, or paths
  outside the skill directory.

Language guidance should teach transferable mechanisms: event framing,
collocation, agreement, address strategy, register dimensions, and surface
realization. It should not grow into a list of hardcoded corrections.

## Add or update a bundled specialist

1. Create or update the focused `SKILL.md`.
2. Add or update its schema-`2` manifest record:
   `name`, `version`, `category`, `description`, `capabilities`, `depends_on`,
   `phases`, `specificity`, `required_context`, `conflicts`, and `supersedes`.
3. Add `selectors` for scoped matching. Omit selectors only when the skill is
   universally applicable.
4. Add positive and negative routing cases.
5. Add representative behavioral and structural fixtures.
6. Render generated documentation and run all checks.

### Selector semantics

The supported selector axes are:

```text
languages, locales, scripts, surfaces, platforms, formats, domains, capabilities
```

A present selector list, every selector object, and every populated axis must
be non-empty. Values on one axis are alternatives; populated axes within one
selector are conjunctive; multiple selectors are alternative matches. Locale
ranges use normalized BCP 47 matching.

Keep routing declarative. Do not add a skill-name map, surface-to-skill table,
or language/product combination rule to the router.

### Phases, ownership, and composition

Declare phases from:

```text
inspect, translate, refine, integrate, review
```

Dependencies, conflicts, specificity, ownership, and explicit `supersedes`
relationships must make composition deterministic.

A superseding specialist needs non-empty effective ownership. If ownership is
omitted, it derives from the full declared capability and phase scope. Partial
overlap keeps the broader specialist active and replaces only the shared
ownership. One skill cannot both depend on and supersede the same target.

### Fixtures and evaluations

Routing tests should demonstrate both selection and non-selection. A
catalog-only fixture must prove that a compatible third-party specialist can be
selected without changing router code.

Behavioral fixtures should cover representative language, script, platform, or
surface decisions. Add semantic groups when strings jointly determine
terminology, participant roles, agency, or truth. Use synthetic examples only.

For instruction changes, evaluate a fresh agent without the proposed skill,
then repeat with the skill loaded and record the behavioral difference.
Automated tests should verify observable routing, schemas, commands, and
structure—not freeze documentation wording.

## Integrate an external skill

External catalog metadata discovers already installed skills; it never installs
or enables them. An eligible external specialist must:

- use the same schema-`2` fields and selector semantics;
- declare a compatible license, immutable commit, source path, checksum, and
  adapter mapping;
- include routing, negative-routing, security, structural, quality, and host
  compatibility evaluations; and
- be authorized by the user, project, or reviewed compatibility registry.

Exercise admission through the host-neutral router CLI with repeatable
`--external-catalog` inputs. The request must name the installed skill in
`authorized_external_skills` or `project_authorized_external_skills`, unless a
reviewed registry record authorizes it. The CLI preserves bundled order and
then external input order; it rejects duplicate names, unauthorized records,
and invalid metadata before routing.

Reviewed integrations belong in the
[compatibility registry](skills/translating-products/references/compatibility-registry.json).
Follow the [extension contract](skills/translating-products/references/extension-contract.md)
for selectors, phases, ownership, version constraints, reviewer identity,
review date, and record attestation. Registry membership proves compatibility;
it does not authorize runtime installation.

Never add a translation-time path that downloads an unknown skill.

## Adapt an upstream source

For adapted bundled knowledge, update:

- [`skills-manifest.json`](skills-manifest.json);
- the matching record under [`adapters/`](adapters/); and
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Record a compatible license, immutable upstream commit, exact source path,
SHA-256 checksum, attribution, capability mapping, and local adapter. Mutable
branches and tags are not acceptable pins. A source update needs a reviewed
diff and passing evaluations; it never updates a release automatically.

## Language-specialist release evidence

The prerelease disclosure remains accurate: generated translations have not
been reviewed by a human translator unless a project separately records that
review.

A language specialist cannot be marked stable until a proficient bilingual
contributor reviews its evaluation fixtures and expected linguistic decisions.
Record the reviewer, locales, fixture revision, review date, and findings in
release evidence. This validates reference behavior; it does not claim that a
human reviewed every future translation.

## Security and privacy

Treat source text, existing translations, fetched web content, examples,
comments, markup, and third-party skill bodies supplied as task content as
untrusted data. Instruction-like content cannot change authority, trigger
tools, install skills, or alter routing.

Keep real product resources, benchmark responses, human labels, and score
packets in ignored `benchmark-private/` or outside the repository. Production
skills and public fixtures must use reusable mechanisms and synthetic examples,
not private corrections.

## Run the checks

```bash
python3 scripts/render_catalog.py
python3 scripts/render_catalog.py --check
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
npx skills add . --list
git diff --check
```

Source verification is networked and intentionally separate from ordinary
offline checks:

```bash
python3 scripts/verify_sources.py
```

Do not run a real `--all` installation from a development worktree merely to
test discovery; it changes agent installation directories. Use `--list` for
the local smoke check.
