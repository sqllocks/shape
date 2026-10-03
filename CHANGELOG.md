# Changelog

Shape is in **early access**. Profiling is available now; data generation and
pipeline integration are in progress. See `docs/plans/COMPLETION_PLAN.md`.

## Unreleased

- State and compatibility policy (`docs/specs/STATE_AND_COMPATIBILITY.md`): every persisted file
  declares `format`, an integer `version`, `shape_version` and `min_shape_version` (the old key
  names `format_version`, `schema_version` and `pack_version` are still read, and still written
  beside `version` in the 1.x series); every 1.x release reads every format version ever released;
  a file from a newer release fails naming the minimum Shape release that reads it; unknown
  optional fields are ignored on read and kept on rewrite; `SHAPE_STRICT_FORMATS=1` (or
  `shape.compat.strict_formats()`) is the strict reader; deprecations warn with
  `FormatDeprecationWarning` and are announced here under "Deprecated" (none today). Covers
  `.shape` artifacts, safe profiles, models, generation schemas and specs, scenario packs,
  registry layouts (`layout.json`, `_layout.json`), run manifests, contracts, signatures and
  profile exports. Run manifests, registry logs and receipts write UTC ISO 8601 times with `Z`.
- `shape migrate SRC DST` (and `shape-migrate`, `shape.migrate`): offline migration that never
  rewrites in place, keeps the original, records `migrated_from` and `source_content_id`, has a
  dry run, refuses downgrades, checks its result through the content id, and writes a receipt
  (signed with `--sign-key`; a signed source needs it). Language-neutral test vectors for the
  canonical forms and content ids (`docs/specs/vectors/state_vectors.json`) and a time-capsule
  corpus loaded in CI (`tests/timecapsule`).
- Reconciliation and time-series quality checks (`docs/VERIFY.md`): `shape verify --config` takes
  `reconcile` rules (row counts per table and partition, aggregates per key or group, with
  tolerances, both sides read through the source layer) and `timeseries` rules (gaps in a regular
  series, stuck values, daylight-saving missing and repeated local hours with an explicit time
  zone). The contract accepts the same optional rules and `shape check` takes `--data`;
  `shape.quality.reconcile` and `shape.quality.check_timeseries` are the Python API.
