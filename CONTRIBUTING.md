# Contributing

Thanks for feeding the crab. This is a small project with a clear design; the design documents in
[docs/design](docs/design/README.md) are the source of truth for what gets built and why.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync` creates `.venv` with the development group.
3. `git` is required by the tests; `gh` (authenticated) only by `crab sniff` and `crab catch`.

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Optional: `uv tool install pre-commit` and `pre-commit install` to run the hooks on every commit.

## Conventions

- Everything in the repository is written in English.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org); the crab
  measures this trait in the repositories it eats and must pass its own check.
- The CLI stays deterministic and offline except for `sniff` and `catch`. No LLM calls.
- Prey content is never executed, and it is always treated as untrusted data.
- New behaviour comes with tests against the fixtures; see
  [tests/fixtures/README.md](tests/fixtures/README.md) for how the synthetic repositories work.
- Windows and Linux are first-class: use `pathlib`, avoid shell-only scripts.

## Reporting bugs

Open an issue with the command you ran, the target repository and, if there is one, the
`manifest.json` of the digest. For vulnerabilities see [SECURITY.md](SECURITY.md).
