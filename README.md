# Hungry Crab

```text
                              o    .
                           .   o   .   O
     /\                                                     /\
    /  \       _____________________________________       /  \
   |    |     /                                     \     |    |
   | /\ |     |     \\\\\\\             ///////     |     | /\ |
   | \/ |     |      (@@@)               (@@@)      |     | \/ |
    \  /      |                                     |      \  /
     \ \      |      /\/\/\/\/\/\/\/\/\/\/\/\/      |      / /
      \ \_____|                                     |_____/ /
       \______\_____________________________________/______/
                  /     /      |   |      \     \
                 /     /       |   |       \     \
```

> **Eat a foreign repository. Digest it. Serve what is worth keeping, legally.**

[![CI](https://github.com/drevendev/HungryCrab/actions/workflows/ci.yml/badge.svg)](https://github.com/drevendev/HungryCrab/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/drevendev/HungryCrab?color=blue)](https://github.com/drevendev/HungryCrab/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Conventional Commits](https://img.shields.io/badge/commits-conventional-fe5196.svg)](https://www.conventionalcommits.org)

Somewhere out there is a repository that fixed your flaky CI two years ago, wrote the `AGENTS.md`
you keep meaning to write, and learned the hard way which file breaks every single time anyone
touches it. Reading it properly costs you an afternoon. Reading fifty of them costs you a month.

The crab reads them for you. It drags the prey into a local cache, dissects it with twelve
deterministic miners, and boils a whole repository down to a digest small enough for an agent to
actually read. Then it holds that digest against *your* repository and serves a ranked menu:
what they have, what you lack, what it would cost you, and exactly what their license lets you
take. Approve a few and they land as issues in your tracker, each with evidence links and a
provenance footer. Say no to one and the crab remembers, so it never offers it again.

The prey is never executed. Not one line of its text reaches your issues unless the license says
it may.

**Status: [0.2.0 "Menu"](https://github.com/drevendev/HungryCrab/releases/latest), the first
release.** Pull-request serving and the clean-room protocol arrive with 0.3 (see the
[roadmap](docs/design/03-roadmap.md)).

## The metaphor, in five words

| Term | Meaning |
|---|---|
| **prey** | the foreign repository being eaten |
| **host** | your repository, the one we eat for |
| **nutrient** | one transferable thing, with a license mode and provenance |
| **menu** | the ranked list of nutrients after prey and host are compared |
| **ledger** | what was eaten, what you accepted, what you turned down |

## Install

Prerequisites: Python 3.11+, `git`, and `gh` authenticated for the GitHub API.

**The CLI**, which every agent then calls:

```bash
uv tool install "hungry-crab @ git+https://github.com/drevendev/HungryCrab"
```

That tracks `master`, which is always green. If you want a specific release instead, append
`@v0.2.0` to the URL and take care of updates yourself.

**Claude Code**, which adds `/crab:eat`, `/crab:sniff` and `/crab:menu`:

```bash
claude plugin marketplace add drevendev/HungryCrab
```

```bash
claude plugin install crab@hungry-crab
```

**Codex**, same plugin, same marketplace:

```bash
codex plugin marketplace add drevendev/HungryCrab
```

```bash
codex plugin add crab@hungry-crab
```

Restart the agent session afterwards so it picks the plugin up. Cursor and anything else that
reads the open `SKILL.md` format can use the `skills/` folder of this repository directly; the
only hard requirement is `crab` on `PATH`.

To see what is out of date across the CLI and every agent you have, ask the crab:

```bash
crab update
```

It reports the installed and available versions, notices which agents are on this machine and
whether the plugin is installed in each, and prints exactly what to run. `crab update --run`
does the plugin work for you.

## Feed the crab

With an agent, one line does the whole protocol: judge the menu, ask you, create the issues.

```text
/crab:eat pallets/click
```

By hand, the same protocol is six commands:

```bash
crab sniff pallets/click --host .                  # license, size, activity: is it worth eating?
```

```bash
crab init                                          # .crab.yml: appetite, serve policy, ledger mode
```

```bash
crab compare pallets/click --host . --issues 300   # digest both sides, diff, score -> gap.md, menu.md
```

```bash
crab menu pallets/click --top 15 --category ci     # the ranked menu of nutrients
```

```bash
crab serve pallets/click --host . --ids crab:ci:ci.cache --as dry-run   # preview the issue
```

```bash
crab ledger mark crab:tooling:tooling.renovate rejected --reason "dependabot is enough"
```

`crab compare` catches and digests the prey the first time; digests are addressed by commit SHA,
so eating the same commit twice is free. Giants take `--since 2y` (history newer than two years)
or `--shallow` (default branch, tree only).

## What the miners extract

`crab digest owner/repo` writes a token-budgeted `digest/` folder that an agent reads
progressively:

| Miner | What it extracts | Output |
|---|---|---|
| `inventory` | tree, languages, LOC, manifests, lock files, entry points, vendored and generated content | `inventory.{json,md}` |
| `license` | SPDX from license files, manifests, headers and the API; per-file exceptions; the verdict against the host license | `license.json` |
| `deps` | normalized dependencies per ecosystem (npm, Python, .NET, Rust, Go), lock-file and pinning policy | `deps.json` |
| `ci` | GitHub Actions workflows: triggers, jobs, matrix, cache, permissions, concurrency, SHA pinning, tools, secrets (names only), dependabot/renovate, other CI systems | `ci.{json,md}` |
| `testing` | frameworks, layout, test/source ratio, coverage threshold, e2e/property/snapshot/fuzz/benchmarks | `tests.{json,md}` |
| `docs` | README outline and sections, community files, changelog format, ADRs, docs site, issue/PR templates | `docs.{json,md}` |
| `ai_config` | CLAUDE.md, AGENTS.md, cursor rules, Copilot instructions, skills, subagents, hooks, MCP servers | `ai.{json,md}` |
| `history` | hotspots, fix ratio, reverts, co-change coupling, cadence, bus factor, tags and release cadence, conventional-commit discipline | `history.{json,md}` |
| `branches` | every branch: ahead/behind, freshness, merged or stale, what the unmerged ones are about | `branches.{json,md}` |
| `issues` | label and time-to-close statistics, the most reacted-to issues, TF-IDF clusters of recurring themes (after `catch --issues N`) | `issues.{json,md}` |
| `architecture` | regex symbol index (TypeScript, Python, C#), internal import graph with hubs and directory layering, public surface | `architecture.{json,md}` |
| `traits` | a flat matrix of ~120 comparable traits derived from all of the above | `traits.json` |

`manifest.json` is the entry point: every file with a token estimate, the miners that ran, a
small summary and the suggested reading order. Markdown stays within a budget (3,500 tokens per
file by default, 30,000 for the whole digest); JSON keeps the full data for scripts.

Guiding principle: **scripts squeeze out everything that can be squeezed deterministically; the
model is spent only on judgment.**

## From digest to issues

- **compare** turns the two digests into candidate nutrients: trait rules (ci, security, tooling,
  hygiene, docs, ai-config, tests), tool and dependency diffs within shared ecosystems, README
  sections, commit and changelog discipline, history and issue lessons, architecture raw
  material. Every candidate carries a stable id, evidence linked to the prey commit, effort, risk
  and a license mode.
- **scoring** is a formula you can read: category weight x value x applicability x license mode x
  effort, minus risk. Weights live in `data/scoring.yml` and are overridable per host.
- **serve** creates issues with a hidden `crab:<id>` marker, a label and a provenance footer, so a
  rerun creates no duplicates. The `why` and `how` come from the agent through a notes file.
- **ledger** (`.crab/ledger.json` by default) remembers every meal and decision; rejected and
  served nutrients vanish from later menus. **`crab tune`** reads it and tells you which weights to
  move, which categories to switch off, and which prey were a waste of time.

## Licenses are decided by an engine, not an opinion

A deterministic `host license x prey license` matrix gives every nutrient one mode:

| Mode | What it allows |
|---|---|
| `COPY` | copy code and configs, keep the notice, record it in `THIRD_PARTY_NOTICES.md` |
| `COPY_FILE` | copy whole files; each keeps its own license (MPL-2.0 style) |
| `REIMPLEMENT` | use as a specification: clean-room rewrite, no verbatim code |
| `IDEAS_ONLY` | ideas, architecture, approaches and facts only |
| `HUMAN` | the engine is unsure; a human decides |

Unknown, missing and source-available licenses always end up as `IDEAS_ONLY` with a human flag.
Issue and discussion text is always `IDEAS_ONLY`, because the copyright belongs to the
commenters. This is a compliance aid, not legal advice.

## Safety

- **Prey code is never executed.** The miners read files and run read-only git plumbing. No
  `npm install`, `pytest` or `make` ever runs inside the cache.
- **Prey content is untrusted data.** Markdown summaries carry structure (headings, names,
  counts), never the body of README or agent-instruction files, and instruction-like fragments
  are flagged in the JSON.
- **Least privilege.** `sniff` and `catch` need read access to the GitHub API; `serve` uses your
  own authenticated `gh` and creates nothing until you ask it to.

## Benchmarks

```bash
uv run python benchmarks/run.py pallets/click colinhacks/zod
```

| Prey | Digest time | Markdown tokens |
|---|---|---|
| pallets/click, 3.4k commits | 1.0 s | 5,323 |
| colinhacks/zod, 513 code files | 4.8 s | 10,873 |

The milestone limits are 120 seconds and 30,000 Markdown tokens per digest. Results land in
`benchmarks/results/`; see [benchmarks/README.md](benchmarks/README.md).

## Development

```bash
uv sync                      # environment with the dev group
```

```bash
uv run pytest                # tests build three synthetic git repositories as fixtures
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

```bash
uv run crab digest . --out digest-out --host-license MIT   # the crab eats itself
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md), the guide coding agents read.

## Design

The design documents live in [docs/design](docs/design/README.md):

- [Concept and skill form factor](docs/design/01-concept-and-skill.md): the three layers, the
  digest format, the license engine, security, the decisions log.
- [MVP](docs/design/02-mvp.md): commands, miners, milestones 0.1 to 0.3, acceptance criteria.
- [Roadmap](docs/design/03-roadmap.md): base crab, evolving crab, forks.
- [Evolving Hungry Crab](docs/design/04-evolving-crab.md): the self-improving loop.

## License

[MIT](LICENSE). The tool helps with license compliance; it is not legal advice.
