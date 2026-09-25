---
name: serve
description: Write and create issues or guarded pull requests for approved nutrients with trace and deduplication markers, using crab serve. Use when serving a menu, writing the why and how of a nutrient, explaining the trace footer, or checking why a nutrient was skipped. Do not use for ordinary issue or pull-request authoring unrelated to a Hungry Crab nutrient, menu, or implementation receipt.
---

# Serve nutrients

`crab serve` creates issues or, for completed clean-room REIMPLEMENT nutrients, guarded pull requests. You write the two sentences that make them worth reading.

## Notes file

Before serving, write `notes.json` with one entry per nutrient you keep:

```json
[
  {
    "id": "crab:ci:ci.cache",
    "why": "Every CI run reinstalls the dependencies; the prey's cache step cuts its lint job to 12 seconds.",
    "how": "Add `enable-cache: true` to astral-sh/setup-uv in ci.yml, keyed on uv.lock; run it on one PR before enabling it for the matrix."
  }
]
```

Rules for `why` and `how`:

- Name a concrete effect in this repository (time, risk, a class of bugs), not a generic benefit.
- `how` names files and tools of the maw, adapted to its toolchain; it is the first step, not a
  full plan.
- Never paste prey text unless the license mode is `COPY`; even then cite the path.
- Optional fields: `title` (if the generated one is off), `serve_as`, `effort`, `risk`.

## Commands

```bash
crab serve <prey> --maw . --ids id1,id2 --notes notes.json --as dry-run   # previews
crab serve <prey> --maw . --ids id1,id2 --notes notes.json --as issue     # creates issues
crab serve <prey> --maw . --top 5 --as dry-run                            # top of the menu
cat receipt.json | crab serve <prey> --maw . --ids id1 --notes notes.json --as pr-branch
crab spec crab:ci:ci.cache      # the maw-relative path of a nutrient's clean-room specification
```

- Dry-run is the default; show the previews and get a confirmation before `--as issue`.
- `serve.issues: off` in `.crab.yml` blocks issue creation; `auto` allows it in CI without asking.
- `serve.prs: off` blocks pull-request publication; `ask` requires explicit `--ids`, while `auto`
  permits ranked selection such as `--top`.
- Pull-request mode accepts the strict JSON implementation receipt returned by
  `crab-cleanroom-impl` on **stdin**. For a batch, concatenate the complete JSON documents with
  whitespace between them. The receipt stream is transport only: Hungry Crab does not persist it
  as a second PR state store.
- Pull-request mode publishes a card only when it is REIMPLEMENT, marked `serve_as: pr`, and has
  a receipt; every other selected card is skipped with its reason, so `--top` serves what it can.
  Each published nutrient has exactly one receipt, whose own `nutrient_id` binds it to the menu
  card, and its specification at the path `crab spec <id>` prints, which is read, scanned and
  carried in the pull request. Duplicate, malformed, stale, non-UTF-8, missing or maw-escaping
  declared files, and a missing specification, fail closed before provider effects.
- `serve_as` is honoured in every mode: an `idea` card (`hunger: <category>: ideas-only`, or an
  issue lesson) is skipped unless its id is passed explicitly, and then only as an issue; an
  `issue` card is never published as a pull request.
- COPY pull-request serving remains blocked until its attribution/materialization contract lands;
  do not route it through the clean-room REIMPLEMENT path.
- The maw must have a GitHub `origin` remote; `gh` must be authenticated. Pull-request publication
  also requires the maw to be the root of a git repository with its `origin` fetchable: files are
  staged relative to that root, so `--maw packages/app` is refused rather than published wrong.
- The receipt on stdin is read as UTF-8 bytes, whatever the console's own code page is.

## Pull-request transaction

`--as pr-branch` does not publish the current dirty diff. It consumes only the exact paths in the
clean-room receipt, freezes their current UTF-8 bytes, and then follows the guarded transaction:

1. prepare **all** selected nutrients into immutable payloads — the receipt's files plus the
   specification at the path `crab spec <id>` prints, without which the nutrient is refused;
2. scan every file plus PR title/body for possible secrets;
3. reconcile marker-bearing PRs already present on GitHub;
4. only then create/reuse the deterministic nutrient branch and create the PR;
5. write the provider URL to the ledger after GitHub returns it.

The branch effect uses a detached temporary worktree and stages the frozen bytes without repository
clean filters. A rerun after a successful PR but failed ledger save reconciles the existing PR by
its opening `<!-- crab:<id> -->` marker instead of creating a duplicate. `max_prs_per_run` counts
new PRs only; reconciliation does not spend creation budget.

## Whose name the artifacts carry

Issues and pull requests go into **the maw**, so serving into another repository means digesting
that repository as the maw: `crab serve <prey> --maw ../their-repo`. It needs a working tree,
because the comparison and guarded PR publication operate against real maw files.

By default artifacts are filed as whoever `gh` is logged in as. `serve.token_env` in that
repository's `.crab.yml` names an environment variable holding a token to use instead — a GitHub
App installation token, or a machine account's — and then artifacts carry the crab's name rather
than a person's. The first log line of an effectful serve says which identity is in use; read it
back to the user before confirming.

Opening an issue needs no special permission on a public repository, but **creating a label needs
write access**. Where the crab cannot create its label it says so once and serves without labels;
deduplication is unaffected, because it reads the `crab:<id>` marker in the body, not the label.

## What an issue contains

See `references/issue-template.md`. Every issue carries a hidden `<!-- crab:<id> -->` marker
(deduplication across runs and machines), the `hungry-crab` label, and a trace footer with prey,
commit, license and mode. The ledger records `served` with the issue URL.

Pull requests carry the same opening marker and trace, plus the clean-room implementation summary
and a link to the specification. The files are exactly the receipt-declared, hash-verified
publication payload plus the specification itself; unrelated dirty maw files are not discovered
or added.

## Skips and their meaning

| Reason | Meaning |
|---|---|
| `ledger: rejected (...)` | decided earlier; do not re-propose unless the user asks |
| `ledger: served <url>` | already served; link to it |
| `issue #N exists (open)` | found by marker on GitHub; the ledger is updated |
| `pull request exists <url>; ledger reconciled` | provider already has the PR; local truth was repaired without creating another |
| `serve.max_prs_per_run reached (N)` | creation budget is exhausted; an already-existing PR could still have reconciled |
| `not in the menu` | run `crab compare` again or check the id |
| `serve_as: idea` | the hunger block or the card itself keeps it an idea; pass the id explicitly to file it as an issue anyway, never as a pull request |
| `serve_as: issue` | (pull-request mode) the card is an issue, not a pull request; serve it with `--as issue` |
| `license mode COPY has no pull-request path yet` | COPY pull requests wait for `crab attribution` (#70); serve the card as an issue |
| `no clean-room receipt` | pull-request mode found no receipt for the card on stdin; run the clean-room protocol first |
