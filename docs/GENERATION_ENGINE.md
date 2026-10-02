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
  on each other (the dry run reports them). The engine does not wait for a level: a table starts as
  soon as the tables it points at are complete (see "Threads and overlapped writing").
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

1. **Compute phase** (`compute.py`): fills `computed` columns from another table: `sum_children`,
   `count_children`, `avg_children`, `min_children`, `max_children` (rows without children get 0;
   decimals are rounded to 2 places), or `lookup_parent`.
2. **Business rules** (`rules.py`): `validate_rules` lists the rules the data breaks;
   `fix_rules` repairs `cross_column` (`<`, `>`) and `cross_table` (`>=`, `>`, `<=`) rules and
   returns what still violates. Repairs draw from the `fix:<rule>` stream.
3. **Correlation** (`correlation.py`): a Gaussian copula reorders the values of the numeric columns
   named in `correlated_columns` to match the target correlations, leaving every column's values
   unchanged. Key-like columns and columns with nulls are not reordered, unless the schema's
   `generation.output.copula_nulls` is `"rank"` (what `shape generate --from` writes): then the
   non-null values are reordered among the non-null rows and the nulls stay where they are.
   Pairs with `|r|` below 0.5 are ignored, unless `generation.output.copula_threshold` lowers
   that (`shape generate --from` writes 0 and lists only the pairs it wants).

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

## Reading SQL DDL

