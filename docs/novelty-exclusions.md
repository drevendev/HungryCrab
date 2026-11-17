# Immutable novelty exclusions

An `already-present` or `duplicate` judgement concerns an implementation, not
arbitrary duplicate prose. New reviewed exclusions require a typed basis in the
exploration's original frozen scope. This verifies provenance and actual source
reads; semantic equivalence remains the analyst's responsibility.

Include `novelty_scope` in the `explore init` plan. It uses the existing
`discover novelty` input fields: explicit receiving path prefixes, default
ref/SHA, explicitly acquired confirmed pending refs/SHAs and pending-collection
coverage. No ref is fetched or checked out by these commands. Missing objects,
changed refs, omitted files and unsupported scope remain unresolved.

The CLI reads immutable implementations, retaining every role/ref binding and
full source body in `snapshot.json`. It records its actual UTC observation time;
a supplied provider `as_of` remains a separate provider-coverage declaration.
Repeated initialization with the same inputs reuses the first frozen observation.
Later ref movement cannot rewrite it. The selected binding retains its real ref
(or `HEAD` for a detached selection).

`explore report` exposes the frozen scope identity, observation time, bindings
and omissions. The full source remains in the snapshot. A reviewed exclusion
adds `novelty_basis` to its ordinary decision:

```json
{
  "schema": "hungry-crab.novelty-exclusion-basis/1",
  "scope_sha256": "ACTUAL_FROZEN_SCOPE_HASH",
  "role": "receiving-pending",
  "ref": "refs/crab/qualification/pr-17",
  "sha": "ACTUAL_ACQUIRED_PENDING_COMMIT",
  "evidence": [
    {
      "path": "src/reader.py",
      "start": 10,
      "end": 18,
      "content_sha256": "ACTUAL_UTF8_RANGE_HASH"
    }
  ]
}
```

These are placeholders, not executed evidence. Select the actual role/ref/SHA
from the frozen scope and hash the exact retained physical lines, including line
endings. The recorder verifies the file and range bytes and attaches the original
observation time and file hash. Ordinary donor and selected-maw evidence still
describe the investigated need; those old ranges cannot substitute for a pending
implementation basis. No byte comparison certifies semantic equivalence.

## Later implementations

Use `explore reconcile-novelty DIRECTORY --input PLAN.json` for a later
observation. Its plan contains `operation`, `original_event`, `scope`,
`novelty_basis`, `author`, `tool`, `attempt` and `reason`.

`original_event` names the exact earlier decision event SHA. `scope` is a new
explicit receiving implementation plan. `novelty_basis` has the shape above but
omits `scope_sha256`: the recorder assigns the new observation identity itself.
The CLI saves the full new scope in a hashed artifact and appends a
`novelty-reconciliation` event. An exact retry is inert; changing an operation's
plan refuses. A raw `explore record` cannot manufacture this event.

The event records later supersession and holds publication. It preserves the
original decision, selected flag, hypothesis, source occurrences, candidate ID,
denominator and all earlier events. It never proves that the baseline already
contained the later implementation or that the investigation independently
discovered it. It remains possible to read the original result in the report.

Effectful serving checks these holds and current frozen refs/context again
before issue or PR creation, alongside the existing fresh provider marker
reconciliation, licensing, path grants, receipt scanning and publication guards.
For scoped findings, the provider must also supply current default and declared
pending heads. The built-in GitHub client reads only that bounded metadata under
a shared 60-second deadline, with no source fetch. Changed/closed pending heads,
a changed default, unavailable metadata or a client without this capability hold
new creation. Already existing marked artifacts can still reconcile without a
new publication. Provider identity is never inferred from stale local refs.
Unknown scope or unavailable/moved refs hold publication; there is no implicit
fetch or automatic equivalence judgement. A dry-run preview grants no effect.

## Compatibility

Older snapshots and events remain readable. A legacy duplicate claim is an
analyst assertion with unknown implementation authority; it is not upgraded to
verified pending equivalence. Reports retain the original records and keep
unresolved hypotheses visible. Unreviewed new assertions may also remain pending.
The exploration event chain is the only judgement history; this adds no second
ledger and changes no stable nutrient key. Deterministic fixtures and Actions
checks establish these mechanics, not useful-change yield or model qualification.
