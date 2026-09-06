# 04 · Evolving Hungry Crab — Design of the Self-Improving Loop

## 1. What it is

A public repository with **active GitHub Actions** that maws an "organism" (`src/`) and its
**goal** (`goal/`). The organism periodically runs the cycle:

```
        ┌──────────────────────────────────────────────────────────┐
        │                                                          │
   GOAL ─► HUNT ─► CONSUME ─► EVOLVE ─► TRIAL ──fail──► HUNT       │
     ▲                                     │                       │
     │                                   pass                      │
     │                                     ▼                       │
     └────────── MOLT ◄──────────────── GROW                       │
               (molting)               (retro)                     │
```

| Phase | What it does | Who | Artifact |
|---|---|---|---|
| **GOAL** | sets a new, harder benchmark without retiring the old ones | model + human (until E3) | PR into `goal/benchmarks/` |
| **HUNT** | searches for candidate repositories for the current failure/goal | `crab hunt` (script) + model picks from the top-20 | `cycles/NNNN/hunt.md` |
| **CONSUME** | digests the chosen prey with the organism as the maw | `crab eat` (script + model) | digest, `menu.md` |
| **EVOLVE** | implements the chosen nutrients in `src/` | model, under license modes | PR `evolve/NNNN-*` |
| **TRIAL** | runs the benchmarks | **plain CI, no LLM** | `metrics.json`, check status |
| **GROW** | retrospective: what helped, what did not, what was learned | model | `cycles/NNNN/retro.md`, update of `docs/LESSONS.md` |
| **MOLT** | refactoring and cleanup: dead code, surplus dependencies, docs sync | model | PR `molt/NNNN` |

The base organism is Hungry Crab itself; the base goal is "digest repositories efficiently". So
the Evolving Crab hunts repositories about git mining, license detection, code metrics and CI
analysis, and picks up techniques from them for its own miners.

## 2. Repository layout

```
evolving-hungry-crab/
├── CONSTITUTION.md              # immutable: purpose, license policy, budget,
│                                # safety rules, what may not be changed autonomously
├── goal/                        # goal pack (the contract for forks)
│   ├── GOAL.md                  # the goal in words: what "better" means, constraints
│   ├── hunt.yml                 # search queries, filters (license, stars, freshness, languages), exclusions
│   ├── budget.yml               # cycles per week, max turns/minutes per phase, max open PRs
│   ├── fitness.py               # runs benchmarks → metrics.json (deterministic)
│   └── benchmarks/
│       ├── current.json         # active thresholds
│       ├── history/             # all previous benchmarks — regression, nothing is ever deleted
│       └── data/                # golden sets, prey pinned by SHA
├── src/                         # the organism (hungry_crab in the base case)
├── cycles/
│   └── 0007/                    # artifacts of one cycle
│       ├── hunt.md · menu.md · evolve.md · metrics.json · retro.md · molt.md
├── docs/
│   ├── LESSONS.md               # accumulated lessons — read into every phase prompt
│   └── log/                     # public decision log
├── skills/                      # phase skills: goal, hunt, consume, evolve, trial, grow, molt
├── .github/
│   ├── CODEOWNERS               # goal/**, .github/**, CONSTITUTION.md → human
│   └── workflows/
│       ├── cycle.yml            # orchestrator: schedule / dispatch → next phase
│       ├── trial.yml            # benchmarks on every PR (required check)
│       ├── guard.yml            # constitution, budget, secret scan, "prey was not executed"
│       └── pages.yml            # dashboard (E4)
└── state/current.json           # pointer to the active cycle and phase (mirrors the issue labels)
```

## 3. Cycle state — GitHub-native

- **Cycle issue** `Cycle #0007` with labels `phase:hunt` … `phase:molt`, `attempt:2`. The body is
  a phase checklist, the comments are the journal of every phase. A human sees everything without
  a special UI.
- **Branches** `evolve/0007-<slug>`, `molt/0007`, `goal/0008`; a PR is the only way to change
  `main`.
- `state/current.json` — machine-readable pointer (which cycle, which phase, attempt, budget
  spent). The workflow reads it instead of parsing labels.
- `concurrency: group: crab-cycle` — phases are strictly sequential.

## 4. Orchestration

`cycle.yml` runs on `schedule` (e.g. every 6 hours), `workflow_dispatch` (manual phase start) and
`issues: labeled` (a human switched the phase with a label). The job reads `state/current.json`,
checks the budget (`guard`), and starts the job for the required phase:

