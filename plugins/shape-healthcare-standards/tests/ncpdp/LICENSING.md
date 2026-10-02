# NCPDP: licensing finding

Date of research: 2026-10-02. Everything below cites sources; no specification text is quoted
or paraphrased in this plugin.

## Conclusion (read this first)

**Nothing standard-conformant can be built openly.** The NCPDP Telecommunication Standard
(version D.0 and its successor F6), its data dictionary and its external code lists are
licensed, member-access NCPDP publications. The field identifiers, the segment layouts, the
required/situational rules and the code values are all inside those documents. The only
openly available derivatives (public payer sheets) carry NCPDP's own copyright notice, so
they are not a safe basis for a shipped field table either.

What is built instead is a **bring-your-own-specification (BYO) layer**: the licensed user
supplies a JSON layout, and the plugin supplies the mapping from the input table contract
and the generic delimited wire framing. The lead should treat this as an escalation: a
default, standard-conformant NCPDP output is not deliverable without an NCPDP licence
decision by the owner.

## Sources

| # | Source | URL | Accessed | What it says / what we concluded |
|---|--------|-----|----------|----------------------------------|
| 1 | NCPDP, "Telecommunication Standard Version D and above" Q&A (member site) | https://member.ncpdp.org/Member/media/pdf/VersionDQuestions.pdf | 2026-10-02 | The Telecommunication IG, Batch IG, External Code List and Data Dictionary are obtained through NCPDP membership. The notice limits use to members' own business purposes and bars passing the publications on to third parties without NCPDP's written authorisation. Conclusion: a spec-derived table may not be redistributed in this open plugin. |
| 2 | NCPDP resources / standards pages | https://www.ncpdp.org/resources.aspx | 2026-10-02 | Standards are offered under NCPDP membership or licence terms. A search of the public site found no free download of the Telecommunication Standard or data dictionary. (A direct fetch of ncpdp.org standards-info paths returned 404 from this environment; the terms are therefore taken from source 1.) |
| 3 | 45 CFR 162.1102 (HIPAA standards for health care claims), consolidated text via Cornell LII mirror of the eCFR | https://www.law.cornell.edu/cfr/text/45/162.1102 (federal source: https://www.ecfr.gov/current/title-45/part-162/section-162.1102) | 2026-10-02 | The rule adopts, for retail pharmacy drug claims, the NCPDP Telecommunication Standard Implementation Guide Version D Release 0 (August 2007) with the Batch Standard 1.2, and, for later periods, Version F6 (January 2020) with Batch 15. It adopts them by reference (§ 162.920); the regulation names the standards but does not reproduce their field tables. Conclusion: the rule gives no open field list. |
| 4 | Kansas Medicaid (KMAP) "NCPDP Version D.0 and 1.2 Transactions Payer Sheets" (a public payer document) | https://www.kmap-state-ks.us/Documents/EDI/2011-1221%20NCPDP%20D%200.pdf | 2026-10-02 | Public. Describes the framing: a fixed-width header segment without field separators, then segments each introduced by a segment identification, fields each preceded by a field separator and the field's identifier, and the three separators: segment 0x1E, group 0x1D, field 0x1C. It also carries NCPDP's copyright and trademark notice and says all rights remain with NCPDP. Conclusion: the separator byte values (framing) are confirmed from a public payer document and are used here; the payer sheet's field tables are NCPDP-derived and are not used. |
| 5 | NCPDP Payer Sheet Template Implementation Guide for D.0 | https://ncpdp.org/NCPDP/media/pdf/Payer_Sheet_Template_1.pdf | 2026-10-02 | The template that payers fill in, which explains why payer sheets are structurally NCPDP material. Not used beyond this observation. |
| 6 | apiv/dzero, open-source Ruby D.0 parser/serializer | https://github.com/apiv/dzero | 2026-10-02 | Public README shows the control-character framing (field separator 0x1C, segment 0x1E) and 2-character field ids. It does embed a field map for the D.0 segments. Licence file could not be retrieved from this environment (the licence API was blocked and the raw LICENSE path returned 404), so its licence is unconfirmed. Conclusion: not used, and not copied from; the third-party field map is no safer than the standard itself. |
| 7 | cosyte/ncpdp, open-source TypeScript parser (D.0 / F6 and SCRIPT) | https://github.com/cosyte/ncpdp | 2026-10-02 | MIT licensed. Its README states that the implementation guides are purchased products and it mentions FS/GS/RS framing. The MIT licence covers its code, not NCPDP's field tables. Conclusion: confirms the framing; nothing was copied from it. |
| 8 | NCPDP data dictionary / SCRIPT standard | (member access only, same terms as source 1) | 2026-10-02 | SCRIPT (ePrescribing) is likewise a licensed NCPDP standard. Out of scope here; nothing built. |

Not verified: the exact text of NCPDP's public licensing terms page, which was not reachable.
No claim about a purchase price or a non-member licence route is made.

## What is built

* `ncpdp/layout.py`: the data model and hand-written validator for a user-supplied JSON layout
  (transactions, segments, fields, usage, source column/constant/expression, formats
  alphanumeric/numeric/date CCYYMMDD/amount with implied decimal, signed overpunch, zero pad,
  max length, separators).
* `ncpdp/wire.py`: writer and parser of the generic framing (fixed-width header, group
  separator 0x1D per transaction, segment separator 0x1E, field separator 0x1C, field = 2-character
  id + value). Separators come from sources 4 and 7 and can be overridden by the layout.
* `ncpdp/mapping.py`: applies a layout to `pharmacy_claim` rows, with `member`, `provider`
  (pharmacy and prescriber) and `drug_reference` companions. Request and response transactions
  are the same mechanism: a response transaction is just a layout transaction that reads
  `claim_status`, `reject_code` and `plan_paid`.
* `ncpdp/sink.py`: `NcpdpSink` (`name = "ncpdp"`), one transmission file per claim, atomic.
* Tests use `synthetic_layout.json` here, whose ids (`X0`, `X1`..., `S1`..., `T1`, `T2`) are
  made up and deliberately not the standard's.

## What is NOT built, and why

* **No default or example layout**, and no NCPDP field table, segment list, transaction code list
  or code values. Every candidate source is either licensed (sources 1, 2, 8) or an NCPDP-copyright
  derivative (4, 5) or a third-party transcription of them (6). A layout built from them could not
  be cited as free of NCPDP text.
* No conformance claim of any kind. Output is "NCPDP-style framing with a user-supplied
  layout"; it is not validated against D.0 or F6 and must not be sent to a payer on that basis.
* No SCRIPT, no batch (file-level) envelope, no transmission-level segments, no multi-transaction
  eligibility-style transmissions, no response-side parsing of payer-specific rules.

## BYO workflow for a licensed user

1. Obtain the standard and data dictionary from NCPDP under your own membership or licence.
2. Write a layout JSON (shape documented in `ncpdp/layout.py`): header fields with their widths,
   one entry per transaction code, segments and fields in your required order, each field's
   source contract column (for example `pharmacy_claim.ndc`, `member.birth_date`,
   `pharmacy.ncpdp_id`), `usage`, format and, where a code list translation is needed, a `map`
   from contract values to your codes. The layout file is yours; keep it out of any public
   repository if your licence says so.
3. Validate it: `Layout.load("my_layout.json")` reports every problem at once.
4. Write: `NcpdpSink(layout="my_layout.json").write(out_dir, "pharmacy_claim", batches,
   tables={"member": ..., "provider": ..., "drug_reference": ...})`, or call
   `write_transaction(rows, layout, "<code>", tables=...)` for bytes.
5. Conformance testing against your payer or a certified tool is your responsibility.
