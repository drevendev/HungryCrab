# 03 · Roadmap: Base Crab → Evolving Crab → Forks

Three tracks run in sequence with overlap: the Evolving Crab can start as soon as the base crab
has deterministic benchmarks (after 0.3), and the fork kit as soon as the Evolving Crab has run a
few clean cycles.

```
Track A · Hungry Crab        0.1 ─ 0.2 ─ 0.3 ═══ 0.4 ─ 0.5 ─ 0.6 ─ 1.0
Track B · Evolving Crab                  E0 ─ E1 ─ E2 ─ E3 ─ E4
Track C · Forks                                        F0 ─ F1 ─ F2
```

## Track A · Hungry Crab (base)

| Version | Name | Content | Exit criterion |
|---|---|---|---|
| 0.1 | Sniff & Digest | CLI, miners without a model, fixtures, CI on Windows + Linux | digest of any public repo within the token policy, ≤ 120 s for ≤ 50k LOC and ≤ 5k commits |
| 0.2 | Menu | compare, scoring, issues/architecture miners, skills, historian/architect subagents, serve issues, ledger, plugin | end-to-end `/crab:eat` → issues, 0 duplicates on rerun |
| 0.2.1 | Self-feeding | `/crab:eat` from a live agent session with the crab as the maw; fix what the skill gets wrong ([05-self-feeding.md](05-self-feeding.md)) | 0.2's exit criterion honestly closed: two live meals, 0 duplicates, skill defects fixed |
| 0.2.2 | Menu benchmark | B1 from [06-benchmark.md](06-benchmark.md): frozen maw and prey, the golden set, the deterministic menu benchmark and its CI gate | **done.** `recall_must@30` = 1.00 (10/10) and `noise@30` = 0.66 (19/29) on master, both gating pull requests through the test suite |
| 0.3 | Serve | PR branches, trace, attribution, clean room, safety hook, wiki, strict mode, docs; the license resolutions and the `trust` relationship; the budget policy and paged documents ([08-budgets-and-feeder.md](08-budgets-and-feeder.md)); coverage measured and gated | ≥ 3 merged PRs in the fleet; **MVP closed** |
| 0.3.1 | Feeder | The deterministic pipeline as a reusable GitHub workflow and a composite action: a maw names its prey, the job runs `catch → digest → compare` with no model anywhere, and uploads the meal as a build artifact. Rate limits, retries and conditional requests, because a runner has no `gh auth`; `--shallow --since` by default. Brought forward from 0.6 ([08-budgets-and-feeder.md](08-budgets-and-feeder.md)) | a repository with no agent installed gets a menu artifact on a schedule; the Evolving Crab's CONSUME phase is this job |
| 0.3.2 | Scheduled crab | `crab loop`: a state machine a local scheduler wakes once per phase — crave, hunt, eat, serve, grow, trial, taste, molt, harden — on this repository or on any target it is pointed at ([07-scheduled-crab.md](07-scheduled-crab.md)) | ten consecutive scheduled wake-ups with no human input except merging, one round on a repository that is not the crab, and one version the crab hardened by itself |
| 0.4 | Deep Bite | Discussions (GraphQL), PR review comments, Actions runs statistics (flaky tests, durations), tree-sitter symbols and call graph, Go/Rust/JVM/PHP/Ruby manifests, GitLab adapter | 8+ ecosystems, architectural nutrients with symbol-level evidence |
| 0.5 | Taste Memory | scorer learning from the ledger (accepted/rejected by category and maw), `crab hunt --for .` (finding prey for a maw via `gh search` + similarity signals), multi-prey (`eat a b c` → merged menu), hunger profiles by repository type | share of accepted issues grows between iterations on the same maw |
| 0.6 | Everywhere | MCP server (`crab_digest`, `crab_menu`), PyPI package, `npx skills add`, docs for Codex/Cursor, `crab report` (HTML report of a digest) | the crab is installable and usable from every harness the fleet uses, not only Claude Code |
| 1.0 | Stable | stable schemas (`nutrient`, `manifest`, `.crab.yml`), semver guarantees, ledger migrations, a set of 50 license test repositories, public benchmarks | schemas frozen, changelog and release automation — on the crab itself |

Released: **0.2.2**. In flight: **0.3**.

Cross-cutting themes of track A:
- **Own hygiene** — from 0.2 the crab eats itself (`/crab:eat` on its own repo with prey from the
  git-mining / license-detection space) and merges its own proposals.
- **Digest anything** — the exit criterion of every version is written in terms of nutrients, but
  the goal underneath is that no repository defeats the digestion: not a gigabyte monorepo, not a
  data-only repository, not a licence split across packages, not an ecosystem no miner knows.
  Every version therefore has a set of prey chosen to break it rather than to feed it, and a meal
  is judged twice: by the nutrients it produced and by the defect it exposed. A meal that teaches
  nothing has to be repeated after the next miner lands, and every defect earns a fixture under
  `tests/fixtures/repos/` rather than a one-off patch. Track B cannot start on a crab that only
  digests the repositories it was written against. The queue itself is maintained by the
  maintainer and is not part of this repository: it names private repositories, and publishing a
  list of targets is not the same as publishing a tool.
