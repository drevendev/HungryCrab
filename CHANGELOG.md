# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.3] - 2026-10-23

A security and reliability update for the CLI and both agent plugins. It bounds untrusted
provider/artifact reads, enforces strict copy policy and protects publication/release provenance.
The experimental publication and scheduled-work acceptance criteria remain open.

### Fixed

- **Bounded reads:** GraphQL drains stdout/stderr with byte caps and a deadline while sending
  request input; digest integrity rejects oversized or size-mismatched JSON before parsing and
  rejects JSON nesting above 128 containers; HTML reports stop reading when their aggregate page budget is spent,
  including failed page reads ([#300](https://github.com/drevendev/HungryCrab/issues/300), items 1–3).
- Document every CLI command and skill in the agent guide and glossary, correct hook coverage,
  and remove the completed installation task from the community guide. Regression checks compare
  the documented lists with the parser and skill directories
  ([#316](https://github.com/drevendev/HungryCrab/issues/316)).
- **Catch:** `--since` values beyond the representable date range (e.g. 4301-digit durations)
  are now rejected with a usage error instead of being passed through
  ([#321](https://github.com/drevendev/HungryCrab/issues/321)).
- **Security:** strict mode downgrades both COPY and COPY_FILE code to clean-room REIMPLEMENT,
  treats scripts and unknown material conservatively, and rechecks materialization receipt paths
  ([#299](https://github.com/drevendev/HungryCrab/issues/299)). Multi-prey publication resolves
  commenter-origin evidence from its own source bundle and refuses relative traversal and links
  ([#338](https://github.com/drevendev/HungryCrab/issues/338)).
- Preserve comma-separated generic type arguments such as `HashMap<K, V>` in wiki headings while
  continuing to strip inline HTML ([#390](https://github.com/drevendev/HungryCrab/issues/390)).
- **Loop security:** MOLT/HARDEN protect nested licence/notice files and attribution records;
  landings and tags require a provider-confirmed user merger distinct from the publisher;
  release traces match exact URLs, nutrient IDs and versions
  ([#345](https://github.com/drevendev/HungryCrab/issues/345),
  [#346](https://github.com/drevendev/HungryCrab/issues/346),
  [#349](https://github.com/drevendev/HungryCrab/issues/349)).

## [0.6.2] - 2026-10-23

A security release on 0.6.0 Everywhere, and the first release published to PyPI from CI. It
fixes [GHSA-443x-5484-qr4g](https://github.com/drevendev/HungryCrab/security/advisories/GHSA-443x-5484-qr4g):
the prey guard judged a command's text rather than the paths the shell builds from it, and
tools other than Bash and PowerShell (the Monitor tool, file writes) were not judged at all.
The fix went through seven rounds of independent adversarial review and ships only where no
reviewed case is weaker than 0.6.1. It also fixes what the review of 2026-10-23 found in 0.6.1
itself: the Feeder action, the pre-push secret scan and heading extraction
([#397](https://github.com/drevendev/HungryCrab/pull/397)). `hungry-crab` is on PyPI now
(`uv tool install hungry-crab`). Update the CLI and the plugins: Claude Code offers the plugin
update with this version.

### Security

- **Prey guard:** a path the shell builds from the command text is judged as the shell builds
  it ([GHSA-443x-5484-qr4g](https://github.com/drevendev/HungryCrab/security/advisories/GHSA-443x-5484-qr4g)).
  A script handed to a nested shell, `eval`, `xargs`, `find -exec`, `env -S`, an alias, `git -c`,
  a command variable such as `GIT_SSH_COMMAND`, or any other program as a quoted command line is
  read as a command of its own, and so is a quoted substitution bash evaluates again
  (arithmetic, prompts, `${x@P}`); a `cd` counts wherever the shell runs it, and variables the
  command changes, `HOME` included, are followed; a path attached to a short option, named in a
  response file or listed in a path variable is resolved; brace alternatives and parameter
  defaults are expanded, a word is also judged with its glob and brace characters taken
  literally, and the text of an expansion or a substitution may hold `/` and `..`; comments,
  here-documents, here-strings, `$$` and carriage returns are read as bash reads them, and
  PowerShell words, comments, here-strings, encoded commands and cmd variables as PowerShell and
  cmd read them; a glob or an 8.3 alias that can match the cache or its parent is a hint of the
  cache; Win32's handling of trailing dots and spaces and Git Bash's `/tmp` are followed. The
  bare name and another directory under `~/.cache` count beside a construct that computes text
  or runs it, and a substitution holding either is refused; a value the command assigns is read
  as code where a shell, `eval`, `trap` or `mapfile -C` may run it; a script a program runs that
  no shell grammar can read is judged by its hints; unknown text glued to the home directory can
  lead anywhere below it; a path compared by its spelling alone, past the budget or too deep to
  resolve, counts as reaching the cache when it hints at it; PowerShell's `Set-Item
  Variable:`, `-OutVariable` and `Tee-Object -Variable` are followed, and a typographic dash in
  a PowerShell's arguments is a dash. A command holding carriage returns is judged both as Git
  Bash reads it, without them, and as bash elsewhere does. A search or recursive listing that
  walks the cache or a directory above it (`find ~`, `fd`, `rg --files`, `grep -r`, `locate`,
  `Get-ChildItem -Recurse`) is refused when what it finds feeds something that runs, and a copy
  of the cache is followed through `cd`. A directory option (`make -C`, `git -C`, `env -C`,
  `tar -C`, `uv --directory`, `-WorkingDirectory`) or a word naming home or another directory
  above the cache moves the command's relative words there; a strong hint beside a
  substitution or a special parameter is refused, as 0.6.1 did. A container bind mount of the
  cache or `~/.cache` is a copy of it, and a copy or a mount of home searched or entered is
  home. The Monitor tool's command is judged as bash, the desktop terminal's as bash and as
  PowerShell in the `cwd` it names, any other tool's `command` as bash, and a
  Write, Edit, MultiEdit or NotebookEdit call whose file is in the cache is refused; a clone's
  `.git/config` that names a program git may start keeps every `git` command there from the
  allowlist. An `scp`, `rsync`, `sftp`, `ssh` or `git` path on this machine
  (`localhost:.cache/…`, `ssh://localhost/~/…`) is a local path, and a brace expansion or
  an array read beside a way to the cache is refused, as 0.6.1 did. A command that links,
  copies, archives or mounts the cache or `~/.cache` is
  refused, even on its own, and so is an encoded script the guard cannot decode. Every decision runs on one bounded budget, and a command the guard cannot parse or
  finish judging within it passes only when its text is plain.
- **Fewer false refusals:** a `grep`, `rg` or `Select-String` search pattern, a fetched URL and
  the package name outside a path (a comparison, echoed text, an install request) are no hint
  of the cache; an exit status and braces are followed rather than refused; a quoted `awk`
  program is not shell syntax; a value `read` from a file, an `ssh` remote command and the home
  directory as the working directory refuse nothing on their own; a substitution is refused
  only when its own text, or the directory it runs in, points at the cache; `pip install .`,
  `cp -r .` or `zip -r` run from the home directory, and another directory under `~/.cache`
  listed into `read`, are not copies or hints of the cache; an allowed bash read may be
  piped into `head`, `tail`, `grep`, `wc` or `cut`; `dirname` and `basename` are printers
  ([#392](https://github.com/drevendev/HungryCrab/issues/392)).
- **Hooks:** the hook command starts the launcher only with a Python that accepts `-I`; an older
  interpreter first on `PATH` no longer exits 2 and blocks every tool call, it is passed over,
  and with no other the hook says so and exits 1
  ([#393](https://github.com/drevendev/HungryCrab/issues/393)).

### Added

- **PyPI:** releases are published to [PyPI](https://pypi.org/project/hungry-crab/) as
  `hungry-crab` by `.github/workflows/publish.yml`, which runs when a GitHub release is
  published, refuses unless the run's tag names the packaged release version, builds the sdist
  and wheel from the tag, checks them with `twine check --strict` and uploads them through
  Trusted Publishing, with no stored token; only the upload job may request an OIDC token, and
  every action is pinned by commit. `CONTRIBUTING.md` carries the step and how to verify it
  ([#395](https://github.com/drevendev/HungryCrab/issues/395)).

### Fixed

- **Feeder:** populated submodules now require `allow-private-artifact: true` for public or
  unknown callers even when their origin is public, including when a gitlink itself is selected
  as the maw. Local untracked, ignored, modified and unpublished bytes cannot be proven public
  by remote visibility. Empty, uninitialised gitlinks remain allowed
  ([#405](https://github.com/drevendev/HungryCrab/issues/405)).

- **Feeder:** for public or unknown callers, a public maw containing a separate nested Git
  checkout (including a second `actions/checkout`) is refused before digest unless
  `allow-private-artifact` is explicitly enabled. A public Git remote cannot vouch for
  local, untracked, ignored, modified or unpublished files. Nested `.git` marker detection
  covers case variants on Windows; ordinary non-Git build output is unaffected
  ([#398](https://github.com/drevendev/HungryCrab/issues/398)).

- **Feeder:** a submodule of the public calling repository, or a directory inside one, no
  longer counts as a tracked directory of the caller; its gitlink is another repository, so the
  maw is confirmed by its own origin through the forge, and refused without
  `allow-private-artifact` when nothing confirms it. A maw confirmed public no longer carries
  a private submodule into the artifact either: every checked-out submodule under it, nested
  ones included, must have an origin the forge confirms public, or the run is refused naming
  the submodule; an uninitialised one holds nothing and is fine
  ([#386](https://github.com/drevendev/HungryCrab/issues/386)).
- **Publication:** `serve --as pr-branch` no longer refuses its own pull-request body. The
  pre-push secret scan reads the commit, blob, tree and raw URLs of GitHub and GitLab, the
  shape of the crab's evidence links, as a public repository, a commit and a path judged
  apart, so a hex commit no longer joins a mixed-case path into one high-entropy token; the
  path is only what the crab's own links contain, and it is still judged as any path is
  ([#387](https://github.com/drevendev/HungryCrab/issues/387)).
- **Publication:** the 0.6.1 secret scan no longer refuses ordinary text it took for
  credentials: a digest after the `@` (`docker://alpine:3.20@sha256:…`) is not a host, so an
  image pinned by digest is not a password in a URL, the Slack shape lists only Slack's
  prefixes (`xox[abcdeprs]-`, `xapp-`), so `xoxo` is a word, and `X-` header names, the
  Kubernetes Secret a chart's `existingSecret` names and YAML or JSON translations under
  `locales/`, `i18n/`, `translations/` and similar directories (and locale-named files under
  Laravel's `lang/`) are not credential literals
  ([#388](https://github.com/drevendev/HungryCrab/issues/388)).
- **No body text in summaries:** nothing inside an HTML block that runs to an end condition
  (`<pre>`, `<script>`, `<style>`, `<textarea>`, comments, processing instructions,
  declarations and CDATA) is a heading until that condition, blank lines included, such a block
  or a fence inside a list item or table leaves the line after it body text, and a link
  reference definition is never setext heading text, so usage text and URLs underlined with
  `---` or `===` no longer reach `docs.md`, `ai.md` or the wiki outline
  ([#389](https://github.com/drevendev/HungryCrab/issues/389)).
- **Documentation:** `SECURITY.md` states the supported versions as they are: security fixes
  land on `master` and ship in a patch release of the latest minor version (0.6.x today), older
  tags get no backports, and advisories name the first fixed release; the README's
  `crab --version` example no longer names a stale version
  ([#391](https://github.com/drevendev/HungryCrab/issues/391)).
- **Installation:** the README and the Everywhere guide no longer say that the package is not
  on PyPI or that the `hungry-crab` name there is not ours; `uv tool install hungry-crab` (or
  `pipx install hungry-crab`) is the stable path, and the git URL tracks `master`. `crab update`
  recognises a CLI installed from the index, compares it with the latest release on PyPI and
  prints `uv tool upgrade hungry-crab` (or the pipx or pip equivalent) instead of reporting
  `unknown` and suggesting a reinstall from git; a git install still follows `master`
  ([#395](https://github.com/drevendev/HungryCrab/issues/395)).

## [0.6.1] - 2026-10-22

A security release on 0.6.0 Everywhere, with no new features. It fixes two vulnerabilities
that let a hostile prey's own git configuration run a program, published as
[GHSA-rjrx-3h4x-g7m4](https://github.com/drevendev/HungryCrab/security/advisories/GHSA-rjrx-3h4x-g7m4)
(a local prey's submodule filter during `crab digest`) and
[GHSA-vp52-vp37-p63w](https://github.com/drevendev/HungryCrab/security/advisories/GHSA-vp52-vp37-p63w)
(`git -C` into a git directory shipped inside the prey, allowed by the prey guard), and every
other finding of the 2026-10-22 review that broke a promise of the threat model in
`SECURITY.md`, after an independent review and a re-review of the fixes
([#380](https://github.com/drevendev/HungryCrab/pull/380)). Update the CLI and the plugins:
Claude Code offers the plugin update with this version.

### Security

- **No prey program runs through git.** No crab git command starts a child git inside a local
  prey's submodules, so a filter named by a submodule's own configuration never runs; a
  worktree fingerprint that cannot vouch for a populated submodule is `unknown`. The prey guard
  lets cache-touching git open only a clone root that `crab catch` made, refuses `--git-dir`,
  `--work-tree`, `--namespace` and every other `-C` target in the cache, a quoted `~`, and the
  options that run programs in any abbreviated or clustered spelling (`--filters`, `--open`,
  `-O`, `--textconv`, `--ext-diff`).
- **Guards:** the prey guard refuses a command that carries a hint of the cache through a
  construct it does not model (nested shells, parameter operators, arrays, `$'…'`, braces,
  substitutions; PowerShell groups, `+`, `-f`, `-join`, `Join-Path`), while text about the
  cache and its parent directory stays allowed; a path a program computes for itself cannot be
  judged by a text guard, as `SECURITY.md` now says
  ([#142](https://github.com/drevendev/HungryCrab/issues/142)); both guards canonicalise
  `\\?\` device, volume and GLOBALROOT paths, local admin shares and 8.3 short names without
  network lookups ([#188](https://github.com/drevendev/HungryCrab/issues/188)) and read the
  hook event as UTF-8
  bytes on Windows ([#315](https://github.com/drevendev/HungryCrab/issues/315)); the clean-room
  guard refuses Grep and Glob scopes that contain the cache
  ([#314](https://github.com/drevendev/HungryCrab/issues/314)); the PowerShell tool now goes
  through both guards, with plain read cmdlets the only cache-touching PowerShell allowed
  ([#141](https://github.com/drevendev/HungryCrab/issues/141)).
- **Scope:** MCP refuses a directory whose git directory, common directory, object or ref store
  lies outside the approved roots, is reached through a link, or borrows objects through
  alternates ([#322](https://github.com/drevendev/HungryCrab/issues/322)) and checks a prey
  path against the roots before touching it
  ([#327](https://github.com/drevendev/HungryCrab/issues/327)); the inventory walk does not
  follow directory junctions, while OneDrive folders are still walked
  ([#323](https://github.com/drevendev/HungryCrab/issues/323)); licence evidence is read only
  from the prey's own tree, never through a link; `crab digest --out` refuses a junction,
  `crab cache rm` removes only a prey directory the crab recorded (a first catch records itself
  before it clones), and a held file ends in a clear error
  ([#211](https://github.com/drevendev/HungryCrab/issues/211)); every `gh` call, prey- and
  maw-side, is pinned to github.com whatever `GH_HOST` says
  ([#318](https://github.com/drevendev/HungryCrab/issues/318)).
- **Bounded digestion:** JUnit archives are inflated in bounded chunks and only stored or
  deflate members are read ([#317](https://github.com/drevendev/HungryCrab/issues/317)); the
  Gradle and other manifest patterns, the front-matter, heading, link, HTML-comment and
  pipe-to-shell rules run in linear time
  ([#329](https://github.com/drevendev/HungryCrab/issues/329),
  [#331](https://github.com/drevendev/HungryCrab/issues/331),
  [#300](https://github.com/drevendev/HungryCrab/issues/300)); deeply nested manifests and
  expanding YAML anchors and merge keys, charged before the document is built, are unparseable
  files, not failed miners
  ([#330](https://github.com/drevendev/HungryCrab/issues/330)); miner warnings are capped per
  miner ([#324](https://github.com/drevendev/HungryCrab/issues/324)).
- **No body text in summaries:** only a paragraph line can be a setext heading
  ([#332](https://github.com/drevendev/HungryCrab/issues/332)); wiki headings carry the same
  caps as README headings ([#337](https://github.com/drevendev/HungryCrab/issues/337)); bidi
  overrides and isolates and plural instruction comments are flagged (bidi marks only where
  nothing is right-to-left), instruction-like author, path, workflow, tag and branch names are
  redacted in the Markdown summaries, also when spelled with `-`, `_`, `/` or `.`, and headings
  lose images, link targets and inline HTML while code spans stay as written
  ([#192](https://github.com/drevendev/HungryCrab/issues/192)).
- **Publication:** the Feeder action refuses a public meal artifact unless the maw is
  confirmed public as well as the prey; a tracked directory of the public calling repository
  counts as public ([#341](https://github.com/drevendev/HungryCrab/issues/341)); the pre-push
  secret scan flags credentials (private keys, GitLab and Slack tokens, webhooks, passwords in
  URLs, credential-named keys in configuration files) instead of identifiers that merely
  contain a credential word, and runs in linear time
  ([#135](https://github.com/drevendev/HungryCrab/issues/135)).

## [0.6.0] - 2026-10-22

Everywhere is a limited stable release of deterministic, human-supervised repository analysis:
the CLI, scoped local MCP, portable skills and offline HTML reports. The owner approved this
scope on 2026-10-21 and deferred the original live acceptance criteria of the earlier
milestones to post-release work; none of them is declared complete. The 0.3-0.5
implementations are included, but PR publication, Scheduled Crab work autonomy and
learning/usefulness claims remain experimental. The optional tree-sitter syntax index was
postponed, so the runtime dependency is PyYAML alone and MCP is the only optional extra.
Install from the `v0.6.0` tag; the package is not on PyPI. The 0.2.1 and 0.2.2 milestones were
never tagged separately, and their changes are included here.

Known issues: cache deletion does not yet prove that the cache owns what it deletes
([#211](https://github.com/drevendev/HungryCrab/issues/211)); licence detection still has open
edge cases ([#189](https://github.com/drevendev/HungryCrab/issues/189),
[#202](https://github.com/drevendev/HungryCrab/issues/202),
[#204](https://github.com/drevendev/HungryCrab/issues/204)); the explicit-output ownership
repair awaits verification against [#210](https://github.com/drevendev/HungryCrab/issues/210).
Live acceptance stays open in #224, #236, #242, #243, #250 and #256; see the
[release record](docs/releases/0.6.0-readiness.md).

### Added

- **0.6 Everywhere:** `crab mcp` exposes scoped `crab_digest` and
  paginated `crab_menu` tools over local stdio using the optional official SDK extra. Startup
  roots constrain local access; network reads are opt-in; the adapter neither executes prey,
  publishes nor mutates the ledger. Modern and legacy protocol clients are tested. UNC and
  device paths are refused before any filesystem call, a path must lie under a root as written
  before it is resolved, a directory that belongs to a git repository above every root is
  refused, and `crab mcp --check` works from any directory, the home directory included.
- **Offline HTML:** `crab report` renders existing digests and single/multi-prey menus as
  self-contained, responsive HTML with coverage, miner health, all retained pages/cards,
  licence modes and pinned evidence. Escaping, safe links, a restrictive content policy,
  bounded reads and explicit output replacement protect the report boundary.
- **Installation acceptance:** isolated uv tool installs from our sdist and wheel on Windows
  and Linux, including installed CLI/MCP/report operations and portable `npx skills add`.
  Host configuration examples and the un-published package-index boundary are documented in
  [Everywhere](docs/everywhere.md). The package is not published to PyPI.

- **0.5 Taste Memory (development milestone):** bounded category learning from confirmed
  decisions, explainable memory snapshots, gap-directed GitHub `hunt`, reviewed hunger profiles,
  atomic multi-prey Feeder bundles with paged menus, full source provenance and conservative
  stable-id deduplication. Aggregate serving retains per-source license and receipt verification.
- **Discovery HUNT:** opt-in, maw-owned discovery with a shortlist bound to the active phase lease.
  Only recording advances the scheduled protocol; fixed prey lists remain allowlists.
- **Reliable feedback:** retain explicit acceptance across automatic issue serving; atomically
  save supported ledgers without losing unknown fields and refuse future or malformed schemas.
- **Tuning repairs:** repeat writes are idempotent, comments and surrounding policy survive,
  real prey-scoped trait families can learn, already-disabled hunger is respected, invalid sample
  thresholds fail, and every uptake kind uses its configured weight. Reject maw config typos and
  wrong-shaped settings before applying policy. See [Taste Memory](docs/taste-memory.md).

- **0.4 Deep Bite (development milestone):** provider signals and eight dependency ecosystems,
  below. The optional tree-sitter syntax index it also planned is postponed until a real
  evidence gap asks for it: native parsers reading hostile prey, ten more packages to keep in
  step and digests that differ by environment outweighed what it added, so `code` stays a
  deferred category and the runtime dependency is still PyYAML alone.
- **Provider signals:** opt-in GitHub Discussions, GitHub/GitLab review comments and run samples;
  job durations and rerun recoveries, plus explicit JUnit flaky-rerun evidence. Unknown report
  coverage stays unknown. Commenter prose remains JSON-only and cannot be quoted in served
  notes. GitLab.com supports nested namespaces, separate cache/authentication and forge-qualified
  ownership; publication still requires a GitHub maw.
- **Eight dependency ecosystems:** Python, npm, .NET, Go, Rust, Ruby, JVM (Maven/Gradle), PHP
  (Composer), with explicit unresolved inheritance/catalog facts and no package execution.
- **Quality measurement:** a 56-candidate ranking pressure report alongside the unchanged human
  B1 corpus (report only: with the default scoring four accepted priorities rank 31-35, and the
  defaults are not tuned to its keys), and B2 freeze/record/blind/fact-check/report commands with immutable receipts, common
  harness/token budgets, repeated measurements, judge agreement and a 20% human audit. The live
  sweep remains [#250](https://github.com/drevendev/HungryCrab/issues/250).

- **Feeder: a full menu with no agent installed.** `crab eat --deterministic` chains sniff,
  catch, all fifteen miners and compare, exports the meal plus both digests, and never writes
  the ledger or creates provider issues. The composite action and reusable workflow upload
  the bundle, expose artifact outputs, use read-only tokens directly and default to a shallow
  90-day history window. API reads support conditional requests and bounded retries. A separate
  weekly smoke workflow checks Linux, Windows and the reusable interface. See
  [the Feeder guide](docs/feeder.md).
- **Independent wiki snapshots and real strict mode** ([#73](https://github.com/drevendev/HungryCrab/issues/73),
  [#74](https://github.com/drevendev/HungryCrab/issues/74)). Wikis contribute names, headings and
  counts without body text, their own commit invalidates digest reuse, and documentation traits
  recognise them. Strict maws downgrade COPY code to clean-room REIMPLEMENT while configurations
  and templates stay copyable, with the policy reason in menu and issue traces.
- **Scheduled Crab:** `crab loop` persists nine phases, daily budgets, exclusive expiring
  leases, bounded retries, pause/resume, human acknowledgements and phase cost history.
  Control repositories keep one state per maw without granting work authorization.
  GROW retains licensed nutrient publication; MOLT/HARDEN use scanned immutable trees,
  protected paths and deterministic PR reconciliation. Merges remain human; release tags
  need the verified merged release head and passing CI. `/crab:loop` runs one phase per wake.
  See [the operator guide](docs/scheduled-crab.md); live rollout evidence is in
  [#242](https://github.com/drevendev/HungryCrab/issues/242).

- **Ruby/Bundler dependencies without executing prey Ruby.** Literal `Gemfile` and
  `.gemspec` declarations and direct `Gemfile.lock` resolutions contribute
  static dependency evidence; dynamic Ruby remains unknown with explicit warnings
  ([#148](https://github.com/drevendev/HungryCrab/pull/148)).

- **Digest coverage is two numbers, and one of them is a gate.** `files_counted / files` could
  not tell a sample corpus the crab skipped on purpose from prey it failed to read, and it
  moved when somebody ran `npm ci`. The inventory now records a `coverage` block, lifted into
  `manifest.json`: the *analysis share* (`files_counted / files_seen`, informational) with every
  excluded file reported beside it by reason — vendored, corpus, examples, ignored, build
  output, generated, binary, LFS — and *visibility*, the explicit losses of the walk: a file
  list truncated at the cap, paths that failed to stat, directories that could not be listed,
  files that stat but cannot be read, plus the symlinks skipped by policy and files behind a
  capped vendored directory, which are reported and are not a loss. `healthy` is visibility
  alone. `crab digest` prints the line, `inventory.md` carries it, and
  `crab digest --fail-on-loss` exits non-zero on an unhealthy digest for CI, where an absent
  fact would otherwise read as an absent trait — and on a digest with no coverage record at
  all, which a cached digest from before this change is not reused as
  ([#76](https://github.com/drevendev/HungryCrab/issues/76)).
- **COPY nutrients become pull requests, with the notice they owe.** A `COPY` or `COPY_FILE`
  card is served with `--as pr-branch` and a *materialization receipt*: the trusted caller's
  statement, after it carried the files into the maw, of which maw path came from which prey
  path, verbatim or adapted. The crab checks the receipt against the meal's prey — label, URL,
  commit and licence — and against the prey's own history through read-only git plumbing in
  the cache (a verbatim file byte for byte; under COPY_FILE every file), appends it to
  `.crab/attributions.json` under an identity of nutrient and commit (a rerun adds nothing, the
  same nutrient from another commit is a second receipt that never rewrites the first), renders
  the notice file named by `attribution_file` from all receipts with a structured obligation —
  Apache's NOTICE file is not a copyright line, and one's own code owes none — and carries files,
  receipts and notice in one pull request. `crab attribution` rewrites the notice file from the
  receipts; `--check` makes it a CI gate. The notice names only what the crab carried in: a COPY
  nutrient filed as an issue took nothing and appears nowhere
  ([#70](https://github.com/drevendev/HungryCrab/issues/70),
  [#22](https://github.com/drevendev/HungryCrab/issues/22)).
- **`crab serve --as pr-branch`: a REIMPLEMENT nutrient becomes a pull request, through a
  transaction that scans before it touches anything.** The clean-room implementer returns a
  strict JSON receipt naming the exact maw paths it changed; the crab freezes those bytes,
  scans every file plus the title and body for secrets, reconciles marker-bearing pull
  requests already on GitHub, and only then stages the frozen bytes in a detached temporary
  worktree, pushes the deterministic `crab/<nutrient>-<hash>` branch and opens the pull
  request with the same `<!-- crab:<id> -->` marker issues carry. `serve.prs` (`ask` requires
  explicit `--ids`, `auto` allows `--top`) and `serve.max_prs_per_run` in `.crab.yml` govern
  it, and a rerun after a crash reconciles instead of duplicating
  ([#69](https://github.com/drevendev/HungryCrab/issues/69),
  [#77](https://github.com/drevendev/HungryCrab/issues/77), pull requests #101–#112).
- **The clean-room protocol, and the hooks that make "never execute prey" mechanical.** The
  `cleanroom` skill writes a code-free specification from the prey evidence; the
  `crab-cleanroom-impl` subagent implements from it in a fresh context and returns the
  receipt. Two `PreToolUse` hooks ship with the plugin: `crab-cleanroom-guard` refuses that
  agent every tool call that names the cache, and `crab-prey-guard` refuses any Bash command
  that touches the cache unless it is one of a few audited read-only shapes, judging the
  executable's provenance rather than its name
  ([#71](https://github.com/drevendev/HungryCrab/issues/71),
  [#72](https://github.com/drevendev/HungryCrab/issues/72), #105, #119).
- **Documents are paged, and the whole digest has a budget policy.** A Markdown file that
  does not fit its per-file budget is split into `history.md`, `history.2.md`, … each naming
  the next, and nothing is dropped. The 30,000-token total is a policy: `warn` (default)
  records how far over the digest went, `enforce` drops whole pages by priority and records
  which, `off` has no ceiling — set as `budget.policy` in `.crab.yml` and read from an
  explicit `--maw` ([#75](https://github.com/drevendev/HungryCrab/issues/75), #120–#122,
  [#129](https://github.com/drevendev/HungryCrab/issues/129)).
- **A digest says what it did not read, and `compare` refuses partial evidence.**
  `crab digest --fail-on-miner-error` exits non-zero when a miner failed, so the CI smoke
  test can fail; a miner blocked by a failed dependency is recorded as `blocked` rather than
  failed; the working tree is part of the cache key, so an edited checkout is never served
  the digest of its last commit; and `crab compare` refuses a digest with a failed miner or a
  damaged artifact unless `--allow-partial` says otherwise
  ([#61](https://github.com/drevendev/HungryCrab/issues/61),
  [#65](https://github.com/drevendev/HungryCrab/issues/65),
  [#67](https://github.com/drevendev/HungryCrab/issues/67), #80, #113, #114).
- **Codex now gets native Hungry Crab branding without borrowing Claude's manifest as its identity.**
  A portable Agent Plugins v1 `plugin.json` carries the repository, the version and, under
  `extensions.com.openai`, the red crab presentation and website, and
  `.agents/plugins/marketplace.json` installs the repository root without duplicating the
  existing skills. The temporary OpenMoji crab keeps
  its adjacent CC BY-SA 4.0 attribution. `crab update` now reads Claude and Codex manifest versions
  separately, so one drifting manifest cannot make the other agent look current
  ([#99](https://github.com/drevendev/HungryCrab/issues/99)).
- **A declared nutrient category is produced by something or deferred by name.** `code` sat in
  `CATEGORIES`, in `scoring.yml` and in every generated `.crab.yml` as a hunger knob while no
  candidate builder could emit it, so the setting could not affect a single meal — and the
  decision to leave it to 0.4 lived in a private backlog, where a deferred category looks
  exactly like a forgotten one. `DEFERRED_CATEGORIES` in `nutrients.py` names the milestone
  that owes each producer, `tests/test_categories.py` refuses a category that is neither
  produced nor listed, and the roadmap, the generated config and the category reference say
  that `code` is inert until 0.4 ([#52](https://github.com/drevendev/HungryCrab/issues/52)).
- **Issue forms, a pull request template and `CODEOWNERS`.** The bug form asks for the three
  things a report never includes — the crab version, the exact command and `manifest.json`;
  the pull request template carries the summary, the test plan and the changelog reminder every
  merged pull request has had so far; `CODEOWNERS` names the files an autonomous phase must not
  change on its own. Making the owner's review mandatory is a branch-protection switch, not a
  file ([#15](https://github.com/drevendev/HungryCrab/issues/15),
  [#20](https://github.com/drevendev/HungryCrab/issues/20),
  [#26](https://github.com/drevendev/HungryCrab/issues/26)).

- **B1, the menu benchmark, and with it milestone 0.2.2.** `benchmarks/menu_benchmark.py` asks
  one question with no model in the room: with today's rules and weights, would the menu still
  put the nutrients a human accepted in the top 30, and would it still show the ones they
  rejected? The golden set is not invented — it is the maintainer's own verdicts on `pypa/pipx`,
  `anthropics/skills` and `github-linguist/linguist`, lifted from the ledger with the rejection
  reasons kept, against frozen digests of those three prey and of this repository at `v0.2.0`.
  Measured at introduction: `recall_must@30` = 1.00 (10/10), `noise@30` = 0.66 (19/29). Both
  gate pull requests through `tests/test_menu_benchmark.py`, on every platform in the matrix.

- **A license governs strangers, and the maw's owner is not one.** `.crab.yml` grows a `trust`
  block: `same_owner` (on by default) treats a prey owned by the account behind the maw's
  `origin` as the maw's own code, `owners` extends that to named accounts, and `bypass_license`
  is an explicit escape hatch. The verdict matrix takes a `Relationship` alongside the two
  licenses; `own` yields `COPY`, and still asks for review when the prey carries a copyleft or
  source-available license, because owning a repository lets its owner relicense what they wrote
  and not what they received. Before this, a maintainer eating their own unlicensed repository
  got `IDEAS_ONLY` — a correct answer to a question nobody had asked.

- [`docs/design/08-budgets-and-feeder.md`](docs/design/08-budgets-and-feeder.md) and a revised
  roadmap. Two policies that decide who the crab is for. **Budgets**: the 30 000-token ceiling was
  a boolean flag pretending to be a limit, and the per-file budget bought its 3 500 tokens by
  dropping the tail of a section. It becomes a policy — `warn` for an agent session, `enforce` for
  a budgeted loop, `off` for a human — and documents that do not fit are paged rather than
  truncated. **The Feeder**: the deterministic pipeline becomes a reusable GitHub workflow, so a
  repository can name its prey, run `catch → digest → compare` in CI with no model anywhere, and
  collect the meal as a build artifact. It moves from milestone 0.6 to 0.3.1, because the
  Evolving Crab's CONSUME phase is that job and building it later means building it twice.
  Being able to digest any repository is now a stated goal of track A rather than an implication
  of it.

- `crab update`: one command that checks the CLI and every agent plugin against master, notices
  which agents are on this machine and whether the plugin is installed in each, and prints what
  to run. `--run` performs the plugin work; the CLI reinstall is only executed when the running
  process is not the uv tool install that would be replaced, because uv cannot replace the crab
  while it is running.
- Two nutrients the second live meal showed were missing. `hygiene.notice-file`: the prey keeps a
  NOTICE file for third-party attribution and the maw does not, which matters for a tool whose
  own verdicts say `notice_required`. `ai-config.skills-corpus`: the prey ships far more agent
  skills than the maw, so its corpus is worth reading even though the maw has skills of its
  own. Every other `ai-config` rule asks a yes/no question, so eating the official skills
  repository used to produce no `ai-config` candidate at all.
- The crab can file issues into a repository it does not own, and under its own name.
  `serve.token_env` in a maw's `.crab.yml` names an environment variable holding the token to
  serve with, so a GitHub App installation token gives the issues a bot's identity instead of a
  maintainer's; `crab serve --as issue` opens by saying which identity it is using. Creating a
  label needs write access where opening an issue does not, so a label the crab cannot create is
  now reported once and the issues are filed without it — deduplication reads the `crab:<id>`
  marker in the body, never the label.

### Changed

- **Canonical digest cache publication is generation-based.** Published digests use
  `digests/.generations/<sha>/<generation>/` with `digests/.refs/<sha>.json`
  for the selected generation, while dirty/shallow/selective requests use
  `digests/.scratch/`. This replaces the old direct `digests/<sha>/` publication
  assumption and matters to cache inspection/recovery (#150–#157).

- **A digest describes one repository; a meal describes a pair.** `crab compare` used to write
  `menu.md`, `gap.md` and `compare.json` into the prey's digest, which every maw that eats that
  prey shares — so a second maw silently overwrote the first one's menu, and the licence verdict,
  which depends on the maw's own licence, was recorded as if it were a fact about the prey. The
  comparison now lands in `maws/<maw>/meals/<prey>@<sha>/` as `menu.md`, `menu.json`, `gap.md`
  and `meal.json`. `crab menu` takes `--maw`, because a menu belongs to one.
- `applicability` is `uptake`, the term nutrition already uses for the fraction of a nutrient
  that is actually absorbed.
- `menu.md` calls its `serve_as` column `Serve as`, as the glossary says, not `Artifact`.
- `docs/design/GLOSSARY.md`: every term in one place, with the words that were replaced and why.
  The vocabulary lived in seven documents, which is how "host" survived as long as it did.

- **The repository the crab feeds is the maw, not the host.** In parasitology a host is what
  gets eaten, which is the opposite of what this word had to mean here, and `--host` read like a
  network address besides. Prey feeds the maw. `--host` is now `--maw`, `--host-license` is
  `--maw-license`, `HostConfig` is `MawConfig`, `src/hungry_crab/host.py` is `maw.py`, and the
  cache keeps local digests under `maws/` instead of `hosts/`. Old `--host` is gone rather than
  deprecated: nothing is released yet, and two vocabularies cost more than one rename.
- **`appetite` in `.crab.yml` is now `hunger`**, with the same values. A file that still says
  `appetite` is a usage error rather than a silent default, because a maw quietly eating what it
  had switched off is worse than a failed command.
- The scheduled crab's first phase is `CRAVE`, not `HUNGER`, now that hunger is the name of a
  configuration block ([`docs/design/07-scheduled-crab.md`](docs/design/07-scheduled-crab.md)).
- README rewritten: what the crab is for, the vocabulary, install instructions for both Claude
  Code and Codex (both consume the same plugin marketplace), and the measured benchmark numbers.
  Installs track `master`; a release tag is opt-in.

### Fixed

- **Review repairs before integration (2026-10-22).** The default wiki probe no longer fails a
  catch when GitHub answers a missing wiki with a login prompt; it records the wiki as
  `unavailable`. A relative `--since`, an older cache without a clone policy or a catch that
  failed after cloning refresh the clone instead of replacing it, and a digest that asks for
  issues on a cached prey keeps its commit. `crab loop serve` no longer counts its own pick as
  the owner's selection, so it respects `serve.prs: ask`, ideas-only hunger and human-review
  verdicts. Section headings pass through `inline()`, Maven property expansion is capped and
  the Gradle patterns are linear. The default scoring carries no trait weights tuned to the B1
  golden keys, so the ranking pressure benchmark is a report, not a gate. The Feeder refuses to
  upload a meal from a public repository unless the prey is confirmed public or
  `allow-private-artifact` is set.
- Refuse case-variant caller files during selective digest stale cleanup before replacing
  any artifact; keep caller bytes and the previous manifest intact on Windows and Linux.

- **Integration safety:** multi-prey source routing preserves the owner's original selection,
  so `--top` cannot bypass `serve.prs: ask` or an ideas-only hunger ceiling. Scheduled landings
  and release tagging require CI bound to the provider-confirmed merge commit, including squash
  merges; absent or incomplete evidence keeps the loop waiting without spending a new phase.
  Explicit-output reruns require exact agreement between artifact and producer ownership tables,
  protecting caller files from orphan manifest entries (#285, #286, #287).
- Preserve current-master API response bounds, strict trust validation and the toolchain while
  integrating Feeder, Scheduled Crab, Deep Bite and Taste Memory. Retain staged clone rollback
  and forge-qualified target diagnostics alongside the repaired installation/source archive docs.

- **Unknown SPDX `WITH` exceptions fail closed.** A missing exception rule
  cannot silently inherit a concrete GPL copying verdict; such combinations
  now require explicit human review
  ([#149](https://github.com/drevendev/HungryCrab/pull/149)).

- **Claude Code sees a new release of the plugin as an update.** Claude Code offers a plugin
  update only when the version string it computes changes, and the plugin carried the same
  version in `.claude-plugin/plugin.json` and in its marketplace entry. The entry no longer has
  one: the version lives in `plugin.json` alone and moves at each release, which is when the
  **Update** button lights up. The README says how to turn on auto-update for the marketplace,
  which Claude Code leaves off for third-party marketplaces, and a release now bumps five
  version places instead of six.
- **Cache deletion stays inside the cache and works under a linked cache root.** `crab cache rm`,
  `crab catch --force` and the replacement of a clone without `.git` made every entry of the
  tree writable before deleting it, and `chmod` followed a symlink to its target outside the
  cache. Nothing is made writable up front now: an entry is retried only after its deletion
  fails, and only when every component from the cache root down to it is a real directory or
  file, never a symlink or a Windows junction. The cache root and its parents are left alone,
  so `~/.cache` on another disk, `CRAB_CACHE_DIR` in a junctioned folder and
  `/home -> /var/home` keep working. A tree that cannot be removed is a reported error with a
  hint instead of a traceback, and digest references refuse junctions as well as symlinks.
  Checking that the cache owns what it deletes is still open
  ([#211](https://github.com/drevendev/HungryCrab/issues/211)).
- **GitHub REST responses have a byte budget before JSON decoding.** Both `gh api` and
  HTTPS refuse a response larger than 8 MiB instead of buffering it without a bound. The
  `gh` transport drains both pipes concurrently, caps stderr at 64 KiB, and kills and reaps
  its direct child on overflow or deadline; no unbounded temporary output file is used.
  Empty responses, bounded diagnostics and optional 404 results keep their existing meaning
  ([#267](https://github.com/drevendev/HungryCrab/issues/267)).
- **Feeder and Scheduled Crab share the meal contract.** EAT resolves portable digest paths
  relative to the exported `meal.json`, independent of the scheduler's working directory, and
  refuses damaged artifacts or wiki visibility loss before advancing. Cached acquisition keeps
  token authentication, live remote-default detection and bounded-history fallback together.
  CI now refuses visibility loss in its self-digest as well as miner failures.
- **COPY serving reports the inputs it leaves in the working tree.** Every published or
  reconciled COPY pull request reports its input paths and nutrient branch, in text and JSON.
  The text output explains how to preserve local edits in a stash before cleaning the checkout;
  the serve skill starts carrying in a separate worktree and keeps configuration and ledger
  commits on the default branch ([#233](https://github.com/drevendev/HungryCrab/issues/233)).
- **A shallow request never reuses a full-history digest at an explicit output path.** Its
  manifest and history describe the current clone, even when HEAD matches a previous full clone.
  Shallow history can grow without changing HEAD, so repeated shallow requests also rerun the
  miners ([#234](https://github.com/drevendev/HungryCrab/pull/234)).
- **Explicit digest output preserves caller-owned files.** `crab digest --out` builds in a
  sibling staging directory and checks every destination name before publishing. Only artifacts
  listed in a previous valid digest manifest can be replaced or removed; unrelated documents,
  year-suffixed notes and a caller's `manifest.json` survive. Case-insensitive collisions are
  refused, and malformed ownership records grant no permission to overwrite files
  ([#210](https://github.com/drevendev/HungryCrab/issues/210)).
- **A cached catch follows the remote's current default branch.** Refresh probes remote HEAD
  before mutation, retargets a single-branch fetch when necessary, checks out the new tree and
  updates `origin/HEAD`. Full and shallow caches can switch even after the old branch is deleted;
  a failed probe preserves the last cached tree and catch record with a retry hint
  ([#238](https://github.com/drevendev/HungryCrab/issues/238)).
- Cached catches honour changed history options and validate dates before mutation (#230).
  Replacements are staged so a failed clone preserves the previous valid cache. README and
  agent-file outlines share a fence-aware heading parser (#193).

- **A COPY receipt answers to the licence of each file it takes, and a pull request never drops
  a receipt.** A receipt could take any path of the prey and record it under the repository's
  licence: vendored GPL code, an Apache subtree with its own LICENSE and NOTICE, a file whose
  header names another licence — under the same-owner verdict even as "no notice owed". Such a
  file is refused now, and so is a taken path that is not a regular file at that commit: a
  directory, a link or a submodule passed as the source of an adapted file. A checkout behind
  the default branch built a branch whose
  `.crab/attributions.json` lacked receipts merged since, which merged cleanly and left a
  merged file attributed nowhere while `crab attribution --check` stayed green; the effect now
  reads the default branch's receipts after the fetch and refuses that branch before any push.
  `attribution_file` has to be a plain relative path in the maw, never under `.git`, never the
  receipts file and never a file the crab did not write (`README.md` would have been replaced by
  the notice). A COPY card whose verdict asks for human review is published only when named
  with `--ids`, and the crab's attribution section is always appended to the body
  ([#185](https://github.com/drevendev/HungryCrab/issues/185)).
- **The notice carries the licence it owes, not a sentence about it.** A COPY notice named the
  obligation — "keep the source's copyright and permission notice" — and reproduced none of it,
  while MIT, BSD, ISC and Boost ask for that notice itself to go with the copy and Apache for
  the licence and the NOTICE file's attributions. `crab serve --as pr-branch` now reads the
  source's licence and NOTICE files at the commit the material came from, keeps them with the
  receipt in `.crab/attributions.json`, and the notice reproduces them at the end of each source
  section in fenced blocks, which a formatter leaves alone. A licence that asks for its notice
  to travel, from a prey with no licence file at its root, is refused. A subdirectory prey's
  paths — the files taken, and its licence — are read from its own directory rather than from
  the top of the repository around it
  ([#183](https://github.com/drevendev/HungryCrab/issues/183)).
- **The notice file survives the maw's formatter.** The notice was a table, and every Markdown
  formatter realigns a table: the first COPY pull request into a maw whose gate is
  `prettier --check .` would have turned that gate red on merge, while `crab attribution --check`
  compares bytes, so the two gates could not both be green. The notice is headings and
  paragraphs now, wrapped at 80 columns, with no table, no list, no hard line break and no
  trailing whitespace — a fixed point of Prettier's defaults, of `--tab-width 4` and of
  `--prose-wrap always` — and its layout is held byte for byte by a test, because changing it
  makes every maw's notice stale. Names are data: a receipt path with a control character is
  refused, and whatever else a path holds stays inside a code span, in the notice and in the
  pull-request body. The body also says what no check has seen: the receipts and the notice are
  written at publication, after the caller's checks ran, and the `serve` skill sends the maw's
  own gate to the branch before anyone merges
  ([#173](https://github.com/drevendev/HungryCrab/issues/173)).
- **A nutrient with no declared origin fails closed, and notes cannot smuggle commenter prose
  past the cap.** `Candidate.origin` defaulted to `licensed`, so a builder that forgot to say
  where its text came from inherited the repository's COPY — and the next builders on the
  roadmap read discussions and review comments, exactly the prose the origin ceiling exists
  for. The default is `unknown` now, capped at HUMAN; all twenty builders declare `licensed` or
  `commenters` explicitly, and a test walks a whole menu to see that none forgot. Reassigning
  `origin` re-caps the mode the card carries and refreshes its trace, never widening it. And
  `crab serve` refuses a card of any origin but `licensed` whose `why` or `how` quotes an issue
  title from the prey's own `issues.json`: the one channel the engine never policed, while a
  comment claimed it did. A quote is its words — case, width, punctuation, escapes and
  invisible characters are not compared, and eight consecutive words of a longer title are a
  quote — and notes that cannot be checked, because the prey digest is gone, are not served
  ([#137](https://github.com/drevendev/HungryCrab/issues/137)).
- **The clean-room specification has a path that exists on Windows, and the pull request
  carries it.** The skill told the caller to write the Stage A specification to
  `.crab/specs/<nutrient-id>.md`; a nutrient id has colons, NTFS refuses them in a file name, so
  Stage A could not complete on Windows, and a Linux maw that committed the file could not be
  checked out there. The path is the CLI's now, derived like the branch name —
  `.crab/specs/<readable>-<8 hex>.md`, printed by the new `crab spec <nutrient-id>` — and the
  promise that the pull request links the specification is kept by the transaction rather than
  by a sentence in a skill: `crab serve --as pr-branch` reads the specification under the maw,
  scans it with the rest of the payload, carries it in the pull request and links it from the
  body, and refuses to publish an implementation without one
  ([#131](https://github.com/drevendev/HungryCrab/issues/131)).
- **`crab serve` honours `serve_as`.** `ideas-only` in the `hunger` block produced cards marked
  `idea`, and nothing downstream read the mark: `--as issue` filed them, `--top` filed every one
  in the top N, and pull-request mode published a REIMPLEMENT card whatever its mark. An `idea`
  card is now skipped with the reason `serve_as: idea` unless its id was asked for by name; in
  pull-request mode the selection is filtered before anything is prepared — a licence mode
  with a pull-request path, marked `pr`, with a receipt — and everything else is reported as
  skipped with its reason, so `--top` serves what it can instead of raising on the first card
  it cannot. The mark is the maw's, not the model's: `serve` reads the `hunger` block again, so
  a category switched off or narrowed after compare holds from the next serve on, and a note
  may narrow `serve_as` but never widen it — a `serve_as`, `effort` or `risk` outside its
  vocabulary is ignored with a warning
  ([#136](https://github.com/drevendev/HungryCrab/issues/136)).
- **ripgrep cannot start a program through the prey guard.** The guard's read-only surface
  refused `rg --pre` and nothing else, but `--hostname-bin=<program>` runs a program to name
  the host in a hyperlink and `-z`/`--search-zip` starts a decompressor for every compressed
  file the prey ships. A cache-touching `rg` now refuses all three, spelled out or inside a
  cluster of short flags; `--no-search-zip` and `--pre-glob` stay allowed. A test also holds
  the clean-room hook's matcher to the implementer's tool list, since the guard runs only
  where the matcher sends it
  ([#176](https://github.com/drevendev/HungryCrab/issues/176)).
- **Unsupported LGPL versions require human review.** Identifiers such as `LGPL-2.2-only` and
  `LGPL-4.0-only` no longer inherit COPY or clean-room permissions from the family prefix. They
  classify as unknown and receive `HUMAN`, including when the unknown licence belongs to the
  maw. Supported versions and deprecated spellings retain their rules
  ([#93](https://github.com/drevendev/HungryCrab/issues/93)); other families remain tracked by #95.

- **The guards run from the plugin, not from `PATH`.** `hooks/hooks.json` named the console
  scripts `crab-prey-guard` and `crab-cleanroom-guard`, so the hooks existed only where the CLI
  was installed with `uv tool install`, and on a Windows machine whose application control
  refuses unsigned launchers they existed and did nothing: `crab-cleanroom-guard.exe` exited
  126, which Claude Code reads as "proceed". Each hook now runs `hooks/guard.py` from
  `${CLAUDE_PLUGIN_ROOT}` with the first Python the shell finds (`python` first on Windows,
  `python3` elsewhere), and the launcher imports the guard from the plugin's own `src/` —
  nothing installed, nothing signed, and always the guard version that shipped with the plugin.
  Its exit codes are the contract: `2` is a guard's refusal and nothing else; `1` with a reason
  is "could not guard" — no Python 3.11+, an unreadable source tree — which Claude Code shows
  and proceeds on, because a hook that blocked every tool call over its own environment would
  be switched off rather than fixed. An interpreter older than 3.11 hands over to a newer one
  found on `PATH` or through `uv python find`. The console scripts stay for manual use
  ([#141](https://github.com/drevendev/HungryCrab/issues/141)).
- **A local prey's `.git/config` cannot make the crab run a program.** A local directory is
  prey, and its git configuration came with it: `core.fsmonitor` ran on every `diff HEAD` and
  `ls-files` of the worktree fingerprint, a filter driver the prey's `.gitattributes` selected
  ran when git hashed a copied or dirty tree, `diff.external` and textconv drivers ran on a
  dirty tree, and `log.showSignature` ran `gpg.program`. Every git command the crab runs now
  switches the monitor and signature checks off, passes `--no-ext-diff --no-textconv` to
  `diff`, `log` and `show`, and empties every filter driver the repository configures
  ([#178](https://github.com/drevendev/HungryCrab/issues/178)).
- **Codex can install the plugin again.** The Codex presentation shipped as a
  `.codex-plugin/plugin.json` overlay with no `name` and no `version`. When that file exists,
  Codex takes the plugin's identity from it rather than from the root `plugin.json`: a missing
  name falls back to the directory the marketplace snapshot was checked out into, `hungry-crab`,
  and a missing version to `local`, so `codex plugin add crab@hungry-crab` and every
  `codex plugin marketplace upgrade hungry-crab` since #118 stopped with "plugin.json name
  `hungry-crab` does not match marketplace plugin name `crab`". The overlay is gone: the
  presentation lives under `extensions.com.openai` in the portable `plugin.json`, which Codex's
  documentation names as the overlay's replacement, the name and version stay in the one file
  `crab update` already reads, and a test refuses the overlay's return. Hooks are a separate
  matter — a plugin with a root manifest goes through Codex's Agent Plugins loader, which has no
  hook support (openai/codex#39895), so the two guards stay Claude Code's until Codex wires them
  ([#141](https://github.com/drevendev/HungryCrab/issues/141)).
- **A prey's names stay on the line they are printed on.** The deps miner took any manifest
  key for a package name, and the menu, `gap.md` and a served issue wrote card titles, `what`,
  `maw_state` and evidence paths raw into list items — so a dependency key holding line breaks
  opened a `## SYSTEM NOTICE` heading with an instruction in the file an agent reads to decide
  what to serve, and in the issue it files. A key that is not a package name is dropped with a
  warning, and every prey-derived value rendered into those files is one line with no control
  characters ([#180](https://github.com/drevendev/HungryCrab/issues/180)).
- **A selective run is not a complete digest, and enforcement drops pages from the tail.**
  `crab digest <prey> --miners license` writes into the same `digests/<sha>` entry as a full
  run and cleans the other miners' files out of it; the next `crab compare` then reused that
  entry as complete evidence — no failed miner, no integrity error, an empty stack and a menu
  with nothing on it until someone passed `--force`. Reuse now requires a record for every
  registered miner. In the same area: under `enforce`, pages of equal priority were dropped by
  file name, and `docs.md` sorts after `docs.3.md`, so the page a reader opens first went
  before its continuations; the last page of a family goes first now. And a `--md-budget` too
  small for a page header — `0`, or `20` — raised a `ValueError` after the miners had run,
  with JSON files written and no manifest; it is a usage error with a hint before anything
  runs, or a named miner's error if a header still does not fit.
- **Two digests of one commit list the same import-graph hubs.** Hubs, orchestrators, directory
  edges and external imports were the `most_common` of counters filled from a set of paths, so
  ties followed the interpreter's hash seed: six seeds gave this repository four hub orders, and
  the architecture card's `what` and evidence changed from run to run. Ties are broken by path
  now ([#197](https://github.com/drevendev/HungryCrab/issues/197)).
- **The prey guard reads a line break as the command separator it is.** `shlex` reads a
  newline as whitespace, so `cat <cache>/README.md` on one line and `python <cache>/setup.py`
  on the next were judged as one long `cat` and allowed; a cache-touching command with a line
  break is now refused like one with `;`. In the same pass: `git log --output=<file>` was an
  allowed write primitive that lands prey bytes wherever a later command runs them, and
  `git grep -O` hands the matches to a program of the caller's choosing — both refused; a
  sibling directory that merely starts with the cache's name (`hungry-crab-other`,
  `crab-prey.old`) is no longer read as the cache; the read-only verbs the `crab-historian`
  agent is told to run in a clone (`show`, `diff`, `blame`, `shortlog`, `describe`,
  `show-ref`, `grep`) are allowed under the same dangerous-argument filter; and both hook
  entry points take undecodable input down the documented transport-failure path instead of
  dying with a traceback, which exited 1 and was read as "allow" too, only louder.
- **Three ways `crab serve` could do the wrong thing with a straight face.** On Windows the
  clean-room receipt on stdin was decoded with the console code page, so a summary with an em
  dash or an accented word became mojibake with lone surrogates that passed the scan and the
  reconciliation, pushed the branch, and only then crashed on the encode before
  `gh pr create` — every rerun the same, so the nutrient was unpublishable; the stream is read
  as bytes and decoded as UTF-8 once. `--maw <subdirectory>` read the receipt's files under the
  subdirectory and staged them relative to the repository root, a plausible pull request that
  changed the wrong file; pr-branch mode now requires the maw to be the repository root and
  says which path to pass. And when the listing of existing issues failed, `--as issue` logged
  a warning and filed everything again, which on a repository without a ledger is exactly the
  duplicate the marker exists to prevent; an effectful serve now refuses, while a dry run still
  warns and goes on.
- **`crab digest --out` deleted files it had not written.** Rerun cleanup treated every file
  in the output directory as a stale artifact unless the current run had just produced it, so
  a directory the caller already used lost its own files, and `--out .` emptied the top level
  of the repository being digested. A digest now owns exactly what a registered miner
  declares — its JSON file and its Markdown page family — and cleanup, the aggregate budget
  and `manifest.json` all stop at that boundary: a stranger's `notes.md` is neither deleted,
  nor budgeted, nor listed as evidence
  ([#126](https://github.com/drevendev/HungryCrab/issues/126)).
- **A local prey's `.crab.yml` no longer decides what the crab reads.** `crab digest <path>`
  loaded the target's own `.crab.yml` for `ignore` whenever the caller passed none, so a
  directory being eaten could hide any part of its tree from every miner, and a broken
  maw-only setting in that file (`mode`, `hunger`, `budget.policy`, a YAML error) aborted the
  digest before a miner ran — through `crab compare`, `crab menu` and `crab serve` on a local
  prey as well. Configuration now comes from an explicit `--maw` only, and its `ignore` list
  applies when the target is that maw: `crab digest . --maw .` is how the crab eats itself
  (the README, `AGENTS.md` and the CI smoke test say so), `crab compare` is unchanged because
  it always knew which side was the maw, and a foreign local directory keeps its whole tree
  ([#128](https://github.com/drevendev/HungryCrab/issues/128)).
- **Prey-cache shell guards now fail closed on executable provenance, not just command names.**
  A cache-touching command is denied when its executable path points into prey even if the file
  is named like an allowed reader, and leading environment assignments are rejected so `PATH`
  or loader variables cannot turn an allowed `cat`/`rg`/`git` shape into prey execution. Ordinary
  read-only cache inspection stays allowed ([#85](https://github.com/drevendev/HungryCrab/issues/85)).
- **Served issues now show the content-origin licence ceiling that produced their mode.**
  `crab serve` renders the normalized content origin, final licence mode and deterministic cap
  reason when one applies, so the same trace recorded in `menu.json` remains visible at the
  publication boundary ([#58](https://github.com/drevendev/HungryCrab/issues/58)).
- **Issue-derived nutrients no longer inherit permissive licence modes from commenter-controlled prose.**
  `issue-lesson` cards are treated as commenter-origin and capped at `IDEAS_ONLY`; unknown or
  future content origins fail closed to `HUMAN`, while the structured trace records the
  normalized origin and the reason for the cap ([#84](https://github.com/drevendev/HungryCrab/issues/84)).
- **A healthy digest no longer trusts producer metadata after its artifact disappears or changes.**
  `crab digest` now invalidates and repairs a cached digest when a successful miner's declared
  artifact is missing, corrupt, mis-owned, the wrong size, or not a JSON object where JSON is
  expected; `crab compare` refuses that inconsistent evidence by default unless the caller
  explicitly opts into `--allow-partial` ([#87](https://github.com/drevendev/HungryCrab/issues/87)).
- **Ignore globs matched by case on Windows and not on Linux.** `is_ignored` ran the path and
  the pattern through `fnmatch.fnmatch`, which lowercases both on Windows and neither on Linux,
  so one `.crab.yml` produced two different digests of one commit — file counts, languages,
  ecosystems and the menu — and each was consistent with itself, so nothing warned. The match
  is `fnmatchcase` on every platform now, the generated config says so, and `Tests/Fixtures`
  against `tests/fixtures/**` is a regression case on both legs of CI
  ([#89](https://github.com/drevendev/HungryCrab/issues/89)).
- **`crab update` called an install with no recorded commit up to date.** The commit comparison
  only ran when PEP 610 provenance was there; without it the CLI came back `OK`, and `OK` is
  not actionable, so the line "reinstall to be sure" was followed by "Nothing to do." A crab at
  master's version string with no commit to compare is `unknown` now: the reinstall command is
  printed for a running uv tool, and executed under `--run` where the crab is not the process
  being replaced ([#97](https://github.com/drevendev/HungryCrab/issues/97)).
- **The hidden-character check flagged every emoji sequence and every Persian word.** U+200D
  glues profession and family emoji together and U+200C is ordinary orthography in Persian and
  the Indic scripts, and both were flagged on sight, so a README heading with 👩‍💻 in it became
  `[line omitted: instruction-like content]`. A joiner is now judged by its neighbours: it is
  legitimate next to a character that is not ASCII and not itself invisible, and suspicious
  wedged into a Latin word, dangling at the end of a heading, or stacked with other invisible
  characters. The zero-width space, the word joiner, the BOM and the Tags block are flagged as
  before ([#66](https://github.com/drevendev/HungryCrab/issues/66)).
- **A repository that measured coverage in CI read as one that did not.** `coverage.configured`
  came from a threshold in a file, a coverage package in a manifest or a config file at the
  root — and a Go or Rust project declares coverage in none of those: the flag is on the `go
  test` line and the upload is an action, both of which the CI miner had already written down.
  The testing miner now requires `ci`; a coverage upload action names the service, a coverage
  flag on a test step counts as configured, and `coverage.in_ci` says which workflow said so.
  The threshold stays a fact declared in a file
  ([#53](https://github.com/drevendev/HungryCrab/issues/53)). The example-tree leak reported
  in [#54](https://github.com/drevendev/HungryCrab/issues/54) turned out not to exist — the
  exclusion reaches the test frameworks through `FileInfo.counted` — and is pinned by a
  regression case instead.
- **The ledger forgot which prey proposed a nutrient, and `crab tune` read that field.**
  Nutrient ids are maw-relative, so every re-proposal overwrote `prey` with the latest prey and
  the one that found the nutrient lost the credit. `prey` and `sha` now name the first
  proposer for the life of the entry; `last_prey`, `last_sha` and `sightings` record the rest.
  A ledger written before this loads unchanged
  ([#60](https://github.com/drevendev/HungryCrab/issues/60)).
- **Issue dedup stopped at 500 issues, and a quote outranked the issue it quoted.** Marker
  discovery asked `gh issue list` for one page of 500, so on a busy repository the crab's own
  issues fell off the end and the next serve filed everything again — on exactly the repository
  the crab is a visitor to, where no ledger catches it. It now pages through every issue with
  `gh api --paginate`, drops pull requests, and does not filter by label, because a repository
  the crab cannot label gets its issues without one. When a marker appears in several issues,
  the one whose body opens with it — the shape `crab serve` writes — wins, then the oldest, in
  whatever order the issues arrive ([#59](https://github.com/drevendev/HungryCrab/issues/59)).

- **Four documents still described a verdict the engine had stopped returning.** Since `HUMAN`
  became reachable, an unrecognised licence is `HUMAN` and a missing one is `IDEAS_ONLY` flagged
  for review — but `README.md`, `docs/design/01-concept-and-skill.md`, the licence engine's own
  docstring and `skills/license/references/matrix.md` all still paired them as `IDEAS_ONLY` +
  `HUMAN`. The last of those is the table the `license` skill hands to a model at the moment it
  decides whether code may be copied, which makes it the one that mattered. The same reference
  now also carries the `own` and `bypass` relationships, which short-circuit the matrix entirely
  and had been documented nowhere an agent reads. `tests/test_docs_match_the_engine.py` guards
  the retired claim by name across every Markdown file in the repository.

- **A cached digest was reused for a question it had not been asked.** The reuse check compared
  the schema, the commit and the depth, and ignored three things the manifest records because
  they change the result: the crab's own version, `ignore`, and the maw's license. The commit
  does not move when `.crab.yml` changes, so step 4 of the `eat` protocol — "the maw reads as the
  wrong stack, add the offending paths to `ignore` and rerun" — returned the cached answer and
  the remedy did nothing at all, leaving the whole meal judged against a repository that does not
  exist. For the same reason an upgraded crab kept serving the previous version's verdicts until
  someone passed `--force`, which is the mistake `crab update` was taught to avoid one layer up.

- **The injection detector cried wolf on ordinary READMEs.** Eating `syrupy-project/syrupy`
  produced four flags, and all four were false: "if you need to bypass a custom object
  representation", "where you need to ignore files by file extension",
  `<!-- prettier-ignore-start -->` and `<!-- markdownlint-restore -->`. A verb like *ignore* or
  *bypass* now has to take an object that is an instruction, a rule, a policy or an agent, and
  HTML comments that open with a known formatter or linter directive are not comments to an
  agent. The five fragments are regression cases in `tests/test_safety.py`; the sentences that
  really are aimed at an agent still flag.

- **`NOASSERTION` meant four different things, which is the same as meaning nothing.** The
  license findings now carry a `resolution`, and each situation gets its own answer.
  `LICENSE-APACHE` next to `LICENSE-MIT` stays a choice (`dual`, SPDX `OR`); license files that
  are not alternatives — `apache-2.0.LICENSE` next to `cc-by-4.0.LICENSE`, or one file that says
  "portions of this software are licensed as follows" — become `split`, where the most
  restrictive of them governs the whole repository until a nutrient names its own path; a license
  file nobody can read becomes `unreadable` and asks a human instead of guessing; and a
  repository that licenses nothing at the root while its packages license themselves becomes
  `per-path`, naming the packages. Two plain bugs fell out of this: license files named after
  their license (`apache-2.0.LICENSE`, and any `LICENSES/` directory) were invisible to the
  detector, and `LICENSE.md` was read as a license *named* `md`.
- An unrecognised license is `HUMAN` rather than `IDEAS_ONLY`. The mode existed, was documented,
  and had never been returned; "we read it and do not understand it" is a different situation
  from "nothing is granted", and only one of them a person can settle in a minute.
- `crab update` compares the **commit** the CLI was installed from, not just its version. Every
  commit of a development series reports the same `0.3.0.dev0`, so version comparison told a crab
  seven commits and one whole rename behind that it was up to date. The installed commit comes
  from `direct_url.json`, which pip and uv both write for a VCS install.
- The plugin manifests carry the CLI's version, and a test keeps them from drifting again. They
  said `0.2.0` while the CLI said `0.3.0.dev0`, so every agent was told its plugin was current
  while its skills still spoke of `--host`.

Everything here was found by the first live meal, the crab eating `pypa/pipx` on its own
repository.

- `ignore` in `.crab.yml` was parsed and read by nothing, so the crab digesting itself reported
  three ecosystems, eslint and twelve test frameworks, all from `tests/fixtures`. Because it
  believed it already measured coverage, the coverage nutrient never appeared.
- A tool of a kind the maw already has is no longer a candidate: `ty` was ranked first on a maw
  running `mypy --strict`. Swapping one type checker for another is a decision, not a nutrient.
- The dependency diff now sees tools a maw configures with a file rather than a pinned
  dependency, and drops the library that merely implements a nutrient already on the menu
  (`pytest-cov` next to "Measure test coverage").
- Issue lessons are capped at three clusters and three popular issues, sorted by size, and titled
  after their largest issue instead of a bare list of TF-IDF terms. They were thirteen of
  twenty-four candidates, all scored the same. (Since the content-origin ceiling above, the
  title is generic again, because an issue title is commenter prose; giving the card back its
  structure without the prose is [#130](https://github.com/drevendev/HungryCrab/issues/130).)
- Maw-dependent licence verdicts are recorded in each maw's meal output
  (`meal.json` / `menu.json`) rather than mutating the shared prey digest's
  `manifest.json`; the latter has no maw-specific verdict.
- An issue for a nutrient the maw lacks entirely no longer reads "What this repository has: no".
- The `eat` skill gained a step: check that the maw column in `gap.md` describes this repository
  before judging anything against it, and guidance to switch a whole category off in `hunger`
  instead of rejecting its cards one by one.
- A test corpus is no longer counted as the repository's own code. `github-linguist/linguist` is
  3390 sample files in four hundred languages against 32 files of Ruby, and the crab read it as
  an Objective-C project with the ecosystems dotnet, go, python and rust, none of them Ruby:
  every manifest it found was a sample, and the menu offered ninety Python dependencies that
  were the contents of `samples/Pip Requirements/filenames/requirements.txt`. It now reads as
  Ruby, 123 files. A repository whose corpus really is its content keeps it.
- A security fix has to read like a fix. "Add support for Cloud Firestore Security Rules" and
  "Whitelist injectionSelector in grammars" gave linguist a security history and put the card at
  the top of the menu; a CVE identifier still speaks for itself.
- Agent frontmatter is read as YAML, not as one line: a skill whose `description: >` or
  `description: |-` spans several lines was recorded as the literal `>`. Four of the twenty
  skills in `anthropics/skills` were unreadable in the digest. A sequence value (`allowed-tools`
  written as a list) is folded into a comma-separated line.

### Documentation

- **The README now documents the current `.crab.yml` surface instead of making users reverse-engineer it.**
  It lists every top-level key, includes the commented defaults written by `crab init`, and calls
  out settings accepted before their behavior arrives; a regression keeps the README,
  `MawConfig`, and `DEFAULT_CONFIG_TEXT` aligned ([#27](https://github.com/drevendev/HungryCrab/issues/27)).
- `docs/design/02-mvp.md` defers to the roadmap on what a milestone contains, and says so. It
  had `npx skills add` in 0.3 while the roadmap — the authority — has it in 0.6, the same way
  the two once disagreed about digest coverage; a test pins the item that drifted
  ([#78](https://github.com/drevendev/HungryCrab/issues/78)).
- `docs/design/06-benchmark.md`: the specification of both benchmarks. B1 measures the menu
  deterministically and gates pull requests; B2 judges whole meals across crab versions, Claude
  models and a no-crab baseline, with blind two-pass judging by a different model family. States
  the hypotheses, the frozen setup, the metrics, the golden set, and the threats to validity.
- `docs/design/05-self-feeding.md`: the stage between 0.2 and 0.3. Milestone 0.2's exit criterion
  is only half met, because `/crab:eat` has never run in a live agent session. The document names
  what the maw is missing, lists twenty prey sniffed against an MIT maw with their license
  modes, gives an order to eat them in, and says what to watch in the skill.

## [0.2.0] - 2026-09-06

The first tagged release. It covers milestone 0.1 "Sniff & Digest" (the deterministic CLI and
its miners) and milestone 0.2 "Menu" (comparison, scoring, serving nutrients as issues, the
ledger, the Agent Skills and the Claude Code plugin).

### Added (milestone 0.2 "Menu")

- `crab compare`: prey digest minus host digest, turned into scored candidate nutrients with
  stable ids, evidence links, effort, risk and a license mode; writes `gap.md`, `menu.md` and
  `menu.json` into the prey digest. `crab menu` prints the ranked menu.
- Scoring weights in `data/scoring.yml`, overridable per host; `.crab.yml` (`crab init`) with
  appetite, serve policy, ledger mode and scoring overrides.
- The ledger (`crab ledger show|mark`): every meal and decision, in the host, the cache or
  nowhere; rejected and served nutrients disappear from later menus.
- `crab serve`: issues with a hidden `crab:<id>` marker, a label and a provenance footer,
  created through `gh` after a dry run; model-written notes per nutrient.
- `crab tune`: weight suggestions from the ledger per category and trait, appetite switch-offs,
  poor-match prey; `--write` applies them.
- `crab catch --issues N` and the `issues` miner (statistics, top by reactions, TF-IDF clusters);
  the `architecture` miner (symbol index, import graph, hubs, layering, public surface).
- Agent Skills `eat`, `license` and `serve`, the `crab-historian` and `crab-architect`
  subagents, the `/crab:sniff` and `/crab:menu` commands, and the Claude Code plugin manifest
  with its own marketplace.
- `benchmarks/run.py`: digest time and token budget per reference prey, with the first results;
  the whole loop was exercised end to end on a private sandbox host (issues created, zero
  duplicates on rerun, decisions recorded, `crab tune` consulted).

### Added (milestone 0.1 "Sniff & Digest")

- `crab sniff`: API-only reconnaissance with a license class, a verdict and warnings for
  archived, forked, stale and giant repositories.
- `crab catch`: clone or refresh the prey into the cache; `--shallow` and `--since` for giants.
- `crab digest`: ten deterministic miners (inventory, license, deps, ci, testing, docs,
  ai_config, history, branches, traits) writing a token-budgeted `digest/` with `manifest.json`;
  digests are addressed by commit SHA and served from the cache on repeat.
- `crab cache`: inspect and clean the cache.
- License engine: SPDX detection from license files, manifests, file headers and the GitHub API;
  the deterministic host x prey verdict matrix with the modes `COPY`, `COPY_FILE`,
  `REIMPLEMENT`, `IDEAS_ONLY` and `HUMAN`.
- Prompt-injection hygiene: summaries carry structure only; instruction-like fragments are
  flagged.
- Three synthetic fixture repositories (npm, pyproject, csproj) built into real git repositories
  by the tests.
- CI on Ubuntu and Windows with Python 3.11 and 3.14: ruff, mypy, pytest and a self-digest
  smoke test.

[Unreleased]: https://github.com/drevendev/HungryCrab/compare/v0.6.3...HEAD
[0.6.3]: https://github.com/drevendev/HungryCrab/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/drevendev/HungryCrab/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/drevendev/HungryCrab/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/drevendev/HungryCrab/compare/v0.2.0...v0.6.0
[0.2.0]: https://github.com/drevendev/HungryCrab/releases/tag/v0.2.0
