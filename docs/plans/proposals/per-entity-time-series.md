# Proposal: per-entity time series as a generation strategy

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

## 1. The problem

A price, a balance or a sensor reading is a series: today's value is yesterday's value plus a change. Analytics, risk models, anomaly detectors and dashboards all depend on that: returns, moving averages, volatility, drawdowns and "balance never below zero" mean nothing on values that are unrelated from one row to the next.

Shape generates every numeric value on its own:

- **The rule is row addressed.** `docs/GENERATION_STRATEGIES.md:38` (rule 1): the value of row `r` depends on the seed, the table, the column, `r`, the spec and the other columns of the same row. The stream key is `(seed, table, column, label)` (`src/shape/generation/rng.py:41`) and the row index is the counter.
- **Numeric strategies follow it.** `distribution` (`src/shape/builtins/strategies/numeric.py:38`, `generate` at `numeric.py:62`) and `empirical` (`numeric.py:110`) draw each row from its family. `correlated` (`src/shape/builtins/strategies/linked.py:174`) scales a column of the same row. `bootstrap`, `formula` and `derived` read the same row or a parent row. No strategy reads the previous row of the same entity.
- **The concrete defect.** The open `capital_markets` domain describes `daily_price` as "Daily OHLCV bars, generated via Geometric Brownian Motion" (`plugins/shape-domains/src/shape_domains/data/capital_markets/schema.json:376`), but `ticker` is a uniform `foreign_key` (`schema.json:395`), `trade_date` uses a `trading_days` pattern that does not exist and falls back to uniform (`schema.json:408`; `docs/GENERATION_ENGINE.md:311`), `open` is an independent `log_normal` draw per row (`schema.json:423`), and `high`, `low`, `close` and `adj_close` are `correlated` multiples of the same row's `open` (`schema.json:442` to `513`). The same ticker's close jumps between unrelated levels from one day to the next, a ticker can have two rows on one day, and `close` can fall outside `[low, high]` until the business rules (`schema.json:1204`) repair it.

What exists and is reused:

- **Row-sequential strategies.** `first_per_parent` and `scd2` (`src/shape/builtins/strategies/groups.py:1` to `7`) read the whole column once, run a row-sequential kernel pass, keep the result with `Engine.cached` and give each chunk its slice. `GENERATION_STRATEGIES.md:454` names them as the documented exception to rule 1. The kernel passes (`docs/GENERATION_KERNEL.md:105`) include `group_order` (`src/shape/generation/kernel_relational.py:48`), which gives each row its position within its group, and `scd2_offsets`, which draws from a stream keyed by the group's first row so the result is independent of chunking.
- **Aggregates of child rows.** The compute phase fills `computed` columns once every table exists (`GENERATION_STRATEGIES.md:267`, `src/shape/generation/compute.py:6`): `sum_children`, `count_children` and others. A total exists; a running total does not.
- **Series outside generation.** The behavior `telemetry_series` primitive is a linear trend plus independent noise, with a "stuck" repeat of the previous value (`plugins/shape-behavior/src/shape_behavior/primitives.py:312` to `318`): not a random walk. `iot_patterns` adds a per-sensor random walk with `np.cumsum` (`plugins/shape-simulation/src/shape_simulation/iot_patterns.py:261`), but it draws from `np.random.default_rng(seed)` (`iot_patterns.py:170`), so it is not row addressed, and it runs on tables after generation.
- **Checks.** `src/shape/quality/timeseries.py` checks gaps, stuck runs and DST (`timeseries.py:4` to `9`); nothing checks returns or autocorrelation.

## 2. Proposed design

### a. The `series` strategy

A row-sequential strategy, built like `scd2`: read the `entity` and `order` columns once, sort the rows by `(entity, order, row)`, run one pass per entity, cache the result, and slice it per chunk.

```json
{"name": "close", "type": "decimal",
 "generator": {"strategy": "series", "entity": "ticker", "order": "trade_date",
               "model": "geometric", "start": {"distribution": "log_normal", "params": {"mean": 4.0, "sigma": 1.0}},
               "drift": 0.06, "volatility": 0.25, "per": "year", "steps_per": 252,
               "volatility_model": {"kind": "garch", "omega": 0.000002, "alpha": 0.08, "beta": 0.9},
               "jumps": {"rate": 2, "size": {"distribution": "normal", "params": {"mean": -0.03, "std_dev": 0.05}}},
               "correlation": {"group": "sector", "rho": 0.5},
               "floor": 0.01}}
```

