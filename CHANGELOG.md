# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything below is on `master` and in no tag. The 0.2.1 and 0.2.2 milestones are complete —
self-feeding, the licence resolutions, the menu benchmark — but neither was ever released, so
their entries wait here for the next tag rather than claiming one of their own. Milestones are
tracked in [docs/design/03-roadmap.md](docs/design/03-roadmap.md); this file tracks releases.

### Added

- **`crab serve --as pr-branch`: a REIMPLEMENT nutrient becomes a pull request, through a
  transaction that scans before it touches anything.** The clean-room implementer returns a
  strict JSON receipt naming the exact maw paths it changed; the crab freezes those bytes,
  scans every file plus the title and body for secrets, reconciles marker-bearing pull
  requests already on GitHub, and only then stages the frozen bytes in a detached temporary
  worktree, pushes the deterministic `crab/<nutrient>-<hash>` branch and opens the pull
  request with the same `<!-- crab:<id> -->` marker issues carry. `serve.prs` (`ask` requires
  explicit `--ids`, `auto` allows `--top`) and `serve.max_prs_per_run` in `.crab.yml` govern
  it, and a rerun after a crash reconciles instead of duplicating. COPY nutrients are refused
  until `crab attribution` exists ([#69](https://github.com/drevendev/HungryCrab/issues/69),
  [#77](https://github.com/drevendev/HungryCrab/issues/77), pull requests #101–#112).
- **The clean-room protocol, and the hooks that make "never execute prey" mechanical.** The
  `cleanroom` skill writes a code-free specification from the prey evidence; the
  `crab-cleanroom-impl` subagent implements from it in a fresh context and returns the
  receipt. Two `PreToolUse` hooks ship with the plugin: `crab-cleanroom-guard` refuses that
  agent every tool call that names the cache, and `crab-prey-guard` refuses any Bash command
  that touches the cache unless it is one of a few audited read-only shapes, judging the
  executable's provenance rather than its name
  ([#71](https://github.com/drevendev/HungryCrab/issues/71),
  [#72](https://github.com/drevendev/HungryCrab/issues/72), #105, #119).
- **Documents are paged, and the whole digest has a budget policy.** A Markdown file that
  does not fit its per-file budget is split into `history.md`, `history.2.md`, … each naming
  the next, and nothing is dropped. The 30,000-token total is a policy: `warn` (default)
  records how far over the digest went, `enforce` drops whole pages by priority and records
  which, `off` has no ceiling — set as `budget.policy` in `.crab.yml` and read from an
  explicit `--maw` ([#75](https://github.com/drevendev/HungryCrab/issues/75), #120–#122,
  [#129](https://github.com/drevendev/HungryCrab/issues/129)).
- **A digest says what it did not read, and `compare` refuses partial evidence.**
  `crab digest --fail-on-miner-error` exits non-zero when a miner failed, so the CI smoke
  test can fail; a miner blocked by a failed dependency is recorded as `blocked` rather than
  failed; the working tree is part of the cache key, so an edited checkout is never served
  the digest of its last commit; and `crab compare` refuses a digest with a failed miner or a
  damaged artifact unless `--allow-partial` says otherwise
  ([#61](https://github.com/drevendev/HungryCrab/issues/61),
  [#65](https://github.com/drevendev/HungryCrab/issues/65),
  [#67](https://github.com/drevendev/HungryCrab/issues/67), #80, #113, #114).
- **Codex now gets native Hungry Crab branding without borrowing Claude's manifest as its identity.**
  A portable Agent Plugins v1 `plugin.json` carries the repository and version, a Codex overlay
  supplies the red crab presentation and website, and `.agents/plugins/marketplace.json` installs
  the repository root without duplicating the existing skills. The temporary OpenMoji crab keeps
  its adjacent CC BY-SA 4.0 attribution. `crab update` now reads Claude and Codex manifest versions
  separately, so one drifting manifest cannot make the other agent look current
  ([#99](https://github.com/drevendev/HungryCrab/issues/99)).
- **A declared nutrient category is produced by something or deferred by name.** `code` sat in
  `CATEGORIES`, in `scoring.yml` and in every generated `.crab.yml` as a hunger knob while no
  candidate builder could emit it, so the setting could not affect a single meal — and the
  decision to leave it to 0.4 lived in a private backlog, where a deferred category looks
  exactly like a forgotten one. `DEFERRED_CATEGORIES` in `nutrients.py` names the milestone
  that owes each producer, `tests/test_categories.py` refuses a category that is neither
  produced nor listed, and the roadmap, the generated config and the category reference say
  that `code` is inert until 0.4 ([#52](https://github.com/drevendev/HungryCrab/issues/52)).
- **Issue forms, a pull request template and `CODEOWNERS`.** The bug form asks for the three
  things a report never includes — the crab version, the exact command and `manifest.json`;
  the pull request template carries the summary, the test plan and the changelog reminder every
  merged pull request has had so far; `CODEOWNERS` names the files an autonomous phase must not
  change on its own. Making the owner's review mandatory is a branch-protection switch, not a
  file ([#15](https://github.com/drevendev/HungryCrab/issues/15),
  [#20](https://github.com/drevendev/HungryCrab/issues/20),
  [#26](https://github.com/drevendev/HungryCrab/issues/26)).

- **B1, the menu benchmark, and with it milestone 0.2.2.** `benchmarks/menu_benchmark.py` asks
  one question with no model in the room: with today's rules and weights, would the menu still
  put the nutrients a human accepted in the top 30, and would it still show the ones they
  rejected? The golden set is not invented — it is the maintainer's own verdicts on `pypa/pipx`,
  `anthropics/skills` and `github-linguist/linguist`, lifted from the ledger with the rejection
  reasons kept, against frozen digests of those three prey and of this repository at `v0.2.0`.
  Measured at introduction: `recall_must@30` = 1.00 (10/10), `noise@30` = 0.66 (19/29). Both
  gate pull requests through `tests/test_menu_benchmark.py`, on every platform in the matrix.

- **A license governs strangers, and the maw's owner is not one.** `.crab.yml` grows a `trust`
  block: `same_owner` (on by default) treats a prey owned by the account behind the maw's
  `origin` as the maw's own code, `owners` extends that to named accounts, and `bypass_license`
  is an explicit escape hatch. The verdict matrix takes a `Relationship` alongside the two
  licenses; `own` yields `COPY`, and still asks for review when the prey carries a copyleft or
  source-available license, because owning a repository lets its owner relicense what they wrote
  and not what they received. Before this, a maintainer eating their own unlicensed repository
  got `IDEAS_ONLY` — a correct answer to a question nobody had asked.

- [`docs/design/08-budgets-and-feeder.md`](docs/design/08-budgets-and-feeder.md) and a revised
  roadmap. Two policies that decide who the crab is for. **Budgets**: the 30 000-token ceiling was
  a boolean flag pretending to be a limit, and the per-file budget bought its 3 500 tokens by
  dropping the tail of a section. It becomes a policy — `warn` for an agent session, `enforce` for
  a budgeted loop, `off` for a human — and documents that do not fit are paged rather than
  truncated. **The Feeder**: the deterministic pipeline becomes a reusable GitHub workflow, so a
  repository can name its prey, run `catch → digest → compare` in CI with no model anywhere, and
  collect the meal as a build artifact. It moves from milestone 0.6 to 0.3.1, because the
  Evolving Crab's CONSUME phase is that job and building it later means building it twice.
  Being able to digest any repository is now a stated goal of track A rather than an implication
  of it.

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
- `menu.md` calls its `serve_as` column `Serve as`, as the glossary says, not `Artifact`.
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

- **A selective run is not a complete digest, and enforcement drops pages from the tail.**
  `crab digest <prey> --miners license` writes into the same `digests/<sha>` entry as a full
  run and cleans the other miners' files out of it; the next `crab compare` then reused that
  entry as complete evidence — no failed miner, no integrity error, an empty stack and a menu
  with nothing on it until someone passed `--force`. Reuse now requires a record for every
  registered miner. In the same area: under `enforce`, pages of equal priority were dropped by
  file name, and `docs.md` sorts after `docs.3.md`, so the page a reader opens first went
  before its continuations; the last page of a family goes first now. And a `--md-budget` too
  small for a page header — `0`, or `20` — raised a `ValueError` after the miners had run,
  with JSON files written and no manifest; it is a usage error with a hint before anything
  runs, or a named miner's error if a header still does not fit.
- **The prey guard reads a line break as the command separator it is.** `shlex` reads a
  newline as whitespace, so `cat <cache>/README.md` on one line and `python <cache>/setup.py`
  on the next were judged as one long `cat` and allowed; a cache-touching command with a line
  break is now refused like one with `;`. In the same pass: `git log --output=<file>` was an
  allowed write primitive that lands prey bytes wherever a later command runs them, and
  `git grep -O` hands the matches to a program of the caller's choosing — both refused; a
  sibling directory that merely starts with the cache's name (`hungry-crab-other`,
  `crab-prey.old`) is no longer read as the cache; the read-only verbs the `crab-historian`
  agent is told to run in a clone (`show`, `diff`, `blame`, `shortlog`, `describe`,
  `show-ref`, `grep`) are allowed under the same dangerous-argument filter; and both hook
  entry points take undecodable input down the documented transport-failure path instead of
  dying with a traceback, which exited 1 and was read as "allow" too, only louder.
- **`crab digest --out` deleted files it had not written.** Rerun cleanup treated every file
  in the output directory as a stale artifact unless the current run had just produced it, so
  a directory the caller already used lost its own files, and `--out .` emptied the top level
  of the repository being digested. A digest now owns exactly what a registered miner
  declares — its JSON file and its Markdown page family — and cleanup, the aggregate budget
  and `manifest.json` all stop at that boundary: a stranger's `notes.md` is neither deleted,
  nor budgeted, nor listed as evidence
  ([#126](https://github.com/drevendev/HungryCrab/issues/126)).
- **A local prey's `.crab.yml` no longer decides what the crab reads.** `crab digest <path>`
  loaded the target's own `.crab.yml` for `ignore` whenever the caller passed none, so a
  directory being eaten could hide any part of its tree from every miner, and a broken
  maw-only setting in that file (`mode`, `hunger`, `budget.policy`, a YAML error) aborted the
  digest before a miner ran — through `crab compare`, `crab menu` and `crab serve` on a local
  prey as well. Configuration now comes from an explicit `--maw` only, and its `ignore` list
  applies when the target is that maw: `crab digest . --maw .` is how the crab eats itself
  (the README, `AGENTS.md` and the CI smoke test say so), `crab compare` is unchanged because
  it always knew which side was the maw, and a foreign local directory keeps its whole tree
  ([#128](https://github.com/drevendev/HungryCrab/issues/128)).
- **Prey-cache shell guards now fail closed on executable provenance, not just command names.**
  A cache-touching command is denied when its executable path points into prey even if the file
  is named like an allowed reader, and leading environment assignments are rejected so `PATH`
  or loader variables cannot turn an allowed `cat`/`rg`/`git` shape into prey execution. Ordinary
  read-only cache inspection stays allowed ([#85](https://github.com/drevendev/HungryCrab/issues/85)).
- **Served issues now show the content-origin licence ceiling that produced their mode.**
  `crab serve` renders the normalized content origin, final licence mode and deterministic cap
  reason when one applies, so the same trace recorded in `menu.json` remains visible at the
  publication boundary ([#58](https://github.com/drevendev/HungryCrab/issues/58)).
- **Issue-derived nutrients no longer inherit permissive licence modes from commenter-controlled prose.**
  `issue-lesson` cards are treated as commenter-origin and capped at `IDEAS_ONLY`; unknown or
  future content origins fail closed to `HUMAN`, while the structured trace records the
  normalized origin and the reason for the cap ([#84](https://github.com/drevendev/HungryCrab/issues/84)).
- **A healthy digest no longer trusts producer metadata after its artifact disappears or changes.**
  `crab digest` now invalidates and repairs a cached digest when a successful miner's declared
  artifact is missing, corrupt, mis-owned, the wrong size, or not a JSON object where JSON is
  expected; `crab compare` refuses that inconsistent evidence by default unless the caller
  explicitly opts into `--allow-partial` ([#87](https://github.com/drevendev/HungryCrab/issues/87)).
- **Ignore globs matched by case on Windows and not on Linux.** `is_ignored` ran the path and
  the pattern through `fnmatch.fnmatch`, which lowercases both on Windows and neither on Linux,
  so one `.crab.yml` produced two different digests of one commit — file counts, languages,
  ecosystems and the menu — and each was consistent with itself, so nothing warned. The match
  is `fnmatchcase` on every platform now, the generated config says so, and `Tests/Fixtures`
  against `tests/fixtures/**` is a regression case on both legs of CI
  ([#89](https://github.com/drevendev/HungryCrab/issues/89)).
- **`crab update` called an install with no recorded commit up to date.** The commit comparison
  only ran when PEP 610 provenance was there; without it the CLI came back `OK`, and `OK` is
  not actionable, so the line "reinstall to be sure" was followed by "Nothing to do." A crab at
  master's version string with no commit to compare is `unknown` now: the reinstall command is
  printed for a running uv tool, and executed under `--run` where the crab is not the process
  being replaced ([#97](https://github.com/drevendev/HungryCrab/issues/97)).
- **The hidden-character check flagged every emoji sequence and every Persian word.** U+200D
  glues profession and family emoji together and U+200C is ordinary orthography in Persian and
  the Indic scripts, and both were flagged on sight, so a README heading with 👩‍💻 in it became
  `[line omitted: instruction-like content]`. A joiner is now judged by its neighbours: it is
  legitimate next to a character that is not ASCII and not itself invisible, and suspicious
  wedged into a Latin word, dangling at the end of a heading, or stacked with other invisible
  characters. The zero-width space, the word joiner, the BOM and the Tags block are flagged as
  before ([#66](https://github.com/drevendev/HungryCrab/issues/66)).
- **A repository that measured coverage in CI read as one that did not.** `coverage.configured`
  came from a threshold in a file, a coverage package in a manifest or a config file at the
  root — and a Go or Rust project declares coverage in none of those: the flag is on the `go
  test` line and the upload is an action, both of which the CI miner had already written down.
  The testing miner now requires `ci`; a coverage upload action names the service, a coverage
  flag on a test step counts as configured, and `coverage.in_ci` says which workflow said so.
  The threshold stays a fact declared in a file
  ([#53](https://github.com/drevendev/HungryCrab/issues/53)). The example-tree leak reported
  in [#54](https://github.com/drevendev/HungryCrab/issues/54) turned out not to exist — the
  exclusion reaches the test frameworks through `FileInfo.counted` — and is pinned by a
  regression case instead.
- **The ledger forgot which prey proposed a nutrient, and `crab tune` read that field.**
  Nutrient ids are maw-relative, so every re-proposal overwrote `prey` with the latest prey and
  the one that found the nutrient lost the credit. `prey` and `sha` now name the first
  proposer for the life of the entry; `last_prey`, `last_sha` and `sightings` record the rest.
  A ledger written before this loads unchanged
  ([#60](https://github.com/drevendev/HungryCrab/issues/60)).
- **Issue dedup stopped at 500 issues, and a quote outranked the issue it quoted.** Marker
  discovery asked `gh issue list` for one page of 500, so on a busy repository the crab's own
  issues fell off the end and the next serve filed everything again — on exactly the repository
  the crab is a visitor to, where no ledger catches it. It now pages through every issue with
  `gh api --paginate`, drops pull requests, and does not filter by label, because a repository
  the crab cannot label gets its issues without one. When a marker appears in several issues,
  the one whose body opens with it — the shape `crab serve` writes — wins, then the oldest, in
  whatever order the issues arrive ([#59](https://github.com/drevendev/HungryCrab/issues/59)).

- **Four documents still described a verdict the engine had stopped returning.** Since `HUMAN`
  became reachable, an unrecognised licence is `HUMAN` and a missing one is `IDEAS_ONLY` flagged
  for review — but `README.md`, `docs/design/01-concept-and-skill.md`, the licence engine's own
  docstring and `skills/license/references/matrix.md` all still paired them as `IDEAS_ONLY` +
  `HUMAN`. The last of those is the table the `license` skill hands to a model at the moment it
  decides whether code may be copied, which makes it the one that mattered. The same reference
  now also carries the `own` and `bypass` relationships, which short-circuit the matrix entirely
  and had been documented nowhere an agent reads. `tests/test_docs_match_the_engine.py` guards
  the retired claim by name across every Markdown file in the repository.

- **A cached digest was reused for a question it had not been asked.** The reuse check compared
  the schema, the commit and the depth, and ignored three things the manifest records because
  they change the result: the crab's own version, `ignore`, and the maw's license. The commit
  does not move when `.crab.yml` changes, so step 4 of the `eat` protocol — "the maw reads as the
  wrong stack, add the offending paths to `ignore` and rerun" — returned the cached answer and
  the remedy did nothing at all, leaving the whole meal judged against a repository that does not
  exist. For the same reason an upgraded crab kept serving the previous version's verdicts until
  someone passed `--force`, which is the mistake `crab update` was taught to avoid one layer up.

- **The injection detector cried wolf on ordinary READMEs.** Eating `syrupy-project/syrupy`
  produced four flags, and all four were false: "if you need to bypass a custom object
  representation", "where you need to ignore files by file extension",
  `<!-- prettier-ignore-start -->` and `<!-- markdownlint-restore -->`. A verb like *ignore* or
  *bypass* now has to take an object that is an instruction, a rule, a policy or an agent, and
  HTML comments that open with a known formatter or linter directive are not comments to an
  agent. The five fragments are regression cases in `tests/test_safety.py`; the sentences that
  really are aimed at an agent still flag.

- **`NOASSERTION` meant four different things, which is the same as meaning nothing.** The
  license findings now carry a `resolution`, and each situation gets its own answer.
  `LICENSE-APACHE` next to `LICENSE-MIT` stays a choice (`dual`, SPDX `OR`); license files that
  are not alternatives — `apache-2.0.LICENSE` next to `cc-by-4.0.LICENSE`, or one file that says
  "portions of this software are licensed as follows" — become `split`, where the most
  restrictive of them governs the whole repository until a nutrient names its own path; a license
  file nobody can read becomes `unreadable` and asks a human instead of guessing; and a
  repository that licenses nothing at the root while its packages license themselves becomes
  `per-path`, naming the packages. Two plain bugs fell out of this: license files named after
  their license (`apache-2.0.LICENSE`, and any `LICENSES/` directory) were invisible to the
  detector, and `LICENSE.md` was read as a license *named* `md`.
- An unrecognised license is `HUMAN` rather than `IDEAS_ONLY`. The mode existed, was documented,
  and had never been returned; "we read it and do not understand it" is a different situation
  from "nothing is granted", and only one of them a person can settle in a minute.
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
  twenty-four candidates, all scored the same. (Since the content-origin ceiling above, the
  title is generic again, because an issue title is commenter prose; giving the card back its
  structure without the prose is [#130](https://github.com/drevendev/HungryCrab/issues/130).)
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

- **The README now documents the current `.crab.yml` surface instead of making users reverse-engineer it.**
  It lists every top-level key, includes the commented defaults written by `crab init`, and calls
  out settings accepted before their behavior arrives; a regression keeps the README,
  `MawConfig`, and `DEFAULT_CONFIG_TEXT` aligned ([#27](https://github.com/drevendev/HungryCrab/issues/27)).
- `docs/design/02-mvp.md` defers to the roadmap on what a milestone contains, and says so. It
  had `npx skills add` in 0.3 while the roadmap — the authority — has it in 0.6, the same way
  the two once disagreed about digest coverage; a test pins the item that drifted
  ([#78](https://github.com/drevendev/HungryCrab/issues/78)).
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