# Healthcare standards (`sqllocks-shape-healthcare-standards`)

Writers that turn generated payer tables into standard healthcare files. All of them read one
published input contract (`shape_healthcare_standards.contract`) and are registered in the
`shape.sinks` entry-point group. Every identifier is synthetic and no writer checks or looks up
an identifier in any external registry.

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
