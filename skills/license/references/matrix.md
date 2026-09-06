# Maw x prey license matrix

Rows are the prey license class, columns the maw license class. The engine picks the more
restrictive mode when in doubt.

| Prey \ Maw | Permissive (MIT, BSD, Apache, MPL maw) | GPL family maw | Proprietary or unlicensed maw |
|---|---|---|---|
| MIT, BSD, ISC, 0BSD, Zlib, Unlicense, CC0, Boost, PostgreSQL, PSF | `COPY` | `COPY` | `COPY` |
| Apache-2.0 | `COPY` + NOTICE | `COPY` + NOTICE for GPL-3.0, AGPL-3.0 and GPL-2.0-or-later; `IDEAS_ONLY` for GPL-2.0-only | `COPY` + NOTICE |
| MPL-2.0, EPL, CDDL | `COPY_FILE` | `COPY_FILE` | `COPY_FILE` |
| LGPL | `REIMPLEMENT` (linking is a separate question) | `COPY` | `REIMPLEMENT` |
| GPL-2.0 / GPL-3.0 | `REIMPLEMENT` (clean room) | `COPY` when the versions are compatible, else `IDEAS_ONLY` | `IDEAS_ONLY` |
| AGPL-3.0 | `REIMPLEMENT` | `COPY` only into an AGPL-3.0 maw, else `IDEAS_ONLY` | `IDEAS_ONLY` |
| BUSL, SSPL, Elastic, Commons Clause, proprietary | `IDEAS_ONLY` | `IDEAS_ONLY` | `IDEAS_ONLY` |
| No license found | `IDEAS_ONLY`, flagged for review | `IDEAS_ONLY`, flagged for review | `IDEAS_ONLY`, flagged for review |
| License read and not classified | `HUMAN` | `HUMAN` | `HUMAN` |
| CC-BY (documentation) | `COPY` + attribution | `COPY` + attribution | `COPY` + attribution |
| CC-BY-SA, GFDL | `COPY_FILE` (share-alike flag) | `COPY_FILE` | `COPY_FILE` |
| CC-BY-NC, CC-BY-ND | `IDEAS_ONLY` | `IDEAS_ONLY` | `IDEAS_ONLY` |

## When the table does not apply

Two relationships short-circuit the whole matrix, and `license.json` records which one was used.

| Relationship | When | Mode |
|---|---|---|
| `own` | the maw's `origin` owner also owns the prey, or the owner is listed in `trust.owners` in the maw's `.crab.yml` | `COPY` — a license governs strangers, and the owner is not one. Still flagged for review under a copyleft or source-available prey: owning a repository lets its owner relicense what they wrote, not what they received from someone else |
| `bypass` | `trust.bypass_license` is on in the maw's `.crab.yml` | `COPY`, always flagged. This is not a finding about the license; it is a decision to stop asking, and every card says so |

Neither is a licence verdict. If a card carries one, say which, because the reader will assume
the matrix was consulted and it was not.

## GPL version compatibility (prey into a GPL maw)

| Prey | GPL-2.0-only maw | GPL-2.0-or-later maw | GPL-3.0 maw | AGPL-3.0 maw |
|---|---|---|---|---|
| GPL-2.0-only | `COPY` | `COPY` | `IDEAS_ONLY` | `IDEAS_ONLY` |
| GPL-2.0-or-later | `COPY` | `COPY` | `COPY` | `COPY` |
| GPL-3.0 | `IDEAS_ONLY` | `COPY` | `COPY` | `COPY` |
| AGPL-3.0 | `IDEAS_ONLY` | `IDEAS_ONLY` | `IDEAS_ONLY` | `COPY` |

The code lives in `src/hungry_crab/licensing/matrix.py`; the tests in `tests/test_licensing.py`
are the executable version of this table.
