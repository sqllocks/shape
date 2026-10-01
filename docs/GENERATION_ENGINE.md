# Generation engine

The engine turns a generation schema into tables, chunk by chunk. This page is the contract that
strategies, writers and domains build on.

## The generation schema

`shape.generation.schema.GenSchema` is the one model every route into generation produces: a
domain plugin, a profile fit, a DDL file or a hand-written JSON file. It holds

| Part | Contents |
|---|---|
| `model` | `name`, `domain`, `schema_mode` (`3nf` or `star`), `locale`, `seed`, `date_range` |
| `tables` | per table: `primary_key`, and ordered `columns`, each with a `type`, `nullable`, `null_rate` and a `generator` (a `strategy` name plus that strategy's keys) |
| `relationships` | parent/child links with column lists and a type |
| `business_rules` | `cross_column`, `cross_table` and `constraint` rules, `A OP B` |
| `generation` | the current `scale`, the `scales` presets (rows per table), and `derived_counts` |
| `correlated_columns` | per table, `[column_a, column_b, r]` triples |

`GenSchema.to_dict()` and `GenSchema.from_dict()` read and write the JSON document described by
`shape/schemas/generation-schema-v1.json`; `GenSchema.validate()` reports semantic problems
(missing keys, unresolved references, generators missing required keys) as `error` or `warning`.

## Planning

* **Table order**: `resolve_order(schema)`. Kahn's algorithm over foreign-key columns and
  relationships, ties broken by name. A cycle raises `CircularDependencyError`, a reference to an
  undefined table `MissingTableError`; a self-reference is not a cycle.
* **Levels**: `dependency_levels(schema)` groups the order into levels of tables that do not depend
  on each other. The engine generates, and returns, tables level by level.
* **Row counts**: `calculate_row_counts(schema, overrides)`: the current preset, then `fixed`,
  `per_parent` x `ratio` and `per_year` counts in the order the schema lists them, then overrides,
  then 100 for any table still without a count.
* **Column order**: `order_columns(table)`: sequence and UUID primary keys, then foreign keys, then
  independent columns, then dependent ones (formula, lookup, derived, conditional, ...), then
  computed ones. A table's output columns come out in this order.
* **Dry run**: `Engine.dry_run()` returns the order, levels, rows, columns, a memory estimate, every
  schema issue and every strategy that cannot be found, and generates nothing.

## Chunks and random access

```python
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema

engine = Engine(GenSchema.from_dict(doc), scale="medium", seed=7)
result = engine.generate()                      # every table, all post-passes applied
chunk = engine.generate_chunk("order", 5_000, 1_000)   # rows 5000..5999, before post-passes
for batch in engine.iter_chunks("order", 65_536):      # record batches, before post-passes
    ...
```

A chunk is any row range of one table. The output of `generate()` is identical for every
`chunk_rows`, because every random number is addressed by row, not by chunk.

### Random streams

`shape.generation.rng.RowStream(seed, table, column, label)` is one Philox4x64-10 stream. Its
`j`-th 64-bit word is word `j % 4` of `numpy.random.Philox(key=key, counter=j // 4).random_raw(4)`,
where `key` is the first 16 bytes of `blake2b(seed, table, column, label)` read as a little-endian
128-bit integer. A row owns `per_row` consecutive words, so a row's numbers are a function of
`(seed, table, column, label, row)` alone. Strategies draw from their own stream (`label` names the
use); the engine uses the labels `null` (the null mask) and `fix:<rule>` (rule repairs).

A strategy that keys its randomness by row gives the same value for a row whatever the chunking.
`tests/generation/test_gen_engine.py` shows that a strategy keyed by chunk does not.

### Strategies

A strategy is any object with a `name` and `generate(spec, ctx)` (plugin group
`shape.strategies`). `spec` is the column's `generator` dict. `ctx` is an `EngineContext`: the
plugin API's `GenerationContext` (`seed`, `table`, `column`, `chunk`, `row_start`, `n_rows`, and
`columns`, the columns of this chunk built so far) plus `engine` and `column_def`. It returns an
Arrow array of `n_rows` values. A strategy that makes several columns returns a mapping of names to
arrays; names that are not columns of the table are internal: later strategies read them from
`ctx.columns`, but they are not output.

`ctx.engine.key_pool(table)` gives the keys of a table's single-column primary key for foreign
keys: a `RangeKeys` (O(1) memory) for a sequence key, an `ArrayKeys` otherwise, both with
`take(indices)`.

The engine applies `null_rate` itself, after the strategy, from the `null` stream. A column without
a generator is all null; a `computed` column is a null placeholder until the compute phase.

## Post-passes

They need whole tables, so `generate()` runs them and `iter_chunks()` does not. In order:

1. **Compute phase** (`compute.py`): fills `computed` columns from another table: `sum_children`,
   `count_children`, `avg_children`, `min_children`, `max_children` (rows without children get 0;
   decimals are rounded to 2 places), or `lookup_parent`.
2. **Business rules** (`rules.py`): `validate_rules` lists the rules the data breaks;
   `fix_rules` repairs `cross_column` (`<`, `>`) and `cross_table` (`>=`, `>`, `<=`) rules and
   returns what still violates. Repairs draw from the `fix:<rule>` stream.
3. **Correlation** (`correlation.py`): a Gaussian copula reorders the values of the numeric columns
   named in `correlated_columns` to match the target correlations, leaving every column's values
   unchanged. Key-like columns and columns with nulls are not reordered.

## Reading SQL DDL

`shape from-ddl FILE` turns `CREATE TABLE` statements (SQL Server, PostgreSQL, MySQL and ANSI SQL,
plus `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`) into a generation schema.

