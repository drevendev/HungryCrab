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

Adding a pair: digest the prey and the maw, copy the comparison JSON files each into
`menu/prey/<slug>/` and `menu/maw/`, and add the verdicts to `golden.yml`. Only prey the
maintainer has actually judged belongs here.

### Ranking pressure

```bash
uv run python benchmarks/ranking_benchmark.py
```

`ranking/pressure.json` freezes a separate synthetic pair producing 56 competing candidates.
It requires prior human-accepted generic priorities to reach the top 30. It is a ranking
regression test, not a new human usefulness corpus. Its negative control disables category
weights and must fail. The original B1 labels and thresholds remain unchanged; larger human
labeling is [#200](https://github.com/drevendev/HungryCrab/issues/200).

## B2 · Meal benchmark

`meal_benchmark.py` implements the frozen experiment protocol from
[06-benchmark.md](../docs/design/06-benchmark.md). It imports real runs from an operator-selected
common harness. It never launches a paid model, invents usage or judges the cards itself.

Create `spec.json` with the full maw SHA, prey `id`, public forge `slug`, full SHA,
`license_mode` ceiling and human-written `must` nutrient ids. Each arm needs `id`, exact `model`,
the same `harness` object (including a positive `token_ceiling`), its prompt path and a full
`crab_sha` for crab-assisted arms (null for baseline). Include a `rubric` path and `repeats >= 2`.
Model ids, golden ids, budget, prompts and rubric are chosen before any arm runs.

```json
{
  "maw": {"sha": "FULL_40_CHARACTER_MAW_SHA"},
  "prey": [{"id": "click", "slug": "pallets/click", "sha": "FULL_40_CHARACTER_PREY_SHA",
            "license_mode": "COPY", "must": ["crab:ci:ci.schedule"]}],
  "rubric": "rubric.md",
  "repeats": 2,
  "arms": [
    {"id": "baseline", "model": "EXACT_MODEL_ID", "crab_sha": null,
     "harness": {"name": "COMMON_HARNESS_REVISION", "token_ceiling": 100000},
     "prompt": "prompts/baseline.md"},
    {"id": "deep-bite", "model": "EXACT_MODEL_ID", "crab_sha": "FULL_40_CHARACTER_CRAB_SHA",
     "harness": {"name": "COMMON_HARNESS_REVISION", "token_ceiling": 100000},
     "prompt": "prompts/crab.md"}
  ]
}
```

Replace all placeholders with actual pins. Expand the arm matrix to the models and crab
versions under comparison. Relative asset paths resolve beside the spec.

```bash
uv run python benchmarks/meal_benchmark.py freeze spec.json sweeps/experiment
uv run python benchmarks/meal_benchmark.py record sweeps/experiment baseline.click.1 cards.json usage.json transcript.log
uv run python benchmarks/meal_benchmark.py pool sweeps/experiment
uv run python benchmarks/meal_benchmark.py fetch-evidence sweeps/experiment evidence
uv run python benchmarks/meal_benchmark.py report sweeps/experiment evidence/roots.json
```

After freezing, run every manifest entry in the same harness with its frozen prompt and token
ceiling. `record` takes a JSON array of nutrient cards, measured `wall_seconds`, `tokens_in`,
`tokens_out`, `cost_usd`, and the actual transcript. Cards require `id`, `category`, `title`,
`what`, `why`, `how`, `evidence` (`path`, optional `url`), `license_mode`, `effort`, `risk`.
Unknown metadata is stripped. Records are sealed against later edits and cannot be overwritten.

`pool` waits for all runs, randomizes opaque ids per prey and removes arm/model/trace metadata
and source URLs from value batches. Relative evidence paths remain visible for specificity
judging. Keep `private/batch-map.json` from judges. Follow the frozen [rubric](rubric.md):

- Codex CLI judges every blind batch twice, producing `judged/value.json` and
  `judged/value-repeat.json`. Entries contain `id`, boolean `useful` and `garbage` (exactly one
  true), integer `quality` (0..3) and `reason`.
- A separate pass uses the blind facts batches and pinned prey objects to produce
  `judged/facts.json`: `id`, boolean `evidence_ok` and `license_ok`.
- A human uses Web ChatGPT to audit the fixed `blind/audit-ids.json` sample, at least 20% per
  prey, saving `judged/audit.json` in the value format. Missing judgments refuse the report.

Evidence fetch uses Git objects only, without checkout, installation, tests or builds of prey.
The report independently verifies blob paths at the frozen SHA and license ceilings. Provider
references still require the fact judge. It reports usefulness, precision, golden recall,
quality, fabrication/license errors, measured cost/time and median/min/max per arm and prey.
Audit disagreement above 15% marks the rubric suspect and the sweep invalid; judge
self-agreement is published separately. A zero-error synthetic infrastructure test is not a
model result. The first real sweep and human audit remain
[#250](https://github.com/drevendev/HungryCrab/issues/250).

The manual **Meal benchmark (B2)** workflow consumes a completed `b2-sweep` artifact from a
specified workflow run, fetches immutable evidence and uploads `b2-report`. It does not schedule
paid runs. Use a sweep run in the same repository, with all records and judgment files included.

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
