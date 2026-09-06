# Host x prey license matrix

Rows are the prey license class, columns the host license class. The engine picks the more
restrictive mode when in doubt.

| Prey \ Host | Permissive (MIT, BSD, Apache, MPL host) | GPL family host | Proprietary or unlicensed host |
|---|---|---|---|
| MIT, BSD, ISC, 0BSD, Zlib, Unlicense, CC0, Boost, PostgreSQL, PSF | `COPY` | `COPY` | `COPY` |
| Apache-2.0 | `COPY` + NOTICE | `COPY` + NOTICE for GPL-3.0, AGPL-3.0 and GPL-2.0-or-later; `IDEAS_ONLY` for GPL-2.0-only | `COPY` + NOTICE |
| MPL-2.0, EPL, CDDL | `COPY_FILE` | `COPY_FILE` | `COPY_FILE` |
| LGPL | `REIMPLEMENT` (linking is a separate question) | `COPY` | `REIMPLEMENT` |
| GPL-2.0 / GPL-3.0 | `REIMPLEMENT` (clean room) | `COPY` when the versions are compatible, else `IDEAS_ONLY` | `IDEAS_ONLY` |
| AGPL-3.0 | `REIMPLEMENT` | `COPY` only into an AGPL-3.0 host, else `IDEAS_ONLY` | `IDEAS_ONLY` |
| BUSL, SSPL, Elastic, Commons Clause, proprietary | `IDEAS_ONLY` | `IDEAS_ONLY` | `IDEAS_ONLY` |
| No license or unrecognised | `IDEAS_ONLY` + `HUMAN` | `IDEAS_ONLY` + `HUMAN` | `IDEAS_ONLY` + `HUMAN` |
| CC-BY (documentation) | `COPY` + attribution | `COPY` + attribution | `COPY` + attribution |
| CC-BY-SA, GFDL | `COPY_FILE` (share-alike flag) | `COPY_FILE` | `COPY_FILE` |
| CC-BY-NC, CC-BY-ND | `IDEAS_ONLY` | `IDEAS_ONLY` | `IDEAS_ONLY` |

## GPL version compatibility (prey into a GPL host)

| Prey | GPL-2.0-only host | GPL-2.0-or-later host | GPL-3.0 host | AGPL-3.0 host |
|---|---|---|---|---|
| GPL-2.0-only | `COPY` | `COPY` | `IDEAS_ONLY` | `IDEAS_ONLY` |
| GPL-2.0-or-later | `COPY` | `COPY` | `COPY` | `COPY` |
| GPL-3.0 | `IDEAS_ONLY` | `COPY` | `COPY` | `COPY` |
| AGPL-3.0 | `IDEAS_ONLY` | `IDEAS_ONLY` | `IDEAS_ONLY` | `COPY` |

The code lives in `src/hungry_crab/licensing/matrix.py`; the tests in `tests/test_licensing.py`
are the executable version of this table.
