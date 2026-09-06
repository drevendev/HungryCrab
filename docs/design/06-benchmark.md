# 06 · Benchmark — is the crab actually worth its tokens?

Specification for the two benchmarks that measure the crab. Written before any of it is built,
so that the metrics are chosen from the questions and not from whatever is easy to count.

## 1. The questions

| # | Hypothesis | Why it matters |
|---|---|---|
| H1 | **Haiku with the crab beats Opus without the crab**, at a fraction of the cost | This is the whole architecture in one sentence: scripts squeeze out the deterministic part, the model only judges. If it holds, the design is validated. If it does not, the deterministic layer is not carrying its weight |
| H2 | Each crab version proposes more useful and less useless than the previous one | Otherwise we are adding rules that feel right and measuring nothing |
| H3 | There is a cheapest model that is good enough for a meal | We want to run on Haiku by default and reach for Opus only where it pays |

Everything below exists to answer those three. A metric that answers none of them does not
belong here.

## 2. Two benchmarks, because the crab has two layers

The deterministic layer (`compare` → `menu`) runs without a model and is fully reproducible.
The judgment layer (skill plus model) costs money and is noisy. Measured together, a regression
in the scoring weights shows up as "the model got worse".

| | B1 · Menu | B2 · Meal |
|---|---|---|
| What it measures | the candidate list the CLI produces | the issues an agent produces from it |
| Models involved | none | the tested model, plus judges |
| Reproducible | yes, bit for bit | no; medians over repeats |
| Cost | zero | dollars per run |
| Runs | CI, every PR that touches rules, scoring or miners | manually, at milestone boundaries |

## 3. Frozen setup

Nothing moves between runs, or the numbers are not comparable.

**Maw:** `drevendev/HungryCrab` at tag `v0.2.0`, checked out into a worktree. Real, ours, and
we know exactly what it lacks (no release automation, no coverage, no security scanning,
hand-written language and license maps, a characters-over-3.5 token estimate).

**Donors,** pinned to a commit SHA recorded in the run manifest:

| Donor | License | Why this one |
|---|---|---|
| `pypa/pipx` | MIT | small, same stack, rich in exactly the CI and release nutrients the maw lacks |
| `github-linguist/linguist` | MIT | large, permissive, exercises the `code` category and evidence links |
| `anthropics/skills` | none detected | exercises the `IDEAS_ONLY` path and the `ai-config` category |

Three different license modes, so the license engine is under test too.

## 4. Arms

An arm is a triple: **(harness, model, crab version)**. Comparing "models" only means something
when the harness is fixed, so every arm runs through the same harness.

| Arm | Crab | What the model gets |
|---|---|---|
| `A0` baseline | none | the prey cloned locally, the maw repository, and the frozen prompt from `benchmarks/prompts/baseline.md` |
| `A1` old | `v0.2.0` CLI and skills | the `eat` protocol as it was released |
| `A2` new | `master` CLI and skills | the current protocol |

Models: `claude-haiku-4-5-20251001`, `claude-sonnet-5`, `claude-opus-5`. Each with standard and
extended reasoning where the model supports it.

The full matrix is expensive, so it is cut:

- **all arms x all models x 2 repeats** on `pypa/pipx` only;
- **reasoning variants** only on `A2`;
- the other two prey only on the reference configuration (`A2` with Sonnet), to check that
  the result generalizes beyond one prey.

That is roughly 25 to 30 agent runs per full sweep.

## 5. The baseline prompt is part of the experiment

`A0` must get a fair shot or the benchmark proves nothing. Its prompt is frozen verbatim in
`benchmarks/prompts/baseline.md`, is written the way a competent user would write it, and asks
for the same output schema as the crab produces. It names the task, links both repositories, and
states the license constraint. It is changed only with a version bump of the benchmark, and the
change is recorded.

`A0` gets the same token ceiling as the crab arms spend on average. Without a ceiling we would be
comparing "one shot" against "read the whole repository".

## 6. Output normalization

Every arm emits the same card schema, so the judge grades substance and not resemblance to the
crab:

```json
{"id": "...", "category": "...", "title": "...", "what": "...", "why_for_maw": "...",
 "how": "...", "evidence": [{"path": "...", "url": "..."}], "license_mode": "...",
 "effort": "S|M|L", "risk": "low|medium|high"}
```

A normalizer turns each run into `runs/<run-id>/nutrients.json` plus a rendered
`nutrients.md`. Ids are replaced with random tokens, and nothing in the file says which arm,
model or prey produced it.

## 7. Judging

All runs for one prey are pooled into a single batch and shuffled, so the judge grades
everything on one scale without knowing what came from where.

**Pass 1 — value, blind.** The judge gets the maw at the frozen SHA and the pooled cards. It
does **not** get the prey. For each card:

- `useful`: would a maintainer of this exact maw act on it? A card is useful only if it names
  something the maw actually lacks and that matters for it.
- `garbage`, with a reason: duplicate, wrong stack, already present in the maw, vague to the
  point of being unactionable.
- `quality` 0 to 3, by anchors, not by feel: 0 generic advice; 1 maw-specific but no concrete
  step; 2 concrete step, no evidence; 3 concrete step naming real files or tools of the maw,
  with evidence.

**Pass 2 — facts, with the prey.** Now the judge gets the prey at its pinned SHA:

