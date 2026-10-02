# OMOP CDM v5.4 validation record

Everything below was run in this session (Python 3.11.15, pyarrow 25.0.1, DuckDB 1.5.6).

## Sources

* Official DDL: OHDSI CommonDataModel, `inst/ddl/5.4/duckdb/`, files
  `OMOPCDM_duckdb_5.4_ddl.sql`, `OMOPCDM_duckdb_5.4_primary_keys.sql`,
  `OMOPCDM_duckdb_5.4_constraints.sql`, vendored unmodified in `ddl/` (Apache-2.0, see
  `ddl/NOTICE` and `ddl/LICENSE`).
* The `v5.4.1` tag (commit 4432382693f4e824f2fd5e482692c33772b7568a) has no `duckdb` directory
  (the raw URL under that tag returns 404), so the files come from the `main` branch, commit
  4a910305b2cb74a4fc2b2c34baf44eb0542ff03f (2026-08-25); they were last changed in commit
  40ff52f7cb1e634dd45c3d7157307a47b1b78652 (2026-07-31).
* Cross-check done once by hand (not in the tests): the column names and order of the 12 tables in
  the `main` DuckDB DDL equal those of the `v5.4.1` tag's PostgreSQL DDL
  (`inst/ddl/5.4/postgresql/OMOPCDM_postgresql_5.4_ddl.sql`) and `omop/ddl.py`.
* Type concept ids come from the OHDSI Vocabulary-v5.0 script
  `working/manual_changes/2020/2020-08-20.sql`, which creates the "Type Concept" vocabulary
  from an alphabetical list; the ids are that list's order starting at 32809 (not read from
  Athena, which needs a login). They agree with the ids other OHDSI ETLs use (ETL-Synthea:
  32827 EHR encounter record, 32838 EHR prescription, 32814 Cost record, 32817 EHR). Used:
  32813 Claim enrolment record, 32810 Claim, 32869 Pharmacy claim, 32814 Cost record.
  44818668 (US dollar) is the currency concept and 8507/8532/8527/8516/8515/8657/8557/
  38003563/38003564/9201/9202/9203 are the gender, race, ethnicity and visit concepts named in the
  task; none of these were checked against a vocabulary download.

## What ran

`pytest plugins/shape-healthcare-standards/tests/omop`: 46 passed.

* The official DDL creates all CDM tables in DuckDB (no vocabulary data is loaded).
* For each of the 12 tables in `omop/ddl.py`: column names, order, nullability and types equal the
  DuckDB `DESCRIBE` of the official DDL (`integer` = int32, `NUMERIC` = decimal(18,3), `TIMESTAMP`
  = timestamp[us], `varchar(n)` = string with the limit from the DDL text).
* Outputs of `sample_tables()` in CSV and in Parquet are loaded with
  `INSERT INTO t (cols) SELECT cols FROM read_csv(..., columns={...})` / `read_parquet`; the CSV
  header and the Parquet column names and types are asserted equal to the DDL first. 11 tables
  written, all loaded.
* Primary keys: all 11 `ALTER TABLE ... ADD CONSTRAINT ... PRIMARY KEY` statements for the written
  tables were applied by DuckDB without error, for CSV and Parquet.
* Foreign keys: DuckDB 1.5.6 does not apply `ADD CONSTRAINT ... FOREIGN KEY` through ALTER, so the
  23 foreign keys between written tables (and to the empty `visit_detail`) were checked with the
  same rule as an anti-join (`child.col IS NOT NULL AND NOT EXISTS parent row`): 0 violations, for
  CSV and Parquet. A second test repeats the anti-joins on its own.
* 43 foreign keys point at vocabulary tables (`concept`, `domain`, ...), which hold no rows
  here, so they were not checked. Concept ids written are the fixed ones in `omop/mapping.py`, 0
  for anything unmapped, or those of a caller's `concept_map`.
* Round trips from the sample tables: 4 persons (gender, birth date, race and ethnicity concepts,
  shared location), 3 locations, 4 observation and 4 payer plan periods (open spans end at the
  latest date in the input, 2024-06-30), 4 visits (inpatient 9201 for the institutional claim),
  9 conditions (one per diagnosis, ICD-10-CM dot restored, concept 0), 7 procedures (6 coded
  lines and 1 ICD-10-PCS), 1 drug exposure (the paid claim; the rejected and the reversed claims
  are excluded), 5 cost rows (4 Visit, 1 Drug) with sums equal to the claims: charge 43956.5, paid
  by payer 17991, paid by patient 1175, 7 providers, 1 care site.
* Also covered: stable output across runs (byte-identical CSV), no staging files left behind,
  CSV header/ISO dates/empty NULLs, `concept_map`, reversed/void/replaced claims left out, the
  member table alone (person + location + death), `tables=` and `format=parquet` options, the
  `member` requirement, truncation to DDL varchar limits, and `shape.plugins.kit.check_sink`.

## Verdicts

* Schemas equal to the official DDL: pass (12 of 12 tables).
* CSV and Parquet load with matching columns: pass.
* Primary keys: pass (applied by DuckDB). Foreign keys: pass by anti-join, 0 violations;
  vocabulary foreign keys not checked (no vocabulary).

## Limits

* The sample data has no `concept_map`; mapped concept ids are only tested with made-up ids.
* Visit-level rows only for persons present in `member`; events of other members are dropped.
