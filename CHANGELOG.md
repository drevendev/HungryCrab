# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `crab update`: one command that checks the CLI and every agent plugin against master, notices
  which agents are on this machine and whether the plugin is installed in each, and prints what
  to run. `--run` performs the plugin work; the CLI reinstall is only executed when the running
  process is not the uv tool install that would be replaced, because uv cannot replace the crab
  while it is running.
- Two nutrients the second live meal showed were missing. `hygiene.notice-file`: the prey keeps a
  NOTICE file for third-party attribution and the maw does not, which matters for a tool whose
  own verdicts say `notice_required`. `ai-config.skills-corpus`: the prey ships far more agent
  skills than the maw, so its corpus is worth reading even though the maw has skills of its
  own. Every other `ai-config` rule asks a yes/no question, so eating the official skills
  repository used to produce no `ai-config` candidate at all.
- The crab can file issues into a repository it does not own, and under its own name.
  `serve.token_env` in a maw's `.crab.yml` names an environment variable holding the token to
  serve with, so a GitHub App installation token gives the issues a bot's identity instead of a
  maintainer's; `crab serve --as issue` opens by saying which identity it is using. Creating a
  label needs write access where opening an issue does not, so a label the crab cannot create is
  now reported once and the issues are filed without it — deduplication reads the `crab:<id>`
  marker in the body, never the label.

### Changed

- **A digest describes one repository; a meal describes a pair.** `crab compare` used to write
  `menu.md`, `gap.md` and `compare.json` into the prey's digest, which every maw that eats that
  prey shares — so a second maw silently overwrote the first one's menu, and the licence verdict,
  which depends on the maw's own licence, was recorded as if it were a fact about the prey. The
  comparison now lands in `maws/<maw>/meals/<prey>@<sha>/` as `menu.md`, `menu.json`, `gap.md`
  and `meal.json`. `crab menu` takes `--maw`, because a menu belongs to one.
- `applicability` is `uptake`, the term nutrition already uses for the fraction of a nutrient
  that is actually absorbed.
- `docs/design/GLOSSARY.md`: every term in one place, with the words that were replaced and why.
  The vocabulary lived in seven documents, which is how "host" survived as long as it did.

- **The repository the crab feeds is the maw, not the host.** In parasitology a host is what
  gets eaten, which is the opposite of what this word had to mean here, and `--host` read like a
  network address besides. Prey feeds the maw. `--host` is now `--maw`, `--host-license` is
  `--maw-license`, `HostConfig` is `MawConfig`, `src/hungry_crab/host.py` is `maw.py`, and the
  cache keeps local digests under `maws/` instead of `hosts/`. Old `--host` is gone rather than
  deprecated: nothing is released yet, and two vocabularies cost more than one rename.
- **`appetite` in `.crab.yml` is now `hunger`**, with the same values. A file that still says
  `appetite` is a usage error rather than a silent default, because a maw quietly eating what it
  had switched off is worse than a failed command.
- The scheduled crab's first phase is `CRAVE`, not `HUNGER`, now that hunger is the name of a
  configuration block ([`docs/design/07-scheduled-crab.md`](docs/design/07-scheduled-crab.md)).
- README rewritten: what the crab is for, the vocabulary, install instructions for both Claude
  Code and Codex (both consume the same plugin marketplace), and the measured benchmark numbers.
  Installs track `master`; a release tag is opt-in.

### Fixed

- `crab update` compares the **commit** the CLI was installed from, not just its version. Every
  commit of a development series reports the same `0.3.0.dev0`, so version comparison told a crab
  seven commits and one whole rename behind that it was up to date. The installed commit comes
  from `direct_url.json`, which pip and uv both write for a VCS install.
- The plugin manifests carry the CLI's version, and a test keeps them from drifting again. They
  said `0.2.0` while the CLI said `0.3.0.dev0`, so every agent was told its plugin was current
  while its skills still spoke of `--host`.

Everything here was found by the first live meal, the crab eating `pypa/pipx` on its own
repository.

- `ignore` in `.crab.yml` was parsed and read by nothing, so the crab digesting itself reported
  three ecosystems, eslint and twelve test frameworks, all from `tests/fixtures`. Because it
  believed it already measured coverage, the coverage nutrient never appeared.
- A tool of a kind the maw already has is no longer a candidate: `ty` was ranked first on a maw
  running `mypy --strict`. Swapping one type checker for another is a decision, not a nutrient.
- The dependency diff now sees tools a maw configures with a file rather than a pinned
  dependency, and drops the library that merely implements a nutrient already on the menu
  (`pytest-cov` next to "Measure test coverage").
- Issue lessons are capped at three clusters and three popular issues, sorted by size, and titled
  after their largest issue instead of a bare list of TF-IDF terms. They were thirteen of
  twenty-four candidates, all scored the same.
- `crab compare` writes the resolved license verdict into the prey digest's `manifest.json`,
  which said `null` while `menu.md` said `COPY`.
- An issue for a nutrient the maw lacks entirely no longer reads "What this repository has: no".
- The `eat` skill gained a step: check that the maw column in `gap.md` describes this repository
  before judging anything against it, and guidance to switch a whole category off in `hunger`
  instead of rejecting its cards one by one.
- A test corpus is no longer counted as the repository's own code. `github-linguist/linguist` is
  3390 sample files in four hundred languages against 32 files of Ruby, and the crab read it as
  an Objective-C project with the ecosystems dotnet, go, python and rust, none of them Ruby:
  every manifest it found was a sample, and the menu offered ninety Python dependencies that
  were the contents of `samples/Pip Requirements/filenames/requirements.txt`. It now reads as
  Ruby, 123 files. A repository whose corpus really is its content keeps it.
- A security fix has to read like a fix. "Add support for Cloud Firestore Security Rules" and
  "Whitelist injectionSelector in grammars" gave linguist a security history and put the card at
  the top of the menu; a CVE identifier still speaks for itself.
- Agent frontmatter is read as YAML, not as one line: a skill whose `description: >` or
  `description: |-` spans several lines was recorded as the literal `>`. Four of the twenty
  skills in `anthropics/skills` were unreadable in the digest. A sequence value (`allowed-tools`
  written as a list) is folded into a comma-separated line.

### Documentation

- `docs/design/06-benchmark.md`: the specification of both benchmarks. B1 measures the menu
  deterministically and gates pull requests; B2 judges whole meals across crab versions, Claude
  models and a no-crab baseline, with blind two-pass judging by a different model family. States
  the hypotheses, the frozen setup, the metrics, the golden set, and the threats to validity.
- `docs/design/05-self-feeding.md`: the stage between 0.2 and 0.3. Milestone 0.2's exit criterion
  is only half met, because `/crab:eat` has never run in a live agent session. The document names
  what the maw is missing, lists twenty prey sniffed against an MIT maw with their license
  modes, gives an order to eat them in, and says what to watch in the skill.

## [0.2.0] - 2026-09-06

The first tagged release. It covers milestone 0.1 "Sniff & Digest" (the deterministic CLI and
its miners) and milestone 0.2 "Menu" (comparison, scoring, serving nutrients as issues, the
ledger, the Agent Skills and the Claude Code plugin).

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

[Unreleased]: https://github.com/drevendev/HungryCrab/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/drevendev/HungryCrab/releases/tag/v0.2.0
