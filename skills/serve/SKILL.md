---
name: serve
description: Write and create issues for approved nutrients with provenance and deduplication markers, using crab serve. Use when serving a menu, writing the why and how of a nutrient, explaining the provenance footer, or checking why a nutrient was skipped.
---

# Serve nutrients

`crab serve` creates the issues; you write the two sentences that make them worth reading.

## Notes file

Before serving, write `notes.json` with one entry per nutrient you keep:

```json
[
  {
    "id": "crab:ci:ci.cache",
    "why_for_host": "Every CI run reinstalls the dependencies; the prey's cache step cuts its lint job to 12 seconds.",
    "how": "Add `enable-cache: true` to astral-sh/setup-uv in ci.yml, keyed on uv.lock; run it on one PR before enabling it for the matrix."
  }
]
```

Rules for `why_for_host` and `how`:

- Name a concrete effect in this repository (time, risk, a class of bugs), not a generic benefit.
- `how` names files and tools of the host, adapted to its toolchain; it is the first step, not a
  full plan.
- Never paste prey text unless the license mode is `COPY`; even then cite the path.
- Optional fields: `title` (if the generated one is off), `artifact`, `effort`, `risk`.

## Commands

```bash
crab serve <prey> --host . --ids id1,id2 --notes notes.json --as dry-run   # previews
crab serve <prey> --host . --ids id1,id2 --notes notes.json --as issue     # creates
crab serve <prey> --host . --top 5 --as dry-run                            # top of the menu
```

- Dry-run is the default; show the previews and get a confirmation before `--as issue`.
- `serve.issues: off` in `.crab.yml` blocks creation; `auto` allows it in CI without asking.
- The host must have a GitHub `origin` remote; `gh` must be authenticated.

## Whose name the issues carry

Issues go into **the host**, so serving into another repository means digesting that repository
as the host: `crab serve <prey> --host ../their-repo`. It needs a working tree, because the
comparison is against real files.

By default the issues are filed as whoever `gh` is logged in as. `serve.token_env` in that
repository's `.crab.yml` names an environment variable holding a token to use instead — a
GitHub App installation token, or a machine account's — and then the issues carry the crab's
name rather than a person's. The first log line of `--as issue` says which identity is in use;
read it back to the user before confirming.

Opening an issue needs no special permission on a public repository, but **creating a label
needs write access**. Where the crab cannot create its label it says so once and serves without
labels; deduplication is unaffected, because it reads the `crab:<id>` marker in the body, not
the label.

## What an issue contains

See `references/issue-template.md`. Every issue carries a hidden `<!-- crab:<id> -->` marker
(deduplication across runs and machines), the `hungry-crab` label, and a provenance footer with
prey, commit, license and mode. The ledger records `served` with the issue URL.

## Skips and their meaning

| Reason | Meaning |
|---|---|
| `ledger: rejected (...)` | decided earlier; do not re-propose unless the user asks |
| `ledger: served <url>` | already an issue; link to it |
| `issue #N exists (open)` | found by marker on GitHub; the ledger is updated |
| `not in the menu` | run `crab compare` again or check the id |

Pull-request serving (`--as pr-branch`) arrives with milestone 0.3; until then serve `pr`
nutrients as issues and say so in the report.
