# Supervised MIT security hardening: implementation and evidence

Date: 2026-10-25. Implementation base: `1c640d14c462a26f376f21a127023633d743a53e`.
Scope: [next-security-hardening-scope.md](next-security-hardening-scope.md).

Integration target: `4cbcc259989aed5f7f6b3ee1ce4a1478c3f77880`. While this batch was implemented,
master independently fixed MCP meal isolation in `a16987a` and added architecture coverage and
canonical-ref preflight fixes. Those changes are integrated and preserved; this branch retains
the explicit analysis origin and pagination regression on top of the upstream isolation fix.

The supervised CLI changes are implemented in this branch. The complete batch remains
**incomplete** because H1 has no independently verified agent host. No deployment, scheduler,
credential provisioning, release tag, advisory edit or issue closure is part of this change.
The final PR records the immutable candidate SHA and CI runs. Check results below distinguish
source/launcher tests from live host enforcement; a skipped platform test is not a pass.

## Selected inventory and remaining slices

The inventory was checked against the baseline source and authenticated issue records before
implementation. Existing fixes are preserved; the linked cards are not all new vulnerabilities.

| Tracking | Baseline classification | Delivered selected slice / remaining limit |
|---|---|---|
| #243 / H1 | Remaining host boundary | Threat model below; BLOCKED, live enforcement NOT_RUN. |
| #298 / H2 | Partly fixed: fresh timestamps, integrity and guarded work already existed | Bind repository/maw identities, freeze inputs, committed work consent, immediate pause signal, head recovery, explicit identical-tree PR-only CI, hook neutralization and owned budgets. Host bypass remains #243. |
| #344 / H2 | Remaining old-ledger adoption | Durable random transaction intent and payload identity; reconcile only open, current-round artifacts from the publishing account; clear round issue scope. |
| #135 / H3 | Issuer formats and generated-path scanning already fixed | Redact early receipt/handoff errors and tracebacks; exact per-finding committed-maw overrides, reason required, content/rule/location changes invalidate them. |
| #141 / H4 | Interpreter launcher and tool matchers already fixed | Compare installed revisions, UNKNOWN provenance, same-version refresh/reinstall, verify resulting install, show running Python and PATH CLI; activation remains UNKNOWN until observed in the host. |
| #325 / H5 | Remaining CLI meal overwrite | MCP returns an in-memory analysis-only menu; no publishable meal directory, source or ledger write. CLI issue dedup/selection survives MCP pagination. |
| #369 / H6 | Publication actions already pinned; other executable references floating | Pin covered workflow/composite actions, disable persisted checkout credentials, enforce pins in CI. |
| #302 / H6 | Selected installer supply-chain slice | Exact skills version plus integrity-locked transitive dependencies; isolated home/cache, minimal environment, telemetry and lifecycle scripts disabled. Other #302 cleanup is deferred. |
| #365 / H7 | Bidi/hidden-character controls already fixed | Matching-only width/confusable/format/whitespace views, original snippets retained; labelled multilingual controls and bounded hostile input. This remains a warning heuristic. |
| #189 / L1 | Remaining permissive substring grants | MIT operative text must match the established grant; riders or enclosing proprietary terms cannot become MIT or MIT-0. |
| #190 / L1 | Remaining unreadable-root fallback | Neither filename, manifest nor API replaces unreadable root terms; absent-root and readable in-tree pointer controls remain supported. |
| #202 / L1 | Remaining ambiguous named-file choices | Only established MIT/Apache named alternatives get the conventional choice; enterprise/unknown terms need human resolution. |
| #206 / L1 | GNU operative-phrase check partly fixed | Anchored whole SPDX tags preserve parentheses; quoted mentions do not grant rights; first operative GNU family/version cannot be changed by a later preamble mention. Other GNU compatibility work is deferred. |
| #95 / L1 | LGPL exact-policy check already fixed | Unsupported file/GNU/CC/GFDL identifiers stay unknown, including mixed/nested MIT expressions and unknown maws. No new compatibility table. |
| #216 / L2 | Regular-file, symlink and SHA-256 checks already fixed | Retain adapted copyright/SPDX notices; reject differing nested package grants, case and trailing-dot aliases. Binary/LFS/mode expansion is deferred. |
| #217 / L2 | Remaining upstream loss | Matching upstream receipts travel with the intermediary receipt and notice; malformed matching evidence refuses; unrelated paths are unaffected; size/hop/width bounds and cycle detection. OWN does not suppress upstream records. |
| #219 / L2 | OWN-from-prose already fixed by #429 | Carry REUSE MIT texts, all selected AND duties and the actual OR branch. General dedication/share-alike tables remain deferred. |
| #205 / L2 | Selected compound MIT/Apache duty loss | Structured selected terms/duties and combined attribution retain copyright and Apache NOTICE duties. General GPLv2-maw compatibility remains deferred. |
| #168 / L2 | Remaining asset-to-root confusion | Asset licence texts stay scoped evidence; they do not invent a root grant. Declared REUSE grants remain usable. |
| #165 / L3 | Remaining equivalent-expression conflicts | Commutative/associative comparison without collapsing AND into OR; equivalent headers are not exceptions. |
| #92 / L3 | Remaining MIT/Apache prose-choice slice | Established choice wording yields OR; split wording and custom terms remain conservative. Non-MIT commercial choices are deferred. |
| #207 / L3 | Relationship evidence partly fixed by #429 | Unknown stays HUMAN in sniff; warnings retain conflicts; document the relationship in meal/card traces. Generic GPL-column redesign is deferred. |
| #254 / R1 | Deflate/LZMA containment and unsupported-method gate already fixed | Guarded Python 3.14 Zstandard exception containment; a later corrupt member discards all earlier facts. No newly enabled unbounded decompressor. |
| #220 / R2 | Remaining mutable-origin inconsistency | Monotone origin, widening refused; narrowing updates mode/reason/trace/prose together; round trip and AST coverage of every Candidate call. IDs are unchanged. |

