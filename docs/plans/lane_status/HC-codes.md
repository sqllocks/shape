# HC-codes: issue #45, lane "reference code sets, validators and detectors": status

Branch `lane/HC-codes` (from the lead's integration tree at abd78cb; `origin/build/main-plan`
merged on 2026-10-02, merge commit, no rebase). Plugin: `plugins/shape-healthcare-codes`
(distribution `sqllocks-shape-healthcare-codes`). No D-xx/T-xx decision, gate or tolerance is
changed; §11 is unedited; §2.3 was touched only by the merge (see "Merge" below); `$SPINDLE_ROOT`
untouched. The issue was not commented on, labelled or closed. No PR.

## For lane HC-domain: the API (pushed first)

**Documented in `docs/plugins/healthcare-codes.md`**; also the plugin README. Importable from
`shape_healthcare_codes`: `load`, `available`, `CodeSet` (`is_valid`, `codes_on`, `get`,
`validity_table`), `CodeSystem`, `icd10cm_valid_billable`, `ndc_marketed` / `NdcIndex`,
`EditIndex` / `age_sex_violations`, `normalize_ndc`, `generate_synthetic_npis`, `luhn_valid`,
`is_synthetic_npi`, `normalize_code`, `fiscal_year`. Free sets need
`shape healthcare-codes fetch ASSET` first (the ICD-10-CM starter subset is in the wheel).
One function works on every diagnosis system (`tests/test_switching.py`).

## Network

Reachable (2026-10-02): ftp.cdc.gov, www.cms.gov, www.fda.gov, www.accessdata.fda.gov,
www.nlm.nih.gov, download.nlm.nih.gov, www.nucc.org, hcup-us.ahrq.gov, x12.org, icd.who.int,
www.bfarm.de, www.cdc.gov. **Blocked: www.ihpa.gov.au** (the proxy answered 502 to CONNECT), so
ICD-10-AM's licence text was not read and its loader is tested on a hand-made fixture only:
`[VERIFY]`. Every builder was run against the real sources (live `fetch` through the proxy for
all nine fetchable assets; row counts match an independent count of each source file, below).

## Assets: source, release, licence, mode, size

Licences were read on each source's own page on 2026-10-02 and are quoted in the plugin's
`THIRD_PARTY_NOTICES.md` (shipped inside the wheel).

| asset | source (release) | licence finding | mode | rows | size (Arrow, zstd) |
|---|---|---|---|---|---|
| `icd10cm` | CDC/NCHS FTP, FY2016-FY2027 + 2021-01, 2022-04, 2023-04 updates (15 releases, each file sha256-pinned) | CDC agency materials: public domain, attribution "Source: CDC/NCHS" | **shipped subset** + fetch | 16,582 shipped / 99,085 full | 0.63 MB shipped / 3.80 MB full |
| `icd10pcs` | CMS, FY2021-FY2027 + Apr 2025, Apr 2026 (9 releases, pinned) | page states no terms; US government work `[VERIFY]` | fetch | 80,501 | 2.00 MB |
| `hcpcs2` | CMS Oct 2026 alpha-numeric file (2026-09-23) | page states no terms; Level II only, CPT records refused `[VERIFY]` | fetch | 9,154 (8,770 codes + 384 modifiers) | 0.49 MB |
| `ndc` | FDA `ndctext.zip`, file of 2026-10-02 | FDA website policy: public domain; openFDA CC0 | fetch | 218,717 packages | 15.6 MB |
| `rxnorm`, `rxnorm_ndc` | NLM `RxNorm_full_prescribe_09082026.zip` (MD5 88bbe4cefabd8e71f58651c1c3188646, published by NLM, reproduced) | "no license required"; public domain core; acknowledgement and currency disclosure requested | fetch | 34,351 concepts; 239,152 RxCUI-NDC pairs | 1.07 MB; 2.28 MB |
| `pos` | CMS place-of-service page (database updated 2024-05-02) | page states no terms `[VERIFY]` | fetch | 53 | 0.01 MB |
| `hcc` | CMS 2027 Initial ICD-10-CM Mappings (2026-05-13): V22, V28, ESRD V21/V24, RxHCC V08 | page states no terms `[VERIFY]` | fetch | 44,864 | 0.51 MB |
| `ccsr` | AHRQ CCSR v2026.1 | page states no terms for the tool `[VERIFY]` | fetch | 87,705 | 0.99 MB |
| `mce_edits` | CMS Medicare Code Editor definitions: age v44, sex v41.1 | page states no terms `[VERIFY]` | fetch | 11,828 | 0.17 MB |
| `nucc_taxonomy` | nucc.org v26.1 | **AMA copyright; commercial use needs a licence** | **BYO** | - | - |
| `carc`, `rarc` | x12.org | **"All X12 work products are copyrighted"**, no grant | **BYO** | - | - |
| `icd10_who` | WHO ICD-10 2019 | WHO copyright; no licence on the page `[VERIFY]` | **BYO** | - | - |
| `icd10gm` | BfArM 2027 | "in the public domain", but a download is "a contract of use between you and the BfArM" | **BYO** (ClaML) | - | - |
| `icd10am` | IHACPA | host blocked; licensed `[VERIFY]` | **BYO** | - | - |
| `cpt`, `snomed`, `revenue_codes`, `type_of_bill` | AMA, SNOMED Intl, NUBC | licensed | **BYO** | - | - |

