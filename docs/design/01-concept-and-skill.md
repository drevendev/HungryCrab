# 01 · Concept and Skill Form Factor

## 1. The idea in one paragraph

Hungry Crab takes a foreign public repository (the *prey*), statically dissects all of it — code,
commit history, branches, issues, wiki, CI/CD, tests, architecture, documentation, AI configs — and
turns it into a **ranked menu of "nutrients"**: concrete things that can be carried over into your
own repository (the *host*). Every nutrient carries a license verdict (what exactly is allowed:
copy, rewrite, idea only) and provenance (where from, which commit, which license). Approved
nutrients become issues and pull requests in the host repository.

Guiding principle: **scripts squeeze out everything that can be squeezed deterministically; the
model is spent only on judgment and implementation.**

## 2. Vocabulary (the metaphor)

| Term | Meaning |
|---|---|
| **Prey** | The foreign repository being eaten |
| **Host** | Your repository, the one we eat for |
| **Sniff** | Quick reconnaissance of the prey via API: license, size, languages, is it worth eating |
| **Catch** | Downloading the prey into a local cache: git mirror, wiki, issues, PRs, releases |
| **Digest** | Deterministic analysis of the prey → the `digest/` folder (JSON + Markdown) |
| **Nutrient** | A unit of value: one transferable thing with a license mode and provenance |
| **Menu** | Ranked list of candidate nutrients after comparing prey with host |
| **Serve** | Turning approved nutrients into issues / PRs / attribution files |
| **Ledger** | Journal in the host repository: what was eaten, what was accepted, what was rejected |
| **Appetite** | Host settings: which nutrient categories it is interested in |
| **Molting** | Refactoring and cleanup after growth (Evolving Crab term) |

## 3. What counts as "useful": the nutrient taxonomy

Categories are fixed — they are keys in the host config, issue labels, and scoring axes.

| Category | Examples | Extracted by |
|---|---|---|
| `ci` | caching, matrix builds, release automation, workflow permissions, reusable workflows, concurrency groups | script (YAML parsing) |
| `tooling` | pre-commit, linters, formatters, `.editorconfig`, devcontainer, Makefile/justfile, dependabot/renovate | script |
| `tests` | test framework, coverage and its threshold, property-based, fuzz, e2e, snapshot, fixtures, **edge cases mined from fix commits and issues** | script + model |
| `docs` | README structure, CONTRIBUTING, ADRs, CHANGELOG format, issue/PR templates, docs site | script + model |
| `hygiene` | CODEOWNERS, SECURITY.md, license headers, semver tags, conventional commits, labels | script |
| `architecture` | layering, module structure, patterns, public API, dependency graph | script (graph, symbols) + model (interpretation) |
| `code` | algorithms, utilities, error handling, optimizations | model, under a license mode |
| `deps` | which libraries they use and why, version pinning, lockfile policy | script + model |
| `security` | security fixes in history, input validation, secret handling, CodeQL/gitleaks in CI | script + model |
| `ai-config` | `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.claude/` (skills, agents, hooks), `.mcp.json` | script + model |
| `history-lesson` | "what broke and how it was fixed": hotspots, reverts, hotfix patterns, file coupling | script (metrics) + model (lesson) |
| `issue-lesson` | recurring user pain, FAQs, known pitfalls, top feature requests | script (clusters) + model (lesson) |

Additionally: **branches** — experimental features that never landed in main often contain ideas;
**releases/tags** — versioning discipline and release-notes format.

