# Positioning and Marketplace copy — Hungry Crab Feeder

**Draft copy for approval; not a live listing.** Applies to the *Action*, not the entire CLI/MCP
or Scheduled Crab. Work tracker: [#305](https://github.com/drevendev/HungryCrab/issues/305).

## Market problem and answer

**The problem:** the best engineering idea for a project may already exist in another
repository, but collecting comparable evidence from READMEs, tests, CI, licensing, commits
and issues is tedious. Blindly copying code is not safe or useful.

**The answer:** point Hungry Crab at the reference **prey** and your own **maw**.
The deterministic Feeder compares the two and uploads an inspectable
**menu** of candidate improvements, each with evidence and license-aware context.
A person chooses what is actually worth doing. It is not a linter, a PR reviewer,
an AI autopilot, or a guarantee of useful gaps.

**Promise to put first:** *evidence-backed ideas, without running the reference project*.

**Important qualifier:** "read-only" means the Action does not change source repos,
issues, PRs or the maw ledger. It **does** read/download reference data and writes a
temporary cache plus an Actions artifact. Do not describe it as fully offline.

## Names (decision, not four separate brands)

| Role | Candidate | Why / decision |
| --- | --- | --- |
| **Preferred actual Action name** | **Hungry Crab Feeder** | Keeps Hungry Crab recognizable and Feeder specific. Matches current root `action.yml`; subject to uniqueness validation in publisher UI. |
| Alternate Marketplace title | **Hungry Crab — Repo Gap Finder** | Benefit-forward search language; risks obscuring the established Feeder API. Use as landing-page heading rather than rename unless tested. |
| Alternate editorial title | **Hungry Crab: Repo Scout** | More approachable, but "scout" could imply a repo-discovery service rather than a comparison. Good article title, not the default Action name. |
| Output / demo concept | **Crab Meal** / **First Feast** | Names for downloaded reports, tutorial and adoption campaign. Not new standalone product names. |
| Reject | "AI Code Reviewer", "Auto-Fix Crab", "Secure Your Repo" | Unsupported promises: no model, no automatic fix, no security certification. |

**Category hypothesis:** `Code quality` primary, `Utilities` secondary if the actual
GitHub Marketplace form offers them. Avoid `Code review` (reviews existing PRs),
`Security` (scanner/auditor), and `AI Assisted` (model-free pipeline). Recheck
categories and name collisions at publish time.

**Search phrases to test in description/readme, not keyword-stuff:** repository comparison;
GitHub Action repo analysis; engineering practice discovery; license-aware improvement
ideas; deterministic code-quality menu; read-only repository audit. Do not claim superior
ranking or external install numbers without measured evidence.

## Ready-to-edit English copy

**Action `name` proposal:** `Hungry Crab Feeder` (currently present; check uniqueness)

**Short metadata `description` proposal:**

> Compare reference repositories with your project and download a deterministic, license-aware improvement menu.

**Short punchline / social hook:**

> Find what your repository is missing.

**Alternative, more playful punchline:**

> Your next good idea might be hiding in another repo. Let the crab bring back a menu.

**10-second pitch:**

> Hungry Crab Feeder compares another repo with yours and gives you a source-linked menu of ideas to consider. No AI model, no copied code, no automatic changes.

**30-second pitch:**

> You can learn a lot from how other projects test, document, ship, and maintain their code—but trawling dozens of repositories takes time. Hungry Crab Feeder runs inside GitHub Actions. Point it at a public reference repository and your own project; it digests both, compares their practices, and uploads a menu with source and license context. Open `menu.md`, inspect the evidence, and decide what to keep. No model is needed; nothing is automatically merged or published to your repository.

**Marketplace / README introduction (about 120 words):**

> **Borrow good engineering ideas, not blind copies.** Hungry Crab Feeder turns a reference
> repository into an evidence-linked menu for your own project. Run it from GitHub Actions,
> inspect the uploaded `menu.md` or `menu.json`, and explore candidate gaps in CI,
> tests, documentation, dependencies and repository maintenance. Each suggestion is
> contextualized with source references and the crab's license/policy trace. The pipeline is
> deterministic and model-free; it does not execute code from the reference repository or
> make pull requests for you. You stay in charge of what is useful, what is irrelevant,
> and what is safe to carry over. Start with two public repositories and a read-only
> workflow. A no-gap result is still a meaningful result.

**One-line listing benefit:** `From two Git repositories to one inspectable menu.`

**Button-equivalent CTA text:** `Try a read-only meal` linking to the
[quickstart](../feeder.md), not a nonexistent Marketplace/install/screenshot URL.

**Owner-facing single-sentence pitch:** `A GitHub Action that lets maintainers learn from
other repositories without spending a weekend reading them or trusting an AI black box.`

## What it is / what it is not

| It is | It is not |
| --- | --- |
| A model-free workflow producing inspectable evidence | A cloud SaaS requiring accounts or payments |
| A read-only comparison of prey and maw | A workflow that opens/merges target PRs |
| A source/license-aware shortlist | An automated legal opinion or security certification |
| A reproducible versioned Action | A hard-capped network/disk sandbox (tracked by #236) |
| A tool that may correctly return NO_GAP | A promise to find something valuable on every run |
| A useful input to a later human/agent decision | An unattended agent that edits source files |

**Disclosures:** the Action may use network access to read public repos; GitHub Actions
artifacts in public workflows can expose source-derived metadata. State required token
scope, supported runner environment, approximate/default acquisition limits and
`allow-private-artifact: false` near the quickstart. Avoid implying the license engine
permits indiscriminate copying.

## Visual identity: attractive but truthful

Keep the recognizable Hungry Crab identity. Use the actual Action branding
(`search` icon on orange) initially. For later **editorial** screenshots/cards:
dark ocean/navy background, orange crab accent, high-contrast monospace evidence chips,
two labeled columns `prey` → `maw`, one `menu.md` panel, readable source/commit/trace.
This is a design brief—not a claim that custom GitHub Marketplace hero artwork is supported
by the publisher UI. Check image placement, platform accessibility and rendering before
producing an asset; no images or badges are fabricated in this documentation PR.

**Hero screenshot order after actual trial:** user workflow → green real run → artifact →
first source-linked card → license mode → explicit human decision. Include a no-gap/rejected
frame to communicate honest limits.

## Demonstration scripts (film only after evidence exists)

**15 s micro-demo:**
1. Show a public test workflow with pinned Action SHA and `prey` input (0–3 s).
2. Trigger a real run; cut to the completed job and artifact, labeled with run ID (3–7 s).
3. Open `menu.md`; highlight one verified source/trace (7–12 s).
4. End on `You decide what to keep.` and a truthful link to the Action (12–15 s).

**35–45 s First Feast case:**
1. Hook: "Another repo solved this problem. Could we learn from it?" (0–5 s).
2. Identify public prey + public maw and their immutable commit SHAs (5–10 s).
3. Show copied `uses:` snippet, read-only permissions and actual runner (10–17 s).
4. Show completed successful run, artifact ID, candidate count **if measured** (17–24 s).
5. Inspect one proposed card and license/source trace (24–33 s).
6. Show reviewer acceptance/rejection, or honestly say "no useful gap" (33–39 s).
7. CTA and limitation: "A menu, not an automatic PR." (39–45 s).

Only real measurements may populate any numbers, visuals, benchmark comparisons, stars,
third-party quotes, badges or approval claims. Evidence template lives in
[evidence.md](evidence.md). Campaign sequencing is [#312](https://github.com/drevendev/HungryCrab/issues/312).
