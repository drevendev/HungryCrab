# Hungry Crab — Design Documents

Design drafts for **Hungry Crab** (eat a foreign repository, digest it, and extract everything
useful for your own repository without violating licenses) and for its overlay
**Evolving Hungry Crab** (a self-improving loop driven by active GitHub Actions).

| Document | Covers |
|---|---|
| [01-concept-and-skill.md](01-concept-and-skill.md) | What the crab is, form factor (CLI + Agent Skills + plugin), installation, architecture, digest format, license engine, security, decisions log |
| [02-mvp.md](02-mvp.md) | Minimum viable product: commands, miners, skill protocol, milestones 0.1–0.3, acceptance criteria, first prey → maw pairs |
| [03-roadmap.md](03-roadmap.md) | Roadmap: base crab → evolving crab → forks |
| [04-evolving-crab.md](04-evolving-crab.md) | Detailed design of the GOAL → HUNT → CONSUME → EVOLVE → TRIAL → GROW → MOLT loop, budgets, constitution, autonomy levels, fork kit (Sudoku example) |
| [05-self-feeding.md](05-self-feeding.md) | The stage between 0.2 and 0.3: run `/crab:eat` from a live agent session with the crab itself as the maw, against twenty sniffed prey |
| [06-benchmark.md](06-benchmark.md) | Specification of the two benchmarks: B1 the deterministic menu benchmark in CI, B2 the judged meal benchmark across crab versions and Claude models |
| [07-scheduled-crab.md](07-scheduled-crab.md) | The local sibling of the evolving crab: crave → hunt → eat → serve → grow → trial → taste → molt → harden, one phase per wake-up, on this repository or on any target it is pointed at |

## Key decisions (TL;DR)

1. **Three layers.** A deterministic CLI `crab` (Python, minimal dependencies) does 90 % of the
   work without a model. On top of it sit Agent Skills (the open `SKILL.md` format) that teach an
   agent the protocol. Everything ships as a Claude Code plugin (skills + subagents + hooks).
2. **The model reads a digest, not the repository.** Scripts compress the prey into a structured
   `digest/` with a token budget and progressive disclosure. The model is spent only where judgment
   is needed: rating the menu, writing issues, implementing PRs.
3. **Licenses are decided by a deterministic engine, not by the model's opinion.** A
   `maw license × prey license` matrix yields a mode for every nutrient:
   `COPY` / `COPY_FILE` / `REIMPLEMENT` (clean room) / `IDEAS_ONLY`. Provenance everywhere.
4. **Prey code is never executed.** Static analysis only. Prey content is untrusted data
   (prompt-injection defense), enforced by a hook.
5. **Artifacts are issues and PRs with a provenance footer**, plus a `ledger` in the maw
   repository for idempotency (eating the same prey again yields only what is new) and for learning
   from accepted/rejected proposals.
6. **MVP** = deterministic transplant of hygiene / CI / tooling / AI configs, plus a menu of ideas
   through the model. Ecosystem priority follows the author's fleet: TypeScript/npm,
   Python/pyproject, C#/.NET, GitHub Actions.
7. **Evolving Hungry Crab** is a separate public repository. Cycle state lives in GitHub-native
   entities (a cycle issue, branches, PRs, labels), phases are separate jobs, TRIAL is plain CI
   without an LLM, the goal is plugged in as a `goal/` pack, and forks consume the workflows as
   reusable workflows from upstream.

## Repository conventions

- **English only.** Documentation, code, code comments, commit messages, issues, PR titles and
  bodies, labels, and generated artifacts are written in English.
- **License:** MIT.
- **Names:** repository `drevendev/HungryCrab`, distribution and plugin `hungry-crab`, CLI `crab`,
  skill namespace `/crab:*`, Python package `hungry_crab`.
- **Commits:** Conventional Commits. The crab measures this trait in prey, so it must pass its own
  check.
- **Ledger** lives in the maw repository by default (`.crab/ledger.json`) and is committed by the
  same PR that serves the nutrients; `cache` and `none` modes exist for maws that do not want it.
