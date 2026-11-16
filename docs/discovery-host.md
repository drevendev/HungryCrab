# Strict discovery host boundary

Strict discovery requires reviewed, in-process host code. A plan's
`host_metering` object is a consistency declaration, not admission. Before
creating a session, the invoking host explicitly binds a `UsageMeter` with
`discovery_host.host_meter`. The controller calls its effect-free `preflight`,
binds its source, qualification-evidence SHA-256 and scope to the session, and
rechecks that identity before new work. A missing, unavailable or changed
adapter refuses new effects. An existing uncertain lease is reconciliation-only.
No plan, CLI flag, environment variable or donor artifact loads adapter code.

**There is no shipped production native meter.** Standalone `discover init`
therefore refuses strict sessions even with syntactically valid JSON. The owned
test meter demonstrates controller mechanics only. Native adapter qualification,
independent source review and a supervised named-pair acceptance run remain
separate requirements; green CI cannot provide them. Reports retain unknown
strict budget compliance and distinguish owned-fixture runtime admission from
legacy declarations.

## Trusted adapter contract

The host must route every provider and model port through
`discovery_session.execute_operation`, including calls nested inside a phase.
The controller saves the original attempt and unique operation identity before
invoking the port. Provider calls consume the shared request cap; the first
search uses its existing prepaid reservation. Model operations reserve a bounded
positive token allowance against both spent tokens and all in-flight allowances.
The adapter's `begin` applies that cap to the native invocation. Its `finish`
reads actual usage independently of semantic output, including on exceptions.
Nested model scopes must be measured exclusively to avoid double accounting.
Provider ports must perform no hidden model work. Retries and pagination are
separate operations, each admitted before dispatch.

The broker settles usage before returning to semantic interpretation, also after
stop, expiry or a changed receiving worktree. Unknown usage stops the original
session. A native per-operation overrun is retained and fails budget compliance.
There is no automatic retry or switch to supervision. A crash leaves its original
operation pending: reconcile accounting, do not repeat the effect.
Only operations in the current live call stack can own nested dispatch. A pending
operation left on disk cannot admit replacement effects after a host restart.

Phase handlers in `drive` are trusted orchestration code and use these wrapped
ports; they are not a Python sandbox. Arbitrary callbacks, direct native SDK
calls and out-of-process model use cannot be certified by this interface. An
adapter review must establish that all such ports are covered before any native
qualification claim. The deterministic CLI installs no model SDK and starts no
model call itself.

## Recording and recovery

`record` accepts zero additional model tokens only. Actual model usage is settled
separately; a caller-supplied semantic token count cannot substitute for the meter.
Every admitted provider/model operation must have the broker's terminal event
before its action can advance. Calling `authorize_request` manually, or posting a
usage JSON file, cannot manufacture that terminal event. Provider request counts
must still match the original permits. An exact completed result retry is inert.

`discover usage` remains an accounting-only recovery ingress for original known
attempts, including legacy, stale and stopped sessions. It grants no operation or
semantic completion. An unknown receipt can later be reconciled to actual tokens
under the same operation/attempt/author/tool identity. Both events remain in the
append-only chain; known tokens are added once. A changed known cost is refused.
Reconciliation never renews the lease, clears a stop or starts a replacement.

Supervised fixed-pair work does not use this strict controller. It starts from
the supplied pair without repository search, retains finite source/provider/time
bounds and reports unavailable inference usage as unknown. See
[`skills/eat/SKILL.md`](../skills/eat/SKILL.md) for the shared receiving-evidence,
licensing and publication protocol.
