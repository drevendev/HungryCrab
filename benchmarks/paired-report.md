# Paired yield/cost reporting for 0.6.7

This offline importer never executes an arm, acquires prey or launches a model. It reuses digest
integrity, receiving identities and ordinary meal review. Full B2 remains `meal_benchmark.py`;
this bounded tool does not replace independent judging or close #250/#256.

## Freeze before measuring

Acquire shared evidence once through existing bounded acquisition. Freeze receiving/prey pins,
selected receiving worktree/ref, policy/ledger/backlog, as-of, depth/channel coverage and limits.
Seal search responses before either arm; lazy first-arm acquisition is setup cost. Install exact
arms in separate environments and retain immutable revision/build metadata plus each arm's
`analysis_context.installed_source_identity()` (implementation and shipped policy data).

A plan contains two simple arm names, pair names, an even repeat count, an `arm_sources` mapping
with exact `version` and 64-hex `source_sha256` for each arm, and `shared_inputs`:

```json
{
  "arms": ["baseline", "candidate"],
  "pairs": ["control"],
  "repeats": 2,
  "arm_sources": {
    "baseline": {"version": "EXACT_VERSION", "source_sha256": "64_HEX_SOURCE_HASH"},
    "candidate": {"version": "EXACT_VERSION", "source_sha256": "64_HEX_SOURCE_HASH"}
  },
  "shared_inputs": {
    "as_of": "FROZEN_UTC_TIME",
    "receiving": {"control": {"sha": "FULL_RECEIVING_SHA", "worktree": "clean", "selected_ref": null}},
    "prey": {"control": {"sha": "FULL_PREY_SHA"}},
    "policy": {"control": "64_HEX_POLICY_FINGERPRINT"},
    "ledger": {"control": "64_HEX_LEDGER_FINGERPRINT"},
    "digest_inputs": {"control": {"prey": "64_HEX_PREY_INPUT_HASH", "maw": "64_HEX_MAW_INPUT_HASH"}},
    "backlog": {"status": "not-acquired", "acquired_at": null},
    "coverage": {"history": "shallow", "issues": "not-acquired"},
    "limits": {"hypotheses_per_pair": 2, "review_minutes_per_pair": 10},
    "held_out_protocol": "Synthetic controls only; real evaluation needs separate authorization"
  }
}
```

Replace all symbolic pins. Policy uses `fingerprint(actual_policy)` and ledger uses
`ledger_identity(actual_consumed_ledger)`; incidental read timestamps are ignored. Optional
`digest_paths` maps sides to relative raw directories (defaults `prey-digest`, `maw-digest`).
Recorded absolute runner paths never locate digests.
Receiving worktree identity is required (a hash for a frozen dirty tree); unknown cannot certify
paired inputs. A selected ref is required: a string asserts that branch and null asserts detached
HEAD, both checked against observed receiving metadata. Prey
defaults to a clean immutable tree. Menu and both digests must share the preregistered clock.

For each pair and side, compute `paired_report.digest_input_identity(shared_manifest)` before
either arm runs. The fingerprint binds repository/ref/worktree/shallow state, acquired API hash,
depth, ignore rules, licence context, coverage, wiki metadata and effective Markdown budgets.
It excludes runner paths, implementation-specific results and measured token totals. Report
checks every run against these frozen inputs, including repeated arms. Human review/time limits
remain operator declarations; this offline tool cannot independently verify their execution.

```text
uv run python benchmarks/paired_report.py freeze plan.json experiment
```

The new directory seals a plan and A/B then B/A schedule. Existing destinations cannot be
overwritten. This registers declared order, not independent execution proof. Execute arms
separately with common immutable evidence, clock, budgets and substantive instructions. Preserve
cold/warm policy and do not let an earlier arm train the later ledger.

## Seal/import results

Preserve raw `menu.json`, full operational digests and all original/hidden/discovered candidates.
For each scheduled id, supply actual measurements with installed-source identity:

```text
uv run python benchmarks/paired_report.py seal experiment RUN_ID raw-bundle measurements.json
uv run python benchmarks/paired_report.py report experiment roots.json paired-result.json
```

`roots.json` explicitly maps scheduled run ids to raw bundle directories. Receipts seal raw bytes
and measurements without changing them. Relocation uses a derived roots map. Existing receipts
and report outputs cannot be overwritten. Changed plans/receipts/raw bytes, mismatched pins or
policy/ledger/effective digest inputs, incomplete digests, invalid token fields, links and unsafe
paths are refused.

`paired_report.measure(callable)` measures portable wall/self CPU and installed source identity.
Unix child CPU and cumulative peak RSS have explicit scope/platform units. Unsupported Windows
child/RSS counters remain null; these peaks are not per-pair deltas or summed simultaneous RAM.
Actual model tokens/currency come from the real harness, never Markdown token estimates. Missing
values remain null, not zero. Record acquisition, CI setup/queue and review costs separately in
additional measurement fields. An init-file hash alone does not identify a build.

## Report template

| Evidence | Keep separate |
|---|---|
| Inputs | frozen plan/revisions, installed version/hash, as-of, limits, coverage |
| Candidates | initial/visible/source occurrences, receiving-scoped unique IDs, hidden, registered |
| Investigation | examined hypotheses, NO_GAP/already-present/not-useful, holds, deferred work |
| Outcomes | explicit selected changes; implementation/CI/publication/default observations |
| Cost | wall, self/child CPU, scoped RSS, raw bytes, Markdown/total estimates, actual model usage |
| Integrity | immutable raw hashes/receipts, derived relocation mapping |

Unknowns remain unknown. A common analyst audit is separate from per-arm outputs and independent
acceptance. Semantic comparison ignores only `compared_at`/`exploration_snapshot` provenance;
evidence, modes, scores, hidden/initial/discovered cards and policy remain. Duplicate content is
exposed and unique bundle/receiving-scoped ID counts are not inflated. Byte equality does not
prove independent executions or semantic acceptance.

## Held-out follow-up (separate authorization required)

The original 30 pairs/three known gaps are training/regression inputs. Before inspecting new
source, reserve a modest fresh sample (for example four pairs), receiving constraints, exact
arms, two balanced repeats and a total review/model budget. Freeze the same hypothesis/time
limit for both arms. Start with authored positive-gap and already-present/NO_GAP controls;
neither proves the deterministic miner found arbitrary behavior.

Keep common review and blind arm assessment distinct. Retain attempted/deferred/incompatible,
duplicate and legally held findings. Independently verify provider outcomes before calling them
accepted yield. Publish costs/uncertainty even if finding count stays unchanged. No quota,
promised percentage gain, weight retune or lower benchmark threshold is a pass condition.
Synthetic tests validate infrastructure only and authorize no real sweep or model spend.
