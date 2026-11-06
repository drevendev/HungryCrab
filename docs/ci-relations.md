# CI reusable-workflow relations — `ci_relations.json`

Hungry Crab retains its existing `ci.json` parser output unchanged. The separate
`ci_relations.json` artifact (schema `hungry-crab.ci-relations/1`) relates a
caller job's already-parsed `uses` string to a workflow from the **same
explicitly supplied** parsed inventory. Neither the miner nor the relation
builder fetches, evaluates or runs any workflow. This is inert prey metadata,
not a successful execution or dependency/cycle analysis.

The sidecar records `caller_path`, `caller_job`, `raw_uses`,
`target_path` (nullable) and `status` for each callsite. Statuses are:

- `resolved-workflow-call`: an exact local `./.github/workflows/*.yml` or
  `*.yaml` target is indexed and advertises `workflow_call`.
- `target-not-workflow-call`: the indexed local target does not advertise
  the callable trigger.
- `not-in-supplied-inventory`: an otherwise valid local target is not among
  the indexed entries; **not** evidence the file is absent.
- `remote-unresolved`: an external repository/action reference; no remote
  fetch or guessed target.
- `expression-unresolved`: any GitHub expression in the reference; never
  interpolate or evaluate it.
- `invalid-local-path`: malformed, nested or escaping local path; no
  path normalization that could conceal traversal.

Indexing is sorted by the original workflow path and capped at **64**
workflows. Jobs within each caller are sorted by original job ID, and emission
is capped at **256** relations. An independent **1 MiB cumulative nested
JSON byte budget** also bounds repeated long `uses` strings; `relation_bytes`
conservatively charges escaped UTF-8 record content, actual nested indentation,
array separators and first-item array overhead as written by the digest.
`byte_limit` reports the cap. `truncation.workflows`,
`truncation.relations`, `truncation.bytes` and `truncated` distinguish partial
evidence. When a callsite would exceed the budget, emission stops and sets
`truncation.bytes`; no `raw_uses` is silently shortened or replaced. The
original parsed `ci.json` rows are preserved. The byte budget covers emitted
nested relation payload (including JSON nesting overhead), not the fixed metadata envelope.
An optional smaller limit can be used for a bounded caller; it cannot exceed
the default caps. If a single relation is emitted under a limit of one and
another exists, `truncation.relations` is true.

These relations express syntax and declarations, not proven runtime
availability, permission, action execution, dependency order or cycles.
Treat any unresolved, unindexed or truncated observation as unknown. The
original `ci.json`, `ci.md` and workflow parser rows remain the source for
other CI facts. See issue #619 for the receiving-owned acceptance boundary.
