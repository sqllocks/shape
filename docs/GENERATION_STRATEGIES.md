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
   (`sequence`) as `int64`; numeric draws as `float64`; text as `string`. The one exception is a
   generator that asks for it: `"output_type": "int64"` (or `float64`, `bool`, `string`) casts the
   strategy's output (floats are rounded first), which is how a profile's integer and boolean
   columns keep their type (`shape.generation.engine.cast_output`). Two more names read the
   column's declared type and are applied to the finished table, so the passes in between still
   work on numbers: `"output_type": "decimal"` is `decimal128(precision, scale)` (rounded to
   `scale`; a value that does not fit `precision` is an error naming the column) and
   `"output_type": "timestamp"` is `timestamp[us]` cut to the column's `precision` fractional
   digits. `shape from-ddl` writes them for `DECIMAL(p,s)`, `DATETIME` (3 digits) and
   `DATETIME2(n)` columns.

### Unknown keys

`GenSchema.validate()` warns about a key in a generator that its strategy does not read, with a "did
you mean" hint for a close name (`sigmaa` for `sigma`), and says so when the key is a **column**
property: `scale`, `null_rate`, `precision`, `max_length` and `nullable` belong on the column, so
a `scale` in a `distribution` generator was ignored and the numbers kept 14 decimal places. The keys
of every built-in strategy are in `shape.generation.spec_keys` (a test checks that no strategy reads
a key that is missing there); a strategy or distribution family from a plugin is not checked.
The same tables build the published JSON Schema, which reports these keys as errors at their place
(`docs/GENERATION_SPEC.md`).

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

## Strategies of P4-04b

### `native` and `faker`
`{"provider": "email"}`: realistic text from the reference pools in `shape/builtins/strategies/pools/`
(one entry per line; names, companies, street names, sentences, cities, state codes, e-mail domains,
URI domains and paths). `provider` defaults to `word`. Every draw is row addressed.

| Provider | Value |
|---|---|
| `first_name`, `last_name`, `company`, `sentence`, `city`, `state_abbr` | one pool entry, uniformly |
| `name` | `first last` |
| `email` | `first.last<1..998>@domain`, lower case, `domain` one of `example.com`, `example.org`, `example.net`; follows the row's own `first_name` and `last_name` columns when the table has both |
| `company_email` | `first.last@<company, lower case, no spaces, commas or dots, 20 characters>.example` |
| `phone_number` | `(AAA) 555-01SS`, `AAA` 200 to 998, `SS` 00 to 99: the 555-0100 to 555-0199 lines reserved for fiction |
| `ssn` | `AAA-GG-SSSS`, `AAA` 900 to 999 (never assigned), `GG` 1 to 99, `SSSS` 1 to 9999 |
| `street_address` | `<100..9998> <street> <St, Ave, Blvd, Dr, Ln, Way, Ct, Pl, Rd or Cir>` |
| `uri` | `https://<domain>/<path>`, `domain` one of `example.com`, `example.org`, `example.net` |
| `ipv4` | `A.B.C.D`, `A` and `D` 1 to 254, `B` and `C` 0 to 255 |
| `postcode` | five digits, 00501 to 99950 |
| `zip_plus4` | `NNNNN-NNNN`: a `postcode` and four digits, 0001 to 9999 |
| `pystr`, `word` | 12 characters from `a-z0-9` |

**Values that cannot belong to a real person (the default).** Synthetic data is shared, emailed and
loaded into test systems, so the identifier providers produce reserved values: the host names RFC 2606
reserves (`email`, `uri`, and the `.example` top-level domain for `company_email`), the 9xx areas of
`ssn`, which the SSA never assigns, and the 555-0100 to 555-0199 telephone lines. Two explicit
options give realistic values instead; **they can produce real people's addresses and numbers, so
never use them for data that leaves a test system**: `"domains": "realistic"` (`email`,
`company_email`, `uri`: real mail providers and `.com` hosts) and `"range": "assignable"` (`ssn`:
areas 001 to 899 without 666; `phone_number`: `(AAA) EEE-SSSS`, `EEE` 200 to 998, `SSSS` 1000 to
9998). Any other value of either option is a `StrategyError`. The `ipv4` provider is unchanged.