## 4. Form factor: three layers

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 2 · Claude Code plugin  (hungry-crab)                │
│  skills/  agents/  hooks/  — /crab:* commands, subagents,   │
│  the "never execute prey code" hook, permission settings    │
├─────────────────────────────────────────────────────────────┤
│  Layer 1 · Agent Skills  (SKILL.md, open standard)          │
│  Protocol: how to call the CLI, how to read the digest      │
│  progressively, how to judge, how to write issues/PRs,      │
│  license rules                                              │
├─────────────────────────────────────────────────────────────┤
│  Layer 0 · CLI `crab`  (Python ≥ 3.11, git, gh)             │
│  sniff · catch · digest · compare · menu · serve · ledger   │
│  Deterministic. No LLM. Runs in CI and in other agents.     │
└─────────────────────────────────────────────────────────────┘
```

### Why this shape

- **CLI separate from the model** — so that 90 % of the work is reproducible, cheap, testable and
  reusable (GitHub Action, MCP server, other agents), and so that the Evolving Crab has something
  to benchmark without an LLM.
- **Python**, not Node/Go: rich ecosystem for git mining and YAML, cross-platform (the author works
  on Windows), and agents write and patch Python well — which matters for the Evolving Crab that
  will modify itself. Dependencies stay minimal (PyYAML; tree-sitter later, optionally) so that
  `pip install` works everywhere.
- **Agent Skills**, not "a prompt in the README": the `SKILL.md` + `scripts/` + `references/`
  format is supported by Claude Code, Codex, Cursor and others; progressive disclosure is built in.
- **Plugin** on top of the skills — because we need subagents (isolated context for the clean
  room), hooks (mechanical enforcement of safety rules) and a command namespace.

### What scripts do vs. what the model does

| Task | Script | Model |
|---|---|---|
| Download everything (git mirror, wiki, issues, PRs, releases) | ✔ | |
| Prey license and per-file exceptions, verdict matrix | ✔ | flags ambiguous cases for a human |
| Inventory, languages, LOC, manifests, entry points | ✔ | |
| Traits matrix (~150 traits) for host and prey, diff | ✔ | |
| Parsing CI, tests, dependencies, docs, AI configs | ✔ | |
| History metrics: hotspots, fix ratio, reverts, coupling, cadence | ✔ | |
| Issue clustering (TF-IDF), top by reactions, time to close | ✔ | |
| Preliminary menu scoring | ✔ | |
| "Is this valuable for *this* host?" | | ✔ |
| Phrasing a lesson from history/issues | | ✔ |
| Issue text, implementation plan, effort/risk estimate | | ✔ |
| Implementing the PR (adaptation or clean room) | | ✔ |
| Creating issues/PRs via gh, deduplication, ledger, attribution | ✔ | |

## 5. Installation

Prerequisites: `git`, `gh` (authenticated), Python ≥ 3.11.

**Path A — Claude Code plugin (primary).** The `HungryCrab` repository is itself a plugin and
its own marketplace (`.claude-plugin/marketplace.json`):

```
/plugin marketplace add drevendev/HungryCrab
/plugin install crab@hungry-crab
```

Skills call the CLI via `${CLAUDE_PLUGIN_ROOT}/src/...` — no separate Python package install is
required. Commands appear as `/crab:eat`, `/crab:sniff`, `/crab:menu`, `/crab:serve`.

**Path B — skills only, any agent.** `npx skills add drevendev/HungryCrab` places the `SKILL.md`
folders into Claude Code / Codex / Cursor. On first run the skill checks for the CLI and suggests
`pip install hungry-crab` (or `uv tool install hungry-crab`).

**Path C — manual.** `git clone`, symlink `skills/*` into `~/.claude/skills/`, `pip install -e .`.

**Path D — CI (post-MVP).** `uses: drevendev/HungryCrab/action@v1` — run digest and serve in the
host's workflow on a schedule or via `workflow_dispatch`.

## 6. Repository layout of `HungryCrab`

```
HungryCrab/
├── .claude-plugin/
│   ├── plugin.json              # plugin manifest
│   └── marketplace.json         # makes the repo its own marketplace
├── skills/
│   ├── eat/SKILL.md             # orchestrator: sniff→catch→digest→compare→menu→serve (/crab:eat)
│   ├── license/                 # SKILL.md + references/matrix.md — mode rules
│   ├── cleanroom/SKILL.md       # protocol: "spec without code → implementation without prey access" (0.3)
│   └── serve/SKILL.md           # how to write issues/PRs, templates, provenance
├── agents/
│   ├── crab-historian.md        # reads history.md/branches.md, formulates lessons
│   ├── crab-architect.md        # reads architecture.md, compares with the host
│   ├── crab-license-auditor.md  # ambiguous license cases
│   └── crab-cleanroom-impl.md   # implementer without read access to the prey cache
├── hooks/hooks.json             # PreToolUse: forbid executing anything from the prey cache
├── src/hungry_crab/
│   ├── cli.py
│   ├── fetch/        (git mirror, wiki, gh api: issues/prs/releases/labels/community)
│   ├── miners/       (license, inventory, traits, ci, tests, deps, history, branches,
│   │                  issues, docs, architecture, ai_config, security)
│   ├── compare/      (traits diff, deps diff, scoring → menu)
│   ├── serve/        (templates, gh issue/pr, dedup, ledger, attribution)
│   └── schemas/      (nutrient.json, digest manifest, .crab.yml)
├── tests/            (synthetic mini repositories as fixtures)
├── docs/
├── action/           (post-MVP: composite GitHub Action)
├── pyproject.toml · README.md · LICENSE (MIT)
```

## 7. Workflow of `/crab:eat <prey> [--host .]`

1. **Sniff.** `crab sniff owner/repo` — API only: license (SPDX), size, languages, stars,
   activity, community profile. Instant verdict: default mode (`COPY`/`IDEAS_ONLY`…), a warning if
   the repository is huge (suggest `--shallow --since 2y`).
2. **Catch.** `crab catch` — `git clone --mirror` (or a partial clone for giants),
   `<repo>.wiki.git` if present, `gh api --paginate` for issues/PRs/releases/labels (limits: N
   newest + top by reactions). Everything goes to `~/.cache/hungry-crab/github/<owner>/<repo>/`: one shared clone plus
   per-commit digests under `digests/<sha>/`.
3. **Digest.** `crab digest` — runs all miners → `digest/` (see § 8). No LLM.
4. **Compare.** `crab compare --host .` — digest of the host (fast, local), diff of
   traits/deps/ci/tests/ai-config, preliminary scoring → `gap.md`, `menu.md`.
5. **Judge (model).** The skill reads `manifest.json`, then `menu.md`, then **only the digest
   sections needed** for the candidates in doubt. For history and architecture it may delegate to
   the `crab-historian` / `crab-architect` subagents. Output: filled nutrient cards — what / why for
   the host / how / mode / effort / risk.
6. **Approve.** A menu table for the user. Interactively — checkboxes; in CI — the policy from
   `.crab.yml` (`issues: auto`, `prs: ask`, limits).
7. **Serve.** `crab serve` creates issues (label `hungry-crab`, hidden id marker in the body,
   provenance footer). For PR nutrients — one branch per nutrient, implementation by the model
   (for `REIMPLEMENT` — via the clean-room subagent), `gh pr create` with provenance, update of
   `THIRD_PARTY_NOTICES.md` if anything was copied.
8. **Ledger.** An entry in the host's `.crab/ledger.json`: prey@sha, the nutrient list and their
   statuses. Eating the same repository again shows only what is new.

## 8. Digest: format and progressive disclosure

```
digest/
├── manifest.json        # what exists, size of each file in tokens, prey sha, schema version
├── license.json         # spdx, confidence, per-file exceptions, verdict vs host
├── inventory.{json,md}  # tree, languages, LOC, manifests, entry points, vendored/generated
├── traits.json          # boolean/enum trait matrix
├── ci.{json,md}         # workflows: triggers, jobs, actions@versions, cache, matrix, permissions
├── tests.md             # test landscape
├── deps.json            # normalized dependencies (runtime/dev/build)
├── history.md           # hotspots, fix ratio, reverts, coupling, cadence, bus factor, notable commits
├── branches.md          # branches: ahead/behind, freshness, what they are about (subjects)
├── issues.md            # clusters, top pains and requests, links; issues.jsonl — raw material
├── docs.md              # README/docs outline, ADRs, CHANGELOG, templates, wiki outline
├── architecture.md      # directory hierarchy with purpose guesses, symbol index, import-graph hubs
├── ai.md                # AI configs found and their gist
├── gap.md               # what the host lacks that the prey has (after compare)
└── menu.md              # ranked candidates with license mode and pre-score
```

Budget rules: each `.md` is ≤ 3–4k tokens by default, `--depth deep` expands; raw material lives
in JSON/JSONL for scripts. `manifest.json` reports sizes so the skill can decide what to read.
Target: a full default digest ≤ 30k tokens.

## 9. Nutrient card (schema)

```json
{
  "id": "crab:owner/repo@1a2b3c:ci:actions-cache-npm:7f3a",
  "category": "ci",
  "title": "npm cache in CI via actions/setup-node cache",
  "what": "…", "why_for_host": "…", "how": "…",
  "evidence": [{"path": ".github/workflows/ci.yml", "lines": "12-30",
                "url": "https://github.com/owner/repo/blob/1a2b3c/.github/workflows/ci.yml#L12-L30"}],
  "license_mode": "REIMPLEMENT",
  "effort": "S", "risk": "low", "score": 0.82,
  "artifact": "pr",
  "status": "proposed",
  "provenance": {"prey": "owner/repo", "sha": "1a2b3c", "license": "GPL-3.0-only",
                 "fetched_at": "2026-09-05T12:00:00Z"}
}
```

`id` is stable (a hash of category + normalized trait), so deduplication and the ledger work
across runs.

## 10. License engine

### Source of truth

1. `gh api repos/{o}/{r}` → `license.spdx_id` (primary, cheap).
2. Locally: `LICENSE*`, `COPYING*`, `SPDX-License-Identifier:` in file headers,
   `package.json.license`, `pyproject.license`, separate licenses in subfolders (`vendor/`,
   `third_party/`) — per-file exceptions.
3. Signs of "source-available but not open": BSL, SSPL, Elastic, Commons Clause, "all rights
   reserved", no license at all.

### Modes

| Mode | What is allowed |
|---|---|
| `COPY` | copy code/configs, keeping the copyright notice and recording it in `THIRD_PARTY_NOTICES.md` |
| `COPY_FILE` | copy whole files; the file keeps its own license (MPL-2.0) |
| `REIMPLEMENT` | use as a specification: clean-room rewrite, no verbatim code carried over |
| `IDEAS_ONLY` | ideas, architecture, approaches, facts only; not a line of code or documentation text |
| `HUMAN` | the engine is unsure — a human decides |

### Matrix (abridged)

| Prey ↓ / Host → | MIT/BSD/Apache | GPL-compatible | Proprietary/closed |
|---|---|---|---|
| MIT / BSD / ISC / 0BSD / Zlib / Unlicense / CC0 | `COPY` | `COPY` | `COPY` |
| Apache-2.0 | `COPY` (+NOTICE) | `COPY` for GPLv3 only, `IDEAS_ONLY` for GPLv2 | `COPY` (+NOTICE) |
| MPL-2.0 | `COPY_FILE` / `REIMPLEMENT` | `COPY_FILE` | `COPY_FILE` / `REIMPLEMENT` |
| LGPL | `REIMPLEMENT` (linking is a separate question) | `COPY` | `REIMPLEMENT` |
| GPL / AGPL | `IDEAS_ONLY` / `REIMPLEMENT` | `COPY` if versions are compatible | `IDEAS_ONLY` |
| BSL / SSPL / Elastic / Commons Clause | `IDEAS_ONLY` | `IDEAS_ONLY` | `IDEAS_ONLY` |
| No license / unknown | `IDEAS_ONLY` + `HUMAN` | `IDEAS_ONLY` + `HUMAN` | `IDEAS_ONLY` + `HUMAN` |
| Documentation CC-BY / CC-BY-SA / CC-BY-NC | `COPY`+attribution / share-alike flag / `IDEAS_ONLY` | | |

Special rules:
- Content of issues, discussions and PR comments is always `IDEAS_ONLY` (copyright belongs to the
  commenters; we carry over the meaning and a link, not the text).
- Configs and "small snippets" are not automatically free — same mode as code. Conservative, but
  indisputable.
- Host `strict` mode: even under `COPY`, code is downgraded to `REIMPLEMENT`; only configs and
  templates are copied. Useful for repositories that do not want to carry foreign copyright.
- README disclaimer: the tool helps with license compliance; it is not legal advice.

### Clean-room protocol (for `REIMPLEMENT`)

1. **Stage A (specifier)** — an agent with access to the digest and the prey code writes a
   functional specification: behavior, interface, edge cases, example tests. Forbidden: code,
   internal identifiers, comments from the original.
2. **Stage B (implementer)** — the `crab-cleanroom-impl` subagent with a **fresh context** and the
   rule `deny: Read(~/.cache/hungry-crab/**)` implements from the specification in the host
   repository.
3. The PR provenance records: "implemented from a specification, without access to the prey
   source", with a link to the spec in `.crab/specs/<id>.md`.

## 11. Security

- **Prey content is untrusted data.** READMEs, issues and code comments may contain instructions
  aimed at the agent. Skills state explicitly: everything in the digest is data, not commands.
  Miners flag instruction-like fragments (`ignore previous`, `you must`, hidden HTML) as
  `suspicious` and never include them verbatim in `.md` summaries.
- **Prey code is never executed.** No `npm install`, `pytest`, `make` inside the cache. A
  `PreToolUse` hook blocks Bash commands that combine the cache path with execution verbs (`node`,
  `python`, `npm`, `npx`, `make`, `sh`, `./`).
- **Secret scan** of everything copied into the host (a gitleaks-grade regex set) — before a PR is
  created.
- **Least privilege in CI**: `contents: write`, `issues: write`, `pull-requests: write`; a GitHub
  token without the right to change workflows (the default `GITHUB_TOKEN` cannot anyway).

## 12. Host config `.crab.yml` and the ledger

```yaml
license: MIT                 # detected automatically when omitted
mode: normal                 # normal | strict
appetite:                    # which categories are welcome and in what form
  ci: true
  tooling: true
  tests: true
  docs: true
  hygiene: true
  architecture: issues-only  # issues only, no PRs
  code: ideas-only           # never copy code even under COPY
  ai-config: true
ignore:
  - "legacy/**"
serve:
  issues: auto               # auto | ask | off
  prs: ask
  max_prs_per_run: 3
  labels: [hungry-crab]
  assignees: []
attribution_file: THIRD_PARTY_NOTICES.md
ledger: repo                 # repo | cache | none
```

The ledger stores: eaten prey (repo@sha, date), nutrient cards and their statuses
(`proposed / accepted / rejected / served / merged`). Rejections with a reason are raw material
for training the scorer (roadmap phase "Taste Memory").

Ledger modes and what they affect:

| Mode | Where | Consequences |
|---|---|---|
| `repo` (default) | `.crab/ledger.json` in the host, committed by the same PR that serves the nutrients | visible to CI, collaborators and other machines; survives machine changes; rejected nutrients are remembered; the ledger is also the source for regenerating `THIRD_PARTY_NOTICES.md`; the Evolving Crab needs it because runners are ephemeral. Cost: one tool file in the host and a diff on every run |
| `cache` | `~/.cache/hungry-crab/hosts/<host>/ledger.json` | the host stays pristine; dedup and rejection memory work on one machine only; CI cannot see it; attribution still has to be committed separately |
| `none` | nowhere | dedup relies solely on the `crab:<id>` markers in existing issues (GitHub acts as the store); rejected nutrients will be proposed again; nothing to learn from |

Regardless of mode, dedup against open issues via the `crab:<id>` marker is always on.

## 13. Non-functional requirements

- Digest of a repository up to 50k LOC and 5k commits — ≤ 2 minutes on a laptop; giants via
  `--shallow`, `--since`, `--filter=blob:none`.
- Windows and Linux are first-class (Python, `pathlib`, no bash-only scripts in the core; shell
  wrappers are optional and come in `.sh`/`.ps1` pairs).
- GitHub API: pagination, rate-limit respect (REST 5000/h, search 30/min), response caching by
  ETag. GitLab/Gitea later through a fetch-layer adapter.
- The prey cache is addressed by SHA: a repeated digest of the same SHA comes from cache.
- Everything that reaches the model passes through budgeted `.md` summaries; raw JSON is for
  scripts only.

## 14. Decisions log

| Question | Decision | Rationale |
|---|---|---|
| CLI name | `crab` | short; PATH conflicts are unlikely and the package keeps the name `hungry-crab` |
| Project license | MIT | simplest, widest adoption |
| Ledger location | host repository, `.crab/ledger.json`, committed together with the crab's own PRs; `cache` and `none` remain available | CI, other machines and the Evolving Crab need it; attribution has to live in the repository anyway; rejected nutrients must be remembered |
| Repository language | English for everything in the repository | public project, tooling and agents expect it |
| Prey sources | GitHub first, fetch layer behind an interface | GitLab/Gitea can be added later without touching the miners |
| Repository name | `drevendev/HungryCrab`; the distribution and the cache directory stay `hungry-crab` | matches the naming of the author's other repositories |
| Plugin and skill names | plugin `crab`, skills `eat`, `license`, `serve` (folders without a prefix), marketplace `hungry-crab` | Claude Code namespaces plugin skills as `/plugin:skill`, so this is the only way to get `/crab:eat`; the price is generic folder names for skills-only installs |
| Nutrient ids | `crab:<category>:<key>`, host-relative and independent of the prey and its SHA; prey-specific lessons carry the prey slug in the key | deduplication must work across runs, machines and different prey suggesting the same thing; the SHA lives in the provenance instead |
| Scoring | `category x value x applicability x mode x effort - risk` with weights in `data/scoring.yml`, overridable per host; `crab tune` moves weights from ledger decisions with fixed, explained steps | a formula a human can read beats a model's opinion; the ledger is the only training signal until the Evolving Crab exists |
| First host | a private sandbox repository built from the npm-app fixture, then the author's real repositories | issues created while the menu is being tuned must not pollute real projects |
