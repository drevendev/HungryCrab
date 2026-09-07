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

## Releasing

Releases are cut by hand until [#14](https://github.com/drevendev/HungryCrab/issues/14) automates
them. Doing it by memory is how 0.2.2 came to be declared released in three documents and to
exist in none of them, so the steps live here.

A **milestone** in [docs/design/03-roadmap.md](docs/design/03-roadmap.md) and a **release** are
different things. Finishing a milestone does not oblige you to tag one; `CHANGELOG.md` names only
versions that have a tag.

1. Decide the version. On `master` it is always a development version, `X.Y.Z.dev0`; the release
   drops the suffix.
2. Bump it in **five** places, which must agree:
   - `pyproject.toml` → `[project].version`
   - `src/hungry_crab/__init__.py` → `__version__`
   - `.claude-plugin/plugin.json` → `version`, with `.dev` spelled `-dev.` (`0.3.0-dev.0`)
   - `.claude-plugin/marketplace.json` → the `crab` entry's `version` (**not** the top-level
     `metadata.version`, which is the marketplace's own schema version)
   - `uv.lock` → refresh with `uv sync` rather than editing it
3. Move the `[Unreleased]` entries under a new `## [X.Y.Z] - YYYY-MM-DD` heading, and update the
   link references at the bottom of `CHANGELOG.md`: `[Unreleased]` compares the new tag to `HEAD`,
   and the new version compares the previous tag to the new one.
4. Update the **Status** line in `README.md` and the `Released:` line in the roadmap.
5. Merge that pull request, then tag the merge commit and push the tag:
   `git tag -a vX.Y.Z -m "X.Y.Z" && git push origin vX.Y.Z`. `claude plugin tag --push` makes the
   plugin's own `crab--vX.Y.Z` tag.
6. `gh release create vX.Y.Z --notes-file` with the changelog section.
7. **Reopen `master`** with a `chore: open X.Y+1.0.dev0 on master` commit, bumping the same five
   places. Skipping this is what leaves every later commit reporting a released version — and the
   digest cache keys on that version, so two different crabs then look identical to it.

Step 2 is guarded: `tests/test_plugin.py` ties `__init__.py` to both plugin manifests and to
`pyproject.toml`, and CI runs `uv sync --locked`, which fails when `uv.lock` still holds the old
version. All five places therefore break the build if one of them is forgotten.

## Reporting bugs

Open an issue with the command you ran, the target repository and, if there is one, the
`manifest.json` of the digest. For vulnerabilities see [SECURITY.md](SECURITY.md).
