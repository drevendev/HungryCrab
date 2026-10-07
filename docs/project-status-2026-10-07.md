# Project state and next milestones: 2026-10-07

This is a dated assessment, not a release announcement. The milestone contract remains
[the roadmap](design/03-roadmap.md), and [the glossary](design/GLOSSARY.md) owns terminology.
The assessment distinguishes published, merged and implemented work so that an open integration
branch cannot be mistaken for a completed release.

## Where the project is

| Layer | Observed state |
|---|---|
| Published release | [v0.2.0 Menu](https://github.com/drevendev/HungryCrab/releases/tag/v0.2.0), published 2026-09-06; the only published version, tagged `v0.2.0` for the CLI and `crab--v0.2.0` for the plugin |
| Upstream `master` | `6a78d92`, licensed COPY/REIMPLEMENT PR serving, attribution receipts, paged digests and guards; the 0.3 release acceptance gate remains open |
| Foundation integration | [#246](https://github.com/drevendev/HungryCrab/pull/246), `181ea9c`: Feeder, wiki, strict policy, Scheduled Crab and pending safety fixes together |
| Latest implementation | [#253](https://github.com/drevendev/HungryCrab/pull/253), `d6c372e`: `0.4.0.dev0` Deep Bite on the foundation above; 25 commits ahead of `master` |
| Evolving Crab and forks | Later tracks in the design; this checkout has no `goal/` pack or Evolving Crab cycle implementation |

The project is beyond the original Menu MVP in implemented capability, but still in integration
and operational validation. It has a substantial deterministic core and optional deeper static
analysis; it has not yet demonstrated the release criteria for a validated improvement loop.
The package still identifies itself as pre-alpha.

Deep Bite implements fifteen miners, optional isolated syntax workers for nine languages,
dependency facts for eight ecosystems, symbol-backed code/architecture review subjects,
GitHub Discussions/reviews/CI samples and GitLab.com acquisition. The base runtime dependency
remains PyYAML. Feeder exports meals without an agent; Scheduled Crab persists bounded phases
and guarded publication. These capabilities are on the open integration stack, not `master`.

## Pending branches and merge order

The primary working tree was clean at the start of the audit. Every fetched local or remote
implementation branch was accounted for by the current Deep Bite tree: either its head is an
ancestor, or its recovered changes are already present under different commit identities.
The Ruff dependency update is also included. Branch names for release CI, configuration schema
and packaging parity point at `master` and contain no implementation for those open issues.

1. Review and merge **#246**. It contains the work of
   [#235](https://github.com/drevendev/HungryCrab/pull/235),
   [#237](https://github.com/drevendev/HungryCrab/pull/237),
   [#239](https://github.com/drevendev/HungryCrab/pull/239),
   [#240](https://github.com/drevendev/HungryCrab/pull/240),
   [#241](https://github.com/drevendev/HungryCrab/pull/241) and
   [#244](https://github.com/drevendev/HungryCrab/pull/244).
2. Refresh **#253** against the resulting `master`, resolve any integration changes, run its
   checks and merge Deep Bite.
3. After the integration lands, reconcile the overlapping PRs, including recovered drafts
   [#175](https://github.com/drevendev/HungryCrab/pull/175) and
   [#234](https://github.com/drevendev/HungryCrab/pull/234), against the final tree. They do not
   need to be merged separately to retain their implemented changes.

The audit does not perform merges, tags or releases. A new implementation PR for those same
changes would duplicate an already open, verified stack.

## What the evidence establishes

The exact Deep Bite head `d6c372ea809fe79b6413cb7d52abc954389fda45` has a successful
[six-job CI run](https://github.com/abogun-product/HungryCrab/actions/runs/37623513860):
lint/strict types, Linux and Windows on Python 3.11 and 3.14, and a base installation without
native parsers. Its
[five-job live Feeder/provider run](https://github.com/abogun-product/HungryCrab/actions/runs/37623519258)
also passed. The
[upstream run](https://github.com/drevendev/HungryCrab/actions/runs/37623464110)
is `action_required`: the repository owner must approve the fork workflow. Green fork runs
verify the commit but do not satisfy that upstream approval step.

The human B1 baseline remains recall 10/10 and noise 19/29. A separate synthetic pressure pair
forces 56 candidates to compete for 30 places and includes a failing scoring negative control.
That closes a regression-testing weakness; it does not establish usefulness on a larger human
corpus ([#200](https://github.com/drevendev/HungryCrab/issues/200)).

The B2 freeze/record/blind/evidence/report pipeline exists. Its synthetic tests establish
experiment integrity, not model quality, accepted improvements or savings. The first real sweep
and human audit remain [#250](https://github.com/drevendev/HungryCrab/issues/250).
Protected GitLab review/pipeline channels also still need authenticated live evidence
([#252](https://github.com/drevendev/HungryCrab/issues/252)).

## Immediate roadmap: integration, hardening and release evidence

Complete these before treating the implementation as a stable, unattended improvement loop:

| Work | Completion evidence |
|---|---|
| Close 0.3 acceptance and choose the release boundary | [#224](https://github.com/drevendev/HungryCrab/issues/224): at least three merged fleet PRs, the 30-repository licence acceptance set, measured B2 results and an executable release procedure; choose versions only after reconciling these gates |
| Repair release and policy input contracts | [#223](https://github.com/drevendev/HungryCrab/issues/223): a release version passes CI while later development remains identifiable; [#225](https://github.com/drevendev/HungryCrab/issues/225): misspelled policy keys cannot silently fall back to more permissive defaults |
| Complete licensing and freshness propagation | [#201](https://github.com/drevendev/HungryCrab/issues/201): per-file/review constraints reach legacy cards as well as syntax-backed cards; [#208](https://github.com/drevendev/HungryCrab/issues/208): cached results reflect all relevant inputs |
| Bound unattended acquisition | [#236](https://github.com/drevendev/HungryCrab/issues/236): download/disk limits include the independent wiki; an API size preflight and a timeout are not hard quotas |
| Validate Scheduled Crab | [#242](https://github.com/drevendev/HungryCrab/issues/242): ten scheduled wake-ups, a second maw, MOLT, restart/update recovery, a human-merged HARDEN release/tag and measured phase costs |
| Enforce phase permissions at the host boundary | [#243](https://github.com/drevendev/HungryCrab/issues/243): live adversarial checks cover generic shell/provider/edit tools and revocation before widening work autonomy |

Continue the existing supervised rollout while collecting those measurements. A passing CLI
test suite cannot establish longitudinal operation or constrain tools outside the CLI.

## Recommended next capability update: 0.5 Taste Memory

The largest product gain would come from making repeated use more relevant to each maw.
Deep Bite broadens what the crab can observe. Taste Memory can turn that evidence into a
better choice of prey and fewer proposals the owner has already learned to reject.
This follows the existing 0.5 milestone and prepares HUNT for the Evolving Crab.

Build it in this order after establishing the B2 baseline:

1. **Make decision memory reliable.** Repair
   [#198](https://github.com/drevendev/HungryCrab/issues/198): idempotent tuning, safe ledger schema
   handling, preserved configuration comments, valid decision thresholds and effective uptake
   weights. Repeated sightings are not independent owner decisions. Keep learned adjustments
   deterministic, bounded, explainable and scoped to the maw.
2. **Discover prey for a concrete gap.** Implement `crab hunt --for .` using the maw's stack,
   missing traits and decision history. Return ranked candidates with reasons and acquisition
   estimates. Searching should not automatically acquire every result.
3. **Compare several prey in one meal.** Merge menus by stable maw-relative nutrient id, retain
   every source's evidence and licence constraints, preserve prey-specific lessons, and avoid
   duplicate serving. More sources must not broaden a licence ceiling.
4. **Add reviewed hunger profiles.** Repository-type defaults should reduce setup work while
   remaining overridable by the maw's explicit policy.

Freeze the evaluation protocol before changing weights: compare acceptance/merge rates,
useful nutrients per owner review, noise, and measured cost per accepted improvement on the
same maws, with a held-out set. Preserve the B1 gates and require zero licence violations.
The current `tune` command supplies suggestions, not a validated adaptive scorer.

Then follow the established roadmap: **0.6 Everywhere** adds MCP/distribution/reporting;
**1.0 Stable** freezes schemas and migrations. Start Evolving Crab **E0/E1** once the base
release, deterministic fitness baseline and measured phase budgets are ready. Scheduled Crab
is useful groundwork, but does not establish the Evolving Crab exit criteria. Wider autonomy
and the fork kit depend on successful observed cycles, not on the existence of a scheduler.