- **Two users, two policies** — an agent session and a budgeted loop want opposite things from
  the same digest, and where they disagree the difference is configuration rather than a
  compromise ([08-budgets-and-feeder.md](08-budgets-and-feeder.md)).
- **Benchmarks as a product** — every version adds entries to `benchmarks/`, because track B
  stands on them. Three of them run at three different rhythms. The **digest benchmark**
  (seconds and tokens on reference prey) has run since 0.1, by hand, when a miner changes.
  **B1, the menu benchmark**, has gated every pull request since 0.2.2: frozen digests, the
  maintainer's own verdicts as the golden set, no model and no network. **B2, the meal
  benchmark**, costs money and runs at milestone boundaries starting with the 0.3 release; it
  judges whole meals across crab versions and models, and its headline question is whether
  Haiku with the crab beats Opus without it. The specification for B1 and B2 is
  [06-benchmark.md](06-benchmark.md). A fourth number is still missing and is owed by 0.4:
  digest **coverage**, `files_counted / files`, which is what actually degrades when a prey is
  larger than the caps.

## Track B · Evolving Hungry Crab

Detailed design in [04-evolving-crab.md](04-evolving-crab.md). Stages here.

| Stage | Name | Content | Exit criterion |
|---|---|---|---|
| E0 | Constitution & Benchmarks | separate public repository, `CONSTITUTION.md` (immutable goals, license policy, budget, safety rules), a `goal/` pack for the base goal "digest repositories efficiently", benchmarks B1–B5 (deterministic), `fitness.py`, TRIAL as plain CI | TRIAL reproducible locally and in CI, baseline recorded |
| E1 | Loop on Rails | `cycle.yml` workflow, cycle issue as the journal, phases as jobs with phase skills, **every phase started by a human** (`workflow_dispatch` / label), Claude via `claude-code-action` with `setup-token` | 3 full cycles with manually started phases, all cycle artifacts in `cycles/NNNN/` |
| E2 | Semi-Auto | schedule, automatic HUNT→CONSUME→EVOLVE→TRIAL transitions, auto-merge of EVOLVE PRs on green TRIAL, GROW writes retro and `LESSONS.md`, MOLT opens a PR; **GOAL and MOLT merged by a human** (CODEOWNERS), budget/hunger, kill switch | 10 cycles with no intervention except GOAL/MOLT approvals, fitness never decreases |
| E3 | Autonomous | autonomous GOAL/MOLT within the constitution, escalation to a human after K failures or starvation, weekly digest for the human | a month of autonomous operation with no incidents (constitution violations, broken main) |
| E4 | Public Organism | GitHub Pages dashboard (fitness per cycle, cost, accepted nutrients), public decision log, "prey" PRs into the base Hungry Crab from evolution results | outsiders can read the evolution history and reuse improvements |

Dependencies: E0 requires 0.3 (something to benchmark) and 0.3.1 (the CONSUME phase is the Feeder job);
E2 requires 0.5 (`crab hunt` is the HUNT phase). E0 also inherits its budget numbers from 0.3.2, which runs the same phases locally and
records what each one costs ([07-scheduled-crab.md](07-scheduled-crab.md)); a cron entry is a
cheaper place to find out that a phase does not fit in one session than a public organism is.

## Track C · Forks (other goals)

| Stage | Name | Content | Exit criterion |
|---|---|---|---|
| F0 | Goal Pack Contract | specification of `goal/` (GOAL.md, hunt.yml, benchmarks/, fitness.py, budget.yml), the organism in `src/`, workflows as **reusable workflows from upstream** (a fork does not copy them, it references `uses: drevendev/evolving-hungry-crab/.github/workflows/cycle.yml@v1`) | the Evolving Crab itself runs on the contract; its goal is just another pack |
| F1 | Reference Fork: Sudoku | template repository `evolving-crab-template` + `goal-sudoku`: solve puzzle sets faster and more completely; demonstrates HUNT for algorithms (DLX, constraint propagation, SAT), license modes for code from prey, the goal ladder | the fork passes ≥ 5 cycles and beats the baseline solver |
| F2 | Fork Kit & Community | `crab new-goal` pack generator, guide "build your own crab in an hour", goal catalog (examples: chess engine by Elo, compressor by ratio, JSON parser by throughput, accessibility linter by recall), safe-benchmark rules (no prey execution, sandbox for the organism) | 2+ forks not by the author pass a cycle |

## What we measure across the whole roadmap

| Metric | Why |
|---|---|
| Share of accepted issues / merged PRs by category | real usefulness of the crab for the fleet |
| License verdict accuracy on the test set | trust in the tool |
| Model tokens per `/crab:eat` | economics; should fall as quality rises |
| Digest time on reference repositories | speed |
| Recall of "golden" nutrients (B3) | quality of the deterministic layer |
| Evolving Crab fitness per cycle, cycles to pass TRIAL | does evolution work |
| Security incidents (prey execution, constitution violations) | must be 0 |
