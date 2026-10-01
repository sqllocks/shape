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

`RowStream` reads through the selected kernel (`docs/GENERATION_KERNEL.md`): `raw`, `uniform` and
`normal` take `per_row` and `slot`, and `derive(suffix)` gives a separate stream for a sub-draw
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
