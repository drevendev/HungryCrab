# Benchmarks

Deterministic measurements that later versions, and the Evolving Crab, are compared against.

## B1 · Menu benchmark

```bash
uv run python benchmarks/menu_benchmark.py
```

Does the deterministic layer still find what a human accepted? Frozen prey and maw digests live
under `menu/`, and `menu/golden.yml` is the maintainer's own verdict on them, lifted from
`.crab/ledger.json`: ten nutrients that were served, twenty-nine that were rejected, each with
the reason kept. No model, no network, no clock: two runs of this produce the same numbers.

| Metric | Meaning | At introduction |
|---|---|---|
| `recall_must@30` | share of accepted nutrients that reach the top 30. A floor | 1.00 (10/10) |
| `noise@30` | share of rejected nutrients that still reach the top 30. A ceiling | 0.66 (19/29) |

`noise@30` is not zero and is not meant to be: several of those cards are reasonable proposals
that this particular maw did not want. It may only go down. Both thresholds gate pull requests
through `tests/test_menu_benchmark.py`, so the number is measured on every platform in the
matrix; moving a threshold to make a change pass defeats the benchmark.

Adding a pair: digest the prey and the maw, copy the eleven JSON files each into
`menu/prey/<slug>/` and `menu/maw/`, and add the verdicts to `golden.yml`. Only prey the
maintainer has actually judged belongs here.

## Digest benchmark

```bash
uv run python benchmarks/run.py pallets/click colinhacks/zod
```

Clones or refreshes each prey first (network time stays out of the measurement), digests it with
`--force`, and writes `results/<date>.json` with seconds, token estimates, size and whether the
milestone limits held: at most 120 seconds and 30,000 Markdown tokens per digest (see
[02-mvp.md](../docs/design/02-mvp.md), acceptance criteria). The exit code is non-zero when a
limit is exceeded, so the script can gate a release.

Results are per machine; the JSON records OS, Python and the crab version. Keep one file per day
and commit it when the numbers are worth remembering (a new miner, a big prey, a regression).

## Reference prey

| Repository | Why |
|---|---|
| `pallets/click` | Python, BSD-3-Clause, 3k+ commits, many tags: history and release cadence |
| `colinhacks/zod` | TypeScript pnpm monorepo, MIT, AI configs, rich issues: architecture and issues |
