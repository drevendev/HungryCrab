# Windows relay evidence (T6 / #679)

The historical managed-local `0xC0000008` observation remains unattributed. Recovered
historical records establish Python 3.11.16/pytest 9.2.0, the acquisition target, a
nearby Git status with HEAD9942f2f and five tracked dirty paths, and two aggregated
exception outputs. The outer PowerShell invocation exited one; the direct Python
status, full build and original byte-exact separated streams were not captured.
Its complete dirty tree/environment have not been reconstructed.
Hosted passing tests do not prove that observation fixed. No speculative runtime fix is
introduced by this diagnostic.

`tests/test_acquisition_windows_forensics.py` runs the original quota-test node in at most
eight isolated Python children on Windows. Each attempt has a 45-second deadline and a
two-second post-kill pipe-drain deadline. The first anomaly ends the series; it is never
retried into a pass. An escaped descendant holding a pipe open is recorded as incomplete
capture, not claimed terminated.

The test retains stdout and stderr as raw `.bin` files even after exit zero. It separately
records nonzero exits, faulthandler diagnostic markers, missing exact one-test summaries,
timeouts and unavailable pipe EOF. A diagnostic message with exit zero is an anomaly for
investigation, not evidence of process termination. The synthetic regression controls emit
messages and exit normally or with code seven; they do not trigger native exceptions.

`manifest.json` and per-attempt JSON record the checkout SHA, tracked changes, relevant source
hashes, Python build/executable, platform, allowlisted Actions/run/image identity, command,
PID, deadlines, return code and its unsigned 32-bit projection, stream completeness, and raw
stream lengths/hashes. The source role explicitly says this is the current checkout, not
the established original failing snapshot. No tokens or unrestricted environment dump are
recorded. Schema two separates subject and harness revisions/hashes and records the
actual pytest/plugin versions, lock hash, interpreter binary hash and compiler. A clean
bounded series means **NOT_REPRODUCED**, never historical-root-cause closure.

## Historical-runtime comparison

The recovered exception records used Python **3.11.16**, whereas the first #693 ordinary
Windows3.11 jobs actually used system **3.11.9**. These are different runtimes. The
`Windows relay attribution` workflow explicitly requires managed3.11.16/3.14.10,
using uv0.12.12 and [only-managed discovery](https://docs.astral.sh/uv/concepts/python-versions/#requiring-or-disabling-managed-python-versions).
Missing distributions fail visibly; no older system interpreter is substituted.

The four bounded legs compare the current checkout with public reference
9e7341986217fdca02a101d225ba4354bfe6480c. Its acquisition and target-test Git blobs
match recovered local HEAD9942f2f4715d2e58180eaac608390421930b28c2 exactly:
`781c15f6fcae747669d78dcd6d59b06626de0cb8` and
`5dea750435fabc5f8f992159624c6524eb844b98`, respectively. Its conftest and project
configuration also match, but its lock differs. The historical record's five tracked
dirty paths do not include these files. This establishes a useful acquisition reference,
not an exact original failing checkout. No unpublished experimental history is needed.

Each leg uses `--capture=sys`, matching the recorded diagnostic invocation, with the
same exact single quota-test target and first-anomaly stop. The historical invocation
ran the entire fifteen-case file; this comparison does not recreate that preceding
state, the managed-local sandbox or an identical interpreter binary/build. Retain
those limits even when all four jobs pass. The workflow runs on its explicit diagnostic
branch during development, relevant PR changes, or manual dispatch after integration.
The original PR693 full checks are left running while the diagnostic branch is tested.

CI uploads `relay-forensics-*` artifacts on both success and failure for focused lifecycle
jobs and the full Windows suites. The artifact directory must be fresh: reuse fails rather
than overwriting older evidence. An interrupted parent may leave `CAPTURE_IN_PROGRESS`;
that is incomplete evidence, not a passing series. Artifact upload itself can still be
prevented by runner loss.

To finish T6, recover the original raw stderr/exit and exact source/environment if available,
or obtain an attributable new reproduction and a bounded explanation/fix. Compare actual
process status with diagnostic text before assigning a cause. Keep #679 open while that
evidence is missing. All execution belongs in Actions; this diagnostic needs no provider,
donor checkout, model call, release, or local runtime probe.