`shape from-ddl FILE` turns `CREATE TABLE` statements (SQL Server, PostgreSQL, MySQL and ANSI SQL,
plus `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`) into a generation schema. Foreign keys are
read wherever the dialect writes them: a table-level `FOREIGN KEY` constraint, an `ALTER TABLE`, or
a column-level `REFERENCES` clause.

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
from its name (`email`, `first_name`, `city`, `state`, `status`, ... and the suffixes `_name`,
`_code`, `_type`, `_status`, `_date`). A one-character string column (`gender CHAR(1)`) holds a
code, so it gets a short value set chosen by the words of its name (`gender` and `sex` M or F,
status words A, I or P, flags such as `is_active` Y or N, any other code A, B or C); a `gender` or
`sex` column of any length is M or F. Identity, serial and auto-increment columns, and
single-column primary keys, are sequences. A declared foreign key is a `foreign_key` column and a
relationship (a self-reference is `self_referencing`), whether it is a table-level constraint or a
column-level clause such as `customer_id INT REFERENCES customer(id)` (with or without
`CONSTRAINT name` and `ON DELETE ...`; `REFERENCES customer` alone means the parent's primary
key). Column names ending `_id` or `Id` (`customer_id`, `CustomerId`, `CustomerID`) that match a
table by its singular or plural name (`order_id` to `order` or `orders`, `category_id` to
`categories`) are foreign keys too, when the DDL does not declare them: the key points at that
table's primary key, whatever it is called, and is left out (the column stays a plain number) when
the table has no single-column primary key. A name written without separators (`orderdate`) is one
word and matches no rule. Binary columns (`VARBINARY`, `BINARY`, `VARBINARY(MAX)`, `IMAGE`, `BYTEA`,
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
| `parquet` | `<table>.parquet` | snappy, dictionary encoding on (T-17); options `row_group_rows` (262,144: a table streamed while it is generated is encoded as its chunks arrive; a larger group would wait for a million rows; a group closes at the first batch boundary at or past it) and `dictionary_page_bytes` (131,072: a column whose dictionary outgrows it stops using one). With the native kernel the file is written by `shape._kernel.ParquetOut`, which encodes every column of every row group as its own task on the kernel's thread pool while the caller goes on, and appends the finished groups in row order; the pure-Python kernel, another `compression`, a nested column type or `SHAPE_PARQUET_WRITER=pyarrow` use pyarrow's writer (one thread). The two write equal tables; the bytes differ |
| `sql` | `<table>.sql` | see below |
| `excel` | `<table>.xlsx` | extra `[excel]`; refuses a table over 1,048,575 rows |
| `delta` | `<dir>/<table>/` | extra `[delta]`; `mode` (`overwrite`, `append`), `partition_by` |

`write_result` writes tables in parallel (`max_workers`: up to 4 threads, fewer when `SHAPE_THREADS` is lower). `write_engine` overlaps generation of chunk *n* + 1 with the
write of chunk *n* when the schema has no post-pass (`needs_post_pass`: computed columns, business
rules or correlations need whole tables). With a post-pass it generates the whole schema and writes each
table as soon as it is final (see "Threads and overlapped writing"), so the writes of the tables no
post-pass changes run while the rest is still being generated.

## Threads and overlapped writing

`Engine.generate()` schedules chunks, not levels (`shape.generation.scheduler`). A table starts as
soon as the tables it points at (foreign keys and relationships) are complete, so a child does not
wait for an unrelated slow table of the level before it, and the chunks that are ready are taken
longest-path-first (the table with the most work behind it goes first). `SHAPE_THREADS` is the most
threads used (unset or `0`: every core; `1`: one). The calling thread runs the chunks itself while
the queued work is small (under about 100,000 cells, rows times columns, which is a few
milliseconds): a pool costs more than it gains on a table of a few thousand rows, each thread's first
calls being cold. Worker threads are added when the queue holds enough work for two or more. A chunk
of rows depends on its row range and on the tables it points at only, so the tables are the same for
any thread count, order and `chunk_rows`; chunks are about 32k to 131k rows unless `chunk_rows` is
given. Numpy, Arrow and the native kernel release the interpreter lock, which is what the threads
share.

```python
engine.generate(on_table=..., on_batch=...)
```

A table is *final* once no post-pass can change it. A table with no computed column, rule repair or
correlated column is final as soon as it is generated; `on_batch(name, batch)` receives its chunks in
row order as they are made and `on_batch(name, None)` when it is whole. Any other table is final after
the last rule repair that can change it and the copula, and `on_table(name, table)` receives it then
(and receives every table when there is no `on_batch`). Both callbacks run on the calling thread:
hand the data to a writer. `write_engine` does exactly that.

`write_engine` leaves one core to the writers (a file in any other format, or a Parquet file through
pyarrow, is encoded by one thread, and with every core generating, the encoder of the largest table
is what a run waits for): `SHAPE_THREADS`
unset means every core less one while writing, and exactly `SHAPE_THREADS` when set. Two passes run
while the tables are still being made, and give what the plain order of the passes gives (tests compare
them): a `sum_children` or `count_children` column over a `sequence` key is accumulated as the child
table's chunks are made (`StreamedAggregate`, the same row-order additions as the single pass), and the
leading business rules are repaired on a helper thread once the tables they name exist, when no
`computed` column and no earlier rule is in their way (`EarlyRules`). The generation path builds Arrow
arrays and reads them back through `shape.generation.arrowkit`, which never imports pandas (pyarrow's
own `array`, `to_numpy` and `scalar` do, about 0.16 s of start-up).

Two rules about business-rule repair keep a table from waiting for the post-passes when it need not
(none changes a value; tests compare each with the plain order):

* A business rule whose comparison `fix_rule` never changes (`>=` or `<=` or `==` between two columns
  of one table, `<` or `==` across tables) is only validated, so it does not hold its table back
  for the post-passes (`can_repair`).
* A table whose every rule is *row-local* is repaired chunk by chunk as the chunk is made, and is
  final at generation (`plan_streamed_rules`): a rule reads the row it repairs and the row its `via`
  key names in a table of an earlier level, and draws from a row-addressed stream. A table
  qualifies when no `computed` or correlated column changes it later, the compute phase reads none
  of the columns it rewrites, and no rule of another table reads a column it rewrites before it, or
  rewrites a column it reads after it (those rules see the repaired values, as in the plain order).

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

`SHAPE_MEMORY_POOL=default` turns all of it off. Values are unaffected (a test compares a run with and
without it); arrays stay valid whichever pool made them. Measured on 4 vCPU (Intel Xeon @ 2.80GHz, KVM),
fresh process per run, interleaved, the `generate.py` timed region at medium: education median 0.205 s
default, 0.148 s with the environment variable, 0.149 s with huge pages off; financial 0.728 s, 0.566 s,
0.586 s (12 runs each, `docs/plans/lane_status/P6-01a.md`, "Escalation 2, round 2").

The first `generate()` in a process also raises glibc's allocation thresholds, once and for the rest
of the process (`shape._process.tune_malloc`, Linux with glibc; `mallopt`: blocks up to 32 MiB come
from the heap, up to 256 MiB of free memory is kept, 16 MiB is requested at a time). glibc otherwise
trims freed memory soon after it is freed and maps each large block fresh, so the short-lived arrays of
a run fault their pages in again. The three are set together because setting one alone freezes the
adaptive mmap threshold at 128 KiB, which is slower than leaving it alone (measured: the trim
threshold alone, +29% on retail). Interleaved fresh-process runs of the medium workloads, 4 vCPU Xeon
@ 2.10GHz: retail 418 to 394 ms, pulse 228 to 213, marketing 140 to 132, financial 311 to 292,
healthcare 307 to 295, supply_chain 90 to 85, education 82 to 78 (medians of 7). A host that sets
`MALLOC_*` or `GLIBC_TUNABLES` itself is left alone; `SHAPE_MEMORY_POOL=default` turns it off. No
value changes.

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

## The command line

```
shape list                                   # installed domains and their modes
shape presets retail                         # rows per table for every scale preset
shape describe retail --mode star --scale medium
shape generate retail --scale medium --seed 42 --format parquet -o out/
shape generate retail --dry-run              # the plan: order, rows, memory; generates nothing
shape from-ddl tables.sql -o shop.gen.json && shape generate shop.gen.json -f csv -o out/
shape validate shop.gen.json                 # a schema file, or a contract; exit 0, 1 or 2
shape continue retail --input out/ -o delta/ # the next inserts, updates and deletes (docs/INCREMENTAL.md)
shape time-travel retail --months 12 -o snaps/   # monthly snapshots of an evolving dataset
```

Start-up and exit are kept short, because they are part of what a run costs (retail `medium` takes
about 0.65 s end to end, of which the imports are about 0.2 s): `generate` imports pandas never (see
`shape.generation.arrowkit`), loads a sink on a writer thread, and, run as the program with no
`--log-json` or `--metrics`, switches the garbage collector off (the imports make the objects it would
walk, and generation makes no reference cycles) and ends the process as soon as the last file is
closed and the output flushed, instead of freeing the tables and unloading the modules.

A target is an installed domain or the path of a generation schema file. `--mode star` picks a
domain's star schema (a domain that has none exits 2; a schema file has the one mode it was
written in). `--scale` must be one of the schema's presets (`shape presets`), `--seed` defaults to
the schema's. `--format summary` (the default) prints the result and writes nothing; every other
format needs `-o DIR` and is written by the writers above. `--json` prints the result, the plan or
the description as JSON. Exit codes: 0 done, 1 a dry run found problems (or `validate` found the
file invalid), 2 bad input.

`shape generate --from X.shape` is reserved for generating from a profile and exits 2 for now.

In Python, `shape.api.generate("retail", scale="medium", seed=42, mode="star")` returns the
`GenerationResult`: `result.tables` maps names to Arrow tables (as does `result["order"]`).

### Logging and metrics

Every command can log JSON lines and write its metrics, with the options before the command or
environment variables:

```
shape --log-json --metrics run.json generate retail --scale small
```

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
