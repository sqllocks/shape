# sqllocks-shape-healthcare-standards

Shape plugin: standard healthcare outputs from the payer tables.

| Sink / emitter | Output |
|---|---|
| `x12-837p`, `x12-837i` | X12 005010 claims, one interchange per file |
| `x12-835` | X12 005010 remittance advice |
| `x12-834` | X12 005010 benefit enrollment |
| `fhir-ndjson`, `fhir-bundle` | FHIR R4 resources (Bulk FHIR ndjson; one JSON bundle per file) |
| `omop` | OMOP CDM v5.4 tables (CSV or Parquet) |
| `ncpdp` | bring-your-own-specification mapping layer (no spec text is shipped) |
| `fhir` (emitter) | FHIR resources as events |

The input is the table contract in `shape_healthcare_standards.contract` (also the source of
truth for the column names and types the payer domain must produce). Every identifier is
synthetic, and the X12 envelopes carry clearly synthetic sender and receiver ids.

Installing the validators the tests use: `pip install 'sqllocks-shape-healthcare-standards[test]'`.
