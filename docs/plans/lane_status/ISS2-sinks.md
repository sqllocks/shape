# ISS2-sinks — sinks and streaming (lane/ISS2-sinks)

Issues: #39 (native streaming) and #43 (abfss / OneLake sink). No decision (D-xx, T-xx), gate or
tolerance was changed; no test was skipped, xfailed or disabled; nothing in `$REFENGINE_ROOT` was
touched; D-13: `python scripts/check_user_facing.py` is clean. No PR, no issue comment.

Sub-notes written by the two builders that worked in parallel on their own worktrees (merged here):
`ISS2-sinks-c.md` (live SQL Server sink) and `ISS2-sinks-d.md` (PostgreSQL / MySQL plugin).

## 1. Requirement map

"built" = built in this lane. "existed" = already on the tree (work package in brackets). "gap" = not
built; the reason is in section 4.

### #39, design

| Requirement | Status | Where (command / API) and example |
|---|---|---|
| Rate in events/s, fixed | existed [P5-01] | `shape emit --realtime --rate 500` |
| Bursts / ramps | existed [P5-01] (steps only) | `--burst START:DURATION:MULT`; smooth ramps, daily curves, Poisson inter-arrival: **gap** |
| Wall-clock pacing | existed [P5-01] | `--realtime` |
| Virtual clock with a speed factor | **built** | `shape stream retail -t order --speed 60x` (`VirtualClock`, `parse_speed`, `EmitConfig.speed`) |
| Hard rate cap | **built** | `--max-rate N` (`RateCap`) |
| Event time vs processing time, lateness, out-of-order | existed [P5-01/P5-04] | `--out-of-order F --ooo-window N`; `shape stream` delivers by event time |
| Duplicate injection (at-least-once) | **built** | `--duplicate-fraction F --duplicate-window N` (`FaultSink`) |
| Poison messages | **built** | `--poison-fraction F` (JSON-text sinks: file, console, Kafka, Event Hubs) |
| Drift and scenario plan | existed for batches [ISS-gaps] | `shape generate-drift PLAN.json`, `shape chaos`; a drift plan applied inside a live `emit` stream: **gap** |
| Entities over time (FKs to emitted keys) | partly existed | tables stream in dependency order (parents first); sessions/lifecycles: `shape simulate stream` (simulation plugin) |
| Stream answer key | **built** | `--answer-key FILE` (late, anomaly, duplicate, poison; `read_answer_key`); chaos has its ground-truth log [existed] |
| Deterministic per seed, resumable position | existed [P5-01] | `--seed`, `--checkpoint FILE` |
| Streaming sink protocol (open / write_batch / flush / commit / close) | existed for events (`EventSink.send/flush/close`, emitter contract [P5-02]); **built** for table sinks | `open_table(...)` returning `write_batch`, `flush`, `close`, `abort` (`docs/SINKS.md`) |
| Acks, retries with backoff | existed [P5-01/02] | `--retries`, emitters return after acknowledgement |
| Idempotent writes, partition keys, headers | existed [P5-02] | key `<table>/<seq>`; Event Hubs partition key; Kafka header `shape-table` |
| Serialization: JSON, CloudEvents | existed [P5-01] | `--envelope cloudevents` |
| Serialization: Avro / Protobuf + schema registry | **gap** | |
| Dead-letter handling | **gap** (poison events can be injected; no DLQ routing) | |
| Metrics | existed (run report: events/s, lag, retries, queue depth) | `--json` |
| Micro-batch commits: rolling Parquet / JSONL | **built** | `roll_rows`, `roll_seconds` on the file sinks and `abfss`; `shape stream ... --to abfss://... --roll-seconds 30` |
| Micro-batch commits: Delta per tick | **built** | `commit_rows`, `commit_seconds`; a stream commits at every checkpoint |
| Tier 1: Fabric Eventstream | existed [P5-02] | `--sink eventstream://name` |
| Tier 1: Event Hubs, Kafka | existed [P5-02] | `--sink eventhubs://ns/hub`, `kafka://host:9092/topic` |
| Tier 1: OneLake / ADLS Gen2 (Parquet, Delta, CSV, JSONL) with rolling files and date partitions | **built** (#43) | `--to abfss://...`, `--to delta+abfss://...` |
| Tier 1: SQL Server / Azure SQL / Fabric Warehouse, live | **built** | `--to mssql://host/db` (`sqlserver` sink), `warehouse` sink; writers existed [P6-07a] |
| Tier 1: Eventhouse / Kusto | existed [P5-02, P6-07a] | `--sink eventhouse://...` |
| Tier 2: PostgreSQL (`COPY`) | **built** | `--to postgresql://host/db` |
| Tier 2: MySQL | **built** (issue #39 comment table) | `--to mysql://host/db` |
| Tier 2: Cosmos, Service Bus, Storage Queues, webhooks, S3, Kinesis, Pub/Sub, MQTT, Snowflake, Databricks | **gap** (not in this lane's list) | the sink/emitter contract and kit are the extension point |
| Tier 3: stdout | existed | `--sink console` |
| Tier 3: Python callback / generator API | existed | `EmitRunner(plan, sink)` takes any object with `send/flush/close` |
| Tier 3: file-drop sink | existed [ISS-gaps] | `shape generate --path-template ... --batch-date ...`; now also to `abfss://` |
| One command, fan-out to several targets | **built** | `shape emit ... --to A --to B`; `shape generate ... --to A --to B` |
| `--dry-run`, live progress line | **gap** (`generate --dry-run` exists; `emit` has none) | |
| `--print` a sample | existed | the console sink |
| `--seed`; `--drift-plan` | `--seed` existed; `--drift-plan` on `emit`: **gap** | |
| Symmetric read side (`stream-profile`, baseline, drift alerts) | existed [P3] | `shape stream-profile` |
| Safe defaults: synthetic marker on every message | **built** | `--synthetic-header` (default on): Kafka header `shape-synthetic`, Event Hubs / Eventstream property `shape_synthetic`, Parquet metadata `shape_synthetic`; the event body is unchanged (D-12) |
| Safe defaults: refuse non-local destinations unless confirmed | **gap**: a policy choice for the owner (it would add a prompt or `--yes` to every `--to`) | |
| Safe defaults: no real mail domains / SSNs | existed (separate issues) | |
| Secrets: env, managed identity, `DefaultAzureCredential`, Key Vault, never on the command line | **built** for the new sinks; Key Vault and `--auth` modes come with lane P6-07b | `--sink-config abfss.account_key=env://NAME`; a literal secret is refused; env vars `AZURE_STORAGE_*`, `PGPASSWORD`, ... |
| Diagnostics (`shape doctor`) incl. broker reachability and IPv6 surprise | existed (`shape doctor`); reachability checks **gap** | |
| Notebook friendly, stop conditions | existed | `EmitConfig(max_events=, duration=)`, `request_stop()` |
| Acceptance: emulator test per sink streaming a fixed time (exactly-once keys, order, restart, answer key, reader sees rows while it runs) | **built** for abfss (Azurite, run here), tests with fakes for the others; see section 3 | |

### #43, ADLS Gen2 / OneLake sink

| # | Requirement | Status |
|---|---|---|
| 1 | URIs: `abfss://c@acct.dfs.core.windows.net/p`, the OneLake form, `delta+abfss://` | built: `shape.builtins.sinks.azure` parses with the source's `parse`; `delta+abfss://` in the Delta sink |
| 2 | Auth: reuse `_azure_auth` (token, credential, key, SAS, connection string, `DefaultAzureCredential`, managed identity); secrets from options file / environment / Key Vault, never the command line | built: the sink calls the source's `_filesystem` and Delta's `_storage_options`; adds the standard `AZURE_STORAGE_*` environment variables; `--sink-config` takes only references (`env://`, `file://`, `kv://` when lane P6-07b's resolver is present) and refuses literal secrets |
| 3 | Formats: Parquet, CSV, TSV, JSONL, IPC plus Delta through one filesystem abstraction | built: the same encoders as the local sinks (`_FileSink._open` now takes a path or an open file), over `fsspec` |
| 4 | Layout: path templates, Hive-style partitions, dated names, per-table format | built: `--path-template`, `--batch-date`, `--table-format`; new tokens `{part}`, `{hhmmss}` |
| 5 | Atomic publish | built: temporary blob under `_shape_tmp/`, rename on close; optional `_SUCCESS` (`--manifest`); a test polls while files roll and every visible file is whole |
| 6 | Streaming: roll files every N rows / seconds; DFS append+flush optional; Delta per micro-batch | built: rolling and Delta commits. DFS `append`/`flush` was **not** built (the issue says "may"; rolling gives the visibility) |
| 7 | Overwrite, append, fail modes; retries with backoff; clear authorization and missing-container errors | built; verified against Azurite for 403 and missing container |
| 8 | Tests: Azurite compose test; nightly/manual real-account test (rename, DFS append, Delta commits); OneLake from a Fabric workspace | Azurite: built and run here (6 tests). Real account / OneLake: `tests/builtins/test_abfss_sink_live.py` written, job `abfss-live`, **not run** (no secrets; the secret names are my choice, the owner creates them). DFS append: not built |
| Acceptance | `--to abfss://landing@acct.dfs.core.windows.net/` with Parquet and CSV in different tables, dated files appear while it runs, a reader never sees a partial file | covered by `test_generate_and_emit_to_the_container_with_the_secret_in_the_environment` (Azurite) and the polling test |

## 2. What was built

Core (`sqllocks-shape`, nothing heavy at import):

| file | what |
|---|---|
| `src/shape/io/store.py` | `Store` (`LocalStore`, `FsspecStore`): files below a root; temporary name then publish; spool + retries with doubling pause for transient errors; error translation hook |
| `src/shape/builtins/sinks/_roll.py` | `RollingTableWriter`: rolling by rows (exact) or seconds, flush = complete the file, modes overwrite / append / fail, `_SUCCESS` |
| `src/shape/builtins/sinks/azure.py` | `AbfssSink` (`shape.sinks: abfss`, schemes `abfss`, `abfs`): one sink for every file format, dated layout, rolling, atomic publish, clear errors |
| `src/shape/builtins/sinks/delta.py` | `delta+abfss://`, `DeltaTableWriter` (commit per micro-batch / checkpoint), `open_table` |
| `src/shape/builtins/sinks/files.py` | `roll_rows` / `roll_seconds` on the local CSV, TSV, JSONL, Parquet, IPC sinks; `open_table`; writers take an open file |
| `src/shape/io/targets.py`, `src/shape/cli/to.py` | routing of a URI to its sink (on top of `shape.plugins.schemes`), `--to`, `--sink-config`, credential references |
| `src/shape/generation/output.py` | `write_targets`: generate once, write each table to every target (fan-out) |
| `src/shape/streaming/emit/tables.py` | `TableEventSink`: a stream into any table sink; checkpoint = flush |
| `src/shape/streaming/emit/faults.py` | `FaultSink` (duplicates, poison), `AnswerKey`, `FanOutSink` |
| `src/shape/streaming/emit/rate.py`, `runtime.py` | `VirtualClock`, `RateCap`, `EmitConfig.speed`, `.max_rate` |
| `src/shape/streaming/emit/source.py`, `anomaly.py`, `formats.py` | answer-key hooks for late and anomalous events; the poison marker column and cut-off body |
| `src/shape/cli/emit.py`, `stream.py`, `generation.py` | the new options |

Plugins: `sqlserver` and `warehouse` sinks in `shape-fabric` (`ISS2-sinks-c.md`); new plugin
`plugins/shape-databases` with `postgres` and `mysql` (`ISS2-sinks-d.md`); `shape-kafka` and
`shape-eventhubs` mark messages synthetic and accept poison. CI: `ci/emulators/docker-compose.yml`
(postgres, mysql), nightly jobs `databases-e2e`, `abfss-live`, the Azurite job runs the new file, and
`database-plugins` in `ci.yml`. Docs: `docs/SINKS.md` (new), `docs/EMIT.md`, `docs/plugins/builtins.md`,
`CHANGELOG.md`.

## 3. Decisions inside the lane (§0.3; none touches §2)

* **Where the live SQL sink lives:** `shape-fabric` (it owns the writers, fakes and tapes and already
  depends on `shape-sqlserver`; the reverse import would be a cycle). Users install
  `sqllocks-shape-fabric`.
* **Rename vs append:** the atomic publish is "temporary name, then `fs.mv`", not DFS append. On
  Azurite and flat blob accounts the move is a server-side copy and a delete (the final blob is still
  whole); on a hierarchical namespace it is a rename. Not exercised against a real account.
* **Checkpoint = flush = roll/commit** for table targets, so a checkpoint never claims more than the
  destination holds. The default `--checkpoint-seconds` is 30 (not 1) when a table target is used, so
  files are not tiny; set it to the freshness readers need.
* **Poison** is carried by a boolean marker column the JSON encoders understand; it is refused for
  sinks that store typed values. **Duplicates and poison are delivery faults** (a wrapper around the
  sink), chosen from the seed and the event key, so they do not change offsets or checkpoints.
* **Synthetic marker** is a transport header / property / file metadata, not a body field: adding a
  body field would change the D-12 event format.
* **ISS2-bugs (#42):** it is stacked on the ISS-profile lane and conflicts with this tree in profiling
  files, so I took only its sink commit (`d13aa82`, cherry-picked with `-x`) and built on its
  `shape.plugins.schemes` (`uri_scheme`, `redact`, `sinks_by_scheme`, `require_scheme`). Reconciliations:
  `sinks_by_scheme` lists `file` first (a test pins that), a scheme that a sink now handles says so
  ("... provided by the abfss sink"), one assertion in their test was widened for that, the landing
  helper has its own extension table again (`EXTENSIONS` left `output.py` in that commit), and
  `output._target` keeps the `contained()` path check. When the lead merges the whole ISS2-bugs lane
  the same commit arrives twice; the resolution is "keep this tree's files".
* **P6-07b:** its credential references (`shape.security.credrefs`) are not on this tree. `--sink-config`
  uses them through `importlib` when the module exists, and supports `env://` alone otherwise; the
  databases plugin has a small local `env://` / `file://` resolver with the same semantics (swap in
  one function). Merge `origin/lane/P6-07b` and replace both.

## 4. Gaps and things for the owner

* Not built: Avro / Protobuf with a schema registry, dead-letter routing, Poisson inter-arrival, daily
  curves and smooth ramps, `--drift-plan` inside a live stream, `emit --dry-run` and a live progress line,
  broker reachability checks in `shape doctor`, the Tier 2 sinks other than PostgreSQL and MySQL, Snowflake,
  Databricks, Cosmos DB, DFS `append`/`flush`.
* **Owner decision:** "refuse non-local destinations unless confirmed" (issue #39, safe defaults) is
  not implemented: it adds a confirmation step to every cloud target. Say if it should be a `--yes`
  flag or an environment switch.
* **Owner action (O-02):** the live job needs `SHAPE_LIVE_ABFSS_URI` and `SHAPE_LIVE_DELTA_URI` (and the
  existing Fabric sign-in secrets); the database jobs need nothing but Docker.
* Not run here (no Docker): the compose services `postgres` and `mysql`, the SQL Server sink emulator
  test. The PostgreSQL / MySQL emulator tests were run by the builder against locally installed
  PostgreSQL 16 and MariaDB 10.11, not the images. Azurite was run here from npm (`azurite` 3.x) on
  127.0.0.1:10000.

## 5. Checks run in this session

(results below were produced on the final tree; see the commit log for the hash)

| check | result |
|---|---|
| `ruff check` and `ruff format --check` (src tests plugins benchmarks/vs_refengine) | clean / 898 files formatted |
| `mypy` (strict, 355 files) | no issues |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | exit 0 |
| `lint-imports` | 1 contract kept, 0 broken |
| `python scripts/check_user_facing.py` | clean (the databases plugin wheel was also checked with `--wheel` by its builder) |
| `bandit -q -r src -ll` and on each of the 7 plugin `src` dirs | exit 0 (existing nosec warnings only) |
| START: median of 10 `shape --version` | 84 ms (limit 300 ms) |
| `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, `SHAPE_KERNEL=rust` | 5322 passed, 52 deselected |
| the same, `SHAPE_KERNEL=python` | 5322 passed, 55 deselected |
| plugin tests (fabric, kafka, eventhubs, databases, sqlserver; not emulator, not live) | 593 passed, 88 deselected |
| `python -m shape.plugins.kit` for fabric, kafka, eventhubs, databases, sqlserver | all exit 0 |
| `pytest -m heavy tests/streaming` | 2 passed |
| `python scripts/check_plugin_skeletons.py` | OK, 7 distributions |
| Azurite emulator test (`tests/builtins/test_abfss_sink_azurite.py`) against Azurite 3 started from npm | 6 passed |

Not run: Docker-based emulators (postgres, mysql, SQL Server) and every `live` test (no Docker, no
secrets). The emulator tests of the databases plugin passed against locally installed PostgreSQL 16
and MariaDB 10.11 in the builder's session (see `ISS2-sinks-d.md`). The final suite runs were made
one commit before the last line-length edit in `src/shape/plugins/schemes.py` (a string split, no
behavior change); ruff, mypy and `tests/generation/test_sink_formats_and_schemes.py` were re-run after it.


## Round 2 (2026-10-03)

Scope: merge `origin/build/main-plan` (ce8fe11, with P6-07b) and use P6-07b's credential references
and `--auth` modes instead of the interim handling; record decision (3) as deferred.

### Merge

Merge commit (no rebase, no force-push) of `origin/build/main-plan` into `lane/ISS2-sinks`.
Conflicts and how they were resolved (both sides' behaviour kept):

| file | resolution |
|---|---|
| `docs/plans/COMPLETION_PLAN.md` | build/main-plan's version (lanes never edit §11 or §2.3) |
| `CHANGELOG.md`, `plugins/shape-fabric/README.md` | both entries kept; the README no longer says the `--auth` modes "will apply" (they do) |
| `src/shape/cli/emit.py` | `--sink` default `None` with repeatable `--to` (this lane) plus the `--auth` argument group; per target, an event sink gets `_auth_options` (P6-07b) and a table sink gets its sign-in through `TargetOptions` |
| `src/shape/generation/output.py` | this lane's `_paths`/`_check_destination` (scheme check, lazy sinks) and P6-07b-era `_write_workbook` (EXCEL) both kept; the older `_paths` signature dropped |
| `src/shape/streaming/emit/sinks.py` | `open_sink` keeps `synthetic`, `table_options` and the table-sink route, and passes `**options` (sign-in) to emitter sinks |

### Interim credential handling replaced

* `shape.cli.to`: the `importlib` fallback (`env://` only without P6-07b) is gone; `--sink-config`
  references go through `shape.security.credrefs.resolve_reference` (`env://`, `file://` with the
  private-file check, `kv://`). A literal secret is still refused.
* `shape-databases` `_auth._resolve_reference`: the local `env://`/`file://` resolver is now a thin
  wrapper over `credrefs` (errors keep the plugin's `CredentialError`); `kv://` now works there too.
* `--auth cli|msi|spn|sql|device-code|fabric`, `--tenant-id`, `--client-id`, `--client-secret REF`,
  `--sql-user`, `--sql-password REF`, `--connection-string` now apply to `shape generate --to` and
  `shape emit/stream --to` for `abfss://`, `delta+abfss://`, `mssql://` and `warehouse://`
  (`cli.to.sign_in_options`, `cli.auth.writer_options`): the credential goes to the sink as
  `credential=`; `--auth sql` needs `--connection-string` and a SQL sink. PostgreSQL and MySQL
  refuse `--auth` with a message (they sign in with a password reference or their environment
  variables). Docs: `docs/SINKS.md` "Secrets".
* Tests: `tests/cli/test_to_sign_in.py` (10).

### Fixes found while verifying the merge (all kept minimal)

* `target_options` looked up a table sink for every remote target, which broke `eventhouse://` and
  `eventstream://` emitters (3 plugin tests in `shape-fabric`, 1 in `shape-kafka`); it now asks only
  for schemes a table sink handles. The refusal message keeps the wording P6-07b's test pins.
* `fsspec` added to the `dev` extra: the lane's tests import it at module level, and nothing CI
  installs provided it (it only worked where `adlfs` was installed).
* `test_the_abfss_sink_authenticates_like_the_source` imported the real `adlfs`; with `adlfs`
  installed, the Azure SDK stayed in `sys.modules` and P6-07b's
  `test_core_imports_no_cloud_sdk_to_resolve_references` failed later in the same run. The test now
  puts a stand-in `adlfs` module in `sys.modules`. Its assertions are unchanged.

### Decision (3), deferred

"Refuse non-local destinations unless confirmed" (`--yes`, or `SHAPE_CONFIRM_REMOTE=1` for notebooks
and pipelines) is decided (§2.3, 2026-10-03) and is built **after the 2026-10-07 talk**. Not built
here. Section 4's "owner decision" on it is answered by that decision.

### Checks (round 2)

| check | result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_refengine) | clean / 927 files formatted |
| `mypy` | no issues in 362 source files |
| `python scripts/check_user_facing.py` | clean |
| `pytest -m "not emulator and not live" --ignore=tests/demo/fabric`, `SHAPE_KERNEL=rust` (venv as CI's main job: `.[dev,streaming,advanced]` + shape-domains) | 5655 passed, 1 failed (the abfss auth test above, fixed after this run) |
| the same, `SHAPE_KERNEL=python` | 5655 passed, 1 failed (the same test, same fix) |
| after the fix: `tests/builtins/test_abfss_sink.py`, `tests/security`, `tests/cli/test_to_sign_in.py`, `test_generate_to.py`, `test_auth_cli.py`, `tests/streaming/emit/test_table_sink.py` | 240 passed |
| plugin tests (not emulator, not live): fabric / databases / kafka / eventhubs / sqlserver | 311 / 155 / 46 / 42 / 150 passed |
| `benchmarks/vs_refengine/stream_1to1/verify.py --scale small` | VERDICT PASS |

The two full runs were not repeated after the one-test fix: the fix touches one test file, and the
tests that could be affected by it were re-run (row 4). Not run: Docker emulators (postgres, mysql,
SQL Server, Azurite) and every `live` test (no Docker, no secrets); `tests/demo/fabric` (ignored as
in round 1). RefEngine was set up per §1.2 for the harness; `$REFENGINE_ROOT` was not modified.
