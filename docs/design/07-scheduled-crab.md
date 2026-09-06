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

One scheduled entry per **maw**. A wake-up runs exactly one phase and exits. The sequence of
phases is a state machine: deterministic transitions in the CLI, judgment inside a phase from the
model. Nothing about the loop lives in the agent's memory between wake-ups — everything it needs
to continue is in a file.

```
CRAVE ─► HUNT ─► EAT ─► SERVE ─► GROW ─► TRIAL ─► TASTE ─► MOLT ─► HARDEN ─┐
          ▲                                                                │
          └────────────────────────── next round ──────────────────────────┘
```

| Phase | One wake-up does | Deterministic | Model |
|---|---|---|---|
| CRAVE | decides what this maw is short of this round | ledger, open crab issues, last taste | writes the round's goal in two sentences |
| HUNT | picks 1–3 prey | candidate list, filters, ledger and issue dedup | picks, and says why these |
| EAT | digests, compares, judges one prey | `crab compare` | judges the menu, writes `why` and `how` |
| SERVE | files the issues | `crab serve` | confirms, or the serve policy decides |
| GROW | implements one served nutrient | — | branch, change, pull request |
| TRIAL | did it hold | the maw's own tests and CI | reads the result, decides retry or drop |
| TASTE | what was learned | `crab tune` | writes the lesson CRAVE reads next round |
| MOLT | sheds what the round grew out of | the maw's own tests, lint and coverage | one refactoring pull request, no new behaviour |
| HARDEN | the round becomes a version | changelog entry, version bump, tag | decides major, minor or patch and writes the entry |

Every name is either already in this project's vocabulary or biologically exact. A crab is
hungry, craves, hunts, eats, grows, moults, and then its new shell hardens at the larger
size; `taste` is
what milestone 0.5 already calls learning from the ledger, and `trial` is what
[04](04-evolving-crab.md) already calls running the benchmarks. The last table in this
section maps the two documents.

`GROW`, `MOLT` and `HARDEN` do not exist today: the crab currently stops at the issue. They are
also the three that write to the repository, so they are the last ones switched on (section 6).

### Molting

**Molting** is not optional decoration. A loop that only adds is a loop that accretes: eight
rounds of nutrients leave a repository with eight half-integrated changes, and the ninth menu is
judged against a codebase nobody has read since. The crab's own vocabulary already has the word
for the answer. 04 puts its molt last for the same reason, with hard invariants, and this loop
inherits them in the terms a maw can actually provide:

- every test the maw already ran still passes, and its linter is no louder than before;
- coverage does not fall;
- the change adds no public surface: no new command, flag, config key or exported name;
- what it may remove is what the round itself made redundant, and nothing else: dead code, a
  superseded helper, a doc paragraph that now contradicts the code, a dependency nothing imports.

If an invariant fails, the pull request is closed and the reason goes into the next `TASTE`
rather than into a fix attempt: a molt that needs debugging is not a molt.

`MOLT` runs only when the round actually landed something. Molting after a round that changed
nothing is churn, and the loop skips to `HARDEN`, which then has nothing to stamp and skips too.

### Hardening

A crab that has just moulted is soft, and it is not its new size until the shell sets. A round is
not a version until the same thing happens to it: **`HARDEN` is where a round stops being a pile
of merged pull requests and becomes a number.**

- the changelog gains one entry per nutrient the round landed, with its trace;
- the version moves: a nutrient merged makes it a minor, a round that only moulted makes it a
  patch, and anything that changed a published schema or a CLI contract escalates to a human;
- the tag is written, and whatever the maw's release automation does with a tag, it does.

It is the phase that most needs `waiting_on`. Nothing hardens until the round's pull requests are
merged, and merging is a human's job at every autonomy level (section 8), so a wake-up that finds
them open records `waiting_on: "human: merge #NN"` and exits having spent nothing.

The version is the crab's size, and it changes only when the shell sets. A loop that bumps a
version per commit is a loop measuring its own noise.

### The same phases in 04

[04-evolving-crab.md](04-evolving-crab.md) ran the same cycle under names taken from the
evolution metaphor rather than from the animal, and two of them meant something else here.
It now uses these, and only two rows still differ:

