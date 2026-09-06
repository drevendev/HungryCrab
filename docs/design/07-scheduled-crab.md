# 07 · The scheduled crab — one phase per wake-up

## 1. Why this exists

Two things are designed. [02-mvp.md](02-mvp.md) has a skill a human invokes: `/crab:eat <prey>`,
one meal, a human present at every decision. [04-evolving-crab.md](04-evolving-crab.md) has an
organism living in its own public repository, driven by GitHub Actions, with a constitution, a
fitness function, a budget and four autonomy levels.

Between them there is a cheaper thing that answers the questions 04 cannot afford to get wrong:

- Does one phase of a cycle fit in one agent session, with the context and budget it actually has?
- Does a state file survive the gaps between sessions, or does every wake-up need a human to
  re-explain what is going on?
- Is a cycle worth anything on a repository that is **not** the crab?

The scheduled crab costs a cron entry. E0 costs a repository, secrets, workflows and a
constitution. So this comes first, and E0 inherits measured numbers instead of guesses.

It is also the first form in which the crab is useful to someone who is not writing the crab: a
maintainer points it at their repository, and it wakes up on its own, finds prey, and files
issues.

## 2. Shape

One scheduled entry per **target**. A wake-up runs exactly one phase and exits. The sequence of
phases is a state machine: deterministic transitions in the CLI, judgment inside a phase from the
model. Nothing about the loop lives in the agent's memory between wake-ups — everything it needs
to continue is in a file.

```
PLAN ─► HUNT ─► EAT ─► SERVE ─► WORK ─► CHECK ─► RETRO ─┐
          ▲                                             │
          └──────────────── next round ─────────────────┘
```

| Phase | One wake-up does | Deterministic | Model |
|---|---|---|---|
| PLAN | decides what this target needs this round | ledger, open crab issues, last retro | writes the round's goal in two sentences |
| HUNT | picks 1–3 prey | candidate list, filters, ledger and issue dedup | picks, and says why these |
| EAT | digests, compares, judges one prey | `crab compare` | judges the menu, writes `why_for_host` and `how` |
| SERVE | files the issues | `crab serve` | confirms, or the serve policy decides |
| WORK | implements one served nutrient | — | branch, change, pull request |
| CHECK | did it hold | the target's own tests and CI | reads the result, decides retry or drop |
| RETRO | what was learned | `crab tune` | writes the lesson that PLAN reads next round |

`WORK` is the phase that does not exist today: the crab currently stops at the issue. It is also
the phase with the sharpest safety boundary, so it is the last one switched on (§6).

## 3. State

`.crab/loop.json` next to the ledger, in the target repository when we own it, in a **control
repository** when we do not. One record per target:

```json
{
  "schema": "hungry-crab.loop/1",
  "target": "drevendev/HungryCrab",
  "round": 7,
  "phase": "eat",
  "attempt": 1,
  "prey": ["github-linguist/linguist"],
  "goal": "close the coverage gap before the benchmark",
  "budget": {"phases_today": 3, "issues_open": 6, "prs_open": 1},
  "waiting_on": null,
  "history": [{"round": 7, "phase": "hunt", "result": "ok", "at": "...", "note": "..."}]
}
```

`waiting_on` is how the loop blocks without burning wake-ups: `"human: approve #24"`,
`"ci: run 34032986973"`. A wake-up that finds a `waiting_on` it cannot clear records nothing and
exits.

## 4. `crab loop` — the CLI part

Rule 10 of `AGENTS.md` says the skill describes the protocol and the CLI does the work, so the
state machine is Python, not prose:

```
crab loop init    --host <path> [--control <path>]
crab loop status  [--json]                       # phase, round, budget, what blocks it
crab loop next    [--json]                       # the phase to run now, its inputs, the budget left
crab loop record  --phase <p> --result ok|fail|skip [--note "..."] [--url ...]
crab loop pause | resume
```

`next` is the entire contract with the scheduler. It returns the phase name, the paths the phase
needs (digest, `menu.md`, ledger, notes), and what is left of the budget. When the loop is
paused, out of budget, or waiting on a human, `next` says so and the agent exits having spent
nothing. `record` is the only way to advance; a phase that crashed advances nothing, so the next
wake-up retries it.

