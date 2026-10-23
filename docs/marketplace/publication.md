# Publication checklist — Hungry Crab Feeder

**Owner-mediated plan only. No release, Marketplace submission, tag or workflow
was changed to produce this file.** Tracking issue:
[#311](https://github.com/drevendev/HungryCrab/issues/311).

GitHub's official procedure:
[Publishing actions in GitHub Marketplace](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace)
and [Releasing and maintaining actions](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/release-and-maintain-actions).

## Verified preflight facts (2026-10-23 snapshot)

| Verified state | Meaning / next check |
| --- | --- |
| Public `drevendev/HungryCrab` | Meets public repository prerequisite; verify at launch |
| One root `action.yml`, name `Hungry Crab Feeder` | Marketplace will check that the name is globally unique and not reserved; do not assume acceptance yet |
| `branding.icon: search`, `color: orange` | Supported metadata candidate; verify "Everything looks good!" in publisher UI |
| Published GitHub Releases [v0.6.0](https://github.com/drevendev/HungryCrab/releases/tag/v0.6.0), [v0.6.1](https://github.com/drevendev/HungryCrab/releases/tag/v0.6.1), [v0.6.2](https://github.com/drevendev/HungryCrab/releases/tag/v0.6.2) exist | `v0.6.2` is the latest published release in this snapshot; verify the chosen tag, peeled SHA and release again at launch |
| `v0.6.2` points to `d7779ebdc7ad14998567bbd0fa17fc3c558cc49b`; the #399 privacy fix is newer | Select a reviewed Action ref containing `aa1b3bf454349358e52bd7ac9f5bc0b797f0c7ae` and verify its CI; any new release needs separate owner authorization |
| Third-party Action calls still use mutable version tags | Track/repair in existing [#301](https://github.com/drevendev/HungryCrab/issues/301) and independently verify new release |
| #211 and #236 remain open | Data ownership and hard quota constraints cannot be hidden by a green generic CI job |

GitHub's publishing guide asks for an Action repository limited to its metadata,
code and necessary supporting files. HungryCrab also ships CLI, MCP and plugin surfaces,
so M1 must explicitly assess this shared layout against the publishing requirements.
Record that decision before assuming eligibility. If a dedicated Action repository is
needed, propose it separately with preserved identity, licensing and release strategy.

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
- [ ] Verify the selected Action ref includes #399 and refuses public-origin nested
      checkouts and populated submodules carrying local-only bytes. Review ordinary
      non-Git output independently; forge visibility does not attest to local files.
- [ ] Preflight `max-repo-kb` is not a hard byte quota; mark #236 accurately
      and choose deliberately small test preys. Unsafe user-data deletion under
      #211 is a launch blocker for destructive paths until reviewed or
      intentionally excluded from the supported Action use.
- [ ] Actual cross-repo pinned-Action run with full suite / smoke on supported
      Linux/Windows hosts; exact Action HEAD tied to all passed CI jobs. Verify
      artifact and sources, clean maw and no unexpected side effects.

### C. Release identity
- [ ] Select the tested release through the normal reviewed release process, checking
      the tag, peeled commit and GitHub Release object together. Existing releases are
      recorded above; the chosen ref must include all required safety corrections.
      **Never move an immutable tag or use an untested head.**
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
