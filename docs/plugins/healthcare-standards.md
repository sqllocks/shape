# `sqllocks-shape-healthcare-standards`

Writers for X12 005010 (837P, 837I, 835, 834), FHIR R4, OMOP CDM and an NCPDP mapping layer, and
two readers that turn X12 and HL7 v2 files into Arrow tables. The writers are described in the
plugin's [README](../../plugins/shape-healthcare-standards/README.md). This page documents the
readers (`shape.sources` entry points `x12` and `hl7v2`).

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
