# Supervised discovery beyond traits

For the persisted invoked host cycle, provider budgets, replacements and actual
verification receipts, see [Invoked discovery sessions](discovery-session.md).

`compare` remains a quick deterministic structural pass. A trait menu does not determine
the full useful surface of a repository. `crab explore` records bounded, goal-led inspection
and validates agent-discovered behavior nutrients without making model calls.

Start with the receiving project's user flows, actual problems, architecture, dependencies
and permitted effects. Select relevant facets: `behavior`, `algorithms`,
`recovery-resources-concurrency`, `performance-streaming`, `testing`, `architecture`,
`packaging-operations`, `documentation-contributors`. Inspect source, tests and examples
against stated hypotheses. Record history/issues/wiki acquisition limits before using
absence; shallow history and zero fetched issues are unexamined channels.

## Behavior-led donor searches

`hunt` finds repositories before acquisition; its repository-search channel reads
metadata/topics and README hints. The digest miners already inspect source, tests,
workflows and other acquired channels. A search hint is not evidence that a donor
implements a behavior or that its code may be copied.

Use `crab hunt --for MAW --json` to capture the actual `receiving_context`, then
write a bounded JSON plan **outside the maw checkout**. Saving plan/report files in
the receiving tree can change the worktree identity the plan is bound to.

```json
{
  "schema": "hungry-crab.hunt-plan/1",
  "receiving_context": "replace with the complete object from the hunt report",
  "hypotheses": [
    {
      "id": "strict-jsonl",
      "facet": "input-validation",
      "behavior": "Reject duplicate keys before using corpus-selection JSONL",
      "queries": ["strict JSONL", "duplicate keys"],
      "priority": 0.9
    }
  ]
}
```

```text
crab hunt --for MAW --plan EXTERNAL_PLAN.json --request-budget 8 --language-neutral --json
```

Plans accept 1..20 hypotheses, each with 1..4 bounded keyword phrases, a distinct
ID, facet, behavior and optional finite priority in 0..1. They must match the known
receiving root, commit/ref, worktree, policy and ledger. Changed or unknown inputs,
duplicate JSON keys and provider qualifiers in keyword phrases are refused before
search. Explicit owner `--query` values remain supported; do not combine them with
a behavior plan. The agent chooses semantic hypotheses; the CLI makes no model call.

The request budget defaults to four and is capped at sixteen. Default plans rotate
across supported dependency/first-party runtime evidence, visit distinct facets
before synonyms, and add a language-neutral pool only when explicitly requested.
Unknown runtimes stay unknown. The report retains `query_plan` attribution, actual
requests, budget/provider deferrals, raw search results and deferred languages.
On a provider failure it preserves the attempted request, stops further requests
and labels the result partial; the CLI prints that report and returns a nonzero
exit status. Failed search rows retain bounded, sanitized `error_message` and
`error_hint` diagnostics, also shown in Markdown. Planned searches recheck live
receiving commits, refs, worktrees, policy and ledger before provider requests.
Explicit queries retain their legacy ranking and
unverified language attribution. Pinned acquisition and source/license verification
are still required after discovery. Code/test index search is separate future work.

## Portable donor artifacts

```text
crab --cache-dir LOCAL_CACHE compare owner/repo --maw MAW --prey-digest DONOR_DIGEST --prey-objects INERT_GIT_REPO --no-ledger
```

This verifies donor identity and artifact consistency, reads the pinned commit's regular
Git-object paths and digests only the local maw. No network clone or donor checkout runs.
The public acquisition artifact contains only donor evidence; never upload the local meal,
maw digest, exploration or private registry back to public CI. Existing Feeder meal imports
and their source identity checks remain separate and supported.

New inventories persist bounded, selected repository-relative file paths and line counts.
Comparison uses them across machines and reports `inventory`, `partial-inventory`,
`git-snapshot`, `partial-git-snapshot`, `verified-working-tree` or `unavailable`. An absent
match in a partial/unavailable capability is not proof of absence. Legacy runner roots are
used only when repository-root/commit/clean-tree identity matches. A stale directory or
nested alias cannot supply evidence. An explicit immutable object store can resolve older
artifact paths/ranges without materializing donor content. Symlinks/submodules are not
regular evidence. The resolver retains path-specific licence ceilings; it never weakens
HUMAN to compensate for unavailable evidence.