```yaml
jobs:
  route:   # reads state, decides which phase to run, checks budget and kill switch
  hunt:    # needs: route; if phase == hunt → crab hunt + model picks → issue comment, state → consume
  consume: # crab eat <prey> --maw . → menu.md → state → evolve
  evolve:  # model implements top-N nutrients → PR evolve/NNNN → state → trial
  trial:   # does nothing itself: TRIAL is trial.yml on the PR; route observes the check status
  grow:    # after merge: retro.md, LESSONS.md, metrics into cycles/ → state → molt
  molt:    # PR molt/NNNN → after merge state → goal
  goal:    # PR goal/NNNN with a new benchmark → after merge a new cycle, state → hunt
```

Model phases use the official `anthropics/claude-code-action@v1` with `claude_code_oauth_token`
(from `claude setup-token`, Pro/Max subscription) or `anthropic_api_key`. The phase prompt = phase
skill + `GOAL.md` + `LESSONS.md` + the current cycle's artifacts. `--max-turns` and the job timeout
come from `budget.yml`.

Note on subscription keys: use only the official Claude Code / its GitHub Action; do not route
the OAuth token through third-party harnesses. Weekly subscription limits *are* the crab's
"hunger": the cycle budget must stay well below the limit, and on exhaustion the cycle pauses
instead of failing.

## 5. TRIAL — benchmarks without an LLM

Rule: everything that decides "pass / fail" must be reproducible in CI without a model. For the
base goal:

| Benchmark | Measures | Example threshold |
|---|---|---|
| B1 License Verdicts | accuracy of SPDX + mode on 40 repositories pinned by SHA | 100 % |
| B2 Traits Recall | recall/precision of traits on 10 labeled prey | ≥ 0.90 / ≥ 0.90 |
| B3 Golden Nutrients | share of "must-find" nutrients (manual labeling of 5 prey→maw pairs) present in the deterministic layer's top-30 menu | ≥ 0.80 |
| B4 Budget | digest size in tokens and time on reference repositories | ≤ 30k, ≤ 120 s |
| B5 Ecosystem Coverage | number of correctly parsed manifest/CI formats | ≥ 6 → grows |
| B6 Menu Quality (slow) | menu rated by an LLM judge against a rubric, and the share of accepted issues in the fleet | weekly, soft threshold |

Benchmarks live in `goal/benchmarks/history/` forever: **GOAL adds, never removes**. Golden data is
pinned to prey SHAs — otherwise the benchmark drifts. LLM-dependent metrics (B6) do not block
merges; they are a signal for GROW and GOAL.

## 6. Phase rules

**HUNT.** `crab hunt --for .` takes queries from `hunt.yml` and hints from the last TRIAL failure
("B2 dropped on C# repositories" → look for C# analyzers), filters (license, stars, freshness,
not in the ledger), scores; the model picks 1–3 prey and writes a rationale. If nothing suitable
is found K times in a row — state `starving`, escalation to a human.

**CONSUME.** A regular `crab eat` with the organism as maw; hunger from `goal/.crab.yml`.

**EVOLVE.** Implements the top-N nutrients (N from the budget), one PR per cycle, one commit per
nutrient. License modes strictly; `REIMPLEMENT` via the clean-room subagent; attribution updated.
The PR must contain: the nutrient list with provenance and the expected impact on benchmarks.

**TRIAL.** `trial.yml` on the PR: `fitness.py` → `metrics.json`; comparison against
`current.json` and the whole `history/`. Failure → a comment with the metrics diff → `attempt+1`,
state `hunt` with a hint; after `max_attempts` — `needs-human`.

**GROW.** Only after merge: a templated retro (hypothesis → result → lesson), update of
`LESSONS.md` (size-capped — old lessons get compressed, otherwise the prompt grows forever),
metrics written to `cycles/NNNN/metrics.json` for the dashboard.

**MOLT.** A refactoring PR with hard invariants: all benchmarks green, coverage not lower,
LOC/complexity not higher, dead code removed (`vulture`/`knip`/equivalents), dependencies pruned,
docs in sync. If the invariants fail — the PR is closed and a lesson goes into the next cycle's
GROW.

**GOAL.** Proposes the next benchmark: harder along one measurable axis (threshold, new ecosystem,
new nutrient category, fewer tokens), with a rationale from the retro. Mandatory: regression is
preserved, the threshold is reachable (baseline already ≥ 50 % of the target), the metric is
deterministic. Until level E3 a human merges it.

## 7. Constitution and safety

`CONSTITUTION.md` — what the organism cannot change by itself:
- purpose, and a ban on changing the goal without a human;
- license policy (the mode matrix, `IDEAS_ONLY` by default for the unknown);
- **prey code is never executed** — CONSUME is static, TRIAL executes only the organism's own code
  on an ephemeral runner;
