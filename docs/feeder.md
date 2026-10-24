# Feeder: a menu without an agent

`crab eat <prey> --deterministic --maw .` runs reconnaissance, acquisition, all fifteen
miners and maw-aware comparison. It calls no model, executes no prey code, creates no issues
or pull requests and leaves the ledger untouched. Existing ledger decisions are read to hide
already decided nutrients. The output is a bundle an agent or a human can read later.

## Run locally

```bash
crab --cache-dir /mnt/crab-quota/cache eat pypa/pipx --deterministic --maw . \
  --acquisition-volume /mnt/crab-quota --out /tmp/crab-meal
# /mnt/crab-quota is a separately provisioned fixed-capacity filesystem (see below).
```

Omit `--out` to keep a unique bundle in the maw's cache. A nonempty output directory is
refused before acquisition. Local repository paths work with API reconnaissance skipped.
`--json` prints the bundle location and manifest as one JSON object; progress goes to stderr.

Defaults: one default branch, a `90d` history window, 100 recent issues plus up to 50
additional reaction-ranked issues, normal miner depth, top 30 cards and wiki acquisition.
`--no-shallow --since all` requests complete history; `--issues 0` skips issue acquisition;
`--no-wiki` skips both wikis. A prey with no commits in the window falls back to a depth-one
tree snapshot. A changed catch policy is applied to an existing cache too. `--wiki-dir <path>`
supplies an independent local Git wiki repository for local fixtures. Commit local wiki edits
before digesting: wiki evidence is read only from the recorded commit, including its raw byte
counts. Dirty and untracked wiki files are excluded from the snapshot.

Both digests are regenerated after evidence refresh. Failed/blocked miners fail the job;
inventory or wiki visibility loss fails unless `--allow-loss` is explicit. Failed runs never export
a success bundle. Wiki reads cap the tree listing at 8 MiB, individual blobs at 2 MiB and page
counts at 2,000 (normal) or 10,000 (deep), recording any loss. Oversized blobs retain their names
and recorded sizes without reading their bodies; missing/unreadable blobs lose healthy coverage.
Markdown
budgets follow the maw's `.crab.yml`: `warn`, `enforce` or `off`;
full JSON survives an enforced reading budget.

GitHub's repository `size` must fit `--max-repo-kb` (307200 by default) before cloning.
This remains an estimate. Remote acquisition additionally requires `--acquisition-volume`,
a dedicated Linux block-device filesystem whose **backing-device capacity** fits `--max-acquisition-disk-bytes`
(1 GiB by default). Put `--cache-dir` inside that volume. The kernel bounds all Git objects,
checkout files, staging, temporary files and filesystem metadata together, including old caches
retained during replacement. A directory on a larger disk, a free-space check or a size sampler
does not satisfy this boundary. Raise the capacity limit and provision a larger volume together
when existing evidence plus a replacement will not fit. Provisioning is an owner/runner operation;
the CLI never mounts disks or elevates privileges.

`--max-download-bytes` defaults to 300 MiB for the **whole invocation**, including all prey,
prey wikis and the maw wiki. A local CONNECT relay bounds bytes forwarded from GitHub/GitLab
to Git, including TLS overhead, reference discovery and retries. TLS remains end to end; the
relay neither decrypts content nor holds credentials. Reads permit at most one sentinel byte
beyond the allowance before refusing; no excess byte is forwarded. Kernel socket buffers and
wire traffic are not metered. Provider REST requests retain their separate response limits.
SSH, local Git transports and redirects are refused for remote budgeted acquisition, and inherited
proxy bypasses and Git object/temp path overrides are cleared. There is no unmetered fallback.

The hard acquisition adapter currently supports Linux block-device mounts with process-group cancellation;
other hosts fail closed for remote Feeder acquisition. Local, already acquired repository paths
remain usable on Windows with `--no-wiki`. Each prey's acquisition transaction stages its
provider preflight/API/HTTP metadata, main repository, prey wiki and requested maw wiki before
publishing any of them. Download/disk exhaustion or handled cancellation preserves the previous
complete cache; cancellation during publication rolls back earlier cache moves. Unrelated digest
generations and HTTP entries are retained. An interrupted rollback preserves its backup and
path journal for owner recovery. Hard process/machine termination cannot run cleanup handlers;
inspect retained staging/backups before retrying on a persistent volume.
Multi-prey invocations share one byte allowance but commit successful acquisition transactions
per source: an earlier complete source may remain cached if a later source fails. The overall
failed invocation creates no success bundle. Existing caches share the same filesystem cap,
so choose a private volume with enough
capacity for both old and staged evidence. New budgeted acquisitions are fresh complete object
transfers under the requested shallow/history policy; no partial-clone filter or promisor is used.
No later lazy object download is needed. Wikis have no checkout. LFS smudging remains disabled.
RAM/CPU limits belong to the runner; a disk quota is not a memory bound for Git delta processing.