```
shape from-ddl tables.sql                      # writes tables.gen.json
shape from-ddl tables.sql -o shop.gen.json --domain shop -s medium:customer=5000,order=25000
shape from-ddl tables.sql --no-smart           # type and name heuristics only
shape from-ddl tables.sql --explain            # print every inference decision
```

| Option | Meaning |
|---|---|
| `-o`, `--output` | where to write the schema (default: the input with the suffix `.gen.json`) |
| `--domain` | the schema's domain; the model is named `<domain>_ddl_import` (default `custom`) |
| `-s`, `--scale` | `preset:table=N,...`: select that scale preset and set those tables' row counts |
| `--smart` / `--no-smart` | smart inference (the default), or keep the first generators |
| `--explain` | print the inference report: rule, table and column, what changed, confidence |

In Python: `shape.generation.ddl.from_ddl(sql, domain=, smart=, scale=)` returns
`(GenSchema, annotations)`; `DdlParser` is the parser alone.

**First generators.** Every column gets one from its type (integers a uniform range, decimals a
normal, dates a uniform date in the model's range, `bit` a weighted enum, `uuid`) and, for strings,
from its name (`email`, `first_name`, `city`, `status`, ... and the suffixes `_name`, `_code`,
`_type`, `_status`, `_date`). Identity, serial and auto-increment columns, and single-column
primary keys, are sequences. A declared foreign key is a `foreign_key` column and a relationship (a
self-reference is `self_referencing`). Column names ending `_id` that match a table by its
singular or plural name (`order_id` to `order` or `orders`, `category_id` to `categories`) are
foreign keys too. Binary columns are left out. A column-level `REFERENCES` clause is not read:
declare keys as `FOREIGN KEY (...)` constraints. Scale presets are 1k, 10k and 100k rows for
tables with no parent, and 2.5k, 25k and 250k for the rest.

**Smart inference** (`shape.generation.ddl_infer`) replaces only placeholder generators, in this
order:

1. *Table roles*: entity, transaction, transaction detail, lookup, hierarchy, bridge, log, and
   `dim_`/`fact_` tables.
2. *Column semantics*: money, quantity, percentage, measurement, rating, status, category, flags,
   the kinds of date, contact fields, codes and text, from the name (CamelCase is read as words)
   and the type.
3. *Foreign-key distributions*: pareto, zipf or uniform by the roles at both ends, and
   `null_rate` 0.15 on a nullable key.
4. *Row counts*: lookup and hierarchy tables get fixed counts (20 to 200, and 50); other tables
   a ratio per parent (bridge 3, address 1.5, detail 2.5, transaction 5, log 10, return 0.15);
   root tables get 1k, 50k and 500k presets.
5. *Numeric distributions*: log-normal money and quantities, a bounded normal for percentages and
   ratings, a normal per kind of measurement.
6. *Status and category value mixes*, by role and by name.
7. *Dates*: seasonal order dates (a Q4 lift, fewer weekends), an end date derived from the start
   date, birth dates 18 to 65 years before the model's end.
8. *Correlations*: cost from price, tax from subtotal or amount, discount from price or total,
   total as quantity times unit price, net as gross minus tax, margin as price minus cost.
9. *Business rules*: end not before start, modified not before created, cost not above price, a
   child's transaction date not before its parent's, money not negative, quantities at least 1,
   percentages 0 to 100, ratings 1 to 5.

`tests/generation/test_ddl.py` tests each step. The reference-comparison harness under
`benchmarks/` checks the import against the reference implementation's on its own DDL fixtures and on
cases that fire every rule: all equal, in every field.

## Writers

Every output format is a `shape.sinks` plugin (`write(uri, table, batches, **options) -> rows`).
`shape.generation.output` puts them behind the engine:

```python
from shape.generation.output import write_engine, write_result, format_summary

write_result(result, "parquet", "out/")                         # a finished GenerationResult
write_engine(engine, "csv", "out/", chunk_rows=65_536)          # streams when no post-pass is needed
print(format_summary(result))                                   # the `summary` output
```

| Format | File | Notes |
|---|---|---|
| `csv`, `tsv` | `<table>.csv`, `<table>.tsv` | header row, nulls are empty fields |
| `jsonl` | `<table>.jsonl` | dates and times ISO 8601; decimals are exact strings |
| `parquet` | `<table>.parquet` | snappy, dictionary encoding on (T-17) |
| `sql` | `<table>.sql` | see below |
| `excel` | `<table>.xlsx` | extra `[excel]`; refuses a table over 1,048,575 rows |
| `delta` | `<dir>/<table>/` | extra `[delta]`; `mode` (`overwrite`, `append`), `partition_by` |

`write_result` writes tables in parallel. `write_engine` overlaps generation of chunk *n* + 1 with the
write of chunk *n* when the schema has no post-pass (`needs_post_pass`: computed columns, business
rules or correlations need whole tables), and otherwise writes `engine.generate()`.

### SQL options

`sql_dialect` (`tsql`, `tsql-fabric-warehouse`, `postgres`, `mysql`), `schema_name`, `batch_size`
(rows per `INSERT`), `ddl`, `drop`, `go` (the `--sql-ddl`, `--sql-drop` and `--sql-go` switches).
The generation schema supplies column types, nullability and the primary key
(`sql_options(schema, table)`). The script has no timestamp, so equal input gives equal bytes.
Choices that differ from a naive port: integers are `BIGINT` in every dialect; T-SQL batches are
capped at 1,000 rows (the server's limit for one `VALUES` list) whatever `batch_size` says;
`tsql-fabric-warehouse` uses `VARCHAR` and `DATETIME2(6)`, writes the primary key as a comment (the
warehouse does not enforce it) and emits no `DISTRIBUTION` clause; `NaN` and infinities become
`NULL`; MySQL string literals escape backslashes.
