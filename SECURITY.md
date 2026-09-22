# Security Policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting:
<https://github.com/drevendev/HungryCrab/security/advisories/new>. Do not open a public issue for
security problems. You will get an acknowledgement within a week.

## Threat model

Hungry Crab downloads and analyses repositories written by strangers. The tool is built so that a
malicious prey repository cannot:

1. execute code on the machine that digests it (the miners only read files and run read-only git
   plumbing; nothing inside the cache is ever run);
2. smuggle instructions to an agent through the digest (summaries carry structure, not body text,
   and instruction-like fragments are flagged);
3. exhaust the machine **while it is being digested** — file counts, file sizes, vendored
   directories and commit counts are all capped, so a prey larger than the caps yields a thinner
   digest rather than a longer one.

### What the agent-side guard covers

The first promise is mechanical inside Claude Code, and only there. The plugin ships two
`PreToolUse` hooks: `crab-prey-guard` refuses a Bash command that touches the prey cache unless
it is one of a few audited read-only shapes, and `crab-cleanroom-guard` keeps the clean-room
implementer out of the cache altogether. Both are console scripts of the `hungry-crab`
distribution, so they exist only where the CLI was installed (`uv tool install`), and a hook
whose command is missing does not block — Claude Code treats that as a non-blocking error and
the tool call proceeds. They see the Bash tool, not PowerShell; Codex runs plugin hooks only
after you trust them; and neither has yet been observed refusing a command in a live session.
Treat them as a second line behind the rule in `AGENTS.md`, not as a sandbox
([#83](https://github.com/drevendev/HungryCrab/issues/83),
[#141](https://github.com/drevendev/HungryCrab/issues/141)).

### What is not bounded: acquisition

Those caps apply to what a miner reads, not to what `crab catch` downloads. By default `catch`
runs a full `git clone`, and refreshing a cached prey runs `git fetch --all --prune --tags`. Both
are bounded by time — one hour for the initial clone, ten minutes for a refresh — and by nothing
else: there is no byte, object or disk limit, and nothing refuses a repository for being too
large. A hostile or merely enormous prey can therefore fill a disk before any miner sees it.

Until that changes, the bound is yours to set:

- `crab sniff <prey>` reports the repository's size from the API before anything is cloned, and
  warns above 300 MB and again above 1 GB;
- `crab catch --shallow --since 2y` limits what is fetched;
- the cache lives under `~/.cache/hungry-crab/` and `crab cache rm <prey>` empties it.

Reports about any of these, or about the license engine producing a permissive verdict where a
restrictive one is warranted, are very welcome.

## Supported versions

The project is pre-release; only the `master` branch receives fixes.