- does every cited path exist in the prey at that commit, and does it show what the card claims?
- is `license_mode` consistent with the prey's license and the maw's MIT?

Fabricated evidence is the failure mode we most expect from `A0`, and pass 1 cannot see it.
This is why the prey is withheld in pass 1 and given in pass 2 rather than hidden entirely.

**Judges.** Codex CLI is the scored judge: it is scriptable, logged and repeatable. Web ChatGPT
audits a 20 % sample by hand. They are the same family, so they are not independent of each
other, but both are a different family from the models under test, which is the bias that
actually threatens this comparison.

**Disagreement rule.** Codex decides the recorded number. When the ChatGPT audit disagrees with
Codex on more than 15 % of the sampled cards, the run is marked `rubric-suspect` and the rubric
is revised before any conclusion is drawn from that sweep. Judge self-agreement is measured too:
the same batch judged twice, and the agreement is published with the results.

## 8. The golden set anchors everything

Without it we compare one model's opinion to another's. The golden set is a human-written list of
nutrients that genuinely matter for the frozen maw, in two tiers, `must` and `nice`, stored as
`benchmarks/golden/<maw>@<sha>/<prey>@<sha>.yml` with a one-line justification each.

It is written **before any arm runs**, from the maw's own digest and known gaps, and frozen.
Writing it afterwards would let us describe what the crab happens to find. Cards proposed by any
arm that are not in the golden set and that both judges call useful become candidates for the
next revision of the set, recorded with the sweep that found them.

## 9. Metrics

**B1, per (maw, prey) pair, deterministic:**

- `recall_must@30`, `recall_nice@30` — golden nutrients present in the top 30 of the menu
- `mean_rank` of the golden items
- `noise_ratio` — candidates neither golden nor on the accepted list
- `digest_tokens`, `menu_tokens`, `compare_seconds`

**B2, per run:**

- `wall_seconds`, `tokens_in`, `tokens_out`, `cost_usd`
- `n_proposed`, `n_useful`, `n_garbage`, `n_disputed`
- `quality_mean` over the useful ones
- `n_fabricated` and `n_license_errors` from pass 2
- `recall_must` against the golden set, computed without a model

**Headline numbers**, tracked separately and never collapsed into one for decisions:

- `useful_per_100k = n_useful / (tokens_total / 100000)` — the efficiency H1 is about
- `precision = n_useful / (n_useful + n_garbage)`
- `quality_mean`
- `recall_must`

One composite exists for the leaderboard view only:

```
meal_score = recall_must * precision * (quality_mean / 3)      # 0..1
```

Cost is never folded into it. Quality against cost is a plot, and the model choice for H3 is
read off that plot, not off a single number.

## 10. File layout

```
benchmarks/
├── run.py                      # the digest benchmark that already exists
├── prompts/baseline.md         # the frozen A0 prompt
├── rubric.md                   # the judging rubric, verbatim, given to the judges
├── golden/<maw>@<sha>/<prey>@<sha>.yml
├── sweeps/<date>/
│   ├── manifest.json           # maw sha, prey shas, arms, model ids, crab versions, prompt hash
│   ├── runs/<run-id>/{nutrients.json,nutrients.md,usage.json,transcript.log}
│   ├── judged/<judge>/<batch>.json
│   └── report.md               # the table and the plot
└── results/<date>.json         # B1, one file per run
```

The manifest is what makes a sweep reproducible: every SHA, every model id, the hash of the
prompt and the rubric. A sweep whose manifest does not pin all of them is not a sweep.

## 11. When it runs

- **B1** in CI, on every pull request that touches `compare/`, `data/scoring.yml`, the miners or
  the fixtures. Gate: `recall_must@30` must not fall below the value recorded on master.
- **B2** at milestone boundaries, before a release, and after any material change to the skills.
  Manual `workflow_dispatch`, because it costs money and needs keys.
- Minimum two repeats per arm, median reported, spread published. A single agent run proves
  nothing and must not be quoted as a result.

## 12. Threats to validity, stated up front

- **Agent runs are not deterministic.** Repeats and medians reduce this; they do not remove it.
  Any difference smaller than the spread between repeats is not a finding.
- **We write both the crab and the golden set.** Mitigated by writing the set first, from the
  maw's needs, and by letting rival arms contribute candidates to it.
- **The judges are one family.** They are a different family from the models under test, which is
  the bias that matters here, but two GPT judges are not two independent judges.
- **Verbosity bias.** Longer, prettier cards score higher with any model judge. The quality
  anchors are written to reward concreteness, not length, and the normalizer strips formatting
  differences between arms.
- **The maw is our own repository.** Results may not generalize; the 0.3 fleet run is what tests
  that.
- **Web ChatGPT is not reproducible.** That is why it audits and does not score.

## 13. Order of work

1. **Stage 0.2.1 first** ([05-self-feeding.md](05-self-feeding.md)). Two or three real meals.
   A rubric written without ever having seen a meal measures the wrong things.
2. **B1**, immediately after: frozen maw and prey, the golden set for `pipx`, the deterministic
   menu benchmark, the CI gate. Cheap and it starts paying at once.
3. **B2** at the 0.3 boundary: the arms, the normalizer, the judging pipeline, the first sweep.

Together this is comparable in size to milestone 0.3 itself. It is a track, not a side task.
