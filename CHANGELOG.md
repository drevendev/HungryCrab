# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
