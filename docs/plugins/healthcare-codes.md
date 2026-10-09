# Healthcare code sets (`sqllocks-shape-healthcare-codes`)

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](../contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" healthcare-codes
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for healthcare-codes
    ```


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
| `hcc_hierarchy` | the hierarchies of the same models (see [Risk adjustment](#risk-adjustment-hierarchies-coefficients-and-a-reference-score)) | `fetch` (or `byo`) |
| `hcc_coefficients` | the coefficients of the same models, per segment | `fetch` (or `byo`) |
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

## Risk adjustment: hierarchies, coefficients and a reference score

The `hcc` table maps ICD-10-CM codes to the categories of the CMS risk-adjustment models. Two
more tables carry the rest of the published models, so generated data can be scored from the
published numbers instead of hard-coded ones. Neither is a table of the common code model (they
have no `code` column): read them with `read_table`, which takes the same `data_dir` and search
order as `load`, and check them with `manifest`.

```python
from shape_healthcare_codes import read_table, risk_score

read_table("hcc_hierarchy")      # after `shape healthcare-codes fetch hcc hcc_hierarchy hcc_coefficients`
read_table("hcc_coefficients")
```

Both are built from the CMS "2027 Initial Model Software" zip
(https://www.cms.gov/files/zip/2027-initial-model-software.zip, packages of 2026-05-04,
retrieved 2026-10-04), which holds one package per model calibration: CMS-HCC V28
(`V2826.115.T2`) and V22 (`V2226.79.O2`), ESRD V24 (`E2426.86.T2`) and V21 (`E2126.87.P2`), and
RxHCC V08 three times (`R0826.84.T2` calibrated on PDP and MAPD enrollees, `.Y1` on MAPD only,
`.Y2` on PDP only). These are the model versions of the `hcc` mapping table. The licence: neither
CMS page states one; a US federal government work (see `THIRD_PARTY_NOTICES.md`, `[VERIFY]`).
A changed file layout makes the builder raise `ValueError` naming what it did not find; it never
writes a partial table.

### `hcc_hierarchy`

From each package's hierarchy macro (`V28115H1`, `V22H79H1`, `V24H86H1`, `V20H87H1`,
`R08X84H1`): one row per `%SET0(CC=..., HIER=%STR(...))` line, in the published order, which is
the order the CMS software applies them. Packages of the same model publish the same hierarchy
and it is kept once (a disagreement is an error).

| column | type | meaning |
|---|---|---|
| `model` | string | `CMS-HCC V28`, `ESRD V24`, `RxHCC V08`, ... (as in `hcc`) |
| `hcc` | string | the category whose presence removes others (`"17"`) |
| `drops` | list of string | the categories it removes (`["18", "19", ...]`) |

### `hcc_coefficients`

From each package's coefficient file (`C2824T2N.csv`, `D2423T2M.csv`, `R0827T11.csv`, ...; header
`Name, Coeff, Label`). A published name is a segment prefix and a variable: the segments and their
prefixes come from the package's main macro (`%&SCOREMAC(PVAR=SCORE_COMMUNITY_NA, ...,
CPREF=CNA_)`), so `CNA_HCC17` is segment `COMMUNITY_NA`, variable `HCC17`.

| column | type | meaning |
|---|---|---|
| `model` | string | as in `hcc` |
| `segment` | string | the published score variable without `SCORE_`: `COMMUNITY_NA`, `COMMUNITY_FBD`, `INSTITUTIONAL`, `NEW_ENROLLEE`, `SNP_NEW_ENROLLEE` (CMS-HCC); `DIAL`, `DIAL_NE`, `GRAFT_INST`, `G_COMM_ND_PBD_GE65`, ... (ESRD); `CE_NoLowAged`, `CE_LTI`, `NE_LowCommunity`, ... (RxHCC). Null for the ESRD transplant and graft adjustment factors, which belong to no segment |
| `variable` | string | as published: a category (`HCC17`, `RXHCC30`), a demographic cell (`F70_74`, `NMCAID_NORIGDIS_NEF65`), an interaction (`DIABETES_HF_V28`) or a count variable (`D1` ... `D10P`) |
| `coefficient` | string | the published decimal text (`"0.395"`, `"0.000"`); never a float, so nothing is rounded. `decimal.Decimal(coefficient)` is exact |
| `payment_years` | list of int16 | the payment year of the package: the year of `DATE_ASOF` in its main program (2027) |
| `software` | string | the package id (`V2826.115.T2`); tells apart packages of the same model |

### File format and bring your own

Each table is an asset like every other (`<id>.arrow` and `<id>.json`), and both declare a
`format` (`shape-hcc-hierarchy`, `shape-hcc-coefficients`) and an integer `version` (1) in the
Arrow schema metadata and in the manifest. `read_table` raises `UnsupportedVersion` (a
`ValueError`) for a newer version, another format, or a version that is not an integer of at
least 1; a file without a declaration is read as version 1. Version 1 files are kept in the
plugin's tests (`tests/data/compat_v1`) and must keep loading.

Without network access, load the CMS zip you have, or your own delimited file with the table's
columns (`drops` and `payment_years` are lists separated by blanks, `;` or `|`; `software`
defaults to `user supplied`):

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

[Run this example](#local-example-3).


`risk.table_problems(mapping, hierarchy, coefficients)` lists every hierarchy category and every
category variable that is not a category of the same model in the mapping (the 2027 tables have
none).

### The reference score

```python
from shape_healthcare_codes import risk_score

risk_score(["E11.65", "I50.9"], "CMS-HCC V28", "COMMUNITY_NA",
           {"F70_74": 1, "DIABETES_HF_V28": 1}, payment_year=2027)
# {"categories": ["38", "226"], "dropped": [],
#  "terms": [{"variable": "HCC38", "coefficient": Decimal("0.166")}, ...],
#  "score": Decimal("1.033")}
```

`score(codes, model, segment, demographics=None, payment_year=None, *, software=None,
data_dir=None)` (also `shape_healthcare_codes.risk.score`):

1. maps each code (normalized, `E11.65` = `E1165`) to the model's categories; a code with no
   category contributes nothing. With `payment_year`, a mapping row counts only when its payment
   year flag for that year is "Yes", if the mapping has flags for that year (the 2027 initial
   mapping flags 2026 only, so for 2027 every row counts);
2. applies the hierarchies in the published order: a rule fires only while its category is still
   present (with categories 62, 63 and 202 in V28, 62 drops 63, so 63 no longer drops 202);
3. adds the coefficient of each remaining category that the segment has, and, where the segment
   has count variables, the one for the number of those categories (`D10P` is 10 or more);
4. adds the coefficient of each variable named in `demographics` (a mapping of variable to 0 or
   1, or a list of names): demographic cells, interactions, `ORIGDS`, `LTIMCAID`, ...

It returns `categories` (after the hierarchies), `dropped`, `terms` and `score` (a `Decimal`, the
exact sum). An unknown model, segment, software, payment year or demographic variable raises
`ValueError` naming the allowed values; so does naming a category or count variable in
`demographics` (they come from the codes). When several packages publish a segment (RxHCC V08)
`software` is required.

**Limits.** This is a documented reference for testing generated data, **not** a certified
implementation of the CMS software, and no equivalence with it is claimed. It does not derive
demographic cells or interactions (the caller names them), apply diagnosis edits (age and sex),
the ESRD transplant and graft adjustment factors that have no segment, normalization factors,
the coding-intensity adjustment, or any other adjustment that is not in the published
coefficient tables.

## Detectors (`shape.detectors`)

`icd10`, `ndc`, `npi`, `hcpcs`, `cpt`, `mbi` (Medicare Beneficiary Identifier), `member_id`. Each
reports the share of sampled values that match as the confidence, returns `None` below 0.9, and
uses the column name to resolve ambiguous shapes (a 5-digit CPT code looks like a ZIP code).
They are registered, and pass the plugin kit; `shape profile` calls detectors once the
pattern-rate work of issue #2 is merged (in this tree the profiler does not yet call plugin
detectors).

## Command

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

[Run this example](#local-example-5).



## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-3"></a>

### Example 4

<!-- example: 3 -->

```bash {.runnable-reference}
shape healthcare-codes fetch hcc_coefficients --file zip=2027-initial-model-software.zip
shape healthcare-codes byo hcc_hierarchy 2027-initial-model-software.zip
shape healthcare-codes byo hcc_coefficients my_coefficients.csv
```

??? info "Output (exit 0)"

    ```text {.expected}
    built /tmp/docs-reference-runs/healthcare-codes/healthcare-assets/hcc_coefficients.arrow
    built /tmp/docs-reference-runs/healthcare-codes/healthcare-assets/hcc_hierarchy.arrow
    built /tmp/docs-reference-runs/healthcare-codes/healthcare-assets/hcc_coefficients.arrow
    ```

<a id="local-example-5"></a>

### Example 6

<!-- example: 5 -->

```bash {.runnable-reference}
shape healthcare-codes list
shape healthcare-codes fetch icd10cm --file FY2016=FY2016.zip --file FY2017=FY2017.txt --file FY2018=FY2018.zip --file FY2019=FY2019.txt --file FY2020=FY2020.txt --file FY2021=FY2021.txt --file 2021-01=2021-01.zip --file FY2022=FY2022.zip --file 2022-04=2022-04.txt --file FY2023=FY2023.zip --file 2023-04=2023-04.zip --file FY2024=FY2024.zip --file FY2025=FY2025.zip --file FY2026=FY2026.zip --file FY2027=FY2027.zip --file tabular=tabular.zip
shape healthcare-codes fetch ndc --file zip=ndc.zip
shape healthcare-codes fetch hcpcs2 --file zip=october-2026-alpha-numeric-hcpcs-file.zip
shape healthcare-codes byo cpt cpt.csv --map code=CPT --map long_desc=Descriptor
shape healthcare-codes byo hcc_coefficients 2027-initial-model-software.zip
shape healthcare-codes notices ndc
shape healthcare-codes verify
```

??? info "Output (exit 0)"

    ```text {.expected}
    icd10cm         shipped built=shipped  public domain (attribution: Source: CDC/NCHS)
    icd10pcs        fetch  built=-        US government work; the page states no copyright or reuse terms; a work of the US federal government (17 U.S.C. 105) [VERIFY]
    hcpcs2          fetch  built=-        US government work; the page states no copyright or reuse terms; a work of the US federal government (17 U.S.C. 105); Level II only, CPT is never read [VERIFY]
    ndc             fetch  built=-        public domain; CC0 1.0 (openFDA terms)
    rxnorm          fetch  built=-        public domain (NLM); no UMLS licence for the prescribable subset; NLM asks for acknowledgement and a currency disclosure
    pos             fetch  built=-        US government work; the page states no copyright or reuse terms; a work of the US federal government (17 U.S.C. 105) [VERIFY]
    hcc             fetch  built=-        US government work; the page states no copyright or reuse terms; a work of the US federal government (17 U.S.C. 105) [VERIFY]
    hcc_hierarchy   fetch  built=user     US government work; the page states no copyright or reuse terms; a work of the US federal government (17 U.S.C. 105) [VERIFY]
    hcc_coefficients fetch  built=user     US government work; the page states no copyright or reuse terms; a work of the US federal government (17 U.S.C. 105) [VERIFY]
    ccsr            fetch  built=-        no terms stated for the tool; AHRQ is a US federal agency [VERIFY]
    mce_edits       fetch  built=-        US government work; the page states no copyright or reuse terms; a work of the US federal government (17 U.S.C. 105) [VERIFY]
    nucc_taxonomy   byo    built=-        copyright American Medical Association; commercial use needs a licence
    carc            byo    built=-        X12 copyright; no redistribution grant
    rarc            byo    built=-        X12 copyright; no redistribution grant
    icd10_who       byo    built=-        WHO copyright; no licence stated on the browser page [VERIFY]
    icd10gm         byo    built=-        public domain per BfArM, but a download is a contract of use with BfArM
    icd10am         byo    built=-        licensed by IHACPA; not read [VERIFY]
    cpt             byo    built=-        licensed; no redistribution
    snomed          byo    built=-        licensed; no redistribution
    revenue_codes   byo    built=-        licensed; no redistribution
    type_of_bill    byo    built=-        licensed; no redistribution
    built /tmp/docs-reference-runs/healthcare-codes/healthcare-assets/icd10cm.arrow
    built /tmp/docs-reference-runs/healthcare-codes/healthcare-assets/ndc.arrow
    built /tmp/docs-reference-runs/healthcare-codes/healthcare-assets/hcpcs2.arrow
    built /tmp/docs-reference-runs/healthcare-codes/healthcare-assets/cpt.arrow
    built /tmp/docs-reference-runs/healthcare-codes/healthcare-assets/hcc_coefficients.arrow
    FDA NDC Directory [fetch]
      source:  https://www.accessdata.fda.gov/cder/ndctext.zip
      release: daily file; the build records its date and SHA-256
      licence: public domain; CC0 1.0 (openFDA terms)
      page:    https://www.fda.gov/about-fda/about-website/website-policies (read 2026-10-02)
      quote:   "Unless otherwise noted, the contents of the FDA website (www.fda.gov) are not copyrighted. They are in the public domain and may be republished, reprinted and otherwise used freely by anyone without the need to obtain permission from FDA."
    cpt.arrow: ok rows=1 release=user supplied sha256=ea1324e412167faf
    hcc_coefficients.arrow: ok rows=73 release=user supplied sha256=4a573409f0b15727
    hcc_hierarchy.arrow: ok rows=30 release=user supplied sha256=d5fdf7a9b98e8ca8
    hcpcs2.arrow: ok rows=3 release=October 2026 (file of 2026-09-23) sha256=bf8555d9f60e779c
    icd10cm.arrow: ok rows=9 release=FY2027 (effective 2026-10-01) sha256=a88a9ae543c500b7
    ndc.arrow: ok rows=4 release=FDA NDC Directory file of 2026-10-09 sha256=3cd82c08d3ee4efa
    ```
