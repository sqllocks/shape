# Schema importers

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


Teams describe their data in JSON Schema, OpenAPI, Avro, Protobuf, Pydantic models or a Power BI
semantic model (TMDL). `shape import-schema` reads the **structure** of any of them (no data, no
statistics) and writes a [generation spec](GENERATION_SPEC.md) that validates against the
published schema (`generation-spec-v1.schema.json`) and generates. `shape from-ddl` does the same
for SQL DDL, and `shape from-dbt` for a dbt project; the importers choose a column's generator the
same way.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

| Exit code | Meaning |
|---|---|
| 0 | the spec is written |
| 1 | `--strict` and at least one element was not imported: no spec is written (the report still is) |
| 2 | bad input: a malformed file (the message names the file, the line where the format has lines, and the element), an ambiguous file, a missing file, `--allow-import` missing |

The same functions are available from Python: `shape.importers.import_schema(source, fmt=None,
allow_import=False)` returns the spec (a `SpecDocument`, so it can be edited and saved) and the
report.

## Which format a file is

`--from` is inferred when it is left out: `.avsc` is Avro, `.proto` Protobuf, `.py` (or
`module.path:Model`) Pydantic, a `.tmdl` file or a folder with a `definition/` folder TMDL. A
`.json`, `.yaml` or `.yml` file is read: an `openapi` key is OpenAPI 3; a record or enum at the top
is Avro; `$schema`, `properties`, `$defs`, `definitions` or `type: object` is JSON Schema. When the
content fits none of them, or more than one, the command exits 2 and asks for `--from`. Swagger 2.0
is refused by name. YAML needs PyYAML (`pip install "sqllocks-shape[yaml]"`); JSON never does.

## How a column's generator is chosen

The same rules for every format, from the column's type and constraints:

| Source says | Generator |
|---|---|
| a one-column key, integer | `sequence` from 1 |
| a key, uuid | `uuid` |
| a key, string | `pattern` `{seq:N}` (N = `maxLength`, at most 8) |
| a reference to another table | `foreign_key` (`self_referencing` for a table that refers to itself) |
| `const` | `constant` |
| `enum` | `choice` over the values |
| integer with `minimum`/`maximum` | `distribution` `uniform` over the bounds (an exclusive bound on an integer moves in by one) |
| number with bounds | `distribution` `uniform`; without bounds `normal` |
| boolean | `weighted_enum` true/false |
| `format: date-time`, `date` | `temporal` over the model date range (the column is `timestamp` or `date`) |
| `format: uuid` | `uuid` |
| `format: email`, `uri`, `ipv4` | `faker` `email`, `url`, `ipv4` |
| `pattern`, when it is literals plus `\d{n}`, `[0-9]{n}` or `[A-Z0-9]{n}` | `pattern` (`{seq:n}`, `{random:n}`); any other regular expression is reported as not imported and the column is text |
| `maxLength` | `faker` text of at most that many characters (a `{random:n}` pattern under 5) |
| a well-known name (`email`, `city`, `status`, `first_name`, ...) | the DDL import's generator for that name |
| nullable | `nullable` and a `null_rate` of 0.05 (keys and `required` properties are not nullable) |
| anything else | `faker` text |

Keys: a table that has a column named `id` or `<table>_id` of an integer, string or uuid type
uses it; otherwise the importer adds an integer `id` column. Foreign key columns take the type of
the key they point at. Row counts: 1000 rows for a table with no foreign key, 2500 with one, ten
and a hundred times that for the `medium` and `large` presets.

A cycle of foreign keys (`A` refers to `B` and `B` to `A`, which the generator cannot order) is
broken at one reference, preferring a nullable reference between named schemas to the link of a
child table to its parent. That column becomes a plain integer column and the report lists it.

## JSON Schema (draft 2020-12 and draft 7)

