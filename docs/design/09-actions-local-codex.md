# 09 · Local Codex with Actions execution

Owner-selected direction, 2026-11-16. Planned in two stages:
[0.6.11 foundations](../releases/0.6.11-scope.md) and [0.7 stable operation](../releases/0.7.0-scope.md).
This is a product contract, not an executed deployment or spending authorization.

## User outcome and execution placement

The user orders crabbing in local Codex, reviews a nutrient menu with evidence,
chooses useful changes and obtains local issue/PR preparation and authorized
delivery. All Crab processing scripts run on GitHub Actions. No prey repository
is downloaded to the local machine.

| Work | Where | Boundary |
| --- | --- | --- |
| Order, receiving goal, semantic hypotheses, nutrient judgments, final explanation | Local Codex | Normal user-invoked context; no hidden native workers or background model supervisor. |
| Acquisition, sniff/digest/compare, index/inspect, history/issues, evidence/rights validation and deterministic reports | Actions | Trusted pinned Crab code; prey is inert data, never installed, built, tested or executed. |
| Receiving probes and patch tests | Actions | Explicitly authorized maw code/owned probes, isolated from prey and publication credentials. |
| Artifact retrieval/inert reading, own-maw editing/Git and authorized issue/PR effects | Local Codex host tools | Thin transport/publication, without a local Crab processing/acquisition runtime. |
| Independent review when requested | Authorized local agent-host reviewer | Real separate input/result/source linkage, or visibly incomplete review. No Actions model service. |

The maw's own clone/Git operations remain allowed. Existing local CLI modes remain
available for other workflows; the new route must not invoke them as a fallback.
Recovery of existing local work is outside this placement contract and authorizes
no new local prey acquisition.

## No local prey repository

Forbidden locally: clone, bare repo, Git objects/packs, source archive, checkout,
mirrored file tree, prey cache, submodule/LFS payload or lazy blob fetch. Review,
errors and serving checks must never silently acquire one.

The owner-clarified evidence profile permits **bounded inert derivatives**:
nutrients, complete structured analysis facts/pages, selected source excerpts,
source links/coordinates/hashes, operative licence/notice texts and receiving-only
probe results. They are data, never executable attachments, and must not reconstruct
or export the repository. Required licence/notice texts are not replaced by hashes.

This distinguishes repository acquisition from evidence needed for judgment.
A source-free profile may omit excerpts; it cannot claim source inspection or
COPY authority when necessary evidence is unavailable.

### Clickable provenance for every nutrient

Owner clarification, 2026-11-16: the essential requirement is a link showing where
each proposed improvement came from, so the user can open the source repository
and assess it directly. Bounded evidence artifacts are permitted; links are mandatory.
Opening the GitHub source page for inspection does not authorize repository acquisition.

Every nutrient card and final issue/PR draft must expose:

- A short explanation of the source mechanism and what is proposed for this maw.
- Clickable source permalinks to the exact repository, full commit, file and
  relevant line range, in the form `https://github.com/OWNER/REPO/blob/FULL_SHA/PATH#LSTART-LEND`.
  Generate them from the recorded source identity, never from a guessed branch URL.
- The relevant receiving location and applicable licence/notice evidence links,
  when those support the claimed gap or transfer authority.

Keep every source occurrence in structured evidence and preserve the main links
in the readable card and publication trace. Repository homepages, mutable branch
links, hashes alone, summaries or excerpts without an openable source link cannot
replace exact provenance. Discussion-derived ideas retain original issue/comment
permalinks and their origin ceiling; do not fabricate a source-file citation.

The Actions producer validates source path/range existence and correspondence to
the excerpt at the pinned revision. Wrong commit/path/range, missing source or
inaccessible evidence remains explicitly unresolved; it cannot certify a supported
nutrient. Mechanical tests cover mismatched evidence and link preservation through
artifact transport, local review and final issue/PR preparation.

## Bounded request cycle

1. Local Codex freezes the goal, pair, selected/default/confirmed-pending maw context,
   policy, operation and finite limits in an inert request.
2. An explicitly authorized host tool dispatches the trusted Actions workflow.
3. Actions validates identities and runs only pinned trusted tooling. Prey
   source/Git/cache stay within its bounded worker scope.
4. Actions exports an evidence manifest or typed failure/hold. No remote model,
   issue/PR creation, merge or release occurs in the acquisition worker.
5. Local transport correlates the exact run/attempt/artifact and checks metadata,
   hashes, archive bounds and schema before exposing inert data.
6. Local Codex judges applicability/novelty. Missing evidence becomes a bounded
   remote request under the same investigation, or an honest unresolved stop.
7. After current-state reconciliation, local Codex prepares the own-maw patch or
   issue. Receiving verification runs on Actions. Publication uses separately
   authorized local host credentials and existing rights/serving gates.

0.6.11 may advertise continuation/transfer as unsupported. 0.7 must implement the
complete supported route rather than declaring unsupported stages complete.

## Request/result envelope v1

Reuse meal, exploration, case, ledger and receipt schemas inside an additive
transport envelope. Command names remain an implementation choice, not a claim
that an undocumented CLI command already exists.

