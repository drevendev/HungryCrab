# 03 · Roadmap: Base Crab → Evolving Crab → Forks

Three tracks run in sequence with overlap: the Evolving Crab can start as soon as the base crab
has deterministic benchmarks (after 0.3), and the fork kit as soon as the Evolving Crab has run a
few clean cycles.

```
Track A · Hungry Crab        0.1 ─ 0.2 ─ 0.3 ═══ 0.4 ─ 0.5 ─ 0.6 ─ 1.0
Track B · Evolving Crab                  E0 ─ E1 ─ E2 ─ E3 ─ E4
Track C · Forks                                        F0 ─ F1 ─ F2
```

## Track A · Hungry Crab (base)

| Version | Name | Content | Exit criterion |
|---|---|---|---|
| 0.1 | Sniff & Digest | CLI, miners without a model, fixtures, CI on Windows + Linux | digest of any public repo within the token policy, ≤ 120 s for ≤ 50k LOC and ≤ 5k commits |
| 0.2 | Menu | compare, scoring, issues/architecture miners, skills, historian/architect subagents, serve issues, ledger, plugin | end-to-end `/crab:eat` → issues, 0 duplicates on rerun |
| 0.2.1 | Self-feeding | `/crab:eat` from a live agent session with the crab as the maw; fix what the skill gets wrong ([05-self-feeding.md](05-self-feeding.md)) | 0.2's exit criterion honestly closed: two live meals, 0 duplicates, skill defects fixed |
| 0.2.2 | Menu benchmark | B1 from [06-benchmark.md](06-benchmark.md): frozen maw and prey, the golden set, the deterministic menu benchmark and its CI gate | **milestone done, not released.** `recall_must@30` = 1.00 (10/10) and `noise@30` = 0.66 (19/29) on master, both gating pull requests through the test suite |
| 0.3 | Serve | PR branches, trace, attribution, clean room, safety hook, docs; the license resolutions and the `trust` relationship; the budget policy and paged documents ([08-budgets-and-feeder.md](08-budgets-and-feeder.md)); coverage measured and gated | ≥ 3 merged PRs in the fleet; **MVP closed** |
| 0.3.1 | Feeder | Independent wiki snapshots and enforced strict mode; the deterministic pipeline as a reusable GitHub workflow and a composite action: a maw names its prey, the job runs `catch → digest → compare` with no model anywhere, and uploads the meal as a build artifact. Rate limits, retries and conditional requests, because a runner has no `gh auth`; `--shallow --since` by default. Brought forward from 0.6 ([08-budgets-and-feeder.md](08-budgets-and-feeder.md)) | a repository with no agent installed gets a menu artifact on a schedule; the Evolving Crab's CONSUME phase is this job |
| 0.3.2 | Scheduled crab | `crab loop`: persisted phases, leases, budgets, retries, control repositories, guarded publication and `/crab:loop` ([operator guide](../scheduled-crab.md), [design](07-scheduled-crab.md)) | Implementation has deterministic tests; live exit evidence remains open in [#242](https://github.com/drevendev/HungryCrab/issues/242): ten scheduled wake-ups, a second maw round, a verified MOLT and a human-merged release/tag with measured phase costs. Not released. |
| 0.4 | Deep Bite | Discussions (GraphQL), PR reviews, CI durations and explicit flaky-rerun report evidence; eight dependency ecosystems and GitLab.com acquisition; a ranking pressure report and the B2 experiment pipeline ([guide](../deep-bite.md)) | Implementation covers eight ecosystems and provider signals. The optional tree-sitter symbol index remains postponed; supervised source-linked `code` registration is implemented under #447 without that dependency. Unreleased milestone criteria remain; the first live B2 sweep is [#250](https://github.com/drevendev/HungryCrab/issues/250). |
| 0.5 | Taste Memory | scorer learning from the ledger (accepted/rejected by category and maw), `crab hunt --for .` (finding prey for a maw via `gh search` + similarity signals), multi-prey (`eat a b c` → merged menu), hunger profiles by repository type | share of accepted issues grows between iterations on the same maw |
| 0.6 | Everywhere | MCP server (`crab_digest`, `crab_menu`), PyPI package, `npx skills add`, docs for Codex/Cursor, `crab report` (HTML report of a digest) | Released as **0.6.0**, a limited stable analysis scope: local stdio MCP, portable skills, source/wheel installation and HTML reports, with Windows/Linux protocol and install checks in CI. The package is on PyPI as `hungry-crab` from 0.6.1, published from CI from 0.6.2. GUI-host meals and the earlier live criteria are post-release work by explicit owner decision. See [Everywhere](../everywhere.md). |
| 1.0 | Stable | stable schemas (`nutrient`, `manifest`, `.crab.yml`), semver guarantees, ledger migrations, a set of 50 license test repositories, public benchmarks | schemas frozen, changelog and release automation — on the crab itself |

Released: **0.6.7** (2026-10-28), **0.6.6** (2026-10-26), **0.6.5** and **0.6.4** (2026-10-25), **0.6.3** and **0.6.2** (2026-10-23), and **0.6.1** (2026-10-22),
security releases on **0.6.0
Everywhere** (2026-10-22), a limited stable analysis release, after **0.2.0**. `hungry-crab` is
on PyPI from 0.6.1.
The 0.2.1-0.5 implementations are included in it; that does not close their unfulfilled
milestone exit criteria, which stay open in #224, #236, #242, #243, #250 and #256. See the
[release record](../releases/0.6.0-readiness.md).

The 0.6.7 discovery-reliability release packages the selected #469 scope, including the final
#476 review repairs. It adds receiving-context checks, verified MCP paging, conservative
configuration/learning behavior and explicit evidence-state reporting.
Its [implementation map](../releases/0.6.7-implementation.md) records the bounded slices;
independent quality/acceptance and the broader milestone criteria remain open.

The 0.6.5 supervised discovery release follows
[the release plan](../releases/next-discovery-release-scope.md), tracked by
[#453](https://github.com/drevendev/HungryCrab/issues/453). It covers pilot PR
follow-through, the pilot findings, wider discovery and three bounded improvements;
unattended host enforcement remains deferred. The
[execution report](../experiments/discovery-453-report.md) records actual coverage and limits.

### Distribution programme — GitHub Marketplace Feeder (proposed, 2026-10-22)

The existing composite **Hungry Crab Feeder** Action has a separate distribution/adoption
roadmap, not a new 0.x runtime milestone: [Marketplace launch plan](../marketplace/README.md)
([programme #304](https://github.com/drevendev/HungryCrab/issues/304)). Its five gates are
**M0** honest story/quickstart, **M1** unique metadata/immutable dependency pins and
cross-repository exact-ref Linux/Windows tests, **M2** real read-only public fleet dogfood,
**M3** verified proof plus owner-mediated Marketplace listing, and **M4** a First Feast
video/article and measured early adoption. The Action is not assumed listed just because
`action.yml` triggers GitHub's "Draft a release" banner. Evidence from `uses: ./` tests
is not equivalent to a published Marketplace installation.

The proposed [copy](../marketplace/positioning.md), [test matrix](../marketplace/dogfood.md),
[proof policy](../marketplace/evidence.md) and
[publication checklist](../marketplace/publication.md) have no invented runs or screenshots.
Reuse [#301](https://github.com/drevendev/HungryCrab/issues/301) for Feeder maw-wiki/token
and immutable pins, [#211](https://github.com/drevendev/HungryCrab/issues/211) for cache
ownership, [#236](https://github.com/drevendev/HungryCrab/issues/236) for hard acquisition
limits, and [#281](https://github.com/drevendev/HungryCrab/issues/281) for the wider
owner-review/usefulness campaign. No code, tags, release, external workflow or Marketplace
publication is performed by the documentation proposal.

### Project state and integration order (2026-10-21)

A historical snapshot of the split before 0.6.0 landed; current status is above.

This is an audit of upstream `master` at `e33860a`, not a release announcement. The only
published version is still **0.2.0**. The code available in development candidates reaches
**0.5.0.dev0**, but that does not close the 0.3 MVP/release gate or prove 0.5 uptake gains.

| Layer | Upstream / candidate status | Remaining evidence |
|---|---|---|
| 0.3 Serve foundation | PR serving, attribution, guards and paged digests are on master; safety and output-ownership repairs are in the integration candidate | Three useful human-merged fleet PRs, the 30-repository licence acceptance set and the complete release checklist: [#224](https://github.com/drevendev/HungryCrab/issues/224) |
| 0.3.1 Feeder | Implemented in #237 and inherited by #246; live fork workflow artifacts exist | Hard acquisition quotas remain [#236](https://github.com/drevendev/HungryCrab/issues/236) |
| 0.3.2 Scheduled Crab | Implemented in #244 and inherited by #246 | Live phase costs, ten wake-ups and a second maw round: [#242](https://github.com/drevendev/HungryCrab/issues/242); host enforcement before wider work autonomy: [#243](https://github.com/drevendev/HungryCrab/issues/243) |
| 0.4 Deep Bite | Implemented in #253 (tree-sitter symbols later postponed); provider signals, eight dependency ecosystems and B2 tooling | Real paid B2 sweep and human audit: [#250](https://github.com/drevendev/HungryCrab/issues/250); authenticated GitLab signals: [#252](https://github.com/drevendev/HungryCrab/issues/252) |
| 0.5 Taste Memory | Implemented in #259; #270 adds owner-scoped prey restrictions | Held-out uptake comparison: [#256](https://github.com/drevendev/HungryCrab/issues/256); deterministic tests alone do not establish usefulness |

The selected recovery path is one integration candidate on current master containing #270
(which already contains #259, #253 and #246), #284 (target resolution), #288 (packaging and
Ruff parity), #283 (offline installation smoke) and #290 (fork CI guidance). Original commits
and authors are retained. The candidate also repairs the multi-prey selection authorization
reported on #259, checks actual merge-commit CI for [#285](https://github.com/drevendev/HungryCrab/issues/285),
and requires exact producer/artifact ownership for [#286](https://github.com/drevendev/HungryCrab/issues/286).
It includes #270's empty-control-prey fix for [#287](https://github.com/drevendev/HungryCrab/issues/287).

If this candidate is selected, do not separately merge its component stacks (#235, #237,
#240, #241, #244, #246, #253, #259 and #270), or superseded drafts #175/#234. Open PRs and
branches are integration proposals until a human merges them; successful fork CI does not
mean upstream fork workflows have been approved. The remote #211 branch has no unique
implementation; its Windows reparse-point/cache-removal issue remains open. The #267
transport branch is already included in master through #268. Do not recover either as a
second implementation merely because its remote ref still exists.

### Everywhere implementation candidate (2026-10-21)

0.6 adds optional official-SDK stdio tools (`crab_digest`, `crab_menu`), explicit local roots,
opt-in remote reads, offline `crab report` views and an isolated wheel/sdist installation gate.
The base runtime still depends only on PyYAML. Clean install and actual protocol calls are
verified on Windows/Linux; host-specific connection recipes and portable skills are in the
[operator guide](../everywhere.md). This is not a published 0.6 release, an assertion that every
GUI host ran a meal, a PyPI upload, or a waiver of #224/#236/#242/#243/#250/#256.

### Next delivery priorities

1. **Land one verified integration.** Keep the current master fixes, resolve shared contracts,
   pass Linux/Windows CI on the selected final head, and retain distinct release/live gates.
2. **Prove a useful first meal.** Follow [First Feast](../FIRST_FEAST.md) / [#281](https://github.com/drevendev/HungryCrab/issues/281):
   finish the executed fixture walkthrough, audit supported and NO_GAP cards, and obtain three
   independently accepted improvements. Record reviewer time, false positives and time to first
   useful result. Establish the frozen 0.4 B2 baseline before comparing learned 0.5 weights.
3. **Complete the release gate.** Resolve #224's licence corpus, fleet evidence and release
   checks. Run the paid B2 experiment only with explicit budget authorization. Neither this
   audit nor integration publishes a tag or enables unattended work.
4. **Build 0.6 Everywhere around the first useful result.** The next capability milestone with
   the strongest expected product impact is a small read-first MCP interface (`crab_digest`,
   `crab_menu`), straightforward distribution and an inspectable report. Reuse the deterministic
   CLI and its policy; keep publication explicit. Acceptance should include a clean install and
   a reproducible useful meal in each supported harness. Add host enforcement (#243) and hard
   quotas (#236) before expanding work autonomy.

The bottleneck is now integration and demonstrated recommendation quality, rather than a
missing miner. The original human B1 corpus still reports recall 10/10 and noise 19/29;
its labels and thresholds must remain frozen. The separate 56-candidate ranking-pressure
fixture reports ranking under load (report only: four accepted priorities rank 31-35 with the
default scoring) but does not replace broader human labels (#200), real
B2 results (#250), or held-out Taste Memory uptake (#256). Evolving Crab E0 remains a later
track until those foundations and operational costs are established.

A milestone and a release are different things, and conflating them is what produced
[#37](https://github.com/drevendev/HungryCrab/issues/37): 0.2.2 was declared released in three
places and existed in none of them. This table is the authority on milestones; `CHANGELOG.md` is
the authority on releases, and it names only what has a tag.

Cross-cutting themes of track A:
- **Own hygiene** — from 0.2 the crab eats itself (`/crab:eat` on its own repo with prey from the
  git-mining / license-detection space) and merges its own proposals.
- **Digest anything** — the exit criterion of every version is written in terms of nutrients, but
  the goal underneath is that no repository defeats the digestion: not a gigabyte monorepo, not a
  data-only repository, not a licence split across packages, not an ecosystem no miner knows.
  Every version therefore has a set of prey chosen to break it rather than to feed it, and a meal
  is judged twice: by the nutrients it produced and by the defect it exposed. A meal that teaches
  nothing has to be repeated after the next miner lands, and every defect earns a fixture under
  `tests/fixtures/repos/` rather than a one-off patch. Track B cannot start on a crab that only
  digests the repositories it was written against. The queue itself is maintained by the
  maintainer and is not part of this repository: it names private repositories, and publishing a
  list of targets is not the same as publishing a tool.
- **Two users, two policies** — an agent session and a budgeted loop want opposite things from
  the same digest, and where they disagree the difference is configuration rather than a
  compromise ([08-budgets-and-feeder.md](08-budgets-and-feeder.md)).
- **Benchmarks as a product** — every version adds entries to `benchmarks/`, because track B
  stands on them. Three of them run at three different rhythms. The **digest benchmark**
  (seconds and tokens on reference prey) has run since 0.1, by hand, when a miner changes.
  **B1, the menu benchmark**, has gated every pull request since 0.2.2: frozen digests, the
  maintainer's own verdicts as the golden set, no model and no network. **B2, the meal
  benchmark**, costs money and runs at milestone boundaries starting with the 0.3 release; it
  judges whole meals across crab versions and models, and its headline question is whether
  Haiku with the crab beats Opus without it. The specification for B1 and B2 is
  [06-benchmark.md](06-benchmark.md). A fourth number, digest **coverage**, belongs to **0.3**,
  whose exit criterion asks for it measured and gated; this paragraph used to owe it to 0.4,
  and a number cannot have two owners. It has been measured now, and measuring it showed it
  is not yet a metric: `files_counted / files` counts a deliberately excluded sample corpus
  and an unread prey the same way, so a repository whose examples the crab skips correctly
  scores like one it failed to read — the maw digesting itself scores 0.065, and `.venv` is
  the whole of the difference. So it became two numbers
  ([#76](https://github.com/drevendev/HungryCrab/issues/76)): the *analysis share*,
  `files_counted / files_seen`, informational, with every excluded file reported beside it by
  reason (vendored, corpus, examples, ignored, generated, binary, LFS); and *visibility*, the
  explicit losses of the walk — a list truncated at the cap, paths that failed to stat — which
  is the only thing a machine may refuse a digest over (`crab digest --fail-on-loss`). Neither
  is the same number as a prey's own test coverage, which the crab reads from the prey and
  gets wrong for a different reason
  ([#53](https://github.com/drevendev/HungryCrab/issues/53)).

## Track B · Evolving Hungry Crab

Detailed design in [04-evolving-crab.md](04-evolving-crab.md). Stages here.

| Stage | Name | Content | Exit criterion |
|---|---|---|---|
| E0 | Constitution & Benchmarks | separate public repository, `CONSTITUTION.md` (immutable goals, license policy, budget, safety rules), a `goal/` pack for the base goal "digest repositories efficiently", benchmarks B1–B5 (deterministic), `fitness.py`, TRIAL as plain CI | TRIAL reproducible locally and in CI, baseline recorded |
| E1 | Loop on Rails | `cycle.yml` workflow, cycle issue as the journal, phases as jobs with phase skills, **every phase started by a human** (`workflow_dispatch` / label), Claude via `claude-code-action` with `setup-token` | 3 full cycles with manually started phases, all cycle artifacts in `cycles/NNNN/` |
| E2 | Semi-Auto | schedule, automatic HUNT→CONSUME→EVOLVE→TRIAL transitions, auto-merge of EVOLVE PRs on green TRIAL, GROW writes retro and `LESSONS.md`, MOLT opens a PR; **GOAL and MOLT merged by a human** (CODEOWNERS), budget/hunger, kill switch | 10 cycles with no intervention except GOAL/MOLT approvals, fitness never decreases |
| E3 | Autonomous | autonomous GOAL/MOLT within the constitution, escalation to a human after K failures or starvation, weekly digest for the human | a month of autonomous operation with no incidents (constitution violations, broken main) |
| E4 | Public Organism | GitHub Pages dashboard (fitness per cycle, cost, accepted nutrients), public decision log, "prey" PRs into the base Hungry Crab from evolution results | outsiders can read the evolution history and reuse improvements |

Dependencies: E0 requires 0.3 (something to benchmark) and 0.3.1 (the CONSUME phase is the Feeder job);
E2 requires 0.5 (`crab hunt` is the HUNT phase). E0 also inherits its budget numbers from 0.3.2, which runs the same phases locally and
records what each one costs ([07-scheduled-crab.md](07-scheduled-crab.md)); a cron entry is a
cheaper place to find out that a phase does not fit in one session than a public organism is.

## Track C · Forks (other goals)

| Stage | Name | Content | Exit criterion |
|---|---|---|---|
| F0 | Goal Pack Contract | specification of `goal/` (GOAL.md, hunt.yml, benchmarks/, fitness.py, budget.yml), the organism in `src/`, workflows as **reusable workflows from upstream** (a fork does not copy them, it references `uses: drevendev/evolving-hungry-crab/.github/workflows/cycle.yml@v1`) | the Evolving Crab itself runs on the contract; its goal is just another pack |
| F1 | Reference Fork: Sudoku | template repository `evolving-crab-template` + `goal-sudoku`: solve puzzle sets faster and more completely; demonstrates HUNT for algorithms (DLX, constraint propagation, SAT), license modes for code from prey, the goal ladder | the fork passes ≥ 5 cycles and beats the baseline solver |
| F2 | Fork Kit & Community | `crab new-goal` pack generator, guide "build your own crab in an hour", goal catalog (examples: chess engine by Elo, compressor by ratio, JSON parser by throughput, accessibility linter by recall), safe-benchmark rules (no prey execution, sandbox for the organism) | 2+ forks not by the author pass a cycle |

## What we measure across the whole roadmap

| Metric | Why |
|---|---|
| Share of accepted issues / merged PRs by category | real usefulness of the crab for the fleet |
| License verdict accuracy on the test set | trust in the tool |
| Model tokens per `/crab:eat` | economics; should fall as quality rises |
| Digest time on reference repositories | speed |
| Recall of "golden" nutrients (B3) | quality of the deterministic layer |
| Evolving Crab fitness per cycle, cycles to pass TRIAL | does evolution work |
| Security incidents (prey execution, constitution violations) | must be 0 |
