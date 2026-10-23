# Everywhere: local MCP, installation and offline HTML

The stable 0.6.0 scope is deterministic, human-supervised analysis: CLI, scoped stdio MCP,
portable skills and offline HTML. PR publication, Scheduled Crab work autonomy and
learning/usefulness claims remain experimental; their outstanding live acceptance work follows
this limited release and is not declared complete. See the
[release record](releases/0.6.0-readiness.md). No scheduler, model runtime or automatic
publication is installed by Everywhere.

## Install once

Python 3.11+ and Git are required, and uv or pipx to install the tool; the build steps below
use uv. Install the latest release of `hungry-crab` from
[PyPI](https://pypi.org/project/hungry-crab/) (`pipx install "hungry-crab[mcp]"` works as
well). Installation downloads the package and its dependencies.

```sh
uv tool install "hungry-crab[mcp]"
crab --version
crab mcp --check
```

To follow `master` instead, install from this repository with
`uv tool install "hungry-crab[mcp] @ git+https://github.com/drevendev/HungryCrab"`; appending a
tag such as `@v0.6.2` to that URL pins a release from git.

The smoke checks after installation are offline and need no GitHub authentication. `--check`
prints the server version, stdio transport and two tool names, then exits. It does not start a
server. Use the absolute installed `crab` executable in a desktop client's configuration if its
PATH differs from your terminal. `uv tool dir --bin` locates uv's executable directory;
`uv tool update-shell` explains how to expose it to new terminals.

The base install omits `[mcp]` and keeps PyYAML as its sole runtime dependency. HTML reports
work in the base install. MCP uses the official Python SDK as an optional extra, the only one.
A base-only `crab mcp --check` reports the
missing extra with exit code 2; it does not install anything itself.

Build/install your own reviewed checkout without Git-based installation:

```sh
uv build
```

Then install the wheel `uv build` wrote to `dist/` (its name carries the version), for example
`uv tool install ./dist/hungry_crab-0.6.2-py3-none-any.whl`.

For MCP, install `hungry-crab[mcp] @ file:///absolute/path/to/the.whl` instead. On Windows use
an absolute file URI such as `file:///C:/packages/the.whl`. Package metadata, wheel, sdist and
isolated tool installation are tested. Releases reach PyPI as `hungry-crab`, built from their
release tag; a wheel you built is the way to install a checkout you reviewed yourself.

## Expose two tools to your local host

```sh
crab mcp --root /absolute/path/to/workspace
```

Use `C:/work/workspace` on Windows. A root must already exist. Relative tool paths resolve
against the first root. Repeat `--root` for separate repositories; a path outside those roots,
including a symlink/junction escape, is refused. The operator chooses these roots at startup;
client-provided roots and tool arguments cannot expand them. The analysis cache must be outside
the approved source roots; choose it with global `--cache-dir` before `mcp` if needed.

- `crab_digest(prey, depth="normal", page=null)` returns a manifest, coverage and available
  Markdown page names. Supply one listed `page` to read that page; JSON miner bodies are not
  exposed as arbitrary file reads. Failed/blocked or damaged producer evidence is refused.
- `crab_menu(prey, maw, offset=0, limit=15, category=null)` compares with a local maw, respecting
  its licence, hunger, memory and existing ledger decisions. `limit` is 1–100. Follow
  `next_offset` until null to read all matching candidates. It does not record a meal or decision
  in the ledger and does not query the issue tracker for duplicate issues.

Both tools write only their analysis cache. They are marked as non-destructive, idempotent
analysis operations with `read_only_hint=false` because cache writes are real writes. There is
no serve, shell, ledger-edit, loop, tag or merge tool. Prey is never executed. Returned repository
content is untrusted data, not instructions. The server serializes its own requests; this does
not add a cross-process lock to the shared CLI cache.

