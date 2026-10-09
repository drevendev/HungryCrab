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
2. Bump it in **six** places, which must agree:
   - `pyproject.toml` → `[project].version`
   - `src/hungry_crab/__init__.py` → `__version__`
   - `.claude-plugin/plugin.json` → `version`, with `.dev` spelled `-dev.` (`0.3.0-dev.0`)
   - `.claude-plugin/marketplace.json` → the `crab` entry's `version` (**not** the top-level
     `metadata.version`, which is the marketplace's own schema version)
   - `plugin.json` → the portable Agent Plugins manifest's `version`, also using `-dev.`
   - `uv.lock` → refresh with `uv sync` rather than editing it
3. Move the `[Unreleased]` entries under a new `## [X.Y.Z] - YYYY-MM-DD` heading, and update the
   link references at the bottom of `CHANGELOG.md`: `[Unreleased]` compares the new tag to `HEAD`,
   and the new version compares the previous tag to the new one.
4. Update the **Status** line in `README.md` and the `Released:` line in the roadmap.
5. In the **same release pull request**, add a second commit `chore: reopen next
   X.Y.Z.dev0`, updating the same six version places (regenerate `uv.lock` with `uv sync`).
   The first commit holds the release version and changelog; the second reopens the next
   development version, so the PR head passes the regular dev-version CI gate.
6. Merge this release PR using **Rebase and merge**, not squash: both commits must survive in
   order on `master`. GitHub will change their SHAs during rebase. Identify the **rebased release
   commit**, not `master` HEAD, and verify its version files and release changelog before tagging.
   Confirm `master` HEAD has the reopened development version and finished green required checks.
7. Tag the exact verified **release commit**, not the reopened `master` HEAD:
   `git tag -a vX.Y.Z <release-commit-sha> -m "X.Y.Z" && git push origin vX.Y.Z`.
   CI runs on `v*` tags and checks the version/changelog on the tagged commit; require a
   completed green tag run **before** `gh release create vX.Y.Z --notes-file ...`.
   Create the separate `crab--vX.Y.Z` plugin tag only after that, if applicable.

Do not squash the release/reopen pair, create a tag from the reopened head, or bypass
required CI. A release candidate or postponement of a milestone does not change these gates.

Step 2 is guarded: `tests/test_plugin.py` and `tests/test_agent_plugin.py` tie `__init__.py` to the
Claude and portable Agent Plugins manifests and to `pyproject.toml`, and CI runs
`uv sync --locked`, which fails when `uv.lock` still holds the old version. All six places
therefore break the build if one of them is forgotten.

## Reporting bugs

Open an issue with the command you ran, the target repository and, if there is one, the
`manifest.json` of the digest. For vulnerabilities see [SECURITY.md](SECURITY.md).