- Fixed (#223): the Eventhouse writer made a KQL table and wrote to it at once, so the engine answered `Entity ... of kind 'Table' was not found` and the write failed with 0 accepted requests; its first request to a table now waits (backoff, `ready_timeout`, default 120 s) for the table to be ready, as the emitter already did. A column the engine refuses (`BadRequest_EntityNameIsNotValid` for `say "hi"`: quoting cannot help, only letters, digits, `_`, space, `.` and `-` are valid in a Kusto column name) is now created under a valid name (other characters become `_`, collisions get a numeric suffix) while the ingestion mapping's JSON path keeps the event's own key. Details in `docs/plans/lane_status/BF-223.md`.
- Fixed (#78): the SQL emulator test helper `rows_of` returned pyodbc `Row` objects, which no longer compare equal to tuples, so `test_awkward_names_cannot_break_out_of_their_quotes` failed in the Nightly `sqlserver-e2e` job. It now returns plain tuples; every assertion is unchanged. The Nightly `fabric-emit-e2e` job failed at install because `shape-fabric` requires `sqllocks-shape-sqlserver==0.9.0`, which the job did not install (workflow diff in `docs/plans/lane_status/BF-78.md`).
- `semantic-model://` source and `shape profile-model` (`sqllocks-shape-fabric`, `docs/plugins/
  cloud-sources.md`, `docs/plugins/fabric-commands.md`). `shape profile semantic-model://<workspace>/
  <model>/<table>` reads a table of a Power BI / Fabric semantic model through `sempy` (options
  `columns`, `batch_rows`, `max_rows` as a DAX `TOPN`, `mode`), with a documented type map to Arrow;
  `shape profile-model WORKSPACE/MODEL -o OUT.shape [--tables] [--max-rows] [--json]` profiles every
  table and records the model's relationships as declared (`evidence: "declared"`, `active`,
  `type`). `sempy` is the plugin extra `semantic-link`, imported only when a semantic-model URI is
  opened. A `many_to_many` relationship in a profile is recorded and no longer becomes a generated
  foreign key or a proposal's profiler-detected key.
- Sinks II, part a (W2-08, #96): `shape generate --to` and `shape emit --to` write to Snowflake
  (`snowflake://user@account/database/schema?warehouse=WH&role=ROLE`: Parquet files `PUT` to the
  table stage, one `COPY INTO` whose loaded row count must equal the staged rows, staged files
  removed also on failure; key-pair or password sign-in) and to Databricks
  (`databricks://host/http_path?catalog=CAT&schema=SCH`: Delta tables in Unity Catalog through a SQL
  warehouse, batched bound multi-row `INSERT`; token or OAuth machine-to-machine sign-in). Both are
  in `plugins/shape-databases` (`shape_databases.SnowflakeSink`, `DatabricksSink`; drivers are the
  plugin extras `[snowflake]` and `[databricks]`, none in core), take the same `write_mode`,
  `schema_name`, `table_prefix`, `commit_rows`, `columns` and `primary_key` as the other database
  sinks, check identifier limits (255 characters, names the database would change) before any
  connection, refuse a secret in the URI and redact credentials in every message. Type maps and
  what a failure leaves behind are in the plugin README.
- Sinks II, part b (W2-08, #96): `shape generate --to synapse://<workspace>.sql.azuresynapse.net/<pool>`
  and `shape emit --to` write to a Synapse dedicated SQL pool (`plugins/shape-fabric`,
  `shape_fabric.SynapseSink` and `SynapseWriter`, `docs/plugins/fabric-writers.md`): the table is
  prepared as the Warehouse writer does, the rows are staged as Parquet under the required
  `staging_path` (an ADLS Gen2 `abfss://` folder), one `COPY INTO ... FILE_TYPE = 'PARQUET'` loads them
  with the managed identity or the signed-in identity (`copy_identity`), the loaded row count must equal
  the staged rows, and the staged files are deleted also when a step fails. Table options
  `distribution` (`ROUND_ROBIN`, `HASH(column)`, `REPLICATE`) and `index` (`CLUSTERED COLUMNSTORE INDEX`,
  `HEAP`). Sign-in is the plugin's `--auth` modes.
- Bridge API 1.2 (`docs/BRIDGE.md`, W7-05): the analysis commands of the open engine reach the JSON
  bridge, and the registry gives drift with severity between two share-safe versions. New commands:
  `report_card` and `report_card_read` (`shape report-card`; the card holds no value of the real
  data), `rules_mutate` (cancellable between mutants), `rules_backtest`, `proposals_contract` and the
  kind `rule` of `proposals_propose` (with `rule` and `stale` in `proposals_list`), `bisect`,
  `bisect_layers` and `timelapse` (the values of a classified column are withheld unless
  `options.include_raw_values`), `registry_diff` and `chaos` (the input check of `shape chaos`
  unchanged: an input not marked as Shape-generated is `policy.unverified_input` before anything is
  written, `allow_real_input` adds the warning `real_input_corrupted`; local output only). New error
  codes `input.contract_conflict` and `policy.unverified_input`. `format_schema` names the formats of
  the new reports (`mutation-plan`, `mutation-report`, `incidents`, `backtest-report`). A request that
  declares `api_version` `1.0` or `1.1` is answered exactly as that version answers it (a 1.2
  command is `usage.unknown_command`, and the 1.2 values of an enumeration are refused as before);
  the 1.1 schemas and vectors are frozen in `docs/bridge/schema/1.1/` and
  `docs/bridge/vectors/1.1/`, and `tests/bridge/test_compat_1_1.py` replays them against the 1.2
  bridge. The job file `shape-bridge-job` stays at version 1.
- `shape registry ROOT diff NAME REF1 REF2` and `shape.registry.drift` (`docs/REGISTRY.md`): for two
  share-safe profiles the result has `drift` with the changes of `shape diff` (`kind`, `severity`,
  `score`) for every metric both safe forms hold, and `not_measured` for the metrics a safe form
  withholds (the range of a column, the outlier rate, ...). Two raw profiles give the result of
  before. `shape.rules.mutation_test` takes the optional `should_stop` and `on_mutant` (what a
  cancellable bridge job needs); a finished run is unchanged.
- Bridge API 1.1 (`docs/BRIDGE.md`, W7-04): the JSON bridge now reaches the workflows the command
  line has. New commands: `proposals_propose`, `proposals_list`, `proposals_decide` (proposals and
  decision files), `project_validate`, `project_show` and the arguments `project` and `source` on
  `profile`, `diff`, `check` and `verify` (the `shape.yml` project file; the bridge never looks for
  one on its own), `design` and `design_from_data` (schema design, read-only), `format_schema` (the
  JSON Schema of each format Shape reads), `profile_show` (a stored `.shape` profile without
  profiling again), `contract_validate` and `safe_scan` (the leak scanner; a finding's message never
  holds the value it found). `verify` gains `source` (the memorization and utility gates, as
  `shape verify --source`) and `details` on every gate (counts, rates, distances and scores, never a
  data value). Request schemas annotate every path argument (`x-path`: `read` or `write`) and
  `domain` (`x-name-or-path`); `index.json` lists each command's `effects` (`reads_files`,
  `writes_files`, `cancels`, `network`), the version that added it and each argument (`since`), and
  every warning code. New error codes: `input.unknown_proposal`, `input.unknown_source`,
  `input.unknown_format`. A request that declares `api_version` `1.0` is answered exactly as 1.0
  answered it (a 1.1 command is `usage.unknown_command`, a 1.1 argument `usage.unknown_argument`,
  and no 1.1 field is added); the 1.0 schemas and vectors are frozen in `docs/bridge/schema/1.0/` and
  `docs/bridge/vectors/1.0/` and `tests/bridge/test_compat_1_0.py` replays them against the 1.1
  bridge. The job file `shape-bridge-job` and the vector files `shape-bridge-vectors` stay at
  version 1.

- Rule mutation testing and backtesting (`docs/RULES_TESTING.md`, W3-01, #99). `shape rules mutate
  DATA CONTRACT.json` plants the corruptions of `shape chaos` one at a time (every applicable one, or a
  `shape-mutation-plan`), profiles each mutant in memory, checks it against the contract (and, with
  `--diff` or a `drift` section, compares it with the unmutated profile) and reports the mutation
  score overall, per corruption kind and per table, the surviving mutants and the rules that killed
  none; `--min-score` gates it. `shape rules backtest REGISTRY NAME CONTRACT.json` replays a contract
  over every committed version of a registry name by business date, with `--window week|month`
  through mergeable profiles, `not measured` for rules the stored form cannot evaluate, `--incidents`
  (caught, missed, alarms outside incidents, `--fail-on-miss`) and `--compare OLD_CONTRACT.json`.
  Python API `shape.rules.mutation_test` and `shape.rules.backtest`; JSON Schemas for the plan, the
  incidents file and both reports (formats `shape-mutation-plan`, `shape-mutation-report`,
  `shape-incidents`, `shape-backtest-report`, version 1).
- Rule suggestion from a profile (W3-02, `docs/PROPOSALS.md#rules`). `shape proposals propose
  PROFILE.shape [PROFILE.shape ...] --kinds rule` (and `shape.proposals.propose_rules`) proposes
  contract v1 rules from what a profile measured: per column `dtype`, `nullable`, `unique`,
  `range`, `pattern`, `allowed_values` (up to 20 values) and `no_placeholder`; per table a
  `row_count` band, `fd` (confidence 0.99 or more) and `reference_pair`. Each has the exact
  contract fragment as its claim, the profile figures as evidence and a documented confidence that
  rises with the support and the number of profiles; a rule is proposed only when it holds on
  every profile given and never from fewer than 30 non-null values. Personal-data columns get
  value-free rules only. `shape proposals contract -d DECISIONS.json -o CONTRACT.json [--merge
  EXISTING.json]` (and `DecisionFile.to_contract`) writes the accepted rules as a contract that
  `shape check` reads. The decision file is version 2 (`decisions-v2.schema.json`) when it holds a
  rule proposal and version 1, byte for byte as before, otherwise; version 2 adds the status
  `stale` for an accepted rule whose evidence no longer holds (`shape proposals list --status
  stale`). `propose` kinds default to `relationship,pii,semantic` as before.
- `shape contract emit CONTRACT.json --to ddl|jsonschema|pandera|gx` (`docs/CONTRACT_EMIT.md`, W5-04):
  a v1 contract as `CREATE TABLE` DDL for the SQL sink's four dialects (Fabric Warehouse constraints
  `NOT ENFORCED`), a draft 2020-12 JSON Schema for a row, a pandera schema as generated Python text,
  or a Great Expectations 1.x suite. Output is byte-identical for the same contract. Every rule a
  target cannot state is listed in `not_expressed` (`--strict` exits 1 and writes nothing; `--json`
  prints the `shape-contract-emit` version 1 result) and kept as metadata (`x-shape`, pandera
  `metadata`, GX `meta`); `contract_from` rebuilds the expressible part (or all of it with
  `use_meta=True`) from a JSON Schema or a suite. New sample `examples/contracts/orders.contract.json`.
  `shape.schemacheck` now understands `maximum`, `pattern` and `not`.
- Plugin allow-list and opt-in signature check (W1-18, #94; `docs/plugins/trust-model.md`).
  `SHAPE_PLUGIN_ALLOWLIST=PATH` (or `plugins.allowlist` in a `shape.yml` passed to `PluginHost`)
  turns on a `shape-plugin-allowlist` v1 file (JSON or YAML, `src/shape/schemas/plugin-allowlist-v1.schema.json`):
  `PluginHost` blocks, before importing, any plugin whose distribution, version (PEP 440
  specifier) or name is not listed (`status: blocked`; the CLI exits 2), checks the installed
  `RECORD` hash and every file hash when `record_sha256` is given, and with `require_signature`
  needs a `shape-plugin.sig` (Ed25519, `shape-plugin-signature` v1) by a trusted key. New:
  `shape plugins sign`, `verify` and `allowlist init`; `shape plugins list` shows
  allowed/blocked and `doctor` lists blocked plugins separately. The trust model now states what
  each first-party plugin can reach (checked against the source by a test). There is still no
  sandbox (D-09).
- `shape bisect`, `shape bisect layers` and `shape timelapse` (`docs/HISTORY.md`, #101): git bisect
  for data. `bisect REGISTRY NAME --good REF --bad REF` binary-searches the committed versions of a
  name (ordered by `business_date`) for the first one whose `shape diff` against the good version
  reports a change (under the `shape.yml` source's thresholds and ignore lists; `--column`,
  `--kind`, or `--contract FILE` to test "the contract fails"), testing at most `ceil(log2(n)) + 2`
  versions, and reports the first bad version, the last good one and the changes between them.
  `--verify-all` tests every version and reports any that flips back; `--coarse week|month`
  bisects over windows merged with `merge_profiles` first. `bisect layers` names the first layer of
  a pipeline (sources of `shape.yml`, `--map` for renamed columns) where a change between two dates
  appears, and where it persists or disappears (exit 0, 1 when none, 2). `timelapse` gives one
  frame per version or merged window (rows, null rate, distinct estimate, quantiles, mean, std, top
  values), marks the change points where `shape diff` reports a change, and writes JSON
  (`shape-timelapse` v1), text sparklines, or one self-contained offline HTML page. Python:
  `shape.history.bisect`, `bisect_layers`, `timelapse`.
- `shape report-card REAL SYNTHETIC` (`docs/REPORT_CARD.md`, #102): one local report card for a
  synthetic dataset. Three sections, each `pass`, `fail` or `not_run` with its reason: fidelity (the
  `shape fidelity` scores and tiers 1 and 2), utility (the `shape verify --source` utility gate) and
  privacy (the memorization gate and a new membership-inference test: with `--holdout`, the AUC of
  telling real rows from held-out real rows by the distance to the closest synthetic row, failing
  above `privacy.max_membership_auc`, default 0.6). Output as `shape-report-card` v1 JSON, Markdown
  or a self-contained HTML page (`-o`, by extension); `--require` turns a skipped section into exit 1;
  exit 2 for unusable input. The verify configuration gains an optional `privacy` section. Python:
  `shape.quality.report_card(...)`. The card holds no value of the data.
- Chaos safety and confirmation for non-local targets (W1-17). **Behaviour change for scripts that
  write to remote targets:** `shape generate --to`, `shape emit`/`stream` (`--sink URI`, `--to`) and
  `shape generate --scale-mode --sink` to a non-local target (a database, OneLake, Event Hubs,
  Kafka; not a path, `file://`, `console` or `localhost`/`127.0.0.1`/`::1`) now exit 2 unless you
  pass `--yes` or set `SHAPE_CONFIRM_REMOTE=1` (or answer `y` at a terminal prompt); `--dry-run`
  needs none (`docs/SINKS.md`). `shape.cli.to.run_to` and the emit target setup take
  `confirm_remote`. `shape generate -o`, `continue`, `time-travel`, `pack run` and `chaos` write
  `_shape_provenance.json` (`format: shape-provenance`, `version: 1`) beside the tables, which are
  unchanged; folder readers skip it and `_SUCCESS`. `shape chaos --input DIR` now refuses table
  files that are not listed there with a matching sha256 (or Parquet with the `shape_synthetic`
  marker) unless `--allow-real-input`, logs `input_provenance` (`verified`/`unverified`) in the
  ground-truth `run` record, and refuses an `-o` that is the input folder or inside it
  (`docs/CHAOS.md`, `docs/THREAT_MODEL.md`).

- VS Code extension and data dictionary (W6-04, #87). `editors/vscode/` (TypeScript, MIT) validates
  and completes `shape.yml` (the bundled `shape-project-v1.schema.json`, through the Red Hat YAML
  extension), offers snippets, shows a `.shape` file as the read-only text of `shape cat` and
  diffs it against Git HEAD (`shape.path` setting; no telemetry, no network). CI builds, tests
  and packages a `.vsix`; marketplace publishing stays a manual step (`docs/RELEASE_POLICY.md`).
  `shape dictionary PROFILE.shape [--project FILE] [--source NAME] --format md|html|json -o OUT`
  writes a data dictionary (`shape-data-dictionary`, version 1) with owners and annotations from
  `shape.yml`; example and top values only with `--examples` and never for columns classified
  CONFIDENTIAL or higher (`docs/DICTIONARY.md`). `jsonschema` joins the `dev` extra.
- File-footer fingerprint, safe-to-share bundle and skew rehearsal (W5-10, #86).
  `shape fingerprint embed|show|verify` and `shape generate --fingerprint` store a signed
  `shape-fingerprint` document (synthetic marker, table and run ids, reproducibility tuple, optional
  Ed25519 signature) as the Parquet footer key `shape.fingerprint` and the Delta table property
  `shape.fingerprint`; `verify` recomputes the table digest (`docs/FINGERPRINT.md`, with a table of
  which local tools keep it, generated by the test run). `shape share-bundle create|verify` runs the
  memorization gate and a top-values check against the source and writes a zip with a signed
  `shape-share-attestation` only if both pass (`docs/SHARE_BUNDLE.md`: evidence that checks passed,
  not a privacy guarantee). `shape skew-rehearsal` generates a schema at scale with the key
  concentration a profile measured and writes a `shape-skew-report` comparing top shares against a
  tolerance (`docs/SCALE.md`). The Parquet and Delta sinks take `fingerprint=True`.
- CSV identifier columns keep their text (#46): an integer column of digits with leading zeros, or of
  one fixed width of five or more digits under an identifier name (`zip`, `npi`, `ndc`, `member_id`),
  is read as text by `shape.profile`, `shape.io` and the commands built on them, so `00000` is not 0.
  `shape profile` has `--string-columns`, `--types FILE.json` and `--infer-types off`
  (`shape.profile(string_columns=, types=, infer_types=)`); an integer column that only looks like an
  identifier is reported as a warning. `learn` and `generate --from` build fixed-width digit text
  (`{digits:N}` pattern token) and keep numeric-looking labels as text.
- Sinks check the URI scheme before writing (#42): a cloud or database URI gives a message that says
  what the sink writes and which sinks handle which scheme, instead of an `OSError` (or a directory
  called `abfss:`). `shape generate -f` accepts every installed `shape.sinks` plugin, `ipc` included (#40).
- `shape stream-profile` keeps the watermark per partition (#41): partitions read at different speeds
  lose no events with the default `--allowed-lateness 0s`. New `--max-partition-skew` (10m) and
  `--partition-idle-timeout` (30 s, followed reads); late events are reported with the
  `--allowed-lateness` that would have kept them.
- `generate --from PROFILE` (and any schema with correlated columns) wrote no files while reporting
  success; the table is now handed to the writer after the correlation pass.
- Stable Python modules (`docs/API_STABILITY.md`, "Stable Python modules", W7-07). `shape.generation.spec_edit`
  (`SpecDocument`, `SpecProblem`, `SpecError`, `validate_text`, `Position`) and
  `shape.generation.spec_schema` (`build_schema`, `published_schema`, `render`, `strategy_names`) are promoted
  to Stable: each has an explicit `__all__`, nothing in them is removed or changed incompatibly in 1.x, and a
  deprecated member keeps working with a `DeprecationWarning` until the next major version. The promise
  covers signatures, dataclass fields, exception bases and four documented behaviours (unchanged specs are
  written back byte for byte, edits keep unknown keys and key order, every `SpecProblem` has a JSON Pointer and
  a line and column for text, `published_schema()` is the shipped file). `python scripts/stable_api_compat.py
  --check` compares the modules with `tests/api/stable_api_baseline.json` (`--write` refreshes it after an
  additive change) and runs in `make check`.

- Fairness slices and training-serving skew (`docs/FAIRNESS_AND_SKEW.md`, W3-11, #104).
  `shape scorecard --slice-by COLUMN[,COLUMN] [--min-slice-rows N] [--max-slice-gap G]
  [--reference REF] [--label COLUMN]` scores every dimension per slice with the gap and worst slice,
  each slice's share of rows (and reference share and ratio), the positive rate and disparity ratio
  of a label (flagged below 0.8, a screening heuristic), and null rates; slices under the minimum are
  pooled and never shown alone, classified slice columns show `slice 1`, `slice 2`, ...
  `--max-slice-gap` exits 1 when exceeded. A scorecard with `slices` is `shape-scorecard` version 2
  (JSON Schemas for versions 1 and 2 in `src/shape/schemas/`); one without is still version 1, byte
  for byte; trends compare slice gaps. New `shape skew TRAIN SERVING [--features] [--label]
  [--slice-by] [--threshold KEY=VALUE] [--project] [-o REPORT.json] [--json]`
  and `shape.quality.skew(...)`: schema, null-rate and PSI skew (the PSI of `shape drift --psi`),
  unseen categories and out-of-range values per feature, ranked by PSI, per slice with `--slice-by`,
  on data or profiles (`shape-skew-report` version 1).

- SQL Server write path II and a DuckDB writer (`docs/SINKS.md`, `docs/plugins/fabric-writers.md`,
  `docs/plugins/fabric-auth.md`, W2-10 / #98). `--auth kerberos --keytab REF --principal NAME@REALM`
  signs in to a SQL Server that takes Windows authentication only: `kinit -k -t` runs into a private
  credential cache (a keytab is `file://PATH`, mode 600, or `kv://VAULT/NAME` holding it
  base64-encoded), the connection uses `Trusted_Connection=yes`, `KRB5CCNAME` is set only while a
  connection opens, and the cache is removed when the command ends, also after an error. A
  generation-schema column may carry `"identity": true` (an optional key of the version-1 document;
  `integer` type and `sequence` strategy only); `shape from-ddl` writes it for `IDENTITY`, `SERIAL`
  and `AUTO_INCREMENT`; the SQL Server writer creates `BIGINT IDENTITY(start, step)` and by default
  keeps the generated keys with `SET IDENTITY_INSERT` (`identity=server` lets the server number
  the rows and is refused when a foreign key references the column); the `sql` script sink emits the
  same for the `tsql` dialect. `--sql-constraints disable` loads into existing tables with
  `NOCHECK CONSTRAINT ALL`, then validates (exit 1 naming each constraint left disabled);
  `truncate` of a referenced table uses `DELETE`. `--write-mode upsert` merges each batch into the
  table on its primary key, so a rerun leaves the same rows and finishes a killed load. A new
  `duckdb` sink (`duckdb:///PATH.duckdb[?schema=main]`, extra `sqllocks-shape[duckdb]`) writes Arrow
  batches into a DuckDB file with `create`, `append`, `truncate`, `replace` and `upsert`. A run no
  longer waits forever when a sink fails after its last batch.

- Planned-change registry (`docs/PLANNED_CHANGES.md`, #90). `shape-changes.yml` (format
  `shape-planned-changes`, version 1, JSON Schema included) lists changes you expect, with a
  window and a reason. `shape diff`, `shape check` and `shape verify` read it (`shape.yml` key
  `changes`, `--changes FILE`, `--no-changes`, `--on DATE`): a planned change inside its window is
  reported as planned and does not fail, a suppressed one is not reported, an expired entry stops
  matching and prints a warning. `shape.diff(..., planned=...)` and the `--json` result add
  `planned`, `planned_not_observed` and `expired`. New `shape changes validate|list|add|ack`.
- Nested and standards sources (`docs/SOURCES.md`, `docs/plugins/healthcare-standards.md`, #83). New
  `json` source (a `.json` file holding one document or an array of documents) and `xml` source, and a
  `flatten` option for `json` and `jsonl`: `flatten="struct"` keeps Arrow structs and lists,
  `flatten="tables"` turns each array into a child table `<parent>__<field>` with `_id`, `_parent_id` and
  `_ordinal` (nested objects become `address.city` columns), and the sources add
  `read_tables`, `read_relationships` and `read_nested` (relationships are
  `shape.generation.schema.Relationship` objects). The XML reader takes a `record` path
  (`//order`), reads attributes and child text into columns and repeated children into child tables, and
  refuses any document that declares entities (`UnsafeXml`). JSON depth is checked across the whole
  document before parsing (`shape.security.jsondepth.check_json_document`) and file size against
  `max_bytes`. Healthcare standards plugin: `x12` source (`x12://FILE`, `.x12`, `.edi`; every segment in
  `x12_segment`, envelope checks with `strict=False` reporting `x12_problems`, and `loops="tables"` for
  one table per 835 loop) and `hl7v2` source (`.hl7`, MLLP framing, escapes decoded; `hl7_segment`, and
  `segments="tables"` for one table per segment id). Structural only: no codes are interpreted.

- Behavior primitives (`docs/plugins/behavior.md`, section 11, #79): `event_sequence`,
  `telemetry_series`, `transaction_stream`, `file_arrival` and `entity_lifecycle` as parameterised,
  deterministic `shape.behaviors` modules (`shape_behavior.primitives` builders, registered by
  `sqllocks-shape-behavior`, passing the plugin conformance kit). `shape behave run NAME --params
  FILE.json` takes the new persisted format `shape-behavior-params` (version 1; a wrong format, a
  newer version, an unknown primitive or an unknown parameter exits 2 naming the key) and `run.json`
  records the parameters used. Same seed and parameters give byte-identical event files, also across
  a checkpoint resume; `telemetry_series` for 10,000 devices at a 1-hour interval for a year runs
  through `--window-years` in bounded memory.

- Univariate depth (`docs/PROFILING_NOTES.md`, #103). Each numeric column of a profile gains, where
  it applies: `distribution_candidates` and `distribution_by_bic` (maximum-likelihood fits of the
  normal, lognormal, exponential, uniform, gamma and Weibull with log-likelihood, AIC, BIC and KS;
  the existing `distribution`, `distribution_params` and `fit_score` are unchanged), `zero_share`
  and `zero_inflation` (observed against a Poisson and a moment-fitted negative binomial),
  `heaping` (multiples of 5, 10, 100 and 1,000 against the share expected from the range and
  resolution), `benford` (first-digit shares, MAD and Nigrini's class) and `tail_index` (the Hill
  estimator with its standard error). Identical under both kernels; computed on all values in
  chunks or on a deterministic sample of at most 50,000 (10,000 for model selection); not part of
  the share-safe profile. `shape diff` reports `zero_inflation_change`, `heaping_change`,
  `benford_change` and `tail_change` (thresholds `zero_share`, `heaping_ratio`,
  `benford_class_steps`, `tail_alpha_drop`, `tail_alpha_max`; each held to sampling noise).
  The `--html` report shows the fields and `shape show` prints them (`Profile.summary()` is
  unchanged). A profile written before this change loads, displays and diffs as before.

- Generator version pinning and a stability promise for pinned fixtures
  (`docs/GENERATION_STABILITY.md`, #92). Every built-in strategy and distribution declares an
  integer `generator_version` (all 1); a plugin may declare it too (optional, missing means 1). A
  generation spec can pin versions in a top-level `generators` map (added to the published JSON
  Schema, kept by `SpecDocument`): `shape generate`, `shape pack run` and `Engine` run a pinned
  name at its pinned version and the rest at the latest. `shape pin SPEC [-o OUT] [--json]` writes
  the current version of everything a spec uses; `shape pin SPEC --check` exits 1 listing the names
  that are not pinned. A pin to a version this Shape does not have exits 2; a pin to a name the
  spec does not use is a warning. The run manifest records `reproducibility.generators` and
  `shape pack replay` regenerates with them. `tests/generation/pinned/` holds a pinned spec per
  built-in strategy and distribution family with its committed dataset id for seed 42, checked in
  both kernel modes in the regular suite.

- OneLake targets in the `abfss` sink (`docs/SINKS.md`, #141). Before the first byte is written, a
  OneLake target is checked once and refused with one line that names the fix: a workspace or item
  name with a space (use the workspace and item IDs), an item that is neither `<name>.<ItemType>` nor
  a GUID, a path outside `<item>/Files/`, an item that does not exist (the storage API cannot create
  Fabric items), a Warehouse (written through T-SQL), and plain files under `Tables/`. ADLS Gen2
  hosts are unchanged and make no extra storage call.

- CLI stability promise, v1.0 definition of done and what is not built (W1-10, #88).
  `docs/CLI_STABILITY.md`: which commands are stable and which experimental (an experimental
  command's `--help` now starts with `(experimental)`), what is stable for a stable command, what
  counts as a breaking change and the deprecation process (a deprecated flag keeps working for at
  least one minor release, prints `shape: warning: --OLD is deprecated and will be removed in X.Y;
  use --NEW` and is listed here). Nothing is deprecated in 1.0. `scripts/cli_surface.py --check`
  (in `make check`) compares the parser with `tests/cli/cli_surface_v1.json`
  (`format: "shape-cli-surface"`, `version: 1`) and fails, naming it, when a stable command, flag
  or choice is removed or renamed; `docs/V1_DONE.md` lists what 1.0 requires for capture or
  profile, diff, gate and replay, each with the tests that prove it, and
  `scripts/check_v1_done.py` confirms those tests exist; `docs/NOT_BUILDING.md` lists decisions
  not to build things. `docs/specs/ONE_ZERO_CONTRACT.md` now states the exit codes the CLI uses
  (`docs/CLI.md`). No command, flag or exit code changed.

- `shape known-answer`, `shape check-answers` and `shape publish-report`, also under `shape fabric`
  (`docs/plugins/fabric-commands.md`, `docs/DRIFT_REPORT.md`). `known-answer DOMAIN|SCHEMA -o DIR`
  writes a dataset (Parquet per table), the semantic model of its schema, `answers.json` (format
  `shape-dax-answers`) with the exact expected value of every measure for the grand total and every
  slice (`Decimal` arithmetic; averages and ratios as exact fractions rounded half-even), and
  `queries.dax`; `--measures` (format `shape-dax-measures`) picks the measures and slices and
  `--plant TABLE.COLUMN=VALUE` sets a decimal column's total exactly inside its generator's bounds.
  `check-answers` compares a DAX client's CSV or JSON export with the answers (exit 0, 1, 2).
  `publish-report PROFILE.shape... | --registry NAME` diffs a profile history with the options of
  `shape diff` and writes `fact_drift_change`, `dim_run`, `dim_column`, `dim_kind` and `dim_date`,
  `drift.bim` with measures, and `report.json` (format `shape-drift-report`). The exporter takes an
  optional `measures` argument that replaces its default measures.

- Starter scenarios, suites, a pytest plugin and database seeding (W5-05, #81). `DriftPlan` has a
  `rename_column` event (`{"kind": "rename_column", "column": "orders.status", "to":
  "order_status", "start": ...}`; the answer key records the dropped and added column as one
  rename). `shape pack list --library` and `shape pack run library:NAME` run ten starter scenarios
  on the retail domain (clean baseline, nulls, duplicates, orphaned foreign keys, late data, and
  add, rename, drop and retype of a column on a schedule), each against a written answer key.
  `shape suite run NAME|FILE` runs the `smoke` and `schema-evolution` suites (or your own) and exits
  1 when a scenario missed its key. The `pytest11` plugin (`pip install 'sqllocks-shape[pytest]'`)
  adds a `shape_dataset` fixture, a `shape_scenario` marker with fixture and `--shape-seed`.
  `shape seed SPEC --target URI` writes the tables in foreign-key order through the `sqlserver`,
  `postgres` and `mysql` sinks, or as ordered INSERT scripts with `sql://DIR`. New persisted
  formats `shape-scenario-expect`, `shape-scenario-library` and `shape-suite` (and
  `shape-scenario`) at version 1. `docs/SCENARIO_LIBRARY.md`, `docs/TESTING_WITH_SHAPE.md`.

- Joint distributions and plausibility (`docs/JOINT.md`, #47). `shape profile` finds placeholder
  values (`00000`, `99999`, `1900-01-01`, `-1`, `N/A`, ...) with their share and evidence, and
  records approximate functional dependencies, two-column keys, association measures for every
  type pair (Pearson, Spearman, Kendall, Cramer's V, Theil's U, correlation ratio, mutual
  information), conditional probability tables and the share of implausible rows, on a bounded
  sample; `reference_pairs` / `--reference-pair` check that columns hold real combinations.
  `shape diff` reports `dependency_broken`, `placeholder_surge`, `implausible_rate_change`,
  `association_shift` and `reference_match_change`, naming the columns and the value. New optional
  contract rules `fd`, `implies`, `reference_pair`, `max_implausible_rate` and `no_placeholder`.
  Generation: hierarchical sampling (`hierarchy` and `hierarchy_field` strategies,
  `HierarchicalSampler`), categorical joint tables from a profile (`conditional_table`), a Chow-Liu
  joint model with per-row plausibility scores and a report of impossible combinations
  (`fit_joint`), and a joint fidelity check (`joint_fidelity`). The joint analysis is on by default
  for a single table and off for a dataset (several tables): `--joint` / `joint=True` turn it on,
  `--no-joint` / `joint=False` off, `SHAPE_PROFILE_JOINT` when the call does not choose.

- `shape demo init|list|run|preflight|cleanup|status|notebook|report` (`docs/DEMO.md`): four scenarios in three
  modes (inference, seeding, streaming); seeding writes to a folder, a Lakehouse, a Warehouse, a SQL database
  or an Eventhouse and records a session that `cleanup` removes exactly; the operations are plain functions
  (`shape.demo`) the JSON bridge calls too. A scenario runs its own domains, a failed run is rolled back,
  `preflight` checks each target, a profile never stores a secret and reports are escaped. Harness:
  `benchmarks/vs_spindle/demo_1to1/` (the fidelity report and the metadata exactly, the generated data by
  T-21, an allow-list with probes, negative controls).
- `shape fabric publish|notebook|deploy-notebook|setup|export-model` and the top-level `shape publish`,
  `shape notebook`, `shape deploy-notebook`, `shape setup-fabric`, `shape export-model`
  (`docs/plugins/fabric-commands.md`): publish a domain to a Lakehouse (landing zone and run manifest),
  Warehouse, SQL Database or Eventhouse; make and deploy a Fabric notebook; make a Fabric Environment;
  export a Power BI semantic model (`.bim`). Names that reach M and DAX are quoted, an accepted (202)
  creation is followed to its end, the workspace listing is read across pages, and the notebook part is
  named for its format. Harness: `benchmarks/vs_spindle/fabric_commands_1to1/` (the `.bim`, the
  notebook, the requests and the landing zone against the baseline, an allow-list with probes,
  negative controls).
- Sinks for OneLake, ADLS Gen2 and databases (`docs/SINKS.md`): `shape generate --to URI` and
  `shape emit/stream --to URI` (repeatable) write to `abfss://` (Parquet, CSV, TSV, JSONL, IPC in
  dated Hive-style folders, rolling files, atomic publish), `delta+abfss://` (a Delta commit per
  micro-batch), `mssql://` (SQL Server, Azure SQL, Fabric Warehouse), `postgresql://` (`COPY`) and
  `mysql://` (`sqllocks-shape-databases`). Local Parquet/CSV/JSONL and Delta sinks take
  `roll_rows`/`roll_seconds`/`commit_rows` so readers see rows while a stream runs. `shape emit`
  gains `--speed 60x` (virtual clock), `--max-rate`, `--duplicate-fraction`, `--poison-fraction`,
  `--answer-key` and the synthetic marker (`--synthetic-header`).
- `shape verify --source DATA`: the memorization gate (exact-match rate and nearest-neighbour
  distance between generated and source rows; fails on a reproduced row in a column classified
  `CONFIDENTIAL` or above, reporting row indices, never values) and the utility gate (train on
  generated data, test on held-out real data, fail below a minimum retention; needs the `[advanced]`
  extra). The verify configuration gains `classifications`, `memorization` and `utility`
  (`docs/VERIFY.md`).
- Run manifest: `format`, `version`, the reproducibility tuple (`reproducibility`) and a
  content-addressed `dataset_id`; `shape pack replay MANIFEST TARGET` regenerates a run and checks
  the id (`docs/REPRODUCIBILITY.md`).
- Air-gap hardening: every test that needs no network runs under a `zero_network` guard that
  fails any connection leaving the machine, and again in CI with networking disabled; pinned,
  hashed lock files for core and each extra (`scripts/offline_lock.py`) are built in CI and
  checked against the declared dependencies; `scripts/check_shipped_data.py` checks that all
  reference data is in the wheel and that nothing downloads at run time (`docs/INSTALL.md`).
- `shape diff`: new `row_count_change` kind (a table with more than twice or fewer than half the
  baseline's rows; thresholds `row_count_ratio_max` and `row_count_ratio_min`). `distribution_change`
  now respects sample size: a changed fitted-family name is reported only when the samples also
  differ by more than sampling noise, so two samples of one distribution no longer trigger it. A
  planted-drift sweep (`tests/diff/test_drift_sweep.py`; fast in CI, full nightly) guards both
  (`docs/DRIFT.md`).
- `shape bridge` (`docs/BRIDGE.md`): a versioned JSON request/response protocol on standard input
  and output (`api_version` `1.0`, request id, `result` and `warnings`, or an `error` with a stable
  code in the groups usage, input, policy, privacy, io, auth and internal). It serves the 17
  commands of the original JSON bridge (the four `demo_*` commands are specified and answer
  `policy.capability_unavailable` until `shape demo` exists) plus `profile`, `diff`, `check`,
  `verify` and `job_status`, `job_cancel`, `job_list`. Long-running commands return a job id
  (`options.async`); job state is a versioned file per job under `--jobs-dir`, so jobs survive a
  restart (a job whose process died reads as `interrupted`); large results come back as a file
  reference with a content id; results never include the raw values of a classified column unless
  `options.include_raw_values` is set. JSON Schemas for every request and result are published in
  `docs/bridge/schema/` (`shape bridge schema --out|--check`) with test vectors in
  `docs/bridge/vectors/`; a compatibility test per command keeps them from changing silently.
  Harness: `benchmarks/vs_spindle/bridge_1to1/`.

- `shape.yml` project file and `shape init` (`docs/PROJECT.md`): named sources, a baseline per
  source (previous run, same weekday, rolling window, month end or a pinned artifact, resolved
  against the registry), drift thresholds and ignore lists per column, gates with `observe` or
  `enforce` modes, and column owners and annotations. Versioned (`format`, integer `version`,
  JSON Schema `shape-project-v1.schema.json`, a frozen version 1 file in the tests).
  `shape profile`, `diff`, `check` and `verify` read it when present and every flag overrides it;
  `shape init` scaffolds `shape.yml`, folders, `.gitattributes` and an example CI workflow;
  `shape project validate` reports every problem with its key path. PyYAML stays an optional
  extra (`yaml`).
- Mergeable profiles (`docs/PROFILE_MERGE.md`): `shape profile --sketches` keeps an optional,
  versioned sketch state beside the profile (the profile and its content id are unchanged), and
  `shape profile merge A.shape B.shape -o OUT.shape` / `shape.profile.merge_profiles` combine
  profiles of partitions or days without re-reading the data: exact statistics exactly,
  cardinality, quantiles and top values within each sketch's documented error. Merged profiles
  carry their inputs' content ids (`Profile.merged_from`).
- `fabric-mirror` sink (`docs/FABRIC_MIRROR.md`): tables as Parquet or CSV files in a Fabric open
  mirroring landing zone, local or `abfss://` OneLake: `__rowMarker__` last (insert, update, delete,
  upsert; `shape continue` delta types map to them), 20-digit sequential file names, publish by
  rename, `_metadata.json` with `keyColumns`. Format rules cited to Microsoft Learn.
- Basic locale packs (`{"strategy": "locale"}`, `docs/LOCALES.md`): places and postcodes for the US, Canada, the UK, Germany, France, India and Australia (GeoNames, CC BY 4.0), phone numbers only in ranges reserved for fiction (US, CA, FR), French first names (INSEE, Licence Ouverte 2.0), and no national identifiers. Names, phone ranges and streets for the other countries are not shipped yet; each provider says so. Sources and licences: `THIRD_PARTY_NOTICES.md`.

- `sqllocks-shape-simulation`, financial simulator: the default window is now the whole span of
  the transactions plus one settlement batch, not 24 hours, so settlements, fraud bursts and
  clearing cover every month of a multi-month table. `duration_hours` still overrides it
  (`docs/plugins/simulation.md`). Recorded as a named, probed difference (`SIM-9`) in the
  parity harness.
- `shape capture` reads every input `shape profile` reads (Parquet, Delta with `--version` and
  `--as-of`, JSONL, globs, folders, `abfss://`) through the same source layer, and `--dataset`
  captures a folder of one file per table. The model has the same content for the same data in any
  format. `shape compatibility` compares models with several tables per table. `docs/CLI.md` and
  `docs/QUICKSTART.md` show the schema-change check on a Parquet feed.
- `--auth cli|msi|spn|sql|device-code|fabric` and credential references (`docs/plugins/fabric-auth.md`) for
  every Fabric writer, source and sink: `shape generate --scale-mode`, `shape emit`, `shape stream`,
  `shape profile` and `shape jobs`. Secrets are `env://`, `file://` or `kv://` references (one shared
  resolver in core, `shape.security.credrefs`; Azure Key Vault comes from the Fabric plugin), never
  command-line values; `file://` refuses a secret file that group or others can read; connection-string
  passwords, keys and tokens are redacted in errors, job records and logs.
- `sqllocks-shape-dbt` (`docs/DBT.md`, issue #44): `shape from-dbt` (a dbt project's `manifest.json`,
  `schema.yml` or `sources.yml` as a generation schema: keys, foreign keys, enums, types with decimal
  precision and scale), `shape to-dbt-tests` (a contract or a profile as `schema.yml` tests for
  `dbt_utils` and `dbt_expectations`, with `--merge` and a documented round trip), `shape dbt-seeds`
  and the `dbt-seeds` sink (CSV seeds with a `seeds:` block of column types, size guidance),
  `shape dbt-report` (one report for a dbt run and a Shape check and drift comparison), a jaffle-shop
  sample project (`examples/dbt_jaffle_shop`) built against DuckDB in CI, and the Fabric pipeline
  `shape_dbt_gate` with the notebook `shape_profile_dbt` (the dbt job activity is `[VERIFY]`).
  Fix: `shape check` reported every `min` and `max` rule of a decimal column as violated.
- `sqllocks-shape-behavior` and the plugin group `shape.behaviors` (`docs/plugins/behavior.md`):
  declarative state-machine modules run by a simulator on a virtual clock (deterministic per seed,
  resumable, vectorized across entities), an event stream as Arrow tables, an extension point for
  domain events, an importer for Generic Module Framework JSON modules that you download, three
  example modules (subscription lifecycle, equipment maintenance, a small healthcare example) and
  `shape behave run|check|import-gmf|examples`. Plugin API v1 gains the `Behavior` protocol,
  `shape.plugins.kit.check_behavior` and `examples/behavior-plugin`.

- `shape generate --scale-mode local_single|local_mp|fabric_spark` and `shape jobs list|status|cancel|resume`
  (`docs/SCALE.md`): the scale router with sinks (memory, Parquet part files, Lakehouse, Warehouse,
  SQL Database, KQL), a durable job store (submit, status, cancel, resume), the `fabric_spark` router
  with its `shape_spark_worker` notebook, a per-chunk-file process option, `ChunkedGenerator` and
  `MultiStoreWriter`. Row counts are exact in every mode. Harness: `benchmarks/vs_spindle/scale_1to1/`
  (T-21 for retail at medium, with negative controls).
- `sqllocks-shape-simulation`: file-drop, SCD2-drop, stream, hybrid and workflow simulators and
  `shape simulate file-drop|scd2|stream|hybrid|workflow` (`docs/SIMULATION_FILES_EVENTS.md`). The
  stream emitter runs on the emit runtime (its pacing, sinks and encoders); the runtime accepts any
  counted, resumable sequence of event blocks (`EventSequence`), and sink selection moved from the
  `shape emit` command to `shape.streaming.emit.open_sink`. Harness:
  `benchmarks/vs_spindle/simulation_1to1/` (mechanism parity and T-21 per simulator, an allow-list
  of the defects fixed, negative controls).
- `shape-simulation` (`docs/plugins/simulation.md`): the pattern simulators (clickstream, financial
  reversals / fraud bursts / settlements, IoT drift / missing readings / alert storms / fleet status,
  operational logs with distributed traces, pulse rideshare telemetry and marts) as Arrow/numpy
  modules, and `shape simulate clickstream|financial|iot|operational-log|pulse`. A run is
  reproducible from its seed (ids come from the seed; the clickstream window starts at
  `start_time`); the financial `transactions` columns follow the configuration; log events that
  start a trace carry its ids; `latency_spike_enabled` and `outage_enabled` are honoured and a run
  without tracing has no trace ids; fractional durations count; IoT alerts do not depend on the
  storm switch; readings per sensor and the domains' own column names are understood. Harness:
  `benchmarks/vs_spindle/simulation_1to1/verify_patterns.py` (parity verifier, negative controls,
  allow-list probes).
- Landing layout (`docs/LANDING.md`): `--path-template`, `--batch-date` and `--table-format` on
  `shape generate`, `shape continue` and `shape chaos` write one file per table per business date
  (`{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}`) with a format per table; the file sinks
  take `path_template` and `batch_date`. Output without the options is unchanged.
- Daily batches (`docs/INCREMENTAL.md`): `shape continue --daily-rows TABLE=N --start-date D
  --batch-date D [--end-date D]` and `shape.generation.batches.BatchGenerator` write one day's new
  rows with stable keys and foreign keys into earlier days, regenerable alone byte for byte.
- `shape chaos` and `shape.chaos.groundtruth` (`docs/CHAOS.md`): named corruptions (`duplicates`,
  `orphan_keys`, `date_shift`, `negative_amounts`, `case_whitespace`, `pii_fill`, `type_change`,
  `null_creep`) with a rate and a seed, and a JSON Lines ground-truth log of every change.
- `shape stream-profile` reads files (issue #33): a path or `file://` URI, a folder, a glob or `-`
  (standard input), as JSON lines (what `shape emit` / `shape stream` write, flat or CloudEvents),
  CSV or Parquet, with the same windows, lateness, event time and checkpoints as a broker;
  `--order event-time` replays a file in time order. `docs/plugins/streaming.md`.
- Delta time travel (issue #36): `shape.profile(path, version=N)` / `as_of=...` and
  `shape profile DIR --version N | --as-of TIMESTAMP` profile an earlier state of a Delta table;
  `as_of` before the first commit is an error, not version 0. The Delta version and commit time
  are recorded as `Profile.provenance` in the `.shape` manifest (not in the profile body).
- Stream API durations (`TumblingProfiler`, `SlidingProfiler`, `SessionProfiler`; issue #34):
  a duration is a `timedelta` or a string with a unit (`"60s"`, `"5m"`). **Breaking:** a bare
  `int` or `float` other than `0` now raises `ValueError` instead of being read as microseconds
  (`60_000` was 60 ms and gave 360 windows for 6 minutes of events instead of 6). Snapshots and
  checkpoints are unchanged and still restore. Spec: `docs/specs/STREAMING_SEMANTICS.md` §2.
- CLI fixes (ISS-cli, `docs/CLI.md`, `docs/REGISTRY.md`): `python -m shape` works; `shape.profile`
  accepts a list of row dicts and `examples/shape_as_code.py` runs (a test runs every example);
  one error policy for the whole CLI: an expected error is `shape: error: MESSAGE` with exit
  code 2 and no traceback (`--debug` or `SHAPE_DEBUG=1` shows it; a bug still raises), and a
  missing file reads the same everywhere. `shape doctor` prints a readable report (Shape version,
  kernel, each package with what needs it; `--json` for scripts; exit 1 when a required package is
  missing). `shape profile` warns on a table with 0 rows (`--fail-on-empty` exits 2). `show` is
  documented as the alias of `inspect`, and `capture` as the model-writing command.
  `shape compatibility` and `shape fidelity` say what they expect instead of failing in a decoder.
- **Registries no longer commit raw values by default.** `shape registry ROOT commit` refuses a raw
  profile (`LocalRegistry.commit(..., allow_raw=False)` raises); `--safe` commits its share-safe
  form, the output of `shape profile safe` commits as it is (leak-scanned first), and `--allow-raw`
  keeps the old behaviour with a warning. `shape profile registry save --safe` stores the safe form
  (`<name>.safe.json`) and a full save says on stderr that it holds real values. `shape registry`
  also gained named arguments, `--meta KEY=VALUE` and `--business-date`, a readable `created` time in
  `log`, `list`, `show`, `diff`, and `checkout -o OUT` (it no longer writes binary to a terminal).
  **Behaviour change:** committing a raw profile to `shape registry` now exits 2.

- Drift (`docs/DRIFT.md`): one engine, `shape.drift.engine`, behind `shape.diff`, `shape.drift.compare`,
  `ShapeMonitor`, `ShapeTimeline.changes` and the stream profiler's windows; `shape.diff` takes
  window profiles. New comparisons with documented defaults: category proportions (`category_shift`),
  pattern, spread, KS distance from the quantiles (`distribution_shift`), min/max (`range_change`),
  string length, outlier rate, boolean true rate (`true_rate_change`; contract rules
  `min_true_rate` / `max_true_rate`), uniqueness, hour of day and day of week. No false drift on
  keys, unique columns of different sizes or date strings. `shape.diff` takes `ignore_columns`,
  `column_thresholds`, `only_columns` and `policy`; `shape diff` takes `--ignore`, `--only`,
  `--policy`, `--threshold` and a flag per global threshold. Change records have a `score`;
  `MonitorEvent.drifts` carry column, kind, severity and score.
- `shape generate-drift` and `shape.generation.drift_plan`: planted drift over time (step, ramp and
  window events on null rates, category weights and new values, distribution parameters, added and
  dropped columns, type changes), one folder of tables per day, each day's schema and an answer key
  (`ground_truth.json`).
- `shape stream` (`docs/EMIT.md`): one table's rows as events in event-time order, on the `shape emit`
  runtime (same options, sinks, formats and delivery guarantees; `--table` required, `-t -s -m`,
  `--rate` 10, `--max-events` is the earliest N events). The flat-event encoder is vectorised
  (Arrow kernels, several threads for large batches) with byte-identical output. Harness:
  `benchmarks/vs_spindle/stream_1to1/` (equivalence verifier, bench, negative control; wired into
  `run.py --only stream`).
- Composites (`docs/GENERATION_ENGINE.md`): `shape composite PRESET|DOMAIN+DOMAIN` generates several domains
  as one dataset, tables prefixed with their domain and linked by shared entities (a person, a location,
  an organisation). Six presets (`enterprise`, `healthcare_system`, `smart_factory`, `digital_commerce`,
  `campus`, `telecom_bundle`; `shape presets --composites`); `generate`, `describe` and `presets` take a
  composite as a target, and `shape.api.generate("enterprise")` returns its tables. `retail` is a packaged
  domain like the other thirteen.
- Live fidelity (`shape emit --live-target`, `docs/EMIT.md`): the emitted events are teed into the
  stream profiler and scored against a target as they go, with the score of `shape fidelity`
  (`score_prepared` is now its single scoring function), drift alerts (`score-low`, `score-drop`,
  `column-low`, `live-error`; stderr, a JSON-lines file and the report), `--live-fail` exit code 1.
  `GlobalProfiler.peek()` reads a running profile. Harness: `benchmarks/live_fidelity/run.py`.
- `shape emit --realtime`: a full garbage collection (cost grows with the host process's heap) and
  a slow checkpoint `fsync` no longer stall the pacing, which showed up as a late batch and a short
  second. Realtime runs freeze the existing objects while pacing and write checkpoints on a writer
  thread; the checkpoint still never passes an undelivered event.
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

### Fixed

- Public Python API documentation (#264): every function `import shape` exports has a docstring
  that matches what it does (`shape.generate` documents each input form, what it returns and the
  arguments it ignores; `timeline`, `view`, `query`, `certify` and `plan` say what they read),
  `view`, `timeline`, `certify` and `plan` declare their return class, `shape.types` passes
  `mypy --strict`, and the new `docs/API.md` lists every exported name with the code's own
  signature, checked by `tests/api/`.
- `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references` no longer depends on test order (#77): it resolves the references in a fresh interpreter and reports which cloud SDK modules got imported, so `azure*` modules left in `sys.modules` by `tests/demo/fabric` cannot fail it.
- Issue #76. The three tests that failed were not numpy-dependent: they called pyarrow in ways older
  releases reject (float16 from Python floats, `if_else` on half-float, hive partition inference on a
  single file). They now build and read their data in a way every supported pyarrow accepts, with
  unchanged assertions. A minimum-versions run showed the declared floors were unusable: pyarrow 14
  cannot be imported with numpy 2, Shape's own code needs `pyarrow.concat_batches` (pyarrow 19), Delta reads need pyarrow 19.0.1 (19.0.0 raises "Repetition level histogram size mismatch"), and
  Rust-vs-numpy bitwise equality holds from numpy 2.3. The core floors are now `numpy>=2.3` and
  `pyarrow>=19.0.1`; `ci/constraints-min.txt` pins them for the minimum-versions check.