**Wheel size: 677 KB** (`sqllocks_shape_healthcare_codes-0.9.0-py3-none-any.whl`, built by
`check_plugin_skeletons.py --build`): the code, 627 KB of ICD-10-CM subset, and the notices. The
full sets (all rows above, about 26 MB together) are built on the user's machine by
`shape healthcare-codes fetch` (downloads about 208 MB the first time; 5 min 35 s through this
proxy for ICD-10-CM, ICD-10-PCS, NDC and RxNorm). Nothing licensed is shipped, downloaded or read:
the HCPCS builder raises `CptRecordError` if a code record begins with a digit.

### Reconciliation of the real builds with the source files (run in this session)

HCPCS: 9,154 records with id 3 or 7 in the file = 9,154 rows. NDC: 218,722 package rows - 4
duplicate package codes - 1 package with no product = 218,717. RxNorm: 34,351 RXNCONSO rows of the
kept term types = 34,351. HCC: 44,864 non-empty category cells = 44,864 rows. CCSR: 87,705 category
pairs = 87,705 rows. MCE: tab-led code rows per section counted independently (3,431 / 2,307 /
529 / 1,949 sex rows; 857 / 2,587 / 126 / 42 age rows) = the table. ICD-10-CM: 69,823 billable
codes in FY2016 and 74,879 in FY2027 (the published counts). The first MCE build found only 194
procedure rows instead of 2,307 (the row pattern required a leading letter); the synthetic
fixture test caught it and the count check confirmed the fix.

## What is built

- **Code model and loader** (`model.py`, `store.py`): one Arrow shape for every system; release
  masks give exact validity per release; `codes_on(date)` draws only codes valid on that date.
- **ICD-10-CM**: code, short and long description, chapter, block, category, billable flag, 7th
  character and its definition, laterality, validity per release (15 releases; October 1 and the
  three mid-year updates). **Known gap `[VERIFY]`**: the April 1, 2020 update (U07.1) has no file
  on the CDC or CMS pages that can be pinned, so U07.1 is reported invalid 2020-04-01 to
  2020-09-30 (valid from FY2021). CDC's April 2025 and April 2026 files are identical to October's
  in code and flag, so they are not releases. FY2020 to FY2023 directories hold the final file of
  their year; the releases are separated using CDC's base files and CMS's updated files.
- **ICD-10-PCS** FY2021 on (files CMS calls "updated" are treated as effective October 1;
  earlier years have unusable or differently-addressed files, so dates before 2020-10-01 are not
  covered `[VERIFY]`).
- **NDC**: 11-digit 5-4-2 from the FDA's three 10-digit layouts (4-4-2, 5-3-2, 5-4-1); marketing
  start and end dates from the package, falling back to the product. Limitation `[VERIFY]`: the
  FDA directory lists current and recently delisted products only.