Network acquisition is disabled by default. To allow remote GitHub/GitLab prey, the operator
must add `--allow-network` to the server command. Remote acquisition uses a shallow 90-day
history window, no issue/discussion/review/run collection and no wiki fetch. It may need suitable
read credentials, network access and time. Open-world annotations reflect this opt-in. The
existing hard acquisition-quota debt (#236) is not solved by the adapter. Start with local prey;
use the existing explicit CLI serving protocol for a reviewed change.

### Codex

The existing local MCP CLI accepts an executable and arguments after `--`:

```sh
codex mcp add crab -- crab mcp --root /absolute/path/to/workspace
```

Equivalent `config.toml` entry (use an absolute executable path for a desktop host):

```toml
[mcp_servers.crab]
command = "/absolute/path/to/crab"
args = ["mcp", "--root", "/absolute/path/to/workspace"]
startup_timeout_sec = 30
tool_timeout_sec = 180
```

Windows TOML may use single-quoted literal paths, for example `command = 'C:\Tools\crab.exe'`.
See the [official MCP configuration documentation](https://developers.openai.com/codex/mcp).
This configures a local stdio host; a hosted chat does not read your local config automatically.

### Cursor and Claude Code

Cursor's project `.cursor/mcp.json` and Claude Code's project `.mcp.json` accept the standard
stdio command/args shape below. Replace both paths with your own absolute paths and retain the
client's normal approval settings. On Windows JSON paths can use forward slashes.

```json
{
  "mcpServers": {
    "crab": {
      "type": "stdio",
      "command": "/absolute/path/to/crab",
      "args": ["mcp", "--root", "/absolute/path/to/workspace"]
    }
  }
}
```

Client settings and approval remain the host's responsibility. The implementation's automated
acceptance uses a real stdio SDK client in modern and initialize-handshake legacy modes on
Windows/Linux; it does not claim that a GUI model-driven meal ran in every host.

References: [Cursor MCP](https://cursor.com/docs/mcp),
[Claude Code MCP](https://code.claude.com/docs/en/mcp),
[official Python SDK](https://github.com/modelcontextprotocol/python-sdk).

## Portable skills

The existing skills can also be installed with the upstream skills CLI:

```sh
npx skills add drevendev/HungryCrab --skill eat --agent codex --copy
```

For this checked-out candidate use `npx skills add . --skill eat --agent codex --copy`.
The installer is a separate trusted Node tool and may download packages; it does not replace
installation of `crab`. A copied portable skill supplies instructions, not a mechanical sandbox
or the Claude plugin's safety hooks. The MCP adapter itself exposes only the operations above.
See the [skills CLI reference](https://github.com/vercel-labs/skills/blob/main/docs/commands.md).

## An inspectable HTML snapshot

Render an existing digest or menu without authentication, network calls, model calls or code
execution. The source can be its directory or `manifest.json` / `menu.json`.

```sh
crab report /path/to/digest --out digest-report.html
crab report /path/to/meal --out meal-report.html
```

Open the result as a local file in any browser. It is a self-contained HTML file with embedded
CSS, no JavaScript, external assets or tracking. A restrictive content policy blocks active
content; data is HTML-escaped and evidence links accept HTTP(S) only. Nothing opens a browser
automatically. Treat the result as a local artifact containing repository names, evidence and
possibly private paths; sharing it is your decision.

The digest view shows miner health, visibility, token counts and every retained Markdown page.
The meal view shows all candidate cards, their original ranks, licence modes, effort/risk,
evidence, pins and hidden decisions. Multi-prey menus retain their corroborating source pins.
Scores are pre-ranking signals. Missing facts, incomplete evidence and an empty menu are not
proof that the maw has no gap. Reports display snapshots and do not certify their authenticity.

The renderer bounds JSON/page reads and the final output size. JSON artifacts use UTF-8,
at most 8 MiB and at most 128 nested objects/arrays. A report reads at most 512 KiB per
Markdown page and 16 MiB in total (plus a sentinel byte to detect overflow), including failed
page reads, then lists the pages it omitted. It refuses malformed input and
unsafe artifact paths. A missing or unreadable digest page is identified in the report rather
than silently disappearing. A new output is created without replacing an existing destination;
`--force` explicitly replaces only a regular `.html` file. Rendering never changes its source.

## Reproduce the installation acceptance

```sh
uv sync --locked --all-extras
uv run python scripts/install_smoke.py --skills
```

This builds our own trusted source, installs the sdist as the base CLI and the wheel with MCP
into temporary uv tool homes outside the checkout, then runs version/help, digest, report and
both stdio protocol modes. It also installs our own eat skill into an isolated consumer project.
It uses no model, GitHub token or provider write. The CI runs this on Windows and Linux; tests
also exercise the full package on Python 3.11 and 3.14. `--offline` reuses already cached build
and package dependencies; Node installation can still need its own cache/network.