## H1: chosen host direction, threat model and deployment gate

The proposed launcher is Claude Code in a disposable Linux execution environment on an existing
GitHub Actions runner, with an independent trusted effect broker. It is a design direction,
not an implemented or supported adapter. The prerequisites below have not been established;
no agent-host workflow was created and no model session or server was launched.

The adversary controls prey data and all model-generated shell, subprocess, provider, MCP and
edit requests. It can edit ordinary worktree files and attempt concurrent sessions, stale-token
replay, policy replacement, direct API use, Git aliases/hooks and credential exfiltration. The
trusted boundary must sit outside that worktree and model process:

- A supervisor binds one process tree and broker capability to the maw identity, phase, active
  lease, transaction and budget. The model cannot write supervisor policy or grant consent.
- A disposable filesystem namespace keeps protected policy, the prey cache and the clean-room
  source boundary outside writable mounts. Separate identities prevent another child process
  from escaping the policy; no Docker socket or runner/cloud credential is mounted.
- The broker holds the only provider/push capability. Direct provider egress is denied at the
  process/network boundary. It rejects merges, default-branch pushes, protected-path changes,
  unbudgeted artifacts, wrong phases and stale/paused leases before effects.
- Pause/revocation is checked independently at every broker effect and terminates or strips
  capabilities from already running process trees. CLI locks are not a host revocation design.
- Ordinary human development outside the supervised session retains its existing tools and
  permissions. Plugin environment errors keep their documented nonblocking exit contract.

BLOCKED prerequisites: a reviewed disposable launcher/image and OS isolation mechanism; actual
Claude runner/session availability; supported authentication and terms; independently verified
costs/limits; broker/provider identity and non-bypassable egress; harmless live shell/API/edit,
concurrent-process, expiry and pause tests. Subscription-token support and free capacity are
unverified. There is no safe inference from the current account's model quota to host capacity.

The implementation deliberately retains human supervision. No host is advertised as supporting
unattended work. H1 cannot be closed by these CLI tests or by the new committed-policy checks.

## Advisory regression map

Authenticated repository advisory records were reread on 2026-10-25. They remain published:
GHSA-rjrx-3h4x-g7m4 and GHSA-vp52-vp37-p63w identify patched version 0.6.1; GHSA-443x-5484-qr4g
identifies 0.6.2. Their published ranges and status are unchanged.

| Track / family | Existing executable regression evidence |
|---|---|
| Submodule child Git/filter execution | `test_git_runner.py`: live harmless-filter controls, no child Git, unknown populated-submodule fingerprints, safe unpopulated controls; digest integration. |
| Clone-root/configuration/helper selection | `test_prey_guard.py`: hostile clone config, foreign Git directories, common/worktree/namespace/program options, safe caught-clone control; launcher invocation. |
| Shell resolution: globs and Windows short names | `test_prey_guard.py` glob/short-name and working-directory cases; both shell event surfaces. |
| Nested shells and attached options | `test_prey_guard.py` shell-wrapper, interpreter and attached-option regressions, including launcher cases. |
| Directory changes and search-to-execution | `test_prey_guard.py` separate-directory-change, search pipelines and safe reader controls. |
| Expansions and tokenization | `test_prey_guard.py` variable/default/brace/expansion and malformed-token cases, later review regression groups. |
| Windows path mappings | `test_prey_guard.py` Git Bash mounts, PowerShell paths, device/share paths and desktop-terminal events. |
| Bounded decisions and launcher failures | `test_prey_guard.py`, `test_hooks.py`, `test_guard_transport.py`: decision budgets, actual hook command lines, isolated plugin-copy launcher, safe readers, UTF-8 transport and explicit nonblocking environment failures. |
| Clean-room isolation | `test_cleanroom_guard.py`, `test_hooks.py`: supported names/events and packaged launcher; live Claude clean-room host session remains NOT_RUN. |

