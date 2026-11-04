# Third-party material index

This register records intentional copies and adaptations carried into HungryCrab.
Add a row in the same change as the material, using the source repository, immutable
commit, SPDX licence, transfer mode, affected file and adaptation. Keep the applicable
copyright, licence text, attribution and modification notices with the material.

The table indexes attribution; it does not grant permission or replace the retained
texts in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the verified COPY receipts
in the [tracked materialization receipts](https://github.com/drevendev/HungryCrab/blob/1e475622d0fe0dea6621307b9680eac493c4c1a7/.crab/attributions.json). Use `crab attribution --check`
to check those generated notices. Human-adopted documents may instead retain their
source attribution and licence link in the document itself, as identified below.
The project's original code remains under [MIT](LICENSE).

| Source | Immutable commit | SPDX licence | Mode | Material and changes |
| --- | --- | --- | --- | --- |
| [yamadashy/repomix](https://github.com/yamadashy/repomix) | `5f4923b92a6289cb6497666c6945e95752a1df90` | MIT | COPY | `.github/workflows/codeql.yml`: adapted for Python, master, immutable action pins and minimal permissions; original copyright and permission text retained in THIRD_PARTY_NOTICES.md. |
| [EthicalSource/contributor_covenant](https://github.com/EthicalSource/contributor_covenant) | `d947a0f0cd479bac3e8f1d2d617aa7f8e7fee5b4` | CC-BY-4.0 | COPY_FILE | `CODE_OF_CONDUCT.md`: Contributor Covenant 2.1; removed website frontmatter and set the owner-approved reporting contact. Original attribution and links retained. |

Contributor Covenant's document and [CC BY 4.0 grant at the same commit](https://github.com/EthicalSource/contributor_covenant/blob/d947a0f0cd479bac3e8f1d2d617aa7f8e7fee5b4/LICENSE.md)
are pinned together; [the original 2.1 document](https://github.com/EthicalSource/contributor_covenant/blob/d947a0f0cd479bac3e8f1d2d617aa7f8e7fee5b4/content/version/2/1/code_of_conduct.md)
identifies its authorship and prior work. Licence: <https://creativecommons.org/licenses/by/4.0/>.
The template's reporting-contact change is recorded above; no upstream source code is adopted.

## Audit boundary

The initial register was reconciled on 2026-11-04 against the tracked materialization
receipts, generated third-party notices, source credits and data assets at
`1e475622d0fe0dea6621307b9680eac493c4c1a7`, plus the document introduced in this patch.
The existing repomix adaptation was present before this register; its receipt and
full licence text are preserved. Installed dependencies are governed by their own
licences and lockfiles. Synthetic licence fixtures and canonical licence-recognition
strings are test/evidence material, not a claim that donor implementations were adopted.
Research citations and issue provenance alone do not constitute a material transfer.
