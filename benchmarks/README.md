# Benchmarks

Deterministic measurements that later versions, and the Evolving Crab, are compared against.

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
