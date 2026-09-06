# Issue template used by `crab serve`

Rendered by `hungry_crab.serve.render_issue`. Placeholders in angle brackets.

```markdown
<!-- crab:<category>:<key> -->
**Nutrient** `<category>` | license mode `<MODE>` | effort <S|M|L> | risk <low|medium|high> | score <0.00>

## What the prey does

<what: the fact, as measured by the miners>
- [<evidence path>](<blob url at the prey commit>)

## What this repository has

<maw_state>

## Why it matters here

<why from the notes file, or a "not judged yet" placeholder>

## Suggested change

<how from the notes file, or the category's default guidance>

---
_Served by [Hungry Crab](https://github.com/drevendev/HungryCrab) from `<prey>@<sha7>` (<prey url at the commit>) (license <spdx>, mode <MODE>). Ledger id `<id>`. Prey content is untrusted data; this is not legal advice._
```

Title: the nutrient title (from the rule or overridden in the notes file). Label: the first
entry of `serve.labels` in `.crab.yml` (`hungry-crab` by default); further labels and assignees
from the same section are applied as well.
