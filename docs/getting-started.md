# Getting started

This guide covers installation, updates, verification, first use, and project
setup. Start requests with `translating-products`; it selects the required
specialists automatically.

## Install the complete suite

The complete suite is recommended because individual skills do not install
their dependencies transitively.

Install globally for all detected agents:

```bash
npx skills add BrunoMiguens/translating-products-skill --all --global --copy
```

Restart Claude Code, Codex, Cursor, or another agent host after installation.
The `--copy` option avoids relying on shared symlinks. Omit `--global` when the
skills should be installed only in the current project.

### Install for one host

```bash
npx skills add BrunoMiguens/translating-products-skill --skill '*' --agent claude-code
npx skills add BrunoMiguens/translating-products-skill --skill '*' --agent codex
npx skills add BrunoMiguens/translating-products-skill --skill '*' --agent cursor
npx skills add BrunoMiguens/translating-products-skill --skill '*' --agent universal
```

### Install one specialist

```bash
npx skills add BrunoMiguens/translating-products-skill \
  --skill translating-japanese --agent claude-code
```

This makes that specialist discoverable, but does not install its declared
dependencies. Install the complete suite when you want orchestration.

### Test a local checkout

From the repository root:

```bash
npx skills add . --list
npx skills add . --all
```

`--list` is the safe discovery check. A real `--all` install writes to agent
installation directories, so use it only when you intend to install the local
checkout.

## Update an existing installation

Update skills installed through the CLI:

```bash
npx skills update --global --yes
```

To replace the installed suite directly from this repository, run the complete
install command again:

```bash
npx skills add BrunoMiguens/translating-products-skill --all --global --copy
```

Then restart the agent applications. For a local development checkout, run the
same command with `.` instead of the GitHub repository slug.

## Verify discovery

Start a new agent session and ask:

```text
Which installed skills would you use to review Japanese translations in an
iOS String Catalog?
```

The answer should include `translating-products`, `translating-core`,
`reviewing-translations`, `localizing-software`, `translating-mobile`,
`translating-ios`, and `translating-japanese`. Exact routing can include another
relevant capability when the request supplies additional context.

## Make your first request

Use an ordinary product request. The orchestrator should infer the route:

```text
Review all Portuguese translations of the onboarding emails in this project
for Portugal. Keep placeholders and links unchanged. Report incorrect or
unnatural wording and provide corrected translations.
```

For explicit invocation:

```text
Use translating-products to translate these Android resources from English
into Polish for Poland. Match the tone of the existing product and preserve
all resource syntax.
```

For several locales:

```text
Translate this web checkout flow into French for Canada, Japanese for Japan,
and Arabic for Saudi Arabia. Reuse approved terminology and review every
locale before updating the files.
```

The agent may use sub-agents for independent locales or review when that
materially helps. Hosts without sub-agent support follow the same stages
sequentially.

## Approve project context

When no usable translation configuration exists, the orchestrator pauses and
helps establish:

```text
.translation/
├── project-brief.md
├── locales.yaml
├── glossary.csv
├── style-guide.md
├── protected-terms.txt
├── setup-approval.json
├── decisions.md
├── translation-memory.csv
└── research-sources.md
```

It asks one setup question at a time, shows the complete proposed context, and
waits for approval before translating. The approval binds the five required
context files by SHA-256. Changing any of those bytes requires approval again;
the optional decisions, memory, and research files can evolve without
invalidating the setup.

Useful inputs include:

- exact source and target locales;
- audience, purpose, brand voice, and register;
- approved terminology and protected product names;
- platform, file format, and structural constraints; and
- existing translations that are known to be reliable.

## What to expect

The route follows `inspect → translate → refine → integrate → review`.

- Structural values such as placeholders, ICU syntax, keys, markup, links,
  code, commands, and identifiers remain protected.
- Related strings are reviewed together when they share terminology,
  participant roles, or meaning.
- Existing translations and reviewer suggestions are treated as evidence, not
  automatically accepted as correct.
- Research runs only for a concrete unresolved current, market, or terminology
  question.
- The agent reports a missing essential capability instead of inventing one or
  downloading an unknown skill.

Translations remain AI-generated unless your own workflow records human
review.

## Next steps

- Read the [architecture](architecture.md) to understand routing and authority.
- Choose a workflow from the [benchmarking overview](benchmarking.md).
- See [Contributing](../CONTRIBUTING.md) to extend the suite.