Dependency ecosystems and first-party runtime evidence are separate facts. A manifest-free
stdlib Python maw can reach Python-specific judgement while retaining zero packages. Docs,
fixtures, examples, vendors and generated files cannot invent a runtime or dependencies.
CI command classification is shared between workflow parsing, traits and exact file evidence.
Root package-script expansion is bounded by depth, command count and bytes; missing scripts,
cycles and unsupported working directories stay explicit. Nothing executes to infer a tool.

## Freeze a comparison and plan

Create `plan.json` with nonempty `scope`, `goals`, `flows`, `constraints`, `author`, `attempt`
and a unique `facets` list. Use `"donor_policy": "MIT-only"` for the supervised pilot.

```text
crab explore init --meal-dir MEAL --maw MAW --plan plan.json
```

The returned directory freezes the original candidate set before hunger/deduplication,
menu and actual digest-artifact hashes, dirty maw identity, source identities, tool source
hash/version, policy and exact consumed ledger snapshot. Old meals without the original
denominator stay qualified as legacy/unknown. Recomparison never rewrites that snapshot.

## Append evidence and decisions

```text
crab explore record EXPLORATION --input observation.json
crab explore report EXPLORATION
```

Every record supplies `author`, `tool`, `attempt` and `reason`. References name `sha`, `path`,
`start` and `end` (inclusive, at most 1,001 lines); they must match the donor/maw revision
and an available file range. An explicit Git object resolver supports legacy source ranges.
The records attest the analyst's inspection; hashes cannot establish semantic truth or an
independent review. The actual CLI source identity is also recorded by the tool.

| kind | Required content |
|---|---|
| `facet` | `facet`, `state`; `examined` needs `hypothesis` and source/maw evidence; `inapplicable` needs evidence and a specific reason |
| `decision` | `hypothesis`, `disposition`, `reviewed`; optional original `candidate_id`; reviewed decisions require source/maw evidence |
| `phase` | `phase`, `state` (PASS/FAIL/UNKNOWN/NOT_RUN), `repository`, `artifact`, `method`; `review-ci` also needs workflow, job, runner and executed steps |

Facet states include `examined`, `inapplicable`, `not-acquired`, `unsupported`,
`budget-deferred`, `policy-blocked`, `unknown`, `unexplored`. Dispositions distinguish
`verified-gap`, `already-present`, `not-useful`, `duplicate`, `out-of-scope`,
`not-investigated`, `unsupported`, `budget-deferred`, `policy-blocked`,
`license-unresolved`, `license-excluded` and `NO_GAP`. Category-only triage remains unreviewed.
NO_GAP additionally needs an `examined_scope` and actual receiving behavior. A facet's
examined hypothesis needs its reviewed decision before exploration can close.

Acquisition, comparison, exploration, implementation, review/CI, publication and
default-acceptance have separate observations. Different repositories/artifacts/methods
remain separate entries: companion billing is not target CI; app attachment is not provider
publication. Successful comparisons or CI cannot advance the other phases. The report names
unreviewed original candidates, unresolved hypotheses and remaining facets. Records are
content-bound and append-only; corrections are later observations, never rewritten history.

## Cumulative inspection and receiving execution (0.6.9)

For a supervised exploration, set `require_inspection_receipts: true` in its
plan. `crab discover contract EXPLORATION --input contract.json` exposes bounded
actual donor/receiving filenames, supported operations and role-specific citation
schemas. For example, `contract.json` can be `{"terms": ["read", "parse"]}`.
An empty witness set grants no citation capability.

`discover find --input find.json` locates literals in pinned source using explicit
`repo`, `sha` and `terms`, with bounded files, total bytes and time. Locations
include actual file line counts. Strings/comments can match; this is navigation,
not an AST or semantic coverage claim. Instructions and README bodies stay excluded.
Implementation/test suffixes consume the read allowance before ancillary files;
other files remain eligible, with uninspected counts retained.

Read selected windows with `discover inspect EXPLORATION --input inspection.json`:

```json
{
  "operation": "reader-body-1",
  "side": "donor",
  "paths": ["reader.py"],
  "ranges": [{"path": "reader.py", "start": 10, "end": 45}],
  "queries": [],
  "related": false
}
```

The existing append-only exploration chain records hash-bound actual packets.
Reusing an operation is idempotent only with identical inputs. `discover witnesses`
returns cumulative reads, so a later licence or test read retains the earlier
implementation window. `discover citations` expands selected `source_evidence`
and `maw_evidence` witness IDs into immutable ranges, refusing unknown IDs and role
swaps. In supervised mode a reviewed decision cannot cite an unread range.
These receipts prove bounded reads, not the analyst's semantic conclusion.

