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

`ranking/pressure.json` freezes a separate synthetic pair and reports where prior
human-accepted generic priorities rank. The 0.6.9 general uptake rule for unknown
agent-host adoption yields 54 candidates: release 26, schedule 27, coverage 10,
issue templates 29 and PR template 30. This restores the existing pressure gate
without key-specific weights or changes to the original B1 labels/thresholds.
The negative control disables category weights and must miss the priorities.
This synthetic control is not a new human usefulness corpus; larger human
labeling remains [#200](https://github.com/drevendev/HungryCrab/issues/200).

## Additional diagnostics and paired reporting

### 0.6.7 paired yield/cost importer

See [paired-report.md](paired-report.md) for the offline freeze/seal/report procedure, raw-artifact
relocation, portable/unknown cost fields and a preregistered held-out follow-up protocol. It uses
the ordinary meal evidence projection; synthetic infrastructure checks are not discovery gains.

### Ranking diagnostics

B1 also reports stdlib MRR/nDCG/Judged@10 diagnostics. Relevance is the unchanged historical
must/must_not proxy, not new default-branch acceptance. DCG uses `(2^grade - 1)/log2(rank+1)`;
IDCG sorts all known grades. Unknown labels contribute zero gain and are disclosed by Judged@k
(known returned labels divided by k, including short lists). Empty/no-relevant queries score
zero. MRR is the unweighted mean of first-positive reciprocal ranks; deterministic menu order
breaks ties. Oracle and reversed-order tests validate sensitivity. No new thresholds or weight
changes are introduced; #200/#303 retain their broader acceptance.

Measured on the unchanged frozen B1 corpus at introduction (macro mean over its three pairs):

| Diagnostic | At introduction | Gate |
|---|---|---|
| MRR@10 | 0.833333 | none |
| nDCG@10 | 0.870574 | none |
| Judged@10 | 0.700000 | none |

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

## Invoked discovery qualification

`qualification_models.py OUTPUT --cwd EMPTY_OWNED_DIRECTORY` reads the native
model catalogue without inference. A catalogue entry is not proof of entitlement;
calibrate a completed inference on known original fixtures before sealing a study.
The native adapter uses the existing account session, disables unrelated tools,
captures actual input (including cached input) plus output, and preserves unknown
usage or overruns. It does not create user-owned chats or start a scheduler.

`qualification_cycle.py MAW-ARM-REPEAT --plan SEALED.json` runs one explicitly
invoked trial. The plan binds installed arm interpreters/source identities,
matching skills, receiving contexts/needs, immutable pool objects/digests,
pre-existing receiving runtimes, native model/effort, limits and artifact hashes.
The trial reads no other trial's judgments. All thirty common sources may be
acquired/mined as declared shared setup; semantic admissions remain separately
bounded at six. Read-only transport preparation checks actual planned input
before admitting semantic donor work. Candidate citations still require actual
CLI inspection receipts after admission.

Original JSON receiving cases are generated and, if necessary, repaired in a
receiving-only fresh context before any donor body is supplied. Every complete
native stage settles actual use before semantic parsing. A fixed runner executes
only trusted receiving code; actual function calls and target/correct controls
remain in the receipt. Runtime claims, design artifacts, legal registration,
publication and human acceptance stay separate. A registration hold retains the
semantic proposal and tries an alternative under the same budget.
When source inspection raises another runtime question, a fresh receiving-only
writer receives the code-free question and owned receiving source, creates
original JSON cases and executes them within the same trial cap. Preserve the
analyst's original conditional tier and the actual execution result separately.
Typed pending/backlog reads retain previous donor/receiving windows; identical
pending content is deduplicated while every role/ref binding survives. Separate
need-owned meals prevent a repeated donor from erasing an earlier registration.

`research_host.py` owns operational command/native receipts, not a second
acceptance ledger. The installed CLI owns the discovery/exploration state. The
admission estimate is calibrated on known controls (6,500 prefix/reply reserve
plus request bytes/3); only native actual usage qualifies the 30,000-token cap.
Native malformed/oversized/degenerate output and interrupted stages remain failed
or unknown, never invented zero-cost work. No API maximum-output-token field is
assumed on the native route. The [native protocol](https://learn.chatgpt.com/docs/app-server)
describes that transport; installed schemas and captured replies are the run's
actual interface evidence.

Seal a source-unseen pool and protocol before both arms; preserve failed
calibration/earlier studies. Run the twelve trials and six separate arm-masked
reviews required by `docs/releases/0.6.9-scope.md`. Setup/engineering costs outside
the captured investigator/reviewer stages remain unknown; these receipts do not
establish whole-project or financial efficiency. Passing known controls alone
does not pass the held-out gain/precision/safety gates.

## Reference prey

| Repository | Why |
|---|---|
| `pallets/click` | Python, BSD-3-Clause, 3k+ commits, many tags: history and release cadence |
| `colinhacks/zod` | TypeScript pnpm monorepo, MIT, AI configs, rich issues: architecture and issues |
