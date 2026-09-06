---
name: smoke
description: >
  Run the smoke suite against a deployed preview and report what broke.
  Use after a deploy, or when the user asks whether the preview is healthy.
allowed-tools:
  - Bash
  - Read
---

# Smoke

## Steps

1. Read the preview URL from the deploy output.
2. Run `pnpm smoke -- --url <preview>`.
3. Report failures with the request that produced them.
