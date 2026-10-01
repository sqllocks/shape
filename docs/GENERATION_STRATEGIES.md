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

## Strategies of P4-04d

The relational strategies. All are row addressed (the parent or version of row `r` is a function of the
seed, the table, the column and `r`), so a chunk read in any order gives the same values. The ones that look
at a whole group of rows read the whole column once and keep the result with `Engine.cached`; the
row-sequential passes run in the kernel (`shape.generation.kernel_relational`, `docs/GENERATION_KERNEL.md`).
Their equivalence to the baseline is tested per strategy in `tests/generation/test_strategies_p404d.py`
(cases in `strategy_1to1/relational_cases.py`).

### `foreign_key`
`{"ref": "parent.pk"}`: a value of the parent's key column. The parent comes from the engine's key pool
(`ctx.engine.key_pool`); a `ref` to a column that is not the key uses that column's values.

| Key | Meaning |
|---|---|
| `distribution` | `uniform` (default), `zipf` (`alpha`, 1.5) or `pareto` (`alpha`, 1.2); anything else is uniform. `alpha` and `max_per_parent` may be at the top level or under `params` (`params` wins) |
| `max_per_parent` | with `pareto`: no parent gets more rows (a row-sequential pass over the table, kept in memory) |
| `constrained_by` | a column of this table; the key is drawn from the parent rows whose column of the same name has the same value. No such parent: null when the column is nullable, else any parent |
| `sample_rate`, `filter` | the rows take the parents of one random sample, without replacement, of `max(1, int(rows * sample_rate))` parent rows (those matching `"column = 'value'"`); a table with more rows than the sample wraps around it |

A `ref` to the table's own key draws uniformly among all its rows (`distribution` does not apply); give the
column `nullable` and a `null_rate` for roots.

`zipf` is Zipf truncated to the parent rows, `P(k)` proportional to `k ** -alpha`. Ranks up to 2**20 are
exact; a larger pool is drawn by inverting the integral of the same power law beyond that. `pareto` cuts a
Lomax draw at its theoretical 99.5th percentile and scales it to the pool (the baseline cuts at the sample's
percentile; the two agree for any realistic chunk).

### `composite_foreign_key` and `composite_fk_field`
`{"ref_table": "line", "ref_columns": ["order_no", "line_no"], "distribution": "uniform"}` draws one row of
the parent table (`uniform`, or `zipf` with `params.alpha`; anything else is uniform) and hands every
`ref_columns` value of it to the row: the column itself gets the first. `{"source_column": "order_no",
"ref_column": "line_no"}` on another column reads one of them (the column must come after the
`composite_foreign_key` column in the table). Only the table's own columns are output.

### `first_per_parent`
`{"parent_column": "customer_id", "default": true}`: `True` on the first row of each value of the parent
column and `False` on the rest (`"default": false` swaps them). Nulls count as one value. Row-sequential:
the parent column is generated for the whole table once.

### `self_referencing` and `self_ref_field`
`{"pk_column": "category_id", "levels": 3, "root_count": 8}` (`max_depth` is an alias of `levels`; `root_count`
defaults to a tenth of the rows): the first `root_count` rows (at most `rows // levels`, at least 1) are level
1 and have no parent; the rest share levels 2 .. `levels` evenly (the first levels take the remainder), and each
takes a uniformly drawn row of the level above as its parent. `{"field": "level"}` on another column gives
the 1-based level. The structure depends on the table's row count, not on the chunk.

### `lifecycle`
`{"phases": {"introduced": 0.1, "active": 0.75, "discontinued": 0.15}}` (or `"values"`): a label with
probability proportional to its weight. Labels are always strings.

### `scd2`
`{"role": "effective_date", "business_key": "customer_id", "min_gap_days": 1}`. Roles: `effective_date` (the
versions of a key get increasing dates in the model's `date_range`, at least `min_gap_days` apart, in row
order; `timestamp[us]` at midnight), `end_date` (the next version's date minus `min_gap_days`; null for the
latest; `effective_date_column` names the date column, default `effective_date`), `is_current` (the latest
version) and `version` (1, 2, ... in date order; row order when there is no date column). Rows with a null
business key get nulls.
