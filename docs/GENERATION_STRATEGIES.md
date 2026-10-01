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
