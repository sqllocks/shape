# Verifying data: gates, quarantine and `shape verify`

`shape verify` checks tables you generated or received against a schema, with a set of
validation gates, and writes a report. The same gates, and a quarantine for what fails them,
are available from Python as `shape.quality`.

```bash
shape verify out/ --schema gates.json                   # a directory of Parquet, CSV or JSONL files
shape verify orders.csv --schema gates.json --strict    # exit 1 on warnings too
shape verify out/ --schema gates.json --statistical -o report.md   # add KS / chi-squared tests
shape verify out/ --schema gates.json -o report.json
```

Exit codes: `0` every gate passed, `1` a gate failed (or a warning under `--strict`), `2` the
input could not be read (missing path, no data files, unreadable schema).

Without `--schema` only row counts are reported. With one, `shape verify` runs:

| Gate | Fails (error) | Warns |
|---|---|---|
| `schema_conformance` | a declared table or column is missing | extra columns; a column type that does not fit the declared type |
| `null_constraint` | nulls (or NaN) in a column declared non-nullable | |
| `unique_constraint` | duplicates in the primary key | |
| `referential_integrity` | foreign-key values with no parent key | a relationship whose table is absent |
| `distribution` (`--statistical`) | never | KS test (`distribution`) or chi-squared test (`enum`) with p below 0.05 |

`distribution` needs scipy (`pip install "sqllocks-shape[scipy]"`) and is skipped with a
warning when it is not installed.

## The gate schema

The schema is a JSON document of Shape's own:

```json
{
  "format": "shape-gates",
  "version": 1,
  "tables": {
    "customer": {
      "primary_key": ["customer_id"],
      "columns": {
        "customer_id": {"type": "integer"},
        "gender": {"type": "string", "nullable": true, "enum": {"M": 0.49, "F": 0.51}},
        "spend": {"type": "float", "distribution": {"name": "lognorm", "params": {"s": 1.0, "scale": 20.0}}}
      }
    },
    "order": {"primary_key": ["order_id"], "columns": {"order_id": {"type": "integer"}, "customer_id": {"type": "integer"}}}
  },
  "relationships": [
    {"name": "placed_by", "parent": "customer", "child": "order",
     "parent_columns": ["customer_id"], "child_columns": ["customer_id"]}
  ]
}
```

Types: `integer`, `bigint`, `string`, `float`, `decimal`, `date`, `datetime`, `boolean`, `uuid`
(any other type is not type-checked). Columns are non-nullable unless `"nullable": true`.
`distribution.name` is a `scipy.stats` distribution and `params` its parameters.

`--schema` also accepts a profile made by `shape profile` (a `.shape` file, or its JSON): a
column is nullable if the profile saw nulls in it, and the primary keys and foreign keys the
profile detected are checked. Profile real data, then verify what you generated from it.

## From Python

```python
from shape.quality import GateRunner, ValidationContext, load_gate_schema, load_tables

tables = load_tables("out/")                       # {"customer": pa.Table, ...}
ctx = ValidationContext(tables=tables, schema=load_gate_schema("gates.json"),
                        config={"ranges": {"order.total": {"min": 0}}})
results = GateRunner().run_all(ctx)                # all nine gates
print(GateRunner.summary(results))
```

Four more gates are driven by `ValidationContext.config` rather than the schema:
`range_constraint` (`ranges`), `temporal_consistency` (`date_range`, `no_future`, `ordering`),
`schema_drift` (`baseline`: `{"table": {"columns": {"col": "int64"}}}`) and `file_format`
(`file_paths`: output files must exist, be non-empty and read in full). Register your own
with `GateRunner.register_gate(name, factory)`.

## Quarantine

```python
from shape.quality import QuarantineManager

qm = QuarantineManager(domain="retail")
qm.quarantine_table(tables["order"], "quarantine/", run_id="2026-10-01", table_name="order",
                    reason="orphan foreign keys", gate_name="referential_integrity")
qm.quarantine_file("out/order.parquet", "quarantine/", "2026-10-01", "failed verify")
qm.get_quarantine_report("quarantine/", "2026-10-01")
```

Each artifact lands in `quarantine/<domain>/<run_id>/` next to a `.._quarantine_meta.json`
(reason, gate, UTC time, original path). `domain`, `run_id` and table names are used as path
components, so Shape refuses any that is not a plain name (letters, digits, `.`, `_`, `-`).

## One command, two inputs

`shape verify` also checks signatures (see `docs/SIGNING.md`): when its argument is a `.shape`
file it verifies that artifact against `--key PUBLIC.pub`, and for any other path it runs the
gates above.
