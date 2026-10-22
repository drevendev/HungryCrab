# Publication checklist — Hungry Crab Feeder

**Owner-mediated plan only. No release, Marketplace submission, tag or workflow
was changed to produce this file.** Tracking issue:
[#311](https://github.com/drevendev/HungryCrab/issues/311).

GitHub's official procedure:
[Publishing actions in GitHub Marketplace](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace)
and [Releasing and maintaining actions](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/release-and-maintain-actions).

## Preflight facts to reconcile (2026-10-22 snapshot)

| State at planning time | Meaning / next check |
| --- | --- |
| Public `drevendev/HungryCrab` | Meets public repository prerequisite; verify at launch |
| One root `action.yml`, name `Hungry Crab Feeder` | Marketplace will check that the name is globally unique and not reserved; do not assume acceptance yet |
| `branding.icon: search`, `color: orange` | Supported metadata candidate; verify "Everything looks good!" in publisher UI |
| Annotated `v0.6.0` tag points to `030155d9078dd86a39a8b78879d30e35664ec4bb` | Tag CI completed successfully (eight jobs); **not** evidence of external Action consumption |
| `v0.6.0` GitHub Release not returned by API; latest published Release `v0.2.0` | Reconcile/release the verified tag *only through normal owner authorization*; do not retag silently |
| Third-party Action calls still use mutable version tags | Track/repair in existing [#301](https://github.com/drevendev/HungryCrab/issues/301) and independently verify new release |
| #211 and #236 remain open | Data ownership and hard quota constraints cannot be hidden by a green generic CI job |

GitHub recommends a repository containing only the Action's necessary files.
This is **guidance**, not proof that HungryCrab's shared CLI repository is
ineligible. Try validating current root Action first, measure dependency and
checkout cost; consider a separate repository only as an independently
reviewed decision with preserved identity and release strategy.

## Release gates (check in order)

### A. Metadata, product, and support
- [ ] Action name uniqueness checked in publisher UI; avoid reserved GitHub features,
      usernames and category names; metadata `description` concise and factual.
- [ ] Exactly one root `action.yml`/`action.yaml`; `author`, inputs, outputs,
      `using: composite`, Feather icon, color and usage docs agree.
- [ ] Correct categories: propose **Code quality** primary, **Utilities** secondary,
      subject to form availability. Do not market as PR reviewer/security scanner.
- [ ] Visible README quickstart, install, required token permissions and limits;
      CONTRIBUTING, LICENSE, issue/support and security reporting links.
- [ ] No misleading "safe by Marketplace verification" or "autonomous fixes" claims.

### B. Trusted execution and reproducibility
- [ ] Review and pin third-party `uses:` dependencies by immutable SHAs;
      reconcile with [#301](https://github.com/drevendev/HungryCrab/issues/301).
- [ ] Action executes trusted code from the reviewed release, not prey code;
      test PowerShell, path quoting and `github.action_path` on Linux/Windows.
- [ ] Validate read-only job permissions, no persisted checkout credentials,
      no secret in log/artifact, and explicit `allow-private-artifact: false`.
- [ ] Verify public prey visibility and private negative tests before uploading;
      no test from public maws using unreviewed private prey or private evidence.
- [ ] Preflight `max-repo-kb` is not a hard byte quota; mark #236 accurately
      and choose deliberately small test preys. Unsafe user-data deletion under
      #211 is a launch blocker for destructive paths until reviewed or
      intentionally excluded from the supported Action use.
- [ ] Actual cross-repo pinned-Action run with full suite / smoke on supported
      Linux/Windows hosts; exact Action HEAD tied to all passed CI jobs. Verify
      artifact and sources, clean maw and no unexpected side effects.

### C. Release identity
- [ ] Resolve the existing `v0.6.0` annotated tag versus missing Release object.
      Pick the **tested** existing tag or a later patch commit by normal reviewed
      release process; **never move an immutable tag or use an untested head**.
- [ ] Approve release notes accurately describing **Feeder Action**, supported
      platforms, defaults, privacy and known limitations; no fabricated test.
- [ ] Verify release commit, tag object/peeled SHA, dependency pins and
      all required tag-triggered CI jobs refer to the same intended sources.
- [ ] Choose support/upgrade policy: GitHub can display semantic versions,
      but production consumer examples should pin full immutable SHAs. Do
      not use `@master` for an accepted proof.

### D. Human GitHub Marketplace UI steps
- [ ] Maintainer navigates to the root `action.yml` banner / Draft a release.
- [ ] Accept the **Marketplace Developer Agreement** if required and satisfy
      account **2FA**. These account actions require the owner; no API shortcut.
- [ ] Select **Publish this Action to the GitHub Marketplace**.
- [ ] Resolve metadata/name warnings until **"Everything looks good!"**.
- [ ] Choose validated primary/secondary categories, the approved tag and
      truthful release title/text; publish the Release using normal permissions.
- [ ] Obtain actual public listing URL and read it back. GitHub publishes eligible
      Actions immediately; listing is not an independent code security audit
      or a "Verified creator" credential.

### E. Post-publish acceptance (not optional)
- [ ] A separate public maw uses the **actual listing's** installation snippet,
      reads the correct pinned release, and passes a genuine Action run.
- [ ] Link run ID/job, artifact `menu.md/json`, relevant source/maw SHAs and
      human review; add truthful public evidence under [#310](https://github.com/drevendev/HungryCrab/issues/310).
- [ ] Confirm Marketplace title, version, README, icon, categories, links and
      copy/paste workflow work; include Linux/Windows proof as appropriate.
- [ ] Track security reports, version updates, onboarding failures and
      external contribution feedback. Create issues from confirmed bugs,
      not from speculative metrics.
- [ ] Start an evidence-first First Feast post/article/demo only after approval
      ([#312](https://github.com/drevendev/HungryCrab/issues/312)).

## Release copy proposal (not submitted)

**Title:** `Hungry Crab Feeder — Evidence-Backed Repo Menus`

**Short body:**

> Hungry Crab Feeder compares a source repository (prey) with your project
> (maw) and uploads an inspectable, license-aware menu of potential
> improvements. The Action is deterministic and model-free; it does not
> execute prey code or open PRs for you. Use a public reference repo and
> read-only workflow permissions. Explore `menu.md` and the source/policy
> trace, decide what is useful, and keep control of your project.
>
> Start with the [Feeder guide](../feeder.md). Review the public/private
> artifact boundary and acquisition-limit caveats before using sensitive
> repositories.

Actual release notes must describe the **real approved tag** and may differ
from this proposal. Do not associate 0.6.0 with the Marketplace if the tested
Action ref for publication is a later patch.

## Rollback / incident plan

If a leak, wrong tag, output bug or safety issue is discovered after
publication, halt promotion and communicate the affected release/ref. The
owner may uncheck Marketplace publication for the affected Release using
GitHub's documented release editor. Do not automatically disable workflows,
delete history, rewrite tags, or revoke unrelated tokens. Fix on a reviewed
branch, test a new immutable release, and update docs with exact affected
versions and a transparent postmortem.

## Launch-stop summary

**NO GO** for: private artifact leak, unsafe cache deletion of user data,
failed exact-ref Windows/Linux CI, mismatched release SHA, unreviewed mutable
dependencies, an unvalidated Marketplace name, or falsely represented
candidate/adoption metrics. In all other cases, report explicit
remaining limits instead of dressing them up as proven guarantees.
