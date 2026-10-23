# Hungry Crab Feeder → GitHub Marketplace

**Status: proposal / documentation only (2026-10-22).** No Marketplace listing, dogfood result,
external installation, release, or user-facing proof is created by this document. Work starts
with [programme #304](https://github.com/drevendev/HungryCrab/issues/304).

> **Find what your repository is missing.** Give the crab a reference repository (the **prey**)
> and your own repository (the **maw**); get a deterministic, source-linked, license-aware
> menu of *possible* improvements, packaged as an Actions artifact.

## What we are launching

**Hungry Crab Feeder** is an existing **composite GitHub Action** at the root
[`action.yml`](../../action.yml). It runs a model-free, read-first pipeline and exports
`feeder.json`, `menu.md`, `menu.json`, `gap.md`, `meal.json`, and two digest
manifests. It does not change the maw, create issues/PRs, run code from the prey, or make
an agent autonomous. Humans review a card's source, license/policy trace, and applicability
before acting through a **separate**, explicitly authorized process.

This is **an additional discovery and distribution surface**, not a new product/repository,
not a listing for every Hungry Crab command, and not the same as the Claude Code plugin
Marketplace. Start with the existing repository to reuse versioning, docs and tests; assess
a dedicated Action repository only if publisher requirements, runtime packaging or user
experience warrant the extra overhead.

The full CLI/MCP/skills are described in [Everywhere](../everywhere.md); the
[Feeder guide](../feeder.md) is the current technical operator contract.

### Verified repository state (2026-10-23)

- Public `drevendev/HungryCrab` has one root `action.yml` with `name`, `description`,
  `branding.icon: search` and `branding.color: orange`.
- GitHub Releases for [v0.6.0](https://github.com/drevendev/HungryCrab/releases/tag/v0.6.0),
  [v0.6.1](https://github.com/drevendev/HungryCrab/releases/tag/v0.6.1) and
  [v0.6.2](https://github.com/drevendev/HungryCrab/releases/tag/v0.6.2) exist. The latest
  published release is `v0.6.2`, at `d7779ebdc7ad14998567bbd0fa17fc3c558cc49b`.
  Release existence alone does not establish a Marketplace listing or external consumption.
- The nested-checkout/submodule privacy correction in
  [#399](https://github.com/drevendev/HungryCrab/pull/399) is merged on `master` at
  `aa1b3bf454349358e52bd7ac9f5bc0b797f0c7ae`; it is newer than `v0.6.2`.
  Select a reviewed Action ref containing that correction for M1, then verify its CI and
  remaining release gates. No existing release is promoted to launch-ready by this plan.
- The Action uses third-party version tags that need immutable pinning; see
  [#301](https://github.com/drevendev/HungryCrab/issues/301).
- [#211](https://github.com/drevendev/HungryCrab/issues/211) (cache ownership) and
  [#236](https://github.com/drevendev/HungryCrab/issues/236) (hard download/disk budgets)
  remain known safety/scale debt, not silent launch successes.
- Existing Feeder smoke uses `uses: ./`; a real external `uses: drevendev/HungryCrab@<sha>`
  run in another maw is a different, required acceptance test.
- **Mandatory M1 exit: F02 external-maw consumption in `drevendev/BeautifulRepo`**
  using `drevendev/HungryCrab@<reviewed-full-SHA>` must execute successfully on **both
  Linux and Windows**, with exact workflow/job links and inspected artifact plus source trace.
  F01 self-consumption (`uses: ./` or an owner/repo ref inside HungryCrab itself) does
  **not** qualify as external interoperability proof or satisfy M1 by itself.

## Staged roadmap (no invented dates)

| Phase | User-visible deliverable | Required evidence and stop gate | Issue |
| --- | --- | --- | --- |
| **M0 — Name the meal** | Name, plain-English pitch, honest description, polished quickstart and shot list | Copy reviewed; example marked illustrative until actually executed | [#305](https://github.com/drevendev/HungryCrab/issues/305) |
| **M1 — Trust the hook** | Metadata/name validity, immutable pins, precise release ref, scoped permissions and runner installation | Exact-ref full Linux/Windows CI, read-only artifact checks, negative permission cases; stop on any leak or unsafe deletion | [#306](https://github.com/drevendev/HungryCrab/issues/306), [#307](https://github.com/drevendev/HungryCrab/issues/307), [#309](https://github.com/drevendev/HungryCrab/issues/309) |
| **M2 — Eat our own cooking** | Real Action use in owned public maws with public prey; honest useful and NO_GAP outcomes | Actual run URLs, pinned maws/prey/action, artifact readback, reviewer decision; no imported user data | [#308](https://github.com/drevendev/HungryCrab/issues/308) |
| **M3 — Show the meal** | Human-reviewed evidence gallery and published Marketplace listing | Evidence survives independent reproduction, owner-accepted Developer Agreement and GitHub Release, valid listing and copied installation example | [#310](https://github.com/drevendev/HungryCrab/issues/310), [#311](https://github.com/drevendev/HungryCrab/issues/311) |
| **M4 — First Feast** | Short actual-run demo, DEV article, community invitation and feedback-driven improvements | Record time to first menu/useful suggestion, false positives, a negative control and genuine adoption only | [#312](https://github.com/drevendev/HungryCrab/issues/312), [#281](https://github.com/drevendev/HungryCrab/issues/281) |

M0 documentation may be reviewed/merged independently. **Do not launch from the
documentation PR.** The owner decides the release/tag and Marketplace publication steps,
including any account agreement or 2FA action.

## Documents

- [Positioning and ready-to-edit copy](positioning.md): naming options, elevator/short/long
  pitches, metadata candidate, category/search intent, voice and visual storyboard.
- [Dogfood experiment matrix](dogfood.md): verified public/private fleet boundaries,
  cross-repository Action acceptance plan, workflow illustration, negative tests and scores.
- [Evidence and proof contract](evidence.md): record template, approval/redaction rules,
  positive and negative case-study outline, video/GIF plan and intentionally empty baseline.
- [Publication and maintenance checklist](publication.md): exact version gates, human UI
  actions, release/Marketplace identity, rollback and post-listing checks.

## Non-negotiable boundaries

1. **Public demos use only public maw + independently verified public prey.** An artifact
   contains repository details, issue titles/excerpts, commit subjects and source evidence;
   assume artifacts from a public repository can be downloaded by other GitHub users.
   Review the local maw bytes too: public origins do not prove that local files are public.
   Retain the default refusal of separate nested checkouts and populated gitlinks, including
   public-origin ones; review ordinary non-Git output separately before a public trial.
2. Never use `allow-private-artifact: true` as an easy pass. A private maw is a separate
   approved trial whose *raw* evidence must stay private. A public Action may be used from
   a private workflow, but Marketplace does not make private content public-safe.
3. Use `permissions: contents: read` and a reviewed immutable Action SHA for actual
   consumer workflows. Do not grant `issues: write`, `pull-requests: write`, or
   `contents: write` just to make this Action work.
4. No prey installation/build/test, execution of untrusted repository code, autonomous
   target edits, guaranteed useful cards, fictitious approval or false **Verified creator** badge.
5. `max-repo-kb` is a size **preflight**, not a hard download/wiki/disk cap. A green smoke
   does not close [#236](https://github.com/drevendev/HungryCrab/issues/236).
6. Each example must say **illustrative / executed / reviewed / adopted**. An output card
   becomes an improvement only after a separate human-approved target change is accepted.

GitHub's own publishing requirements and UI path:
[Publish in GitHub Marketplace](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace).
This Marketplace programme is subordinate to the existing release and safety rules; do not
interpret it as authority to merge an unrelated PR or change repository settings.