| Record | Required fields/facts |
| --- | --- |
| Request | Schema version, owner investigation/request IDs, operation/parent result, goal, prey repo/full SHA, maw repo/selected SHA, default/confirmed-pending identities and observation time, tool/workflow revision, policy/config hashes and limits. |
| Operations | Fixed-pair meal; bounded index/find/inspect continuation; novelty/rights check; receiving-only probe/patch check. Advertise unsupported operations. No arbitrary shell, executable input or free-form model prompt. |
| Limits | Shared requests/acquisitions, download/disk/output bytes, archive members/unpacked bytes, source windows, whole-investigation/per-job wall time. Freeze supported defaults/maxima before implementation tests; separate inference usage availability. |
| Result identity | Request hash, immutable tool/workflow identities, pair/context/policy hashes, producer repository/run/attempt/job, timestamps, artifact ID and provider digest. Missing identity is unavailable/legacy, never invented. |
| Result contents | Status/capabilities; complete JSON/paged summaries; stable nutrient IDs; actual read/source witnesses; novelty/rights basis; original receiving results; omissions/continuations; deterministic usage and diagnostics. |
| Evidence table | Canonical relative path, type, bytes, SHA-256 and producer for every file. Source excerpts also bind original prey SHA/path/blob/range and applicable grant. No undeclared or executable attachments. |
| Local decisions | Original proposals/revisions, accepted/rejected/held reasons, requested/performed review, publication identity and current reconciliation, retaining stable maw-relative nutrient keys. |

For dirty/local-only maw work, never claim a remote SHA covers the edits.
Initially require an explicitly selected accessible committed snapshot or return
`receiving-snapshot-unavailable`. Later receiving-only patch/snapshot transport
needs explicit authorization, exact hashes and visibility protection. No implicit
push of local changes to make a remote job work.

Receiving-probe requests reference an original authorized maw path at its pinned
revision (or an admitted receiving-only snapshot), rather than accepting arbitrary
inline code. The runner never substitutes a donor script or test for that probe.

### Initial bounded profile

Proposed v1 defaults, to be enforced and qualified before release: request metadata
at most 32 KiB UTF-8; one prey, with no repository-search phase; 300 MiB shared
download allowance and 1 GiB acquisition filesystem capacity; 20 minutes per job
and 45 minutes for the investigation; artifact archive at most 32 MiB, unpacked
content at most 128 MiB and at most 4,096 regular members; source windows at most
60 lines/8 KiB per response, with exact continuations. Applicable finite provider
request and continuation-count limits must be sealed with the implemented adapter
before dispatch; an unspecified/unsupported required bound refuses admission.

All declared pages/JSON must fit an admitted export. If they do not, return an
explicit incomplete result or bounded remote continuation, never silently omit
the tail and claim a complete bundle. Export uses an allowlist of generated
evidence roles, not a renamed source-tree archive. A bounded licensed own-maw
patch, where explicitly requested and cleared, is a receiving deliverable rather
than an alternate channel for exporting the prey repository.

## Authority, integrity and failure handling

- Resolve trusted producer repository/workflow independently of the uploaded
  manifest. Bind selected ref, actual executed revision, run attempt and artifact.
  A self-reported hash is not independent certification of behavior.
- Pin trusted tooling/actions to reviewed full revisions. Never use scripts or
  a local Action from the prey/maw as the analysis implementation.
- Acquisition has necessary read access only, no publication/model credential.
  Receiving tests use separate scopes. Prey execution is forbidden on every worker.
- Archive admission rejects absolute/drive/UNC/traversal paths, aliases/duplicate
  names, symlink/reparse/special files, bombs, unsupported nesting and excess
  declared/actual bytes before materialization.
- Provider archive digests and per-file hashes cover different objects; check
  each where supported. Mismatch is a hard refusal, not a warning followed by
  admission. Unavailable verification stays explicit.
- Expired/deleted artifacts, missing logs, cancellation, quota failures, failed
  miners and partial/stale evidence have separate states. No automatic rerun,
  success-bundle export or local acquisition follows them.
- Owner request IDs do not make `workflow_dispatch` exactly-once. Reconcile uncertain
  dispatches before retry. Preserve duplicate-run evidence and actual costs.
- Preserve private-artifact refusal. Public artifacts can expose receiving/source
  information; no implicit public mirror, wider token or visibility override.
- Bind path grants, origin ceilings and upstream notices to transferable evidence.
  Missing/held rights stay held. Remotely backed serving checks must not populate
  a local prey cache. Clean-room reader/implementer separation still applies.

## Lifecycle and costs

Keep dispatch/admission, acquisition, analysis, artifact availability, analyst
selection, receiving verification, independent review, publication and adoption
separate. Empty results are valid. Hashes/green CI cannot establish semantic value.

Return the local model at meaningful completion/failure or decision boundaries.
Use native host completion notification or bounded non-model waiting. If unavailable,
report the limitation; do not repeatedly resample a long chat to poll or settle costs.

Record local model input/cache/output/reasoning as available; Actions queue/job
time and usage; requests/bytes/storage; failed attempts, operator interventions and
delivery. Cached input remains included. Unknown money or usage is not zero.
Supervised work can proceed without strict cost certification; efficiency claims cannot.

## Existing foundation and platform constraints

Reuse [Feeder](../feeder.md), `action.yml`, `.github/workflows/feeder.yml`, bounded
acquisition and portable meal/evidence/serving records. Feeder already exports
model-free artifacts. Its reusable workflow currently has `workflow_call`, not
the new complete order/continuation surface. Some publication checks still require
prey objects. These are implementation gaps, not already shipped capabilities.

GitHub references checked on 2026-11-16:

- [Dispatch syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatch):
  register the trusted workflow on the default branch through reviewed integration;
  a feature-branch file alone is not an installed dispatch service.
- [Artifact download](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts):
  access/expiry constrain recovery. Configure finite retention and record expiry.
- [Artifact validation](https://docs.github.com/en/actions/tutorials/store-and-share-data#validating-artifacts):
  GitHub's download action reports digest mismatch as a warning; Crab must refuse admission.
- [Privileged workflow security](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target):
  never execute untrusted downloaded code/artifacts. Our static rule also forbids
  prey execution in unprivileged jobs.
