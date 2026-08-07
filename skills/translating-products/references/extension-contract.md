# External specialist extension contract

An external specialist is optional and must already be installed. It joins the
same manifest-derived capability catalog as bundled skills; a catalog record
never installs, downloads, enables, or grants authority to a skill.

## Required catalog metadata

An installed external specialist must provide the same record fields as a
bundled specialist: `name`, `version`, `category`, `description`,
`capabilities`, `depends_on`, `phases`, `specificity`, `required_context`,
`conflicts`, and `supersedes`. The catalog itself uses schema version `2`.
`capabilities` and `phases` are non-empty. `ownership` is optional as described
below. `selectors` is optional only to express universal applicability.

When present, `selectors` is a non-empty list of non-empty alternative matching
rules, and every axis has at least one value. An omitted field means universal
applicability; `selectors: []`, an empty selector object, `null`, and empty axis
values are malformed rather than wildcard spellings. Each populated axis
within one selector must match the task profile; values within an axis are
alternatives; multiple selectors are alternative routes into the skill. The
only selector axes are `languages`, `locales`, `scripts`, `surfaces`,
`platforms`, `formats`, `domains`, and `capabilities`. Locale values use
normalized BCP 47 ranges.

`phases` contains one or more of `inspect`, `translate`, `refine`,
`integrate`, and `review`. `specificity` declares the scope (universal,
writing-system, language, locale, surface, platform, format, domain, or
quality). `required_context` lists profile fields that must be resolved before
the skill runs. `conflicts` lists incompatible specialists. `supersedes` is
only for an explicit replacement of broader guidance. A skill that declares
`ownership`, when present, is a map from every declared capability to the
non-empty subset of declared phases it owns; when omitted, the router derives
ownership of every declared capability in every declared phase. A superseding
skill declares it when the default is not the intended scope. The router
applies replacement only to shared capability-and-phase slices. It removes the
broader module only when every one of those slices is covered; otherwise it
keeps the broader module and returns the scoped override plan. Unrelated
capabilities, phases, and dependencies remain active.

Relationship validation runs on the complete bundled-plus-external graph
before selector evaluation. Dependencies may cross execution phases because
they declare load and contract requirements, not an execution-phase edge.
Unknown and self targets, dependency cycles, supersedes cycles, mixed cycles,
and a target appearing in both `depends_on` and `supersedes` are invalid. A
superseding record must also share a category and owned capability-phase slice
with its target and have strictly narrower `specificity`.

Ignore an external candidate when any required metadata is missing, ambiguous,
invalid, or conflicts cannot be resolved deterministically.

## Authorization and catalog-only admission

Use an eligible external specialist only when one of these channels authorizes
it:

1. The user explicitly selects it.
2. `.translation/project-brief.md` lists it.
3. `compatibility-registry.json` contains a reviewed entry for it.

An authorized specialist must already be installed before routing. Supply its
schema-`2` catalog or manifest through the portable public CLI:

```bash
python3 route_capabilities.py REQUEST_JSON \
  --external-catalog INSTALLED_CATALOG_OR_MANIFEST.json \
  --installed-root PORTABLE_INSTALLED_SKILLS_ROOT \
  --compatibility-registry compatibility-registry.json
```

Repeat `--external-catalog` in the desired deterministic input order. The
corresponding installed root contains `<skill-name>/SKILL.md` with matching
portable `name` and `description` frontmatter plus a matching
`capability-manifest.json` identity record; the router rejects symlinked,
missing, ambiguous, or mismatched installations.

The
request authorizes names with `authorized_external_skills` for an explicit user
selection or `project_authorized_external_skills` for approved project context;
the registry is the third authorization channel. Bundled entries keep their
catalog order, followed by admitted external inputs in argument order. Duplicate
names, unauthorized entries, and invalid metadata fail deterministically before
routing. If an authorized specialist is unavailable, use compatible bundled
guidance. Never install a specialist at runtime. A public CLI test must prove
that a compatible third-party record is selected without modifying the router.

## Specialist authoring checklist

Every production external specialist documents its scope and non-scope,
context signals, reusable procedure, relevant principles, protected invariants
and authority boundaries, failure classes, and QA evidence or handoff. Examples
may illustrate principles but are non-exhaustive. Direct reuse of benchmark
prompts, expected answers, or disguised answer tables fails validation.

## Reviewed registry entries

Contributors add an entry only after review. Each entry contains exactly
`name`, `version_constraint`, `compatible_orchestrator_version`,
`capabilities`, `authority_scope`, `dependencies`, `conflicts`, `supersedes`,
`reviewer`, `evaluation_evidence`, and `review_date`.

`version_constraint` and `compatible_orchestrator_version` accept an exact
`MAJOR.MINOR.PATCH` version or one comparison using `==`, `>=`, `<=`, `>`, or
`<`, such as `>=1.0.0`. Compound ranges, wildcards, caret ranges, tilde ranges,
prereleases, and build metadata are unsupported and fail clearly.

`authority_scope` is an object containing exactly `phases`, `ownership`, and
`selectors`. `phases` binds every declared execution phase. `ownership` binds
every capability to its owned phases. `selectors` copies the admitted selector
list, or is `null` when the admitted record omits selectors for universal
applicability. This binds language, locale, script, surface, platform, format,
domain, and requested-capability scope without granting authority through a
broader review claim.

`reviewer` is a stable, referenceable HTTPS identity URI with a non-root path;
queries, fragments, embedded credentials, whitespace, and anonymous labels are
invalid. `review_date` is a canonical `YYYY-MM-DD` calendar date that cannot be
in the future. Both are immutable review claims because the evidence digest
below binds them.

`evaluation_evidence` contains exactly one lowercase `sha256:` digest. Compute
it from canonical UTF-8 JSON with sorted object keys, no ASCII escaping, and
`,`/`:` separators. The hashed object has `admitted_skill` set to the exact
installed record and `registry_claims` set to the complete reviewed entry with
`evaluation_evidence` omitted. The router recomputes this attestation, so an
arbitrary label, placeholder digest, changed record, reviewer, or date cannot
reuse an earlier review.

Reviewers verify metadata validity, installation identity, dependency and
conflict behavior, authority boundaries, representative evaluation output,
and host neutrality before accepting an entry. The attestation proves which
record and claims were reviewed; it does not replace that human review.
