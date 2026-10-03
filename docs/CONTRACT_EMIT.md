# Emitting a contract: DDL, JSON Schema, pandera, Great Expectations

A v1 contract (the file `shape check` reads, `src/shape/contracts/v1.py`) says what a table must
hold. `shape to-dbt-tests` turns it into dbt tests ([DBT.md](DBT.md)). `shape contract emit` turns
it into the other places data is validated: database constraints, a JSON Schema for a row payload,
a pandera schema for a Python pipeline, or a Great Expectations suite. It does not run any of them.

```bash
shape contract emit examples/contracts/orders.contract.json --to ddl -o orders.sql
shape contract emit orders.contract.json --to ddl --dialect postgres -o orders.pg.sql
shape contract emit orders.contract.json --to jsonschema -o orders.schema.json
shape contract emit orders.contract.json --to pandera -o orders_schema.py
shape contract emit orders.contract.json --to gx -o orders_suite.json
shape contract emit orders.contract.json --to jsonschema --strict    # exit 1 if anything is lost
```

The same contract and options always give the same bytes (no timestamp, no id, sorted keys).
What a target cannot say is **listed, never silently dropped** (`not_expressed`), and kept as
metadata where the target has a place for it.

## Command

`shape contract emit CONTRACT.json --to ddl|jsonschema|pandera|gx [-o OUT] [--table NAME]
[--dialect tsql|tsql-fabric-warehouse|postgres|mysql] [--strict] [--json]`

| Flag | Meaning |
|---|---|
| `--to TARGET` | `ddl`, `jsonschema`, `pandera` or `gx` (required) |
| `-o OUT` | write the text here (default: print it) |
| `--table NAME` | for a `tables` contract, emit this table (`jsonschema` and `gx` emit one table and need it); for a single-table contract, the table's name (default: the file name without `.json` and `.contract`) |
| `--dialect D` | `ddl` only. Default `tsql` |
| `--strict` | exit 1 when any rule cannot be expressed: the list is printed (stderr) and nothing is written |
| `--json` | print the result (below) as JSON on stdout |

Exit codes: **0** written; **1** `--strict` and something is not expressible; **2** a malformed
contract (the `ContractError` message that `shape check` gives), an unknown target, an unknown
dialect or table, or `--dialect` with a target other than `ddl`.

### The result: `shape-contract-emit`, version 1

`--json` prints, and `shape.contracts.emit.emit(contract, target, **options)` returns, an
`EmitResult`; `to_dict()` is:

```json
{
  "format": "shape-contract-emit",
  "not_expressed": [
    {"table": "orders", "column": "amount", "rule": "distribution", "reason": "..."},
    {"table": "orders", "column": null, "rule": "fd", "reason": "..."}
  ],
  "target": "gx",
  "text": "...",
  "version": 1
}
```

`column` is `null` for a table-level rule. `not_expressed` is sorted by table, column and rule. A
newer `version` than this Shape reads is refused with a message (the compatibility policy,
[API_STABILITY.md](API_STABILITY.md)). The version-1 document is pinned by
`tests/contracts/emit/test_emit_compat.py`.

### Python

```python
from shape.contracts.emit import emit, contract_from, expressible

result = emit("orders.contract.json", "jsonschema", table="orders")
result.text, result.not_expressed
contract_from("jsonschema", result.text)                 # the expressible part, as a contract
contract_from("jsonschema", result.text, use_meta=True)  # all of it, recovered from x-shape
expressible(contract, "jsonschema")                      # what contract_from returns, computed
```

`contract_from` reads `jsonschema` and `gx` documents; the result is one table's contract in a
normal form (rules that say nothing, such as `nullable: true`, are dropped; `required_columns` is
sorted; the null rate has nine places; a `None` in `allowed_values` is removed because
`nullable` decides about nulls). A schema or suite written by hand is read for the parts it has.

## Mapping tables

A contract `pattern` is a label the profiler gives a column when at least 90% of a sample matches
(`email`, `uuid`, `ssn`, `mac_address`, `ip_address`, `iban`, `postal_code`, `date`, `phone`,
`currency_code`, `language_code`); the emitted check uses that label's regular expression.
JSON Schema, pandera and the database test it on every row, which is stricter than the profile;
Great Expectations keeps the 90% (`mostly`).

A column the contract lists but does not name in `required_columns` is optional in the emitted
schema and suite (`shape check` reports it missing). A column with no rules is dropped.

### `ddl`

CREATE TABLE through the SQL sink's table emitter (the same quoting and type spelling as
`shape design`), with the constraints inside the table.

