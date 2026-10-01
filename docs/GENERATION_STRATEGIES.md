# Generation strategies

A strategy fills one column of a table, one chunk at a time. This page is the contract the
strategies of Phase 4 are written against; the engine side is in `docs/GENERATION_ENGINE.md` and the
native kernel in `docs/GENERATION_KERNEL.md`.

## The interface (stable)

```python
class MyStrategy:
    name = "my_strategy"                       # the `strategy` value in a column's generator
    def generate(self, spec, ctx):             # spec: the generator dict; ctx: EngineContext
        ...                                    # -> pyarrow.Array of ctx.n_rows values
```

Register it under the `shape.strategies` entry-point group. `generate` may also return a mapping of
names to arrays when one strategy makes several columns; names that are not columns of the table
are internal (later strategies read them from `ctx.columns`; they are not output).

`ctx` is the plugin API's `GenerationContext` (`seed`, `table`, `column`, `chunk`, `row_start`,
`n_rows`, `columns`: the columns of this chunk built so far) plus, in the engine, `engine` (the
schema, `row_counts`, `key_pool(table)`) and `column_def` (the column: `type`, `scale`, ...).

### Rules every strategy follows

1. **Row addressed.** The value of row `r` depends on the run seed, the table, the column, `r`,
   the spec and the other columns *of the same row*, never on the chunk, the order of the calls or
   the thread. Draw numbers from `shape.generation.strategy_kit.stream(ctx, label)`, a Philox
   stream; read it at `ctx.row_start`. Use one label per independent random choice
   (`"v"`, `"random0"`, ...), so no two share numbers. Never use `ctx.chunk` to key a draw, and
   never `numpy.random` or `random` with a seed.
   `tests/generation/test_strategies_p404a.py::test_identical_for_any_chunking_and_random_access`
   is the test to copy.
2. **No per-value Python on the hot path.** Use whole arrays: numpy, Arrow compute, or the kernel
   (`shape.generation.kernel_ops`).
3. **Nulls belong to the engine.** Return values for every row; the engine applies the column's
   `null_rate` afterwards from its own stream. A strategy returns nulls only where the data
   itself is null (a null in a referenced column).
4. **Errors name the column.** Raise `StrategyError` (a `ValueError`) with the strategy and
   `table.column`: `require(spec, key, ctx, "strategy")`, `where(ctx)`.
5. **Types.** A strategy returns its natural type and the engine does not cast: integers
   (`sequence`) as `int64`; numeric draws as `float64`; text as `string`.

### Helpers

| Module | Function | Use |
|---|---|---|
| `shape.generation.strategy_kit` | `stream(ctx, label)` | the column's `RowStream`: `uniform`, `normal`, `raw` words |
| | `require(spec, key, ctx, strategy)`, `where(ctx)`, `StrategyError` | spec access and errors |
| | `spec_params(spec)` | the `params` mapping if there is one, else the spec |
| | `column_scale(ctx)`, `round_to_scale(values, ctx)` | the column's decimal scale |
| `shape.generation.kernel_ops` | `alias_table`, `alias_draw` | weighted categorical draws (two words per row) |
| | `pool_take`, `template_strings`, `join_strings`, `string_case` | string assembly |
| | `uuid4`, `random_strings` | UUIDs and random text |
| | `day_weights`, `hour_weights_peaks`, `temporal_sample` | dates and times |

## Testing a strategy against the baseline

