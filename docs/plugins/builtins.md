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
| `shape.sinks` | `parquet` | Parquet file (snappy, dictionary encoding on; T-17) |
| `shape.sinks` | `jsonl` | JSON Lines file |
| `shape.sinks` | `ipc` | Arrow IPC file |
| `shape.sinks` | `tsv` | Tab-separated file |
| `shape.sinks` | `sql` | SQL `INSERT` script, with optional DDL (`tsql`, `tsql-fabric-warehouse`, `postgres`, `mysql`) |
| `shape.sinks` | `excel` | Excel workbook (`pip install 'sqllocks-shape[excel]'`, openpyxl) |
| `shape.sinks` | `delta` | Delta table (`pip install 'sqllocks-shape[delta]'`, deltalake) |
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
| `shape.distributions` | `normal` | `loc + scale * N(0,1)` |
| `shape.distributions` | `uniform` | uniform on `[loc, loc + scale)` |
| `shape.distributions` | `exponential` | `loc + scale * Exp(1)` |
| `shape.distributions` | `lognormal` | `loc + scale * exp(s * N(0,1))` |
| `shape.calendars` | `us_federal` | the eleven US federal holidays on their observed days |
| `shape.calendars` | `us_retail` | Black Friday, Cyber Monday, Christmas Eve and the gift holidays |

## Rules

- The entry points are declared in `pyproject.toml` and mirrored in
  `shape.builtins.catalog`; a test fails if they differ. `shape.plugins.registry` registers any
  built-in that discovery did not find, so a source tree with stale metadata still lists them.
- Only `shape.plugins.registry` may import `shape.builtins` (import-linter contract in
  `pyproject.toml`, run by `lint-imports` in `make check` and CI). Library code reaches a built-in
  with `default_host().get(group, name)`.
- Plugins load lazily: listing or running `shape profile` imports none of them.
- Fitters and distributions use scipy's parameter names (`loc`, `scale`, and `s` for lognormal).
- The calendars' lift is neutral (1.0) unless `holiday_lift` is set; `holidays(start, end)`
  returns the rule-derived dates.
- Not built-ins: the Kafka and Event Hubs stream sources are the plugins `shape-kafka` and
  `shape-eventhubs` (`docs/plugins/streaming.md`). The DB-API adapter (`shape.connectors`) needs a
  live connection object, not a URI; it moves into a plugin with the Phase 6 work.
