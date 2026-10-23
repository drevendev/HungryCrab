# Proof before praise — the Marketplace evidence contract

**Status: no completed public pilot recorded for this programme.**
This file is a *template*, not an evidence gallery. The user explicitly requested
proof in HungryCrab and the Marketplace only **after real tests**. The future
evidence gate is [#310](https://github.com/drevendev/HungryCrab/issues/310);
the wider adoption campaign is [#281](https://github.com/drevendev/HungryCrab/issues/281).

## Proof hierarchy

| Level | What happened | Acceptable public wording |
| --- | --- | --- |
| **P0 — PLANNED** | Scenario, workflow or copy exists only on paper | "Planned", "illustrative", "not yet tested" |
| **P1 — EXECUTED** | Exact workflow run finished; artifact or failure read back | "Executed on [OS] at [SHA]"; report actual outcome |
| **P2 — REVIEWED** | Human checked at least one specific card, source and license trace | "Reviewed candidate"; mark useful/rejected/NO_GAP |
| **P3 — ADOPTED** | Separate target-repo PR was human-reviewed and actually merged | "Accepted improvement" with target PR and merge SHA |
| **P4 — MARKETPLACE INSTALLED** | An external maw copied/used the actual published Marketplace Action release and ran it | "Installed via GitHub Marketplace" with listing URL + run |

A pass on self-test `uses: ./` is *not* P4. A suggested card is *not* P3.
A positive P1 cannot be assumed to be P2, and a `NO_GAP` is honest P2 evidence
when a reviewer really checked the result.

## One reproducible trial record

For every future executed public test, create **one dated, descriptive Markdown
record** under `docs/marketplace/evidence/` in a *separate* post-run PR.
Use no invented values or copy from an unrelated run.

```markdown
# <Test name> — YYYY-MM-DD
Status: EXECUTED / REVIEWED / ADOPTED / MARKETPLACE_INSTALLED / NO_GAP
Audience/scope: public-maw + public-prey / private redacted summary
Purpose and hypothesis:
Maw: <public URL>@<40-hex commit SHA>; license/policy:
Prey: <public URL>@<40-hex commit SHA>; license:
Consumed Action: drevendev/HungryCrab@<40-hex commit SHA>; listing/ref:
Runner: <OS + version>; trigger: workflow_dispatch
GitHub Actions workflow file + commit:
Run/job URL + conclusion:
Artifact ID/name/URL + sha256 or immutable identity:
Evidence manifest SHA, visibility/coverage and producer health:
Side effects: issues_created=0; ledger_written=false; maw git status:
Top 3 candidate IDs + license mode + exact source URL/line/commit:
Negative/NO_GAP/rejected case ID and reviewer reason:
Measured duration: wall time / time-to-first-menu (with source):
Human reviewer + date: 
Decision: useful / not applicable / needs research / no gap
Accepted target PR + merged commit SHA (ONLY IF ACTUALLY MERGED):
Observed limitations, false positives and blockers:
Allowed public screenshots/clip filename, redaction reviewer:
Independent reproduction/review link:
```

Record actual errors too: failed CI, missing/invalid token, source visibility,
license ambiguity, unavailable wiki, no useful cards, or hard resource cap.
Do not delete the failure record merely to improve the page's tone.

## What a strong public case study shows

The **first positive-or-useful example** should tell a specific story, not
count feature flags. Proposed outline, filled *after* execution:
1. **Question:** what engineering practice is missing from this named public maw?
2. **Setup:** two pinned public repositories, versioned Action ref, explicit
   permissions and input policy.
3. **Run:** actual GitHub Actions job conclusion + artifact; screenshot with IDs
   and accessible transcription.
4. **Evidence:** one suggested card, source link, commit, license and why it fits
   this maw; compare with the actual current maw.
5. **Human decision:** useful / rejected / needs more context; if merged later,
   a separate target PR and merge SHA.
6. **What it missed:** a negative case or one false positive, plus what was
   learned and any follow-up issue.
7. **Try it yourself:** install snippet from verified release, not a floating
   branch or fake Marketplace URL.

The **negative/control example** should be just as visible:
- A correctly refused public/private artifact attempt; **or**
- A tiny/cross-domain maw where no applicable nutrient is found.
- Show the exact guard/outcome and why refusing or returning zero was correct.
- Never reveal private repository contents or identifiers to demonstrate refusal.

## Screenshot / video approval

The same source material can support the repo README, Marketplace-associated
README, a DEV post and a short social demo **only if the platform supports the
display surface**. Marketplace's automatic Action listing is not guaranteed
to offer a custom cover-image slot. A GitHub Action icon must remain a
supported Feather `branding.icon` and valid color.

**Capture sequence after execution:**
- Frame A: workflow at a reviewed SHA with `contents: read`.
- Frame B: green exact-head job with visible runner/context.
- Frame C: downloadable named Action artifact; no hidden tokens/path.
- Frame D: menu card with real source permalink/commit.
- Frame E: license mode and human decision, including rejected/NO_GAP note.
- Frame F: optional separate **merged** target PR if that really happened.
- Frame G: Marketplace listing/install snippet (only after verified listing).

**15-second script:** input (3s) → verified run (4s) → menu (5s) →
"No auto-PR. You decide." (3s). **35–45-second script:** add pinned
prey/maw identity, artifact readback, reviewer reaction, one failure/limitation.
Embed captions, accessible alt text, concrete screenshot provenance and a
link to the original run. Avoid stock terminal footage portrayed as a real
run. A looping GIF may omit sensitive material but must not falsify output.

**Redaction:** a private maw/prey name, private branch/commit, secrets, issue
contents, internal paths or unapproved user information cannot enter a
public evidence file, video frame, screenshot alt text, PR or Markdown link.
Publish only independently approved public-safe aggregate after owner review.
Keep original raw evidence in the producing run's appropriately private context.

## Evidence review gates

- All published example fields resolve to actual objects at verified SHAs.
- The artifact was opened and its sample checked, not merely attached.
- A second reviewer or independent later-wake check verifies the significant
  candidate and its license/source trace.
- Disclose model-free read-only constraints, `max-repo-kb` being a preflight,
  and any open safety bugs; do not claim privacy certification from listing.
- Metrics use observed denominators; no invented active users, star counts,
  downloads, badges, testimonials, benchmark lift or 3 merged fleet changes.
- A README/Marketplace proof update is a **new PR after tests**; this planning
  PR intentionally has no images, stats, case study or runnable proof badge.

### Planned first proof slots (EMPTY)

| Slot | Required target | Current evidence |
| --- | --- | --- |
| F01 public self-feeding example | HungryCrab maw, BeautifulRepo public prey | **NOT RUN** |
| F02 public external-maw example | BeautifulRepo maw, HungryCrab public prey | **NOT RUN** |
| F05 negative / no-gap | Quillgrain_Common public maw | **NOT RUN** |
| Refusal / private guard | No secrets or private inputs in public docs | **NOT RUN** |
| Marketplace-installed example | External `uses:` copied from published listing | **NOT PUBLISHED / NOT RUN** |

Replace each placeholder only after GitHub readback, human review, and a
separate evidence PR, not on anticipated success.
