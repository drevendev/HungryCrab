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
| 0.1 | Sniff & Digest | CLI, miners without a model, fixtures, CI on Windows + Linux | digest of any public repo ≤ 30k tokens, ≤ 120 s |
| 0.2 | Menu | compare, scoring, issues/architecture miners, skills, historian/architect subagents, serve issues, ledger, plugin | end-to-end `/crab:eat` → issues, 0 duplicates on rerun |
| 0.2.1 | Self-feeding | `/crab:eat` from a live agent session with the crab as the maw; fix what the skill gets wrong ([05-self-feeding.md](05-self-feeding.md)) | 0.2's exit criterion honestly closed: two live meals, 0 duplicates, skill defects fixed |
| 0.2.2 | Menu benchmark | B1 from [06-benchmark.md](06-benchmark.md): frozen maw and prey, the golden set, the deterministic menu benchmark and its CI gate | `recall_must@30` measured on master and gating pull requests |
| 0.3 | Serve | PR branches, trace, attribution, clean room, safety hook, wiki, strict mode, docs | ≥ 3 merged PRs in the fleet; **MVP closed** |
| 0.3.1 | Scheduled crab | `crab loop`: a state machine a local scheduler wakes once per phase — crave, hunt, eat, serve, grow, trial, taste, molt, harden — on this repository or on any target it is pointed at ([07-scheduled-crab.md](07-scheduled-crab.md)) | ten consecutive scheduled wake-ups with no human input except merging, one round on a repository that is not the crab, and one version the crab hardened by itself |
| 0.4 | Deep Bite | Discussions (GraphQL), PR review comments, Actions runs statistics (flaky tests, durations), tree-sitter symbols and call graph, Go/Rust/JVM/PHP/Ruby manifests, GitLab adapter | 8+ ecosystems, architectural nutrients with symbol-level evidence |
| 0.5 | Taste Memory | scorer learning from the ledger (accepted/rejected by category and maw), `crab hunt --for .` (finding prey for a maw via `gh search` + similarity signals), multi-prey (`eat a b c` → merged menu), hunger profiles by repository type | share of accepted issues grows between iterations on the same maw |
| 0.6 | Everywhere | composite GitHub Action, MCP server (`crab_digest`, `crab_menu`), PyPI package, `npx skills add`, docs for Codex/Cursor, `crab report` (HTML report of a digest) | the crab runs on a schedule in a maw's CI without Claude Code |
| 1.0 | Stable | stable schemas (`nutrient`, `manifest`, `.crab.yml`), semver guarantees, ledger migrations, a set of 50 license test repositories, public benchmarks | schemas frozen, changelog and release automation — on the crab itself |

Cross-cutting themes of track A:
- **Own hygiene** — from 0.2 the crab eats itself (`/crab:eat` on its own repo with prey from the
  git-mining / license-detection space) and merges its own proposals.
- **Benchmarks as a product** — every version adds entries to `benchmarks/`, because track B
  stands on them. The specification is [06-benchmark.md](06-benchmark.md): B1 is deterministic
  and gates pull requests, B2 judges whole meals across crab versions and Claude models and runs
  at milestone boundaries. The headline question is whether Haiku with the crab beats Opus
  without it.

## Track B · Evolving Hungry Crab

Detailed design in [04-evolving-crab.md](04-evolving-crab.md). Stages here.

| Stage | Name | Content | Exit criterion |
|---|---|---|---|
| E0 | Constitution & Benchmarks | separate public repository, `CONSTITUTION.md` (immutable goals, license policy, budget, safety rules), a `goal/` pack for the base goal "digest repositories efficiently", benchmarks B1–B5 (deterministic), `fitness.py`, TRIAL as plain CI | TRIAL reproducible locally and in CI, baseline recorded |
| E1 | Loop on Rails | `cycle.yml` workflow, cycle issue as the journal, phases as jobs with phase skills, **every phase started by a human** (`workflow_dispatch` / label), Claude via `claude-code-action` with `setup-token` | 3 full cycles with manually started phases, all cycle artifacts in `cycles/NNNN/` |
| E2 | Semi-Auto | schedule, automatic HUNT→CONSUME→EVOLVE→TRIAL transitions, auto-merge of EVOLVE PRs on green TRIAL, GROW writes retro and `LESSONS.md`, MOLT opens a PR; **GOAL and MOLT merged by a human** (CODEOWNERS), budget/hunger, kill switch | 10 cycles with no intervention except GOAL/MOLT approvals, fitness never decreases |
| E3 | Autonomous | autonomous GOAL/MOLT within the constitution, escalation to a human after K failures or starvation, weekly digest for the human | a month of autonomous operation with no incidents (constitution violations, broken main) |
| E4 | Public Organism | GitHub Pages dashboard (fitness per cycle, cost, accepted nutrients), public decision log, "prey" PRs into the base Hungry Crab from evolution results | outsiders can read the evolution history and reuse improvements |

Dependencies: E0 requires 0.3 (something to benchmark); E2 requires 0.5 (`crab hunt` is the HUNT
phase). E0 also inherits its budget numbers from 0.3.1, which runs the same phases locally and
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
