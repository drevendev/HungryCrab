# Supervised discovery beyond traits

`compare` remains a quick deterministic structural pass. A trait menu does not determine
the full useful surface of a repository. `crab explore` records bounded, goal-led inspection
and validates agent-discovered behavior nutrients without making model calls.

Start with the receiving project's user flows, actual problems, architecture, dependencies
and permitted effects. Select relevant facets: `behavior`, `algorithms`,
`recovery-resources-concurrency`, `performance-streaming`, `testing`, `architecture`,
`packaging-operations`, `documentation-contributors`. Inspect source, tests and examples
against stated hypotheses. Record history/issues/wiki acquisition limits before using
absence; shallow history and zero fetched issues are unexamined channels.

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