## Install in CI

The composite action installs only the trusted Hungry Crab checkout with locked dependencies
in its own environment. It never installs the prey or the maw's packages. Check out the maw
first. Remote acquisition uses Linux hosted runners by default: the composite action provisions
an ephemeral fixed-size ext4 loop image with `mkfs.ext4` and noninteractive `sudo mount`,
mounts it `nosuid,nodev,noexec`, and unmounts/removes it in an `always()` cleanup step. A runner
without those capabilities must supply its own `acquisition-volume`; an unsupported process
boundary still refuses remote acquisition. The existing local Windows path needs no volume.
To retain a cache between invocations, supply an owner-provisioned volume and a cache inside it;
the action cleans up only volumes it created itself.

**Marketplace preparation:** the [Feeder listing and dogfood roadmap](marketplace/README.md)
tracks public/limited-private acceptance, exact release pins, truthful evidence and
owner-mediated Marketplace publication. The `@master` example below illustrates the Action
interface only: a production installation and a proof of Marketplace usage require a reviewed
immutable Action SHA, tested from a separate repository. Planned examples are not executed
proof. See the [public trial matrix](marketplace/dogfood.md) and
[evidence contract](marketplace/evidence.md) before making a promotional claim.

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
`acquisition-volume`, `max-download-bytes`, `max-acquisition-disk-bytes`,
`allow-loss`, `artifact-name`, `retention-days` and `allow-private-artifact`. The composite additionally accepts `maw`,
`out`, `cache-dir` and `token`. The reusable accepts `runner` (default `ubuntu-latest`) and
optional secret `prey-token`. Paths/default caches live in runner temporary storage.

REST uses the job token directly, without `gh` or `gh auth`. A private prey outside the
caller needs an explicit read token. The meal artifact carries both digests: the prey's file
tree, issue titles and excerpts and commit subjects, and the maw's file tree, commit subjects
and wiki headings. Artifacts of a public repository are downloadable by anyone signed in to
GitHub. So when the calling repository is public (or its visibility is not in the event
payload), the Feeder refuses before any work unless both sides are confirmed public: the forge
confirms the prey is public (a local prey path counts as unconfirmed), and the maw directory is
the root of a git checkout whose `origin` is either the public calling repository or a
repository the forge confirms is public, or it is a directory of the public calling repository's
own checkout that holds tracked files (a monorepo package such as `maw: packages/app`). A maw
with no recognisable `origin`, an untracked directory, or one the `token` cannot see, counts as
unconfirmed. `allow-private-artifact: true` uploads it anyway.
For public or unknown callers, a maw containing a separate nested Git checkout or a populated
submodule is also refused before digest, even when that checkout's origin is public. Remote
visibility cannot vouch for local untracked, ignored, modified or unpublished files. Selecting
a gitlink itself as the maw does not bypass this boundary. Empty, uninitialised gitlinks and
ordinary build output without Git metadata remain allowed. Use `allow-private-artifact: true`
only when you intend to expose those local bytes in the artifact.
Git uses a process-local, GitHub-scoped HTTP header,
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
Wiki page names, counts and headings are in `wiki.json`/`wiki.md`; body prose stays in Git
objects. Page counts include Markdown aliases, Creole, RDoc, Pod, AsciiDoc, MediaWiki, Org,
Textile and reStructuredText. Heading extraction supports Markdown only. The independent wiki
commit participates in digest identity and menu provenance.
A maw wiki satisfies the structured documentation gap just as a docs directory does.

To act, explicitly run `crab compare <prey> --maw .` in today's maw, review/select cards,
then use the existing `crab serve` and clean-room protocol. That separate step records the
ledger and may create issues or PRs. Scheduled Feeder stops at the artifact. Evolving Crab's
CONSUME phase can read `menu.json` directly without a model or repository mutations.

## Strict mode

`.crab.yml` `mode: strict` is enforced by the license layer during comparison. `COPY` and
`COPY_FILE` code become clean-room `REIMPLEMENT`; recognized configs and templates retain
their legal copy mode. Source code evidence (including source tests) takes precedence over a
configuration category. Shell scripts, unknown file types, missing path evidence, architecture,
history, code and unknown categories require reimplementation. Known configuration names and
formats are explicit exceptions. Existing `HUMAN`, `IDEAS_ONLY` and `REIMPLEMENT` verdicts are
never widened. Material, maw mode and downgrade reason travel in the menu and served issue's
license trace. Materialization rechecks each source and destination path against strict policy
before reading or copying content. Models must not invent or reverse these verdicts.

`Feeder smoke` exercises the composite on Linux/Windows and the reusable workflow against
a live public prey, checks both digests and verifies a clean maw. It runs separately from
pull-request tests so provider outages do not block fixture checks. Tests cover wiki refresh
at an unchanged prey SHA, conditional requests, retries, strict/normal menus, artifact safety,
visibility gates and unchanged ledgers.
