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
`PreToolUse` hooks: `crab-prey-guard` refuses a Bash, PowerShell, Monitor or desktop-terminal
command that touches the prey cache unless it is one of a few audited read-only shapes (a
terminal command is judged as bash and as PowerShell, in the working directory it names), and a Write, Edit,
MultiEdit or NotebookEdit call whose file is in the cache; `crab-cleanroom-guard` keeps the
clean-room implementer out of the cache altogether. Both run from the plugin's own `src/` through
`hooks/guard.py`, with the first Python 3.11+ the shell finds, so they exist wherever the plugin
is installed and do not depend on a console script being present or signed. When the launcher
cannot run the guard — no interpreter that starts with `-I`, an unreadable source tree, or an
unexpected guard exception — it says
so and exits 1, which Claude Code shows to you and proceeds on: a non-blocking error by design,
because a hook that blocked every tool call over its own environment would be disabled rather
than fixed. A PowerShell command that touches the cache is allowed only as a read cmdlet
(`Get-Content`, `Get-ChildItem`, `Select-String`, `Test-Path`, `Get-Item`) with literal
arguments; a bash read may be piped into `head`, `tail`, `grep`, `wc` or `cut`, none of
which writes a file or starts a program. Before any `git` command in a clone is allowed,
the clone's `.git/config` is read the way git reads it (never run): one that names a
program git may start (`core.fsmonitor`, `core.pager`, `diff.external`, a textconv or
merge driver, a filter, an alias, an include, a credential helper), or a `commondir`
that sends git elsewhere, refuses it. The prey guard reads command text: it follows the paths a
shell builds from variables (`HOME` included), parameter defaults, `~`, globs, braces, `cd`
and the directory options programs take (`make -C`, `git -C`, `env -C`, `tar -C`, `uv
--directory`, `-WorkingDirectory`) and reads a command's relative words from home or any
other directory above the cache the command names; it reads a script handed to a nested
shell, `eval`, an alias or a command variable such as
`GIT_SSH_COMMAND` as a command of its own, and refuses a command that points at the cache (a glob
or 8.3 alias of its name, `~/.cache` spelled any way, the name in a path-shaped word) through a
construct it does not model, such as a parameter operator, an array, a positional parameter, a
value read from a pipe or a PowerShell expression; the bare name `hungry-crab`, or another
directory under `~/.cache`, counts beside a construct that computes text or turns it into
commands, and a substitution that holds any of them is refused. It refuses a command that links,
copies, archives or mounts the cache or `~/.cache` (`cp -r`, `rsync`, `tar`, `zip`,
`Compress-Archive`, `python -m zipfile`, a container's `-v`/`--mount`, read-only or not),
even on its own; one that runs what a search of the cache or a directory above it finds
(`find ~ … | xargs python`, `fd … ~ -x`, `rg --pre`, `Get-ChildItem ~ -Recurse |
ForEach-Object`, cmd's `for /r`, `forfiles /s`), a copy or a mount of home searched or
entered counting as home; and an encoded script it cannot decode. Message text is no
exception: a commit, tag or pull request message that spells the cache counts as the
cache, as it did in 0.6.1, because it can be read back and run. A command with carriage
returns is judged both as Git Bash reads it, without them, and as bash elsewhere does. A
command it cannot parse, or cannot finish judging within its fixed budget, passes only
when its text is plain: no expansion, glob, tilde, brace or escape and no hint of the cache. A
path computed inside a program — `python -c`, a script, what a program prints, a file read back
later, a script a program decodes (`echo … | base64 -d | sh`) — cannot be judged by a text
guard at all, and neither can a copy of the cache made by an earlier call or by a program (a
backup of the home directory, an archive unpacked later): the guard finds such a copy only
where its path still spells `.cache/hungry-crab`, or where the same command made it. A
copy, clone or shell over ssh from this machine (`scp localhost:.cache/…`, `git clone
ssh://localhost/~/…`) names a local path and is judged as one. The desktop terminal tool
reuses a tab by its `tab_id`, which keeps whatever directory earlier commands left it in;
the guard cannot see that directory, only the `cwd` a call names. Codex does not
load plugin hooks from a plugin with a root `plugin.json`
(openai/codex#39895); the prey guard has been observed refusing a command in a
live session, the clean-room guard has not yet. Treat them as a second line behind the rule in
`AGENTS.md`, not as a sandbox ([#83](https://github.com/drevendev/HungryCrab/issues/83),
[#141](https://github.com/drevendev/HungryCrab/issues/141)).

### Cache ownership before mutation

Before refreshing, replacing or removing an existing prey cache, Crab requires a catch or sniff
record naming that prey and verifies that the clone has exactly the requested origin. It also
checks the effective URL after Git includes, URL rewrites and environment configuration, and
refreshes only that origin. A sniff record owns provider metadata; an existing clone also
needs its acquisition marker or matching catch record. Sniff refuses to claim an existing
unowned directory, and a checkout at the cache-entry root is refused even with a legacy marker.
Links and Windows reparse points below the chosen cache root are
refused; the root itself and its ancestors may be user-configured filesystem aliases.
Generated API and catch files are checked at their exact output paths and atomically replaced
through exclusive temporary files, so existing hardlinks are never opened for truncation.

New main and wiki clones also carry an acquisition marker inside `.git`. Standalone wiki
replacement requires that marker or a matching wiki catch record. Whole-entry removal verifies
wiki provenance independently from the main clone; a main-only catch record does not authorize
deleting an unrelated wiki checkout. Quota transactions
verify old clone identity before moving any cache entry. Ownership and output-path preflight
refusals preserve existing data;
an old unmarked checkout must be inspected and moved aside manually rather than adopted by
`--force`. These checks do not claim protection from a concurrent process rewriting the cache.
Local clones do not share object hardlinks with their source. Cleanup refuses permission
repair on a shared file inode and reports retained data for inspection.

### Acquisition limits

Remote Feeder acquisition requires a dedicated fixed-capacity filesystem and a shared download
budget across repositories and wikis. Unsupported hosts refuse remote acquisition. The public
Linux Action provisions a disposable volume; see [the Feeder guide](docs/feeder.md). Quota
failure or handled cancellation discards staging and preserves the previous cache cohort.

### What is not bounded: acquisition

Miner caps apply to what a miner reads. Outside the Feeder quota scope, standalone `crab catch`
runs a full `git clone`, and refreshing a cached prey fetches its verified origin with pruning
and tags. Both
are bounded by time — one hour for the initial clone, ten minutes for a refresh — and by nothing
else: there is no byte, object or disk limit, and nothing refuses a repository for being too
large. A hostile or merely enormous prey can therefore fill a disk before any miner sees it.

For standalone acquisition the bound is yours to set:

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