| Contract rule | DDL |
|---|---|
| `dtype` | column type: `integer` BIGINT, `float`, `string` VARCHAR (widened to the longest allowed value), `boolean`, `date`, `datetime` timestamp |
| `nullable: false` | `NOT NULL` (others `NULL`) |
| `unique: true` | `CONSTRAINT UQ_<table>_<column> UNIQUE (col)`; Fabric Warehouse: `UNIQUE NONCLUSTERED (col) NOT ENFORCED` |
| `min`, `max` (numbers) | `CHECK (col BETWEEN min AND max)`, or `>= min` / `<= max` for one bound |
| `allowed_values` | `CHECK (col IN (...))` |
| `pattern` | `CHECK (col ~ 'regex')` on PostgreSQL, `REGEXP` on MySQL |
| `required_columns`, `allow_extra_columns: false` | the table has exactly its columns |

Constraint names longer than the dialect allows are cut and get a short hash, so they stay unique
and stable. The text parses with `shape from-ddl` for every dialect (a test checks it).

Not expressible: `max_null_rate` (NULL is per column, not a share), `distribution`,
`min_true_rate` / `max_true_rate`, `no_placeholder`, `row_count`, the joint rules, `pattern` on
T-SQL (no regular-expression test), `min` / `max` / `allowed_values` on Fabric Warehouse (it has no
`CHECK` constraint; only `UNIQUE` and friends, which are never enforced there), a bound that is not
a number, an empty `allowed_values`, an unknown `dtype` or label. A column with no `dtype` is typed
from its rules (a number bound gives `integer` or `float`, otherwise `string`) and reports it.

### `jsonschema`

A draft 2020-12 schema for one row. Check it with `shape.schemacheck` (which now understands
`maximum`, `pattern` and `not`) or any 2020-12 validator.

| Contract rule | JSON Schema |
|---|---|
| `dtype` | `type`: `integer`, `number` (float), `string`, `boolean`; `date` / `datetime` are a `string` with `format` `date` / `date-time` |
| `nullable` | a nullable column's `type` also lists `"null"` and its `enum` holds `null`; `nullable: false` leaves them out (`{"not": {"type": "null"}}` with no `dtype`) |
| `min`, `max` | `minimum`, `maximum` |
| `allowed_values` | `enum` |
| `pattern` | `pattern` |
| `required_columns` | `required` (a column named only there is a property with no keywords) |
| `allow_extra_columns: false` | `additionalProperties: false` |

Not expressible: `unique` (a property of the table, not of a row), `max_null_rate`,
`distribution`, true rates, `no_placeholder`, every table-level rule (`row_count` and the joint
rules), non-numeric bounds, an empty `allowed_values`, an unknown `dtype` or label. They are kept as
`x-shape`: on the property for a column, and in the root's `x-shape.rules` for the table; the root
`x-shape` also declares `format` and `version`.

### `pandera`

Python source with a `SCHEMAS` dict of `DataFrameSchema`, one per table. It is generated as text:
emitting imports neither pandera nor pandas. It imports `pandera.pandas`, or `pandera` before 0.24.

| Contract rule | pandera |
|---|---|
| `dtype` | `Column(dtype)`: `"int64"`, `"float64"`, `"str"`, `"bool"`, `"datetime64[ns]"`, `pa.Date`; a nullable integer is `"Int64"` and a nullable boolean `"boolean"`, because numpy's cannot hold a null |
| `nullable` | `nullable=` (pandera's default is `False`, so a column that may be null says `True`) |
| `unique: true` | `unique=True` |
| `min`, `max` | `Check.in_range(min, max)`; `Check.ge(min)` or `Check.le(max)` for one bound |
| `allowed_values` | `Check.isin([...])` |
| `pattern` | `Check.str_matches(regex)` |
| `row_count` | a table `Check` on `len(df)` |
| `required_columns` | `required=True` (listed but not required: `required=False`) |
| `allow_extra_columns: false` | `strict=True` |

Not expressible: `max_null_rate` (a `Column` has no tolerated share of nulls), `distribution`,
true rates, `no_placeholder`, the joint rules, non-numeric bounds, an empty `allowed_values`, an
unknown `dtype` or label. They are kept in `metadata={"shape": {...}}` on the column or the schema.
There is no reader for pandera.

### `gx`

A Great Expectations 1.x suite as JSON (`gx.ExpectationSuite(**json.loads(text))`), no id and no
timestamp. Each expectation names its contract rule in `meta.shape_rule`.

| Contract rule | Expectation |
|---|---|
| `required_columns` | `expect_column_to_exist` |
| `nullable: false` | `expect_column_values_to_not_be_null` |
| `max_null_rate` | `expect_column_values_to_not_be_null` with `mostly` = 1 - rate |
| `unique: true` | `expect_column_values_to_be_unique` |
| `min`, `max` | `expect_column_values_to_be_between` |
| `allowed_values` | `expect_column_values_to_be_in_set` |
| `pattern` | `expect_column_values_to_match_regex` with `mostly` 0.9 |
| `row_count` | `expect_table_row_count_to_be_between` |
| `allow_extra_columns: false` | `expect_table_columns_to_match_set` (`exact_match`) |

Not expressible: `dtype` (a GX type name depends on the backend: pandas, Spark or SQL),
`distribution`, true rates, `no_placeholder`, the joint rules, non-numeric bounds, an empty
`allowed_values`, an unknown label. They are kept in the suite's `meta.shape` (`columns` and
`rules`).

