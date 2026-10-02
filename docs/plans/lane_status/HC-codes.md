# HC-codes — issue #45, lane "reference code sets, validators and detectors" — status

Branch `lane/HC-codes` (from the lead's integration tree at abd78cb). Plugin:
`plugins/shape-healthcare-codes` (distribution `sqllocks-shape-healthcare-codes`). No D-xx/T-xx
decision, gate or tolerance is touched; §11 and §2.3 are unedited; `$SPINDLE_ROOT` untouched.
The issue was not commented on, labelled or closed. `scripts/check_plugin_skeletons.py` lists the
new distribution (its `EXPECTED` tuple, which T-09 describes as "first-party plugins", was the
only edit outside the plugin besides docs).

## For lane HC-domain: the API (pushed first)

**Documented in `docs/plugins/healthcare-codes.md`** (code model, loader, validators, NPI range,
detectors). Importable from `shape_healthcare_codes`: `load`, `available`, `CodeSet`
(`is_valid`, `codes_on`, `get`), `CodeSystem`, `icd10cm_valid_billable`, `ndc_marketed` /
`NdcIndex`, `normalize_ndc`, `generate_synthetic_npis`, `luhn_valid`, `is_synthetic_npi`,
`normalize_code`, `fiscal_year`. Free sets need `shape healthcare-codes fetch ASSET` first.

## Network

Reachable from this environment (2026-10-02): ftp.cdc.gov, www.cms.gov, www.fda.gov,
www.accessdata.fda.gov, www.nlm.nih.gov, download.nlm.nih.gov, www.nucc.org, hcup-us.ahrq.gov,
x12.org, icd.who.int, www.bfarm.de. **Blocked: www.ihpa.gov.au** (the proxy answered 502 to
CONNECT), so ICD-10-AM's licence text was not read and the loader is built against a hand-made
fixture: `[VERIFY]`.

## Licence findings (read from each source's own page on 2026-10-02; quotes in the plugin's
`THIRD_PARTY_NOTICES.md`)

| asset | source | release | licence finding | mode |
|---|---|---|---|---|
| ICD-10-CM | CDC/NCHS ftp | FY2016-FY2027 | CDC agency materials: public domain, attribution "Source: CDC" | fetch + shipped subset |
| ICD-10-PCS | CMS | FY2027 | page states no terms; US government work `[VERIFY]` | fetch |
| HCPCS Level II | CMS | Oct 2026 | page states no terms; zip's layout says CPT records are AMA-copyrighted; Level II file has letter codes only | fetch (letter codes only) |
| NDC | FDA | daily (dated build) | FDA website policy and openFDA terms: public domain / CC0 | fetch |
| RxNorm prescribable | NLM | 09082026 (MD5 pinned) | "no license required"; public domain core, acknowledgement text required | fetch |
| NUCC taxonomy | nucc.org | 26.1 | **AMA copyright; commercial use needs a licence** | **BYO** |
| Place of service | CMS | May 2024 | no terms stated `[VERIFY]` | fetch |
| CMS-HCC mappings | CMS | by model | no terms stated `[VERIFY]` | fetch |
| AHRQ CCSR | HCUP | v2026.1 | no terms stated for the tool `[VERIFY]` | fetch |
| CARC / RARC | X12 | current | **"All X12 work products are copyrighted"**, no grant | **BYO** |
| WHO ICD-10 | WHO | 2019 | WHO copyright; no licence on the page `[VERIFY]` | **BYO** |
| ICD-10-GM | BfArM | 2027 | "in the public domain", but a download creates "a contract of use between you and the BfArM" | **BYO** |
| ICD-10-AM | IHACPA | - | host blocked; licensed `[VERIFY]` | **BYO** |
| CPT, SNOMED CT, revenue codes, type of bill | AMA, SNOMED Intl, NUBC | - | licensed | **BYO** |

NPI: CMS, "Requirements for NPI and NPI Check Digit" (2004), read in full: nine digits plus a
Luhn check digit computed with the 80840 prefix; NPIs "will initially be issued with the first
digit = 1 or 2". Synthetic range is the NPIs that begin `99`. `[VERIFY]` CMS keeps the right to
use other first digits "when coordinated".

## Progress

- [x] Plugin skeleton, code model, loader API, NPI, NDC normalization, ICD-10-CM and NDC
  validators, detectors, API doc, notices. Checks run for this commit are listed below.
- [ ] Asset builders (ICD-10-CM per fiscal year, PCS, HCPCS II, NDC, RxNorm, POS, HCC, CCSR),
  BYO loaders, `shape healthcare-codes` command, age/sex edits, per-asset sizes and checksums.

## Checks (this commit)

Run in this session: `pytest plugins/shape-healthcare-codes/tests` (20 passed); `ruff check` and
`ruff format --check` on `plugins/shape-healthcare-codes`; `mypy --strict` on the plugin source
(8 files, no issues); `scripts/check_plugin_skeletons.py` (8 distributions OK);
`scripts/check_user_facing.py` (clean). The full set is run before the final push.