The column's `max_length` cuts the text. `native` raises `StrategyError` for any other provider.
`faker` serves the same providers identically; for any other provider it needs the `faker` package
(`pip install faker`, `ImportError` without it) and takes `args` as the provider's keyword
arguments. It draws a pool of `min(rows of the table, 50,000)` values once, from a Faker seeded by
the run seed, the table and the column, with the model's `locale`; row `r` reads pool entry `r`
while the table fits the pool (every value distinct), else a uniformly drawn entry. A strategy
that needs more than the pools offer is a `shape.strategies` plugin.

### `formula`
`{"expression": "quantity * unit_price * (1 - discount_percent / 100)"}`: a column computed from
other columns of the same row. The expression is parsed and checked against a fixed grammar, then
evaluated on whole columns with numpy; it is never passed to `eval`. Allowed: the names of numeric
or boolean columns generated earlier in the table, numbers, `+ - * / // % **`, unary `-` `+`,
comparisons (chained too), `and` `or` `not`, `a if c else b`, `abs`, `round`, `min` and `max`
(element-wise), `np_round`, `np_clip`, `np_where`, `np_maximum`, `np_minimum`, `np_abs`, `np_sqrt`,
`np_log`, `np_exp`, `np_floor`, `np_ceil` and `np_nan`. Anything else (attribute access, other
calls, text, indexing) is a `StrategyError` naming the column. A null in any column the expression
reads gives a null result; division by zero and the log of a negative give `inf` or `nan`, as in
numpy. The column's `scale` rounds. Integer inputs give `int64`, comparisons give `bool`.

### `derived`
`{"source": "order_date", "rule": "add_days", "params": {"distribution": "uniform", "min": 3, "max": 30}}`.
`source` is a column generated earlier in the table, or `"table.column"` with `via`, the name of
this table's foreign-key column (the parent's column is read by key; a missing parent gives
null). `rule` (alias `operation`) is `copy` (the default) or `add_days`: the source plus a whole
number of days drawn by `params.distribution` (`uniform` with `min` and `max`, `log_normal` with
`mean` and `sigma`, `normal` with `mean` and `std_dev`, the last two clipped to `[min, max]`; any
other name is `uniform`; `min` and `max` default to 1 and 30). `"days": N` at the top of the spec is
`add_days` of exactly `N`. The result keeps the source's date or timestamp type; ISO text is
read as `timestamp[us]` and text that is not a date becomes null.

