# Built-in plugins

Everything core ships is a plugin: it registers through the same entry points as a third-party
plugin and is reached through the [plugin host](host.md). `shape plugins doctor` lists them all
(with their `sqllocks-shape` source).

| Group | Name | What it is |
|---|---|---|
| `shape.sources` | `csv` | CSV and TSV files (`.csv`, `.tsv`, compression suffixes) |
| `shape.sources` | `parquet` | Parquet files |
| `shape.sources` | `jsonl` | JSON Lines files (`.jsonl`, `.ndjson`); `flatten="tables"` splits nested data into related tables (see [SOURCES.md](../SOURCES.md)) |
| `shape.sources` | `ipc` | Arrow IPC files (`.arrow`, `.ipc`, `.feather`) |
| `shape.sources` | `json` | JSON files holding one document or an array of documents, flat, as structs or as related tables (`.json`; see [SOURCES.md](../SOURCES.md)) |
| `shape.sources` | `xml` | XML files: the elements picked by a record path become rows, repeated children become child tables (`.xml`; see [SOURCES.md](../SOURCES.md)) |
| `shape.sources` | `abfss` | CSV, Parquet, JSONL and IPC files in OneLake and ADLS Gen2, by `abfss://` URI (extra `[azure]`; see [cloud-sources.md](cloud-sources.md)) |
| `shape.sources` | `delta` | Delta tables: a local directory, or `delta+abfss://` in OneLake and ADLS Gen2 (extra `[azure]` for cloud tables) |
| `shape.sinks` | `csv` | CSV file |
| `shape.sinks` | `parquet` | Parquet file (snappy, dictionary encoding on; T-17; row groups of up to 1,048,576 rows) |
| `shape.sinks` | `jsonl` | JSON Lines file |
| `shape.sinks` | `ipc` | Arrow IPC file |
| `shape.sinks` | `tsv` | Tab-separated file |
| `shape.sinks` | `sql` | SQL `INSERT` script, with optional DDL (`tsql`, `tsql-fabric-warehouse`, `postgres`, `mysql`) |
| `shape.sinks` | `excel` | Excel workbook (`pip install 'sqllocks-shape[excel]'`, openpyxl) |
| `shape.sinks` | `delta` | Delta table (`pip install 'sqllocks-shape[delta]'`, deltalake); `delta+abfss://` in OneLake and ADLS Gen2, with a commit per micro-batch (`commit_rows`) |
| `shape.sinks` | `abfss` | Files (Parquet, CSV, TSV, JSONL, IPC) in OneLake and ADLS Gen2, dated Hive-style folders, rolling files, atomic publish (extra `[azure]`) |
| `shape.sinks` | `fabric-mirror` | Fabric open mirroring landing zone, local or `abfss://` (`docs/FABRIC_MIRROR.md`) |
| `shape.emitters` | `console` | events as JSON lines on standard output (`shape emit`; [../EMIT.md](../EMIT.md)) |
| `shape.emitters` | `file` | events as JSON lines in one file (`file:///path.jsonl`) |
| `shape.emitters` | `jsonl` | events as JSON lines, one `<table>.jsonl` file per table in a directory (`jsonl:///dir`) |
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
| `shape.strategies` | `locale` | basic locale packs: places, postcodes, reserved-range phone numbers and first names for a country (`docs/LOCALES.md`) |
| `shape.strategies` | `uuid` | version-4 UUID strings |
| `shape.strategies` | `weighted_enum` | a value from a `{value: weight}` mapping (alias sampling) |
| `shape.strategies` | `distribution` | values from a distribution family, clipped by `min`/`max` and rounded to the column's scale |
| `shape.strategies` | `empirical` | inverse-transform sampling of a stored quantile fingerprint |
| `shape.strategies` | `pattern` | strings from a format with `{seq:n}`, `{random:n}` and `{column}` tokens |
| `shape.strategies` | `native` | text from the built-in pools (names, companies, streets, sentences, cities, states, e-mail addresses, phone numbers, SSNs, URIs) |
| `shape.strategies` | `faker` | the `native` providers, and any provider of the optional `faker` package |
| `shape.strategies` | `formula` | a column computed from other columns of the row by a checked expression (no `eval`) |
| `shape.strategies` | `derived` | a column derived from another column of the row, or of a parent row, by `copy` or `add_days` |
| `shape.strategies` | `computed` | a column back-filled from child rows (or the parent) by the compute phase |
| `shape.strategies` | `lookup` | a column of a parent table, by the key in this row |
| `shape.strategies` | `conditional` | one of two values per row, chosen by a condition on another column |
| `shape.strategies` | `correlated` | another column times a random factor, plus or minus a random offset |
| `shape.strategies` | `reference_data` | a value or a weighted name from a named reference dataset |
| `shape.strategies` | `record_sample` | one field of a randomly chosen reference record (the anchor of a record group) |
| `shape.strategies` | `record_field` | another field of the record the table's `record_sample` column chose |
| `shape.strategies` | `hierarchy` | one field of a reference record reached by walking a hierarchy (state, county, city, ZIP) level by level (the anchor of a hierarchy group) |
| `shape.strategies` | `hierarchy_field` | another field of the record the table's `hierarchy` column reached |
| `shape.strategies` | `conditional_table` | a category drawn given another column of the row, from a table of the probability of this value given the source value |
| `shape.strategies` | `bootstrap` | a field of a source row of a reference dataset drawn with replacement (columns of a table share the row), numbers jittered by a fraction of their spread |
| `shape.strategies` | `temporal` | timestamps, uniform or with month, weekday and hour profiles |
| `shape.strategies` | `foreign_key` | keys of a parent table: uniform, Zipf or Pareto (optionally capped per parent), constrained by another column, sampled, or self-referencing |
| `shape.strategies` | `composite_foreign_key` | one parent row of a composite key, all its `ref_columns` |
| `shape.strategies` | `composite_fk_field` | one column of the parent row `composite_foreign_key` drew |
| `shape.strategies` | `first_per_parent` | `True` on the first row of each parent value |
| `shape.strategies` | `self_referencing` | the parent key of a row of the same table, in a level hierarchy |
| `shape.strategies` | `self_ref_field` | a field of that hierarchy (the level) |
| `shape.strategies` | `lifecycle` | a phase label from weighted phases |
| `shape.strategies` | `scd2` | effective date, end date, current flag or version of a type 2 slowly changing dimension |
| `shape.transforms` | `mask` | replaces personal data with synthetic values of the same format: `shape mask` (see [MASK.md](../MASK.md)) |
| `shape.commands` | `ctgan` | `shape ctgan`: fit a CTGAN model on a table and sample rows (needs the `[ctgan]` extra) |
| `shape.transforms` | `star` | `shape transform star`: tables to dimension and fact tables with surrogate keys and a date dimension |
| `shape.transforms` | `cdm` | `shape transform cdm`: tables renamed to their Common Data Model entities |
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
| `shape.chaos` | `schema` | adds, reorders and (past the breaking-change day) drops, renames or retypes columns |
| `shape.chaos` | `value` | nulls, out-of-range numbers, junk text in number columns, encoding damage, future dates, negated amounts |
| `shape.chaos` | `file` | corrupts the bytes of the batch's CSV rendering (truncation, byte damage, partial write, empty, garbage header, swapped delimiter, poison JSON, stray BOM) |
| `shape.chaos` | `referential` | duplicate primary keys (orphan foreign keys need a second table: use `shape.chaos`) |
| `shape.chaos` | `temporal` | late arrivals, swapped timestamps, timezone shifts, daylight-saving boundary values |
| `shape.chaos` | `volume` | a 10x spike, an empty batch or a single row |
| `shape.reports` | `json` | the fidelity report as indented JSON with sorted keys |
| `shape.reports` | `md` | the fidelity report as Markdown |
| `shape.reports` | `html` | the fidelity report as one self-contained HTML page (inline styles, no scripts) |

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
- Behavior modules (`shape.behaviors`) are registered by the plugin `shape-behavior`, not by
  core: `subscription`, `equipment_maintenance` and `healthcare_screening` (a tiny
  example written from scratch) appear in `shape plugins list` once `sqllocks-shape-behavior` is
  installed ([behavior.md](behavior.md)).
- Not built-ins: the Kafka and Event Hubs stream sources are the plugins `shape-kafka` and
  `shape-eventhubs` (`docs/plugins/streaming.md`). The DB-API adapter (`shape.connectors`) needs a
  live connection object, not a URI; it moves into a plugin with the Phase 6 work.
