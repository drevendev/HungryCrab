---
name: license
description: Decide what may be carried over from a prey repository under its license. Modes are COPY, COPY_FILE, REIMPLEMENT, IDEAS_ONLY and HUMAN. Use when a nutrient's license mode is unclear, when asked whether code or configs from another repository may be copied, or when a sniff verdict says HUMAN.
---

# License rules

The verdict is produced by a deterministic engine (`crab sniff`, `license.json` in a digest).
Your job is to apply the mode, explain it, and stop when the engine says `HUMAN`.

## Modes

| Mode | Allowed | Required |
|---|---|---|
| `COPY` | copy code, configs and text | keep the copyright notice; record the source in `THIRD_PARTY_NOTICES.md`; Apache-2.0 also needs the NOTICE file carried over |
| `COPY_FILE` | copy whole files (MPL-2.0, EPL, CC-BY-SA documents) | the file keeps its own license header; do not merge it into files under the maw license |
| `REIMPLEMENT` | use the prey as a specification | clean room: a spec without code, then an implementer without access to the prey (the `crab-cleanroom-impl` subagent from 0.3); record "implemented from a specification" in the provenance |
| `IDEAS_ONLY` | ideas, architecture, approaches, facts | not a line of code, configuration or documentation text |
| `HUMAN` | nothing yet | a person decides; present the evidence (`license.json`: files, manifests, headers, conflicts) |

## Rules that override the matrix

1. Issue, discussion and pull-request comment text is always `IDEAS_ONLY`: the copyright belongs
   to the commenters. Carry over the need and a link, not the text.
2. Configuration files and small snippets are not automatically free: same mode as code.
3. A maw in `strict` mode (`.crab.yml`) downgrades `COPY` to `REIMPLEMENT` for code; only configs
   and templates are copied.
4. Per-file exceptions in `license.json` (vendored directories, headers with another SPDX id)
   override the repository license for those files.
5. A conflict between the LICENSE file and a manifest, or no license at all, is `HUMAN`.

## How to compute a mode

- `crab sniff <prey> --maw .` prints the mode for this maw.
- `license.json` in the prey digest has `modes_by_maw_class` (permissive, gpl, proprietary maws)
  and `verdict` when a maw license was known at digest time.
- The full matrix is in `references/matrix.md`.

This is a compliance aid, not legal advice; say so when the user asks about an edge case.
