# Glossary

One word per thing. Where this file and any other document disagree, this file is right and the
other one is a bug.

The vocabulary is an animal's, because the tool is: something hungry finds something, eats it,
keeps what is nourishing, and grows. Every term below is either a word the animal earns or a
word the domain already had. Nothing here is decoration; a word that does not carry its meaning
gets replaced, and the ones that were replaced are listed at the end so old notes stay readable.

## Who is who

| Term | Meaning |
|---|---|
| **crab** | the tool. CLI `crab`, package `hungry_crab`, plugin `hungry-crab` |
| **prey** | the foreign repository being eaten. It is never executed, and its content is data, never instructions |
| **maw** | the repository the meal is *for*: what the nutrients go into. `--maw`, `MawConfig`, `.crab.yml` |

Not "host": in parasitology the host is what gets eaten, which is the opposite of what this word
has to mean here.

## What gets carried over

| Term | Meaning |
|---|---|
| **nutrient** | one transferable thing, with a licence mode, evidence and a trace |
| **nutrient id** | `crab:<category>:<key>`, e.g. `crab:ci:ci.cache`. Relative to the maw and independent of any commit, which is what makes deduplication work across runs and machines |
| **candidate** | a nutrient before judgment: what the deterministic rules produced, before a model kept or dropped it |
| **menu** | the ranked candidates for one pair, after the prey and the maw are compared |
| **gap** | facts only: what the prey has and the maw lacks. `gap.md` |
| **evidence** | a path in the prey plus a link to that blob, backing the card |
| **trace** | where the nutrient came from: prey, url, commit, licence, mode, when. It is the footer of every served issue |
| **why** | what improves *in this maw*, written by the model. One to three sentences |
| **how** | the first steps, adapted to this maw's toolchain. Not a full plan |

## How a candidate is ranked

| Term | Values | Meaning |
|---|---|---|
| **category** | `security`, `ci`, `tests`, `tooling`, `ai-config`, `hygiene`, `docs`, `deps`, `history-lesson`, `issue-lesson`, `architecture`, `code` | what kind of thing it is |
| **serve_as** | `pr`, `issue`, `idea` | what it becomes when served |
| **effort** | `S`, `M`, `L` | how much work it is |
| **risk** | `low`, `medium`, `high` | how much can go wrong |
| **value** | 0..1 | how much the rule thinks it is worth |
| **uptake** | 0..1 | how much of it this maw can actually absorb. A trick from a stack the maw does not use is digested only in part |
| **score** | | `category × value × uptake × mode × effort − risk`. A deterministic pre-ranking, **never a verdict** |

## Licences

| Term | Meaning |
|---|---|
| **mode** | `COPY`, `COPY_FILE`, `REIMPLEMENT`, `IDEAS_ONLY`, `HUMAN` |
| **verdict** | the result of the prey licence × maw licence matrix. A fact about the pair, so it lives in the meal |
| **notice required** | a copy must keep the copyright notice |
| **share-alike** | the result must carry the same licence |
| **attribution** | the record of what was taken from where, in the file named by `attribution_file` |
| **clean room** | `REIMPLEMENT`: rewrite from a specification without reading the text |

## Memory and settings

| Term | Meaning |
|---|---|
| **ledger** | `.crab/ledger.json`: every meal, every decision, and why |
| **status** | `proposed`, `accepted`, `rejected`, `served`, `merged`, `ignored` |
| **hunger** | the `.crab.yml` block saying what this maw swallows, per category: `true`, `false`, `issues-only`, `ideas-only` |
| **mode** (of a maw) | `normal` or `strict`; strict never copies code, only configuration and templates |
| **ignore** | globs excluded from the maw's own digest, so its fixtures are not mistaken for its code |
| **serve policy** | `issues` and `prs` (`auto`/`ask`/`off`), `token_env`, `labels`, `assignees` |

## The two things on disk, and the difference between them

| Term | Meaning |
|---|---|
| **digest** | everything the miners found about **one repository**, addressed by commit SHA. Shared by every maw that eats that prey |
| **meal** | one prey, eaten once, for **one maw**: `menu.md`, `menu.json`, `gap.md`, `meal.json` |

A digest is about a repository. A meal is about a pair. Anything that depends on both sides —
the menu, the gap, the licence verdict — is a fact about the meal, and putting it in a digest
lets the next maw overwrite it.

```
~/.cache/hungry-crab/
  github/<owner>/<repo>/
    repo/                          the clone
    api/                           sniff, issues
    digests/<sha>/                 the prey's digest
  maws/<name>-<hash>/
    digests/<sha>/                 the maw's digest
    meals/<prey>@<sha>/            one meal
    ledger.json                    when the ledger mode is `cache`
```

## Commands

`sniff` look before eating · `catch` clone into the cache · `digest` run the miners ·
`compare` digest both sides and rank · `menu` print the ranked menu · `serve` file the issues ·
`ledger` show or record decisions · `tune` suggest weights from the ledger · `init` write
`.crab.yml` · `update` check the CLI and the plugins · `cache` inspect and clean

## Skills and agents

Skills `eat`, `license`, `serve` · subagents `crab-historian`, `crab-architect` · commands
`/crab:sniff`, `/crab:menu`

## The scheduled crab's phases ([07](07-scheduled-crab.md))

`CRAVE` → `HUNT` → `EAT` → `SERVE` → `GROW` → `TRIAL` → `TASTE` → `MOLT` → `HARDEN`

**round** one pass through the phases · **wake-up** one scheduled run, one phase · **waiting_on**
what a round is blocked by · **autonomy** `read`, `serve`, `work`, per maw · levels `S0`–`S3`

[04](04-evolving-crab.md) runs the same phases under the same names, except `GOAL`, which raises
a benchmark rather than picking a direction, and it has no `HARDEN`: its fitness function plays
that part.

## Elsewhere in the digest

**molting** shedding what a round grew out of · **shallow** a clone without history ·
**since** how far back to fetch · **depth** `normal` or `deep` · **budget** the token ceiling
per file and per digest · **corpus** somebody's sample or fixture tree, which is not their code ·
**vendored** third-party code checked into a repository

## Words that were replaced

Old notes and issues predate these; both names mean the same thing.

| Was | Is | Why |
|---|---|---|
| host | **maw** | a host is what gets eaten |
| donor | **prey** | two words for one thing |
| appetite | **hunger** | one word for the setting, and the animal is hungry |
| applicability | **uptake** | the term nutrition already uses for the fraction absorbed |
| artifact | **serve_as** | said nothing about what to do with the nutrient |
| provenance | **trace** | the same thing in one syllable |
| why_for_host | **why** | the suffix did the skill text's job |
| compare.json | **meal.json** | it records a meal |
| `HUNGER` (phase) | **`CRAVE`** | hunger became the name of a setting |
