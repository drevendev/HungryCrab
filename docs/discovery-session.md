# Invoked discovery sessions

`eat --python` remains the deterministic acquisition/digest/comparison pipeline.
The installed **eat skill**, running in the user's invoked agent host, supplies
semantic investigation. `discover` owns the bounded queue, immutable receiving
identity, actual receipts and stopping decisions. It does not call a model or
start a scheduler. See [discovery.md](discovery.md) for exploration decisions,
registration and the existing per-meal publication path.

## Freeze receiving needs

Read actual receiving source/tests and check the default branch and confirmed
pending work before selecting donors. Keep unknown coverage explicit. Print the
current identity with `crab discover context --maw PATH`. Copy that entire object
into a plan outside the receiving worktree:

```json
{
  "goal": "Bound the receiving input flow without adding runtime dependencies",
  "receiving_context": {"copy": "the complete discover context result"},
  "limits": {"requests": 16, "donors": 6, "hypotheses": 12,
             "replacement_rounds": 2, "model_tokens": 30000, "seconds": 2700},
  "needs": [{
    "id": "bounded-input",
    "flow": "A user imports a large local report",
    "expected": "Memory and decompressed input have finite bounds",
    "observed": "Describe the actual receiving limitation or proposed benefit",
    "impact": "Explain the receiving consequence",
    "constraints": "Python 3.11+, standard library, preserve valid report behavior",
    "queries": ["bounded report reader", "stream decompression input limit"],
    "maw_evidence": [{"sha": "ACTUAL_RECEIVING_COMMIT", "path": "src/reader.py",
                      "start": 10, "end": 25}],
    "backlog": {"status": "partial", "as_of": "ACTUAL_ACQUISITION_TIME"}
  }]
}
```

The placeholder context, commit, path and ranges must be replaced with actual
evidence. `backlog.status` is `checked`, `partial` or `unavailable`. A checked
record also needs `default_ref`, `default_sha`, `method` and a bounded `pending`
list. This records the host's coverage; it does not establish semantic novelty
by itself. Limits may be reduced, never raised above the displayed hard bounds.

```text
crab discover init SESSION --maw PATH --plan PLAN.json
crab discover next SESSION
```

## Consume the actual leased action

`next` returns `search`, `acquire`, `inspect`, `verify` or `decide`, an owned
`attempt`, remaining model budget, receiving need and current lead. Execute that
action and append its actual receipt:

```json
{
  "attempt": "COPY_THE_LEASED_ATTEMPT",
  "author": "invoked host identity",
  "tool": "actual host/tool version",
  "model_tokens": 1234,
  "result": {"requests": 1, "candidates": [{"repository": "owner/project"}]}
}
```

```text
crab discover record SESSION --input RECEIPT.json
crab discover next SESSION
crab discover report SESSION
```

The search result above is an example, not an instruction to invent candidates
or token use. Other action results are:

| Action | Actual result required |
| --- | --- |
| acquire | `meal_dir` from a fresh receiving-bound compare of the reserved repository; or explicit `status` of `unavailable`, `license-held`, `unsupported` and a reason |
| inspect | `exploration`, an actual receiving-bound `explore` state directory |
| verify | Explicit `evidence_level`: `source-supported`, `behavior-verified`, `design-proposal` or `unrun` |
| decide | Matching actual exploration `hypothesis`, `disposition`, receiving-specific `reason`; gaps also require `adaptation`, `maintenance`, `novelty` |

A behavior-verified result requires `expected`, `observed`, `receiving_sha`,
`probe_owner: "receiving"`, `probe_sha256` and `execution_receipt` from an actual
original receiving probe. `crab discover probe OUTPUT --maw PATH --input PLAN`
accepts `source` (the original probe file), `author`, `expected`, `controls` and
`original_receiving_owned: true`; inspect the original code before execution.
Never use donor source/tests as a probe. The CLI checks real process results,
context stability and artifact hashes; a PASS string is insufficient.

A design proposal instead requires `acceptance_artifact`: actual `path`,
`sha256`, full `receiving_context`, `original_receiving_owned: true`, a nonempty
`criteria` list and an `assessment`. This is an original receiving acceptance
document, not an executed runtime fix or independent human acceptance.

After already-present, duplicate, disproved, unsupported or held outcomes, the
controller tries the next distinct supported lead, then bounded replacement
queries. It retains prior holds and unfinished needs. Exact recorded retries are
idempotent. An expired attempt returns `reconcile`: determine its actual effects
and record the existing attempt instead of blindly repeating a provider request.
`discovery_session.drive` is the Python host adapter: it **calls** the supplied
trusted action handlers and records their actual receipts. It never supplies
fabricated semantic judgments or a second acceptance ledger.

`spent` is the conservative precharged request/acquisition budget. Unused or
uncertain reservations are retained; individual action receipts record actual
requests and outcomes. Do not report a reservation as a confirmed completed
network request or donor acquisition. Model-token receipts record actual use.

## Source and provider tools

All tools below take `--input PLAN.json`. Source is inert untrusted data;
instruction/README bodies are excluded. None imports or executes donor code.

| Command | Important plan fields and bounds |
| --- | --- |
| `discover search` | `queries`: provider (`github`/`gitlab`), surface (`repository`/`code`), query; GitLab global code is explicitly unsupported. One request per query, no hidden retries. Code blob SHAs are never commit pins. |
| `discover packet` | `repo`, immutable `sha`, `paths`, `need`, optional Python AST `queries`, `related`, `source_url`, `byte_budget`; 32 paths, 64 KiB/file, bounded isolated syntax worker and related import/test associations. Associations are not coverage. |
| `discover history` | `repo`, `sha`, `paths`, `hypothesis`, `reason`, optional `commits`; bounded pinned path log/diffs, shallow history and omitted bytes remain explicit. |
| `discover issues` | `repository`, selected `numbers`, `hypothesis`, `reason`; bounded issue evidence, commenter origin remains IDEAS_ONLY. |
| `discover frontier` | `roots` containing actual source-supported relationship edges; verifies canonical public target identity and an immutable pin, at most one hop. Each verification request consumes budget. |
| `discover rank` | Bounded `candidates` and `limit`; relevance, marginal needs, prior exposure, evidenced lineage and adaptation cost. Equivalence groups are proposals; no votes or IDs migrate. |

Attach provider tools to the same active session using `session`, `attempt`,
`operation` and `request_budget` in their plans. A leased search accepts exactly
one query. Additional issue/frontier requests charge the same global budget
before effects. Distinct operation identities cannot replay a charged uncertain
operation. Standalone tools have their own explicit per-invocation budget and
must not be used to evade an invoked session's total limits.

The host must meter actual model usage across its entire invoked investigation,
including planning/review, and stop before starting work it cannot bound. A
reported overrun remains actual spend and fails the budget gate. When usage is
unavailable, use `discover stop SESSION --reason usage-unavailable`: actual token
use becomes unknown and cannot support an efficiency claim. Other explicit
stops are `external-dependency` and `policy-or-legal-dependency`. These preserve
the uncertain lease for review; stopping is not scoped completion.

Reports separate scoped needs, verification levels, unknown novelty coverage
and each meal's original structural seed denominator. Publication, CI, human
acceptance and later default-branch retention remain separate outcomes. Legal
adoption uses actual selected path grants and obligations; replacing a held donor
does not change its policy or license status.
