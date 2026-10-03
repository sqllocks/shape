# Healthcare code sets (`sqllocks-shape-healthcare-codes`)

The API a domain pack builds on. Everything is importable from `shape_healthcare_codes`.
Nothing reads the network at import or at lookup time: code sets are Arrow files built once by
`shape healthcare-codes fetch` (free sets) or `shape healthcare-codes byo` (licensed sets you
supply), and loaded from the data directory.

Licences, sources and releases: the plugin's `THIRD_PARTY_NOTICES.md` and
`shape healthcare-codes notices`. Nothing licensed is shipped; only a starter subset of ICD-10-CM
(0.6 MB) is inside the wheel.

## Loading a code set

```python
from shape_healthcare_codes import load, available, CodeSystem, AssetMissing

icd = load(CodeSystem.ICD10CM)   # a CodeSet; raises AssetMissing if not built (the message says
                                 # whether to `fetch` it or bring your own file)
available()                      # {"icd10cm": "shipped", "ndc": "user", ...}
```

Search order: `$SHAPE_HEALTHCARE_CODES_DIR`, else `$XDG_CACHE_HOME/shape/healthcare-codes`, else
`~/.cache/shape/healthcare-codes`; then the subset inside the wheel (so `fetch icd10cm` replaces
the subset with the full set). Each asset is `<id>.arrow` (Arrow IPC, zstd) and `<id>.json` (its
manifest: release, source URL, SHA-256 of every source file, rows).

`CodeSystem` values (the asset ids):

| id | what | how you get it |
|---|---|---|
| `icd10cm` | ICD-10-CM, FY2016 to FY2027 | starter subset shipped; `fetch icd10cm` for all 99,085 codes |
| `icd10pcs` | ICD-10-PCS, FY2021 to FY2027 | `fetch` |
| `hcpcs2` | HCPCS Level II codes and modifiers (`kind` column) | `fetch` |
| `ndc` | FDA NDC directory, one row per package, 11-digit | `fetch` |
| `rxnorm`, `rxnorm_ndc` | RxNorm prescribable concepts, RxCUI to NDC | `fetch rxnorm` |
| `pos` | CMS place-of-service codes | `fetch` |
| `hcc` | ICD-10-CM to CMS-HCC V22/V28, ESRD V21/V24, RxHCC V08 (long table) | `fetch` |
| `ccsr` | ICD-10-CM to AHRQ CCSR categories (long table) | `fetch` |
| `mce_edits` | age and sex edits (see below) | `fetch` |
| `nucc_taxonomy`, `carc`, `rarc` | provider taxonomy, denial reason and remark codes | `byo` |
| `icd10_who`, `icd10gm`, `icd10am` | WHO ICD-10, ICD-10-GM, ICD-10-AM | `byo` (ClaML or a delimited file) |
| `cpt`, `snomed`, `revenue_codes`, `type_of_bill` | licensed sets | `byo` |

## The common code model

Every code system loads with these columns, so a domain can switch systems
(`DIAGNOSIS_SYSTEMS` and `PROCEDURE_SYSTEMS` in `shape_healthcare_codes.model` list the
interchangeable ones: `load(CodeSystem.ICD10GM)` is used exactly like `load(CodeSystem.ICD10CM)`):

| column | type | meaning |
|---|---|---|
| `code` | string | no punctuation, upper case (`E119`) |
| `short_desc`, `long_desc` | string | descriptions |
| `leaf` | bool | can be reported (billable) |
| `valid_from`, `valid_to` | date32 | first and last day the code was in a release this build covers; `valid_to` null: current |

A system adds columns: ICD-10-CM has `category`, `chapter`, `chapter_desc`, `block`,
`block_desc`, `ext7` and `ext7_meaning` (the 7th-character extension and its definition),
`laterality` (`right`, `left`, `bilateral`, `unspecified`) and the release masks; ICD-10-PCS has
`section`; HCPCS has `kind` (`code` or `modifier`), `betos`, `coverage_code`; NDC has the
product attributes (`proprietary_name`, `dosage_form`, `route`, `dea_schedule`, ...).

