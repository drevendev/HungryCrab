# Taste Memory (0.5 development milestone)

The maw's confirmed decisions influence future menus and prey discovery. A multi-prey Feeder
meal compares several sources against one maw and keeps their provenance together. No model,
scheduler, provider publication or prey execution is started by learning, hunt or eat.

## Start with a reviewed profile

```console
crab profiles --json
crab init --maw . --profile auto
```

Profiles are `balanced`, `library`, `cli`, `service` and `frontend`. Auto reads only static maw
manifests. Review the generated `.crab.yml`: auto is a suggestion, not a project classification
authority. An existing file is never replaced without `--force`. Explicit hunger entries override
the selected profile. Every profile keeps code ideas-only and architecture issues-only; a profile
does not widen licensing or publication authorization. `balanced` preserves the earlier defaults.

## Record decisions, then compare again

```console
crab ledger mark crab:ci:ci.cache accepted --reason "Repeated installs dominate this CI"
crab ledger mark crab:deps:deps.example rejected --reason "No need in this maw"
crab compare example/prey --maw .
crab tune --maw . --json
```

Only `accepted`, `merged` and `rejected` are training labels. Serving an issue is a delivery event,
not an endorsement. The ledger preserves a prior explicit acceptance when it becomes `served`.
Marking `proposed` or `ignored` clears that feedback. A stable nutrient id supplies one current
decision: later sightings, repeated serving and duplicated sources never create extra votes.
Legacy accepted/merged/rejected entries remain usable; a legacy served entry carries no inferred
approval. Nutrient ids and ledger schema `/1` remain unchanged; `feedback` is an additive field.

```yaml
memory:
  enabled: true
  min_decisions: 3
  strength: 0.3
```

For a category with enough decisions, the acceptance posterior is `(accepted + 1) / (total + 2)`.
Its multiplier is `1 + strength * (2 * posterior - 1)`, bounded by `1 +/- strength`. Each comparison
recomputes from default scoring plus explicit owner overrides. It never compounds earlier learned
weights. Explicit category overrides win; learned weights cannot change hunger, a license ceiling,
effort, risk, serve authorization or the clean-room protocol. `memory.enabled: false` restores
unlearned scoring. Menus include the decision fingerprint, counts and effective category weights.

`crab tune --write` pins conservative category and trait overrides in `.crab.yml`; it does not
apply hunger or prey suggestions automatically. Repeating it with unchanged decisions leaves the
file byte-identical after the first run. It replaces only the scoring section and preserves the
surrounding comments, line endings and policy. Anchored or flow-style top-level YAML is refused
before a write; paste the proposed weights manually for those configurations.

Prey-specific learning families such as `history-lesson.*.fix-prone` aggregate distinct ids without
renaming them. Exact trait overrides win over family overrides. Generic maw-relative cards remain
one decision each. Uptake uses `same_stack`, `transferable` or `other_stack` from scoring; all three
weights now affect the corresponding cards.

Ledger writes use an atomic replacement. Unknown fields in a supported schema survive a save;
future schemas, duplicate ids, invalid statuses and malformed collections fail before rewrite.
A nonblocking OS lock protects replacement. A stale writer fails before overwriting newer
decisions; reload and reconcile its change before retrying. Process death releases the lock.

## Hunt from the maw's gaps

```console
crab hunt --for . --json
crab hunt --for . --query "topic:testing language:Python" --limit 5
```

Hunt digests only the explicit maw, builds missing-trait queries from its enabled hunger, and reads
GitHub repository-search metadata. It makes at most four requests with at most 100 results each.
It neither clones nor evaluates a found prey's code. Search topic matches suggest where to look;
they do not prove that a repository implements the missing trait. Stack compatibility and bounded
star signals rank the shortlist; confirmed earlier decisions adjust a previously visited prey's
fit when revisiting is explicitly enabled. README bodies and descriptions do not reach the report.

```yaml
hunt:
  queries: []                # derive up to four from gaps when empty
  exclude: [example/avoid]
  licenses: []               # optional SPDX metadata allowlist
  min_stars: 20
  max_repo_kb: 307200
  max_candidates: 50
  limit: 10
  include_seen: false
  allow_unknown_size: false
```

Hunt removes inactive, forked, nonpublic, excluded, previously eaten and oversized prey. Unknown
size is excluded unless explicitly allowed. The report retains query counts, GitHub's incomplete
flag, filtering reasons and truncation. A failed request is an error, never an empty successful
search. Search licenses are provisional metadata under the foreign relationship: pinned digest
licensing must still be checked before serving. Discovery currently supports GitHub; Feeder
continues to accept local, GitHub and GitLab prey.

## Compare several prey in one meal

```console
crab eat example/one example/two --deterministic --maw . --out ../meal
crab menu --meal-dir ../meal --json
crab serve --meal-dir ../meal --maw . --top 3 --as dry-run
```

Two to ten distinct prey are exported atomically. If a source fails, the aggregate is not published.
Each `sources/<id>/` is a complete ordinary Feeder bundle with its pinned digests, menu and meal.
The aggregate `menu.json` uses `hungry-crab.menu/1` with `kind: multi-prey`; `feeder.json` uses
`hungry-crab.feeder-multi/1` and records every source file hash. Markdown is paged without dropping
cards. Reading and serving rebuild the aggregate from its verified source menus.

Generic cards deduplicate by stable nutrient id; prey-specific lessons stay distinct. Every merged
card retains all original cards, commits, evidence, origins and license modes in `trace.sources`.
The representative is the most restrictive source, with deterministic tie-breaking. It retains
that source's score: corroboration does not grant extra votes or a more permissive license.

`serve --meal-dir` supports dry-run, issues and receipt-backed PR branches through the existing
per-source policy and verification. Selection is made once across the aggregate. PR limits apply
to the whole invocation; current maw hunger still applies. COPY verifies the selected source at
its pinned commit; REIMPLEMENT retains clean-room receipts. Source hashes detect partial edits;
they are not signatures or a substitute for protecting the maw's own files.

## Scheduled HUNT

Set maw-owned `loop.discovery: true` to opt in. A nonempty fixed `loop.prey` remains an allowlist.
With an empty list, the current discovery shortlist is the allowlist for that HUNT lease.

```console
crab loop next --maw . --json
crab loop hunt --maw . --token <active-token> --json
crab loop record --maw . --token <active-token> --phase hunt --result ok --receipt chosen.json
```

`chosen.json` is `{"prey": ["example/one"]}`. Discovery does not advance a phase. Recording checks
the lease, round, unchanged maw policy, distinct selection and prey budget. An expired or retried
lease needs a fresh search. No scheduler starts and no merge is performed.

## Acceptance evidence

Deterministic regressions cover neutral/small samples, reversals, explicit overrides, repeat
sightings, served feedback, tuning idempotency, strict config, ledger compatibility, bounded
discovery, lease selection, aggregate licensing, provenance, integrity and failed-source rollback.
The existing B1 and ranking benchmarks retain their frozen thresholds and labels.

These tests establish correctness, not real-world usefulness lift. The live held-out evaluation is
[#256](https://github.com/drevendev/HungryCrab/issues/256); complex YAML editing is
[#258](https://github.com/drevendev/HungryCrab/issues/258). The paid B2 baseline and human
audit in #250 remain open. A live Taste Memory comparison needs a frozen 0.4 baseline, separate
training and held-out meals, independently recorded owner decisions, uptake/noise metrics and
publication traces. Release and longitudinal rollout gates in #224 and #242 remain separate.
