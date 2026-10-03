# ISS2-sinks (c): live SQL Server / Azure SQL / Fabric SQL sink

## What was built

* `shape.sinks` entry `sqlserver` (`shape_fabric:SqlServerSink`, schemes `mssql`, `sqlserver`) and
  entry `warehouse` (`shape_fabric:WarehouseSink`, the existing adapter, now registered), both in
  `plugins/shape-fabric/pyproject.toml`; exported lazily from `shape_fabric`.
* `SqlServerSink` (`plugins/shape-fabric/src/shape_fabric/sinks.py`) is a thin adapter over the
  P6-07a `SqlDatabaseWriter`: URI `mssql://[user[:pw]@]host[:port]/db?schema=&write_mode=&batch_size=&commit_rows=&user=&driver=&encrypt=&trust_server_certificate=&timeout=`
  plus options (`credential`, `connection`, `connection_string`, `password`, `schema_name`,
  `columns`, `primary_key`, `schema`). Options beat the URI. The connection string is built with
  `shape_sqlserver.sql.build_connection_string`; identifiers, types, INSERTs, write modes and the
  connection (`_tsql.connect`, Entra token via the pluggable `credential`) are all the writer's.
* `SqlDatabaseWriter.write_table` got `commit_rows` (the only change to the writer): commit after
  every N rows while consuming the batch iterable (rounded up to a `batch_size` round trip).
  Default (None) keeps the old single-transaction behaviour. With `commit_rows`, a failure rolls
  back only the open chunk; committed rows and the table stay (a new table is not dropped).

## Decision: where the class lives

shape-fabric depends on shape-sqlserver, not the reverse, and the writers, fakes, recorded tapes
and the existing `Sink` adapters are already in shape-fabric. Registering the sink there needs no
import cycle and no moved code (the smallest correct change). Users of the live SQL sink therefore
install `sqllocks-shape-fabric` (noted in the shape-sqlserver README). Entry-point name `warehouse`
(not `fabric-warehouse`) because the kit requires entry-point name == `name` and the class already
was `warehouse`.

## Secrets

Never on a command line (only options/URI user-info). `password`, URI password and the PWD/token
values inside `connection_string` are masked (`***`) in any exception message; when a driver or
caller echoes one, the exception is re-raised masked with the cause/context chain dropped. A
password in the URI query is rejected without echoing it. Log records (`shape_fabric.sinks`,
DEBUG) carry only the redacted destination. Tests: `test_a_secret_never_appears_in_an_exception_or_a_log_record`
(3 forms), `..._masked_in_a_write_failure_and_in_the_logs`, unknown-option echo, emulator failed login.

## Auth

`credential=` only (get_token(scope) or callable), exactly as the writers. `auth=` in the URI/options
accepts only `sql`; other modes are refused with a message pointing at `credential=`. The `--auth`
modes and env://, kv://, file:// references are lane P6-07b's; nothing duplicated here.

## Files

* `plugins/shape-fabric/src/shape_fabric/{sinks,sqldb,__init__}.py`, `plugins/shape-fabric/pyproject.toml`
* tests: `plugins/shape-fabric/tests/test_sqlserver_sink.py` (19, fake server + kit `check_sink`),
  `plugins/shape-fabric/tests/test_sqlserver_sink_emulator.py` (`emulator` marker, 3 tests)
* `.github/workflows/nightly.yml`: step added to the existing `sqlserver-e2e` job
* docs: `plugins/shape-fabric/README.md`, `docs/plugins/fabric-writers.md` (new section),
  `plugins/shape-sqlserver/README.md` (pointer)

## Checks (this session)

* `ruff check` and `ruff format --check` on plugins/shape-fabric and plugins/shape-sqlserver: clean.
* `pytest plugins/shape-fabric/tests plugins/shape-sqlserver/tests -m "not emulator and not live"`: 350 passed, 46 deselected.
* `python -m shape.plugins.kit sqllocks-shape-fabric` (against a scratch non-editable install of
  this worktree's plugin, because the venv's editable install points at another checkout): OK, 5
  plugins conform (sqlserver and warehouse with shared rules only; `check_sink` itself runs in the
  unit test against the fake server, for both URI schemes).
* `bandit -q -r plugins/shape-fabric/src -ll`: no findings (only pre-existing nosec warnings).
* `scripts/check_user_facing.py`: clean. `scripts/check_plugin_skeletons.py`: OK. No wheel built.
* mypy --strict on the two touched plugin modules: one error, `import-untyped` on the existing
  `shape_sqlserver.sql` import (pre-existing; its ignore comment sits on the inner line). Core was
  not touched, so core mypy was not run.

## Not run

* The emulator e2e test (`test_sqlserver_sink_emulator.py`): no Docker here. It is written and wired
  into the nightly `sqlserver-e2e` job, but has never executed against a real server; the
  `commit_rows` mid-write visibility assertion in particular is unverified on SQL Server.
* Live Fabric/Azure runs (owner O-02).

## Open points for the lead

* `commit_rows` default is off (one transaction per call, the writer's safe default); the emit
  runtime should pass `commit_rows` (and `write_mode=append`) for micro-batch streaming.
* The shared venv's editable install of the plugins points at /home/user/shape, so entry-point
  metadata there does not show the new `shape.sinks` entries until `pip install -e plugins/shape-fabric`
  is re-run from the merged tree.
