# Healthcare code sets (`sqllocks-shape-healthcare-codes`)

The API the healthcare payer domain (lane HC-domain, issue #45) builds on. Everything is
importable from `shape_healthcare_codes`. Nothing reads the network at import or at lookup
time: code sets are Arrow files built once by `shape healthcare-codes fetch` (or supplied by the
user, for licensed sets) and loaded from the data directory.

## Loading a code set

```python
from shape_healthcare_codes import load, available, CodeSystem, AssetMissing

icd = load(CodeSystem.ICD10CM)        # a CodeSet; raises AssetMissing if not built (the message
                                      # says whether to `fetch` it or bring your own file)
available()                           # {"icd10cm": "user", ...}: what is built here
```

Search order: `$SHAPE_HEALTHCARE_CODES_DIR`, else `$XDG_CACHE_HOME/shape/healthcare-codes`, else
`~/.cache/shape/healthcare-codes`; then the small subset inside the wheel. Each asset is
`<id>.arrow` (Arrow IPC, zstd) and `<id>.json` (release, source URL, source checksums, rows).

`CodeSystem` values (the asset ids): `icd10cm`, `icd10pcs`, `hcpcs2`, `ndc`, `rxnorm`, `pos`,
`hcc`, `ccsr`, `nucc_taxonomy`, `carc`, `rarc`, `icd10_who`, `icd10gm`, `icd10am`, `cpt`,
`snomed`, `revenue_codes`, `type_of_bill`.

## The common code model

Every code system loads with these columns, so a domain can switch systems (`DIAGNOSIS_SYSTEMS`,
`PROCEDURE_SYSTEMS` in `shape_healthcare_codes.model` list the interchangeable ones):

| column | type | meaning |
|---|---|---|
| `code` | string | no punctuation, upper case (`E119`) |
| `short_desc`, `long_desc` | string | descriptions |
| `leaf` | bool | can be reported (billable) |
| `valid_from`, `valid_to` | date32 | first and last day the code was in the release range built; `valid_to` null: current |

Systems add columns (ICD-10-CM: `chapter`, `block`, `category`, `ext7`, `laterality`,
`fy_valid_mask`, `fy_leaf_mask`).

`CodeSet` methods:

* `is_valid(code, on, leaf_only=False)`: date-aware validity. Where `fy_valid_mask` exists the
  answer is exact per US fiscal year (October 1 starts the next one); bit `fy - 2016`.
* `codes_on(on, leaf_only=True)`: every code valid on that day, as an Arrow array. A generator
  draws diagnoses from this, so every drawn code is valid and billable on its date of service.
* `get(code)`, `code in codeset`, `select(codes)`, `len(codeset)`, `column(name)`.

`normalize_code("e11.9") == "E119"`; `format_icd10("E119") == "E11.9"`; `fiscal_year(date)`.

## Validators

```python
icd10cm_valid_billable(icd, "E11.9", date_of_service)   # exists AND billable on that date
ndc_marketed(NdcIndex.from_table(load("ndc").table), "0002-3227-30", fill_date)
normalize_ndc("0002-3227-30") == "00002322730"          # 4-4-2, 5-3-2 and 5-4-1 -> 5-4-2
luhn_valid(npi); is_real_format_npi(npi); is_synthetic_npi(npi)
generate_synthetic_npis(n, seed)                        # distinct, deterministic
```

### NPI: the synthetic range

CMS ("Requirements for NPI and NPI Check Digit", 2004) says an NPI is nine digits plus a Luhn
check digit computed as if the prefix `80840` were present, and that NPIs "will initially be
issued with the first digit = 1 or 2". Generated NPIs therefore begin with `99`: they pass the
check digit (anything that only checks it accepts them), and they cannot be an assigned NPI while
real NPIs begin with 1 or 2. A validator that also requires a first digit of 1 or 2
(`is_real_format_npi`) rejects them on purpose. `[VERIFY]` CMS reserves the right to use other
first digits "when coordinated"; the prefix is one constant (`npi.SYNTHETIC_PREFIX`).

## Detectors (`shape.detectors`)

`icd10`, `ndc`, `npi`, `hcpcs`, `cpt`, `mbi` (Medicare Beneficiary Identifier), `member_id`. Each
reports the share of sampled values that match as the confidence, returns `None` below 0.9, and
uses the column name to resolve ambiguous shapes (5-digit CPT versus ZIP).

## Licences and bring-your-own

See the plugin's `THIRD_PARTY_NOTICES.md`. Free sets are fetched from the official source at a
pinned release; CPT, SNOMED CT, revenue codes, type of bill, NUCC taxonomy, CARC/RARC and the
international sets are loaded from a file the user supplies (`shape healthcare-codes byo`).
