# 08 · Budgets and the Feeder

Two decisions that were made once, early, for one kind of user, and have been quietly applying to
everyone since. Both are written down here because they change who the crab is for.

## 1. A token budget is a reading plan, not a wall

### What the number is

A token is the unit a language model reads: a BPE fragment, roughly four characters of English.
The crab links no tokenizer; [`tokens.py`](../../src/hungry_crab/tokens.py) divides characters by
`CHARS_PER_TOKEN = 3.5`. Every figure in a manifest is therefore an **estimate**, and it is worse
than that sounds: the ratio is calibrated for Latin-script code and prose, and Cyrillic, CJK or
base64 move it far enough that "3 500 tokens" can be 2 000 or 7 000.

### What was enforced, and what only looked enforced

| Budget | Value | Status before this document |
|---|---|---|
| Per Markdown file | 3 500 (`normal`), 12 000 (`deep`) | **Enforced.** `MdDoc.render(budget)` drops lines from the least important section until the estimate fits |
| Whole digest | 30 000 | **A boolean.** `over_budget` in the manifest. Nothing trims, nothing warns, nothing fails |

A digest has nine Markdown files, so the worst case at `normal` depth is 31 500 tokens and at
`deep` depth 108 000 — three and a half times a ceiling the project describes as a limit. It has
never been hit only because the miners cap their own tables long before a file reaches its budget:
this repository digests to 6 088 tokens, `colinhacks/zod` to 10 873.

### The decision

**The per-file budget stops dropping content and starts paging.** When a document does not fit, it
is split — `history.md`, `history.2.md` — each part carrying a header that names the next. Nothing
is lost, and the reader decides how far to read. Dropping the tail of a section was only ever
defensible because the JSON twin kept everything, which is an argument for why the loss is
survivable, not for why it should happen.

**The whole-digest budget becomes a policy, because the two users of the crab want opposite
things.**

| Policy | For | Behaviour on exceeding |
|---|---|---|
| `warn` (default) | an agent session: Claude Code, Codex | The digest is complete. The manifest says how far over it went, and the skill asks the operator whether to read it all, read the top of the menu only, or re-digest shallower |
| `enforce` | the Evolving Crab, and any budgeted loop | Pages are dropped by priority until the total fits, and the manifest records exactly what was dropped |
| `off` | a human reading a report | No ceiling |

The Evolving Crab counts tokens because it pays for them out of a fixed budget per cycle, and a
phase that does not fit is a phase that fails. An interactive agent has no such constraint: it has
a context window far larger than 30 000 tokens, a human next to it, and the ability to read one
file, decide, and read another. Applying the organism's budget to the plugin was never a finding
about what a digest needs; it was one number written for one purpose and then inherited.

The skill's protocol changes accordingly: progressive disclosure stops being a suggestion in
`SKILL.md` and becomes what the artifact is shaped like.

## 2. The Feeder: digesting prey in CI, with no model

Everything up to the menu is deterministic. `sniff`, `catch`, `digest` and `compare` do not call a
model, are not allowed to execute prey, and already produce exactly the artifact an agent would
want to read later. Nothing about that requires an agent to be present while it happens.

So a repository should be able to install a job, name its prey, and find a menu waiting for it.

```yaml
# .github/workflows/crab.yml in the maw
on:
  schedule: [{cron: "0 3 * * 1"}]
  workflow_dispatch:
    inputs:
      prey: {description: "owner/repo", required: true}
jobs:
  eat:
    uses: drevendev/HungryCrab/.github/workflows/feeder.yml@v0
    with:
      prey: ${{ inputs.prey || 'pypa/pipx' }}
```

The job runs `catch → digest → compare`, uploads the meal (`menu.md`, `gap.md`, `menu.json`,
`meal.json`) plus the prey digest as a build artifact, and stops. A model reads it afterwards —
in an agent session, in a pull-request comment, or never. The ledger and the issues stay behind a
second, explicit step, because filing issues is a side effect and a scheduled job should not have
one by default.

This is not a new capability. It is milestone 0.6 (`composite GitHub Action`, exit criterion "the
crab runs on a schedule in a maw's CI without Claude Code") brought forward, and it is brought
forward for three reasons:

1. It is mostly packaging. The deterministic pipeline exists and is tested; what is missing is an
   `action.yml`, a reusable workflow, artifact upload and a single `crab eat --deterministic`
   entry point that chains the four commands.
2. It is the honest answer to "does the deterministic layer carry its weight". A menu produced
   with no model in the room is the whole thesis of the project, visible.
3. The Evolving Crab's CONSUME phase runs inside GitHub Actions and needs precisely this job.
   Building it at 0.6 would mean building it twice, or building track B on top of something that
   does not exist yet.

### What it costs

The prey has to be cloned on the runner, which is where the size limits in the backlog stop being
theoretical: a runner has a disk quota and a job timeout, and `--shallow --since` becomes the
default rather than an option. The GitHub API is called from a runner without `gh auth`, on the
job token, so the rate-limit and retry work is a prerequisite and not a nicety.