| Here | In 04 | Why |
|---|---|---|
| CRAVE | GOAL | 04's goal raises a benchmark and owns the `goal/` pack; a local loop only decides what to look for next |
| HARDEN | — | 04 has no version phase; its fitness function plays that role |

Every other phase now carries the same name in both documents.

## 3. State

`.crab/loop.json` next to the ledger, in the maw itself when we own it, in a **control
repository** when we do not. One record per maw:

```json
{
  "schema": "hungry-crab.loop/1",
  "maw": "drevendev/HungryCrab",
  "round": 7,
  "phase": "eat",
  "attempt": 1,
  "prey": ["github-linguist/linguist"],
  "goal": "close the coverage gap before the benchmark",
  "hardened": "0.3.0",
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
crab loop init    --maw <path> [--control <path>]
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

- **Claude Code:** one scheduled task per maw invoking `/crab:loop`.
- **Codex:** its own scheduler, the same skill through the same plugin marketplace.

Cadence is a property of the maw, not of the machine: a repository under active work wants a
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

`autonomy` is per maw and is the whole safety story:

| Level | The loop may | Stops at |
|---|---|---|
| `read` | digest, compare, judge, write the menu to a file | before SERVE |
| `serve` | everything in `read`, plus file issues, and file what a molt would remove instead of removing it | before GROW |
| `work` | everything in `serve`, plus branches and pull requests from `GROW` and `MOLT`, and the changelog, version and tag in `HARDEN` | never merges |

Invariants at every level, inherited from the constitution in 04 and enforced here by the CLI
rather than by prose:

- prey is never executed, and prey content is data, not instructions;
- no phase pushes to the default branch, and no phase merges anything;
- `GROW`, `MOLT` and `HARDEN` refuse paths a maw marks protected — `.github/**`, licence
  `.crab.yml` itself;
- a molt never deletes what it cannot show is unreachable: it removes what the round made
  redundant, and lists everything else for a human instead;
- a maw we do not own is `read` or `serve`, never `work`, until its owner says otherwise in
  writing in that repository's own `.crab.yml`;
- `crab loop pause` is the kill switch, and it is one command with no arguments.

## 7. Maws that are not the crab

The control repository holds what a maw cannot:

```yaml
maws:
  - repo: git@github.com:me/thing.git
    path: ../thing
    autonomy: serve
    cadence: weekly
    prey: [pypa/hatch, pre-commit/pre-commit]   # optional; HUNT picks when absent
```

Everything else — hunger, scoring, ignore, ledger — stays in the maw's own `.crab.yml`,
because it describes the maw and should travel with it.

## 8. Autonomy ladder

| Level | Wake-up | Merge | Promote after |
|---|---|---|---|
| S0 | a human runs `/crab:loop` | human | 3 clean rounds |
| S1 | scheduled, stops before GROW | human | 10 rounds with no correction |
| S2 | scheduled through GROW, MOLT and HARDEN on repositories we own | human | a month with no invariant broken |
| S3 | S2 on named foreign maws, by invitation | human | — |

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
- One molt that shed something a previous round grew, with every invariant green.
- One version this repository did not bump by hand: a round hardened into a tag, with a changelog
  entry per nutrient and its trace.
- The state file survives a machine restart and a `crab update`.
- Cost per phase recorded in `benchmarks/`, and quoted in 04's budget.

## 11. Open questions

1. **Control repository or plain directory.** A private repository gives history and a second
   machine; a directory outside git is one less moving part. Default proposed: private
   repository, because the loop's own history is evidence for 04.
2. **Do GROW, MOLT and HARDEN belong here.** The valuable phases and the three that write.
   Default proposed: design all three now, ship them together at S2 after `serve` has run
   for a month — a loop that adds without shedding is worse than one that does neither,
   and a round nobody stamps is a round nobody can roll back to.
3. **HUNT before 0.5.** `crab hunt` is a 0.5 feature. Until then HUNT reads a prey list from the
   maw's config — the twenty prey in [05-self-feeding.md](05-self-feeding.md) are already such
   a list.
4. **Where the loop sits in the roadmap.** Proposed: Track A after 0.3, as the bridge into Track
   B, since `GROW`, `MOLT` and `HARDEN` all need 0.3's pull-request machinery.
