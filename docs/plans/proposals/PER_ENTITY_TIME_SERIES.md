# Proposal: per-entity time series

Status: **proposed, not scheduled.** Written 2026-10-13 with the `capital_markets` trade-date fix,
which documented the gap below as a known limitation instead of building an engine in that change.

## The gap

Every generation strategy is row addressed (`docs/GENERATION_STRATEGIES.md`, "Rules every strategy
follows"): the value of row `r` depends on the seed, the table, the column, `r` and the other
columns of the same row, never on another row. Nothing in the catalogue makes the rows of one entity
a series ordered in time:

- `temporal` draws each row's timestamp on its own; rows are not ordered by entity or date.
- `distribution` draws each value on its own.
- `correlated` and `derived` read columns of the same row only, so a day's `close` cannot lead to
  the next day's `open`.
- `lifecycle` and `scd2` order a few status rows per key, with no numeric path.
- `shape-simulation`'s IoT drift adds a random walk to sensor readings after generation, inside
  that plugin; it is not a strategy a schema can name.

The result, in `capital_markets.daily_price` (`plugins/shape-domains/.../capital_markets/schema.json`):
`open` is an independent log-normal draw per row, `high`, `low`, `close` and `adj_close` are
multiples of it, and one ticker's consecutive days are unrelated. The data serves volume, schema and
join tests, not returns, volatility, backtests or anything that reads a price path. The same gap
affects account balances, meter readings, inventory levels and any other quantity that should
evolve per entity.

## Proposed behaviour

A table-level generator (a "series table") rather than a column strategy, because a series needs
the whole of an entity's rows at once:

```json
"daily_price": {
  "series": {
    "entity": "ticker",
    "time": {"column": "trade_date", "calendar": "<a calendar spec>", "step": "day"},
    "columns": {
      "close": {"model": "gbm", "start": {"distribution": "log_normal", "mean": 4.0, "sigma": 1.2},
                "drift": 0.07, "volatility": {"model": "garch11", "omega": 1e-6, "alpha": 0.08,
                "beta": 0.9}, "jumps": {"rate": 2.0, "mean": -0.02, "std_dev": 0.05}},
      "open": {"from": "close", "lag": 1, "gap": {"distribution": "normal", "std_dev": 0.003}},
      "high": {"envelope": "max", "of": ["open", "close"], "spread": 0.01},
      "low": {"envelope": "min", "of": ["open", "close"], "spread": 0.01},
      "volume": {"model": "log_ar1", "mean": 15, "phi": 0.6, "sigma": 0.5,
                 "with_volatility": 0.4}
    },
    "correlation": {"by": "sector_id", "within": 0.4, "market": 0.3}
  }
}
```

1. **Rows and order.** One row per entity per open day of the calendar between the entity's first
   and last day (an optional listing and delisting date per entity). The table is sorted by entity,
   then time. Row counts follow from the calendar, not from `ratio`; `per_parent` remains for the
   number of entities.
2. **Random walk.** Log returns `r_t = mu dt + sigma_t sqrt(dt) z_t` (geometric Brownian motion);
   `close_t = close_{t-1} exp(r_t)`. The start value is a distribution draw per entity.
3. **Volatility clustering.** `sigma_t` from GARCH(1,1) (`omega`, `alpha`, `beta`, with
   `alpha + beta < 1` checked) or a constant. Heavy tails through Student-t innovations (`df`).
4. **Jumps.** A Poisson number of jumps per step (`rate` per year) with normal log sizes (Merton);
   optional scheduled jumps on dates of another table (earnings, splits) with their own size.
5. **Correlation between series.** A one-factor or sector-factor model: `z_t = sqrt(m) M_t +
   sqrt(w) S_{sector,t} + sqrt(1 - m - w) e_t`, so tickers co-move with the market and their
   sector. The factors are drawn once per time step from their own streams.
6. **Derived columns.** `open` from the previous close plus an overnight gap, `high` and `low` as
   envelopes of `open` and `close`, so the `daily_price` business rules hold by construction;
   `adj_close` from splits and dividends of their tables when given.

## Determinism

A series cannot be row addressed in the usual sense: row `t` of an entity depends on rows before
it. The proposal keeps the property that matters: output does not depend on chunking or threads.

- Innovations are row addressed: `z` for (entity, step) comes from a Philox stream keyed
  `(seed, table, column, "z")` at the row's index, and the factor draws from streams keyed by the
  step alone. Only the cumulative sum is sequential.
- Chunks are whole entities. An entity's path is a prefix scan over its innovations (GARCH is a
  sequential recurrence, done in the kernel); entities are independent given the factors, so they
  run in parallel.
- The same seed gives the same bytes at any chunk size or thread count; a test in the style of
  `test_identical_for_any_chunking_and_random_access` covers it.

## Where it lives

- Kernel: the scan, GARCH recurrence and jump draws in `rust/shape-kernel`, with the pure-Python
  kernel in step (both kernel modes run the same tests).
- Engine: the `series` table block in `shape.generation.schema` (keys checked like
  `spec_keys`), planning of row counts from the calendar, and the published JSON Schema.
- Fitting: `shape generate --from` could fit drift, GARCH and correlation from a profiled table that
  has an entity key and a time column (a later step; the profile would need per-entity return
  statistics).
- Domains: `capital_markets.daily_price` moves to a series table; `financial` balances,
  `iot.reading` and `telecom.usage_record` are candidates.

## Tests and acceptance

- Statistical: the mean and standard deviation of log returns match `mu` and `sigma` within
  tolerance; squared returns are autocorrelated under GARCH and not under a constant volatility;
  the jump count matches `rate`; the pairwise correlation of returns within a sector matches the
  factor loadings.
- Structural: one row per entity per open day, sorted; `low <= open, close <= high`; the next
  `open` follows the previous `close` within the gap distribution.
- Determinism across chunk sizes, thread counts and both kernel modes.
- Normative behaviour gets requirement rows in `docs/specs/requirements.yaml` and conformance
  tests, as `CONTRIBUTING.md` asks.

## Open questions

- Table-level block or a group of column strategies with a shared anchor (like `record_sample` and
  `record_field`)? The anchor form fits the existing catalogue but cannot set row counts or order.
- How a series table interacts with `null_rate`, `shape chaos` and incremental batches (a batch
  appends days, so its first row must continue the previous batch's last state).
- Intraday series (`trade` ticks) on the same paths: a Brownian bridge between daily bars.