`discover novelty --maw MAW --input scope.json` reads selected/default/confirmed
pending implementations from explicitly acquired local Git objects. The input
names `prefixes`, `default_sha`, optional `default_ref`, `pending` entries with
`sha` and canonical local `ref: refs/crab/qualification/pr-N`, and
`pending_collection: {"complete": true, "as_of": "..."}`. Bodies are deduplicated
while every role/SHA/path binding survives. Missing objects, byte/count limits and
incomplete collection remain explicit. Scoped implementation comparison does
not require proving absence across all historical issues; an open request alone
does not establish an implementation. No fetch or branch switch occurs.

`discover probe` checks the actual interpreter against supported declared
`requires-python` constraints before running. It refuses incompatible or
unsupported requirements instead of silently substituting a runtime or installing
dependencies. Choose an already trusted receiving interpreter before invocation.
Optional `required_receiving_calls: [{"path": "app.py", "qualified_name": "consume"}]`
requires actual calls to those pinned receiving declarations. AST parsing and
printing PASS cannot satisfy it; trace bytes and identity survive in the receipt.

`discover cases OUTPUT --maw MAW --input cases.json` provides a fixed original
receiving-only runner. The plan declares `original_receiving_owned: true`,
`target: {"path": "app.py", "qualified_name": "consume"}` and 2..8 `cases` with
`label`, `role`, JSON `args`/`kwargs`, optional relative text `files`, and `checks`.
At least one target and positive control are required. Each check has a JSON
`pointer` into the actual `value` or `exception` and exactly one `equals`,
`contains` or `not_contains` predicate. Supported data factories are `$fixture`
(relative path in a fresh temporary fixture), `$bytes` (base64), `$stream`
(text stream rejecting unbounded reads), `$io` (ordinary text BytesIO), and
`$helper` (an explicitly declared pinned zero-argument receiving function).
`$base64` encodes original UTF-8 text and `$git_blob_sha1` computes its exact Git
blob identity, allowing consistent original analyzer inputs without executing
those inputs or inventing object hashes. Factory values must be text.
Agent content is JSON data, never executable Python. Cases record actual values,
exceptions, predicate outcomes and function calls; a failed predicate is not
automatically a valid defect or independent acceptance. Donor code never runs.
Returned bytes encode as `{"base64": "..."}`; dataclasses become objects and
iterators become bounded `{"items": [...], "truncated": false}` results. Factories
apply to arguments, not expected predicate values.

## Register a non-trait behavior nutrient

```text
crab explore register EXPLORATION --input finding.json --source-repo INERT_GIT_REPO
```

A finding supplies `disposition: verified-gap`, a stable maw-relative `key` such as
`code.bounded-input`, `title`, `behavior`, `gap`, `adaptation`, `maintenance`, `acceptance`,
source/maw evidence and the author/tool/attempt/reason fields. The CLI rechecks immutable
source ranges, operative root grant and file-specific grants, applies origin and current
maw licence/hunger policy, and registers a `code` card in the same meal. This path requires
licensed repository source; an explicitly
commenter or unknown `origin` is refused rather than promoted to licensed. Unresolved grants
stay refused; retain the finding as a legal/policy hold. Different evidence cannot silently
replace an existing key. The original denominator remains intact and discovered cards are
recorded separately. If hunger is off, the held finding stays visible in exploration/menu
metadata; default `code: ideas-only` keeps it as an idea.

Registration is an analyst-attested gap, not machine proof of usefulness or model discovery
quality. The synthetic control includes a real bounded-read receiving implementation and
a receiving project already using bounded reads. Never execute the donor to establish it.
Further implementation still passes existing receipt, notice, origin, hunger, deduplication,
secret-scan and publication gates. Registration authorizes no widening or autonomous effect.

Exploration never writes acceptance/rejection learning labels. Its learning audit identifies
consumed legacy feedback lacking review evidence. Reconcile such evidence before training;
do not turn unknown or not-investigated into a human rejection. Safe issue-lesson titles use
source/cluster/issue identifiers and numeric facts; model titles remain discarded for
commenter-origin cards. Raw issue titles cannot re-enter the menu or served body.

There is no PR or kept-card quota. Measure examined hypotheses, useful verified gaps,
adaptation/maintenance costs and later maintainer acceptance. Report the selected scope and
pending work. An immutable receipt, green CI or a published PR is not default-branch acceptance.

## Receiving target and report joins (0.6.7)

State the selected ref/SHA/worktree, intended implementation target and platform/dependency
constraints before donor selection. Inspect receiving code/tests and a bounded backlog;
retain acquisition time/coverage/limits, or explicitly not-acquired. Observing a checkout does
not confirm an intended branch. Commands never switch, fetch or widen policy to resolve it.