| Source | Becomes |
|---|---|
| root object with `properties` | a table named by `title`, else the file name |
| root array of objects | the table of its items |
| a root with only `$defs`/`definitions` | one table per object definition |
| `$ref` to `#/...` in the document | followed; a `$ref` to an object is a table once (`#/$defs/Address` is table `Address`) |
| property that is a `$ref` to an object | a foreign key column `<property>_id` to that table |
| nested object (inline) | child table `<parent>_<property>` with a foreign key `<parent>_id` |
| array of objects (inline) | child table, as for a nested object |
| array of `$ref` objects | a nullable foreign key column `<parent>_id` on the referenced table |
| array of scalars | child value table: `id`, `<parent>_id`, `value` |
| `allOf` | the object branches merged into one table |
| `oneOf` / `anyOf` with one non-null branch | that branch, nullable |
| `type: ["string", "null"]` | nullable string |
| `enum`, `const`, `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `maxLength`, `pattern`, `format` | as in the table above |

Not imported (listed in the report): `oneOf`/`anyOf` with several non-null branches and a type
list with several non-null types (the column is a string); an object without `properties` (a map)
and an array of arrays or without `items` (a string column); a `$ref` that leaves the document
(external file or URL); `multipleOf`, `minLength`, `minItems`, `maxItems`, `uniqueItems`,
`contains`, `patternProperties`, `not`, `if`/`then`/`else`, `dependentRequired`,
`dependentSchemas`, `prefixItems` and the other keywords no generator reads; a definition that
nothing refers to when the root is an object; an exclusive bound on a non-integer (it is imported
as an inclusive bound).

## OpenAPI 3.0 and 3.1

One table per object schema of `components.schemas`, by the JSON Schema rules above:
`$ref` between schemas becomes a relationship. OpenAPI 3.0 `nullable: true` and 3.1 type lists are
read. Not imported (listed): `paths`, and the other component kinds (`parameters`, `responses`,
`requestBodies`, `headers`, `securitySchemes`, `links`, `callbacks`, `examples`); a schema that is
not an object (an enum or a string is used where it is referred to); `discriminator`; `oneOf`
over several schemas.

## Avro

| Source | Becomes |
|---|---|
| record | a table (the namespace is dropped from the name) |
| record defined inline in a field or an array | child table with a foreign key to its parent |
| a field that names a record | a foreign key column `<field>_id` |
| array of a named record | a nullable foreign key column on that record's table |
| array of scalars, enum or logical types | child value table |
| map of scalars | child table with `key` and `value` |
| `["null", T]` | nullable `T` |
| enum (inline or by name) | `choice` over the symbols |
| `int`, `long` | integer; `float`, `double` float; `boolean`; `string` |
| logical `decimal` | decimal with the declared precision and scale, generated in `0` to `10^(precision-scale) - 1` |
| logical `date`, `timestamp-millis`, `timestamp-micros`, `local-timestamp-*`, `time-*`, `uuid` | `date`, `timestamp`, `time`, `uuid` |

Not imported: `bytes` and `fixed` fields (the column is left out, as `shape from-ddl` leaves out
binary columns); a union with several non-null branches, an array of arrays, unions or maps, a map
of records or arrays (string columns); an unknown logical type (read as its base type);
`duration`; a null-only field.

## Protobuf (`proto3`)

Parsed in Python, without `protoc`. A message is a table (a nested message is `Outer_Inner`);
`import` is followed only inside the directory of the input (no absolute path, no `..`), and the
`google/protobuf` well-known imports are built in.

| Source | Becomes |
|---|---|
| message field of message type | a foreign key column `<field>_id` to that message's table |
| `repeated` message field | a nullable foreign key column on the message's table, pointing at the owner |
| `repeated` scalar or enum | child value table |
| `map<K, V>` of scalars or enums | child table with `key` and `value` |
| enum | `choice` over the value names |
| `optional` field, `oneof` member | nullable column |
| integer, `bool`, `string`, `float`, `double` types | integer, boolean, string, float |
| `google.protobuf.Timestamp` | `timestamp` |
| wrapper types (`StringValue`, `Int32Value`, ...) | nullable scalar |

Not imported: `bytes` fields; `Duration`, `Any`, `Struct`, `Value`, `ListValue`, `Empty` and
`FieldMask`; a map with a message value; that only one member of a `oneof` is set; `service`
definitions; a type from an `import` that was not found (the column is a string). A file that is
not `proto3`, uses `required` or `extend`, repeats a field number, starts an enum at a non-zero
value, or uses a type that is defined nowhere is malformed (exit 2, with the line).

## Pydantic v2

`--from pydantic module.path:ModelName` (or `path/to/models.py:ModelName`; a `.py` path alone
imports every model the file defines). The model's `model_json_schema()` is read by the JSON Schema
importer, so its rules apply: `Optional[int]` is a nullable integer, a nested model a child table,
a `list` of models a child table, an `Enum` a `choice`, `Field(ge=, le=, max_length=)` bounds.

**Security.** Reading a Pydantic model means importing the module, which runs that module's code
with your permissions. For that reason the command refuses without `--allow-import` (exit 2) and
imports nothing before that check. Use it only on code you would run anyway. Without Pydantic v2
installed the command exits 2 with that reason; the other formats do not need it. The other
importers never run input: they parse it.

## TMDL (Power BI semantic model)

Reads a `.tmdl` file, a folder of them, or a project folder with a `definition/` folder.

| Source | Becomes |
|---|---|
| `table` | a table |
| `column` with `dataType` `int64`, `double`, `decimal`, `string`, `boolean` | integer, float, decimal (precision 18, the scale of its `formatString`), string, boolean |
| `dataType: dateTime` | `date` when its `formatString` is `Short Date`, `Long Date` or `Medium Date`, else `timestamp` |
| `relationship` | a foreign key from the many side to the one side |
| `fromCardinality`/`toCardinality` | `many`/`one` (the default) is `one_to_many`; `one`/`many` swaps the sides; `one`/`one` is `one_to_one`; `many`/`many` is `many_to_many` |
| the key of a table | the column its relationships point at, else its `isKey` column, else a generated `id` |

Names may be quoted (`'Customer Master'`, a quote doubled). Not imported (listed in the report):
hierarchies (with their levels), measures, calculated columns, partitions, calculation groups,
roles, perspectives, cultures, expressions, `bothDirections` filtering, a `binary` column (left
out), a column with an unknown or missing `dataType` (a string). A date or other non-key-type
column that relationships point at is not made the key (a generated one is, and the generated dates
are not unique). An inactive relationship is imported as a foreign key and says so in the report.

## The report

`--report` writes a deterministic JSON document (no time stamp):

```json
{
  "format": "shape-import-report",
  "version": 1,
  "source": {"file": "order.schema.json", "format": "jsonschema"},
  "summary": {"imported": 31, "not_imported": 2},
  "imported": [
    {"element": "#/properties/quantity", "kind": "column",
     "became": "order.quantity (integer, distribution uniform)"}
  ],
  "not_imported": [
    {"element": "#/properties/mixed", "kind": "oneOf",
     "reason": "oneOf with 3 non-null branches: imported as string"}
  ]
}
```

`imported` lists every source element that became a table, column, generated column or
relationship and what it became; `not_imported` every element that was left out or approximated,
with the reason. `element` is a JSON Pointer (`#/...`) for JSON, YAML and Avro sources,
`Message.field` for Protobuf, and `table X/column Y` for TMDL. `version` is an integer: keys may be
added within a version, never removed or changed in meaning; a test holds a frozen version 1
report against the current output.

With `--strict`, any `not_imported` entry makes the command exit 1 and write no spec.

## Not covered

Importing data or statistics (these read structure only); writing JSON Schema, Avro or Protobuf
from a spec; publishing TMDL to a Fabric workspace or editing a Power BI project in place;
compiling Protobuf with `protoc`, or resolving `import` beyond the directory of the input. The
TMDL export of a star design is in [DESIGN.md](DESIGN.md#tmdl).
