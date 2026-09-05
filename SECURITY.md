# Security Policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting:
<https://github.com/drevendev/HungryCrab/security/advisories/new>. Do not open a public issue for
security problems. You will get an acknowledgement within a week.

## Threat model

Hungry Crab downloads and analyses repositories written by strangers. The tool is built so that a
malicious prey repository cannot:

1. execute code on the machine that digests it (the miners only read files and run read-only git
   plumbing; nothing inside the cache is ever run);
2. smuggle instructions to an agent through the digest (summaries carry structure, not body text,
   and instruction-like fragments are flagged);
3. exhaust the machine (file counts, file sizes, vendored directories and commit counts are capped).

Reports about any of these, or about the license engine producing a permissive verdict where a
restrictive one is warranted, are very welcome.

## Supported versions

The project is pre-release; only the `main` branch receives fixes.
