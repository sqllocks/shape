# Built-in plugins

Everything core ships is a plugin: it registers through the same entry points as a third-party
plugin and is reached through the [plugin host](host.md). `shape plugins doctor` lists them all
(with their `sqllocks-shape` source).

| Group | Name | What it is |
|---|---|---|
| `shape.sources` | `csv` | CSV and TSV files (`.csv`, `.tsv`, compression suffixes) |
| `shape.sources` | `parquet` | Parquet files |
| `shape.sources` | `jsonl` | JSON Lines files (`.jsonl`, `.ndjson`) |
| `shape.sources` | `ipc` | Arrow IPC files (`.arrow`, `.ipc`, `.feather`) |
| `shape.sources` | `abfss` | CSV, Parquet, JSONL and IPC files in OneLake and ADLS Gen2, by `abfss://` URI (extra `[azure]`; see [cloud-sources.md](cloud-sources.md)) |
| `shape.sources` | `delta` | Delta tables: a local directory, or `delta+abfss://` in OneLake and ADLS Gen2 (extra `[azure]` for cloud tables) |
| `shape.sinks` | `csv` | CSV file |
| `shape.sinks` | `parquet` | Parquet file (zstd by default) |
| `shape.sinks` | `jsonl` | JSON Lines file |
| `shape.sinks` | `ipc` | Arrow IPC file |
| `shape.detectors` | `email` | email syntax |
| `shape.detectors` | `us_ssn` | US social security number syntax |
| `shape.detectors` | `phone` | telephone-like syntax |
| `shape.detectors` | `ipv4` | IPv4 address syntax |
| `shape.fitters` | `auto` | best of normal, uniform, exponential and lognormal, as `shape.profile` picks it |
| `shape.strategies` | `constant` | one value repeated |
| `shape.strategies` | `sequence` | `start + row * step`, independent of chunking |
| `shape.strategies` | `choice` | weighted choice from values |
| `shape.strategies` | `uniform` | uniform on `[low, high)` |
| `shape.strategies` | `normal` | normal with `mean` and `stddev` |
| `shape.strategies` | `address` | coherent addresses from reference rows |
| `shape.strategies` | `uuid` | version-4 UUID strings |
| `shape.strategies` | `weighted_enum` | a value from a `{value: weight}` mapping (alias sampling) |
| `shape.strategies` | `distribution` | values from a distribution family, clipped by `min`/`max` and rounded to the column's scale |
| `shape.strategies` | `empirical` | inverse-transform sampling of a stored quantile fingerprint |
| `shape.strategies` | `pattern` | strings from a format with `{seq:n}`, `{random:n}` and `{column}` tokens |
| `shape.strategies` | `lookup` | a column of a parent table, by the key in this row |
| `shape.strategies` | `conditional` | one of two values per row, chosen by a condition on another column |
| `shape.strategies` | `correlated` | another column times a random factor, plus or minus a random offset |
| `shape.strategies` | `reference_data` | a value or a weighted name from a named reference dataset |
| `shape.strategies` | `record_sample` | one field of a randomly chosen reference record (the anchor of a record group) |
| `shape.strategies` | `record_field` | another field of the record the table's `record_sample` column chose |
| `shape.strategies` | `temporal` | timestamps, uniform or with month, weekday and hour profiles |
| `shape.distributions` | `normal` | `loc + scale * N(0,1)` |
| `shape.distributions` | `uniform` | uniform on `[loc, loc + scale)` |
| `shape.distributions` | `exponential` | `loc + scale * Exp(1)` |
| `shape.distributions` | `lognormal` | `loc + scale * exp(s * N(0,1))` |
| `shape.distributions` | `log_normal` | `exp(mu + sigma * N(0,1))` |
| `shape.distributions` | `pareto` | Type I Pareto with `alpha` and `xm` |
| `shape.distributions` | `zipf` | Zipf on `1 .. max` with exponent `a` |
| `shape.distributions` | `geometric` | trials to the first success, probability `p` |
| `shape.distributions` | `poisson` | Poisson with mean `lam` |
| `shape.distributions` | `bernoulli` | 0 or 1 with `P(1) = p` |
| `shape.distributions` | `gamma` | gamma with shape `k` and scale `theta` |
| `shape.distributions` | `beta` | beta with `a` and `b` |
| `shape.distributions` | `weibull` | Weibull with shape `k` and scale `lam` |
| `shape.distributions` | `triangular` | triangular on `[low, high]` with peak `mode` |
| `shape.distributions` | `negative_binomial` | failures before the `r`-th success, probability `p` |
| `shape.distributions` | `power_law_cutoff` | density proportional to `x ** -alpha * exp(-lam * x)` for `x >= xmin` |
| `shape.distributions` | `mixture` | a weighted mixture of other families |
| `shape.distributions` | `truncated` | a family restricted to `[low, high]` |
| `shape.distributions` | `histogram` | an empirical histogram (`edges`, `weights`) |
| `shape.calendars` | `us_federal` | the eleven US federal holidays on their observed days |
| `shape.calendars` | `us_retail` | Black Friday, Cyber Monday, Christmas Eve and the gift holidays |
| `shape.calendars` | `composite` | any mix of holiday calendars, custom events, paydays, month-end and quarter-end effects and trends (`with_spec`) |

## Rules

- The entry points are declared in `pyproject.toml` and mirrored in
  `shape.builtins.catalog`; a test fails if they differ. `shape.plugins.registry` registers any
  built-in that discovery did not find, so a source tree with stale metadata still lists them.
- Only `shape.plugins.registry` may import `shape.builtins` (import-linter contract in
  `pyproject.toml`, run by `lint-imports` in `make check` and CI). Library code reaches a built-in
  with `default_host().get(group, name)`.
- Plugins load lazily: listing or running `shape profile` imports none of them.
- The fitter and the distributions `normal`, `uniform`, `exponential` and `lognormal` use scipy's
  parameter names (`loc`, `scale`, and `s` for lognormal); the other distributions take their own
  (`mu`, `sigma`, `alpha`, `lam`, ...), see `docs/GENERATION_STRATEGIES.md`.
- Every draw is addressed by row (`docs/GENERATION_KERNEL.md`): a column's values do not depend
  on how the table is chunked.
- The calendars' lift is neutral (1.0) unless `holiday_lift` or `lifts` is set (ramp-up and decay
  optional, `docs/GENERATION_CALENDARS.md`); `holidays(start, end)`
  returns the rule-derived dates.
- Not built-ins yet: the DB-API, Kafka and Event Hubs connectors (`shape.connectors`) need a live
  connection object or a broker, not a URI; they move into plugins with the Phase 6 work.
