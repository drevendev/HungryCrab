# Owner-requested stop point — 2026-11-18

Recorded: 2026-11-17 22:44:23 UTC. Work is **PAUSED** on the owner's instruction.
Existing GitHub Actions jobs remain running; the local monitoring loop is stopped.
No job cancellation, new run, tag or release was requested or performed at pause.

## Native integration completed

| PR | Reviewed source | Landed master | Method | Validation |
| --- | --- | --- | --- | --- |
| #677 | e1466b3cd8951e8f433ef5a3d0a2a0a4940436e2 | 3baff1e553316c6ec6a592084ddace7abdf448e1 | Native rebase | Source CI14/14 + CodeQL; separate-agent APPROVE; landed CI14/14 + CodeQL |
| #687 | b221583f119ab196c5b0ed8ba085c25174c93839 | 9fb6f1b8af1bef9e4b224c99bf289952ebcbe246 | Native squash; GitHub reported non-rebaseable under linear-history policy | Refreshed-source CI18/18 + CodeQL; fresh separate-agent APPROVE; landed CI18/18 + CodeQL |

Source and landed trees are identical for both PRs. Source histories remain on
`codex/archive/pr-677-e1466b3` and `codex/archive/pr-687-b221583`.
Only CHANGELOG conflicted when refreshing #687; both notes and both focused matrices survive.
The account approval of earlier #687 b843960 remains attributed to that commit;
separate-agent approval of refreshed b221583 is not a new account approval.

Final master: `9fb6f1b8af1bef9e4b224c99bf289952ebcbe246`.
[Master CI44183162178](https://github.com/drevendev/HungryCrab/actions/runs/44183162178)
passed **18/18**; [CodeQL44183162117](https://github.com/drevendev/HungryCrab/actions/runs/44183162117)
passed **1/1**. Full suites: Ubuntu3.11 5183 passed/44 skipped,
Ubuntu3.14 5184/43, Windows3.11 5219/8, Windows3.14 5220/7.
Individual final logs were inspected.

#678 is CLOSED/completed for the bounded relay lifecycle.
#670 remains OPEN for native-meter/supervised acceptance.
#679 remains OPEN: hosted success does not diagnose the historical managed-local0xc0000008 cause.
#675 remains OPEN; no release was cut.

## T2 implementation preserved

[Draft PR #690](https://github.com/drevendev/HungryCrab/pull/690), tracking #671.
Branch: `codex/t2-immutable-novelty-671-integration`.
Source: `a0479fd90fe7b0fc1b5a5904f71935bb3d79615f`.
Base: integrated master9fb6f1b above.
Worktree: `C:\MyProjects\Codex\HungryCrab\.crab\worktrees\t2-671`.
Local source equals upstream and the worktree was clean at pause.
Initial implementation remains on `codex/t2-immutable-novelty-671` at cf6512658ae52cbe9687b68f220ecda0f761cb27.

Implemented: immutable role/ref/SHA/source-range/observation identity for exclusions;
explicit unresolved legacy/unknown status; append-only later supersession preserving
original need/selected decision/history/occurrences/stable IDs; current local and bounded
provider metadata checks before new publication; reconciliation of existing provider
markers; root-controller refusal of unresolved or superseded conclusions.
The skill now freezes novelty scope in the initial exploration plan.
Semantic equivalence remains analyst-owned.

Current-head [CI44188458091](https://github.com/drevendev/HungryCrab/actions/runs/44188458091)
is **IN_PROGRESS, 20/22 successful jobs** at pause:

- Full Windows3.11 [job134415784611](https://github.com/drevendev/HungryCrab/actions/runs/44188458091/job/134415784611): IN_PROGRESS.
- Full Windows3.14 [job134415784624](https://github.com/drevendev/HungryCrab/actions/runs/44188458091/job/134415784624): IN_PROGRESS.
- Four immutable-novelty environments: **60 passed each**.
- Full Linux3.11: **5215 passed/44 skipped**, job134415784696.
- Full Linux3.14: **5216 passed/43 skipped**, job134415784634.
- Lint/format, strict mypy, build/sdist, supply-chain, base/clean installation,
  acquisition quotas/lifecycle and discovery-operation gates: SUCCESS.
- [CodeQL44188458110](https://github.com/drevendev/HungryCrab/actions/runs/44188458110): **SUCCESS, 1/1**.

Pending Windows jobs are not success. Draft state and #671 stay OPEN/PAUSED.
Independent T2 source review has not been performed. Earlier formatting failures,
superseded runs and their passing focused observations remain preserved separately.
Hosted formatting artifacts were applied locally as file edits; no local formatter ran.

## Resume sequence

1. Read this existing Actions run and the actual PR690 head/base/master. Do not dispatch a
   replacement run merely because the chat resumed.
2. On unchanged a0479fd, inspect the original two Windows job conclusions and final logs.
   Preserve any original failure with its exact source/runner/job.
3. Fix remaining bounded defects if necessary; all validation stays in Actions.
   No paid model qualification or local donor acquisition/execution.
4. Once exact-head CI22/22 and CodeQL succeed, record final counts and update PR690/#671/#675.
   Independent source review remains a separate pending stage.
5. Do not merge T2, tag or release without authorization. Preserve the broad open gates and history.

Machine-readable receipt: [INTEGRATION-T2-20261118.json](INTEGRATION-T2-20261118.json).
