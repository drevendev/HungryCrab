# AI configuration: portable content and platform evidence

`crab digest` records AI configuration in `ai.json` and `ai.md`. It reads structural metadata;
it does not install plugins, invoke hooks, follow manifest commands, or execute skill bodies.

## Portable skills do not identify a platform

A `skills/review/SKILL.md`, `.agents/skills/review/SKILL.md`, or generic `agents/reviewer.md`
can be shared between agent runtimes. The crab keeps these files in its skills/subagents lists,
but does not infer Claude from their presence alone. A portable-only repository can therefore
have a non-empty `skills` list and an empty `present` list. Its Markdown summary says that
portable content was found, rather than claiming that there are no skills.

Claude instruction files and Claude-scoped skills, subagents, commands, settings, and hooks
remain platform evidence. Existing Cursor rules, Copilot files, Codex configuration, and MCP
markers retain their existing behavior. The legacy skill `location` field describes packaging;
it is not a platform label.

## Native plugin manifests

The following exact, root-relative paths identify platform-specific configuration artifacts:

- Claude: `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
- Codex: `.codex-plugin/plugin.json`.
- Cursor: `.cursor-plugin/plugin.json` and `.cursor-plugin/marketplace.json`.

Each `plugin` entry records `path`, `tool`, `parse_error`, bounded scalar `name` and `version`,
and a bounded list of top-level `keys`. No description, hook command, or instruction body is
copied into that entry. `ai.md` lists the manifest path, inferred platform, and JSON parse status.

This is path evidence, not schema validation or proof of successful installation. A malformed
manifest still identifies a configuration artifact for its platform, but has `parse_error: true`
and no parsed metadata. JSON arrays and scalars are not accepted as manifest objects. An empty
JSON object is parseable; that does not make it a valid, usable plugin.

An arbitrary root `plugin.json` is not treated as Codex or Claude configuration. In particular,
HungryCrab's root portable Agent Plugins manifest is not platform-specific evidence. Nested
sample paths and lookalike directories do not match the native manifest paths above.

The miner uses eligible inventory files only: vendored and generated entries remain excluded.
It sorts eligible paths before extraction, so equivalent inventories produce the same ordering.
No transfer-mode decision, trust policy, network access, or prey execution is added by this
classification.