`CodeSet` methods:

* `is_valid(code, on, leaf_only=False)`: date-aware validity. Where the table has release masks
  (`valid_mask`, `leaf_mask`; bit `i` is release `i` of `CodeSet.releases`) the answer is exact
  per release; otherwise it uses `valid_from` / `valid_to`. ICD-10-CM changes every October 1
  and, for three updates, mid-year (2021-01-01, 2022-04-01, 2023-04-01); each is a release.
* `codes_on(on, leaf_only=True)`: every code valid on that day, as an Arrow array. A generator
  draws diagnoses from this, so every drawn code is valid and billable on its date of service.
* `validity_table(codes=None)`: the per-release validity of codes as a long table (`code`,
  `release`, `effective`, `valid`, `leaf`).
* `get(code)`, `code in codeset`, `select(codes)`, `len(codeset)`, `column(name)`, `release_index`.

`normalize_code("e11.9") == "E119"`; `format_icd10("E119") == "E11.9"`; `fiscal_year(date)`.

## Validators

```python
icd10cm_valid_billable(icd, "E11.9", date_of_service)   # exists AND billable on that date
ndc_marketed(NdcIndex.from_table(load("ndc").table), "0002-0152-01", fill_date)
normalize_ndc("0002-3227-30") == "00002322730"          # 4-4-2, 5-3-2 and 5-4-1 -> 5-4-2
luhn_valid(npi); is_real_format_npi(npi); is_synthetic_npi(npi)
generate_synthetic_npis(n, seed)                        # distinct, deterministic
edits = EditIndex.from_table(load("mce_edits").table)
age_sex_violations(edits, "O80", 30, "M")               # ["sex:F"]; [] means plausible
```

### Age and sex edits

From CMS's "Definitions of Medicare Code Edits": ICD-10-CM codes limited to the perinatal period
(age 0), children (0-17), the maternity age range (9-64) or adults (15-124); and codes (and, for
ICD-10-PCS, procedures) limited to females or to males. CMS deactivated the sex-conflict edit on
2024-10-01 and stopped listing it, so the sex lists are those of v41.1, the last version that
had them; codes added after FY2024 are not on them. Sex outside "F" and "M" is not checked.

### NPI: the synthetic range

CMS ("Requirements for NPI and NPI Check Digit", 2004) says an NPI is nine digits plus a Luhn
check digit computed as if the prefix `80840` were present, and that NPIs "will initially be
issued with the first digit = 1 or 2". Generated NPIs therefore begin with `99`: they pass the
check digit (anything that only checks it accepts them), and they cannot be an assigned NPI while
real NPIs begin with 1 or 2. A validator that also requires a first digit of 1 or 2
(`is_real_format_npi`) rejects them on purpose. `[VERIFY]` CMS keeps the right to use other first
digits "when coordinated"; the prefix is one constant (`npi.SYNTHETIC_PREFIX`).

## Detectors (`shape.detectors`)

`icd10`, `ndc`, `npi`, `hcpcs`, `cpt`, `mbi` (Medicare Beneficiary Identifier), `member_id`. Each
reports the share of sampled values that match as the confidence, returns `None` below 0.9, and
uses the column name to resolve ambiguous shapes (a 5-digit CPT code looks like a ZIP code).
They are registered, and pass the plugin kit; `shape profile` calls detectors once the
pattern-rate work of issue #2 is merged (in this tree the profiler does not yet call plugin
detectors).

## Command

```
shape healthcare-codes list
shape healthcare-codes fetch icd10cm ndc         # download at the pinned release, build
shape healthcare-codes fetch hcpcs2 --file zip=october-2026-alpha-numeric-hcpcs-file.zip
shape healthcare-codes byo cpt cpt.csv --map code=CPT --map long_desc=Descriptor
shape healthcare-codes notices ndc
shape healthcare-codes verify
```