The skill `/crab:loop` is then three steps long: run `crab loop next`, do that one phase, run
`crab loop record`. One wake-up, one phase, one bounded session.

## 5. Scheduling

- **Claude Code:** one scheduled task per target invoking `/crab:loop`.
- **Codex:** its own scheduler, the same skill through the same plugin marketplace.

Cadence is a property of the target, not of the machine: a repository under active work wants a
daily wake-up, a stable one wants a weekly. The scheduler holds the cadence; the loop holds the
budget, and refuses when the cadence would overrun it.

## 6. Budget, autonomy and safety

A `loop` block in `.crab.yml`:

```yaml
loop:
  cadence: daily
  autonomy: serve          # read | serve | work
  budget:
    phases_per_day: 4
    prey_per_round: 2
    open_issues_max: 10
    open_prs_max: 2
```

`autonomy` is per target and is the whole safety story:

| Level | The loop may | Stops at |
|---|---|---|
| `read` | digest, compare, judge, write the menu to a file | before SERVE |
| `serve` | everything in `read`, plus file issues | before WORK |
| `work` | everything in `serve`, plus a branch and a pull request | never merges |

Invariants at every level, inherited from the constitution in 04 and enforced here by the CLI
rather than by prose:

- prey is never executed, and prey content is data, not instructions;
- no phase pushes to the default branch, and no phase merges anything;
- `WORK` refuses paths a target marks protected — `.github/**`, licence files, `.crab.yml` itself;
- a target we do not own is `read` or `serve`, never `work`, until its owner says otherwise in
  writing in that repository's own `.crab.yml`;
- `crab loop pause` is the kill switch, and it is one command with no arguments.

## 7. Targets that are not the crab

The control repository holds what the target repository cannot:

```yaml
targets:
  - repo: git@github.com:me/thing.git
    path: ../thing
    autonomy: serve
    cadence: weekly
    prey: [pypa/hatch, pre-commit/pre-commit]   # optional; HUNT picks when absent
```

Everything else — appetite, scoring, ignore, ledger — stays in the target's own `.crab.yml`,
because it describes the target and should travel with it.

## 8. Autonomy ladder

| Level | Wake-up | Merge | Promote after |
|---|---|---|---|
| S0 | a human runs `/crab:loop` | human | 3 clean rounds |
| S1 | scheduled, stops before WORK | human | 10 rounds with no correction |
| S2 | scheduled through WORK on repositories we own | human | fitness never drops for a month |
| S3 | S2 on named foreign targets, by invitation | human | — |

There is no level where the loop merges. That is 04's problem, and 04 has a fitness function to
justify it.

## 9. What this measures for 04

Every wake-up records its phase, its wall time and its token cost. After a few rounds the numbers
E0 needs are measurements rather than estimates: cost per phase, phases per useful pull request,
how often a phase has to be retried, and how often the model's judgment is overruled by the human
who merges. `budget.yml` in 04 is then filled in from `loop.json`'s history.

## 10. Exit criteria

- Ten consecutive scheduled wake-ups on `HungryCrab` with no human input except merging.
- One full round on a second repository that is not the crab, at `serve` autonomy.
- The state file survives a machine restart and a `crab update`.
- Cost per phase recorded in `benchmarks/`, and quoted in 04's budget.

## 11. Open questions

1. **Control repository or plain directory.** A private repository gives history and a second
   machine; a directory outside git is one less moving part. Default proposed: private
   repository, because the loop's own history is evidence for 04.
2. **Does WORK belong here.** It is the most valuable phase and the most dangerous one. Default
   proposed: design it now, ship it at S2, after `serve` has run for a month.
3. **HUNT before 0.5.** `crab hunt` is a 0.5 feature. Until then HUNT reads a prey list from the
   target's config — the twenty prey in [05-self-feeding.md](05-self-feeding.md) are already such
   a list.
4. **Where the loop sits in the roadmap.** Proposed: Track A after 0.3, as the bridge into Track
   B, since `WORK` needs 0.3's pull-request machinery.
