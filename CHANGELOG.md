# Changelog

Shape is in **early access**. Profiling is available now; data generation and
pipeline integration are in progress. See `docs/plans/COMPLETION_PLAN.md`.

## Unreleased

- Emitters (`shape.emitters`): `console`, `file` and `jsonl` in core; `kafka` (`sqllocks-shape-kafka`),
  `eventhubs` (`sqllocks-shape-eventhubs`), `eventstream` and `eventhouse` (`sqllocks-shape-fabric`).
  Every message carries the idempotency key `<table>/<seq>`; delivery is at-least-once with
  backpressure and a checkpoint that never passes an undelivered event. The contract each emitter
  meets is `shape.streaming.emit.contract` (`docs/EMIT.md`).
- `shape pack run|validate|list` and `shape.scenario` (`docs/SCENARIO_PACKS.md`): scenario packs (YAML that
  bundles a domain, a `file_drop`, `stream` or `hybrid` simulation, chaos, validation gates and landing
  paths) and generation specs (GSL, `*.gsl.yaml`), with a run manifest written next to every run.
  Shape ships no packs of its own. Landing paths cannot leave the output directory; unknown gates fail
  instead of passing; chaos in a pack or spec is applied.
- `shape emit`: the emitter runtime (`docs/EMIT.md`). Streams a domain's or schema's rows as
  JSON-lines events with the idempotency key `(_shape_table, _shape_seq)`: realtime pacing
  (`--rate`, `--burst START:DURATION:MULT`) or as fast as possible (the default), `--out-of-order`,
  `--anomaly-fraction` (through the `shape.chaos` mutator protocol), `--max-events`, `--duration`,
  a CloudEvents envelope, backpressure, at-least-once delivery and a checkpoint on shutdown
  (`kill -9` then restart, deduplicated on the key, equals an uninterrupted run).
  `shape.streaming.emit` holds the runtime.
- Chaos engineering (`docs/CHAOS.md`): `shape.chaos` injects deterministic data-quality faults in six
  categories (schema, value, file, referential, temporal, volume) through a seeded `ChaosEngine` and
  as `shape.chaos` plugins, and `shape.chaos.inject_anomalies` corrupts a chosen fraction of the
  rows of a batch without changing its schema (the entry point for `--anomaly-fraction`).
- `shape mask PATH -o DIR` (and the `mask` built-in of `shape.transforms`): replaces personal data
  in CSV or Parquet files with synthetic values of the same format (`docs/MASK.md`). Columns are
  found from their names and from the value patterns of Shape's profile engine; null positions,
  types and every other column are kept, the same value gets the same replacement everywhere so
  keys and the columns that refer to them still match, and no original value is written back.
- Incremental data (`docs/INCREMENTAL.md`): `shape continue DOMAIN --input DIR -o OUT` writes the next
  batch of inserts, updates and soft deletes for existing data (`--inserts`, `--update-fraction`,
  `--delete-fraction`, `--transitions`, `--seed`, `--as-of`; rows tagged `_shape_delta_type` and
  `_shape_delta_timestamp`), and `shape time-travel DOMAIN -o OUT` writes monthly snapshots of a
  dataset that grows, churns and changes with seasonality (`--months`, `--growth-rate`,
  `--churn-rate`, `--update-fraction`, `--seasonality`, `--start-date`). Python:
  `shape.generation.incremental`. Zero rates change nothing, no row is both updated and deleted,
  inserted rows never reference a parent deleted in the same delta, and every snapshot keeps all its
  foreign keys.
- Profile files and the profile registry (`docs/PROFILE_REGISTRY.md`): `shape profile export|import|list|validate` and
  `shape profile registry list|save|delete|tag|diff|reindex|validate` (named, tagged `.shape` profiles under
  `system/table/name`; `shape registry` keeps its meaning).
- Generation start-up: `import shape` now selects Arrow's system memory pool for the whole process (`ARROW_DEFAULT_MEMORY_POOL=system` when `pyarrow` is not loaded yet; otherwise transparent huge pages are switched off for the process on Linux), which removes a 10 to 13 ms stall at the first allocation and about a fifth of the time of a medium run on a virtual machine; `SHAPE_MEMORY_POOL=default` turns it off (`docs/GENERATION_ENGINE.md`). The text providers build their name pools from the file bytes (10 to 20 ms less on the first name column). Generated values are unchanged.
- Fixes (PF-06b): `shape check` / `shape.check` no longer passes a multi-table contract
  (`{"tables": {...}}`) against a single-table profile without testing it: that is now a
  `ContractError` (exit 2), and a table the contract names that the profile lacks is a
  `table_exists` violation (exit 1). A contract may still check only some of a dataset's tables. `shape profile FOLDER --dataset` profiles one table per file (named by the file
  name); a folder whose files do not share their columns is refused without it. Artifact folders
  of the Fabric and Synapse notebooks are named to the microsecond and claimed exclusively
  (`20260930T120000123456Z`, then `_2`, `_3` ...), so two runs in one second no longer overwrite
  a baseline.