The reported eight shell-resolution families are covered by these grouped existing tests;
later fourth/fifth/seventh-review regressions remain in the complete suite. A source unit test
does not certify a live host. Neither a timeout nor launcher exit 1 counts as a refusal.

## Compatibility, migration and operational limits

- The package and plugin remain the unreleased `0.6.4.dev0` / `0.6.4-dev.0` series. A version
  string alone cannot identify this hardening. Use the reviewed implementation revision plus
  the PR's final green head; no new minimum released version is claimed before a release is
  separately authorized. The three advisory minimums above cover those historical advisories.
- Existing materialization receipts load without an `upstream` field. New receipts may retain
  nested upstream records, bounded to eight hops, 32 children per node and bounded source reads.
  Older clients do not understand that extension: keep CLI/plugin revisions aligned.
- Regenerate old unfrozen EAT evidence. Existing unrelated ledger URLs are not migrated into
  current transactions. Preserve old state for operator inspection rather than forging a receipt.
- Origin widening after construction now raises `ValueError`; construct a new card from trusted
  evidence when its origin genuinely changes. Narrowing and loading cannot restore source prose.
- MCP menu results have `origin: analysis-only` and `meal_dir: null`; serving continues to use the
  ordinary CLI meal. MCP never publishes or writes the ledger/source checkout.
- A scanner override contains only `finding` and `reason`, in committed maw policy. It cannot
  disable a rule, wildcard a path or survive changed content. The current policy is rechecked at
  publication. This is supervised policy, not a host sandbox against an agent with Git access.
- Matching-only Unicode normalization never rewrites transferred content. The warning corpus
  includes labelled Latin evasions and ordinary Russian, Greek, CJK, RTL, emoji and notation.
  It cannot promise complete prompt-injection detection.
- CI action SHAs were verified against their publisher's tagged refs. Dependabot updates those
  pins and the isolated npm lockfile; `scripts/check_supply_chain.py` rejects floating refs,
  persisted checkout credentials and missing registry integrity in covered paths.
- Feeder repository/wiki disk/download quotas and cache rollback remain required checks.
  Standalone catch outside that scope has no newly claimed universal disk quota.

## Validation record

### Review follow-up: #436–#440

