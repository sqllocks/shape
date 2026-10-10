# Generation engine

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" GENERATION_ENGINE
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for GENERATION_ENGINE
    ```


The engine turns a generation schema into tables, chunk by chunk. This page is the contract that
strategies, writers and domains build on.

## The generation schema

`shape.generation.schema.GenSchema` is the one model every route into generation produces: a
domain plugin, a profile fit, a DDL file or a hand-written JSON file. It holds

| Part | Contents |
|---|---|
| `model` | `name`, `domain`, `schema_mode` (`3nf` or `star`), `locale`, `seed`, `date_range` |
| `tables` | per table: `primary_key`, and ordered `columns`, each with a `type`, `nullable`, `null_rate`, a `generator` (a `strategy` name plus that strategy's keys) and an optional `identity` (below) |
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
  then 100 for any table still without a count. A count that is not a whole, non-negative number
  (a `ratio` that is text or negative, an override of -5), or a `per_year` count without a
  `date_range` start and end in order, is a `ShapeSchemaError` naming the table.
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

`RowStream` reads through the selected kernel (`docs/GENERATION_KERNEL.md`): `raw`, `uniform` and
`normal` take `per_row` and `slot`, and `stream_key`, `uniform_from_raw` and `normal_from_raw` are the building blocks, and `derive(suffix)` gives a separate stream for a sub-draw
(a rejection attempt, a mixture component). Strategies are written against
`docs/GENERATION_STRATEGIES.md`, timestamps and calendars against `docs/GENERATION_CALENDARS.md`.

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

Two more services serve strategies that need a whole column or a whole-table result:

* `engine.generate_column(table, column, row_start, n_rows)` builds one column of a row range (the columns
  before it in generation order, which are all it can depend on) and returns it with nulls applied, equal to the same column of
  `generate_chunk`.
* `engine.cached(key, build)` runs `build()` once per engine and `key`. Strategies keep results that are
  computed from the schema and seed alone here (the first row of each parent, the versions of each business
  key, a capped foreign key), so a chunk read in any order finds the same value.

The engine applies `null_rate` itself, after the strategy, from the `null` stream. A column without
a generator is all null; a `computed` column is a null placeholder until the compute phase.

## Post-passes

They need whole tables, so `generate()` runs them and `iter_chunks()` does not. In order:

1. **Correlation** (`correlation.py`): a Gaussian copula reorders the values of the numeric columns
   named in `correlated_columns` to match the target correlations, leaving every column's values
   unchanged. Key-like columns and columns with nulls are not reordered, unless the schema's
   `generation.output.copula_nulls` is `"rank"` (what `shape generate --from` writes): then the
   non-null values are reordered among the non-null rows and the nulls stay where they are.
   Pairs with `|r|` below 0.5 are ignored, unless `generation.output.copula_threshold` lowers
   that (`shape generate --from` writes 0 and lists only the pairs it wants). It runs first, so
   the passes after it see the reordered rows and keep their results: a `computed` sum matches its
   child rows and a repaired rule holds (a `computed` column is still empty when it runs, so it is
   never reordered).
2. **Compute phase** (`compute.py`): fills `computed` columns from another table: `sum_children`,
   `count_children`, `avg_children`, `min_children`, `max_children` (rows without children get 0;
   decimals are rounded to 2 places), or `lookup_parent`.
3. **Business rules** (`rules.py`): `validate_rules` lists the rules the data breaks (an
   `A OP B` comparison, or a `constraint` `x BETWEEN low AND high`, both ends included);
   `fix_rules` repairs `cross_column` (`<`, `>`) and `cross_table` (`>=`, `>`, `<=`) rules and
   returns what still violates. Repairs draw from the `fix:<rule>` stream. `remaining_violations`
   is what the finished tables break.

   **Mixed-type copula** (`copula_mixed.py`, opt in with `shape generate --from PROFILE
   --mixed-copula`): the schema's `generation.output.copula_mixed` block (`format`
   `shape.copula-mixed`, `version` 1, one entry per table: the stored column order, the categories
   in their stored order, the latent correlation matrix) reorders numeric and categorical columns
   after the numeric copula. See `docs/JOINT.md`.

## From data: `learn`, `generate --from` and `plan`

Two commands turn data into a generation schema, for different jobs.

`shape learn PATH -o SCHEMA.json` profiles CSV, Parquet or JSON Lines files (a directory is one table
per file) and writes a schema that `shape generate SCHEMA.json` runs: a strategy per column (key,
foreign key, pattern, date, enum, numeric distribution, text), relationships, scale presets
(`small`, `medium` and `large`, from the row counts) and correlated column pairs (|r| of at least
0.5). The rules are `shape.generation.learn.SchemaBuilder`'s; a few go beyond the obvious mapping,
each named with its reason in `learn.DIFFERENCES` (a numeric column
with more distinct values than its profile lists is not an enum; an exponential fit is generated as
an exponential; a log-normal fit with a material location shift is generated from the quantiles;
currency and language codes keep their own values). A low-cardinality column's values are written
into the schema as its weights, so a schema learned from personal data carries those values: e-mail,
phone, ssn and ip patterns never are.

`shape generate --from X.shape` generates from a profile at its row counts, through
`shape.generation.fit.fit_schema`, which starts from the learned schema and then fits it for
generation:

| Profile | Generated by |
|---|---|
| enum column | `weighted_enum` with the profile's exact weights; integers and booleans keep their type (`output_type`) |
| numeric column | the fitted family (normal, uniform, log-normal, exponential) or, when the fit is poor, the profile's quantiles; decimals read off the minimum and maximum |
| null rate | the column's `null_rate`, per row, independently of the other columns |
| correlation | a Gaussian copula over the numeric columns; each pair's `rho` is calibrated (`calibrate_correlation`) so the generated Pearson correlation matches the profile's, and nulls are skipped by rank (`copula_nulls: "rank"`) |
| timestamp column | the month, weekday and hour weights within the observed range; a column of midnights is generated at midnight (`granularity: "day"`) |
| key | a sequence from the profile's first value, a uuid, or a draw from the parent's keys |
| text with a pattern | a built-in provider in that format (e-mail, phone, ip address, postal code); otherwise a provider guessed from the column name |

`shape plan X.shape` prints, for every field of every column, table and the dataset, whether
generated data keeps it: `preserved` (to sampling error), `approximate` (kept roughly, the reason
says how) or `not_modelled` (generation ignores it). Free-text values and their lengths, which
values are missing together, dependencies between columns other than the correlations, and the order
of rows are `not_modelled`. `tests/generation/test_fit.py` generates from a profile, profiles the
result and checks every field the plan calls `preserved` (five standard errors for a statistic,
equality for structure), so the plan cannot claim more than the data shows. `plan` also reads
portable evidence documents (`shape capture`) and checks each item against what the generator can
build from it.

## Identity columns

A column may carry `"identity": true` (an optional boolean key of the version-1 document, absent
from a document written before it and omitted when false). The column must be of type `integer`
and use the `sequence` strategy; anything else is a validation error naming the column
(`tables.T.columns.C`). The SQL Server writer creates it as `BIGINT IDENTITY(start, step)` from the
sequence's `start` and `step` and, by default, keeps the generated values with `SET IDENTITY_INSERT`
(`docs/SINKS.md`); the `sql` script sink does the same for the `tsql` dialect. A schema without
`identity` produces byte-identical DDL and scripts.

## Reading SQL DDL

`shape from-ddl FILE` turns `CREATE TABLE` statements (SQL Server, PostgreSQL, MySQL and ANSI SQL,
plus `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`) into a generation schema. Foreign keys are
read wherever the dialect writes them: a table-level `FOREIGN KEY` constraint, an `ALTER TABLE`, or
a column-level `REFERENCES` clause.

[Run this example](#local-example-1).


| Option | Meaning |
|---|---|
| `-o`, `--output` | where to write the schema (default: the input with the suffix `.gen.json`) |
| `--domain` | the schema's domain; the model is named `<domain>_ddl_import` (default `custom`) |
| `-s`, `--scale` | `preset:table=N,...`: select that scale preset and set those tables' row counts (not negative; a table the file does not define stays in the preset, and `validate` warns about it) |
| `--smart` / `--no-smart` | smart inference (the default), or keep the first generators |
| `--explain` | print the inference report: rule, table and column, what changed, confidence |

`IDENTITY`, `SERIAL` / `BIGSERIAL` / `SMALLSERIAL` and `AUTO_INCREMENT` columns become `sequence`
columns with `"identity": true`.

In Python: `shape.generation.ddl.from_ddl(sql, domain=, smart=, scale=)` returns
`(GenSchema, annotations)`; `DdlParser` is the parser alone.

**First generators.** Every column gets one from its type (integers a uniform range, decimals a
normal, dates a uniform date in the model's range, `bit` a weighted enum, `uuid`) and, for strings,
from its name (`email`, `first_name`, `city`, `state`, `status`, ... and the suffixes `_name`,
`_code`, `_type`, `_status`, `_date`). A one-character string column (`gender CHAR(1)`) holds a
code, so it gets a short value set chosen by the words of its name (`gender` and `sex` M or F,
status words A, I or P, flags such as `is_active` Y or N, any other code A, B or C); a `gender` or
`sex` column of any length is M or F. Identity, serial and auto-increment columns, and
single-column primary keys, are sequences. A declared foreign key is a `foreign_key` column and a
relationship (a self-reference is `self_referencing`), whether it is a table-level constraint or a
column-level clause such as `customer_id INT REFERENCES customer(id)` (with or without
`CONSTRAINT name` and `ON DELETE ...`; `REFERENCES customer` alone means the parent's primary
key). A foreign key that is also the table's whole primary key
(`customer_profile.customer_id`) is a one-to-one child: it takes each parent at most once, and the
table has as many rows as its parent. A primary-key column is never generated null. Column names ending `_id` or `Id` (`customer_id`, `CustomerId`, `CustomerID`) that match a
table by its singular or plural name (`order_id` to `order` or `orders`, `category_id` to
`categories`) are foreign keys too, when the DDL does not declare them: the key points at that
table's primary key, whatever it is called, and is left out (the column stays a plain number) when
the table has no single-column primary key. A name written without separators (`orderdate`) is one
word and matches no rule. Types are read as the dialects write them: quoted (`[decimal](18, 2)`), with MySQL's `UNSIGNED`
and `ZEROFILL`, PostgreSQL's `WITH TIME ZONE`, `DOUBLE`; a MySQL `ENUM('a', 'b')` draws its values,
and a MySQL `KEY idx (a)` line is an index, not a column. Binary columns (`VARBINARY`, `BINARY`, `VARBINARY(MAX)`, `IMAGE`, `BYTEA`,
and the `BLOB` types) are left out. `MAX` is a length like any other. A generated string never
exceeds its column: a code in `CHAR(2)` is two random characters (`A7`), and a value set keeps
only the values that fit (a `status` in `VARCHAR(7)` is active or pending; in `CHAR(2)` a code set
A, I or P); text from the name rules is cut at the length. Scale presets are 1k, 10k and 100k
rows for tables with no parent, and 2.5k, 25k and 250k for the rest.

**Smart inference** (`shape.generation.ddl_infer`) replaces only placeholder generators, in this
order:

1. *Table roles*: entity, transaction, transaction detail, lookup, hierarchy, bridge, log, and
   `dim_`/`fact_` tables.
2. *Column semantics*: money, quantity, percentage, measurement, rating, status, category, flags,
   the kinds of date, contact fields, codes and text, from the name and the type. Names are matched
   by whole words, with CamelCase and snake_case split (and a plural matching its singular):
   `discount_pct` is a percentage (not a quantity, though it contains "count"), `current_value` is
   not money ("rent"), `model` is not a category ("mode"), a `catalog` table is not a log ("log"),
   and a `state` column is a state, not a status. Table names are matched the same way.
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
   total as quantity times unit price, net as gross minus tax, margin as price minus cost, and a
   parent's total (`total`, `total_amount`, `order_total`, ...) as the sum of its child rows' amount
   (`line_total`, `amount`, ...): a `computed` column that the compute phase fills once every
   table exists. The rule applies when the child points at the parent through one key and both
   columns are money.
9. *Business rules*: end not before start, modified not before created, cost not above price, a
   child's transaction date not before its parent's, money not negative, quantities at least 1,
   percentages 0 to 100, ratings 1 to 5.

`tests/generation/test_ddl.py` and `tests/generation/test_ddl_fixes.py` test each step. The
reference-comparison harness under `benchmarks/` checks the import against the reference
implementation's on its own DDL fixtures and on cases that fire every rule: equal in every field
except those that the five fixes above change (column-level keys, `MAX` binary types, whole-word
names, one-character codes, parent totals), which the harness lists one by one as intentional
differences.

## Domains

A domain is a `shape.domains` plugin: a `name` and `definition()`, which returns a
`DomainDefinition` (a generation schema as a JSON-style mapping, reference data as Arrow tables,
and scale presets). `shape.generation.domains.load_domain(name)` finds the plugin, registers its
reference data as the datasets that `reference_data`, `record_sample` and `record_field` read, and
returns a `LoadedDomain` (`name`, the parsed `schema`, the `definition`); `domain_names()` lists the
installed ones; an unknown name raises `DomainNotFoundError`, which lists them.

```python
from shape.generation.domains import load_domain
from shape.generation.engine import Engine

