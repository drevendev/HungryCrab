---
description: Sniff a repository (license, size, languages, verdict) before eating it
argument-hint: owner/repo
allowed-tools: Bash(crab:*), Bash(uv run:*)
---

Run `crab sniff $ARGUMENTS --host .` (if `crab` is not on PATH, run
`uv run --project "${CLAUDE_PLUGIN_ROOT}" crab sniff $ARGUMENTS --host .`).

Report in a short table: license and class, the verdict, the mode for this repository, size,
activity, and every warning. Do not catch or digest yet; end with the suggested next command
from the output.
