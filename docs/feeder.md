# Feeder: a menu without an agent

`crab eat <prey> --deterministic --maw .` runs reconnaissance, acquisition, all thirteen
miners and maw-aware comparison. It calls no model, executes no prey code, creates no issues
or pull requests and leaves the ledger untouched. Existing ledger decisions are read to hide
already decided nutrients. The output is a bundle an agent or a human can read later.

## Run locally

```bash
crab eat pypa/pipx --deterministic --maw . --out /tmp/crab-meal
# PowerShell: choose an empty directory outside the sources, e.g. C:/Temp/crab-meal
```

Omit `--out` to keep a unique bundle in the maw's cache. A nonempty output directory is
refused before acquisition. Local repository paths work with API reconnaissance skipped.
`--json` prints the bundle location and manifest as one JSON object; progress goes to stderr.

Defaults: one default branch, a `90d` history window, 100 recent issues plus up to 50
additional reaction-ranked issues, normal miner depth, top 30 cards and wiki acquisition.
`--no-shallow --since all` requests complete history; `--issues 0` skips issue acquisition;
`--no-wiki` skips both wikis. A prey with no commits in the window falls back to a depth-one
tree snapshot. A changed catch policy is applied to an existing cache too. `--wiki-dir <path>`
supplies an independent local Git wiki checkout for local fixtures.

Both digests are regenerated after evidence refresh. Failed/blocked miners fail the job;
inventory or wiki visibility loss fails unless `--allow-loss` is explicit. Failed runs never export
a success bundle. Wiki reads cap page counts and page bytes, recording any loss. Markdown
budgets follow the maw's `.crab.yml`: `warn`, `enforce` or `off`;
full JSON survives an enforced reading budget.

GitHub's repository `size` must fit `--max-repo-kb` (307200 by default) before cloning.
This is a preflight, **not a hard download or disk quota**: the estimate and independent wiki
storage cannot prove an upper bound. LFS smudging stays disabled. The reusable job has a
20-minute timeout; choose a suitable runner and smaller prey for stricter resource limits.

## Install in CI

The composite action installs only the trusted Hungry Crab checkout with locked dependencies
in its own environment. It never installs the prey or the maw's packages. Check out the maw
first; Linux and Windows hosted runners are supported.

```yaml
name: Weekly menu
on:
  workflow_dispatch:
  schedule:
    - cron: '17 3 * * 1'
permissions:
  contents: read
jobs:
  eat:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: drevendev/HungryCrab@master # Pin a reviewed commit SHA in production.
        id: feeder
        with:
          prey: pypa/pipx
          artifact-name: crab-meal
```

Or replace that job with a call to the reusable workflow:

```yaml
jobs:
  eat:
    uses: drevendev/HungryCrab/.github/workflows/feeder.yml@master
    with:
      prey: pypa/pipx
```

Pin a reviewed commit SHA for reproducibility. The reusable workflow checks out the caller
as the maw with full history and resolves the action from its own repository at that same
running commit (`$/`). It exports `artifact-id`, `artifact-url` and `candidate-count`.
The composite also exports `bundle-path` and `menu-path` for later steps in the same job.
Use distinct artifact names for matrix jobs. Retention defaults to 14 days.

Both interfaces expose `since`, `shallow`, `issues`, `wiki`, `depth`, `top`, `max-repo-kb`,
`allow-loss`, `artifact-name` and `retention-days`. The composite additionally accepts `maw`,
`out`, `cache-dir` and `token`. The reusable accepts `runner` (default `ubuntu-latest`) and
optional secret `prey-token`. Paths/default caches live in runner temporary storage.

REST uses the job token directly, without `gh` or `gh auth`. A private prey outside the
caller needs an explicit read token. Git uses a process-local, GitHub-scoped HTTP header,
never credentials in URLs or stored configuration. Issue acquisition also needs read access
to issues; set `issues: 0` for a contents-only token. Public prey need no write permissions.
GETs retry transient network/5xx failures and rate limits with bounded backoff, honour
`Retry-After`/reset times, and fail with a retry hint when a delay exceeds 60 seconds.
ETag/Last-Modified responses are revalidated. Cached API bodies are partitioned by credential
and never uploaded.

## Read and consume a bundle

```text
crab-meal/
  feeder.json                 run provenance, warnings and no-write declaration
  menu.md, menu.json          ranked cards, modes and policy reasons
  gap.md, meal.json           comparison and relative digest references
  prey-digest/                declared miner artifacts and manifest
  maw-digest/                 declared miner artifacts and manifest
```

Download from the Actions run or use `actions/download-artifact` with the returned ID.
Start with `menu.md`; JSON contains all cards, including those outside the top N.
Wiki page names, counts and headings are in `wiki.json`/`wiki.md`; body prose stays in the
checkout. The independent wiki commit participates in digest identity and menu provenance.
A maw wiki satisfies the structured documentation gap just as a docs directory does.

To act, explicitly run `crab compare <prey> --maw .` in today's maw, review/select cards,
then use the existing `crab serve` and clean-room protocol. That separate step records the
ledger and may create issues or PRs. Scheduled Feeder stops at the artifact. Evolving Crab's
CONSUME phase can read `menu.json` directly without a model or repository mutations.

## Strict mode

`.crab.yml` `mode: strict` is enforced by the license layer during comparison. `COPY` code
becomes clean-room `REIMPLEMENT`; configs and templates retain `COPY`. Source code evidence
(including source tests) takes precedence over a configuration category. Architecture,
history, code and unknown categories require reimplementation. Existing `HUMAN`,
`IDEAS_ONLY`, `REIMPLEMENT` and `COPY_FILE` verdicts are never widened. Material, maw mode
and downgrade reason travel in the menu and served issue's license trace. Models must not
invent or reverse these verdicts.

`Feeder smoke` exercises the composite on Linux/Windows and the reusable workflow against
a live public prey, checks both digests and verifies a clean maw. It runs separately from
pull-request tests so provider outages do not block fixture checks. Tests cover wiki refresh
at an unchanged prey SHA, conditional requests, retries, strict/normal menus, artifact safety,
visibility gates and unchanged ledgers.
