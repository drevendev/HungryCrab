# AI configuration metadata boundary

Structural JSON metadata is still prey-controlled text, not an instruction to the maw.
The AI-config miner applies the existing scalar filter to settings member names, hook-event
names, permission default modes, MCP server names and transports, and plugin manifest keys.
Plugin names and versions retain the same filter.

Each retained string is whitespace-normalized and limited to 160 characters. Strings flagged
by the existing instruction-like detector become `[omitted: instruction-like]`. Complex
permission-mode and transport values are not converted to strings. An absent, null or empty
transport retains the existing `stdio`/`http` inference; an explicit non-string type is omitted.

Settings keys and hook events retain at most 30 entries; MCP server names, distinct transports
and plugin keys retain at most 20. Ordering is deterministic. These are digest-output bounds,
not a replacement for input-file size limits, a universal prompt-injection defense, or
permission to execute anything from a prey repository.

Regression tests use real `MineContext`, `AiConfigMiner` and the existing safety filter. They
cover JSON and rendered Markdown output, ordinary configuration names, complex scalar types,
length limits, count limits and deterministic ordering. Platform inference remains governed by
[platform evidence](ai-config-platform-evidence.md); this change does not alter native paths.
