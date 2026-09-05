# Hungry Crab

@AGENTS.md

## Claude Code specifics

- Run everything through `uv run ...`; the project environment is `.venv`, managed by uv.
- Use the fixtures under `tests/fixtures` in tests; never clone real repositories from a test.
- Anything under `~/.cache/hungry-crab/` is prey: read it, never run it.
- Chat with the maintainer may happen in Russian; everything committed to the repository is
  English.