- **NPI**: Luhn check digit with the 80840 prefix, checked against the CMS worked example
  (123456789 gives 3). **Rule verified from CMS's own document**: NPIs "will initially be issued
  with the first digit = 1 or 2". Synthetic NPIs begin `99` (never assigned while real NPIs begin
  1 or 2; CMS reserves the right to use other first digits "when coordinated", so `[VERIFY]`).
  `is_real_format_npi` rejects them on purpose.
- **Age and sex edits** from the CMS MCE lists. CMS deactivated the sex-conflict edit on
  2024-10-01 and stopped listing it; the sex lists are v41.1's (the last), so codes added after
  FY2024 are not on them.
- **BYO loaders**: delimited files (column names found or mapped) and ClaML; XML that declares
  entities is refused.
- **Detectors** (`shape.detectors`): `icd10`, `ndc`, `npi`, `hcpcs`, `cpt`, `mbi`, `member_id`;
  registered and passing the plugin kit with samples. `shape profile` does not yet call plugin
  detectors in this tree (the issue #2 pattern-rate work is not merged here), so profile
  recognition is verified through the kit, not through `shape profile`.
- **Command** `shape healthcare-codes list | fetch | byo | notices | verify`.

## Not done / for the lead

- The plan's T-08 lists the extras and T-09 / §5 the first-party plugins; this plugin is not in
  either list, and I did not add an extra (that changes T-08: owner decision). I added it to
  `EXPECTED` in `scripts/check_plugin_skeletons.py` (the check refuses an unlisted plugin) and
  made that script handle a hyphenated name.
- **CI is not wired**: add `-e plugins/shape-healthcare-codes` and
  `plugins/shape-healthcare-codes/tests` to the plugin contract-test job in
  `.github/workflows/ci.yml`. I did not change CI: its job runs on Windows too, which I cannot
  run here (the tests use `Path.as_uri()` and no POSIX-only calls).
- `scripts/check_secrets.py` reports `plugins/shape-fabric/tests/test_recorded.py` (identical to
  the base; not touched by this lane).
- Not built: the chaos mutators, the generator, X12 and FHIR emitters (other lanes); Elixhauser
  and Charlson crosswalks (not requested in this lane; the issue lists them under ICD-10
  capability: HCC and CCSR are built, the comorbidity indexes are not); CPT/HCPCS agreement with
  place of service and specialty (needs licensed CPT and the NUCC taxonomy: BYO).

## Merge

`git merge origin/build/main-plan` conflicted in two files, both resolved by keeping both sides:
`scripts/check_plugin_skeletons.py` (main-plan removed `mcp` from the list; this lane adds
`healthcare-codes`) and `docs/plans/COMPLETION_PLAN.md` (T-07: the lead's tzdata text kept; T-08:
main-plan's text without `[mcp]` taken; §2.3: the lead's four rows and main-plan's P6-11 row both
kept). No text of either side was edited.

## Checks (run in this session on this branch, after the merge)

- `pytest plugins/shape-healthcare-codes/tests`: 100 passed.
- `ruff check` and `ruff format --check` on `src tests plugins benchmarks/vs_spindle`: clean.
- `mypy` (repo config, strict): no issues in 347 files; `mypy --strict` on the plugin source: no
  issues in 24 files.
- `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80`; `vulture` on the plugin:
  clean. `lint-imports`: 1 contract kept, 0 broken.
- `scripts/check_user_facing.py` clean, and `--wheel` on the built plugin wheel clean.
- `scripts/check_plugin_skeletons.py` (7 distributions) and `--build` (7 wheels): OK.
- `scripts/check_requirements.py`, `check_conformance_coverage.py`: OK.
- `bandit -q -r src -ll`: clean; on the plugin: clean (two findings fixed: XML entity expansion
  and `urlopen` schemes, each with a test).
- START: `shape --version` 75-81 ms (limit 300 ms); `import shape_healthcare_codes` loads no
  Arrow (tested).
- Plugin kit: `python -m shape.plugins.kit sqllocks-shape-healthcare-codes`: 8 plugins conform.
- Suites `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`
  with `SHAPE_KERNEL=rust` and `python`: see the last section.
