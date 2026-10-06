# Scheduled Crab

`crab loop` implements [design 07](design/07-scheduled-crab.md): one model phase per local
scheduler wake-up, with state on disk. It never runs a model, starts a scheduler or merges a PR.
The `/crab:loop` skill supplies judgement; the CLI validates receipts and owns publication.
Live rollout and measured phase costs are tracked in [#242](https://github.com/drevendev/HungryCrab/issues/242).

## Configure and initialize

The maw owner adds this block to `.crab.yml`:

```yaml
loop:
  cadence: daily
  autonomy: serve          # read | serve | work
  work_authorized: false  # explicit owner consent required for work and tags
  prey: [pypa/hatch, astral-sh/ruff]
  budget:
    phases_per_day: 4
    prey_per_round: 2      # at most 3
    open_issues_max: 10    # open artifacts starting with a crab marker
    open_prs_max: 2
  max_attempts: 3
  lease_minutes: 60
  protected: [src/licensing/**]  # additive; defaults cannot be removed
  release_files: [CHANGELOG.md, pyproject.toml, src/example/__init__.py]
```

All loop keys and limits are validated. Default protected paths are `.github/**`, `.git/**`,
license files, `.crab.yml` and loop state. Set both `autonomy: work` and `work_authorized: true`
only after the S0/S1 observation period. A control repository cannot grant work consent.
Work also needs a persistent ledger (`repo` or `cache`).

```bash
crab loop init --maw /path/to/maw
crab loop status --maw /path/to/maw --json
crab loop next --maw /path/to/maw --json
```

Own-maw state is `.crab/loop.json`. For foreign maws pass `--control /path/to/control` to every
command: each maw gets `.crab/loops/<identity>.json`, without loop state in the foreign maw.
Optional `.crab/maws.yml` in the control repository configures its targets:

```yaml
maws:
  - repo: example/maw
    path: ../maw           # relative to the control repository
    autonomy: serve       # narrows the maw policy; never raises it
    cadence: weekly
    prey: [pypa/hatch]
```

Keep private goals, costs and receipts in a private control repository. Commit consistent state
snapshots for portability; ignore disposable `.lock` carriers. Do not reset history on updates.

## Scheduler and recovery

Configure an existing local scheduler to invoke `/crab:loop --maw <absolute-path>` once per
wake-up. Cadence belongs to that scheduler. To avoid model costs while blocked, a harness can
call `next` first and invoke a model only for `ready: true`. Pass that existing lease to the
model; do not call `next` again inside that session. Direct skill invocation combines these steps.

`next` reserves a daily phase and returns `active.token`, phase/round, current prey, input paths
and budget left. It never advances. `record` advances only after validating evidence. A live
lease blocks competing schedulers; expiry records interrupted work and retries the same phase
up to `max_attempts`. Actual attempts, including failures/crashes, consume slots. UTC midnight
resets the daily allowance. Paused, provider-waiting, policy-blocked and budget-blocked calls
reserve nothing. Provider failures fail closed. State writes are atomic; OS locks release on death.

CI/merge waits read provider truth. Changed heads need human attention; an empty CI suite is
pending. TRIAL needs local tests and passing CI at the recorded GROW head. Failed MOLT proposals
are closed and become lessons. Every merge remains human.

```bash
crab loop record --token TOKEN --phase crave --result ok --receipt receipt.json
crab loop pause                  # one-command kill switch inside the maw
crab loop resume
crab loop acknowledge           # human-only retry acknowledgement
crab loop acknowledge --skip-work  # human ends implementation at a read/serve boundary
crab loop acknowledge --drop-pr https://github.com/example/maw/pull/1  # reject a failed GROW
crab loop metrics --json         # wall time and optional measured tokens/cost history
```

Pass the same maw/control arguments as `next`. Pause survives restart and blocks in-flight
effects/recording; resume preserves an unexpired lease. Acknowledgement cannot discard an
active lease or skip hardening of landed changes. Read stops before SERVE; serve stops before
GROW. A human can skip implementation, then TASTE and empty MOLT/HARDEN finish the round.
Subsequent rounds start at HUNT and use the last TASTE goal and lesson.
TRIAL can record a reasoned skip with `{"drop_pr": "<recorded-GROW-URL>"}`; the CLI closes that
unchanged, unmerged head and carries a lesson forward. A failed CI block can be dropped through
human acknowledgement. A human acknowledgement after a closed release PR starts a new
publication revision, retaining the previous attempt in history.

## Receipts and publication

Receipts are JSON objects. Optional `tokens` is a non-negative integer and `cost_usd` a finite
non-negative number. Omit unknown measurements; do not substitute estimated costs.

| Phase | Required output |
|---|---|
| CRAVE | `{"goal": "Improve test isolation"}` |
| HUNT | `{"prey": ["pypa/hatch"], "rationale": "Relevant tests"}` |
| EAT | `{"meal": "/absolute/meal.json", "notes": "/absolute/notes.json"}`; notes map real menu IDs to why/how. Fresh meal/menu, matching prey/maw commits, intact artifacts and healthy coverage are required. |
| SERVE | `{"urls": ["https://github.com/example/maw/issues/1"]}` from `loop serve`; skip with a reason when empty. |
| GROW | Existing serve implementation receipt; record ok after the CLI stores the PR. |
| TRIAL | `{"tests_passed": true}`; provider CI independently must pass. |
| TASTE | `{"lesson": "Small fixtures exposed the bug", "goal": "Improve cache isolation"}`; goal is optional. |
| MOLT | Publish receipt below, then record ok. Failure closes the proposal and advances to HARDEN with its lesson. |
| HARDEN | Publish receipt below; after merge, tag and record ok. |

```bash
crab loop serve --token TOKEN --prey pypa/hatch --id crab:tests:tests.unit
# GROW uses the same command with an existing clean-room or COPY receipt:
crab loop serve --token TOKEN --prey pypa/hatch --id crab:tests:tests.unit --receipt impl.json
crab loop publish --token TOKEN --head FULL_COMMIT_SHA --title "refactor: molt helper" --body-file pr.md --receipt molt.json
crab loop tag --token TOKEN
```

Each effect rechecks open-artifact caps. GROW publishes one served nutrient per round, retaining
license, hunger, attribution, clean-room and secret gates. MOLT/HARDEN require an immutable
local commit containing the current default branch and exact declared paths. They scan the full
payload before effects, use deterministic branches/markers, reconcile PRs, and refuse symlinks,
submodules, type changes, protected paths, ambiguous push targets and unexpected existing content.
Dirty working-tree files are never included. Git publication runs no repository hooks.

MOLT receipt:

```json
{"changed_paths": ["src/redundant.py", "src/app.py"],
 "checks": {"tests_passed": true, "lint_before": 0, "lint_after": 0,
            "coverage_before": 81.2, "coverage_after": 81.4, "public_surface_added": false},
 "unreachable": {"src/redundant.py": "Only caller removed; reference scan and tests show no callers"}}
```

Only paths landed this round may change. Deletions need a path introduced this round and explicit
unreachability evidence. Measurements/proof are attestations for human review: the CLI verifies
bounds and Git/provider path history, not semantic unreachability of arbitrary programs. Retain
the underlying tests and measurements with the PR.

HARDEN receipt:

```json
{"changed_paths": ["CHANGELOG.md", "pyproject.toml"], "previous_version": "0.3.1",
 "version": "0.4.0", "contract_changed": false}
```

HARDEN requires all round PRs merged or closed, a minor bump for nutrients or patch for MOLT
alone, and CHANGELOG.md tracing all landed URLs/nutrient IDs. Other paths must be authorized
release files containing the declared before/after versions. Contract changes need human planning.
Publication releases the lease and waits without advancing. After human merge and green CI at
the exact release head, a later HARDEN lease creates `v<version>` at the verified merge commit,
refuses conflicting tags, and records success. It never tags an unmerged release or failed CI.

Hard byte/disk quotas remain in [#236](https://github.com/drevendev/HungryCrab/issues/236).
Use bounded prey for rollout. Host hooks, branch protection and sandboxing are the runtime
boundary against an agent bypassing the CLI; their live verification is separate from this protocol.
Loop-specific host enforcement is tracked in [#243](https://github.com/drevendev/HungryCrab/issues/243).
