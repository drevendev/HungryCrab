# Discovery 0.6.11: operator and host preflight failures

The 5 November 2026 comparison at
`f4eaeb0e7fe3c3a01fa9fad7f20d075cfc124b49` is **unqualified**. The operator
(Codex) declared the host ready without testing the actual full comparison
path on both arms. This is a preparation and experiment-host failure. It does
not establish that the candidate product is better or that the baseline's
semantic discovery is worse. Acceptance #540 remains unmet.

## What happened

All twelve trial processes and six separate masked reviews finished. There
were ten assessment records covering five distinct donor repositories. Two
baseline trials stopped after planning. No fresh receiving runtime probe ran.
All six reviewers found coverage inadequate.

Two raw positive proposals were retained, including a baseline proposal whose
CLI record was refused. Both reviewers supported one candidate design; the
baseline proposal received one confirmation and one unsupported verdict. The
observed counts were candidate 1/1 and baseline 0/1. Only HungryCrab had a
confirmed proposal; the required gain in two of three projects was absent.
Empty proposal sets in the other projects do not establish precision.

The one supported original design is tracked in [#619](https://github.com/drevendev/HungryCrab/issues/619).
Its utility is separate from comparative qualification, code transfer,
implementation, publication and adoption. Creating that issue was separately
authorized after the study; the original study had no publication effects.

## Mistake 1: source-view compatibility was not tested with the released reader

CI exported immutable bare Git repositories. The 0.6.8 reader uses
`<sha>:./<path>` when resolving donor evidence absent from a digest's line-count
inventory. Git rejects that relative syntax outside a working tree. The current
host's reader could read the same export, so tests through that reader concealed
the published-baseline incompatibility.

Two baseline `explore record` operations consequently refused individual review
evidence. Native source excerpts and the raw positive still reached masked
review, so the denominator was not erased; the baseline CLI round trip still
failed. A later read-only reproduction on an already exposed control confirmed
the relative-path failure. No trial was replayed to hide it.

Required prevention: derive the same bounded inert working-tree representation
for both arms, then actually import each arm's own reader and execute each
arm's `compare -> explore -> record` path. A current-reader proxy is not proof
of published-baseline compatibility. Never patch the published 0.6.8 source.

## Mistake 2: a successful hand-driven control was generalized too far

The exposed useful design control, its continuation and independent review
established their recorded path. They did not exercise every entry point and
source representation of the actual trial executor on both arms. Owned tests
and component-level native checks likewise did not establish the integrated
release-comparison path.

Required prevention: run the actual executor on owned/exposed controls, using
the actual arm Python/source/skill identities, same model and effort, transport,
receiving case writer, controller and accounting. The control must finish a
useful investigation and genuinely continue after an encountered already-present,
duplicate, held or oversized source. Retain the original receiving proof and
acceptance artifacts. An independent non-planner review must support those
observed outcomes before fresh inference.

## Mistake 3: budgets were verified as ceilings, not as a usable complete cycle

The host preserved the 30,000-token ceiling and complete study usage but spent
large amounts on planning and source/novelty context. Many runs reached only
one assessment and lacked a feasible continuation or original probe stage.
Large metadata packets, hashes and unused case instructions cost actual input
tokens, including cached input. Finishing a metered call did not mean a useful
investigation had finished.

Required prevention: project evidence by actual scope and exact ranges, retain
full originals and omissions, and qualify a complete runtime and design path
under the unchanged budget. Source-empty packets must not consume an assessment
call. Original cases must be authored in receiving-only context; implementation
or test copying is not an optimization. Do not silently increase budgets, use
estimated tokens as actual usage, replay interrupted calls or score shallow
record counts as utility.

## Costs and preserved history

The invalid study used 409,831 actual native tokens: 126,142 baseline, 151,069
candidate and 132,620 reviews. Final study usage was complete and in cap. Earlier
preparation/control has a retained lower bound of 124,098, for a combined native
lower bound of 533,929. Earlier final tails, root-chat tokens, monetary totals
and CI currency remain unknown. Failed controls and studies remain failed.

Private planning, cohort forecasts, prompts and raw receiving evidence remain
local. This postmortem publishes the operator errors and aggregate observations,
not the private preparation directory or an acceptance claim.

## Fresh inference is a separate gated action

The original thirty sources are now exposed. A later owner-authorized repair
does not make them fresh again. Before another comparison, preserve the failed
source and receipts, qualify the repaired full executor on owned/exposed data,
freeze receiving/default/pending context and prior exposure, and seal one
untouched cohort and executable protocol. Keep the published 0.6.8 baseline,
three maws, twelve trials, six masked reviews, all original caps and acceptance
thresholds. Do not choose repeated cohorts until a favorable result appears.

Report execution, useful unique proposals, precision denominators, coverage,
rights and adoption separately. A technically completed but inadequately covered
comparison is unqualified, regardless of its apparent arm score.

## Repair observations, 6 November 2026

The real portable-digest/object round trip now records an omitted-inventory
source reference through both actual CLIs (0.6.8 and 0.6.11.dev0). Two earlier
failed integration attempts are retained. The second accidentally tested local
comparison, which does not bind an object store; component reader success did
not establish that distinct route. Future controls use the actual portable route.

The first new full-host engineering block at `1eed6a1` was still **not ready**:
the owned candidate fixture used invalid `backlog.status: complete`, so the
candidate CLI correctly refused configuration before any model call. Baseline
completed original typed receiving probes, rejected present behavior and reached
a source-supported runtime gap in 26,226 actual tokens and 196.905 seconds.
That component result does not pass the failed two-arm block or independent
review. No fresh cohort was selected or run. Preserve those 26,226 tokens and
the zero-inference candidate refusal; do not reset or conceal the attempt.

The control preparer must validate runtime and design plans through the actual
candidate `discover init` before writing an executable control profile or allowing
either arm to spend model tokens. Fixture preparation is not exempt from the
same configuration preflight required of fresh qualification.

The corrected four small controls at `5685161` completed in 101,553 actual
tokens; an independent 21,880-token review supported their recorded outcomes.
They still did not establish the original exposed real-project gate. Its actual
full executor rejected existing behavior, then deferred both the deliberately
oversized packet and the useful continuation: the latter required an estimated
13,387 tokens with only 9,819 remaining. The real control stopped at 20,181
actual tokens without a useful completion. It remains failed, not qualified by
the smaller fixture results. No fresh cohort was selected.

The native worker's directory was inside this repository. Codex automatically
loads AGENTS instructions along that path, introducing inputs beyond the sealed
worker protocol and its explicitly supplied matching skill. Isolate experimental
workers using the invocation-only `project_doc_max_bytes=0` setting. Preserve
the operator's normal instructions/configuration, worker safety base instructions,
disabled tools, model/effort, exact skills and unchanged actual-token accounting.
Measure the resulting actual usage and repeat full-head controls before treating
this as a fix. The [official instruction-discovery documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
describes the automatically loaded files and their configurable combined limit.

The isolated JSON transport preflight used 5,364 actual tokens. Isolation alone
is not a completed budget repair. Keep the explicit matching skill, exact source
bodies and coordinates, and remove repeated protocol/preparation metadata from
model transport. Short receipt aliases must expand to their original donor or
receiving read IDs before CLI validation; retain both representations and the
alias map. Keep complete original facts for review and explicit omissions.

Admission remains an estimate with a 6,500-token prefix/reply reserve: use the
repository's 3.5-byte ASCII estimate and a stricter allowance for non-ASCII bytes.
It must never settle usage, reset a budget, erase cached input or override the
30,000-actual-token guard. The revised envelope still requires actual completed
real-project controls and independent review; smaller estimated prompts do not
establish successful investigation or qualification.

The next real control at `ac8db9b` still stopped without useful completion at
19,312 actual tokens: the continuation estimate was 11,297 with 10,688 remaining.
Retain that failure. Its source-relevance assessment spent tokens on the known
NuGet repository whose actual CLI verdict and operative grant were HUMAN-held.
The earlier hand-driven control had rejected that hold before semantic spending;
the actual executor lacked that deterministic step. Moving it to the actual
executor is necessary; another prompt-size estimate is not its substitute.

Before semantic admission, let each actual arm compare the immutable source and
check an operative selected-path grant when its repository verdict is HUMAN.
An established path grant can avoid a blanket overview hold; an unknown operative
grant stays held with **utility unassessed**, zero semantic-call cost and the
original precharged controller attempt. No hold becomes a negative usefulness
judgment or copy clearance. Keep distinct ordinary receiving verification,
oversized deferral and useful continuation in the real exposed protocol.

Published 0.6.8 does not contain the newer `licensing.path_grants` API. The adapter
must record that capability as unavailable and preserve its actual CLI HUMAN
verdict, never import the candidate resolver for it. Both real arm environments
now retain the known NuGet hold in a deterministic component preflight. That
component check is separate from the still-required full native controls.

The next real control at `fd2ee3f` paid 9,574 actual planner tokens before the
actual comparison correctly refused a dirty requirementslib digest. The inert
archive materializer had omitted empty gitlink directories, making three
uninitialized submodules appear deleted. Preserve that failed control and its
original manifest. Create a new source view retaining only those empty directories,
without fetching or executing submodules, then regenerate both actual-arm digests.
Validate every digest's clean state, public identity and immutable pin before
any paid planning, and exercise both actual CLI comparisons before the next
real control. Never repair an artifact by editing its manifest's dirty flag.
