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
`PreToolUse` hooks: `crab-prey-guard` refuses a Bash or PowerShell command that touches the prey
cache unless it is one of a few audited read-only shapes, and `crab-cleanroom-guard` keeps the
clean-room implementer out of the cache altogether. Both run from the plugin's own `src/` through
`hooks/guard.py`, with the first Python 3.11+ the shell finds, so they exist wherever the plugin
is installed and do not depend on a console script being present or signed. When the launcher
cannot run the guard — no interpreter, an unreadable source tree — it says so and exits 1, which
Claude Code shows to you and proceeds on: a non-blocking error by design, because a hook that
blocked every tool call over its own environment would be disabled rather than fixed. They see
the Bash and PowerShell tools; a PowerShell command that touches the cache is allowed only as a
read cmdlet (`Get-Content`, `Get-ChildItem`, `Select-String`, `Test-Path`, `Get-Item`) with
literal arguments. The prey guard reads command text: it follows the paths a shell builds from
variables, `~`, globs and `cd`, and it refuses a command that points at the cache (its name,
`hungry-` for the default root, `~/.cache`) through a construct it does not model, such as a
nested shell, a parameter operator, an array, a substitution or a PowerShell expression. A path
computed inside a program — `python -c`, a script, a file read back later — cannot be judged by
a text guard at all. Codex does not load plugin hooks from a plugin with a root `plugin.json`
(openai/codex#39895); the prey guard has been observed refusing a command in a
live session, the clean-room guard has not yet. Treat them as a second line behind the rule in
`AGENTS.md`, not as a sandbox ([#83](https://github.com/drevendev/HungryCrab/issues/83),
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

Security fixes land on `master` first and ship in a patch release of the latest minor version,
which is 0.6.x today. Older minor versions and older tags get no backports: the way to a fix is
the latest patch release, or `master`. Each published advisory names the first release that
contains the fix as its patched version, so check a new report against the latest release and
`master`.
