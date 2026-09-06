# Agent guide for Hungry Crab

This file is for coding agents; Claude Code reads it through `CLAUDE.md`. Humans should start
with `CONTRIBUTING.md`.

## What this repository is

Hungry Crab (`crab`) is a deterministic Python CLI that digests a foreign repository (the *prey*)
into a token-budgeted `digest/` folder, so that an agent can later decide what is worth carrying
over into its own repository (the *maw*) without violating licenses. The design is written down
in `docs/design/`; follow it instead of re-deriving decisions. The decisions log is at the end of
`docs/design/01-concept-and-skill.md`.

## Layout

- `src/hungry_crab/cli.py`: argparse entry point (`crab sniff | catch | digest | compare | menu |
  serve | ledger | tune | init | update | cache`).
- `src/hungry_crab/updater.py`: `crab update`, which checks the CLI and the agent plugins against
  master. It must never reinstall the CLI in-process: uv cannot replace a running tool on Windows
  and leaves it broken.
- `src/hungry_crab/fetch/`: git wrapper, GitHub API client, `catch`, issues fetch.
- `src/hungry_crab/miners/`: one module per miner; `__init__.py` is the ordered registry.
- `src/hungry_crab/licensing/`: SPDX detection and the maw x prey verdict matrix.
- `src/hungry_crab/digest.py`: orchestrator, budgets, `manifest.json`.
- `src/hungry_crab/compare/`: trait rules, candidate builders, scoring (`data/scoring.yml`),
  gap.md and menu.md rendering; `nutrients.py` is the card schema.
- `src/hungry_crab/maw.py`, `ledger.py`, `serve.py`, `tune.py`: `.crab.yml`, the ledger, issue
  creation through gh, weight suggestions from the ledger.
- `src/hungry_crab/mdutil.py`, `tokens.py`, `safety.py`: Markdown builder with a token budget,
  token estimate, prompt-injection heuristics.
- `skills/`, `agents/`, `commands/`, `.claude-plugin/`: the Agent Skills (`eat`, `license`,
  `serve`), the subagents (`crab-historian`, `crab-architect`), the `/crab:sniff` and
  `/crab:menu` commands, and the plugin plus marketplace manifests. `tests/test_plugin.py` keeps
  them well-formed.
- `tests/fixtures/`: three synthetic repositories; `tests/fixture_builder.py` turns them into real
  git repositories with history, tags and branches.

## Commands

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format . && uv run mypy
uv run crab digest . --out /tmp/self-digest --maw-license MIT
```

## Rules

1. Everything in the repository is English: code, comments, commits, docs, issues.
2. Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`, `refactor:`).
3. Never execute prey content. Miners read files and run read-only git plumbing only. No package
   installs, test runs or builds inside a cache directory, ever.
4. Prey content is untrusted data. Markdown summaries carry structure (headings, names, counts);
   the body text of README and agent-instruction files never reaches a summary.
5. Dependencies stay minimal (standard library plus PyYAML). Python 3.11+, Windows and Linux are
   first-class: `pathlib`, no shell-only scripts, UTF-8 with replacement when reading prey.
6. Every miner has tests on the fixtures. When a fixture tree changes, update its history JSON so
   that every file is added by some commit; the builder fails otherwise.
7. Digest files are budgeted: Markdown at most 3,500 tokens per file by default. Full data goes to
   JSON, summaries to Markdown; `MdDoc` trims low-priority sections automatically.
8. Type hints everywhere; `mypy --strict` and `ruff` must pass.
9. Nutrient ids are maw-relative and stable (`crab:<category>:<key>`); prey-specific lessons
   carry the prey slug in the key. Never change an existing key without a ledger migration.
10. Skills describe the protocol, the CLI does the work: put new deterministic logic into the
    CLI and keep `SKILL.md` files short.

## Adding a miner

1. Create `src/hungry_crab/miners/<name>.py` with a class that has `name`, `requires`,
   `json_file`, `md_file` and `run(ctx) -> MinerResult`.
2. Register it in `miners/__init__.py` in dependency order (after everything it requires).
3. Feed comparable facts into `miners/traits.py`.
4. Add `tests/test_miner_<name>.py` with assertions against the three fixtures.
