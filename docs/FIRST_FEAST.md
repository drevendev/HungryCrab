# First Feast: from a working crab to useful improvements

Status: owner-requested programme proposal, 2026-10-19. Not a release announcement,
completed pilot, deployment instruction or change to existing acceptance gates.
Coordination: [#281](https://github.com/drevendev/HungryCrab/issues/281).

## The promise

**Find useful ideas in other repositories. Turn them into reviewed improvements.**

A digest is not the product outcome. A menu is not an accepted recommendation. The
outcome is a maw getting a useful improvement whose evidence, licence mode, trace and
human decision are inspectable. The crab must also explain when there is nothing worth
carrying over. Vocabulary remains owned by [the glossary](design/GLOSSARY.md).

The first audience is maintainers and agent-assisted developers with an existing
repository, a concrete improvement goal and limited review time. This is not a promise
of autonomous repository rewriting, legal clearance or universal model judgment.

## 1. Recover the prepared work before expanding the feature set

This is a dated recovery map, not a mutable live queue. Re-read exact PR heads and the
current default branch before acting on it.

- Documentation network/publication boundaries: #275 was published from the existing
  #273 branch and is merged in master at `424aee466f7db644db79e6258b4b48c4cd298263`.
- Release/reopen procedure: #260 is merged. The additional ancestry conformance check
  is tracked in #279 and published in #280. Its standalone local tests ran; whole-repo
  CI and actual tag execution must not be inferred from that result.
- Foundation integration: #235, #237, #240, #241 and #244 overlap with #246. Deep Bite
  #253 builds on that foundation; Taste Memory #259 extends the stack; #270 contains
  that stack plus an additional control-prey restriction. Select one integration path
  against current master, retaining already merged fixes. Do not merge every component
  independently and then merge the entire stack as though these were separate features.
- Old #175/#234 changes are represented by later candidates. Close superseded work only
  after the replacement is actually accepted and its relevant tests survive.
- #240 still needs exact-head resolution of its output-ownership/publication findings.
  Consistent manifest fields alone do not establish trustworthy ownership. A prepared
  set-equality patch must not be sold as a complete provenance fix.
- #241 cache refresh must retain both correct default-branch selection and requested
  history limits. Check the integrated implementation, not an older isolated patch.
- Do not reapply the old claim that an empty control prey list necessarily removes the
  maw allowlist: #270's inspected `_authorized_loop` uses
  `requested.prey or authoritative.prey`. That specific shortcut is not demonstrated
  by the current source. Other authorization findings require their own reproductions.

Code integration needs actual full tests, Ruff, mypy and the repository's Linux/Windows
matrix on the exact candidate. Green checks from an old fork head, author-reported
counts and a conflict-free merge calculation are different evidence. A missing check
is not a pass. A newly authored revision is not self-merged in the same execution.

## 2. Ship a coherent foundation, not a misleading version number

[#224](https://github.com/drevendev/HungryCrab/issues/224) still owns the 0.3 acceptance
criteria. Finish the approved foundation and its evidence first. First Feast is the
working name for that public-use milestone, not an instruction to change versions.
The 0.4/0.5 feature stack remains a separately qualified candidate until its own gates
pass. A larger version string does not cure an unvalidated integration.

Before a stable release, the acceptance review links to:

1. Exact integration revision and full required checks, with unresolved safety findings
   either fixed or explicitly keeping the affected capability out of the candidate.
   Excluding a capability does not silently waive #224's existing required criteria.
2. A clean installation proof from #274 and an executed first meal from #278, including
   Windows/Linux observations, command output, exit codes and any network prerequisites.
3. Reconciled versions and release notes, including #226's changelog audit. Preserve the
   documented release/reopen commit pair, exact rebased release SHA and green tag run.
4. Existing licence/corpus, ranking and human-uptake gates. The paid B2 work in #250 is
   NOT_RUN until the owner explicitly approves its cost and credentials. Do not replace
   it with synthetic PASS or silently drop it to meet a launch date.
5. Three genuine human-accepted nutrient improvements where required by #224. A trivial
   change made only to inflate this count does not demonstrate uptake.

The release owner decides the accepted scope and tag only after this review. No tag,
release, paid test, workflow rerun, new sender or protection change is part of this doc.

## 3. Run a small, read-first fleet pilot

Proposed maws: HungryCrab itself, BeautifulRepo and Devostasis. Selection and each
maw's authority must be confirmed by its owner before operational use. This document
creates no target-repository task, credential grant or scheduled job.

These maws test different value: dogfooding the crab's own tooling, repository
presentation/contributor experience, and a real project's tests/maintenance workflow.
The creative-writing/game-design experiment in #272 remains a separate pilot with its
existing target set; do not blur its evidence into this engineering trial.

For each selected maw:

- Pin the maw and crab revisions and define one concrete need before looking for prey.
- Select at most three small relevant public prey. Record current stars/forks, declared
  licence, GitHub API size and the actual `crab sniff` result before qualifying a meal.
  A community nomination is intake, not a completed hunt or reuse permission.
- Acquire and inspect prey only as data. Never run their setup, scripts, hooks or tests.
  Use an isolated cache/output area and explicit current maw constraints. Begin at
  read-only autonomy; keep GitHub publication disabled until separately authorized.
- Produce and independently review at most three candidate nutrients per maw. Preserve
  unsupported, duplicate, NO_GAP and incompatible-stack findings rather than dropping
  them from the denominator. Deterministic score is a pre-ranking, not the verdict.
- Only a genuinely useful accepted candidate proceeds to a small target-repo PR under
  that repository's rules. Verify its tests and human decision. No auto-merge or quota
  of generated issues; stop when the useful candidate set is exhausted.

Run record, created when there is actual evidence rather than empty scaffolding:
`crab_sha`, `maw_sha`, `prey_sha`, meal path, nutrient id, source-file evidence,
licence/mode uncertainty, reviewer verdict/reason, measured review time, proposed
change, target PR, tests and observed integration result. Keep private maws, local
usernames and transport identifiers out of public records. Publish only sanitized,
owner-approved examples.

The trial answers four questions: how long until the first useful recommendation;
how many reviewed candidates are supported and relevant; how much maintainer time
review costs; and which proposed improvements are actually accepted. Record the
measurement method and observation window. No promised success rate or invented time.

## 4. Give contributors small tasks with visible outcomes

Start with [the contributor entry point](COMMUNITY_START_HERE.md), not a request to
install an autonomous fleet. The current small tasks are:

- #274: verify installation and explain the offline smoke check.
- #276: nominate one inspectable prey with one specific nutrient and an actual maw gap.
- #277: independently fact-check one frozen benchmark nutrient.
- #278: record one executed local-fixture meal without GitHub publication.

Contributors reserve one task in its issue before starting. A readable audit comment
is welcome; maintainers agree a docs PR when it adds durable value. Do not split one
observation into many event-credit PRs. After acceptance, close the finite task and open
a distinct successor only when there is a real next question.

The `hacktoberfest` label is for discovery. The official
[2026 FAQ](https://dev.to/challenges/hf26) says PRs are not counted for rewards. Never
promise official eligibility, swag or mark work accepted merely because a label exists.

## 5. Publicize evidence in increasing order of reach

First publish one reproducible demonstration and one real before/after case study.
The demonstration follows #278; the case study links meal evidence, why the maw needed
it, what changed, the reviewing human's decision and the final target PR. Include one
rejected recommendation to make the limitations clear. Do not use fabricated terminal
screenshots, generated testimonials or unpublished branch work as shipped proof.

Then prepare a compact release pack: a clear README entry, installation steps, a
60–90-second executed demo, the case study, release notes and four contributor links.
Draft the story for DEV, then short audience-specific Bluesky/Mastodon summaries; these
are proposed channels, not permission to post with another agent/account. Coordinate
with the owner's growth workflow rather than create a second publisher.

A useful article angle is: **What our crab found, what we rejected, and which change
actually merged.** A generic feature list or a claim that the crab replaces maintainers
is weaker and harder to substantiate.

Broader launch communities and public showcases come after the first users can repeat
the experience. Track visits to the start guide, completed first meals, distinct useful
contributors and accepted maw improvements. Stars are a secondary reach signal, not
proof that recommendations work. Do not buy attention, mass-message maintainers or
open unsolicited generated issues in unrelated repositories.

## Exit conditions and honest holds

- Foundation stable: #224's actual evidence complete, not merely a prepared PR stack.
- First use usable: install plus first meal independently reproducible.
- Adoption demonstrated: reviewed improvements and rejected cases recorded honestly.
- Scheduled operation proven: #242/#243 satisfied separately; read-first pilot success
  is not proof of unattended tool authority or reliable scheduling.
- Learning demonstrated: #256 held-out Taste Memory results, not just passing unit tests.
- Promotion ready: a release and case study support every advertised capability.

If a gate is blocked, publish the truthful limitation and finish an independent useful
step. Do not keep rewriting the same local patch, create parallel counters/senders, or
turn a safety refusal into an invitation to use another account or API.
