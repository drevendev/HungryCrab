# Nutrient categories and what counts as valuable

Categories are fixed: they are keys in `.crab.yml` (hunger), issue labels and scoring axes.

| Category | Valuable when | Usually served as |
|---|---|---|
| `security` | the maw runs code from the network or accepts input and has no scanning, no SECURITY.md, no least-privilege CI | pr (scanner workflow), issue |
| `ci` | the maw builds or tests in CI and lacks caching, a matrix, permissions, concurrency, timeouts or release automation that the prey has | pr |
| `tests` | the prey's kind of tests (e2e, property, snapshot, fuzz, benchmarks) exposes a class of bugs the maw cares about; a coverage gate exists in the prey and not here | issue, pr for a coverage gate |
| `tooling` | a linter, formatter, type checker, pre-commit, dependabot, editorconfig or runtime pin the maw lacks in an ecosystem it uses | pr |
| `ai-config` | the maw is agent-driven and lacks AGENTS.md, CLAUDE.md, skills, subagents, hooks or MCP config; the prey shows what a good one covers | pr for instruction files, issue for skills |
| `hygiene` | community files, changelog discipline, semver tags, conventional commits, README sections | pr (small), issue (process) |
| `docs` | a docs site, ADRs, a docs directory; only if the maw has readers | issue |
| `deps` | a library the prey uses for a problem the maw also has; never adopt for its own sake | issue |
| `history-lesson` | the prey's fix-prone areas and reverts point at a fragile design the maw shares | issue, from the historian |
| `issue-lesson` | recurring user pain in the prey that the maw's users will hit too | idea, then issue |
| `architecture` | layering or hubs in the prey suggest a structural change the maw would benefit from | issue, from the architect |
| `code` | an algorithm or utility worth copying (mode `COPY`) or reimplementing (`REIMPLEMENT`) | issue until 0.3, then pr |

## Drop when

- the maw already does it differently on purpose (check the ledger reasons and the README);
- the nutrient is stack-specific and the stacks differ (uptake below 0.5);
- the license mode is `IDEAS_ONLY` and the value is in the text, not the idea;
- it would add a dependency the maw's users must install without a clear problem it solves.
