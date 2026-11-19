# Portable rights authority — T8 / A2 / A3 (review proposal)

Status: **PROPOSED, NOT IMPLEMENTED**. Parent: [T8 #681](https://github.com/drevendev/HungryCrab/issues/681); [A2 #682](https://github.com/drevendev/HungryCrab/issues/682), [A3 #683](https://github.com/drevendev/HungryCrab/issues/683) are workstream references to reconcile against their actual issue identities before implementation. This proposal must not be presented as a working attestation protocol.

The existing source-backed `crab serve` must remain intact. The portable route adds a separately gated **artifact-only** entry point; an imported receipt, verdict, checksum or local override is not authority to copy, reimplement or publish.

## Security boundary

The threat is an adversarial, stale, incomplete or transplanted evidence bundle that looks like a successful analysis. A valid JSON document, matching self-declared hashes, green CI badge, source URL or `relationship=own` claim does not independently establish (a) *which tool ran*, (b) *which bytes it observed*, (c) *which source grants apply*, or (d) *what was actually materialized*. Treat the entire uploaded archive and every included manifest, receipt, comment and link as untrusted data.

Three separate witnesses must agree:

1. **Provider witness (outside the archive).** A locally configured, reviewed producer allowlist plus independently retrieved native platform evidence: producer repository and workflow identity, full executed workflow/tool revision, job/conclusion, run ID, attempt, artifact ID and provider-side artifact digest if available. Reject a missing or unmatched identity. A `workflow_dispatch` acknowledgement alone is not such a witness. Do not manufacture platform attestations from the uploaded manifest.
2. **Byte witness (admitted artifact).** The admitted archive digest, canonical member table and each actual file's raw SHA-256, length, role and logical owner are compared with the independently qualified artifact. Admission limits/path controls remain A2's responsibility. A manifest checksum only detects corruption *relative to itself*; it cannot qualify a malicious producer. Unknown/incomplete member inventories, extra members, digest mismatch, unreadable bytes, unsupported formats and absent provider verification remain held.
3. **Source-and-rights witness (verified producer output).** From a trusted, pinned source reader executing in the qualified job, bind the exact full Git object ID and object format of the prey commit, each canonical source path and its blob/raw-byte digest, proven licence/grant regions and selection, origin ceiling, relevant upstream notices and materialization evidence. The offline consumer verifies the correspondence of this witness to the qualified producer and to the imported bytes, not by fetching or checking out the prey locally.

For Git SHA-1, require a full 40-character object ID in portable evidence; for Git SHA-256, require a full 64-character ID **with the declared object format**. Never resolve a seven-character prefix from an offline receipt as part of portable authority. The legacy v1 local materialization receipt's permissive 7–64-character parser remains a separate source-backed compatibility concern; do not reinterpret or automatically upgrade it.

## Evidence records (logical contract, not an implemented JSON API)

The producer's portable result must carry all these independently verifiable facts:

- `producer`: repository identity, immutable workflow/tool commits, native run/attempt/job IDs and status; the consumer binds these to **out-of-band** provider records.
- `request`: stable request identity, selected prey+receiving committed snapshots, policy/config digest and operation; dirty/local receiving edits are explicitly *uncovered* unless separately admitted through a reviewed receiving-only snapshot.
- `bundle`: exact artifact/provider identity, immutable digest, bounded complete file inventory with role, canonical relative path, byte count and SHA-256. No checkout, raw prey cache, Git objects or executable preload.
- `source_witnesses[]`: repository URL, full Git OID and object format, canonical source path, blob OID or raw-byte SHA-256, optional exact source line/range and immutable permalink. Region coverage and grant basis refer to these same source bytes.
- `rights[]`: mode (COPY, COPY_FILE, REIMPLEMENT or IDEAS_ONLY), evidence-backed origin ceiling, licence expression and selected terms, every path-specific grant, unresolved/unsupported clause, upstream copyright/licence/NOTICE content hash and text as required. Missing rights cannot become a permissive default.
- `materialization[]` for COPY: immutable receiving destination path and carried/adapted byte digest, corresponding source path and digest, `verbatim` flag and change description. For verbatim copies, equality is an exact byte comparison. For adaptations, a matching digest establishes the recorded bytes but **not** licence compliance by itself.
- `cleanroom` for REIMPLEMENT: source-reader/implementer separation and the independently checked receiving artifact/draft; author prose alone does not prove separation.
- `omissions` and `status`: explicit partial/error/unknown/unsupported states and missing witness IDs. An incomplete or failed producer run is never promoted to a complete successful source of publication authority.

Avoid duplicating authority in editable `verdict` fields: these are presentations of the verified evidence, not authorization tokens. Each immutable source link used in cards, drafts, notices and review must point to the original commit/path/range, not a mutable branch, repository homepage or unlabeled screenshot. Private source content must not be made public merely to manufacture a clickable link.

## Consumer decision order

1. Resolve the trusted platform producer independently. Any missing, stale, wrong or unverifiable run/attempt/artifact identity => `REMOTE_VERIFICATION_REQUIRED` / HOLD.
2. Admit and verify the complete inert archive under A2 quotas/path/byte limits. Broken, partial, unknown-member or mismatched byte evidence => HOLD/REFUSE. Do not parse executable content as instructions or hydrate prey.
3. Validate every imported source witness against its qualified producer job and exact per-file material. Reject a short/unsupported OID or a witness transplanted between sources, runs or attempts.
4. Recompute the licence, origin, path grant and upstream-obligation decision using the **verified** facts. Unsupported expressions, unresolved composite duties, unknown ownership and absent notices => HOLD, never permissive COPY. `OWN` exceptions need independent ownership evidence, not the imported label.
5. Compare the materialization/cleanroom proof with the imported destination content and the selected receiving snapshot. Missing/changed bytes, wrong mode or unverified clean-room separation => HOLD.
6. Only then offer an artifact-only local review or explicitly authorized dry-run; effectful publication continues through the current `serve` safety gates, native original-revision reconciliation and independent human review. A portable dry-run must not silently obtain new rights by executing `catch`, Git/HTTPS prey fetch or provider lookup.
7. Return a typed `REMOTE_VERIFICATION_REQUIRED` or HOLD with the missing witness and no side effects. Never fall back to source acquisition, an alternate provider, a user-edited verdict or an unverified legacy record merely because the archive looks plausible.

This decision order separates **integrity** (the bytes match) from **authenticity** (the qualified producer actually emitted them) and **transfer authority** (verified grants cover the intended operation). A positive result requires all three; none substitutes for another.

## Actions acceptance controls

Execute only owned synthetic fixtures in GitHub Actions with exact head/run readback; never run prey code. The positive fixture is a fully qualified trusted producer with matching complete archive inventory, full source commit+blob identities, explicit grant region, required notices, receiving snapshot, and a successful authorized COPY dry-run. The following negative controls must each HOLD/REFUSE with **zero** local prey acquisition, zero publication, and no silently created cache:

1. Upload a byte-perfect self-consistent archive from an untrusted producer, or swap the provider's run/attempt/job/artifact ID while preserving its embedded manifest.
2. Change only the uploaded source commit OID, substitute a valid-looking seven-character prefix, or claim SHA-256 object format for a SHA-1 ID.
3. Swap a taken file's raw digest, permute source path/grant region, or alter the admitted destination bytes while keeping a plausible COPY receipt.
4. Remove one notice, inherited upstream receipt or required composite licence term; change the selected OR branch without source-backed permission.
5. Set `relationship=own` solely in the uploaded verdict; remove the independent ownership witness.
6. Claim REIMPLEMENT without verified reader/implementer separation, or attach a COPIED file to an IDEAS_ONLY source.
7. Insert a surplus archive member, duplicate case-folding destination name, traversal/absolute path, symlink, unsupported file type or archive exceeding admitted limits.
8. Supply a partial/failed/cancelled/stale result with `success` written in a self-reported status field, or mismatch the selected receiving revision.
9. Replace an immutable source permalink with a mutable default branch, homepage or source path outside the attested range.
10. Run the artifact-only local consumer with no prey checkout/cache and with network/source acquisition disabled. Positive review/dry-run must work from admitted evidence; denied rights must stay denied without fallback.

Record the **original** job/run/status, fixture IDs, exact source revision and actual pass/fail. Do not call a test executed merely because it is listed here.

## Implementation slicing and non-goals

- **A2 first:** independently qualified provider/admission identity and bounded byte manifest. An unchecked archive cannot be the substrate for rights.
- **T8 next:** pure deterministic rights assessor consuming qualified A2 facts, with typed HOLD reasons and source/grant/materialization witness validation. It must not create a generic public "verified" flag from data bundled by the uploader.
- **A3 next:** inert review/dry-run experience, immutable provenance links, and integration with existing current-receiving, attribution/notice, cleanroom and publication guards. Complete one native Actions positive+negative matrix on the integrated head before any merge claim.

Do not require signed evidence in a format that the provider does not actually issue; instead report unavailable independent qualification as HOLD. Do not add a shadow trust store, auto-fetch prey, weaken old `serve` protections, auto-close parent issues, or claim that documentation alone implements T8. Security and contributor review of the *integration* is mandatory before making the new consumer capable of effectful serving.
