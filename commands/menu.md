---
description: Show the ranked menu of nutrients from the last compare of a prey
argument-hint: owner/repo [--top N] [--category ci,tests]
allowed-tools: Bash(crab:*), Bash(uv run:*)
---

Run `crab menu $ARGUMENTS` (or `uv run --project "${CLAUDE_PLUGIN_ROOT}" crab menu $ARGUMENTS`
when `crab` is not on PATH).

If it reports that there is no menu yet, run `crab compare <prey> --host . --issues 300` first
and then the menu again. Present the ranked candidates as a table (rank, score, category,
nutrient, mode, effort, artifact, id) and remind the user that the `eat` skill judges and
serves them.