Comparisons now include optional `receiving_context` metadata. Explore init freezes the actual
observation; a plan may assert `receiving_context.sha`, `selected_ref` and `root`. A mismatch
is refused. Legacy missing metadata is unknown. After receiving code, HEAD, ref or policy
changes, regenerate before registration; historical reports remain readable. Multi-prey menus
require compatible receiving/learning/policy inputs, retain all source cards and keep restrictive
ceilings. Equivalent ledger read timestamps do not change context.

`crab menu --json` adds `analysis_review`; HTML uses the same projection. A validated snapshot
plus registration events must match the derived menu, not an arbitrary latest report. Missing,
stale or corrupt exploration remains unknown. Structural seeds, analyst review, registrations,
holds and conflicting observations are distinct. Phase observations retain repository/head,
method and CI details. Offline views do not authenticate provider truth or the current checkout.
Historical ledger acceptance and nonempty why/how do not prove CI or default-branch ancestry.

An optional `selected: true` decision requires a reviewed verified gap and a known original or
registered candidate. Record alternative choices/reasons separately; selection never renames
IDs, authorizes publication or trains learning. Counts distinguish initial/visible occurrences,
receiving-scoped unique IDs, hidden records, registrations, examined hypotheses and explicit
selections. Unknown selections/costs remain null. Completion links the actual explore report
and preserves earlier deferred hypotheses when a facet later investigates something else.

## Bounded receiving verification transport

A discovery need may declare `verification_mode: runtime` (the legacy default)
or `design`. The latter identifies an additive API/architecture question: write
receiving-specific acceptance criteria without inventing a runtime failure or a
large literal stress fixture. This intent is retained in the controller handoff;
it never supplies verification or semantic acceptance by itself.

`crab discover case-results RECEIPT --maw .` verifies the original execution
receipt, current receiving context, artifact bytes and required function-call
witnesses before projecting every original case/check. Actual pass/fail flags,
observed values, exception evidence and original result hashes survive. Full
returned objects stay in the hash-verified stdout. Values over the default
512-byte display limit become explicit byte-count/SHA256 omissions; use
`--value-bytes 64..8192` for another bounded projection. Retained value bytes are
bounded at 32,000 and the complete result at 64 KiB. An omission is not a passing
value, and an actual failed assertion is not automatically a useful defect.

Each target/positive case records `target_called` during its main invocation
(including generator consumption), outside argument-factory/helper execution.
`target_calls_complete: false` marks an unavailable fixture even if another
control called the target. Legacy missing per-case evidence stays unknown. The
original probe also pins the trusted runner's bytes before execution.

`discover novelty` also accepts up to eight scoped Python declarations:

```json
{"symbols": [{"path": "src/package/config.py", "qualified_name": "Config.load"}]}
```

Supply this alongside the existing explicit prefixes/default/pending plan. Each
acquired role/ref records declaration availability, exact decorator-inclusive
ranges and a source-content hash. Absence, duplicate definitions, unsupported
syntax and resource exhaustion stay explicit. Shared bodies are parsed once
within the existing scope limits. Equality of declaration bytes does not prove
runtime equivalence: imports, globals, helpers and other changed code need their
own comparison. Full file bodies and immutable bindings remain in the artifact.

Source windows use physical LF/CRLF/CR coordinates; Unicode separators inside
source characters do not invent lines or move parser citations. A bounded chunk
whose end exceeds EOF returns only existing lines with `requested_end` and
`range_clipped_to_eof`. A start beyond EOF remains an explicit omission; range,
file, byte and time caps are unchanged.

Set `include_differences: true` in a novelty plan to expose bounded actual
selected-versus-default/pending code changes before the semantic decision.
Identical source pairs share every role/ref binding. Ordered unique-line anchors
are a linear text comparison, not syntax or runtime equivalence; their changed
ranges include one context line. The projection retains at most 16 comparisons,
16 hunks per comparison, 320 lines and 16,000 content bytes. Omitted comparisons,
ranges and hunk counts remain explicit; unread or policy-excluded source is never
reported as established absence. Complete raw files and line endings remain in
the parent artifact.

Behavior registration accepts a separate `source_behavior`: an observed,
code-free claim grounded in the cited donor ranges. `behavior` and `adaptation`
describe the proposed receiving change and cannot establish that the donor
already implements it. Missing source claims, including legacy exploratory
cards, render an explicit unknown in issue previews. Nutrient and ledger IDs
remain unchanged.
