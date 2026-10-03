# AUD-dbplugins: audit of the SQL Server, databases, domains and simulation plugins

Branch `lane/AUD-dbplugins`, started from `origin/build/main-plan` at 5c91ea5.

Area: `plugins/shape-sqlserver/**`, `plugins/shape-databases/**`, `plugins/shape-domains/**`,
`plugins/shape-simulation/**`, `docs/plugins/sqlserver.md`, `docs/plugins/simulation.md`,
`docs/SIMULATION_FILES_EVENTS.md`.

## Phase 1: findings

Coverage before the audit (`pytest -m "not emulator and not live" --cov`):

| Plugin | Tests | Coverage |
|---|---|---|
| shape-sqlserver | 150 passed | 95% (`testing.py` 86%) |
| shape-databases | 155 passed | 96% |
| shape-domains | 1 passed | 2% (`retail.py` 0%; it is exercised only by core's tests) |
| shape-simulation | 156 passed | 97% |

Each finding was reproduced in this session. Status values: **fixed** (with its commit),
**lead** (cannot be fixed within the lane's rules), **open** (not fixed, see the reason).

| # | Sev | Issue | Where | Finding | Status |
|---|---|---|---|---|---|
| 1 | high | #338 | `shape_databases/_auth.py` `scrub` | A password with a run of spaces or a tab leaks into `WriteError` messages (whitespace collapsed before the secret is replaced). | see below |
| 2 | high | #339 | `shape_databases/_auth.py` `ENTRA_DB_SCOPE` | The default Entra scope is `https://ossrh-postgresql.azure.com/.default`; Azure Database for PostgreSQL/MySQL tokens are for `https://ossrdbms-aad.database.windows.net/.default`. | lead: pinned by `tests/test_credentials.py:94` |
| 3 | high | #413 | `shape_simulation/financial_patterns.py` | One far-future transaction time (9999-12-31) or a huge `duration_hours` gives an unbounded window: 17M settlement batches, 6.7 GB. | see below |
| 4 | medium | #340 | `shape_sqlserver/sql.py` `COLUMNS_QUERY` | A column of a user-defined alias type is typed by its alias: profiled as `string`, and `mssql://` reads fail (`ArrowTypeError`). | see below |
| 5 | medium | #341 | `shape_sqlserver/catalog.py` `read_catalog` | `--tables` naming unknown tables, or a schema with no tables, writes an empty profile and exits 0. | see below |
| 6 | medium | #342 | `shape_sqlserver/catalog.py` `table_stem` | A table named `factory` or `dimension` loses its leading `fact`/`dim`: no guessed key and a foreign key from its own key to itself. | see below |
| 7 | medium | #343 | `shape_domains/retail.py` | `definition()`, `star_map()` and `cdm_entities()` return the cached dicts; a caller's change alters every later load in the process. | see below |
| 8 | medium | #345 | `shape_simulation/file_drop.py` `_slot_rows` | Backfill and restatement of a table without a time column re-drop `table.slice` rows, not the round-robin rows the partition held. | see below |
| 9 | medium | #417 | `financial_patterns.py` | Without a time column, settlements drop the `n % batches` remainder transactions. | see below |
| 10 | medium | #422 | `_patterns.py` | ISO-8601 text times with `Z` or an offset crash financial, IoT and pulse. | see below |
| 11 | medium | #426 | `clickstream_patterns.py` | `bot_pages_per_session=0` crashes with `IndexError` (exit 1). | see below |
| 12 | medium | #431 | `scd2_file_drops.py` | Tracking the key, duplicate keys, rates of 0 (still one change) and rates outside [0, 1] break the documented chain invariant or settings. | see below |
| 13 | low | #344 | `shape_databases/_base.py` `parse_uri` | A bad URI parameter value does not name the parameter. | see below |
| 14 | low | #433 | pattern configs | Zero divisors give tracebacks (exit 1, docs say 2); type and range errors do not name the setting; `service_count=-1` gives 7 services. | see below |
| 15 | low | #435 | `stream_emit.py` `emit` | `emit(config=...)` sizes the replay window from the constructor's config. | see below |
| 16 | low | #437 | `operational_log_patterns.py` | Error bursts land after the end of a fractional window. | see below |
| 17 | low | #439 | `iot_patterns.py` | Baseline alerts are at least one per device for any window under 16 h (docs: one per eight hours). | see below |
| 18 | low | #441 | `state_machine.py` | `entity_summary` is empty when no entity moves. | see below |

Not filed (judgement calls or behaviour the docs state):
- `profile-db -o` into a missing folder fails after profiling, exit 1 with a clear message
  (`plugin command 'profile-db' failed: FileNotFoundError ...`).
- A declared foreign key to a table outside `--tables` stays in `relationships` (it is declared).
- MySQL sink: a table with more than about 60 `VARCHAR(255)` utf8mb4 columns exceeds MySQL's
  65,535-byte row limit at `CREATE TABLE`. Not reproduced here (no MySQL server in this session;
  the emulator job is nightly).
- Financial: reversal and fraud rows in the combined `transactions` have no event time (their
  time is in `reversed_at`). Design question.
- Operational log: `events_per_hour=0` still gives one event per service per hour; error-burst
  429s are ERROR rows not counted in `error_count`.
- Workflow: a negative dwell is accepted; a zoned `start_time` warns and is stored as naive UTC.
- SCD2 and file drop: an entity named `../x` writes outside `base_path` (names come from the
  user's own schema).
- Clickstream: `bot_fraction=0` with bots enabled still gives one bot session.