The review at `be7d693` reproduced four remaining acceptance defects despite green
[CI 40175197841](https://github.com/drevendev/HungryCrab/actions/runs/40175197841), plus a focused
hook-test gap. The follow-up changes and executable controls are:

| Issue | Correction and regression evidence |
|---|---|
| #436 | Copyright metadata must match bounded year/holder forms; separate sentences, clauses and unstructured phrases refuse, including case changes and standalone SPDX tags. `test_security_review.py` exercises detection and complete COPY refusal plus ordinary notices. Unrecognized holder formats require review. |
| #437 | Immutable root expressions retain their operators; named component texts cannot replace an explicit AND with OR. REUSE requires a matching immutable manifest or per-file declaration. Complete preparation tests cover weaker receipts, equivalent reordered expressions and both terms' AND notices. |
| #438 | GROW requires initial/current/committed automatic PR consent and a current persistent ledger. Process-local effect checks cover built-in Git effects and ledger writes as well as provider calls. Tests revoke ledger/PR permission on the same operation and assert zero subsequent writes/pushes and unchanged persisted state; guards do not escape to ordinary caller operations. |
| #439 | Every retained upstream ancestor is checked for required notices, independently of OWN. Tests cover damage at several supported depths, positive distinct multi-hop notices, ownership and existing unrelated-path/bounds controls. |
| #440 | `test_git_hooks.py` stubs ordinary/scoped process execution for fetch/push/update-ref, verifies final override precedence and an existing empty directory during execution, and checks cleanup on success, nonzero exit, timeout, OS error and output-limit failure. Scoped directories stay on the quota volume; read-only calls allocate none. |

Initial follow-up integration: 710 passed, four unavailable symlink controls skipped on Windows
Python 3.11. Further effect-boundary integration: 134 passed. Final expanded integration:
762 passed, four unavailable symlink cases skipped; nine pure metadata controls passed after
the final capitalization refinement. The full matrix's job timeout is 30 minutes to accommodate
the observed 18.5-minute Windows real-Git baseline and additional regressions; individual
performance/security test limits are unchanged. The PR records the immutable final
follow-up head, repeated full CI, second review and actual merge-commit CI. H1 remains BLOCKED;
the process-local checks do not certify an agent-host sandbox or enable unattended operation.

Repeat-review compatibility controls also retain terminal punctuation and C/HTML-commented
copyright notices. The licensing/preparation group passed 325 tests with four unavailable
symlink skips, and native Python 3.14 passed 92 review regressions before that final metadata
refinement; eight direct full-body/tag controls verify the added metadata forms. Final-head
CI repeats the complete matrix after these changes.
Same-development-version digests from licence-policy revision 2 are also invalidated and
re-mined under revision 3, so cached pre-review grants cannot survive an ordinary rerun.

### Original implementation checks

Local platform: Windows, Python 3.11.16 and installed Python 3.14.7. Every source change is
reviewed/tested against synthetic data; no real prey setup, test, build or install was executed.

| Criterion | Status | Evidence |
|---|---|---|
| Focused loop/publication/COPY integration | PASS | 125 tests, Windows Python 3.11; `.venv/security-integration-2.log` (local log, not packaged). |
| Final grant/publication integration | PASS | 469 tests, Windows Python 3.11; four unavailable symlink cases and native 3.14 decompression are explicitly skipped here. |
| Final guard/ownership/summary controls | PASS | 997 tests, Windows Python 3.11; includes all cache guard cases and verified OWN/fork controls. |
| Final enclosing-grant review | PASS | 429 tests, Windows Python 3.11; separate restrictive clauses before MIT and after choice prose refuse, ordinary titles remain recognized; four unavailable symlink cases skipped. |
| Windows CI timing review | FAIL, corrected; final CI pending | Candidate `0994048`, [run 40173874353](https://github.com/drevendev/HungryCrab/actions/runs/40173874353): Windows 3.11 had 3,589 passed/six skipped; 3.14 had 3,590 passed/five skipped. Each had one wiki timing failure (1.08/1.23 s against 1.0 s). The predicate now folds ASCII whitespace and scans once, avoiding per-character Unicode transforms, offset allocation and duplicate scans. The original one-second limit is unchanged. The full control took 0.14 s locally; 5,000 deterministic ASCII differential examples agreed with the previous two-view contract; 93 native Python 3.14 safety controls passed. |
| Ruff, format, strict mypy | PASS | Local checks; final candidate CI repeats them. |
| Initial local Python 3.11 suite | FAIL | Earlier working tree: 3,522 passed, 8 failed, 43 platform skips. Changed valid-grant/summary fixtures, README defaults and verified OWN duties; isolated known-safe PATH controls. The wiki timing control passes without concurrent full-suite load. Final candidate requires a new full run. |
| Initial local Python 3.14 suite | FAIL | Same eight earlier-working-tree findings; 3,522 passed, 43 platform skips. Superseded only by final candidate runs, not by a claim that skips passed. |
| Source/wheel and optional installer smoke | PASS | Installed sdist and wheel CLI/digest/HTML, real stdio MCP, and locked portable skills installer. |
| Fresh sdist/wheel, contents and self-digest | PASS | Isolated `.venv/security-package/dist`, successful `check_sdist.py`; 14 miners, zero failures, healthy visibility and budget. |
| Final-head Linux/Windows 3.11/3.14 CI matrix and installed-artifact jobs | NOT_RUN (pending) | [PR #434](https://github.com/drevendev/HungryCrab/pull/434) records the final candidate. Preliminary run [40173273984](https://github.com/drevendev/HungryCrab/actions/runs/40173273984) at `8c88a41092e96e31879d09685080e28a56e2dd5e`: Linux 3.11/3.14, lint, base and both clean-install jobs passed; Windows tests were still running when this record was written. The additional grant review requires fresh final-head CI. |
| Real Linux hard-quota job | PASS (preliminary candidate) | Dedicated job in [40173273984](https://github.com/drevendev/HungryCrab/actions/runs/40173273984) passed at `8c88a41092e96e31879d09685080e28a56e2dd5e`; final-head CI repeats it. Local Windows skips are not evidence. |
| Live agent-host shell/API/edit/isolation and revocation | BLOCKED | H1 prerequisites above; no host adapter is certified. |
| Live clean-room host activation | NOT_RUN | Launcher/event tests do not establish session activation. |
| Actual merged-master CI | NOT_RUN (pending follow-up review) | The owner authorized merge after the #436–#440 fixes and a clean second review; the PR records the eventual merge revision and its CI. |
| Release/advisory publication | NOT_RUN | Separate publication decision required; no version/tag/advisory changes. |

No selected issue is automatically closed by this batch. In particular, partial issue slices
and H1 retain their unresolved/deferred acceptance. The PR's final checks provide the candidate
revision and provider run URLs without rewriting this document after every asynchronous check.
