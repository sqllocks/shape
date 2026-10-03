# sqllocks-shape-healthcare-standards

Shape plugin: standard healthcare outputs from the payer tables.

| Sink / emitter / source | Output |
|---|---|
| `x12-837p`, `x12-837i` | X12 005010 claims, one interchange per file |
| `x12-835` | X12 005010 remittance advice |
| `x12-834` | X12 005010 benefit enrollment |
| `fhir-ndjson`, `fhir-bundle` | FHIR R4 resources (Bulk FHIR ndjson; one JSON bundle per file) |
| `omop` | OMOP CDM v5.4 tables (CSV or Parquet) |
| `ncpdp` | bring-your-own-specification mapping layer (no spec text is shipped) |
| `fhir` (emitter) | FHIR resources as events |
| `x12` (source) | reads X12 interchanges (`x12://FILE`, `.x12`, `.edi`) into an `x12_segment` table, and for the 835 one table per loop (`loops="tables"`) |
| `hl7v2` (source) | reads HL7 v2 messages (`.hl7`, MLLP framing allowed) into an `hl7_segment` table, and one table per segment id (`segments="tables"`) |

The input is the table contract in `shape_healthcare_standards.contract` (also the source of
truth for the column names and types the payer domain must produce). Every identifier is
synthetic, and the X12 envelopes carry clearly synthetic sender and receiver ids.

Installing the validators the tests use: `pip install 'sqllocks-shape-healthcare-standards[test]'`.

The two readers are structural: they split interchanges and messages into segments and fields and
interpret no code. Table layouts and options are in
[`docs/plugins/healthcare-standards.md`](../../docs/plugins/healthcare-standards.md).
