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
The interpreter uses `-I -B`: cwd alone does not put the receiving tree on the
import path. An optional exact boolean `import_receiving: true` prepends only
the declared receiving root and its `src` directory, leaving the original probe
source unchanged. The receipt records the mode and command. The default retains
isolated imports; inherited `PYTHONPATH` is never forwarded. Use this mode only
for trusted receiving code, never donor/cache paths. This is not an untrusted
code sandbox. An import failure leaves the probe unverified; it does not establish
an application defect.

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
| `discover index` | `repo`, immutable `sha`, `need`, optional literal path `terms`, directory `prefix`, `limit` (1..64), `symbol_files` (0..16). Lists real source/test filenames and bounded Python declaration ranges before selecting windows; no README/instruction bodies, imports or execution. Path matches, declarations and test filenames establish navigation, not usefulness, coverage or transfer permission. |
| `discover search` | `queries`: provider (`github`/`gitlab`), surface (`repository`/`code`), query; GitLab global code is explicitly unsupported. One request per query, no hidden retries. Code blob SHAs are never commit pins. |
| `discover packet` | `repo`, immutable `sha`, `need`; explicit `paths` with optional Python AST `queries`, automatic `terms`, or explicit `ranges` of `path`/`start`/`end` (at most 60 lines each). 32 paths, 64 KiB/file, bounded syntax worker and related import/test associations. Associations are not coverage. |
| `discover view` | Existing `artifact`, estimated-token `budget` (512..3500), optional `cursor`. Source windows first; remaining fields, including notices, retain continuation. Cursor binds canonical payload hash and budget. No acquisition, model call or permission decision. |
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

Use the index before guessing paths or reading arbitrary first lines. Its source
declarations include actual pinned start/end ranges; a declaration may itself be
longer than the returned 60-line window. The JSON retains observed/unreturned
file counts, deferred symbol work and tree omissions. Tree navigation is bounded
to 128 directories, 10,000 entries and 5,000 regular paths; the index never follows
links/submodules or obtains missing blobs. Other structural languages and oversized
Python files remain explicit. Read selected source/test windows through `packet`
before making a semantic decision. An index cannot close an inspection phase.

Python declarations retain lexical qualified names and actual end lines.
Bounded literal `contains` queries select declaration names without evaluation.
Packets retain the complete bounded `structural_result`, including worker limit
reasons and unselected matches. Up to eight distinct source ranges are selected
by caller query order, then source order; duplicate and deferred counts remain
explicit. Priority applies to returned worker matches, not to nodes beyond an
exhausted worker limit. Packet byte omissions remain separate from that limit.
A clipped window retains `node_end` and `range_truncated`; continue with explicit
`ranges` instead of treating sixty lines as the whole function. An out-of-file
range remains an omission. `view` requires the full raw JSON as authoritative
evidence. Its token estimate is not a model-usage receipt. Navigation and pages
never establish semantic usefulness, test coverage or transfer permission.

An open request is not an implemented improvement. A concrete new mechanism
for an unimplemented receiving need may be useful: link the existing issue and
identify the added implementation detail. Already implemented and equivalent
confirmed pending implementations are excluded from novelty. Unknown pending
access stays unknown and cannot establish a verified gap.

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

## Model-use settlement when semantic work is refused

After reserving an action, the invoked host may settle a completed model stage
with `crab discover usage SESSION --input USAGE.json` before recording its semantic
result. Supply `attempt`, a unique host `operation`, `author`, `tool` and actual
`model_tokens` (input including cached input plus output). Use `null` when actual
use is unknown: this stops the session with unknown usage. An exact retry is
idempotent; changing a receipt under the same operation identity is refused.

This records cost only. It does not advance a phase, refresh a lease, accept a
finding or write acceptance learning. Late settlement of a known attempt is
allowed after expiry, a rejected semantic result, a receiving change or a stop;
it cannot grant more work. Actual overrun is retained and stops further work.
The remaining-token value on an existing lease reflects the settled total.

After separately settling a model stage, subsequent deterministic action records
carry `model_tokens: 0`; do not charge the same stage again. Existing action-only
receipts remain supported. Keep `reviewed: false` for unresolved exploration
dispositions (`unsupported`, `budget-deferred`, `policy-blocked`,
`license-unresolved`, `not-investigated`). Such observations can support a bounded
replacement without being reviewed gaps. A decision still needs a matching
actual child observation; never weaken that check to settle a model bill.
