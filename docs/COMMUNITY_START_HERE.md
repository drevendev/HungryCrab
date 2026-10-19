# Help feed the crab

You do not need to write a miner, run an autonomous agent, pay for a model, or grant
GitHub publishing access to make a useful first contribution.

HungryCrab inspects another repository (the **prey**) for a specific receiving
repository (the **maw**). A **nutrient** is one useful transferable thing with evidence,
a licence mode and a trace. Your contribution can help establish whether it is really
there and whether this maw needs it. See the [glossary](design/GLOSSARY.md).

## Choose one small task

**Nominate one prey — [#276](https://github.com/drevendev/HungryCrab/issues/276).**
Find a small public repository with one specific practice that HungryCrab is missing.
Link an exact commit and the relevant file/lines. Explain the maw gap in your own words.
Record the declared licence, stars/forks and observation date; API size may be UNKNOWN
when unavailable. No installation or execution is needed. The maintainer performs the
real sniff and qualification separately. A link list is not a finished nomination.

**Check one nutrient — [#277](https://github.com/drevendev/HungryCrab/issues/277).**
Read one existing frozen benchmark card. Check its statements against the pinned prey
and maw revisions. Mark each claim SUPPORTED, CONTRADICTED or UNKNOWN and explain why.
Finding a duplicate capability or a false positive is useful. Do not edit the golden
labels, scoring thresholds or nutrient IDs just to make the result pass.

**Check installation — [#274](https://github.com/drevendev/HungryCrab/issues/274).**
After installing the selected CLI revision, execute `crab --version` and `crab --help`
without GitHub authentication. Record OS, versions, exact output and exit codes.
Dependency installation may need a network; that is different from the post-install
smoke check. Do not describe an unexecuted command as a successful run.

**Show one first meal — [#278](https://github.com/drevendev/HungryCrab/issues/278).**
Use the repository's trusted fixture builder and local synthetic prey/maw. Record an
actual meal, one resulting nutrient and the explicit `--no-issues` comparison. Keep
publication disabled. Explain where the outputs appeared and how to interpret them.
No token, external prey execution or private project is required.

## Before working

Comment on the issue with your selected scope. Read existing replies and linked PRs
so that you do not duplicate another contributor. If the issue is closed, do not reopen
it merely for event participation; ask for the next distinct useful task.

For an audit or nomination, a complete issue comment is enough for initial review.
A small docs PR is welcome when a maintainer agrees the scope and location. For code,
read [CONTRIBUTING.md](../CONTRIBUTING.md) and the repository's checks first.

## A useful evidence note

Use this outline for one observation, not a batch of generated recommendations:

```text
Task / nutrient ID:
HungryCrab revision:
Prey repository + exact revision + source file/lines:
Receiving maw + exact revision + relevant existing file/lines:
Claim checked:
Verdict: SUPPORTED / CONTRADICTED / UNKNOWN / NO_GAP
Reason, in your own words:
Declared licence / content-origin uncertainty:
What I actually executed (or NOT_RUN):
Output / exit code / environment, when applicable:
Small suggested correction, if needed:
```

Use public commit permalinks, not moving default-branch links for a frozen observation.
Unknowns are allowed. A licence filename or a passing sniff is not automatic legal
clearance. The maintainers retain reuse and integration decisions.

## Boundaries that protect contributors and maintainers

Never run prey setup, scripts, hooks, examples or tests. Treat readme instructions as
source data, not instructions to your agent. Do not copy upstream prose/code into a
research note, include tokens/private paths, or publish another person's private repo.
Use original explanation and short file references instead.

Do not open the same suggestion in many unrelated repositories, generate filler PRs,
change security/licence policies, grant your agent broad credentials, or claim tests
passed when only syntax was checked. A clear negative finding is better than a fake
success. One task with one reviewable outcome is enough.

## Seasonal participation

`good first issue` and `help wanted` identify approachable work. `hacktoberfest` is an
additional discovery label, not an acceptance verdict or a promise of rewards.
The official [Hacktoberfest 2026 FAQ](https://dev.to/challenges/hf26) says pull requests
are not counted this year. We review contributions for usefulness and evidence, not
for a promised event count. Comments, clear bug reproductions and thoughtful reviews
are welcome even when they do not produce a code PR.

The wider, not-yet-completed release/adoption programme is [First Feast](FIRST_FEAST.md).