result = Engine(load_domain("retail").schema, scale="medium", seed=1).generate()
```

The `sqllocks-shape-domains` package (`pip install sqllocks-shape[domains]`) ships `retail`, `capital_markets`,
`education`, `financial`, `healthcare`, `hr`, `insurance`, `iot`, `manufacturing`, `marketing`, `pulse`, `real_estate`,
`supply_chain` and `telecom` (each also in `star` mode; see the package's README for their tables). `retail` is nine
tables (customer, address, product_category, product, store, promotion, order, order_line, return),
the row counts of the `small`, `medium`, `large` and `xlarge` presets, and its reference data. Its
uniform dates are `timestamp[ns]` (`temporal` with `unit: "ns"`) and the seasonal ones
`timestamp[us]`; the other domains follow the same rule (every non-seasonal `temporal` column is `ns`,
including the `trading_days` pattern of `capital_markets`, which is uniform). `financial`, `healthcare`, `insurance`, `iot`, `real_estate`,
`supply_chain` and `telecom` read the ZIP
locations that `retail` ships. In `capital_markets`, `industry.industry_name` is the empty string for
every row, as in the reference output it is checked against (its schema says `constant ""`).

All fourteen domains are one packaged layout (`data/<domain>/schema.json`, `schema_star.json`,
`reference/*.arrow`; `retail` also `transforms.json`, the maps behind `shape transform star|cdm`), built
by the repository's domain export script and checked against it in CI. Core ships no domain data and
no domain code.

### Composites

`shape composite` generates several domains as one dataset (`shape.generation.composite`). The merged
schema prefixes every table with its domain (`retail_customer`, `hr_employee`) and follows the prefix in
every reference (foreign keys, lookups, computed children, derived sources, business rules, scale
presets and derived counts). Domains are linked by **shared entities**: a person, a location, an
organisation. Each concept lists the table that plays it in each domain; the first listed domain in a
composite is the concept's primary and the others get a bridge column
`shared_<concept>_<domain>_<table>_id`, a foreign key to the primary's key. A preset may instead name its
links (`person: hr.employee`, `retail: customer.customer_id`).

[Run this example](#local-example-3).


`generate`, `describe` and `presets` take a composite as their target too, and
`shape.api.generate("enterprise", scale="small")` returns its tables. A composite has the `3nf` mode only.
A domain without the scale asked for (`pulse` has no `warehouse`) generates at its first scale. The
domains offer their presets and shared-entity tables through an optional `composition()` method on the
`shape.domains` plugin object (`shape_domains.composition`); core holds no domain knowledge.

Two things differ from the reference output on purpose, because reproducing them would give wrong data:

* A link to a column a table already has (a preset's `customer.customer_id`, the table's own key) cannot
  make that column a foreign key. The key keeps its values and the link gets its own bridge column.
* Two domains' reference datasets with one name and different content (`department_names` of education
  and of HR) are kept apart: each domain reads its own.

Both are named, with the columns they touch, in the comparison harness and asserted by the tests.

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
| `parquet` | `<table>.parquet` | snappy, dictionary encoding on (T-17); options `row_group_rows` (262,144: a table streamed while it is generated is encoded as its chunks arrive; a larger group would wait for a million rows) and `dictionary_page_bytes` (131,072: a column whose dictionary outgrows it stops using one) |
| `sql` | `<table>.sql` | see below |
| `excel` | `<domain>.xlsx` | one workbook for all the tables, extra `[excel]`; see [EXCEL.md](EXCEL.md) |
| `delta` | `<dir>/<table>/` | extra `[delta]`; `mode` (`overwrite`, `append`), `partition_by` |

`write_result` writes tables in parallel (`max_workers`: up to 4 threads, fewer when `SHAPE_THREADS` is lower). `write_engine` overlaps generation of chunk *n* + 1 with the
write of chunk *n* when the schema has no post-pass (`needs_post_pass`: computed columns, business
rules or correlations need whole tables). With a post-pass it generates the whole schema and writes each
table as soon as it is final (see "Threads and overlapped writing"), so the writes of the tables no
post-pass changes run while the rest is still being generated.

## Daily batches

Row addressing makes an incremental run cheap: a day's rows are rows `i x N .. (i + 1) x N - 1` of a
table whose row count is the total so far, so keys are stable and a foreign key can reach every
earlier day. `shape.generation.batches.BatchGenerator` does this (and `shape continue --daily-rows`
is its command line); the worked example, with daily orders referencing customers, regenerating one
day on its own and the determinism guarantee (same seed and date, same bytes), is in
[INCREMENTAL.md](INCREMENTAL.md#daily-batches). The files can be written in a dated
[landing layout](LANDING.md).

## Threads and overlapped writing

`Engine.generate()` generates a dependency level at a time and spreads the chunks of the level's
tables over `SHAPE_THREADS` threads (unset or `0`: every core; `1`: one). A chunk of rows depends on
its row range and on earlier levels only, so the tables are the same for any thread count and any
`chunk_rows`; chunks are about 32k to 131k rows unless `chunk_rows` is given. Numpy, Arrow and the
native kernel release the interpreter lock, which is what the threads share.

```python
engine.generate(on_table=..., on_batch=...)
```

A table is *final* once no post-pass can change it. A table with no computed column, rule repair or
correlated column is final as soon as it is generated; `on_batch(name, batch)` receives its chunks in
row order as they are made and `on_batch(name, None)` when it is whole. Any other table is final after
the copula and the last rule repair that can change it, and `on_table(name, table)` receives it then
(and receives every table when there is no `on_batch`). Both callbacks run on the calling thread:
hand the data to a writer. `write_engine` does exactly that.

`write_engine` leaves one core to the writer threads (a Parquet file is encoded by one thread, and with
every core generating, the encoder of the largest table is what a run waits for): `SHAPE_THREADS`
unset means every core less one while writing, and exactly `SHAPE_THREADS` when set. Two passes run
while the tables are still being made, and give what the plain order of the passes gives (tests compare
them): a `sum_children` or `count_children` column over a `sequence` key is accumulated as the child
table's chunks are made (`StreamedAggregate`, the same row-order additions as the single pass), and the
leading business rules are repaired on a helper thread once the tables they name exist, when no
`computed` column and no earlier rule is in their way (`EarlyRules`). The generation path builds Arrow
arrays and reads them back through `shape.generation.arrowkit`, which never imports pandas (pyarrow's
own `array`, `to_numpy` and `scalar` can).

`import shape` sets Arrow's allocator for the whole process, in one place (`shape._process.configure`),
so the command line and the Python API behave alike. Arrow's default pool (mimalloc) reserves a large
arena with `MADV_HUGEPAGE` at its first allocation, which on a virtual machine with transparent huge
pages in `madvise` mode stalls 10 to 13 ms of system time in about half of all processes, and it maps
fresh memory for each large array and returns it soon after, which makes the page faults of
short-lived arrays a large part of a run. Two cases:

* `shape` is imported before `pyarrow` (the command line always is): `ARROW_DEFAULT_MEMORY_POOL=system`
  is set for the process, so every Arrow user in it, the Parquet encoder included, takes the system
  pool. A value the environment already has is kept.
* `pyarrow` was imported first: its C++ default pool can no longer change, so transparent huge pages
  are switched off for the process (`prctl(PR_SET_THP_DISABLE)`, Linux), which removes the stall.
  `generate()` additionally runs with Arrow's system pool for the Python-level allocations
  (`shape.generation.runtime.generation_memory`).

`SHAPE_MEMORY_POOL=default` turns all of it off. Values are unaffected by pool selection; arrays remain valid with their original pool.
<!-- owner: performance maintainer — commit a product measurement with the machine and workload before publishing allocator timing numbers. -->

`shape.generation.keypos` finds the row of a key (`first_positions`, `first_rows`): for the primary key
of a parent that is a sequence (`start`, `start + 1`, ...) the row is `key - start`, found by the native
`dense_rows` kernel in one pass; any other key is found by binary search or Arrow's lookup. The compute
phase's `sum_children` and `count_children` use the native `group_sums` kernel for such a parent.

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

Data cannot add a statement to the script (#724, #285). A table, schema or column name with a
control character (such as a line break) is refused with an error, since `sqlcmd`, SSMS and Azure
Data Studio split a T-SQL script on any line that holds only `GO`, even inside a bracketed name. A
T-SQL text value with a line break is written as concatenated pieces, `N'a' + NCHAR(10) + N'b'`
(`'a' + CHAR(10) + 'b'` for Fabric Warehouse), starting with `CAST(N'' AS NVARCHAR(MAX))` when it
is longer than 8,000 bytes, so the concatenation does not truncate it. A PostgreSQL literal with a
backslash is written as `E'...'` with the backslash doubled, which reads the same whatever
`standard_conforming_strings` says. Values without a line break or a backslash keep their bytes.

## The command line

[Run this example](#local-example-6).


The generation path avoids importing pandas directly (see
`shape.generation.arrowkit`), loads a sink on a writer thread, and, run as the program with no
`--log-json` or `--metrics`, switches the garbage collector off (the imports make the objects it would
walk, and generation makes no reference cycles) and ends the process as soon as the last file is
closed and the output flushed, instead of freeing the tables and unloading the modules.

A target is an installed domain or the path of a generation schema file. `--mode star` picks a
domain's star schema (a domain that has none exits 2; a schema file has the one mode it was
written in). `--scale` must be one of the schema's presets (`shape presets`; the engine refuses any
other name, also from Python), `--seed` defaults to
the schema's. `--format summary` (the default) prints the result and writes nothing; every other
format needs `-o DIR` and is written by the writers above. `--json` prints the result, the plan or
the description as JSON. Exit codes: 0 done, 1 a dry run found problems (or `validate` found the
file invalid), 2 bad input.

`shape generate --from X.shape` generates from a profile (see "From data" above).

In Python, `shape.api.generate("retail", scale="medium", seed=42, mode="star")` returns the
`GenerationResult`: `result.tables` maps names to Arrow tables (as does `result["order"]`).

### Logging and metrics

Every command can log JSON lines and write its metrics, with the options before the command or
environment variables:

[Run this example](#local-example-7).


| Option | Variable | Meaning |
|---|---|---|
| `--log-json` | `SHAPE_LOG_JSON=1` | one JSON object per log line on stderr: `timestamp`, `level`, `logger`, `message`, extra fields |
| `--log-level LEVEL` | `SHAPE_LOG_LEVEL` | default `INFO` |
| `--metrics FILE` | `SHAPE_METRICS` | write the run's metrics as JSON: `run_id`, `command`, `exit_code`, `total_elapsed_seconds`, and for `generate` the domain, mode, scale, seed, format, rows and tables |

`shape.runlog` has the same pieces for library code: `configure_logging`, `RunMetrics` (with
`start_table`, `end_table`, `record_event`, `finish`). Nothing is sent anywhere.

### `shape validate`

`shape validate FILE` reads the file and decides by its content: `schema_version`, `model` and
`tables` make it a generation schema (checked against the JSON Schema, then by `GenSchema.validate`;
errors exit 1, warnings are printed), `name` and `fields` make it a contract, and anything else,
including a document in another tool's format, exits 2.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape from-ddl tables.sql                      # writes tables.gen.json
shape from-ddl tables.sql -o shop.gen.json --domain shop -s medium:customer=5000,order=25000
shape from-ddl tables.sql --no-smart           # type and name heuristics only
shape from-ddl tables.sql --explain            # print every inference decision
```

??? info "Output (exit 0)"

    ```text {.expected}
    Shape DDL import (smart)

      Source: tables.sql
      Output: tables.gen.json
      Tables: 1
      Relationships: 0
      Business rules: 1
      Inferences: 4

      orders: 4 columns (PK: order_id)

    Schema written to tables.gen.json
    Shape DDL import (smart)

      Source: tables.sql
      Output: shop.gen.json
      Tables: 1
      Relationships: 0
      Business rules: 1
      Inferences: 4

      orders: 4 columns (PK: order_id)

    Schema written to shop.gen.json
    Shape DDL import

      Source: tables.sql
      Output: tables.gen.json
      Tables: 1
      Relationships: 0
      Business rules: 0

      orders: 4 columns (PK: order_id)

    Schema written to tables.gen.json
    Shape DDL import (smart)

      Source: tables.sql
      Output: tables.gen.json
      Tables: 1
      Relationships: 0
      Business rules: 1
      Inferences: 4

      orders: 4 columns (PK: order_id)

    Schema written to tables.gen.json

    --- Inference Report ---

      [TC-UNKNOWN] orders: Classified as UNKNOWN (confidence: 80%)
      [CA-SCALE] orders: Root table — scale presets set (1K / 50K / 500K) (confidence: 90%)
      [ND-MONETARY] orders.amount: Upgraded to log_normal distribution based on MONETARY semantic (confidence: 75%)
      [BR-05] orders.amount: Monetary column amount >= 0 (confidence: 80%)
    ```

<a id="local-example-3"></a>

### Example 4

<!-- example: 3 -->

```bash {.runnable-reference}
shape presets --composites                    # the six presets: enterprise, healthcare_system, smart_factory,
                                              # digital_commerce, campus, telecom_bundle
shape composite enterprise --scale small      # a preset ...
shape composite retail+hr+financial -f parquet -o out/    # ... or domains joined by '+'
shape presets campus                          # rows per table, as for a domain
```

??? info "Output (exit 0)"

    ```text {.expected}
    Composite presets (6):

      enterprise           Enterprise dataset combining retail, HR, and financial domains
                           Domains: retail, hr, financial

      healthcare_system    Healthcare system with insurance and HR
                           Domains: healthcare, insurance, hr

      smart_factory        Smart factory combining manufacturing, IoT, and supply chain
                           Domains: manufacturing, iot, supply_chain

      digital_commerce     Digital commerce with retail, marketing, and financial data
                           Domains: retail, marketing, financial

      campus               University campus combining education and HR
                           Domains: education, hr

      telecom_bundle       Telecom provider with marketing and billing
                           Domains: telecom, marketing, financial

    Shape Generation Result
    ========================================
    Schema: composite_financial_hr_retail
    Domain: composite
    Mode:   3nf
    Seed:   42
    Time:   0.2s

    Table                             Rows  Columns
    ---------------------------------------------
    financial_branch                   200        9
    financial_customer               1,000       10
    financial_loan                     400        9
    financial_loan_payment           4,800        7
    financial_transaction_category           40        4
    hr_department                       30        4
    hr_position                         80        6
    hr_employee                        500       11
    financial_account                2,200        8
    financial_card                   1,760        8
    financial_statement             13,200        8
    financial_transaction           10,000        9
    financial_fraud_flag               200        6
    hr_compensation                  1,500        6
    hr_performance_review            1,250        7
    hr_termination                      75        6
    hr_time_off_request              2,500        7
    hr_training                        100        5
    hr_training_enrollment           2,000        7
    retail_customer                  1,000        9
    retail_address                   1,500       10
    retail_product_category             50        4
    retail_product                     500        6
    retail_promotion                   300        6
    retail_store                       150        5
    retail_order                     5,000        8
    retail_order_line               12,500        8
    retail_return                      850        5
    ---------------------------------------------
    TOTAL                           63,685
    Wrote 28 parquet files to out/: 63,685 rows in 28 tables (0.32s)
    table                          fabric_demo         large        medium         small     warehouse        xlarge           xxl          xxxl
    education_department                    25            25            25            25            25            25            25            25
    education_instructor                   150           150           150           150           150           150           150           150
    education_course                       300           300           300           300           300           300           300           300
    education_student                      200       200,000        20,000         2,000     2,000,000     5,000,000    20,000,000   100,000,000
    education_enrollment                 1,600     1,600,000       160,000        16,000    16,000,000    40,000,000   160,000,000   800,000,000
    education_financial_aid                140       140,000        14,000         1,400     1,400,000     3,500,000    14,000,000    70,000,000
    education_course_section               600           600           600           600           600           600           600           600
    education_grade_appeal                  32        32,000         3,200           320       320,000       800,000     3,200,000    16,000,000
    education_academic_standing            400       400,000        40,000         4,000     4,000,000    10,000,000    40,000,000   200,000,000
    hr_department                           30            30            30            30            30            30            30            30
    hr_position                             80            80            80            80            80            80            80            80
    hr_employee                            100        50,000         5,000           500     5,000,000       500,000    20,000,000   100,000,000
    hr_compensation                        300       150,000        15,000         1,500    15,000,000     1,500,000    60,000,000   300,000,000
    hr_performance_review                  250       125,000        12,500         1,250    12,500,000     1,250,000    50,000,000   250,000,000
    hr_time_off_request                    500       250,000        25,000         2,500    25,000,000     2,500,000   100,000,000   500,000,000
    hr_training                            100           100           100           100           100           100           100           100
    hr_training_enrollment                 400       200,000        20,000         2,000    20,000,000     2,000,000    80,000,000   400,000,000
    hr_termination                          15         7,500           750            75       750,000        75,000     3,000,000    15,000,000
    total                                5,222     3,155,785       316,735        32,830   101,971,285    67,126,285   550,201,285 2,751,001,285
    ```

<a id="local-example-6"></a>

### Example 7

<!-- example: 6 -->

```bash {.runnable-reference}
shape list                                   # installed domains and their modes
shape presets retail                         # rows per table for every scale preset
shape composite enterprise                   # several domains as one dataset (see Composites)
shape describe retail --mode star --scale medium
shape generate retail --scale medium --seed 42 --format parquet -o out/
shape generate retail --dry-run              # the plan: order, rows, memory; generates nothing
shape from-ddl tables.sql -o shop.gen.json && shape generate shop.gen.json -f csv -o out/
shape validate shop.gen.json                 # a schema file, or a contract; exit 0, 1 or 2
shape continue retail --input out/ -o delta/ # the next inserts, updates and deletes (docs/INCREMENTAL.md)
shape time-travel retail --months 12 -o snaps/   # monthly snapshots of an evolving dataset
```

??? info "Output (exit 0)"

    ```text {.expected}
    domain                  modes
    capital_markets         3nf, star
    education               3nf, star
    financial               3nf, star
    healthcare              3nf, star
    hr                      3nf, star
    insurance               3nf, star
    iot                     3nf, star
    manufacturing           3nf, star
    marketing               3nf, star
    pulse                   3nf, star
    real_estate             3nf, star
    retail                  3nf, star
    supply_chain            3nf, star
    telecom                 3nf, star
    table                          fabric_demo         small        medium         large        xlarge     warehouse           xxl          xxxl
    customer                               200         1,000        50,000       500,000     5,000,000     1,000,000    20,000,000   100,000,000
    address                                300         1,500        75,000       750,000     7,500,000     1,500,000    30,000,000   150,000,000
    product_category                        50            50            50            50            50            50            50            50
    product                                100           500         5,000        25,000       100,000        50,000       200,000       500,000
    store                                  150           150           150           150           150           150           150           150
    promotion                              200           200           200           200           200           200           200           200
    order                                1,000         5,000       500,000     5,000,000   100,000,000    10,000,000 1,000,000,00010,000,000,000
    order_line                           2,500        12,500     1,250,000    12,500,000   250,000,000    25,000,000 2,500,000,00025,000,000,000
    return                                 170           850        85,000       850,000    17,000,000     1,700,000   170,000,000 1,700,000,000
    total                                4,670        21,750     1,965,400    19,625,400   379,600,400    39,250,400 3,720,200,40036,950,500,400
    Shape Generation Result
    ========================================
    Schema: composite_financial_hr_retail
    Domain: composite
    Mode:   3nf
    Seed:   42
    Time:   0.2s

    Table                             Rows  Columns
    ---------------------------------------------
    financial_branch                   200        9
    financial_customer               1,000       10
    financial_loan                     400        9
    financial_loan_payment           4,800        7
    financial_transaction_category           40        4
    hr_department                       30        4
    hr_position                         80        6
    hr_employee                        500       11
    financial_account                2,200        8
    financial_card                   1,760        8
    financial_statement             13,200        8
    financial_transaction           10,000        9
    financial_fraud_flag               200        6
    hr_compensation                  1,500        6
    hr_performance_review            1,250        7
    hr_termination                      75        6
    hr_time_off_request              2,500        7
    hr_training                        100        5
    hr_training_enrollment           2,000        7
    retail_customer                  1,000        9
    retail_address                   1,500       10
    retail_product_category             50        4
    retail_product                     500        6
    retail_promotion                   300        6
    retail_store                       150        5
    retail_order                     5,000        8
    retail_order_line               12,500        8
    retail_return                      850        5
    ---------------------------------------------
    TOTAL                           63,685
    retail_star  domain=retail  mode=star  scale=medium
    Retail domain — star schema

    table                               rows  columns  primary key
    customer                          50,000        8  customer_id
    address                           75,000       10  address_id
    product_category                      50        4  category_id
    product                            5,000        6  product_id
    promotion                            200        6  promotion_id
    store                                150        5  store_id
    order                            500,000        8  order_id
    order_line                     1,250,000        8  order_line_id
    return                            85,000        5  return_id

    customer
      customer_id                 integer       sequence
      first_name                  string        faker
      last_name                   string        faker
      email                       string        faker (nullable)
      gender                      string        weighted_enum
      loyalty_tier                string        weighted_enum
      signup_date                 timestamp     temporal
      is_active                   string        weighted_enum

    address
      address_id                  integer       sequence
      customer_id                 integer       foreign_key
      address_type                string        weighted_enum
      street                      string        faker
      city                        string        record_sample
      state                       string        record_field
      zip_code                    string        record_field
      lat                         decimal       record_field
      lng                         decimal       record_field
      is_primary                  boolean       first_per_parent

    product_category
      category_id                 integer       sequence
      category_name               string        reference_data
      parent_category_id          integer       self_referencing (nullable)
      level                       integer       self_ref_field

    product
      product_id                  integer       sequence
      category_id                 integer       foreign_key
      product_name                string        reference_data
      unit_price                  decimal       distribution
      product_status              string        lifecycle
      cost                        decimal       correlated

    promotion
      promotion_id                integer       sequence
      promo_name                  string        reference_data
      promo_type                  string        weighted_enum
      discount_pct                decimal       weighted_enum
      start_date                  timestamp     temporal
      end_date                    timestamp     derived

    store
      store_id                    integer       sequence
      store_name                  string        pattern
      store_type                  string        weighted_enum
      city                        string        faker
      state                       string        faker

    order
      order_id                    integer       sequence
      customer_id                 integer       foreign_key
      store_id                    integer       foreign_key
      shipping_address_id         integer       foreign_key (nullable)
      promotion_id                integer       foreign_key (nullable)
      order_date                  timestamp     temporal
      status                      string        weighted_enum
      order_total                 decimal       computed

    order_line
      order_line_id               integer       sequence
      order_id                    integer       foreign_key
      product_id                  integer       foreign_key
      quantity                    integer       distribution
      promotion_id                integer       lookup (nullable)
      unit_price                  decimal       lookup
      discount_percent            decimal       conditional
      line_total                  decimal       formula

    return
      return_id                   integer       sequence
      order_id                    integer       foreign_key
      reason                      string        weighted_enum
      refund_amount               decimal       distribution
      return_date                 timestamp     derived

    relationships
      address(customer_id) -> customer(customer_id)
      product(category_id) -> product_category(category_id)
      order(customer_id) -> customer(customer_id)
      order(store_id) -> store(store_id)
      order(shipping_address_id) -> address(address_id)
      order(promotion_id) -> promotion(promotion_id)
      order_line(order_id) -> order(order_id)
      order_line(product_id) -> product(product_id)
      return(order_id) -> order(order_id)

    business rules: order_date_after_signup, return_after_order, cost_less_than_price, refund_leq_order_total, line_total_positive

    scale presets: fabric_demo, small, medium, large, xlarge, warehouse, xxl, xxxl
    Wrote 9 parquet files to out/: 1,965,400 rows in 9 tables (7.45s)
    retail_3nf  domain=retail  mode=3nf  seed=42  scale=small
    table                                 rows  columns   est. MB
    customer                             1,000        8       0.5
    product_category                        50        4       0.0
    promotion                              200        6       0.0
    store                                  150        5       0.0
    address                              1,500       10       0.4
    product                                500        6       0.1
    order                                5,000        8       0.5
    order_line                          12,500        8       0.8
    return                                 850        5       0.1
    total                               21,750                2.4
    ok: nothing was generated
    Shape DDL import (smart)

      Source: tables.sql
      Output: shop.gen.json
      Tables: 1
      Relationships: 0
      Business rules: 1
      Inferences: 4

      orders: 4 columns (PK: order_id)

    Schema written to shop.gen.json
    Wrote 1 csv files to out/: 1,000 rows in 1 tables (0.03s)
    {"errors": [], "kind": "generation-schema", "mode": "3nf", "name": "custom_ddl_import", "tables": 1, "valid": true, "warnings": []}
    Source: out/ (39 tables)
    Incremental Generation Result
    ========================================
      customer: +100 inserts, ~5000 updates, -1000 deletes
      address: +100 inserts, ~7500 updates, -1500 deletes
      product_category: +100 inserts, ~5 updates, -1 deletes
      product: +100 inserts, ~500 updates, -100 deletes
      promotion: +100 inserts, ~20 updates, -4 deletes
      store: +100 inserts, ~15 updates, -3 deletes
      order: +100 inserts, ~50000 updates, -10000 deletes
      order_line: +100 inserts, ~125000 updates, -25000 deletes
      return: +100 inserts, ~8500 updates, -1700 deletes
      customers: +100 inserts, ~2 updates, -1 deletes
      financial_account: +100 inserts, ~220 updates, -44 deletes
      financial_branch: +100 inserts, ~20 updates, -4 deletes
      financial_card: +100 inserts, ~176 updates, -35 deletes
      financial_customer: +100 inserts, ~100 updates, -20 deletes
      financial_fraud_flag: +100 inserts, ~20 updates, -4 deletes
      financial_loan: +100 inserts, ~40 updates, -8 deletes
      financial_loan_payment: +100 inserts, ~480 updates, -96 deletes
      financial_statement: +100 inserts, ~1320 updates, -264 deletes
      financial_transaction: +100 inserts, ~1000 updates, -200 deletes
      financial_transaction_category: +100 inserts, ~4 updates, -1 deletes
      hr_compensation: +100 inserts, ~150 updates, -30 deletes
      hr_department: +100 inserts, ~3 updates, -1 deletes
      hr_employee: +100 inserts, ~50 updates, -10 deletes
      hr_performance_review: +100 inserts, ~125 updates, -25 deletes
      hr_position: +100 inserts, ~8 updates, -1 deletes
      hr_termination: +100 inserts, ~7 updates, -1 deletes
      hr_time_off_request: +100 inserts, ~250 updates, -50 deletes
      hr_training: +100 inserts, ~10 updates, -2 deletes
      hr_training_enrollment: +100 inserts, ~200 updates, -40 deletes
      orders: +100 inserts, ~100 updates, -20 deletes
      retail_address: +100 inserts, ~150 updates, -30 deletes
      retail_customer: +100 inserts, ~100 updates, -20 deletes
      retail_order: +100 inserts, ~500 updates, -100 deletes
      retail_order_line: +100 inserts, ~1250 updates, -250 deletes
      retail_product: +100 inserts, ~50 updates, -10 deletes
      retail_product_category: +100 inserts, ~5 updates, -1 deletes
      retail_promotion: +100 inserts, ~30 updates, -6 deletes
      retail_return: +100 inserts, ~85 updates, -17 deletes
      retail_store: +100 inserts, ~15 updates, -3 deletes

    Written 39 delta files to delta//
    Time-Travel Result
    ==================================================
    Domain: retail
    Snapshots: 13

      Month           Date          Tables       Rows
      ----------------------------------------------
      Month 0        2023-01-01         9     21,750
      Month 1        2023-02-01         9     22,401
      Month 2        2023-03-01         9     23,071
      Month 3        2023-04-01         9     23,762
      Month 4        2023-05-01         9     24,475
      Month 5        2023-06-01         9     25,209
      Month 6        2023-07-01         9     25,967
      Month 7        2023-08-01         9     26,745
      Month 8        2023-09-01         9     27,549
      Month 9        2023-10-01         9     28,374
      Month 10       2023-11-01         9     29,224
      Month 11       2023-12-01         9     30,103
      Month 12       2024-01-01         9     31,007

    Written 117 files to snaps//
    ```

<a id="local-example-7"></a>

### Example 8

<!-- example: 7 -->

```bash {.runnable-reference}
shape --log-json --metrics run.json generate retail --scale small
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"timestamp": "2026-10-09T17:01:17.723065+00:00", "level": "INFO", "logger": "shape", "message": "command started", "command": "generate"}
    {"timestamp": "2026-10-09T17:01:18.122962+00:00", "level": "INFO", "logger": "shape", "message": "command finished", "command": "generate", "exit_code": 0, "metrics": {"run_id": "20261009T170117_generate", "total_elapsed_seconds": 0.3999, "total_rows": 0, "total_tables": 0, "tables": 9, "events": [], "command": "generate", "domain": "retail", "mode": "3nf", "scale": "small", "seed": 42, "format": "summary", "rows": 21750, "exit_code": 0}}
    Shape Generation Result
    ========================================
    Schema: retail_3nf
    Domain: retail
    Mode:   3nf
    Seed:   42
    Time:   0.1s

    Table                             Rows  Columns
    ---------------------------------------------
    customer                         1,000        8
    address                          1,500       10
    product_category                    50        4
    product                            500        6
    promotion                          200        6
    store                              150        5
    order                            5,000        8
    order_line                      12,500        8
    return                             850        5
    ---------------------------------------------
    TOTAL                           21,750
    ```
