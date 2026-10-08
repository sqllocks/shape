# `sqllocks-shape-healthcare-standards`

Writers for X12 005010 (837P, 837I, 835, 834), FHIR R4, OMOP CDM and an NCPDP mapping layer, and
two readers that turn X12 and HL7 v2 files into Arrow tables. The writers are described in the
plugin's [README](../../plugins/shape-healthcare-standards/README.md). This page lists the X12
writers, documents the 277CA claim acknowledgment writer (`x12-277ca`), the readers
(`shape.sources` entry points `x12` and `hl7v2`) and, at the end, the `tables` option every
multi-table write depends on ([Companion tables](#companion-tables-tables)).

Both readers are **structural**. They split a file into interchanges, messages, segments and
fields, and keep every value as text. They do not interpret codes, apply business rules or validate
a message against its type. Their tables work with `shape.profile` like any other table; load them
with `read_tables`, and profile or check each one.

Common to both: `schema(uri, **options)` and `read(uri, **options)` give one table (the default one,
or the `table` option); `read_tables(uri, **options)` gives every table as a `dict[str, Table]`,
`read_relationships(uri, **options)` the links between them as
`shape.generation.schema.Relationship` objects (usable by `shape generate`), and
`read_nested(uri, **options)` gives `(tables, relationships)`. `max_bytes` (default 268435456) is the
largest file read; a bigger one raises `ValueError` naming the limit. Text is UTF-8 (a BOM is
ignored), or Latin-1 if the file is not valid UTF-8. An unknown option raises `TypeError`.

```python
from shape_healthcare_standards.sources import X12Source

tables = X12Source().read_tables("remits/835.x12", loops="tables")
tables["loop_2100"]            # one row per claim, columns CLP01, CLP02, ...
```

## Writers

| Sink | Primary table | Output | Files |
|---|---|---|---|
| `x12-837p`, `x12-837i` | `medical_claim` | X12 005010 professional / institutional claims | `837P-NNNNNN.x12`, `837I-NNNNNN.x12` |
| `x12-835` | `medical_claim` | X12 005010X221A1 remittance advice | `835-NNNNNN.x12` |
| `x12-834` | `member` | X12 005010 enrollment | `834-NNNNNN.x12` |
| `x12-277ca` | `claim_acknowledgment` | X12 005010X214 claim acknowledgment | `277CA-NNNNNN.x12` |

Other sinks (FHIR, OMOP, NCPDP) are described in the plugin README. Companion tables are passed
through the `tables=` option of `write`.

## 277CA claim acknowledgment (`x12-277ca`)

The 277CA answers a submitted claim. Use it to test a claims pipeline end to end: generate
claims, write them with `x12-837p`, then write their acknowledgments and feed those back to
the submission flow. Which claims are accepted or rejected, and why, is up to your own
generation schema; the writer only formats the table.

### The `claim_acknowledgment` table

One row per acknowledged claim, or per acknowledged service line. Key: `(claim_id, line_number)`;
a null `line_number` is the claim level. A claim that has line rows also needs its claim-level
row on the same `acknowledgment_date`.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `claim_id` | string | yes | Acknowledged claim; matches `medical_claim.claim_id`. Written as `TRN02` of loop 2200D. |
| `acknowledgment_date` | date32 | yes | Date of the acknowledgment (`STC02`, `DTP*009`). One interchange is written per distinct date. |
| `status_category_code` | string | yes | Claim status category, such as `A1`, `A2`, `A3`, `A6`, `A7`. Shape: two uppercase letters, or a letter and a digit. |
| `status_code` | string | yes | Claim status code. Shape: 1 to 5 digits. |
| `line_number` | int64 | no | Set for a service-line acknowledgment; matches `medical_claim_line.line_number`. |
| `entity_identifier_code` | string | no | Entity the status refers to (`STC01-03`). Shape: 2 or 3 uppercase letters or digits. |
| `action_code` | string | no | `WQ` accepted or `U` rejected. Derived from the category when empty (below). |
| `reference_number` | string | no | The acknowledging party's claim control number (`REF*1K`). |
| `received_date` | date32 | no | When the acknowledging party received the claim (`DTP*050`); the acknowledgment date when empty. |

Codes are copied through and checked for shape only. The plugin ships no code-set meanings; a
malformed code raises `ContractError` naming the claim, the line and the column.

### Action code rule

When `action_code` is null it is derived from the category:

| Category | Action |
|---|---|
| `A1`, `A2` | `WQ` (accepted) |
| `A3`, `A6`, `A7`, `A8` | `U` (rejected) |
| anything else | `ContractError` naming the `claim_id` and the `action_code` column; set it explicitly |

A service-line status can only report a rejection: the 005010X214 map allows only `U` in
`STC03` of loop 2220D, so a line row whose action is `WQ` (explicit or derived) raises
`ContractError`. At the transaction (2200B) and billing-provider (2200C) level `STC03` is `WQ`
when any claim below is accepted and `U` when all are rejected. The accepted and rejected
quantities and amounts are the counts and the summed `medical_claim.total_billed` of the
claim-level rows with action `WQ` and `U`, so `accepted + rejected` always equals the number of
claim-level rows.

### Loops and segments

One interchange (`GS01` `HN`, `GS08` `005010X214`) per `acknowledgment_date`, each with one
`ST*277` set.

| Loop | Segments |
|---|---|
| header | `BHT*0085*08*<trace>*<date>*<time>*TH` |
| 2000A information source | `HL*1**20*1`; 2100A `NM1*PR`; 2200A `TRN*1`, `DTP*050` (received), `DTP*009` (processed) |
| 2000B information receiver | `HL*..*21*1`; 2100B `NM1*41`; 2200B `TRN*2`, `STC`, `QTY*90`, `QTY*AA`, `AMT*YU`, `AMT*YY` |
| 2000C billing provider | `HL*..*19*1`; 2100C `NM1*85` (NPI from `medical_claim.billing_npi`, name from `provider`); 2200C `TRN*1`, `STC`, `QTY*QA`, `QTY*QC`, `AMT*YU`, `AMT*YY` |
| 2000D patient | `HL*..*PT`; 2100D `NM1*QC` (`medical_claim.member_id`, name from `member`) |
| 2200D claim status | `TRN*2*<claim_id>`, `STC` (category:status:entity, date, action, `total_billed`), `REF*1K` when `reference_number` is set, `DTP*472` (`D8`, or `RD8` for a range) |
| 2220D service line status | `SVC`, `STC` (action `U`), `REF*FJ` (line number), `DTP*472` |

Envelope control numbers, `SE01` counts and delimiters come from the shared X12 core.
Interchange and group control numbers count up from `interchange_control` and `group_control`
for each date. Without a `provider` or `member` table the names are `BILLING PROVIDER` and
`UNKNOWN`; when the table is given, a missing row raises `ContractError`.

### `AckOptions`

| Field | Default | Meaning |
|---|---|---|
| `source_name`, `source_id`, `source_qualifier` | `SYNTHETIC PAYER`, `SYNTHPAYER`, `PI` | Information source (`NM1*PR`) |
| `receiver_name`, `receiver_id` | `SYNTHETIC SUBMITTER`, `SYNTHSUBMITTER` | Information receiver (`NM1*41`, qualifier `46`) |
| `trace_id` | `SYNTHTRACE` | Prefix of the transaction trace number (`BHT03`, `TRN02` of 2200A) |
| `accepted_status` | `("A1", "19", "PR")` | Category, status code and entity of the 2200B / 2200C status when any claim is accepted |
| `rejected_status` | `("A3", "21", "PR")` | The same when every claim is rejected |

Sender, receiver, `created`, control numbers and `delimiters` come from the shared envelope options
(`sender_id`, `receiver_id`, `created`, `interchange_control`, ...). The same tables, options and
`created` give byte-identical files. Every value written passes through the X12 text cleaner,
so a `*`, `~`, `:` or `^` in a name becomes a space.

### Example

```python
from shape_healthcare_standards.sinks import X12Acknowledgment277CASink
from shape_healthcare_standards.testing import sample_tables

tables = sample_tables()  # includes a claim_acknowledgment table
n = X12Acknowledgment277CASink().write(
    "file://OUT",
    "claim_acknowledgment",
    tables["claim_acknowledgment"].to_batches(),
    tables=tables,  # medical_claim is required; provider, member, medical_claim_line are used when given
)
# OUT/277CA-000001.x12, OUT/277CA-000002.x12; n is the number of acknowledgment rows
```

Install the test-only validator with `pip install 'sqllocks-shape-healthcare-standards[test]'`.

## `x12`: X12 interchanges

URIs: `x12://FILE` (any file name), or a path or `file://` URI ending in `.x12` or `.edi`.

| Option | Default | Meaning |
|---|---|---|
| `loops` | `None` | `"tables"` also writes one table per 835 loop |
| `strict` | `True` | `False` reads a file that breaks envelope rules and reports the problems |
| `table` | `x12_segment` | which table `schema` and `read` return |
| `max_bytes` | 268435456 | largest file |

Delimiters are read from each ISA segment (it is always 106 characters: ISA01 to ISA16, with the
repetition separator at ISA11, the component separator at ISA16 and the segment terminator after
it), so a file may use any delimiters, and a file may hold several interchanges with different
ones. Line breaks between segments are ignored. An ISA that is truncated or malformed raises
`ValueError` ("ISA segment is truncated: ...") even with `strict=False`, because the delimiters
cannot be known.

### `x12_segment`

One row per segment of every interchange, group and transaction set, envelope segments included.

| Column | Type | Meaning |
|---|---|---|
| `interchange_control` | string | ISA13 of the interchange the segment is in |
| `group_control` | string, null for ISA and IEA | GS06 of the group (GS through GE) |
| `transaction_control` | string, null outside a set | ST02 of the set (ST through SE) |
| `position` | int64 | 1-based position of the segment in its interchange (ISA is 1) |
| `segment_id` | string | `CLP`, `NM1`, ... |
| `loop_id` | string, null | the 835 loop (below); null for envelope segments and other sets |
| `elements` | list of string | every element after the segment id, as written; a composite keeps its component separators (`["HC:99213", "100"]`) |

Joined with the segment terminator and the element separator, the rows give back the file's
segments exactly: a file written by the `x12-835` sink and read back has the same segments.

### `x12_problems`

Always present; empty for a clean file.

| Column | Type |
|---|---|
| `interchange_control` | string |
| `segment_id` | string |
| `position` | int64 |
| `message` | string |

### Envelope rules

Checked as the file is read: ISA13 equals IEA02; IEA01 equals the number of groups; GS06 equals
GE02; GE01 equals the number of sets; ST02 equals SE02; SE01 equals the number of segments from ST
to SE inclusive; every ST, GS and ISA has its SE, GE and IEA; every segment is terminated. With
`strict=True` the first broken rule raises `ValueError` naming the segment and its position
(`SE at position 21: SE01 says '18' segments but the set has 19 (ST to SE)`). With `strict=False`
the file is read in full and every broken rule is a row of `x12_problems`.

### 835 loop tables (`loops="tables"`)

For transaction sets `835` in a group with GS08 `005010X221A1`, each segment's `loop_id` is set
and these tables are written (all six always exist, empty when the file has no 835):

| Table | Loop | Starts at | Parent |
|---|---|---|---|
| `loop_header` | header (ST, BPR, TRN, CUR, REF, DTM) | `ST` | none |
| `loop_1000a` | payer identification | `N1*PR` | `loop_header` |
| `loop_1000b` | payee identification | `N1*PE` | `loop_header` |
| `loop_2000` | header number | `LX` | `loop_header` |
| `loop_2100` | claim payment | `CLP` | `loop_2000` |
| `loop_2110` | service payment | `SVC` | `loop_2100` |

`PLB` and `SE` have `loop_id` `summary` and are in `x12_segment` only. A claim with no `LX` before
it gets an empty `loop_2000` row of its own, so every `loop_2100` has a parent.

Each table has `_id` (1, 2, 3 ... through the file, across sets), `interchange_control`,
`group_control`, `transaction_control` and, below the header, `_parent_id` (the parent table's
`_id`; the relationships are returned by `read_relationships`). The other columns are named by
segment and element position:

* element 1 of `CLP` is `CLP01`, element 2 of `CAS` is `CAS02`;
* an element that is a composite anywhere in the file is split into `SVC01_1`, `SVC01_2`, ...
  (and there is then no `SVC01`);
* a segment that repeats inside one loop instance (two `CAS` on one service) puts its n-th
  occurrence in the same names with `__n` added (`CAS02`, `CAS02__2`);
* an empty element is null; every column is a string.

The unabridged segments are always in `x12_segment`. Other transaction sets (837, 834, ...) are
read as generic segments only (`loop_id` null). Interpreting the codes in an 835 is out of scope.

## `hl7v2`: HL7 v2 messages

URIs: `hl7v2://FILE` (any file name), or a path or `file://` URI ending in `.hl7`.

| Option | Default | Meaning |
|---|---|---|
| `segments` | `None` | `"tables"` also writes `hl7_message` and one table per segment id |
| `table` | `hl7_segment` | which table `schema` and `read` return |
| `max_bytes` | 268435456 | largest file |

A file holds one or more messages; each `MSH` segment starts one. MLLP framing (`0x0B` before the
message, `0x1C 0x0D` after) is stripped when present. Segments end at `\r`, `\n` or `\r\n`. The
separators come from each message's own `MSH`: `MSH-1` is the field separator and `MSH-2` holds the
component, repetition, escape and subcomponent characters. Input that does not start with an `MSH`
segment (or is empty) raises `ValueError` ("HL7 message has no MSH segment"); so does an `MSH-2`
with fewer than four characters, and a segment whose id is not three capital letters or digits.

Escape sequences are decoded after splitting, so a decoded separator never splits a value again:
`\F\`, `\S\`, `\T\`, `\R\` and `\E\` give the field, component, subcomponent, repetition and escape
characters, `\.br\` a newline, `\Xhh..\` the characters with those hex codes, `\H\` and `\N\` are
dropped, and any other sequence is kept as written. Subcomponents are not split: a component keeps
its subcomponent separators.

### `hl7_segment`

One row per segment of every message.

| Column | Type | Meaning |
|---|---|---|
| `message_index` | int64 | 1, 2, 3 ... the message in the file |
| `message_control_id` | string, null | `MSH-10` |
| `message_type` | string, null | `MSH-9` as written (`ADT^A01^ADT_A01`) |
| `position` | int64 | 1-based position in the message (`MSH` is 1) |
| `segment_id` | string | `PID`, `OBX`, ... |
| `fields` | list of list of list of string | fields, then repetitions, then components |

`fields[n - 1]` is field `n` for every segment, `MSH` included: `MSH-1` is `[["|"]]` and `MSH-2` the
raw encoding characters, neither split nor decoded. An empty field is `[[""]]`, so positions never
shift. `PID-3` of `1001^^^HOSP^MR~1002^^^OTHER^PI` is
`[["1001", "", "", "HOSP", "MR"], ["1002", "", "", "OTHER", "PI"]]`.

### Segment tables (`segments="tables"`)

`hl7_message` has one row per message (`message_index`, `message_control_id`, `message_type`). Each
segment id that occurs gets a table named `hl7_<id>` in lower case (`hl7_pid`, `hl7_obx`) with one
row per segment: `message_index`, `message_control_id`, `position`, then one string column per
field:

* field `n` of segment `SEG` is `SEG_n` (`PID_8`);
* a field with components anywhere in the table is split into `PID_5_1`, `PID_5_2`, ... (and there
  is then no `PID_5`);
* a field that repeats puts its r-th repetition in the same names with `__r` added
  (`PID_3_1`, `PID_3_1__2`);
* an empty value is null here (`hl7_segment` keeps the empty string).

Every segment table is a child of `hl7_message` through `message_index`
(`read_relationships`). No message-type-specific validation is done and no table is specific to a
domain.

Not supported: writing HL7 v2 or XML, batch segments (`FHS`, `BHS`) before the first `MSH`.

## Companion tables (`tables`)

A writer receives one table as its batches and reads the other tables it needs (members,
providers, eligibility, claim lines, ...) from the `tables` option. It applies to the `shape.sinks`
entry points `x12-837p`, `x12-837i`, `x12-835`, `x12-834`, `fhir-ndjson`, `fhir-bundle`, `omop` and
`ncpdp`, and to the `fhir` emitter. It is part of the plugin's stable interface in 1.x (see
[stability.md](stability.md#stable-plugin-options)).

```python
sink.write("out/", "medical_claim", batches, tables={"member": members, "provider": providers,
                                                      "medical_claim_line": lines,
                                                      "claim_diagnosis": diagnoses})
```

- **Name and value.** The option is `tables`: a mapping from contract table name (see
  `shape_healthcare_standards.contract.CONTRACT`) to a `pyarrow.Table` or `pyarrow.RecordBatch`.
  `None`, or no `tables` option at all, means no companion tables; the two behave the same.
- **Which tables.** Each writer requires some tables and reads others when they are present. The
  table below is `shape_healthcare_standards.companion_tables(sink_name)` for every writer
  (`{"required": [...], "optional": [...]}`, in contract order; `ValueError` for any other name).
  The primary table the sink received counts as present. *Required* means the writer raises
  `ContractError` when the table is absent and a row needs it (the 834 writer checks before
  reading anything). A table in neither column is never read.
- **Precedence.** The primary table the sink received replaces a companion of the same name. With
  zero batches, the companion of that name is used.
- **Coercion.** Every table, primary or companion, is coerced by `contract.coerce` exactly as the
  primary table: contract column names and types, extra columns ignored, a missing optional column
  null, dates truncated from timestamps. A `RecordBatch` and a `Table` with the same rows give the
  same output.
- **Errors.** All are `shape_healthcare_standards.contract.ContractError` (a `ValueError`):
  an unknown table name in `tables` (`unknown table 'x'; contract tables: [...]`); a missing
  required table (`... is not in the member table`, `no service lines in medical_claim_line`,
  `sink 'x12-834' needs the 'eligibility' table (as the table or in tables=)`); no rows and no
  columns for the primary table and none in `tables` (`no rows and no columns were given for table
  'medical_claim'`); a required column missing from a table (`table 'member' lacks required
  column(s) [...]`).

The `ncpdp` sink requires no table itself: which companions it reads depends on the layout you
supply (a layout field that reads `member`, `provider` or `drug_reference` fails with the layout's
`MappingError` when that table is missing).

<!-- companion-tables:start (generated by shape_healthcare_standards.companions.markdown_table) -->
| Writer | Required | Read when present |
|---|---|---|
| `x12-837p` | `member`, `provider`, `medical_claim_line`, `claim_diagnosis` | `eligibility` |
| `x12-837i` | `member`, `provider`, `medical_claim_line`, `claim_diagnosis` | `eligibility`, `claim_procedure` |
| `x12-835` | `member`, `provider` | `eligibility`, `medical_claim_line` |
| `x12-834` | `eligibility` | `provider` |
| `x12-277ca` | `medical_claim`, `medical_claim_line` | `member`, `provider` |
| `fhir-ndjson` | none | `member`, `eligibility`, `provider`, `medical_claim_line`, `claim_diagnosis`, `claim_procedure`, `drug_reference` |
| `fhir-bundle` | none | `member`, `eligibility`, `provider`, `medical_claim_line`, `claim_diagnosis`, `claim_procedure`, `drug_reference` |
| `omop` | `member` | `eligibility`, `provider`, `medical_claim`, `medical_claim_line`, `claim_diagnosis`, `claim_procedure`, `pharmacy_claim`, `drug_reference` |
| `ncpdp` | none | `member`, `provider`, `drug_reference` |
| `fhir` | none | `member`, `eligibility`, `provider`, `medical_claim_line`, `claim_diagnosis`, `claim_procedure`, `drug_reference` |
<!-- companion-tables:end -->