- **`model`**: `random_walk` (`x_t = x_{t-1} + step`, step from any `distribution` spec), `geometric` (`x_t = x_{t-1} * exp((drift - vol^2/2) dt + vol sqrt(dt) z)`), `mean_reverting` (`x_t = x_{t-1} + speed (mean - x_{t-1}) dt + vol sqrt(dt) z`, an Ornstein-Uhlenbeck step; `"log": true` reverts the logarithm, for rates and prices).
- **`start`**: the first value of each entity, a distribution spec, a number, or `{"column": "listing_price"}` read from the entity's first row (a parent value through `derived` first).
- **`dt`**: `per` and `steps_per` turn annual drift and volatility into one step. With `"dt": "order"` the step uses the actual gap between consecutive `order` values (irregular readings).
- **`volatility_model`** (clustered volatility): `constant` (default), `garch` (GARCH(1,1) on the step's shock), or `regime` (two volatilities with switching probabilities).
- **`jumps`**: a Poisson count per step at `rate` per `per`, each jump drawn from `size` and added to the log (geometric) or the value.
- **`correlation`**: the shock of every entity with the same `group` value (a column of the row, such as `sector`) at the same `order` value shares a common factor: `z = sqrt(rho) c + sqrt(1 - rho) e`. The common draw `c` is keyed by `(group value, order value)`, so it is the same for all entities in the group on that date, whatever the chunking.
- **`floor`** and `ceiling` clip, and `round` rounds the output.
- **Draws.** Each entity's shocks come from a stream keyed by the entity's value (not its first row, so adding rows of another entity does not move this one), word `v` for its `v`-th step, as `scd2_offsets` does with its group stream.
- **Ties.** Two rows of one entity with the same `order` value are an error naming both rows, unless `"ties": "row_order"` is given.

### b. Fields of a series: OHLC and returns

`series_field` reads the cached series of another column by role, like `record_field` and `self_ref_field`: `previous` (the value one step earlier, null for the first), `return` (`x_t / x_{t-1} - 1`), `log_return`, `change`, and a bar: `open` (the previous close times a small gap draw), `high` and `low` (the bar's extremes drawn so `low <= min(open, close)` and `high >= max(open, close)` always hold). A bar is consistent by construction, so business rules no longer have to repair it.

### c. Panel rows: one row per entity per step

A series is only useful when the rows form a grid: each ticker once per trading day. Two small additions:

- `foreign_key` gains `"layout": "blocked"`: row `r` takes parent `r // per_parent` (with the `derived_counts` `per_parent` ratio the table already declares, `schema.json:1315`), so rows of one parent are contiguous.
- `temporal` gains `"pattern": "per_entity_sequence"` with `entity`, `every` (`1 day`, `1 hour`) and an optional `calendar` (business days only, holidays from the calendars of `docs/GENERATION_CALENDARS.md`): the `v`-th row of an entity (from `group_order`) gets the `v`-th step from `start`. This is also the `trading_days` pattern the domain already names.

### d. Running balances (compute phase)

Two new `computed` rules, run by the compute phase after every table exists:

- `running_sum` over the same table: `{"rule": "running_sum", "column": "amount", "by": "account_id", "order": "posted_at", "opening": {"column": "opening_balance", "via": "account_id"}}` gives `balance_after` on each transaction.
- `running_sum_children` from another table: a snapshot table `(account_id, as_of_date)` gets `{"rule": "running_sum_children", "child_table": "transaction", "child_column": "amount", "child_order": "posted_at", "order": "as_of_date"}`: the opening balance plus every child amount of that parent with `posted_at <= as_of_date`.

Both reuse the compute phase's grouping (`group_sums`, `compute.py:73`); they are exact and draw nothing. A `"min": 0` option reports (it does not hide) the first row where the balance would go below zero, so a schema author sees that the amounts need a rule.

### e. Determinism and the kernel

`GENERATION_KERNEL.md:110` says every row-sequential kernel result is an integer or a flag, which is why native and twin agree exactly. A series is floating point. Proposed: the series pass runs in numpy in both kernel modes (one implementation, no twin), with a fixed operation order per entity, so output is identical across kernel modes and chunk sizes. Whether it is byte-identical across platforms is the same question the existing `normal` and `log_normal` draws already raise (W8-04); see open question 2.

## 3. Compatibility

- **New strategies and keys.** `series`, `series_field`, the `blocked` layout, the `per_entity_sequence` pattern and the two compute rules are new, so every existing spec is unchanged and its output byte-identical for every seed. Each new strategy starts at `generator_version` 1 (`GENERATION_STRATEGIES.md:27`).
- **Older readers.** The generation-spec JSON Schema is closed per strategy (`additionalProperties: false`, `src/shape/schemas/generation-spec-v1.schema.json:2719` for `temporal`), and an unknown strategy is an error, so a spec using any of them fails loudly on an older reader. `"layout"` on `foreign_key` and the new `temporal` pattern must be added to `spec_keys.py`; without them an older reader would treat the unknown `pattern` as `uniform` silently (`temporal.py:255`). Proposed: the generation spec's version follows the state and compatibility policy (W1-01) for that reason.
- **The `capital_markets` domain.** Rewriting `daily_price` with these strategies changes that domain's output for every seed. That is a separate, announced item (phase 3) with a changelog entry, not part of the strategy work.
- **Profiles.** Phase 1 and 2 do not change the profile format. Fitting series parameters from a profile (open question 3) would.

## 4. Wanted (ready to paste into the issue)

Phase 1, the series strategy:
1. A built-in `series` strategy with `entity`, `order`, `model` (`random_walk`, `geometric`, `mean_reverting` with `log`), `start` (distribution, number or column), `drift`, `volatility`, `mean`, `speed`, `per`, `steps_per`, `dt`, `floor`, `ceiling`, `round`, `ties`; registered in `STRATEGY_KEYS`, `STRATEGY_REQUIRED_KEYS` and the published generation-spec JSON Schema.
2. `volatility_model` (`constant`, `garch`, `regime`) and `jumps` (`rate`, `size`).
3. `correlation` (`group`, `rho`) with the common factor keyed by group value and order value.
4. Per-entity streams keyed by the entity value; a row-sequential pass cached with `Engine.cached` and sliced per chunk, identical for any `chunk_rows`, thread count and kernel mode.
5. `series_field` with roles `previous`, `return`, `log_return`, `change`, `open`, `high`, `low`.

Phase 2, panel rows and balances:
6. `foreign_key` `"layout": "blocked"` and `temporal` `"pattern": "per_entity_sequence"` (with `every` and an optional business-day `calendar`).
7. Compute rules `running_sum` and `running_sum_children` with `opening` and the `min` report.

Phase 3, the domain:
8. `capital_markets.daily_price` rebuilt on items 1 to 6 (one row per ticker per trading day, a geometric close with clustered volatility and a sector correlation, a consistent bar), with a changelog entry for the changed output.
9. Documentation in `docs/GENERATION_STRATEGIES.md` (a "Series" section with the model equations) and `docs/GENERATION_KERNEL.md` (why the series pass has no twin).

Acceptance beyond section 13's common items: over a large panel, the sample mean and standard deviation of log returns of a `geometric` series match `drift` and `volatility` within a stated tolerance; the lag-1 autocorrelation of returns is near 0 and that of a `random_walk` level is near 1; with `garch`, the autocorrelation of squared returns is positive; with `correlation`, the cross-entity correlation of returns within a group matches `rho` within a stated tolerance and across groups is near 0; every bar satisfies `low <= open, close <= high`; `running_sum` equals a reference cumulative sum exactly; the same seed gives byte-identical output for any chunk size and in both kernel modes; existing specs give byte-identical output before and after the change.

## 5. Open questions for the lead
1. Whether `series` should also be offered as a behavior primitive (a `telemetry_series` with `model`), or the behavior plugin keeps its own simpler series.
2. Whether floating-point series need a cross-platform byte-identity promise now (W8-04), which would mean avoiding platform `exp` and `log` in the pass, or whether identity per platform is enough.
3. Whether profiling should estimate series parameters (per-entity drift, volatility, autocorrelation) so `profile` then `generate --from` reproduces them, as a later package.
