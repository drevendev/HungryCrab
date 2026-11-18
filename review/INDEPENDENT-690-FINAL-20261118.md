# Independent review and corrections: PR #690 — 2026-11-18

Final receipt: 2026-11-18 07:32:33 UTC. Reviewer: `/root/independent_review_690`, a separate
non-author agent with fresh context. Final verdict: **no actionable findings**
on exact head `dba4d976073d96f2e3dd105dd22635f34f4c9f90` against actual
master/merge base `d01f7b2f3c3f9cee9439d22cecbc645219fc4c82`.

**Independent source review and required Actions checks are complete.
PR #690 is OPEN / READY / UNMERGED.** No formal GitHub-account approval is claimed.

## Original findings and preserved failure proof

The [original independent report](INDEPENDENT-690-20261118.md) requested changes
on a0479fd against 9fb6f1b, after all 12 original changed files and relevant callers
were inspected. Two P1 groups were found:

- Indexed/registered candidates could bypass known exclusions or later holds.
- Full context equality rejected prepared implementation and owned ledger receipts.

[Owned original-head regression run 44249292106](https://github.com/drevendev/HungryCrab/actions/runs/44249292106)
reproduced **5 product failures and 2 passing ordinary controls in each** supported
Ubuntu/Windows x Python 3.11/3.14 environment. All four final job logs were read.
Earlier fixture setup errors in 44247982478/44248679671 remain separate; they are
not product findings. Regression providers are fakes and Git remotes are owned fixtures.

## Corrections and repeated independent review

The f8d2510 correction review also identified an unresolved update clearing an
exclusion, missing binding to the actual receiving root, and lost child-directory
alias protection. All were corrected in 83e3fc1 and independently rechecked.
The proposed non-primary-source late-event trigger was withdrawn as an actionable
current-CLI finding because sealed bundle integrity includes exploration events.

Exact prepared receipt inputs now have content-bound allowances, and shared
publication state recognizes only the admitted ledger and actual load/save byte
receipts. The remaining frozen worktree projection, current root/ref/SHA/policy,
provider heads and state-path protections remain active. Initially dirty unrelated
tracked baselines are preserved; later drift is refused.

The actual current master d01f7b2 (#688/#691) was merged without conflict while
preserving history. The complete 16-file acb3d50 review found no product issue,
and identified the required Ruff raw-regex fix. The single-line correction is
dba4d97, which the reviewer independently verified as the actual final PR HEAD.
No actionable findings remain.

Final files covered: CI, changelog, novelty protocol, eat skill,
discovery_session, exploration, exploration_cli, multiserve, novelty,
novelty_exclusions, novelty_publication, pr_serve, serve,
test_novelty_exclusions, test_novelty_publication, test_release_069_discovery,
plus relevant unchanged callers and existing guards.

## Final exact-head Actions evidence

[CI 44252351512](https://github.com/drevendev/HungryCrab/actions/runs/44252351512):
**SUCCESS, 22/22 jobs**.
[CodeQL 44252351574](https://github.com/drevendev/HungryCrab/actions/runs/44252351574):
**SUCCESS, 1/1**. Actual run metadata matches the final head.

| Full suite | Job | Passed | Skipped |
| --- | --- | ---: | ---: |
| Ubuntu / Python 3.11 | 134629917680 | 5257 | 44 |
| Ubuntu / Python 3.14 | 134629917609 | 5258 | 43 |
| Windows / Python 3.11 | 134629917607 | 5293 | 8 |
| Windows / Python 3.14 | 134629917629 | 5294 | 7 |

All four immutable-novelty environments passed **97 cases each**. Actual final
full-suite and novelty logs were inspected. Checkout logs identify synthetic
merge 02fa079 merging exact dba4d97 into exact d01f7b2.

Lint/format, strict mypy, package/supply-chain, base/clean installations, acquisition
quota/lifecycle, discovery gates, benchmarks and self-digest passed. Controls cover
ordinary/indexed exclusions, removed traces, unresolved replacements, later holds,
provider-marker reconciliation, own prepared COPY/REIMPLEMENT inputs, own saved
ledger receipts and external ledger/source/input/policy/ref/root/alias changes.
Dirty-baseline preserve/refuse controls passed in all supported environments.

Earlier failed style and superseded runs remain historical. They are not
relabeled as passing final-head checks. No additional validation is needed for
this unchanged checked head.

## Handoff

PR #690 is ready and remains unmerged. #671's bounded owned controls are checked;
the issue stays OPEN until native integration. #675 and the broader #670/#679
gates remain open; #678 remains completed. Hosted Windows success does not
diagnose the historical managed-local crash or qualify comparative usefulness.

The reviewer made no edits, local checks, runtime probes, Actions dispatches or
external mutations. The primary agent ran all tests/lint/type/build/install checks
only in Actions. No paid model campaign, local donor acquisition/execution, T2
merge, tag or release occurred.

Source history and the correction branch remain preserved. Before any separately
authorized merge, refresh the exact PR head, current master and required checks.

[Machine-readable evidence](INDEPENDENT-690-EVIDENCE-20261118.json).