Each strategy has cases in `strategy_1to1/cases.py` of the benchmark harness (a one-table schema
per configuration). `baseline.py` there, run in the baseline venv, writes the fingerprints of the
pinned baseline at its fixed seeds 43-46 to the harness's `fixtures/strategies/<strategy>.json`
(`--check` proves the fixtures still equal the baseline's output). The test generates every case
with the engine at Shape's seed 1042 and applies T-21 clauses (b)-(e) with `compare.py`: null rate,
KS, total variation distance with vocabulary overlap, and for text the length, character-class
and mask distributions. The test needs no baseline venv.

To add a strategy: add its cases, run `baseline.py --strategy <name>`, copy
`test_strategies_p404a.py` (equivalence, chunking, determinism, negative controls, bad specs).

## Strategies of P4-04a

### `sequence`
`{"start": 1, "step": 1}`: `start + row * step` as `int64`, whatever the chunking.

### `uuid`
Version-4 UUID strings (lowercase, `8-4-4-4-12`) from two words per row.

### `weighted_enum`
`{"values": {"gold": 0.5, "silver": 0.3, "bronze": 0.2}}`: a key drawn with probability
proportional to its weight (weights may be any non-negative numbers with a positive sum, zero
allowed). Keys that all read as numbers give a `float64` column, otherwise a `string` column.

### `distribution`
`{"distribution": "log_normal", "mean": 3.5, "sigma": 0.8, "min": 1, "max": 500}`; the parameters
may also sit under `"params"`. `min` and `max` clip every value; the column's `scale` rounds. The
column is `float64`.

| Family | Spec parameters | Family parameters (`families.py`) |
|---|---|---|
| `uniform` | `min` (0), `max` (1) | `low`, `high` |
| `normal` | `mean` (0), `std_dev` / `sigma` / `std` (1) | `mu`, `sigma` |
| `log_normal` | `mean` (0), `sigma` / `std` (1) | `mu`, `sigma` (of the log) |
| `pareto` | `alpha` (1.5), `min` (1) | `alpha`, `xm` |
| `zipf` | `alpha` (1.5), `max` (1000) | `a`, `max` (truncated: `P(k)` proportional to `k ** -a`) |
| `geometric` | `p` (0.5) | `p` (support 1, 2, ...) |
| `poisson` | `lambda` (5) | `lam` |
| `bernoulli` | `probability` (0.5) | `p` |

A name that is not a built-in family is looked up in the `shape.distributions` plugins (their
`sample(params, ctx)` gets the spec's parameters).

**Adding a family** (the extension point P4-05 uses): subclass
`shape.builtins.distributions.families.Family`, set `name`, `defaults` (parameter names and
defaults) and `words_per_row`, implement `draw(stream, row_start, n, params)` returning float64,
override `from_spec` when the spec spells parameters differently, and add the instance to `FAMILIES`.
A family draws a fixed number of words per row (no rejection loops) so it stays row addressed.

### `empirical`
`{"quantiles": {"p1": .., "p5": .., "p10": .., "p25": .., "p50": .., "p75": .., "p90": .., "p95": ..,
"p99": ..}}` (optionally `p0_5` and `p99_5` as tail anchors): a uniform draw mapped through the
interpolated quantile function (`"interpolation": "linear"`, or `"cubic"` when scipy is installed,
else linear with an `ImportWarning`). `min`/`max` clip. Values never leave the outermost anchors.

### `pattern`
`{"format": "ORD-{seq:5}-{random:3}"}`. `{seq}` / `{seq:6}`: the table's 1-based row number,
zero-padded; `{random:4}`: that many characters from `A-Z0-9` (4 without a width); `{column}` /
`{column:3}`: the value of another column of the same row, zero-padded; text outside tokens is
literal; a token naming no column stays as written. A null in a referenced column gives a null
row.

## Strategies of P4-04c

These read other columns, other tables or reference datasets. All are row addressed.

### `lookup`
`{"source_table": "product", "source_column": "unit_price", "via": "product_id"}`: the
`source_column` of the parent row whose key equals this row's `via` column (a column defined earlier
in the table, usually a foreign key). The column has the source column's type. A null key, or a key
with no parent row, gives null.

The key column of the parent is `key` when the spec names one, else the parent column called
`via`, else the column this table's foreign key `via` points at, else the parent's single-column
primary key. Keys compare by value (an integer key matches the same number held as a float). When
a key repeats in the parent, the first row wins. `shape.generation.lookup` is the helper:
`lookup_values(ctx, source_table, source_column, via, key=None)` does the whole operation, and
`key_index(engine, table, column)` returns the cached `KeyIndex` (built once per engine; its
`positions(values)` gives the row of each key, `-1` for none). The parent table is generated in
full once, the way `Engine.key_pool` does for a table that is not a sequence. A lookup that leads
back to a table already being generated raises an error ("circular") instead of recursing.

### `conditional`
`{"condition": "promotion_id IS NOT NULL", "true_generator": {...}, "false_generator": {"fixed": 0.0}}`.

* Conditions: `<column> IS NULL`, `<column> IS NOT NULL` (the column name matches without regard to
  case), `<column> == <value>`, `<column> != <value>` (the value may be quoted; both sides compare
  as numbers when both read as numbers, otherwise as text; a null equals nothing, so `!=` is true
  for it).
* Branches: `{"fixed": value}` or an inline `{"strategy": "lookup", ...}` (a null key gives 0;
  a missing key gives null); `{}` gives 0.
* The column is `float64`; a text branch makes it `string` (the other branch is converted).

Where the baseline silently degrades, Shape raises a `StrategyError` that names the column: a
condition that is none of the four forms, a column the condition names that is not generated
before this one, and an inline strategy other than `fixed` and `lookup`.

### `correlated`
`{"source_column": "unit_price", "rule": "multiply", "params": {"factor_min": 0.3, "factor_max": 0.7}}`.
`rule` (also spelled `operation`) is `multiply` (source times a uniform factor, default 0.30 to
0.70), `add` (plus a uniform offset, `offset_min` and `offset_max`, default 0 to 10) or `subtract`
(minus such an offset, never below 0). `params.min` and `params.max` stand for either pair. The
result is rounded to the column's `scale` (2 without one). A null source gives null. `float64`.

### `reference_data`
`{"dataset": "colors"}` or `{"dataset": "products", "field": "category"}`. A dataset of strings is
sampled uniformly; a dataset of records with `field` gives that field of a uniformly chosen record
(so a value repeated in the dataset is more likely); records without `field` are read as `name`
(or `value`) with a `weight`, and a name is drawn in proportion to its weight. The column has the
type of the values (`string`, `int64`, `float64`; a field that mixes types becomes `string`).

### `record_sample` and `record_field`
`record_sample` (`{"dataset": "places", "field": "city"}`) is the anchor of a group of columns that
share one randomly chosen record: this column is that record's `field`. `record_field`
(`{"dataset": "places", "field": "zip"}`) columns of the same table and dataset give the other
fields of the same record. `"unique": true` on the anchor gives every row a different record when
the table has no more rows than the dataset (the records are a keyed permutation of the dataset,
`shape.generation.permutation.permute`); with more rows it falls back to sampling with
replacement, as the baseline does. With several `record_sample` columns for one dataset the last
one defined decides the record.

`record_field` does not read what the anchor made: it recomputes the record from the anchor's own
stream, so the pair agrees for any chunking, the anchor may be defined before or after the field,
and the anchor's null rate does not touch the fields.

### `temporal`
`{"pattern": "seasonal", "start": "2022-01-01", "end": "2025-12-31", "profiles": {...}}`.

* Range: `date_range` (or `range`) `{"start", "end"}`, else top-level `start`/`end`, else
  `range_ref: "model.date_range"` (the schema's own range), else 2022-01-01 to 2025-12-31.
* `pattern: "uniform"` (the default, and what any other value means): uniform on `[start, end)`.
* `pattern: "seasonal"`: `profiles.month` (`Jan`..`Dec`) and `profiles.day_of_week` (`Mon`..`Sun`)
  weigh the days (a name left out weighs `1/12` or `1/7`; `month_weights` and
  `day_of_week_weights` at the top level are accepted too). The probability of a (month, weekday)
  bucket is the product of the two weights, shared equally by the days of the range in it; the
  probability of a bucket with no day in the range is spread over the other days. The end date is a
  possible day. Without a month or weekday profile the range is uniform.
* `profiles.hour_of_day` replaces the time of day by a whole second in an hour drawn uniformly, or
  from `{"distribution": "bimodal", "peaks": [12, 18], "std_dev": 2}`: equally likely Gaussian
  peaks, wrapped around midnight. Without it the time of day is uniform to the microsecond.
* The column is `timestamp[us]`. Calendars, paydays and trends (`shape.calendars`) are separate.

### Reference datasets (`shape.generation.reference`)
`load_dataset(name)` finds a dataset in this order: datasets registered in the process
(`register_dataset(name, rows_or_table)`; a domain plugin registers the `reference_data` tables of
its `DomainDefinition`), then `<name>.json` in each search directory (`add_search_path(dir)`, then
the directories of the `SHAPE_REFERENCE_PATH` environment variable, named `REFERENCE_PATH_ENV`). A JSON dataset is a list of
strings or a list of objects whose keys are the fields (the first object names them). `Dataset`
holds Arrow columns; `unregister_dataset` and `clear_search_paths` undo the above;
`DatasetNotFoundError` lists where it looked. Loaded files are cached by path and modification time.

### Tests
`tests/generation/test_strategies_p404c.py` follows the recipe above for these strategies; the
cases are in `strategy_1to1/cases.py`. Datetime columns are compared on month, weekday and hour of
day profiles (and the share of whole-second values) as well as KS.
