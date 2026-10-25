# Next security hardening batch: MIT repository workflows

Status: implementation in progress, 2026-10-25. H1 remains BLOCKED; the complete batch is not yet
accepted. See [implementation evidence](security-hardening-evidence.md) for delivered slices,
test results and remaining host/deployment gates.
Baseline: `master` at `1c640d14c462a26f376f21a127023633d743a53e` (after #429).
Owner request: scope the next substantial security fix, including the nine host/publication
cards, three repository advisories, MIT-relevant licence defects, #254 and #220.

This document specifies a work batch. It assigns no release version or SemVer major bump.
Creating it does not authorize implementation, deployment, scheduled runs, publication,
credential provisioning, advisory edits or issue closure. No production or test code changes
are part of this planning task.

## 1. Outcome and scope boundary

Make human-supervised meals and reviewed transfers between MIT repositories dependable, while
closing the selected host, secret and publication boundaries. Scheduled work remains restricted
until its actual agent host satisfies #243; a successful CLI test is not host enforcement.

For this batch, **MIT workflows** means:

- an MIT prey and an MIT maw;
- explicit dual-licensing that offers an MIT branch, including MIT OR Apache-2.0;
- an MIT root containing separately licensed packages, assets or previously attributed files;
- uncertain, custom or mixed grants that might otherwise be misreported as MIT or another
  copyable licence when feeding an MIT maw.

The last two cases are necessary negative controls. A repository's MIT root is not permission
to copy every file it contains. Reject or request human resolution for material whose grant
cannot be established; do not expand this batch into a general compatibility engine for every
non-MIT prey and maw combination.

The selected inventory is **24 open issue cards plus 3 published advisory regression tracks**:

| Group | Count | Tracking |
|---|---:|---|
| Host, secrets and publication | 9 | #243, #298, #135, #141, #344, #325, #369, #302, #365 |
| MIT-relevant licence work | 13 | #189, #190, #202, #206, #95, #205, #216, #217, #219, #168, #207, #165, #92 |
| Additional reliability and origin integrity | 2 | #254, #220 |
| Already fixed advisories: preserve and verify | 3 | GHSA-rjrx-3h4x-g7m4, GHSA-vp52-vp37-p63w, GHSA-443x-5484-qr4g |

These are tracking entries, not 27 independent unresolved vulnerabilities. Some cards are
partially fixed or overlap. Reproduce the remaining slice on the implementation base before
changing it. Preserve completed protections instead of reimplementing an old issue description.

## 2. Advisory baseline: existing fixes, mandatory regression coverage

Read through the authenticated repository advisories API during this planning pass. All three are
**published**, not open draft reports, and their published records identify patched releases.
Their presence on the Security page does not mean current master remains vulnerable.

| Advisory | Published severity | Existing patched version | Required evidence in this batch |
|---|---|---|---|
| [GHSA-rjrx-3h4x-g7m4](https://github.com/drevendev/HungryCrab/security/advisories/GHSA-rjrx-3h4x-g7m4) | High | 0.6.1; versions below 0.6.1 affected | No child Git process enters a populated prey submodule and runs its configured filter while fingerprinting, digesting or comparing. Preserve unknown fingerprints where submodule contents cannot be vouched for. |
| [GHSA-vp52-vp37-p63w](https://github.com/drevendev/HungryCrab/security/advisories/GHSA-vp52-vp37-p63w) | High | 0.6.1; versions below 0.6.1 affected | Cache-touching agent Git reads open only a permitted clone root; a Git directory shipped by prey, alternate git/worktree/namespace selectors and program-running options cannot run a configured helper. |
| [GHSA-443x-5484-qr4g](https://github.com/drevendev/HungryCrab/security/advisories/GHSA-443x-5484-qr4g) | Medium | 0.6.2; versions below 0.6.2 affected | Preserve all eight reported shell-resolution families and the later tool-surface repairs, including bounded decisions, globs/short names, nested shells, directory changes, attached options, expansions, tokenization and Windows path mappings. |

The first two were fixed by #380; the third advisory identifies #400 and 0.6.2. Current master
also contains subsequent guard hardening. Validate current source and installed plugin artifacts;
do not copy historical vulnerable snippets back into production code.

Acceptance for all three tracks:

1. Map each reported family to an existing regression or a narrowly added missing regression.
2. Run synthetic, harmless negative controls on Linux and Windows, including real Git process
   traces and installed hook-launcher checks where applicable. Never execute real prey content.
3. Exercise the actual tool event and launcher for supported hosts, not just a classifier helper.
4. Keep known-safe reader controls passing. Denials must finish within the hook's time budget;
   a timeout or launcher failure must not be reported as a successful refusal.
5. Record exact revisions, platforms, results and any unsupported host. A missing run is NOT_RUN.
6. Update advisory status or patched ranges only if new evidence warrants it, under a separate
   publication decision. No new CVE or advisory publication is implied by this scope.

## 3. Host, secrets and publication workstreams

Priority indicates implementation order and acceptance urgency, not a formal CVSS score.

### H1. Agent-host enforcement — #243 — P0 for unattended work

Tracking: [#243](https://github.com/drevendev/HungryCrab/issues/243).

Include this previously deferred item in the plan. The new request authorizes planning it;
it does not start a launcher, supply credentials or enable autonomy.

Target preference from the owner: an isolated Claude Code agent on GitHub Actions, ideally
within free usage. There is no agreed launcher yet. Runner availability, subscription-token
support, service terms, usage limits and costs remain unverified deployment prerequisites.
Do not promise a free server or treat an unavailable model limit as unlimited capacity.

Required boundary:

- Bind the actual agent session and effects to an active maw-owned lease and phase policy.
- Enforce shell/subprocess, direct provider/API and file-edit paths, including effects outside
  Crab's own commands. Block protected-path edits, default-branch pushes, merges, expired or
  paused leases, and effects outside the authorized transaction/budget.
- Prevent policy escalation through agent-editable working-tree consent or a control repo.
  Revocation must affect an already running session; another process/tool must not bypass it.
- Preserve prey and clean-room isolation. A textual shell guard is an additional defence,
  not the required process/filesystem/provider boundary.
- Keep ordinary human development outside an active scheduled session usable. Do not turn
  plugin environment errors into a global blanket denial or redefine the guard exit contract.

Acceptance: a host-specific threat model, chosen launcher and disposable execution environment,
then live adversarial shell/API/edit and pause/revocation tests with harmless markers. If no
verified host is available, mark H1 BLOCKED/NOT_RUN, retain supervised operation and do not claim
that the complete batch enables unattended work. An implementation of other workstreams does
not close #243.

### H2. Scheduled protocol and artifact ownership — #298, #344 — P0/P1

Tracking: [#298](https://github.com/drevendev/HungryCrab/issues/298),
[#344](https://github.com/drevendev/HungryCrab/issues/344).

Required result:

- Bind EAT evidence to the correct prey, maw, commit and verified digest identities; freeze
  or hash the menu/notes consumed by later phases. Detect edits between EAT and SERVE/GROW.
- Verify remaining consent, default-autonomy and revocation behaviour against actual current
  code. An agent cannot grant itself work by changing working-tree configuration.
- Reject artifacts from a previous round or unrelated human activity. Adopt crash-recovery
  effects only when their transaction identity proves they belong to this authorized attempt.
- Reconcile provider truth before recording a PR as open/new. An old or already merged PR
  cannot satisfy the current GROW, trigger another release, or become eligible for closure
  through a drop/skip action. Keep issue ownership scoped to its round.
- Resolve the remaining head-change, PR-only-CI, open-artifact-budget and Git-hook-neutralization
  cases from #298; distinguish external denial-of-service via forged markers from legitimate
  artifact budgets. Preserve prior fixes instead of assuming every historical bullet is open.

Acceptance: stale/current-round and crash-recovery controls; substituted digests and edited
menus; paused/revoked work; changed PR head and PR-only CI; unrelated artifact markers; no
closing another actor's PR; no false HARDEN for a previously released change. Tests must prove
both refusal and absence of unintended provider effects.

### H3. Secret-safe validation and narrow overrides — #135 — P0/P1

Tracking: [#135](https://github.com/drevendev/HungryCrab/issues/135).

Most issuer-format detection and generated-path scanning already exist. Remaining work:

- Remove raw secret-bearing paths from receipt, handoff and pre-publication validation errors,
  including failures that happen before the final scanner. Keep raw paths only internally for
  validation; render a safe opaque identity and useful category/location metadata.
- Define a current maw-owned, per-finding override bound to the finding's file/content identity
  and rule, with an explicit reason. Changed material invalidates the override. No global
  scanner-disable switch, wildcard bypass or matched secret copied into configuration/logs.
- Document the override in the relevant serving protocol after its CLI policy is implemented.

Acceptance: synthetic secret-shaped filenames in missing/changed/duplicate/invalid receipts and
handoffs never appear in errors, logs or published material; refusal precedes push/publication.
Retain detection of existing token/private-key/URL cases and safe-code controls. Test override
matching, staleness, policy ownership and non-matching secrets.

### H4. Installed plugin freshness and activation — #141 — P1

Tracking: [#141](https://github.com/drevendev/HungryCrab/issues/141).

Distinguish an installed plugin's revision from its version. Same dev version with an older
revision must be actionable, never falsely current. Define a safe refresh/reinstall path when
the agent's updater compares versions only; verify the resulting installed revision.

Acceptance: plugin-only installation and interpreter-based launcher continue to work; PowerShell
and existing tool matchers remain covered. Missing/uninspectable provenance is explicit UNKNOWN.
Document actual host hook support and health; do not claim hooks loaded on an unsupported host.
Do not reinstall a running CLI in-process on Windows.

### H5. MCP meal isolation — #325 — P1

Tracking: [#325](https://github.com/drevendev/HungryCrab/issues/325).

An analysis-only MCP menu must not replace the CLI meal later consumed by publication. Use an
isolated output namespace or an in-memory result, with an explicit analysis origin if persisted.

Acceptance: prepare a CLI meal with issue dedup, call and paginate MCP menu, then prove the CLI
meal and serving selection are unchanged. No ledger, source-checkout or provider mutations.
Preserve digest-integrity and incomplete-evidence refusals.

### H6. CI and install-smoke supply chain — #369, security slice of #302 — P1

Tracking: [#369](https://github.com/drevendev/HungryCrab/issues/369),
[#302](https://github.com/drevendev/HungryCrab/issues/302).

- Pin third-party workflow/composite-action references to verified immutable commits, with
  readable version comments and an auditable update mechanism. Review token permissions and
  checkout credential persistence per job. Publication jobs retain only necessary authority.
- Pin the optional skills installer invoked by install-smoke; run it with isolated home/cache,
  disabled telemetry and a minimal environment without developer/cloud credentials.
- Add a repository-level check preventing accidental reintroduction of floating executable
  dependencies in the covered paths. No new runtime security-scanner dependency is required.

Acceptance: Linux/Windows source and wheel installation smoke still passes; installer inputs
and versions are reproducible; canary environment credentials do not reach the installer.
Related pinning in #301/#231 is covered here without duplicating implementations. Unrelated
ledger/YAML/path-cleanup bullets of #302 are outside this security slice.

### H7. Instruction-heuristic evasions — #365 — P1/P2

Tracking: [#365](https://github.com/drevendev/HungryCrab/issues/365).

Keep existing bidi/hidden-character repairs. Address remaining homoglyph, width, inserted
format-character and whitespace variants using bounded matching-only transformations and an
explicit policy. Preserve source evidence; do not silently rewrite content that is transferred.

Acceptance: labelled malicious and benign examples, including ordinary Russian/Greek/CJK,
right-to-left text, emoji and code notation. Report false positives and missed examples.
Matching remains bounded on hostile input. A warning heuristic must not be advertised as a
complete prompt-injection defence or a replacement for H1 and deterministic effect checks.

## 4. MIT-relevant licensing workstreams

### L1. Establish the grant before granting COPY — P0

| Tracking | Selected MIT-relevant slice | Acceptance |
|---|---|---|
| [#189](https://github.com/drevendev/HungryCrab/issues/189) | MIT-like text with operative restrictions, a bundled MIT notice inside proprietary terms, or competing grants must not become plain MIT COPY. | Restrictive/mixed synthetic texts refuse COPY or require HUMAN; genuine MIT text with ordinary copyright/title variations remains recognized. |
| [#190](https://github.com/drevendev/HungryCrab/issues/190) | An unreadable root grant cannot be replaced by an ISC/MIT manifest, API claim or filename. | Unreadable root yields explicit uncertainty/review. An absent root may still use legitimate manifest evidence; pointer files with readable referenced grants work. |
| [#202](https://github.com/drevendev/HungryCrab/issues/202) | A filename pair containing MIT and enterprise/BUSL/unknown terms is not proof that MIT applies to all material. | Ambiguous open-core layouts require resolution; explicit, established MIT/Apache alternatives retain their valid choice. |
| [#206](https://github.com/drevendev/HungryCrab/issues/206) | Preserve complete SPDX expressions containing MIT; quoted tag text and GNU mentions must not manufacture an MIT-compatible grant. | Parse whole anchored tag expressions, including parentheses; never authorize from a truncated MIT prefix. GNU negative controls distinguish operative grants from mentions without expanding every GNU compatibility rule. |
| [#95](https://github.com/drevendev/HungryCrab/issues/95) | Unsupported/future identifiers in mixed or nested MIT-project evidence cannot inherit policy solely from a familiar prefix. | Unsupported leaves remain unsupported; known policies retain their results. A valid MIT choice requires established choice semantics, not an invented permissive classification of the other leaf. |

Use deterministic, bounded logic with the repository's minimal runtime dependencies. A root
licence decision alone must not erase the per-path/review protections introduced in #429.

### L2. Carry MIT notices and upstream provenance through COPY — P0/P1

| Tracking | Selected MIT-relevant slice | Acceptance |
|---|---|---|
| [#216](https://github.com/drevendev/HungryCrab/issues/216) | Retain copyright/SPDX notices in adapted MIT material; detect differing nested package-manifest grants; reject filesystem aliases that publish a different path. | Missing required notices, a foreign nested package grant and case/trailing-dot aliases refuse preparation or require an explicit justified resolution. Preserve existing regular-file/symlink checks and SHA-256 support. File modes/binary/LFS features are deferred unless required by the chosen MIT material. |
| [#217](https://github.com/drevendev/HungryCrab/issues/217) | An MIT file transferred via another repository retains the original upstream source, terms and notice, including same-owner hops. | A two-hop receipt/notice names the intermediary and original source; malformed matching upstream receipts fail closed. Bound parsing/traversal and detect provenance cycles. An unrelated taken path is unaffected. |
| [#219](https://github.com/drevendev/HungryCrab/issues/219) | MIT in REUSE layouts and MIT-containing compound grants retain their applicable texts and obligations. | Carry LICENSES/MIT.txt when applicable; preserve all obligations of a proven AND, and the actual selected OR branch. Do not redo OWN-from-prose, already repaired by #429. General public-domain/share-alike tables are deferred outside mixed MIT paths. |
| [#205](https://github.com/drevendev/HungryCrab/issues/205) | An MIT-containing AND with Apache retains the latter's known NOTICE duties when carried into an MIT maw. | The structured result and prepared receipt preserve both terms' applicable duties. GPLv2 maw compatibility and a general non-MIT matrix rewrite are excluded. |
| [#168](https://github.com/drevendev/HungryCrab/issues/168) | Asset/third-party licence files under an MIT project do not become the root grant or disappear from per-path checks. | An MIT root remains MIT with separate asset restrictions; absent root plus a font licence does not invent a root grant. Recognized REUSE MIT layouts remain supported with honest per-path evidence. |

Preserve #429's independent live provider verification for inferred OWN and current explicit
BYPASS policy checks. A trusted account or an MIT root does not waive third-party obligations.
Do not substitute a new trust flag for notice/provenance validation.

### L3. Avoid blocking valid MIT meals and misleading their operators — P1

| Tracking | Selected MIT-relevant slice | Acceptance |
|---|---|---|
| [#165](https://github.com/drevendev/HungryCrab/issues/165) | Equivalent MIT OR Apache expressions in different orders produce false conflicts and HUMAN cards. | Expression comparison is commutative/associative where appropriate without conflating AND with OR; matching headers are not exceptions, genuinely foreign headers remain exceptions. |
| [#92](https://github.com/drevendev/HungryCrab/issues/92) | Explicit prose offering an MIT/Apache choice must not become an obligatory combination. | Established choice wording selects OR; genuine split wording stays AND; ambiguous/custom terms need human resolution. Non-MIT GPL/commercial choice coverage is deferred. |
| [#207](https://github.com/drevendev/HungryCrab/issues/207) | For an MIT maw, unknown versus absent licences, conflict reasons and recorded relationships agree across sniff, digest, menu and trace. | Unclassified evidence stays HUMAN, not a missing-licence fallback; real conflicts are named. Keep relationship location/documentation consistent with #429. Redesigning the generic GPL summary column is deferred. |

Adding #165 and #92 is deliberate: both can obstruct valid MIT or MIT/Apache repositories even
though their current failure direction is conservative rather than an excessive permission.

## 5. Additional selected issues

### R1. Corrupt compressed reports — #254 — P1/P2

Tracking: [#254](https://github.com/drevendev/HungryCrab/issues/254).

Preserve existing deflate/LZMA handling; contain the remaining Python 3.14 Zstandard decompressor
failure as invalid-report coverage. Discard partial facts from the whole invalid archive.
Nothing is extracted or executed, and existing compressed/decompressed byte bounds remain.

Acceptance: corrupt Zstandard on Python 3.14, an older-Python guarded-import control, corrupt
LZMA and valid-then-corrupt multi-entry archives. Requested acquisition returns explicit missing
coverage rather than an unexpected traceback or success-looking partial report.

### R2. Atomic content-origin invariants — #220 — P1/P2

Tracking: [#220](https://github.com/drevendev/HungryCrab/issues/220).

Choose and document one origin-mutation contract: either prohibit widening, or retain a trusted
base verdict and derive the effective mode consistently. Narrowing must update mode, reason,
trace and permitted prose together. Serialization/loading cannot restore disallowed prose.

Acceptance: widening/narrowing and round-trip tests; commenter/unknown content stays capped;
an AST-level check covers every candidate builder's explicit origin declaration, beyond the
subset of builders reachable from current fixture pairings. Nutrient IDs remain stable.

## 6. Delivery sequence and dependencies

1. Freeze the implementation base and classify every selected issue bullet as remaining,
   already fixed, selected slice or deferred. Establish advisory regression baselines.
2. Deliver H3/H4/H6 and R1 in bounded changes: secret-safe validation, actual plugin freshness,
   supply-chain isolation and compressed-report error containment.
3. Deliver H5 and H2 with frozen evidence and round/transaction ownership. Coordinate H1's
   host architecture in parallel as a planning dependency; no deployment starts automatically.
4. Deliver L1, then expression semantics from L3, then L2 and R2. Shared expression/provenance
   representations should have one owner and migration contract, not competing fixes per issue.
5. Validate H7 and the integrated MIT workflow. Complete H1's actual host tests before widening
   autonomy; otherwise keep that acceptance explicitly blocked and the batch incomplete.
6. Review the final integration, then prepare a separately approved release/version decision.

The unit of delivery can be several PRs. Each closes only its verified scope. A partial fix of
#298, #302, #95, #205, #206, #207, #216 or #219 must not automatically close the entire issue
while deferred acceptance remains. Likewise, landing this document closes nothing.

## 7. Integration acceptance and evidence

- Complete MIT -> MIT catch/digest/compare/menu and reviewed COPY preparation, including real
  synthetic Git histories, MIT notices, an MIT/Apache choice, REUSE, nested foreign terms and
  a two-hop transfer. Assert the generated payload/receipts, not only licence classifier strings.
- Preserve #429 regressions: review flags and exception scopes, comment-origin ceilings,
  notice obligations despite review, and combined OWN/provenance tampering.
- Preserve cache ownership and Feeder repository/wiki download+disk quotas with rollback.
  Standalone catch outside the Feeder quota scope is still documented separately; this batch
  does not silently claim a universal disk quota or expand #236's completed scope.
- Refusals precede unauthorized reads/effects where required and leave maw, ledger, cache and
  provider state unchanged. Use harmless synthetic markers and disposable environments.
- Run Ruff, format check, strict mypy, packaging/sdist and installed-artifact checks, plus the
  full Linux/Windows Python 3.11/3.14 CI matrix and real hard-quota job. No skipped required
  check counts as a pass. No real prey setup, install, build or test is executed.
- Review all fresh findings on the exact candidate head; require green CI there, then verify
  green CI on the actual merged master commit. New findings need their own reproduction.
- Record each criterion as PASS, FAIL, BLOCKED or NOT_RUN with revision, platform and evidence.
  Independent host enforcement evidence is mandatory for H1; mock hooks alone do not satisfy it.
- Release notes identify supported capabilities, remaining limits, compatibility/migration and
  the actual minimum safe CLI/plugin versions. Follow CONTRIBUTING.md for release/reopen
  commits, tags and publication after the release is separately authorized.

## 8. Explicit exclusions and open decisions

- [#204](https://github.com/drevendev/HungryCrab/issues/204): general EPL/CDDL/MPL into GPL maw
  compatibility is outside the MIT workflow target.
- [#94](https://github.com/drevendev/HungryCrab/issues/94): the standalone BSD-4 advertising
  obligation model is deferred. MIT-root third-party restrictions are still preserved/refused
  by L1/L2; no root-MIT blanket exemption is introduced.
- Non-MIT compatibility, dedication/share-alike matrix expansion, general binary/LFS transfer,
  unrelated ranking/marketing/features and all remaining bullets outside selected slices.
- New prey consumption or copying from the hypothesis issues is not authorized by this plan.
  Their technical problems can be fixed in the CLI without starting an eat workflow.
- No new server, workflow, scheduler, recurring automation, credential, paid run, release tag
  or publication is created during planning. Existing private infrastructure is untouched.
- Decide the actual H1 launcher and isolation mechanism before implementing its adapter; assess
  cost/free-runner feasibility at that point. This document supplies no key and guarantees no
  subscription capacity. A host without enforcement remains unsupported for unattended work.
- Assign the release version only after the final compatibility scope is known. The phrase
  "major fix" here names the size of the hardening batch, not a decision to publish 1.0 or 0.7.

Source records: the linked issues/advisories, current source at the baseline revision,
[SECURITY.md](../../SECURITY.md), [CONTRIBUTING.md](../../CONTRIBUTING.md), the
[scheduled operator guide](../scheduled-crab.md) and the [glossary](../design/GLOSSARY.md).
