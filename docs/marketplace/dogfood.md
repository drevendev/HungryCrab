# Dogfood plan — make the Marketplace Action prove itself

**This is a test specification, not a test report.** No workflow, target-repo changes or
live public/private repository analysis is initiated by this documentation.
Tracking: [#307](https://github.com/drevendev/HungryCrab/issues/307),
[#308](https://github.com/drevendev/HungryCrab/issues/308),
[#309](https://github.com/drevendev/HungryCrab/issues/309).

The product is a two-repository comparison: **prey** is a reference, **maw** is
the owner's current project. Do not confuse a successful Feeder run with a
useful recommendation or an accepted pull request.

## Public test fleet

The following repository identities and public visibility were verified via GitHub
at planning time (2026-10-22). Re-check access, license, HEAD, and safe acquisition
size at the moment of execution; no pairing guarantees an improvement.

| Scenario | Maw (Action runs here) | Prey (read only) | Learning question | Expected review |
| --- | --- | --- | --- | --- |
| F01 — self-feeding | [HungryCrab](https://github.com/drevendev/HungryCrab) | [BeautifulRepo](https://github.com/drevendev/BeautifulRepo) | Does our own tooling see actionable repo hygiene? | Check CI/docs candidates and a real NO_GAP possibility |
| F02 — reverse perspective | [BeautifulRepo](https://github.com/drevendev/BeautifulRepo) | [HungryCrab](https://github.com/drevendev/HungryCrab) | Can a mature CLI repo offer maintainability lessons? | Reject unrelated language-specific advice |
| F03 — cross-domain | [Devostasis](https://github.com/drevendev/Devostasis) | [HungryCrab](https://github.com/drevendev/HungryCrab) | Are test/release/contributor practices transferable? | Examine licenses and avoid pretending syntax is portable |
| F04 — small product | [PatchPitch](https://github.com/drevendev/PatchPitch) | [BeautifulRepo](https://github.com/drevendev/BeautifulRepo) | Are repo onboarding and docs gaps meaningful? | Ask a human whether proposed changes solve real friction |
| F05 — negative/minimal | [Quillgrain_Common](https://github.com/drevendev/Quillgrain_Common) | [BeautifulRepo](https://github.com/drevendev/BeautifulRepo) | Does a tiny/different repo trigger spurious certainty? | Keep an honest NO_GAP / NOT_APPLICABLE result |

Start F01, then F02 and F03; F04/F05 are additional breadth and negative controls.

**F02 is a mandatory M1 exit gate, not optional follow-up dogfood.** Execute the
reviewed, full-SHA-pinned HungryCrab Action from an actual workflow in
`drevendev/BeautifulRepo` on **both Linux and Windows**. Retain exact run/job links,
the Action SHA, and inspected artifact/trace evidence for each platform. F01 within
HungryCrab, including an `owner/repo@sha` reference to itself, does not establish
cross-repository consumer compatibility. Do not promote F02 to EXECUTED or M1 PASS
without these independent readbacks.
Do not create trivial target PRs to force positive examples. This complements,
rather than replaces, [First Feast #281](https://github.com/drevendev/HungryCrab/issues/281).

**Private fleet:** optional internal-only read-only trials may use two owner-selected
private maws **after explicit owner approval**. Their identities, branches, artifacts
and raw results must remain private; only independently redacted aggregate findings can
be used publicly. Never give a public maw a private prey. The publication-safety
negative tests remain [#309](https://github.com/drevendev/HungryCrab/issues/309).

## Stages of verification

### T0 — exercise the actual package, before Marketplace publication

1. Pin the exact Action commit from an independently reviewed release candidate.
   The selected ref must include the nested-checkout/submodule privacy correction
   merged in [#399](https://github.com/drevendev/HungryCrab/pull/399), which is newer than
   the currently published `v0.6.2`. Verify third-party pins and every other M1 gate
   independently; choosing a recent commit alone does not establish launch readiness.
2. Start a manual `workflow_dispatch` in the public **maw**, using
   `uses: drevendev/HungryCrab@<approved-full-commit-sha>` from an external
   repository. The existing self-test `uses: ./` is useful but not equivalent.
3. Run Linux and Windows. Start with `issues: '0'` and `wiki: 'false'` to keep
   the first acceptance bounded; exercise extended inputs only once minimal runs
   are understood. Use read-only job permissions and no stored checkout credentials.
4. Verify uploaded bundle contents `feeder.json`, `menu.json`, `menu.md`,
   `meal.json`, `prey-digest/manifest.json`, and `maw-digest/manifest.json`,
   including source SHA/license/policy trace, coverage, and success/failure status.
5. Confirm `feeder.json.side_effects.issues_created == 0`,
   `feeder.json.side_effects.ledger_written == false`, no tracked maw
   modifications, no foreign-code execution, and no private material in the artifact.
6. Inspect 3 candidate cards plus at least one excluded/negative/no-gap case.
   Record both useful **and** rejected recommendations with a human rationale.
7. Repeat exact inputs on the same released SHA and compare relevant deterministic
   fields. Dynamic time/URLs and source changes must be identified, not described
   as unexplained nondeterminism.

### Illustrative F02 external-consumer workflow (NOT run or accepted)

This example belongs in a *future* `drevendev/BeautifulRepo` test PR after branch owner
approval. Replace the Action SHA placeholder with the reviewed full commit from T0;
it is intentionally not runnable with a historical, unpatched release. Also pin third-party
actions by reviewed full SHAs before the trial. Both platforms are required, and distinct
artifact names preserve their separate bundles. This is an example, not executed evidence:

```yaml
name: Hungry Crab public-pair experiment
on:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  meal:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7 # illustrative; pin reviewed SHA in an actual test PR
        with:
          fetch-depth: 0
          persist-credentials: false
      - name: Inspect a public reference repository
        id: feeder
        uses: drevendev/HungryCrab@<reviewed-full-commit-sha>
        with:
          prey: drevendev/HungryCrab
          issues: '0'
          wiki: 'false'
          artifact-name: hungrycrab-public-f02-${{ matrix.os }}
          allow-private-artifact: 'false'
```

For the public acceptance matrix, test both the direct composite Action and the
separately documented reusable workflow; do not assume their inputs, checkout
semantics, or output names are interchangeable without observing them.

### T1 — Marketplace consumption after publication

1. Use the **actual Marketplace listing's** displayed installation snippet in a
   new or existing owner-approved public maw workflow (not `./`).
2. Verify the exact release reference resolves to the expected commit before run.
3. Repeat a previously pinned public scenario on Linux and Windows if feasible;
   link the Market listing, Actions run, artifact and final code SHA in the record.
4. Inspect the public page: title, short description, categories, README,
   install code, version and contribution/support links render correctly.
5. Only after this test passes may a public proof say **"installed from GitHub
   Marketplace"**. T0 alone cannot establish that claim.

## Negative controls and reliability

| Control | Required outcome | Other owner |
| --- | --- | --- |
| Public maw + private or unverifiable prey | Fail closed before artifact | #309 |
| Public maw + local/uncertain source visibility | Fail closed, no source leak | #309, #301 |
| Public-origin nested checkout or populated gitlink with local-only files | Refuse before digest with `allow-private-artifact: false`; include modified, ignored and unpublished content | #309, #398, #405 |
| Missing/invalid read token, 403/rate limit | Actionable failure, no token in logs | #309, #301 |
| Missing/invalid `prey`; unsupported GitHub ref | No apparent success artifact | #307 |
| Deliberate no-useful-gap prey | Reviewer may accept zero value, not fake a card | #308 |
| Failed miner, unexpected coverage loss | No mislabeled complete/successful meal | #307 |
| Overlarge repo or wiki / hard-quota gap | Do not imply `max-repo-kb` guarantees disk/network cap | #236 |
| Cache collision, foreign/uncommitted user work | Reject rather than destroy data | #211 |
| Foreign instructions/code in prey | Treat as untrusted evidence; never execute | #309 |

**Safety stop:** no further publication or publicity if a private artifact leaks, a
repo is changed without authorization, a foreign checkout is deleted, security
checks fail, or the release SHA differs from the tested SHA.

## Human results rubric

A successful run means only **the pipeline finished**. Evaluate each run on
reproducibility, source/trace quality, time-to-first-menu (actual duration),
quality of the top 3 cards, false-positive rate, and whether any recommendation
is meaningful enough for a separate human-approved follow-up. Record the
reviewer's explicit `USEFUL`, `NOT_APPLICABLE`, `NEEDS_RESEARCH`, or
`NO_GAP` choice. An accepted **change** requires a separate reviewed target
PR and merge SHA; an uploaded card is not equivalent.

See the [evidence template](evidence.md). At documentation time there are
**zero newly executed dogfood trials under this Marketplace programme**.
