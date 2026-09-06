# 05 · Self-feeding — the crab eats for itself

## 1. Why this stage exists

Milestone 0.2 is code-complete, but its exit criterion is only half met:

> end-to-end `/crab:eat` creates issues in a test repository; a repeated run yields 0 duplicates.

The second half is verified: on the sandbox host the crab created three issues from
`colinhacks/zod` and a rerun produced zero duplicates. The first half is not. Every run so far
went through the CLI by hand. The `eat` skill, the subagents and the plugin have been read, never
executed by an agent. Until an agent walks the protocol, the skill is a document, not a feature.

The roadmap already calls for this ("own hygiene: from 0.2 the crab eats itself"), so this stage
just makes it explicit and puts it before 0.3.

## 2. Goal

Run `/crab:eat` from a live agent session, with **Hungry Crab itself as the host**, against prey
chosen for what the crab is missing. Two outcomes matter equally:

1. **Nutrients.** Real issues in `drevendev/HungryCrab` that make the crab better.
2. **Skill defects.** Every place the protocol misleads the agent, costs it a wrong turn, or
   leaves it guessing. These are the actual deliverable; the issues are the side effect.

## 3. What the crab is missing (so we know what to look for)

Facts about the host, from its own digest:

- no release automation, no coverage measurement, no coverage gate;
- no CodeQL or any security scanning in CI;
- no docs site, no ADRs;
- no `THIRD_PARTY_NOTICES.md` and no attribution command (0.3 territory);
- language detection, LOC counting and the license corpus are hand-written maps;
- the token estimate is characters divided by 3.5, not a tokenizer;
- SPDX expressions (`OR`, `AND`) are split by hand.

## 4. Prey

Twenty candidates, all sniffed with the crab on 2026-09-06 against an MIT host. Mode is the
license verdict for this repository.

### Cousins: digest format, budgets, ignore rules

| Prey | Mode | Size | What to take |
|---|---|---|---|
| `yamadashy/repomix` | COPY | 28 MB | per-file and per-output token counting, layered ignore rules, output formats, release pipeline |
| `coderamp-labs/gitingest` | COPY | 1 MB | the closest cousin in Python: how a tree becomes a prompt, size limits, exclusion patterns |
| `mufeedvh/code2prompt` | COPY | 8 MB | output templating, token budget handling, glob filters |
| `simonw/files-to-prompt` | COPY | <1 MB | minimal CLI ergonomics; a model release workflow for a small Python CLI |
| `openai/tiktoken` | COPY | <1 MB | a real tokenizer to replace or calibrate our characters-over-3.5 estimate |

### The license engine

| Prey | Mode | Size | What to take |
|---|---|---|---|
| `aboutcode-org/scancode-toolkit` | IDEAS_ONLY (HUMAN) | 694 MB | the reference detector: rule corpus and matching strategy. Catch with `--shallow --since 2y` |
| `licensee/licensee` | COPY | 4 MB | GitHub's own detector: matching heuristics, confidence thresholds, corpus layout |
| `aboutcode-org/license-expression` | IDEAS_ONLY (HUMAN) | 36 MB | proper SPDX expression parsing; ours splits `OR` and `AND` by hand |
| `google/licenseclassifier` | COPY | 87 MB | classification by similarity instead of exact phrase matching |
| `fsfe/reuse-tool` | IDEAS_ONLY | 7 MB | SPDX header conventions and the compliance workflow around them |

### History, inventory, symbols

| Prey | Mode | Size | What to take |
|---|---|---|---|
| `ishepard/pydriller` | COPY | 51 MB | the reference git-mining framework: which metrics are worth computing and how history is walked |
| `adamtornhill/code-maat` | IDEAS_ONLY | 1 MB | the origin of co-change coupling and hotspot analysis; our history miner reimplements a subset |
| `erikbern/git-of-theseus` | COPY | 2 MB | code survival over time. No pushes for 1015 days, so ideas over code |
| `boyter/scc` | COPY | 16 MB | LOC counting that separates code, comments and blanks, plus complexity; we count raw lines |
| `github-linguist/linguist` | COPY | 41 MB | the language corpus: extensions, vendored paths, generated-file heuristics. Directly replaces our hand-written maps |

### Release engineering and the agent layer

| Prey | Mode | Size | What to take |
|---|---|---|---|
| `pypa/hatch` | COPY | 49 MB | our own build backend's project: CI matrix, docs site, release automation, coverage |
| `pypa/pipx` | COPY | 5 MB | install UX and the release workflow of a user-facing Python CLI |
| `pre-commit/pre-commit` | COPY | 4 MB | hook conventions and a very disciplined CI |
| `anthropics/skills` | IDEAS_ONLY | 5 MB | the official Agent Skills corpus: how a good `SKILL.md` reads, frontmatter and references layout |
| `vercel-labs/skills` | COPY | 2 MB | the cross-agent skills installer; how to be installable beyond Claude Code and Codex |

Four prey report no license through the API and one reports a custom one, so they arrive as
`IDEAS_ONLY` or `HUMAN`. That is the engine being conservative on purpose: `sniff` only reads the
forge API, while `digest` also reads license files, manifests and headers and may resolve them.
Checking whether it does is part of this stage.

## 5. Order

Start small and same-stack, then widen:

1. `pypa/pipx` — 5 MB, MIT, Python, rich in exactly the CI and release nutrients the host lacks.
2. `anthropics/skills` — tests the `ai-config` category and the `IDEAS_ONLY` path.
3. `github-linguist/linguist` — a large permissive prey, tests the `code` category and evidence links.
4. `aboutcode-org/scancode-toolkit` — the giant: tests `--shallow --since`, the `HUMAN` verdict and the budget.

## 6. What to watch in the skill

- Does the agent find the CLI, or does the fallback path in `eat/SKILL.md` mislead it?
- Does it read `manifest.json` and `menu.md` first, or does it wander into raw JSON?
- Does it delegate history and architecture to the subagents, or do it badly itself?
- Does it write `why_for_host` and `how` that a maintainer would act on, or generic filler?
- Does it respect the license mode without being reminded?
- Does it record rejections in the ledger, or drop them silently?
- How many tokens does one meal actually cost?

## 7. Exit criteria

- `/crab:eat` run end to end from a live agent session, at least twice, on different prey.
- Issues created in `drevendev/HungryCrab` with provenance, and a rerun yields zero duplicates.
- Every skill defect found is either fixed or written down as an issue.
- The token cost of one meal recorded in `benchmarks/`.
- Milestone 0.2's exit criterion honestly closed; then 0.3 starts.
