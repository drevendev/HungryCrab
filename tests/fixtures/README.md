# Fixtures

Four synthetic mini repositories with deliberate gaps, used by the miner tests.

| Fixture | Stack | Has | Deliberately lacks |
|---|---|---|---|
| `npm-app` | Vite + TypeScript, pnpm | CI with cache/matrix/permissions/concurrency, release workflow, dependabot, Playwright e2e, Vitest, fast-check, CHANGELOG (keep a changelog), CONTRIBUTING, CLAUDE.md, `.claude/` settings and a skill, committed `dist/`, `examples/` in Python and Go | SECURITY.md, CODEOWNERS, coverage threshold, pre-commit |
| `pyproject-cli` | Python CLI, uv, hatchling, Apache-2.0 | pytest with `--cov-fail-under`, Hypothesis, pre-commit, mkdocs + ADR, SECURITY.md, CODEOWNERS, issue/PR templates, publish workflow, AGENTS.md, `.cursorrules` | dependabot, `.editorconfig`, CLAUDE.md, CI cache, CI permissions, e2e tests |
| `dotnet-lib` | C# library, xUnit, BenchmarkDotNet, GPL-3.0 | multi-target csproj, tests, benchmarks, CODE_OF_CONDUCT, `.editorconfig` | CI matrix/cache/permissions, dependabot, AI configs, CONTRIBUTING, CHANGELOG, coverage threshold |
| `go-service` | Go daemon, cobra, testify, MIT | `go.mod` in **both** `require` spellings (block and single-line `// indirect`), stdlib `testing` in one package and testify in another, golangci-lint, a Go version matrix in CI, semver tags | coverage threshold, dependabot, AI configs, CONTRIBUTING, CHANGELOG, `.editorconfig` |

## Layout

- `repos/<name>/` is the final tree. A file named `X.fixture` is copied as `X`; the suffix keeps
  fixture `CLAUDE.md` / `.gitignore` files from acting on this repository.
- `histories/<name>.json` is the ordered commit list that produces the tree: which files each
  commit adds, which existing files it touches (a harmless line is appended), tags, and
  branches. Every file in the tree must be added by some commit or the builder fails.
- `tests/fixture_builder.py` turns both into a real git repository under a temporary directory.

`go-service` exists because Go broke the crab twice on real prey: `ossf/scorecard` reported
271 `_test.go` files and no framework, and a single-line `require` was dropped silently. It
carries both shapes on purpose, so neither defect can come back quietly.

`npm-app` carries the `promptfoo/promptfoo` shape on purpose: two example providers with a
manifest each, in languages the app is not written in. Read without `mark_example_trees` it
reports three ecosystems and offers flask and numpy as dependencies to borrow, so
`test_npm_dependency_policy` and `test_npm_traits` fail the moment that pass stops working.

The histories are the interesting part for the history and branches miners: hotspots with a
high fix ratio, a revert, mixed conventional-commit discipline, semver tags with a cadence, and
stale as well as fresh unmerged branches. Test "now" is fixed to 2025-06-01 in `conftest.py`.