### `computed`
`{"rule": "sum_children", "child_table": "order_line", "child_column": "line_total"}`: written as a
null placeholder and filled by the compute phase once every table exists
(`docs/GENERATION_ENGINE.md`). Rules: `sum_children`, `count_children`, `avg_children`,
`min_children`, `max_children` (rows without children get 0; floats are rounded to 2 places as
numpy rounds, so a sum never prints as `114.49000000000001`) and `lookup_parent` (copy a column of
the parent table through this table's foreign key). Sums and counts of integers stay integers.
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

### `bootstrap`
`{"dataset": "people", "field": "income", "jitter": 0.01}`: `field` of a source row of a dataset of
records, rows drawn with replacement. Every `bootstrap` column of a table that names the same
dataset takes the same source row for a given row, so the columns keep the source's joint
distribution. `jitter` (default 0.01, `0` for none) is the standard deviation of normal noise as a
fraction of the source column's standard deviation; it applies to integer and float fields only (they
become `float64`; a constant column and text are never jittered). Nulls stay null. The source rows are
copied: a bootstrapped table contains the people of the source (`docs/FIDELITY_TIERS.md`). The
library form, seeded with numpy's generator, is `shape.fidelity.bootstrap_table`.

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
* `end`: a date (`2026-05-01`) stands for the whole day, so the end day is a possible day for every
  pattern and `start == end` is one single day (a business day's landing file). An `end` with a time
  (`2026-05-01T12:00:00`) is the exact bound, exclusive for `uniform`. An `end` before the start is
  an error that says so; `start == end` with a time is an error that asks for dates.
* `pattern: "uniform"` (the default, and what any other value means): uniform on `[start, end]`
  (`end` as above).
* `pattern: "seasonal"`: `profiles.month` (`Jan`..`Dec`) and `profiles.day_of_week` (`Mon`..`Sun`)
  weigh the days (a name left out weighs `1/12` or `1/7`; `month_weights` and
  `day_of_week_weights` at the top level are accepted too). The probability of a (month, weekday)
  bucket is the product of the two weights, shared equally by the days of the range in it; the
  probability of a bucket with no day in the range is spread over the other days. The end date is a
  possible day, as it is for `uniform`. Without a month or weekday profile the range is uniform.
* `profiles.hour_of_day` replaces the time of day by a whole second in an hour drawn uniformly, from
  one weight per hour (`{"0": 0.01, ..., "23": 0.02}`, a profile's hour histogram), or from
  `{"distribution": "bimodal", "peaks": [12, 18], "std_dev": 2}`: equally likely Gaussian
  peaks, wrapped around midnight. Without it the time of day is uniform to the microsecond.
* `"granularity": "day"` cuts every value to midnight (a date column: the hour profile is ignored).
* The column is `timestamp[us]`, or the `unit` given (`s`, `ms`, `us` or `ns`): the same instants in
  another Arrow unit, for output that must match a `timestamp[ns]` column type. Calendars, paydays
  and trends (`shape.calendars`) are separate.

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

### `address`
`{"strategy": "address", "scope": [{"state": "WA"}]}`: coherent addresses from a scope alone. The
column is a struct of `address_line_1`, `city`, `county`, `state`, `postal_code`, `country`,
`latitude`, `longitude`, `timezone`, `mode` and `reference_id`, or, with `"field": "city"` (also
`zip`, `lat`, `lng`, ...), one of them as a plain column.

* **Scope.** A list (or one entry) of `{"state": "WA"}`, `{"postal_code": "98101"}`,
  `{"city": "Seattle", "state": "WA"}`, `{"county": ..., "state": ...}`, `"WA"`, `"98101"` or
  `"Seattle, WA"`; `weights` (one per entry) and `exclude` (places to leave out). Without a scope
  every state of the reference is an entry of equal weight. A place the reference does not have
  is a `StrategyError` naming the column.
* **Reference.** The places come from `reference`: `{"dataset": name}` (any dataset registered with
  `shape.generation.reference.register_dataset`; its columns `city`, `state`, `postal_code` (or
  `zip`), `latitude` (or `lat`) and `longitude` (or `lng`) are required, `county`, `country`,
  `street`, `timezone` optional), or rows given inline (dicts, `AddressReference`, `Location`, or
  what `shape.location.load_geonames_postal` returns). Without `reference` the dataset
  `us_zip_locations` is used: 40,977 US ZIP codes with city, state and coordinates, shipped by the
  `sqllocations-shape-domains` package (GeoNames data, attribution in `THIRD_PARTY_NOTICES.md`);
  without that package the error says so. A named dataset keeps a schema small; the reference is
  compiled once per engine, not per chunk.
* **Coherence.** One place is drawn for every row, and the city, state, ZIP and coordinates are
  that place's (the coordinates within 0.002 degrees of its reference point, or exactly at it in
  `reference` and `exact_reference` modes). Every address column of a table with the same `group`
  (default `address`) draws the same place for the same row, so separate `city`, `state`,
  `postal_code`, `latitude` and `longitude` columns agree; give a second set of columns another
  `group` for an independent address (a work address next to a home address).
* **Mode.** `street_synthetic` (default): a house number 1 to 9999 and the reference street's name
  (or, when the reference has no streets, a name and a suffix from Shape's street pools);
  `geographic`: the number and `Synthetic Way`; `reference` and `exact_reference`: the reference's
  street and point as they are (the reference needs streets).

Row addressed: the value of row `r` depends on the seed, the table, the group, `r` and the spec,
never on the chunk.

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
| `fan_out` | the 80/20 helper (`FanOut`, below): `{"top_fraction": 0.2, "top_share": 0.8, "shape": "power"}`; replaces `distribution` |
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

## Skewed fan-out

`shape.generation.fanout.FanOut(n_parents, top_fraction=0.2, top_share=0.8, shape="power" |
"two_tier", shuffle=True)` draws a parent index for each child row (`draw(stream, row_start,
n_rows)`) so that the top `top_fraction` of parents hold `top_share` of the children (the 80/20
rule). `concentration_weights(...)` gives the rank weights and `top_share_of(parents, n_parents,
fraction)` measures a generated column. `FanOut.from_spec` reads `{"top_fraction", "top_share",
"shape", "shuffle"}`; P4-04d's `foreign_key` uses it for a `fan_out` key.

`kernel_ops.alias_table(weights)` returns an `AliasTable` (`size`, `prob`, `alias`), cached by the weights.