## Worked example

`examples/contracts/orders.contract.json` is a single-table contract with a bit of everything:

```json
{
  "row_count": {"min": 1, "max": 1000000},
  "required_columns": ["order_id", "customer_email", "status", "amount", "placed_at"],
  "allow_extra_columns": false,
  "columns": {
    "order_id": {"dtype": "integer", "nullable": false, "unique": true, "min": 1},
    "customer_email": {"dtype": "string", "nullable": false, "pattern": "email"},
    "status": {"dtype": "string", "nullable": false,
               "allowed_values": ["new", "paid", "shipped", "cancelled"]},
    "amount": {"dtype": "float", "nullable": false, "min": 0, "max": 100000,
               "distribution": "lognormal"},
    "discount_code": {"dtype": "string", "max_null_rate": 0.8},
    "is_gift": {"dtype": "boolean", "min_true_rate": 0.01, "max_true_rate": 0.2},
    "placed_at": {"dtype": "datetime", "nullable": false}
  },
  "fd": [{"determinant": "order_id", "dependent": "customer_email", "min_confidence": 1}]
}
```

`--to ddl` (T-SQL):

```sql
CREATE TABLE [orders] (
    [order_id]                     BIGINT               NOT NULL,
    [customer_email]               NVARCHAR(255)        NOT NULL,
    [status]                       NVARCHAR(255)        NOT NULL,
    [amount]                       FLOAT                NOT NULL,
    [discount_code]                NVARCHAR(255)        NULL,
    [is_gift]                      BIT                  NULL,
    [placed_at]                    DATETIME2            NOT NULL,
    CONSTRAINT [UQ_orders_order_id] UNIQUE ([order_id]),
    CONSTRAINT [CK_orders_order_id_range] CHECK ([order_id] >= 1),
    CONSTRAINT [CK_orders_status_values] CHECK ([status] IN (N'new', N'paid', N'shipped', N'cancelled')),
    CONSTRAINT [CK_orders_amount_range] CHECK ([amount] BETWEEN 0 AND 100000)
);
GO
```

`--to jsonschema` (one property shown):

```json
"amount": {"maximum": 100000, "minimum": 0, "type": "number",
           "x-shape": {"distribution": "lognormal"}}
```

`--to pandera` (one column shown):

```python
"amount": Column("float64", nullable=False, checks=[Check.in_range(0, 100000)],
                 metadata={"shape": {"distribution": "lognormal"}}),
```

`--to gx` (the expectation of `amount`):

```json
{"type": "expect_column_values_to_be_between",
 "kwargs": {"column": "amount", "min_value": 0, "max_value": 100000},
 "meta": {"shape_rule": "min_max"}}
```

What each target leaves out for this contract (`not_expressed`, columns in order):

| Rule | ddl (tsql) | jsonschema | pandera | gx |
|---|---|---|---|---|
| `row_count` (table) | not expressed | not expressed | `Check` on `len(df)` | `expect_table_row_count_to_be_between` |
| `fd` (table) | not expressed | not expressed | not expressed | not expressed |
| `order_id.unique` | `UNIQUE` | not expressed | `unique=True` | `..._to_be_unique` |
| `customer_email.pattern` | not expressed (T-SQL) | `pattern` | `str_matches` | `match_regex` (`mostly` 0.9) |
| `amount.distribution` | not expressed | not expressed | not expressed | not expressed |
| `discount_code.max_null_rate` | not expressed | not expressed | not expressed | `mostly` 0.2 |
| `is_gift.min_true_rate`, `max_true_rate` | not expressed | not expressed | not expressed | not expressed |
| every `dtype` | column type | `type` | `Column(dtype)` | not expressed |

`shape contract emit orders.contract.json --to jsonschema --strict` therefore exits 1 and prints
the list; without `--strict` the file is written and the list is in `--json`.

Rebuilding the contract from the schema:

```python
from shape.contracts.emit import contract_from
contract_from("jsonschema", text)                  # what the schema states
contract_from("jsonschema", text, use_meta=True)   # the whole contract, from x-shape
```

## What is out of scope

dbt has its own command (`shape to-dbt-tests`, [DBT.md](DBT.md)). Running the emitted artifacts
against a database or a data frame is not part of `shape check`. Reading a pandera schema or a GX
suite written by hand is not supported (a hand-written JSON Schema or suite is read for what
`contract_from` understands, nothing more).