- Fidelity tiers 1 to 3 (`docs/FIDELITY_TIERS.md`): `shape fidelity REFERENCE SYNTHETIC --tier 1|2|3`
  (tier 1: Gaussian-mixture fits, conditional profiles, adversarial AUC, temporal profiles and
  periodicity; tier 2: format preservation, string similarity, cardinality and anomaly-rate checks;
  tier 3, experimental: Chow-Liu dependency trees), `shape drift REFERENCE CURRENT [--psi]`, the
  `bootstrap` generation strategy, `shape.privacy.dp.DifferentialPrivacy` (Laplace and Gaussian
  noise from OS entropy unless a seed is passed), and `shape ctgan` with the `[ctgan]` extra. New
  extra `[advanced]` (scikit-learn). Without scikit-learn, tier 1 still runs and names what it left
  out.
- Profile to generation: `shape generate --from X.shape` fits a generation schema to a profile and
  generates it at the profile's row counts (`--rows N` for a one-table profile; `--scale`, `--seed`,
  `--format`, `-o` and `--dry-run` work as for a domain). It rebuilds each column's marginal
  (exact value weights for enums, the fitted family or the quantiles for numbers, types kept),
  missing values, a Gaussian copula over the numeric columns (the profile's Pearson correlations,
  corrected for the marginals' shapes) and the month, weekday and hour profile of timestamps.
  `shape plan X.shape` lists every field of the profile as `preserved`, `approximate` or
  `not_modelled`, with the reason (`--status`, `--rows`); it no longer rejects profiles. Python:
  `shape.generate(profile)` and `shape.plan(profile)`; `shape.generation.fit.fit_schema`.
- `shape learn PATH [-o SCHEMA.json] [--format csv|parquet|jsonl] [--domain NAME]` profiles data
  files (a directory is one table per file) and writes the generation schema that reproduces them
  (`shape.generation.learn.SchemaBuilder`).
- Fixes: `missingness` and `gaussian_copula` (`shape.generation.future`) now apply the missing
  values and the marginal distributions (G5); `plan_reconstruction` checks each item instead of
  reporting everything as preserved (G6).
- Generation: a generator may set `output_type` (`int64`, `float64`, `bool`, `string`); `temporal`
  takes `granularity: "day"` and one weight per hour in `hour_of_day`; the `ipv4`, `postcode` and
  `zip_plus4` providers are built in; the copula's `generation.output.copula_nulls` and
  `copula_threshold` options.
- `shape generate` starts faster and ends sooner: the generation path never imports pandas
  (`shape.generation.arrowkit`), sinks and sources load on first use, Parquet row groups are 256k
  rows (were 1M), one core is left to the writer threads, and two passes run while tables are still
  being made (summed children, leading business rules). The data is unchanged.
- Generation commands: `shape generate DOMAIN|SCHEMA.json` (`--mode 3nf|star`, `--scale`, `--seed`,
  `--format summary|csv|tsv|jsonl|parquet|excel|sql|delta`, `-o DIR`, `--dry-run`, the SQL options
  `--sql-dialect`, `--schema-name`, `--batch-size`, `--sql-ddl`, `--sql-drop`, `--sql-go`, and for
  Delta `--delta-mode`, `--partition-by`), `shape describe`, `shape list` and `shape presets`.
  `shape from-ddl` writes a schema file that `generate` and `describe` read. The retail domain has
  a `star` schema next to `3nf`. `shape generate --rows N` with no target still prints demo rows;
  Python:
  `shape.api.generate("retail", scale="medium", seed=42, mode="star")` returns the generated
  Arrow tables (`result.tables`, `result["order"]`).
- Run logging and metrics for every command: `shape --log-json --log-level LEVEL --metrics FILE
  COMMAND ...` (or `SHAPE_LOG_JSON`, `SHAPE_LOG_LEVEL`, `SHAPE_METRICS`) logs JSON lines to stderr
  and writes the run's metrics (command, exit code, seconds, rows, tables) to FILE. In Python:
  `shape.runlog` (`configure_logging`, `RunMetrics`).
- `shape validate FILE` dispatches on what the file holds: a generation schema goes through the
  schema validator (JSON Schema, then keys, relationships, rules, scale presets and strategy
  keys; exit 1 when invalid), a contract through the contract validation, and any other document
  exits 2. A contract that does not validate now exits 1 (it raised before).
- `shape fidelity REFERENCE SYNTHETIC` (alias `compare`): scores synthetic tables against reference
  tables, per column, per table and overall, on a 0-100 scale, and writes JSON, Markdown or HTML
  reports (`-o`, repeatable; the `shape.reports` plugin group). Pass marks `--min-score`,
  `--min-table-score` and `--min-column-score`; exit 0 on pass, 1 on failure, 2 for bad input. The
  scoring equals the one the generation equivalence standard (plan T-21 clause h) asserts per
  table, to within 1e-9, and a missing column or table scores 0 and an empty reference fails. See
  `docs/FIDELITY.md`. `shape fidelity PROFILE.json DATA.csv` certifies as before.
- Fixes: `certify` scored a column missing from the generated data 1.0 and passed a profile that
  describes no columns (a missing column now scores 0; an empty profile fails); `shape
  certify-shapes` always exited 0 (it now exits 3 when the score is below `--threshold`, default
  0.9).

- Enum rule: a profiled column is an enum (`is_enum`, with every value in `enum_values`) only if,
  besides the existing size limits (fewer than 200 distinct values, or a distinct ratio under 0.30
  with fewer than 50,000), its values repeat: distinct values are at most half of the non-null
  values, and a unique column is never an enum. Before, every column of a table under 200 rows
  was an enum, unique keys, e-mails and free text included, so generation from a profile
  resampled only those exact values. Same rule in both kernels and in the SQL Server plugin's
  sampled profile. `value_counts_ext` (the top 500 values) is unchanged.
- `shape from-ddl FILE`: reads SQL `CREATE TABLE` DDL (SQL Server, PostgreSQL, MySQL, ANSI; inline,
  table-level and `ALTER TABLE` foreign keys) into a generation schema, with smart inference of
  distributions, key patterns, row ratios, seasonality, correlations and business rules
  (`--smart`, the default; `--explain` prints each decision). See `docs/GENERATION_ENGINE.md`.
  Fixes in the import: a column-level `REFERENCES parent(col)` is a foreign key to that column
  (it was ignored, and the guessed key could name a column that does not exist); `VARBINARY(MAX)`,
  `BINARY(MAX)` and the `BLOB` types are binary and left out; names match whole words
  (`discount_pct` is a percentage, `state` is a state and not a status, `model` is not a category,
  a `catalog` table is not a log); `gender CHAR(1)` and other one-character codes get a value set;
  a parent's total is the sum of its child rows (CR-08); a key the DDL does not declare, guessed
  by name (`customer_id`), points at the parent's primary key (it pointed at
  `customer.customer_id`, which usually does not exist) and is not guessed when the parent has no
  single-column key; `CustomerId` and `CustomerID` are read like `customer_id`; generated strings
  never exceed the declared length (`country_code CHAR(2)` got six digits).
- Stream profiling runtime (`shape.streaming`): tumbling, sliding, session and global windows over
  Arrow micro-batches profiled in bounded mode, with watermarks, allowed lateness and a late-data
  policy; windows can be snapshotted and restored exactly. Bounded per-key sketches (LRU, TTL, a hard
  memory cap), a vectorized windowed `Deduplicator`, and a `StreamConsumer` that commits offsets and
  window state in atomic checkpoints, resumes after a restart and reconnects without replaying.
  Fixes: `TumblingWindow` could not be restored, keyed state grew without bound, `deduplicate_ids`
  looped over every row, and a reconnect replayed the batches already delivered. See
  `docs/specs/STREAMING_SEMANTICS.md`.
- `shape verify`: validation gates (schema conformance, nulls, primary keys, foreign keys,
  ranges, temporal consistency, file format, schema drift, distributions), a quarantine for
  failed files and tables, and a Markdown or JSON report. See `docs/VERIFY.md`.
- Relicensed under the MIT license.
- Repository cleaned up for public release: removed internal milestone and
  qualification records.
- README and changelog rewritten to describe the current state.
