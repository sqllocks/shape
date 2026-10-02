# ISS2-sinks (d): live PostgreSQL and MySQL sinks

Status: built; every check below was run in this session on the final tree. No gate, tolerance or
decision changed; no test skipped, xfailed or disabled. No push. The pinned baseline checkout was
not touched. D-13: nothing user-facing names the baseline.

## What was built

New plugin distribution `plugins/shape-databases` (`sqllocks-shape-databases`, package
`shape_databases`; the skeleton script's `shape-<short>` rule fits the name, so no rename), version
0.9.0 in lockstep, registering in `shape.sinks`:

* `postgres` (`postgresql://`, `postgres://`): `COPY "s"."t" (cols) FROM STDIN` through psycopg 3
  (`cursor.copy` + `write_row`, text format; values are COPY data, never statement text). Rows are
  streamed batch by batch (bounded memory, tested). Whole table is one transaction (PostgreSQL DDL
  is transactional); `commit_rows=N` = one COPY and one commit per N rows.
* `mysql` (`mysql://`): PyMySQL (pure Python, no libmysqlclient), `executemany` of
  `INSERT ... VALUES (%s, ...)` (PyMySQL batches it into multi-row INSERTs), `batch_size` rows per
  call, `commit_rows` commits every N rows. `LOAD DATA LOCAL INFILE` is not used (documented).
  `local_infile=False` is passed to the connection.

| file | what |
|---|---|
| `plugins/shape-databases/src/shape_databases/_base.py` | URI parsing (query allow-list, password in URI refused), the shared write flow, modes, error handling |
| `.../_sql.py` | identifier checks, `CREATE TABLE`, value conversion; reuses core's `shape.builtins.sinks.sql` `_quote`, `_column_type`, `_logical_type` (no reimplementation) |
| `.../_auth.py` | password sources, `Secret`, `scrub` |
| `.../postgres.py`, `mysql.py` | the two sinks |
| `.../errors.py` | `WriteError` (with `rows_committed`), `CredentialError` |
| `.../testing.py` | `FakeServer` / `FakeConnection`: in-memory server with a transaction model (transactional DDL for postgres, implicit commit for mysql), records every statement, COPY row and INSERT parameter list; failure injection; `sample_batch` |
| `plugins/shape-databases/tests/` | 155 contract tests + 11 emulator tests |
| `scripts/check_plugin_skeletons.py`, `tests/plugins/test_plugin_kit_install.py` | `databases` added to the expected distributions (8 now; wheel count 8) |
| `pyproject.toml` (root) | extras `postgres`, `mysql`, `databases` pin `sqllocks-shape-databases[...]==0.9.0` |
| `ci/emulators/docker-compose.yml` | `postgres` (16) and `mysql` (8.4) services, healthchecks, ports 5432/3306 |
| `.github/workflows/nightly.yml` | `databases-e2e` job modelled on `sqlserver-e2e` |
| `.github/workflows/ci.yml` | `database-plugins` job: contract tests (ubuntu + windows) and the kit run, no driver installed |

## Decisions (documented, for the lead to veto)

1. **Drivers are extras of the plugin, not dependencies**: base dependencies are only
   `sqllocks-shape==0.9.0` (skeleton rule); `[postgres]` = `psycopg[binary]>=3.1,<4`, `[mysql]` =
   `pymysql>=1.1,<2`. Neither is in any core dependency list (T-07; tested). Without its driver a
   sink raises a `ShapeError` with the pip command. The sqlserver plugin puts pyodbc in
   `dependencies`; this differs on purpose because the two sinks are independent.
2. **PyMySQL** over mysql-connector: pure Python, wheel-only, no C client library, simple bound
   `executemany` batching.
3. **Credential object shape mirrors `shape_fabric._auth` (lane P6-07b)**: an object with
   `get_token(scope)` -> `.token`, or a function `scope -> token`; the token is the password
   (Entra database logins; default scope `https://ossrh-postgresql.azure.com/.default`,
   `token_scope` overrides). References `env://` and `file://` (file must be `chmod 600`, same rule
   and wording as `shape.security.credrefs` on lane P6-07b / ISS-sign). **`shape.security.credrefs`
   is not in this branch's base**, so `_auth.py` carries a small local `env://`/`file://`
   resolver with the same semantics. After ISS-sign merges, it can be swapped for the core
   resolver (one function, `_resolve_reference`); `kv://` is not supported here (a Key Vault
   reference needs the Fabric plugin's resolver, and this plugin must not import shape_fabric).
4. **Password never in the URI**: a URI with a password is refused (message without the value).
   Connection parameters go to the driver as keyword arguments, never as a connection string.
5. **No `from exc` chain**: driver exceptions can echo what they were given, so the `WriteError`
   is built from a scrubbed message and raised outside the `except` block (no `__cause__` and no
   `__context__`). The cost is that the driver traceback is not attached; the exception type and
   scrubbed message are in the text.
6. **Failure semantics** (documented in the module docstrings and README): postgres without
   `commit_rows` is all-or-nothing including the table; mysql without `commit_rows` rolls rows back
   and drops a table this call created (DDL cannot be rolled back; `replace`/`truncate` have
   already destroyed the old rows); with `commit_rows`, committed rows stay and
   `WriteError.rows_committed` reports them.
7. **Table creation**: string columns sized to the first batch (min 255; > 4000 chars, or nested,
   becomes TEXT/LONGTEXT); nested values are written as JSON text; tz-aware timestamps stored as
   UTC; MySQL NaN/Infinity become NULL (as the script dialect does). Unknown extra options passed
   to `write()` are ignored (the core output layer passes generic options).
8. **Identifier checks beyond quoting**: NUL refused; PostgreSQL > 63 bytes and MySQL > 64
   characters refused (the database would truncate or reject); MySQL trailing space and `%` refused
   (`%` would be read as a placeholder by PyMySQL).
9. `schema_name` for MySQL is the database (created `IF NOT EXISTS` when this call creates the
   table).

## Checks run (this session, worktree tree)

* `pytest plugins/shape-databases/tests -q` with real servers up: **166 passed** (155 contract
  + 11 emulator-marked). The 11 emulator-marked tests ran against **PostgreSQL 16.14** (local
  install, `initdb`, port 5432) and **MariaDB 10.11.14** (apt, port 3306, `PyMySQL`), because no
  Docker is available here. They have **not** been run against the Docker images the nightly job
  uses (`postgres:16`, `mysql:8.4`); MariaDB is not MySQL 8.4 (differences are possible in
  information_schema, error text and TLS defaults). The compose file, the nightly job and the CI
  job were only syntax-checked (`yaml.safe_load`); `docker compose config` was not run.
* `pytest plugins/shape-databases/tests -m "not emulator"`: 155 passed (what CI runs on every PR).
* `tests/plugins/test_plugin_kit_install.py`: 7 passed (8 distributions, 8 wheels).
* `ruff check` and `ruff format --check` on `plugins/shape-databases`, `scripts/check_plugin_skeletons.py`,
  `tests/plugins/test_plugin_kit_install.py`: clean. (`ruff format --check scripts` reports two
  other files, `check_requirements.py` and `check_secrets.py`, that were already unformatted; not touched.)
* `mypy --strict -p shape_databases` (MYPYPATH of core src + plugin src): no error in the plugin
  (core's own non-strict modules print errors; they are on the ratchet list).
* `python -m shape.plugins.kit sqllocks-shape-databases`: OK, 2 plugins conform (shared rules); and
  `kit.check_sink` against each sink with the fake (with `read_back`) in `test_conformance.py`.
* `bandit -q -r plugins/shape-databases/src -ll`: exit 0 (two reviewed B608 `nosec`: the MySQL
  INSERT text holds only checked, quoted identifiers; the regex in `testing.py` reads statements).
* `python scripts/check_user_facing.py`: clean; `--wheel` on the built
  `sqllocks_shape_databases-0.9.0-py3-none-any.whl`: clean.
* `python scripts/check_plugin_skeletons.py` (and `--build ... --no-isolation`): OK, 8
  distributions, 8 pure wheels.

## Secret-leak test

`tests/test_credentials.py`: a driver (fake) that echoes the password in its error message in
four shapes (`password=...`, a URI, `pwd=...`, bare) for both sinks, and a connect error that
echoes it. Asserts the secret is in no exception message (the whole `__cause__`/`__context__`
chain and `args`), no `repr`, no formatted traceback, no log record at DEBUG, no stdout/stderr,
and not in any recorded server statement. Also: `Secret` repr, a failing credential function, a
password in the URI, and the `file://` permission rule.

## Not done / open

* Nightly job, compose services and the CI job are untested in their real environment (no Docker,
  no GitHub Actions here).
* `shape generate` / the core output layer does not yet route `postgresql://` or `mysql://`
  destinations to these sinks (that is core CLI/output code outside this lane's scope); the sinks
  are reachable through the plugin host (`default_host().get("shape.sinks", "postgres")`) once the
  distribution is installed.
* `kv://` references are not accepted (see decision 3).
* No docs page under `docs/` beyond the plugin README.
