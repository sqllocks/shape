# HC-standards: standard healthcare outputs (issue #45, lane `lane/HC-standards`)

Plugin: `plugins/shape-healthcare-standards` (distribution `sqllocks-shape-healthcare-standards`,
package `shape_healthcare_standards`). Sinks: `x12-837p`, `x12-837i`, `x12-835`, `x12-834`,
`fhir-ndjson`, `fhir-bundle`, `omop`, `ncpdp`. Emitter: `fhir`. Validators are test-only
(`pip install '...[test]'`: `pyx12`, `fhir.resources`, `duckdb`; the HL7 validator JAR via
`SHAPE_FHIR_VALIDATOR_JAR`). Every identifier is synthetic (NPIs start with 9, outside the real
1/2 range, with a valid check digit; X12 sender and receiver ids are `SYNTHSENDER` /
`SYNTHRECEIVER`, ISA15 = `T`).

## Input table contract (for lane `lane/HC-domain`)

`shape_healthcare_standards/contract.py`: tables `member`, `eligibility`, `provider`,
`medical_claim`, `medical_claim_line`, `claim_diagnosis`, `claim_procedure`, `pharmacy_claim`,
`drug_reference`; one `Col` per column (name, Arrow type, required, meaning). Extra columns are
ignored, a missing optional column reads as null, a missing required column is an error.
**HC-domain: produce these names, or tell this lane which differ.** At the time of writing
`origin/lane/HC-domain` and `origin/lane/HC-codes` carried no commits beyond the shared base, so
the writers copy code values through and only check their shape (`codes.py`); the codes lane's
API is not used yet. Writers also require internal consistency (strict, with clear errors):
claim `total_billed` = sum of line billed; for the 835, line allowed = paid + member
responsibility and header paid = sum of line paid.

## Formats

| Format | Built | Validator and verdict |
|---|---|---|
| X12 837P 005010X222A1 | one interchange per file; 2000A/B/C hierarchy, CLM, HI, SV1 | `pyx12` 4.0.0 (BSD, test-only): valid, 0 errors |
| X12 837I 005010X223A2 | CLM (TOB), CL1, HI with POA, DRG, ICD-10-PCS, SV2 | `pyx12`: valid, 0 errors |
| X12 835 005010X221A1 | one ST per payee, line-level CAS (PR 1/2/3, CO 45, CARC), RARC, reversals negated, PLB for a negative net | `pyx12`: valid, 0 errors (also reversal and net-negative cases) |
| X12 834 005010X220A1 | INS loop per member, HD per span, PCP, maintenance 030 or auto 021/024 | `pyx12`: valid, 0 errors |
| FHIR R4 | Patient, Coverage, Claim, ExplanationOfBenefit, MedicationDispense, Practitioner, Organization; Bulk ndjson, bundles; emitter | official HL7 validator 6.10.4, R4 4.0.1 + US Core 6.1.0 + CARIN BB 2.1.0, terminology online: 2 errors (both one synthetic sample NDC not in the NDC directory), rest warnings. Details: `plugins/shape-healthcare-standards/tests/fhir/VALIDATION.md`. US Core applies only to Patient, Practitioner and Organization. The validator was run on the Bundle files, not the ndjson |
| OMOP CDM v5.4 | person, location, observation_period, payer_plan_period, visit, condition, procedure, drug_exposure, cost, provider, care_site (CSV or Parquet) | loaded into DuckDB from the official OHDSI v5.4 DDL (vendored, Apache-2.0): schemas equal, PKs applied, 23 FKs checked by anti-join with 0 violations (DuckDB 1.5.6 cannot add FK constraints); 43 FKs to vocabulary tables not checkable without a vocabulary. Concept ids not in a supplied `concept_map` are 0. Details: `tests/omop/VALIDATION.md` |
| NCPDP | bring-your-own-specification mapping layer only: user-supplied JSON layout, generic separators, writer and parser, round trip tested with a synthetic layout | no conformance claim; nothing standard-conformant is built |

The X12 round-trip tests (tables -> file -> `pyx12` reader -> key fields, control totals, SE/GE/IEA
counts and control numbers) are in `tests/x12/test_x12.py`; a corrupted file is shown to fail.

## NCPDP licensing finding (accessed 2026-10-02; sources in `tests/ncpdp/LICENSING.md`)

The Telecommunication Standard (D.0, and F6), Batch, External Code List and Data Dictionary are
NCPDP member products; the member notice limits use to the member's own business and bars passing
them to third parties without written permission. 45 CFR 162.1102 adopts D.0 (Batch 1.2) and F6 by
reference and reproduces no field list. A public state Medicaid payer sheet confirms the framing
(separators 0x1E / 0x1D / 0x1C, field = separator + 2-character id + value) but carries NCPDP's
copyright, so no field table was copied. The NCPDP licensing page itself returned 404 from this
environment, so purchase terms and non-member routes are unverified. **Escalation (§0.4): a
default NCPDP output needs an owner licensing decision** (NCPDP membership or written permission).

## Open items for the lead

- Sample NDC `00093726001` is synthetic (the HL7 validator reports it unknown); the test allows
  exactly that message.
- `scripts/check_plugin_skeletons.py` accepts the hyphenated name (`EXPECTED` now lists
  `healthcare-standards`). Not added: a root extra (T-08), CI jobs for the plugin tests, and the
  `[test]` extras in CI; the lead owns those.
- All sinks use `schemes = ("file",)` (the kit rejects empty-string schemes); bare paths work.
- A sink writes only its own rows (837P ignores `claim_type` I); companion tables arrive through
  the `tables` option.
- OMOP ids are 31-bit (the DDL types them `integer`); type concept ids come from the OHDSI
  vocabulary creation script and were not checked against an Athena download.
- HC-domain and HC-codes had not landed; re-run the round trips against their real tables when
  they do.

## Checks run (this session, on the merged tree)

Run on the merged tree (after merging `origin/build/main-plan`), Python 3.11, with the sibling
plugins installed as CI does:

- `ruff check` and `ruff format --check` (src tests plugins benchmarks/vs_spindle): clean.
- `mypy` (project config, 347 files): clean. `mypy --strict` over the plugin source (27 files): clean.
- `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80`: clean. `lint-imports`: 1 kept, 0 broken.
- `python scripts/check_user_facing.py`: clean; `--wheel` on the built plugin wheel: clean.
- `bandit -q -r src -ll`: no findings (only existing nosec warnings); on the plugin source: no findings.
- `scripts/check_plugin_skeletons.py`: OK; `python -m shape.plugins.kit sqllocks-shape-healthcare-standards`: 9 plugins conform.
- START (`shape --version`, median of 10): 62 ms (limit 300 ms).
- Plugin tests: 139 passed, including the official HL7 validator test (`SHAPE_FHIR_VALIDATOR_JAR` set, Java 21).
- Suites `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`: with
  `SHAPE_KERNEL=rust` and with `SHAPE_KERNEL=python`, 5194 passed, 2 failed each. Both failures
  were `tests/plugins/test_plugin_kit_install.py` (it hard-codes the six T-09 distributions);
  that test now lists the seventh (`healthcare-standards`), and the file passes (7 passed). The
  full suites were not re-run after that one-constant change.
- Not run here: the heavy-marked suites, `tests/demo/fabric`, emulator and live tests.
