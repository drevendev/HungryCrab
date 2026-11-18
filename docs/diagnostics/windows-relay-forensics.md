# Windows relay evidence (T6 / #679)

The historical managed-local `0xC0000008` observation remains unattributed. Its exact
source/dirty tree, Python build, raw output and process exit have not been established.
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
recorded. A clean bounded series means **NOT_REPRODUCED**, never historical-root-cause closure.

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
