# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (milestone 0.2 "Menu")

- `crab compare`: prey digest minus host digest, turned into scored candidate nutrients with
  stable ids, evidence links, effort, risk and a license mode; writes `gap.md`, `menu.md` and
  `menu.json` into the prey digest. `crab menu` prints the ranked menu.
- Scoring weights in `data/scoring.yml`, overridable per host; `.crab.yml` (`crab init`) with
  appetite, serve policy, ledger mode and scoring overrides.
- The ledger (`crab ledger show|mark`): every meal and decision, in the host, the cache or
  nowhere; rejected and served nutrients disappear from later menus.
- `crab serve`: issues with a hidden `crab:<id>` marker, a label and a provenance footer,
  created through `gh` after a dry run; model-written notes per nutrient.
- `crab tune`: weight suggestions from the ledger per category and trait, appetite switch-offs,
  poor-match prey; `--write` applies them.
- `crab catch --issues N` and the `issues` miner (statistics, top by reactions, TF-IDF clusters);
  the `architecture` miner (symbol index, import graph, hubs, layering, public surface).
- Agent Skills `eat`, `license` and `serve`, the `crab-historian` and `crab-architect`
  subagents, the `/crab:sniff` and `/crab:menu` commands, and the Claude Code plugin manifest
  with its own marketplace.
- `benchmarks/run.py`: digest time and token budget per reference prey, with the first results;
  the whole loop was exercised end to end on a private sandbox host (issues created, zero
  duplicates on rerun, decisions recorded, `crab tune` consulted).

### Added (milestone 0.1 "Sniff & Digest")

- `crab sniff`: API-only reconnaissance with a license class, a verdict and warnings for
  archived, forked, stale and giant repositories.
- `crab catch`: clone or refresh the prey into the cache; `--shallow` and `--since` for giants.
- `crab digest`: ten deterministic miners (inventory, license, deps, ci, testing, docs,
  ai_config, history, branches, traits) writing a token-budgeted `digest/` with `manifest.json`;
  digests are addressed by commit SHA and served from the cache on repeat.
- `crab cache`: inspect and clean the cache.
- License engine: SPDX detection from license files, manifests, file headers and the GitHub API;
  the deterministic host x prey verdict matrix with the modes `COPY`, `COPY_FILE`,
  `REIMPLEMENT`, `IDEAS_ONLY` and `HUMAN`.
- Prompt-injection hygiene: summaries carry structure only; instruction-like fragments are
  flagged.
- Three synthetic fixture repositories (npm, pyproject, csproj) built into real git repositories
  by the tests.
- CI on Ubuntu and Windows with Python 3.11 and 3.14: ruff, mypy, pytest and a self-digest
  smoke test.