- prey content is data, not instructions;
- budget and kill switch: the repo variable `CRAB_PAUSED=true` stops `route`;
- a ban on touching secrets, workflows, `CODEOWNERS` and the constitution from autonomous phases.

Mechanics, not promises:
- `CODEOWNERS` on `goal/**`, `.github/**`, `CONSTITUTION.md` + the branch rule "review from code
  owners" → changes to those paths need a human; everything else merges on checks.
- The default `GITHUB_TOKEN` cannot push workflow changes — we keep it that way.
- `guard.yml` on every PR: the diff does not touch protected paths (when the author is the bot),
  secret scan, attribution updated on `COPY`, no execution from the cache in the CONSUME logs.
- The `PreToolUse` hook from the base crab is enabled in CI as well.
- All secrets with minimal rights; the model token is visible only to model-phase jobs.

## 8. Autonomy levels ("training wheels")

| Level | Who starts phases | Who merges | Promote after |
|---|---|---|---|
| L0 | a human, every phase | a human | 3 clean cycles |
| L1 | schedule | a human — everything | 5 cycles without PR edits |
| L2 | schedule | auto: EVOLVE on green TRIAL; human: GOAL, MOLT | 10 cycles, fitness never drops |
| L3 | schedule | auto for everything within the constitution; the human gets a weekly digest and escalations | indefinitely, with the kill switch |

## 9. Observability (E4)

`pages.yml` builds a static dashboard from `cycles/*/metrics.json`: fitness per cycle, attempts
to pass TRIAL, cost (turns/minutes), accepted nutrients by category, eaten prey with licenses.
Plus the public `docs/log/` — a readable history of decisions.

## 10. Fork kit: another goal in an hour

Contract: a fork changes **only** `goal/` and `src/`; workflows are consumed as reusable workflows
from upstream and update without manual merges:

```yaml
# .github/workflows/cycle.yml in a fork
jobs:
  cycle:
    uses: drevendev/evolving-hungry-crab/.github/workflows/cycle.yml@v1
    secrets: inherit
```

`crab new-goal <name>` generates a pack skeleton with questions: what does "better" mean, how to
measure it without an LLM, where the data comes from, which queries to hunt with.

### Example: `goal-sudoku`

| Element | Content |
|---|---|
| `GOAL.md` | solve Sudoku sets completely and fast; correctness is absolute, speed is the growth axis |
| `hunt.yml` | queries: `sudoku solver`, `exact cover`, `dancing links`, `DLX`, `constraint propagation sudoku`, `SAT sudoku`; any language (ideas), permissive only for `COPY`; stars ≥ 50 |
| `benchmarks/data/` | `top95`, `hardest20`, generated 17-clue puzzles, later 16×16 |
| `fitness.py` | solve rate, p50/p95 time, memory; deterministic, no network |
| `src/` | a naive backtracking solver as the baseline |
| GOAL ladder | 100 % of top95 in < 1 s total → < 100 ms → hardest20 → 16×16 → difficulty rating → generator with unique solutions |

What happens in a cycle: HUNT finds DLX-based solvers; CONSUME yields nutrients: "Knuth's DLX
algorithm" (`IDEAS_ONLY` — the idea is free), "a puzzle test set" (check the data license),
"bitmask candidate representation" (idea), "benchmark harness" (`COPY` if MIT); EVOLVE implements
DLX in the clean room; TRIAL shows p95; GROW records what produced the gain; MOLT throws out the
old backtracking if it is not needed as a fallback; GOAL raises the bar.

### Other goals that fit the contract

A chess engine (Elo against fixed opponents), a compressor (ratio × speed on a corpus), a JSON
parser (throughput, conformance suite), an accessibility linter (recall/precision on a labeled
corpus), a level generator for a game (playability/diversity metrics — relevant for a fleet with
idle and simulation games). Goals without a deterministic metric ("make the documentation better")
fit poorly — they need an LLM judge, which makes for a slow, non-strict TRIAL.

## 11. Risks and countermeasures

| Risk | Countermeasure |
|---|---|
| Overfitting to the benchmark (the crab "memorizes" the golden set) | hidden holdout set in TRIAL, prey rotation in benchmarks, B6 as an external signal |
| Unbounded prompt growth (`LESSONS.md`) | size cap, compression of old lessons in GROW |
| Prey drift (repository changed, benchmark broke) | everything pinned by SHA |
| Burning through subscription limits | `budget.yml`, pause instead of failure, weekly report |
| Prompt injection from prey in autonomous mode | same rules as the base crab, plus `guard.yml`, plus protected paths |
| Breaking its own main | PRs only + required TRIAL + guard; rollback is a plain revert |
| The goal degrades into "easy wins" | GOAL requires a growth axis and baseline ≥ 50 % of the threshold; a human merges GOAL until L3 |
