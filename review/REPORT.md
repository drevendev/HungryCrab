# Open pull request review — 2026-11-16

Both open PRs require changes before approval. This is an author re-audit, not an independent approval. Neither reviewed branch was modified.

| PR | Reviewed head | Native checks at that head | New findings |
| --- | --- | --- | --- |
| [#677](https://github.com/drevendev/HungryCrab/pull/677) | `31956b47fb9b280da83a72f4bc666552fe7b7851` | CI and CodeQL success | Three P1/P2 accounting/dispatch findings plus one P2 adapter-contract finding |
| [#687](https://github.com/drevendev/HungryCrab/pull/687) | `1785044d5cd787c693410420a7c04079964d4815` | CI and CodeQL success | One P1 late-dispatch finding |

## Executed evidence

[Actions run 43949515206](https://github.com/drevendev/HungryCrab/actions/runs/43949515206) executes the [owned probes](https://github.com/drevendev/HungryCrab/blob/4721f977e8853b8d7b85265826d8ca823529fec9/review/probes.py) against the pinned subject revisions. The harness revision is `4721f977e8853b8d7b85265826d8ca823529fec9`; it is separate from the subject revisions.

All eight jobs completed. Every finding reproduced on Ubuntu and Windows, with Python 3.11 and 3.14. PR #677 has five failing assertions for four distinct defects, one passing control and two probes skipped because they belong to the other PR. PR #687 has one failing assertion, one passing control and six other-PR skips. The failure conclusion records violated safety expectations, not environment/setup failures.

The ordinary controls establish that immediate known model overruns are reported as failures, and a live relay can connect and dispose of an owned upstream socket. No native model, donor program, local test, release, merge or publication was run. The relay's upstream is a local socket pair; no real GitHub upstream connection is made.

## R1 — P1 — PR #677 reports unsettled model cost as zero

Location: [operation admission](https://github.com/drevendev/HungryCrab/blob/31956b47fb9b280da83a72f4bc666552fe7b7851/src/hungry_crab/discovery_session.py#L959), [report projection](https://github.com/drevendev/HungryCrab/blob/31956b47fb9b280da83a72f4bc666552fe7b7851/src/hungry_crab/discovery_session.py#L789).

Starting a model operation does not make usage incomplete, and summary only consults the existing mutable completeness flag. After a host interruption before the usage event, next_action correctly returns reconcile, but report still returns actual_model_tokens=0 and model_usage_complete=true. An explicitly stopped in-flight model has the same incorrect snapshot. This can persist after process death, so an unknown charge is presented as a known zero.

Reproductions: test_stopped_in_flight_model_cost_must_be_unknown and test_orphaned_model_cost_must_not_be_reported_as_zero. Both observe 0 where unknown is required.

Required change: derive completeness from unsettled model operations as well as unknown receipts, and restore completeness only when all original charges are known. Preserve the original stop and lease.

## R2 — P1 — PR #677 authorizes using a clock captured before preflight

Location: [execute_operation](https://github.com/drevendev/HungryCrab/blob/31956b47fb9b280da83a72f4bc666552fe7b7851/src/hungry_crab/discovery_session.py#L919).

The timestamp is captured before state/context loading and two live-meter preflights. The expiration comparison then uses that old timestamp. If preflight crosses the ten-minute lease deadline, the provider callback is still invoked after expiry. The deterministic clock probe advances eleven minutes during an effect-free preflight and observes one callback; no sleeping or native spend is used.

Reproduction: test_expiry_during_meter_preflight_must_refuse_dispatch.

Required change: refresh the admission clock after potentially slow preflight/state reads and recheck the applicable lease/session deadline immediately before dispatch. Apply the same clock discipline to other admission paths that capture time before preflight.

## R3 — P1 — PR #687 can start a new upstream connect after scope exit

Location: [upstream dispatch](https://github.com/drevendev/HungryCrab/blob/1785044d5cd787c693410420a7c04079964d4815/src/hungry_crab/fetch/acquisition.py#L340).

The relay checks scope ownership only after socket.create_connection returns. A handler can receive the final header byte, pause before processing it, and resume after scope() has stopped and returned. It then starts a new upstream connection. Registration rejects and closes the result, but the network effect has already happened outside the scope.

Reproduction: test_scope_exit_must_prevent_a_new_upstream_connect. Events hold the final header read until teardown completes; the connector is then called with (github.com, 443). The ordinary live-scope control passes.

Required change: gate the start of upstream connection work against the scope's stop state before the network effect, retaining disposal of already-started late connections. Registration of the completed result alone is insufficient.

## R4 — P2 — PR #677 loses per-operation overrun detection on reconciliation

Location: [record_usage reconciliation](https://github.com/drevendev/HungryCrab/blob/31956b47fb9b280da83a72f4bc666552fe7b7851/src/hungry_crab/discovery_session.py#L492).

The broker checks actual tokens against model_token_limit only in its immediate terminal path. When an operation admitted for 100 tokens first reports unknown and is later reconciled to 101, record_usage checks only the global 30,000-token cap. It clears incomplete usage but never sets meter_cap_violated; budget_within_limits remains null. The identical immediate known overrun correctly returns false. The stopped session does not resume, and the 101 tokens are retained; the defect is the lost known-cap-violation verdict.

Reproduction: test_late_actual_usage_must_enforce_the_original_operation_cap, paired with test_control_known_model_use_and_known_cap_violation.

Required change: reconcile actual usage against the original operation allowance, retain its violation flag and preserve the first stop reason/time. Keep legacy unattributed accounting separate.

## R5 — P2 — PR #677 skips finish after a successful begin returning None

Location: [meter terminal path](https://github.com/drevendev/HungryCrab/blob/31956b47fb9b280da83a72f4bc666552fe7b7851/src/hungry_crab/discovery_session.py#L975), [public protocol](https://github.com/drevendev/HungryCrab/blob/31956b47fb9b280da83a72f4bc666552fe7b7851/src/hungry_crab/discovery_host.py#L51).

The protocol returns object and specifies no non-null restriction. A stateless meter can successfully begin with None and still retrieve usage in finish(None). The broker invokes the model callback, skips finish because it uses None as the failure sentinel, and records unknown usage instead of the available 23-token receipt. If None is intended to mean failed admission, it should be rejected before invoking the model; the current behavior does neither.

Reproduction: test_successful_begin_with_none_ticket_must_still_finish. The callback executes, but finish is never called.

Required change: distinguish a successful begin from an uninitialized ticket, or explicitly validate/reject an invalid ticket before the effect.

## Integration and remaining acceptance

A read-only three-way merge check finds two conflicts in .github/workflows/ci.yml between these PRs. Preserve both focused job groups and their intended full-suite dependencies during native integration; do not choose one side wholesale.

The absence of a production native meter and the dedicated supervised fixed-pair acceptance remain explicit #670 limitations, not newly discovered defects. The historical Windows 0xc0000008 root cause remains unconfirmed under #679; green hosted checks do not diagnose it. Both PRs have no submitted independent approval at review time.

Reviewed: all changed files, the relevant controller/relay callers, tests and failure-path controls, current check metadata and review provenance. Findings above are limited to reproduced behaviors; source-level integration observations are identified separately.
