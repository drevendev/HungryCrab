# Deep Bite (0.4)

Deep Bite adds syntax evidence, eight ecosystem manifests and provider signals to the
deterministic pipeline. It is development functionality, not a release announcement.

## Install and use

The basic CLI still depends only on PyYAML. Install the optional, locked grammar packages for
syntax analysis:

```text
uv sync --all-extras
uv run crab catch owner/repo --shallow --issues 100 --discussions 25 --reviews 100 --runs 10
uv run crab digest owner/repo --maw . --depth deep
uv run crab compare owner/repo --maw .
uv run crab eat https://gitlab.com/group/project --deterministic --maw . --allow-unknown-size
```

For a tool install, request the `deep` extra on the same pinned source/version you already
use. No dependency is installed inside a prey clone. `--discussions`, `--reviews` and `--runs`
are opt-in counts (0..3000) on catch, digest, compare and eat. A digest with explicit acquisition
counts refreshes those channels even when the clone already exists. Discussions require GitHub
authentication. Review prose stays in untrusted JSON; Markdown carries counts and paths only.

## Syntax contract

`symbols.json` holds declarations, kinds, qualified names, parameter syntax-node counts, source line ranges,
lexical calls and edges. `symbols.md` pages the declaration index. Python, JavaScript,
TypeScript/TSX, C#, Go, Rust, Java, PHP and Ruby use their official tree-sitter grammars.
The existing `architecture.json` remains compatible; new architecture cards reference precise
declarations rather than changing legacy nutrient ids.

The graph records unambiguous same-file lexical references. Imports, member dispatch,
overloads and dynamic calls remain unresolved. It is not a whole-program runtime graph or a
claim that a named function implements an algorithm. `code` cards are review subjects for
callable boundaries with callers in a language indexed in the maw; an architect must decide
whether carrying the idea over is useful. Identical qualified names already in the maw suppress
the proposal. Cards carry line links at the prey commit, a stable prey-specific id, and the
graph interpretation in their trace. They start as ideas. License review flags or applicable
file exceptions narrow syntax-backed subjects to HUMAN.

Parsers run in a trusted isolated Python subprocess, with credentials removed, a grammar
allowlist, bounded input/output, node traversal and a ten-second worker lease. Workers receive
data over stdin; no prey module is imported or executed. Native crashes, timeouts, syntax
errors, oversized files, unsupported languages and missing extras have distinct coverage
statuses. These do not become successful empty declarations. Normal/deep file caps are
1000/8000; each source is at most 400 KB; batches are at most sixteen files. Inspect the
coverage block before treating an index as complete. Parser and grammar versions, including
missing extras, and all acquired API data participate in digest cache identity.

## Ecosystems and providers

Dependency facts cover Python, npm, .NET, Go, Rust, Ruby, JVM and PHP. Maven namespaces,
local properties and dependency management are read statically. Parent POMs are not fetched.
Gradle literal coordinates are read, with unresolved catalogs/interpolation counted explicitly.
Composer distinguishes platform requirements from packages. Rust records workspace dependency
references and target-specific requirements; an inherited workspace version is not guessed or
called pinned. Go reads require directives, never replace/exclude blocks. Ruby uses the existing
Gemfile/gemspec/lock enrichment. No package manager is launched.

GitHub shorthand remains `owner/repo`. GitLab.com accepts HTTPS and SSH URLs with nested
namespaces. Its cache uses `gitlab/`, separate from `github/`; evidence URLs use `/-/blob/`.
`GITLAB_TOKEN` is optional for public reads and is scoped to GitLab in process-local Git headers.
Ownership trust is forge-qualified (`gitlab.com/group`) so equal account names on different
forges cannot bypass licensing. GitLab supports repository/language metadata, issues, merge
request reviews, pipelines and independent wikis. GitHub Discussions have no GitLab equivalent
in this protocol and report unsupported. GitLab review threads and pipeline jobs can require a
`GITLAB_TOKEN` with read_api permission even when repository metadata and Git objects are public.
Self-hosted instances and GitLab maw publication are
outside this fetch adapter; serving explicitly refuses them instead of sending a malformed gh
command. Analysis and dry-run meals remain available.

GitLab often withholds repository size from anonymous clients. Sniff reports unknown; Feeder
refuses its size preflight unless `--allow-unknown-size` is explicit. Known size remains a
preflight, not a hard download quota ([#236](https://github.com/drevendev/HungryCrab/issues/236)).

## CI reliability evidence

`signals.json` keeps channel samples and provenance. Run statistics include failures, median
durations and jobs that failed in the previous attempt and succeeded on rerun. GitHub duration
is the sum of job seconds, not parallel wall time. GitLab duration is provider pipeline time.
Each sample records truncation; acquisition failures fail the requested command.

A job retry does not prove a flaky test. Test-level counts require JUnit's explicit
`flakyFailure`/`flakyError` markers, with no final failure, in named JUnit/test-results/Surefire
artifacts. Archives are parsed in memory, never extracted, with compressed/uncompressed/XML and
case limits; DTDs and entities are refused, including UTF-16 encodings. Archive reads use gh's
authenticated redirect handling and require actions:read. Clients without it report unavailable
test-report coverage. Missing reports yield an unknown flaky count, not zero. Logs, failure
prose and arbitrary artifact programs never become digest Markdown.

## Measured quality

The human B1 corpus is unchanged. A separate synthetic pressure pair produces more than fifty
candidates, so top-30 now tests ranking as well as rule activation. Its generic priorities come
from prior human acceptance, not a new invented usefulness golden set. An intentionally broken
scoring configuration must fail that gate. Real human labeling of a larger corpus remains part
of [#200](https://github.com/drevendev/HungryCrab/issues/200).

The [B2 operator guide](../benchmarks/README.md) describes the full frozen experiment pipeline.
Synthetic transcript tests validate that pipeline, not model quality or cost. The first live
sweep and human audit are [#250](https://github.com/drevendev/HungryCrab/issues/250), under the
existing release-evidence gate [#224](https://github.com/drevendev/HungryCrab/issues/224).
