# Hungry Crab

> Eat a foreign repository, digest it, and extract everything useful for your own repo
> without violating licenses.

[![CI](https://github.com/drevendev/HungryCrab/actions/workflows/ci.yml/badge.svg)](https://github.com/drevendev/HungryCrab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Conventional Commits](https://img.shields.io/badge/commits-conventional-fe5196.svg)](https://www.conventionalcommits.org)

**Status: pre-release, milestone 0.1 "Sniff & Digest".** The deterministic CLI works today.
The comparison with your own repository, the ranked menu of nutrients, the Agent Skills and the
Claude Code plugin arrive with 0.2 and 0.3 (see the [roadmap](docs/design/03-roadmap.md)).

## What it does

Hungry Crab takes a public GitHub repository (the *prey*), dissects it statically and produces a
token-budgeted `digest/` folder that an agent can read progressively:

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
| `traits` | a flat matrix of ~120 comparable traits derived from all of the above | `traits.json` |

`manifest.json` is the entry point: every file with a token estimate, the miners that ran, a
small summary and the suggested reading order. Markdown files stay within a budget (3,500 tokens
each by default, 30,000 for the whole digest); JSON files keep the full data for scripts.

Guiding principle: **scripts squeeze out everything that can be squeezed deterministically; the
model is spent only on judgment.** Nothing in the prey is ever executed, and everything in the
digest is treated as untrusted data.

## Quick start

Prerequisites: Python 3.11+, `git`, and `gh` (authenticated) for the GitHub API.

```bash
uv tool install "hungry-crab @ git+https://github.com/drevendev/HungryCrab"
# or: pip install git+https://github.com/drevendev/HungryCrab
```

```bash
crab sniff pallets/click          # license, size, languages, verdict: is it worth eating?
crab catch pallets/click          # clone into ~/.cache/hungry-crab (all branches)
crab digest pallets/click         # run the miners, write digest/, print the token budget
crab digest . --host-license MIT  # digest a local repository, e.g. the host itself
```

`crab digest owner/repo` catches the prey first when it is not cached yet. Giants can be caught
with `--since 2y` (history newer than two years, all branches) or `--shallow` (default branch,
tree only). Digests are addressed by commit SHA: digesting the same commit twice is free.

Example output for `crab digest pallets/click`:

```text
file                  tokens     bytes  miner
ci.md                    709      2479  ci
history.md              1564      5471  history
inventory.md             693      2425  inventory
...
markdown tokens: 4167 of 30000 (ok); all files: 21109
miners: 10 ok, 0 failed; 0.75 s
```

## License engine

Licenses are decided by a deterministic `host license x prey license` matrix, never by a
model's opinion. Every nutrient gets one of five modes:

| Mode | What is allowed |
|---|---|
| `COPY` | copy code and configs, keep the copyright notice, record it in `THIRD_PARTY_NOTICES.md` |
| `COPY_FILE` | copy whole files; each keeps its own license (MPL-2.0 style) |
| `REIMPLEMENT` | use as a specification: clean-room rewrite, no verbatim code |
| `IDEAS_ONLY` | ideas, architecture, approaches and facts only |
| `HUMAN` | the engine is unsure; a human decides |

Unknown, missing and source-available licenses always end up as `IDEAS_ONLY` with a human flag.
This is a compliance aid, not legal advice.

## Safety

- **Prey code is never executed.** The miners read files and run read-only git plumbing. No
  `npm install`, `pytest` or `make` ever runs inside the cache.
- **Prey content is untrusted data.** Markdown summaries carry structure (headings, names,
  counts), never the body of README or agent-instruction files, and instruction-like fragments
  are flagged in the JSON side.
- **Least privilege.** `sniff` needs only read access to the GitHub API (`gh` or a token).

## Development

```bash
uv sync                      # environment with the dev group
uv run pytest                # tests build three synthetic git repositories as fixtures
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run crab digest . --out /tmp/self-digest --host-license MIT   # the crab eats itself
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) (the guide coding agents read).

## Design

The design documents live in [docs/design](docs/design/README.md):

- [Concept and skill form factor](docs/design/01-concept-and-skill.md): the three layers, the
  digest format, the license engine, security, the decisions log.
- [MVP](docs/design/02-mvp.md): commands, miners, milestones 0.1 to 0.3, acceptance criteria.
- [Roadmap](docs/design/03-roadmap.md): base crab, evolving crab, forks.
- [Evolving Hungry Crab](docs/design/04-evolving-crab.md): the self-improving loop.

## License

[MIT](LICENSE). The tool helps with license compliance; it is not legal advice.
