# Proposal: direct profiling sources for PostgreSQL, MySQL, Snowflake and Databricks

Status: **proposal** (2026-10-12, Eastern Time). Not a plan edit: nothing in
`docs/plans/COMPLETION_PLAN.md` (decisions, gates, tolerances, tracker) changes. The work
package IDs `PS-01` to `PS-08` are placeholders until the lead assigns real IDs and tracker rows.

Context: the owner approved premium change request CR-0087 (private repository): profile the
data in one system and generate a synthetic replica in another. The public engine's part is
(1) direct whole-database profiling sources for PostgreSQL, MySQL, Snowflake and Databricks
(Unity Catalog) with the same output as `shape profile-db`, and (2) a documented, tested
type mapping between those platforms and Fabric. The replica generation itself stays premium.

Contents:
0. Summary and recommendation
1. What exists today
2. Design
3. Work packages
4. Type mapping
5. Risks
6. Open questions
7. Estimates and order
8. Owner actions
Appendix A: live-test variables · Appendix B: path ownership · Appendix C: scope boundary

---

## 0. Summary and recommendation

- **Extract a dialect-independent relational-profiling core from the SQL Server plugin**
  (`plugins/shape-sqlserver/src/shape_sqlserver/profiler.py` and `catalog.py`) into core as
  `shape.profile.relational`, and make SQL Server its first dialect. §2.1 argues why this is the
  right design and why the alternatives (copy per plugin; core inside `shape-databases`) lose.
- **One command for every platform:** `shape profile-db URI`, dispatched by URI scheme through
  a new entry-point group `shape.profilers` (additive to Plugin API v1; §6 Q2). The existing
  SQL Server flags keep working unchanged.
- **Four dialects in `shape-databases`** (which already holds the drivers as extras, the
  password and token handling, the identifier rules and the fakes for all four platforms):
  PostgreSQL, MySQL, Snowflake, Databricks. Each reuses its sink's connection code.
- **The same output contract as profile-db** (tables, columns, Shape dtypes, sampled statistics,
  declared and inferred keys, relationships with evidence), plus three additive fields:
  `declared_type` per column, `enforced` per relationship and `platform` per dataset.
- **Three test levels per dialect** (T-26): contract tests against fakes that answer the catalog
  and sampling queries; emulator tests for PostgreSQL 16 and MySQL 8.4 in the existing nightly
  `databases-e2e` job; recorded, scrubbed tapes for Snowflake and Databricks; and live tests per
  platform that **fail by variable name** when a setting is missing (the `fabric-live` pattern,
  not the skipping pattern of today's `plugins/shape-databases/tests/test_live.py`).
- **A canonical type model** (`DeclaredType` → Arrow type + Shape dtype + facets) with a
  source-to-canonical parser per dialect and canonical-to-DDL tables for six targets
  (PostgreSQL, MySQL, Snowflake, Databricks, Fabric Warehouse, Fabric SQL database), tested cell
  by cell and verified live by executing the emitted DDL on Fabric.
- Estimate: **about 16 builder-days** across eight work packages (§7), with the four dialects
  parallel after PS-02. Eleven open questions (§6), each with a recommendation.

---

## 1. What exists today

Facts verified in this session against `main` at `304efc6`.

| Area | Where | What it gives this proposal |
|---|---|---|
| `shape profile-db` | `plugins/shape-sqlserver/src/shape_sqlserver/command.py` | argparse surface: `--connection-string`/`--server`/`--database`/`--auth`/`--schema`/`--tables`/`--sample-rows`/`-o`/`--json`; exit 2 with a redacted message on any error |
| Profiling flow | `plugins/shape-sqlserver/src/shape_sqlserver/profiler.py` (454 lines) | catalog walk, sample per table (whole table when it fits, else a deterministic spread, else first rows), per-column statistics from the sample, key merge (declared > data > name), `DatasetProfile` assembly, the `sampling` note; the result is an ordinary multi-table Shape profile |
| Catalog model | `plugins/shape-sqlserver/src/shape_sqlserver/catalog.py` | `ColumnInfo`, `TableInfo`, `ForeignKey`, `Catalog`, `guess_primary_key`, name stemming (`key_stem`, `table_stem`) |
| SQL Server specifics | `plugins/shape-sqlserver/src/shape_sqlserver/sql.py` | `sys.*` catalog queries, bracket quoting, `TOP n`, the scrambled `CHECKSUM` spread, `sql_type_to_dtype`, `sql_type_to_arrow`, `datetimeoffset` converter |
| Test doubles | `plugins/shape-sqlserver/src/shape_sqlserver/testing.py` | `FakeConnection` answering the catalog and `SELECT TOP` queries; eight deterministic scenarios (`retail`, `warehouse_full`, `warehouse_empty`, `name_only`, `id_named`, `mixed_keys`, `key_names`, `no_keys`); `ddl()`/`insert_rows()` load the same data into a real server |
| Emulator and parity | `.github/workflows/nightly.yml` job `sqlserver-e2e`; `benchmarks/vs_refengine/db_1to1/verify.py` | SQL Server 2022 container; T-22 field-by-field parity of database profiling (the baseline moves to recorded results under FRZ-01) |
| Sinks | `plugins/shape-databases` (`postgres.py`, `mysql.py`, `snowflake.py`, `databricks.py`, `_base.py`, `_sql.py`, `_auth.py`, `testing.py`) | URI parsing with secrets refused in the URI, password and token resolution (`env://`, `file://`, `kv://`, Entra tokens, Snowflake key pairs, Databricks OAuth M2M), identifier limits per dialect, `FakeServer`, `FakeSnowflake`, `FakeDatabricks`, `SAMPLE_SCHEMA` |
| CI containers | `ci/emulators/docker-compose.yml`; nightly job `databases-e2e` | `postgres:16` and `mysql:8.4`, URIs `SHAPE_TEST_POSTGRES` / `SHAPE_TEST_MYSQL`, passwords `SHAPE_POSTGRES_PASSWORD` / `SHAPE_MYSQL_PASSWORD` |
| Live pattern | `plugins/shape-fabric/tests/test_live_commands.py`, `.github/workflows/fabric-live.yml` | `need(name)` asserts the variable (fails by name); `environment: fabric-live`; JUnit step summary; tapes recorded under `SHAPE_RECORD_DIR` |
| Recorded DB-API tapes | `plugins/shape-fabric/src/shape_fabric/recording.py` (`TapeConnection`, `Tape`, `scrub`, `find_secrets`) | record once, replay in every test, refuse to save a tape that holds a secret |
| Target DDL today | `src/shape/builtins/sinks/sql.py` (`_TYPES` for `tsql`, `tsql-fabric-warehouse`, `postgres`, `mysql`), `plugins/shape-fabric/src/shape_fabric/_tsql.py` (Arrow → T-SQL with the Warehouse limits), `plugins/shape-databases/src/shape_databases/_sql.py` | the Arrow-to-DDL rules the replica side already uses; the type matrix in §4 extends them, it does not replace them |
| Docs | `docs/plugins/sqlserver.md`, `docs/SINKS.md`, `docs/databases/{postgresql,mysql,snowflake,databricks}.md` | the pages the work packages extend; `docs/plans/` is outside the published site and the wording check |

Gaps: no profiling of PostgreSQL, MySQL, Snowflake or Databricks; no shared catalog model; the
type knowledge is spread over three modules with no source-type side (what `numeric` without
precision, `tinyint(1)` or `NUMBER(38,0)` mean); the `shape-databases` live tests skip instead of
failing by name.

---

## 2. Design

### 2.1 A shared relational-profiling core (and why)

**Claim:** the SQL Server profiler is about nine tenths dialect-independent. Reading
`profiler.py` line by line, the dialect-specific parts are: which catalog queries run (`sql.py`,
`catalog.read_catalog`), how identifiers are quoted, the text of the whole-table and spread
queries, which types the hash can take, the SQL-type-to-dtype map, and driver value conversion.
Everything else (sampling policy, the fallback chain, `_profile_column`, `_links_from_data`,
`_infer_keys_from_names`, `guess_primary_key`, the declared > data > name merge,
`DatasetProfile` assembly, `sampled_rows`/`sample_method`/`sampling`/`fk_evidence`) is the
product's behaviour and documented as such in `docs/plugins/sqlserver.md`.

**Options.**

| Option | Consequence |
|---|---|
| A. Copy the profiler into each new dialect | four divergent copies of the documented behaviour; every rule fix lands four times; parity between platforms unprovable |
| B. Core in `shape-databases`; `shape-sqlserver` depends on it | `shape-sqlserver` gains a dependency it does not need, and `shape-fabric` (which depends on `shape-sqlserver`) transitively too; the SQL Server behaviour would be owned by a package that today knows nothing about profiling |
| C. Core in `shape-sqlserver`; `shape-databases` depends on it | every PostgreSQL or Snowflake install pulls `pyodbc>=5.0` (a hard dependency of `shape-sqlserver`), which defeats the extras design of `shape-databases` (T-07/T-08: no driver a host does not use) |
| **D. Core in `src/shape/profile/relational/`** (pure Python, numpy and pyarrow only; no driver import) | one implementation; both plugins depend only on core, which they already do; the output contract lives next to the profile model it produces; the contract test kit runs with no driver at all; mypy strict and import-linter cover it from day one |

**Recommendation: D.** The module is internal (not in `shape.__all__`, not in the stable API
baseline) until a later work package promotes it. Core gains no dependency (T-07) and imports no
client library; `tests/security` zero-network tests keep passing because nothing opens a socket
at import time.

**The dialect interface** (a Protocol in `shape.profile.relational.dialect`; names are
implementation detail per §0.3):

```python
class Dialect(Protocol):
    name: str                                   # "mssql" | "postgres" | "mysql" | "snowflake" | "databricks"
    enforces_keys: bool                         # PRIMARY KEY / FOREIGN KEY are enforced on this platform
    def read_catalog(self, cursor, schema: str, tables: list[str] | None) -> Catalog: ...
    def row_count(self, cursor, table: TableInfo) -> int: ...
    def whole_query(self, schema: str, table: str, rows: int) -> str: ...
    def spread_query(self, schema: str, table: str, rows: int,
                     hash_columns: list[str], tie_break: list[str] | None) -> str | None: ...
    def hashable(self, column: ColumnInfo) -> bool: ...
    def declared_type(self, column: ColumnInfo) -> DeclaredType: ...   # §4.1
    def normalize(self, value: Any, declared: DeclaredType) -> Any: ... # driver value -> plain value
```

`Catalog`, `TableInfo`, `ColumnInfo`, `ForeignKey` move to core unchanged except that
`ColumnInfo` carries a `DeclaredType` (§4.1) beside the raw `type_name`, and `ForeignKey`
carries `enforced: bool | None`. `profile_connection(conn, dialect, *, schema, sample_rows,
tables, name)` is the core entry point; `shape_sqlserver.profile_database` becomes a thin
wrapper that opens the connection and calls it with `SqlServerDialect`.

**The freeze that makes the extraction safe (PS-01):** the profile of every
`shape_sqlserver.testing.scenario` is recorded from `main` before the change and must be
byte-identical after it (`scripts/profile_db_golden.py`). Nothing about SQL Server behaviour
changes in PS-01; only where the code lives.

### 2.2 One command, dispatched by scheme

`shape profile-db` today is a `shape.commands` entry point of `shape-sqlserver`. Two plugins
cannot register the same command name, so the command moves to core as a thin dispatcher and the
dialects register in a new entry-point group:

```
[project.entry-points."shape.profilers"]
mssql = "shape_sqlserver:SqlServerProfiler"            # shape-sqlserver
postgres = "shape_databases:PostgresProfiler"           # shape-databases, extra [postgres]
mysql = "shape_databases:MySqlProfiler"                 # extra [mysql]
snowflake = "shape_databases:SnowflakeProfiler"         # extra [snowflake]
databricks = "shape_databases:DatabricksProfiler"       # extra [databricks]
```

A `Profiler` has `schemes`, `profile(uri, **options) -> Profile` and `describe(uri) -> str`
(the printable label with no secret). The command:

```
shape profile-db URI [--schema S] [--tables A,B] [--sample-rows N] [-o OUT.shape] [--json SUMMARY.json]
shape profile-db --server HOST --database DB [--auth ...] ...     # SQL Server flags, unchanged
```

URIs are the sinks' own: `postgresql://user@host:5432/db`, `mysql://user@host/db`,
`snowflake://user@account/DB/SCHEMA?warehouse=WH&role=R`,
`databricks://host/sql/1.0/warehouses/ID?catalog=C&schema=S`, `mssql://host/db`. A password or
token in a URI is refused with the sinks' message. Secrets come from the same places as for
writing: `--password env://NAME` or `file://PATH`, or the environment (`SHAPE_POSTGRES_PASSWORD`
then `PGPASSWORD`; `SHAPE_MYSQL_PASSWORD` then `MYSQL_PWD`; `SNOWFLAKE_PASSWORD` or a key pair;
`DATABRICKS_TOKEN` or OAuth M2M). Reading never asks for the non-local confirmation
(`docs/SINKS.md`: reading from a remote source never asks). An unknown scheme is exit 2 with the
schemes installed, like the sinks. When the `shape.profilers` group is judged the wrong vehicle
(§6 Q2), the fallback is a per-plugin command name (`profile-postgres`, ...), which costs four
command names and four help texts for the same options.

### 2.3 Output contract

The result is the profile `profile-db` produces today (`docs/plugins/sqlserver.md`, "What is
exact and what is sampled"), with the same `sampled_rows`, `sample_method`, `sampling` and
`fk_evidence` fields and the same null-means-unknown rules, plus three **additive** fields:

| Field | Where | Content |
|---|---|---|
| `declared_type` | every column | `{"platform": "postgres", "text": "numeric(10,2)", "base": "numeric", "precision": 10, "scale": 2, "length": null, "unsigned": false, "tz": "none", "nested": null, "identity": false, "enum": null}` (§4.1); the exact catalog text is kept so nothing is lost |
| `enforced` | every relationship | `true` (PostgreSQL, MySQL InnoDB, SQL Server, Fabric SQL database), `false` (Snowflake, Databricks informational constraints, Fabric Warehouse), `null` for inferred links |
| `platform` | the dataset | `{"name": "snowflake", "version": "9.12.1", "profiler": "sqllocks-shape-databases 0.9.x"}` |

Readers that do not know the fields ignore them (the `.shape` reader keeps unknown keys). Whether
the addition needs a format version step is a W1-01 question (§6 Q7).

### 2.4 Sampling per platform

The rule stays the documented one: a table with at most `n` rows is read whole; a larger one is
read as `n` rows spread deterministically over the table by a hash of the key (or of every
hashable column when no key is declared); when the platform rejects that query the first `n`
rows are read and `sample_method` says so.

| Platform | Whole table | Spread order | Hash reproducible in Python |
|---|---|---|---|
| SQL Server (today) | `SELECT TOP n *` | `(CAST(CHECKSUM(k…) AS bigint) * 1327217885) % 2147483647, k…` | no (stand-in in the fake) |
| PostgreSQL | `SELECT * … LIMIT n` | `(('x' \|\| left(md5(concat_ws(E'\x1f', k::text…)), 8))::bit(32)::int::bigint * 1327217885) % 2147483647, k…` | **yes** (`hashlib.md5`) |
| MySQL | `SELECT * … LIMIT n` | `(CRC32(CONCAT_WS(CHAR(31), k…)) * 1327217885) % 2147483647, k…` | **yes** (`zlib.crc32`) |
| Snowflake | `SELECT * … LIMIT n` | `HASH(k…), k…` (64-bit, not the identity on integers, so no scramble) | no |
| Databricks | `SELECT * … LIMIT n` | `xxhash64(k…), k…` | no (Spark's value encoding) |

Where the hash is reproducible the fake computes the same order as the server, so the emulator
tests can require the **whole profile** to equal the fake's. For Snowflake and Databricks the
tests require the catalog facts, row counts and the statistics of every table read whole to be
exact, and for sampled tables only `sampled_rows == n`, the method, and that the sampled minimum
and maximum lie inside the table's true range. The spread query scans the table once and keeps
`n` rows (a top-n sort); the cost note in `docs/plugins/sqlserver.md` applies to each platform
and each page says so.

Row counts are exact: `SELECT count(*)` per table (PostgreSQL's `reltuples` and MySQL's
`TABLE_ROWS` are estimates; Snowflake's `INFORMATION_SCHEMA.TABLES.ROW_COUNT` is exact for
standard tables and is used as is; Delta answers `count(*)` from file statistics). §6 Q3 covers
an estimate option.

### 2.5 Catalog sources per platform

| Platform | Tables | Columns and types | Keys |
|---|---|---|---|
| PostgreSQL | `pg_class` (relkind `r`, `p`) in `pg_namespace` | `pg_attribute` with `format_type(atttypid, atttypmod)`, `attnotnull`, `attidentity`, `pg_attrdef` for `nextval(` (serial); domains resolved to the base type like SQL Server alias types; `pg_enum` labels for enum types | `pg_constraint` contype `p`/`f` with `conkey`/`confkey` ordinals; enforced |
| MySQL | `information_schema.TABLES` (`BASE TABLE`, `ENGINE`) | `COLUMNS` (`COLUMN_TYPE` for `unsigned`, `tinyint(1)`, `enum(...)`, `set(...)`; `CHARACTER_SET_NAME`; `DATETIME_PRECISION`; `EXTRA` for `auto_increment`) | `TABLE_CONSTRAINTS` + `KEY_COLUMN_USAGE` + `REFERENTIAL_CONSTRAINTS`; enforced on InnoDB only (`enforced` false otherwise) |
| Snowflake | `<DB>.INFORMATION_SCHEMA.TABLES` (`BASE TABLE`, `ROW_COUNT`) | `INFORMATION_SCHEMA.COLUMNS` (`DATA_TYPE`, precision, scale, `CHARACTER_MAXIMUM_LENGTH`, `DATETIME_PRECISION`, `IS_IDENTITY`) | `SHOW PRIMARY KEYS IN SCHEMA`, `SHOW IMPORTED KEYS IN SCHEMA` (`key_sequence`); declared, not enforced |
| Databricks | `<catalog>.information_schema.tables` (`table_type` `MANAGED`/`EXTERNAL`, `data_source_format`) | `information_schema.columns.full_data_type` (`decimal(10,2)`, `array<string>`, `timestamp_ntz`, `variant`), `is_nullable`, `is_identity` | `table_constraints` + `key_column_usage` + `constraint_column_usage`; `enforced = 'NO'`; `hive_metastore` has no information schema: exit 2 naming Unity Catalog as the requirement |

Identifier case: Snowflake upper-cases unquoted names and Unity Catalog lower-cases table and
schema names; the dialect quotes every identifier it emits and reports names as the catalog holds
them, as `shape_databases._sql.check_identifier` already requires for writes.

### 2.6 Connections and secrets

Each sink already parses the URI, resolves the secret and opens the connection
(`parse_target`, `resolve_auth`, `connect_params`, `default_connect`). PS-03 to PS-06 lift that
into a per-platform `connection.py` used by the sink and the profiler, with no behaviour change
to the sink (its tests pin the statements it sends). Every message goes through
`shape_databases._auth.scrub` so a driver that echoes its parameters cannot leak a password.

### 2.7 Tests at three levels (T-26)

| Level | PostgreSQL / MySQL | Snowflake / Databricks |
|---|---|---|
| contract (every PR) | `FakeCatalogServer` in `shape_databases.testing` answers the catalog and sampling queries of §2.4/2.5 for the dialect-neutral scenarios of the core test kit; the eight SQL Server scenarios are ported to the kit in PS-02 so every dialect profiles the same databases | `FakeSnowflake` / `FakeDatabricks` extended the same way, plus replayed tapes recorded live (`TapeConnection`; scrubbed; a committed tape is scanned for secrets by the suite) |
| emulator (nightly) | the `databases-e2e` job: scenarios loaded with the dialect's `ddl()`/`insert_rows()`, profiled, compared with the fake's profile: **equal** (reproducible hash) | none exists; the tapes stand in |
| live (nightly, where accounts exist) | `SHAPE_LIVE_POSTGRES_URI`, `SHAPE_LIVE_MYSQL_URI` with the usual password variables | `SHAPE_TEST_SNOWFLAKE_URI`, `SHAPE_TEST_DATABRICKS_URI` with `SNOWFLAKE_PASSWORD` / `SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY` / `DATABRICKS_TOKEN` (the sink live tests' names, so one account serves both) |

Every live test calls `need(NAME)`, which **asserts** the variable (fails by name, never skips),
creates a schema `shape_live_<8 hex>`, loads the scenario through the existing sink, profiles it,
checks the exact catalog facts and row counts, and drops the schema in `finally`. The workflow
(PS-08) is `fabric-live.yml` with the names changed: an environment `databases-live`, one run at
a time, a JUnit step summary, tapes uploaded, and the job fails when pytest does.

---

## 3. Work packages

Format follows §7 of the plan: **ID — title** · Depends · Scope · Owned paths · Deliverables ·
Acceptance · Fixes, plus an estimate (§7 of this proposal). Acceptance commands run from the
repository root in the development environment of `CONTRIBUTING.md`; "exit 0" means the command
exits 0 and prints what is quoted. Every package also meets the plan's definition of done (§6.2):
`ruff check`, `ruff format --check`, mypy strict for every new or rewritten module, the suite
with `-m "not emulator and not live"` in both kernel modes, docs in the same commit, and no gate,
tolerance or decision changed. Scenario facts below (6 tables, 3 relationships, `customer` has
2,500 rows) are those of `shape_sqlserver.testing.scenario("retail")`, which
`plugins/shape-sqlserver/tests/test_profiler.py` already asserts.

**PS-01 — Relational profiling core, extracted from the SQL Server plugin**
- Depends: none.
- Scope: move the dialect-independent profiling flow and the catalog model into core
  (§2.1, option D); make `shape-sqlserver` the first dialect; change no behaviour.
- Owned paths: `src/shape/profile/relational/` (new: `__init__.py`, `catalog.py`, `dialect.py`,
  `profiler.py`, `types.py` with the `DeclaredType` model of §4.1 and nothing else of §4),
  `plugins/shape-sqlserver/src/shape_sqlserver/{profiler,catalog,sql}.py` (become the dialect
  and thin wrappers; `shape_sqlserver.profile_database`, `fetch_sample`, `read_catalog`,
  `guess_primary_key` keep their names and signatures), `scripts/profile_db_golden.py` (new),
  `plugins/shape-sqlserver/tests/golden/` (new), `tests/profile/relational/` (new),
  `docs/plugins/sqlserver.md` (one paragraph: where the behaviour lives now).
- Deliverables:
  - `shape.profile.relational.profile_connection(conn, dialect, *, schema, sample_rows, tables, name)`
    and the `Dialect` Protocol of §2.1; `Catalog`, `TableInfo`, `ColumnInfo` (with
    `declared_type`), `ForeignKey` (with `enforced`); the key inference and name stemming.
  - `SqlServerDialect` in `shape_sqlserver.sql` wrapping the existing queries, quoting, spread
    query, type map and `datetimeoffset` converter. `declared_type` for SQL Server is filled
    from `sys.columns` (`type_name`, `max_length`, `precision`, `scale`, `is_identity`).
  - `scripts/profile_db_golden.py --record|--check`: the profile JSON of every scenario of
    `shape_sqlserver.testing` (`scale=1, seed=7`), recorded on `main` **before** the move and
    committed; `--check` reruns and compares byte for byte. The three additive fields of §2.3 are
    excluded from the comparison by name, so the freeze proves nothing else moved.
  - A fake dialect in `tests/profile/relational/fake_dialect.py` (standard library only) that
    answers the catalog and sampling queries from in-memory tables, so the core is tested with no
    plugin installed.
- Acceptance:
  - `python scripts/profile_db_golden.py --check` prints `8 scenarios identical`, exit 0.
  - `pytest -q -m "not emulator and not live" plugins/shape-sqlserver/tests tests/profile/relational`
    exits 0 with no test removed, skipped or changed in what it asserts.
  - `python -c "import sys, shape.profile.relational; print(sorted(m.split('.')[0] for m in sys.modules if m.split('.')[0] in ('pyodbc', 'psycopg', 'pymysql', 'snowflake', 'databricks')))"`
    prints `[]`, exit 0 (the core imports no driver).
  - `ruff check src tests plugins benchmarks/vs_refengine && ruff format --check src tests plugins benchmarks/vs_refengine && mypy`
    exit 0; `lint-imports` exit 0 (core never imports a plugin).
  - `pytest -q plugins/shape-fabric/tests -m "not emulator and not live"` exits 0 (the Fabric
    plugin imports `shape_sqlserver.sql` helpers; they keep their names).
  - Nightly `sqlserver-e2e` stays green on the first run after the merge (the emulator tests
    are unchanged).
- Fixes: none.

**PS-02 — `shape profile-db URI`: dispatch, the `shape.profilers` group, the scenario kit**
- Depends: PS-01.
- Scope: one command for every platform (§2.2); the dialect-neutral scenario kit every
  dialect's tests reuse; the DB-API tape promoted to core so `shape-databases` can replay
  recordings without depending on `shape-fabric`.
- Owned paths: `src/shape/cli/profile_db.py` (new), `src/shape/plugins/api/v1.py` (the
  `Profiler` Protocol, additive), `src/shape/plugins/registry.py` (the group),
  `src/shape/testing/recording.py` (new: `Tape`, `TapeConnection`, `scrub`, `find_secrets`,
  `save` moved from `shape_fabric.recording`, which re-exports them),
  `src/shape/profile/relational/testing.py` (new: the scenario kit), `plugins/shape-sqlserver`
  (`pyproject.toml` entry point, `command.py` reduced to the SQL Server argument group),
  `docs/plugins/api-v1.md`, `docs/plugins/sqlserver.md`, `docs/CLI.md`, `tests/cli/test_profile_db.py`
  (new), `tests/profile/relational/test_scenarios.py` (new), `scripts/cli_surface.py` output.
- Deliverables:
  - `shape profile-db URI` with `--schema`, `--tables`, `--sample-rows`, `-o`, `--json`,
    `--password REF` (an `env://` or `file://` reference only); the SQL Server flags
    (`--connection-string`, `--server`, `--database`, `--auth`, `--tenant-id`, `--client-id`,
    `--client-secret`) are accepted exactly as today and select the `mssql` profiler; a URI and
    `--server` together are exit 2. The JSON line printed on success is unchanged
    (`written`, `shape_content_id`, `tables`, `relationships`).
  - The `shape.profilers` entry-point group with a `Profiler` Protocol (`schemes`, `profile`,
    `describe`), listed by `shape plugins list` and checked by `shape plugins doctor` like every
    other group; `SHAPE_API` major mismatch rejected the same way.
  - The scenario kit: the eight scenarios of `shape_sqlserver.testing` expressed as
    dialect-neutral tables (name, columns with canonical types, rows, keys), with a
    `materialize(dialect_fake)` helper, so each dialect's fake and each emulator test loads the
    same data. `shape_sqlserver.testing.scenario()` is rebuilt on top of it and its golden
    profiles (PS-01) still match.
  - `tests/profile/relational/test_scenarios.py`: every scenario through the fake dialect
    equals its golden profile apart from the three additive fields.
- Acceptance:
  - `shape profile-db --help` exits 0 and shows the `URI` positional, the shared options and
    the "SQL Server" argument group.
  - `shape profile-db 'postgresql://me@db.example/shop' -o out.shape` with only `shape-sqlserver`
    installed prints `shape: error: no profiler for scheme 'postgresql'; installed: mssql`,
    exit 2.
  - `shape profile-db 'postgresql://me:pw@db.example/shop' -o out.shape` exits 2 with the
    sinks' "a password must not be part of the URI" message.
  - `shape plugins list` output contains `shape.profilers:mssql`, exit 0.
  - `python scripts/cli_surface.py --check` exit 0 after the surface file is regenerated in
    the same commit.
  - `pytest -q tests/cli/test_profile_db.py tests/profile/relational plugins/shape-fabric/tests -m "not emulator and not live"`
    exit 0 (the tape move keeps every Fabric replay test passing).
  - `python scripts/profile_db_golden.py --check` still prints `8 scenarios identical`, exit 0.
- Fixes: none.

**PS-03 — PostgreSQL profiler**
- Depends: PS-02.
- Scope: a `PostgresDialect` and `PostgresProfiler` in `shape-databases` (extra `[postgres]`,
  `psycopg` 3), the PostgreSQL source-type parser of §4.3, contract, emulator and live tests.
- Owned paths: `plugins/shape-databases/src/shape_databases/postgres.py` (the sink's
  connection code lifted into `postgres_connection.py`, used by both),
  `plugins/shape-databases/src/shape_databases/profile_postgres.py` (new),
  `plugins/shape-databases/src/shape_databases/testing.py` (`FakeCatalogServer("postgres")`),
  `plugins/shape-databases/tests/test_profile_postgres.py`, `test_profile_postgres_emulator.py`,
  `test_profile_live.py` (the PostgreSQL test), `.github/workflows/nightly.yml` (the
  `databases-e2e` job runs the new emulator file), `docs/databases/postgresql.md`,
  `docs/SOURCES.md` (one row), `plugins/shape-databases/README.md`.
- Deliverables:
  - Catalog from `pg_catalog` (§2.5): ordinary and partitioned tables of one schema (default
    `public`), columns with `format_type` text, not-null, identity and serial detection, domains
    resolved to their base type, enum labels, declared primary and foreign keys with ordinals
    (`enforced: true`), exact `count(*)` row counts.
  - Sampling per §2.4 with the md5 spread; `sample_method` is `all rows`, `hash spread`,
    `first rows (fallback)` or `none`.
  - Value normalisation: `Decimal` kept for `numeric`, `datetime` with `tzinfo` converted to
    UTC and kept zone-aware, arrays and `json`/`jsonb` rendered as JSON text for the statistics
    (dtype `string`, facet `nested`), `interval` as text, `uuid` as text, `bytea` as bytes.
  - The fake implements md5 exactly, so the emulator test asserts the whole profile.
  - Live test against the owner's PostgreSQL account (O-PS-1 in §8): schema
    `shape_live_<hex>` loaded through `PostgresSink`, profiled, exact catalog facts and row
    counts asserted, schema dropped.
- Acceptance:
  - `pytest -q -m contract plugins/shape-databases/tests/test_profile_postgres.py` exit 0;
    includes one test per §4.3 PostgreSQL case (arrays, `jsonb`, `numeric` without precision,
    `timestamptz`, `serial`/identity, enum, domain, `uuid`, `text`) asserting the
    `declared_type` and the Shape dtype.
  - `docker compose -f ci/emulators/docker-compose.yml up -d --wait postgres && SHAPE_POSTGRES_PASSWORD=shape_emulator pytest -q -m emulator plugins/shape-databases/tests/test_profile_postgres_emulator.py`
    exit 0; the test asserts, for each of the eight scenarios, that the server's profile equals
    the fake's profile (whole profile, including sampled statistics).
  - With the `retail` scenario loaded into schema `retail` of the emulator:
    `SHAPE_POSTGRES_PASSWORD=shape_emulator shape profile-db postgresql://shape@localhost:5432/shape --schema retail -o retail.shape --json retail.json`
    prints `{"written": "retail.shape", "shape_content_id": "<id>", "tables": 6, "relationships": 3}`,
    exit 0; `python -c "import json; p=json.load(open('retail.json')); print(p['tables']['customer']['row_count'])"`
    prints `2500`.
  - `env -u SHAPE_LIVE_POSTGRES_URI pytest -q -m live plugins/shape-databases/tests/test_profile_live.py -k postgres`
    exits 1 with `AssertionError: SHAPE_LIVE_POSTGRES_URI is not set (live tests need it)`;
    with the account's variables it exits 0.
  - `pytest -q -m zero_network tests/security` exit 0 (no socket at import).
- Fixes: none.

**PS-04 — MySQL profiler**
- Depends: PS-02.
- Scope: `MySqlDialect` and `MySqlProfiler` (extra `[mysql]`, PyMySQL), the MySQL source-type
  parser of §4.3, contract, emulator and live tests.
- Owned paths: `plugins/shape-databases/src/shape_databases/mysql.py` (connection lifted into
  `mysql_connection.py`), `profile_mysql.py` (new), `testing.py` (`FakeCatalogServer("mysql")`),
  `plugins/shape-databases/tests/test_profile_mysql.py`, `test_profile_mysql_emulator.py`,
  `test_profile_live.py` (the MySQL test), `.github/workflows/nightly.yml` (`databases-e2e`),
  `docs/databases/mysql.md`, `docs/SOURCES.md` (one row), `plugins/shape-databases/README.md`.
- Deliverables:
  - Catalog from `information_schema` (§2.5): base tables of one database (MySQL's schema is
    its database; `--schema` names it and defaults to the URI's database), `COLUMN_TYPE` parsed
    for `unsigned`, display width, `enum`/`set` members and character set, `auto_increment`,
    declared keys (`enforced: true` for InnoDB, `false` for other engines), exact `count(*)`.
  - Sampling with the CRC32 spread (§2.4); the fake implements CRC32 exactly.
  - Normalisation: `TINYINT(1)` to boolean per §4.3 (only when the sample holds nothing but
    0, 1 and NULL), `BIT(1)` to boolean, `YEAR` to integer, `JSON` to JSON text (dtype
    `string`, facet `nested`), `TIMESTAMP` values as UTC, `DATETIME` naive, `DECIMAL` as
    `Decimal`, `SET` as text.
  - Live test against O-PS-2 (§8), same shape as PS-03's.
- Acceptance:
  - `pytest -q -m contract plugins/shape-databases/tests/test_profile_mysql.py` exit 0;
    includes one test per §4.3 MySQL case (`TINYINT UNSIGNED`, `INT UNSIGNED`,
    `BIGINT UNSIGNED`, `TINYINT(1)` as boolean and as small integer, `ENUM`, `SET`, `JSON`,
    `VARCHAR(n)` in `utf8mb4`, `TIMESTAMP` versus `DATETIME(6)`, `DECIMAL(65,30)`).
  - `docker compose -f ci/emulators/docker-compose.yml up -d --wait mysql && SHAPE_MYSQL_PASSWORD=shape_emulator pytest -q -m emulator plugins/shape-databases/tests/test_profile_mysql_emulator.py`
    exit 0; whole-profile equality with the fake for each scenario, loaded one scenario at a
    time into database `shape` and dropped afterwards.
  - With `retail` loaded: `SHAPE_MYSQL_PASSWORD=shape_emulator shape profile-db mysql://shape@127.0.0.1:3306/shape -o retail.shape`
    prints `{"written": "retail.shape", "shape_content_id": "<id>", "tables": 6, "relationships": 3}`,
    exit 0.
  - `env -u SHAPE_LIVE_MYSQL_URI pytest -q -m live plugins/shape-databases/tests/test_profile_live.py -k mysql`
    exits 1 with `AssertionError: SHAPE_LIVE_MYSQL_URI is not set (live tests need it)`; with
    the account's variables it exits 0.
- Fixes: none.

**PS-05 — Snowflake profiler**
- Depends: PS-02.
- Scope: `SnowflakeDialect` and `SnowflakeProfiler` (extra `[snowflake]`), the Snowflake
  source-type parser of §4.3, contract tests against the fake and against recorded tapes, a live
  test that also records the tapes.
- Owned paths: `plugins/shape-databases/src/shape_databases/snowflake.py` (connection lifted
  into `snowflake_connection.py`), `profile_snowflake.py` (new), `testing.py` (`FakeSnowflake`
  answers the catalog and `SHOW` queries), `plugins/shape-databases/tests/test_profile_snowflake.py`,
  `plugins/shape-databases/tests/tapes/snowflake_*.json` (new, scrubbed), `test_profile_live.py`
  (the Snowflake test), `docs/databases/snowflake.md`, `docs/SOURCES.md`, `README.md`.
- Deliverables:
  - Catalog (§2.5): `INFORMATION_SCHEMA.TABLES` and `COLUMNS` of `DB.SCHEMA` from the URI,
    `SHOW PRIMARY KEYS IN SCHEMA` and `SHOW IMPORTED KEYS IN SCHEMA` read from the cursor,
    `ROW_COUNT` as the exact count, `enforced: false` on every declared key; identifiers reported
    as the catalog holds them (upper case unless created quoted).
  - Sampling with `HASH` (§2.4). `sampling.method` names the Snowflake query and its credit
    cost (one scan, top-n) in the profile.
  - Normalisation: `NUMBER(p,s)` as `Decimal`, `NUMBER(p,0)` per §4.3/§6 Q4,
    `TIMESTAMP_NTZ` naive, `TIMESTAMP_LTZ`/`TIMESTAMP_TZ` as UTC zone-aware (the original
    offset of `TZ` values is kept in the `declared_type` facet `tz: "offset"`),
    `VARIANT`/`OBJECT`/`ARRAY` as JSON text (dtype `string`, facet `nested`), `GEOGRAPHY` as
    GeoJSON text, `BINARY` as bytes.
  - Tapes: the live test records the real conversation through `shape.testing.recording`
    (`SHAPE_RECORD_DIR`), `save` refuses a tape holding a secret, and the committed tapes are
    replayed by `test_profile_snowflake.py`; a suite test scans every tape with `find_secrets`.
  - Live test against O-PS-3 (§8): schema `SHAPE_LIVE_<HEX>` created in the test database,
    loaded through `SnowflakeSink`, profiled, exact catalog facts asserted, dropped.
- Acceptance:
  - `pytest -q -m contract plugins/shape-databases/tests/test_profile_snowflake.py` exit 0;
    includes the §4.3 Snowflake cases (`NUMBER(38,0)`, `NUMBER(10,2)`, `VARIANT`, `OBJECT`,
    `ARRAY`, `TIMESTAMP_NTZ`, `TIMESTAMP_LTZ`, `TIMESTAMP_TZ`, `VARCHAR` with no length,
    `BINARY`, `BOOLEAN`, `GEOGRAPHY`) and one tape replay per scenario recorded.
  - `pytest -q plugins/shape-databases/tests/test_tapes.py` exit 0 (`find_secrets` finds
    nothing in any committed tape).
  - `env -u SHAPE_TEST_SNOWFLAKE_URI pytest -q -m live plugins/shape-databases/tests/test_profile_live.py -k snowflake`
    exits 1 with `AssertionError: SHAPE_TEST_SNOWFLAKE_URI is not set (live tests need it)`;
    with the account's variables it exits 0 and, when `SHAPE_RECORD_DIR` is set, writes
    `snowflake_retail.json` there with `"source": "live"`.
  - `shape profile-db 'snowflake://USER@ACCOUNT/SHAPE_LIVE/RETAIL?warehouse=WH' -o retail.shape`
    against the live schema prints `{"written": "retail.shape", "shape_content_id": "<id>", "tables": 6, "relationships": 3}`,
    exit 0, and `retail.shape`'s relationships all carry `"enforced": false`.
- Fixes: none.

**PS-06 — Databricks (Unity Catalog) profiler**
- Depends: PS-02.
- Scope: `DatabricksDialect` and `DatabricksProfiler` (extra `[databricks]`), the Delta
  source-type parser of §4.3, contract tests against the fake and recorded tapes, a live test.
- Owned paths: `plugins/shape-databases/src/shape_databases/databricks.py` (connection lifted
  into `databricks_connection.py`), `profile_databricks.py` (new), `testing.py`
  (`FakeDatabricks` answers `information_schema`), `plugins/shape-databases/tests/test_profile_databricks.py`,
  `plugins/shape-databases/tests/tapes/databricks_*.json`, `test_profile_live.py` (the
  Databricks test), `docs/databases/databricks.md`, `docs/SOURCES.md`, `README.md`.
- Deliverables:
  - Catalog (§2.5) from `<catalog>.information_schema`: managed and external tables of one
    schema, `full_data_type` parsed (including `array<...>`, `map<...>`, `struct<...>`,
    `timestamp_ntz`, `variant`, `interval`), identity columns, declared primary and foreign
    keys with `enforced: false` (informational constraints; `RELY` recorded as a facet), exact
    `count(*)`. A `hive_metastore` catalog, or a URI without `catalog=`, is exit 2 naming Unity
    Catalog as the requirement.
  - Sampling with `xxhash64` (§2.4).
  - Normalisation: `TIMESTAMP` as UTC zone-aware, `TIMESTAMP_NTZ` naive, nested types and
    `VARIANT` as JSON text (dtype `string`, facet `nested`), `DECIMAL` as `Decimal`, `BINARY`
    as bytes, `INTERVAL` as text.
  - Tapes and live test as in PS-05, against O-PS-4 (§8), schema `shape_live_<hex>` created
    in the test catalog and dropped.
- Acceptance:
  - `pytest -q -m contract plugins/shape-databases/tests/test_profile_databricks.py` exit 0;
    includes the §4.3 Databricks cases (`TINYINT`, `DECIMAL(38,18)`, `STRING`, `TIMESTAMP`,
    `TIMESTAMP_NTZ`, `ARRAY<INT>`, `MAP<STRING,STRING>`, `STRUCT<...>`, `VARIANT`,
    `INTERVAL DAY TO SECOND`, identity column, an informational primary key, a `RELY` foreign
    key) and one tape replay per scenario recorded.
  - `shape profile-db 'databricks://host/sql/1.0/warehouses/ID?catalog=hive_metastore&schema=x' -o out.shape`
    exits 2 with `shape: error: profiling needs a Unity Catalog catalog; hive_metastore has no information schema`
    (asserted against the fake; no connection is opened).
  - `env -u SHAPE_TEST_DATABRICKS_URI pytest -q -m live plugins/shape-databases/tests/test_profile_live.py -k databricks`
    exits 1 with `AssertionError: SHAPE_TEST_DATABRICKS_URI is not set (live tests need it)`;
    with the account's variables it exits 0.
  - Against the live schema the `retail` command line prints `"tables": 6, "relationships": 3`
    and every relationship carries `"enforced": false`.
- Fixes: none.

**PS-07 — Type mapping: canonical types, six targets, the documented matrix**
- Depends: PS-01 (the `DeclaredType` model); the matrix rows of a source platform land with
  its profiler (PS-03 to PS-06) and the test grows with them.
- Scope: §4 as code and as a page the tests read: canonical-to-DDL for PostgreSQL, MySQL,
  Snowflake, Databricks, Fabric Warehouse and Fabric SQL database; keys and constraints per
  target; the seam through which a profile's `declared_type` reaches the sinks.
- Owned paths: `src/shape/profile/relational/types.py` (parsers' shared pieces),
  `src/shape/profile/relational/targets.py` (new), `docs/databases/TYPE_MAPPING.md` (new),
  `tests/profile/relational/test_type_matrix.py` (new), `src/shape/builtins/sinks/sql.py`,
  `plugins/shape-fabric/src/shape_fabric/_tsql.py`, `plugins/shape-databases/src/shape_databases/_sql.py`
  (each honours an optional `sql_type` and `keys` entry in the `columns`/options mapping; every
  existing DDL test stays byte-identical when the entries are absent),
  `src/shape/generation/output.py` (`sql_options` carries `declared_type` from a profile into
  `columns`), `docs/SINKS.md` (one paragraph), `docs/plugins/fabric-writers.md`.
- Deliverables:
  - `targets.ddl_type(declared: DeclaredType, target: str) -> str` for the six targets and
    `targets.key_statements(table, keys, target) -> list[str]` (§4.4): enforced constraints on
    PostgreSQL, MySQL and Fabric SQL database; declared-not-enforced on Snowflake, Databricks
    (`NOT ENFORCED`) and Fabric Warehouse (`NONCLUSTERED NOT ENFORCED` through `ALTER TABLE`,
    behind `keys=declare`; §6 Q6).
  - `docs/databases/TYPE_MAPPING.md`: the §4.2 matrix and the §4.3 source tables, and the
    rules in words. **The test parses the page's tables**, so the page cannot drift from the
    code (no claim without enforcement, §6.4).
  - A canonical-type parser per platform lives with its profiler; PS-07 adds the shared
    grammar (`name(p,s)`, `name(n)`, `unsigned`, `array<...>`, `[]` suffix, `with time zone`).
- Acceptance:
  - `pytest -q tests/profile/relational/test_type_matrix.py` exit 0 and its summary line
    reports one passed test per matrix cell: at the end of PS-07 with PS-03 to PS-06 merged,
    `docs/databases/TYPE_MAPPING.md` has 24 canonical rows × 6 targets = 144 target cells and
    at least 48 source rows (12 per platform), so at least 192 tests pass.
  - `python -c "from shape.profile.relational.types import parse; from shape.profile.relational.targets import ddl_type; d=parse('postgres','numeric'); print(d.arrow, ddl_type(d,'fabric-warehouse'), ddl_type(d,'postgres'))"`
    prints `decimal128(38, 9) DECIMAL(38,9) NUMERIC` (§4.3, numeric without precision).
  - `python -c "from shape.profile.relational.types import parse; from shape.profile.relational.targets import ddl_type; print(ddl_type(parse('snowflake','NUMBER(38,0)'),'fabric-warehouse'))"`
    prints `BIGINT` (§6 Q4's recommendation; `DECIMAL(38,0)` if the owner picks option B).
  - `python -c "from shape.profile.relational.types import parse; from shape.profile.relational.targets import ddl_type; print(ddl_type(parse('mysql','varchar(9000)'),'fabric-warehouse'))"`
    raises `ShapeError: a Warehouse string holds at most 8000 characters (max_length 9000)`,
    exit 1.
  - `pytest -q tests/builtins tests/generation plugins/shape-fabric/tests plugins/shape-databases/tests -m "not emulator and not live"`
    exit 0, with every existing DDL expectation unchanged.
  - `mkdocs build --strict && python scripts/check_doc_links.py --site site && python scripts/check_user_facing.py --site site`
    exit 0 with the new page in the navigation.
- Fixes: none.

**PS-08 — Live workflow and the Fabric DDL round trip**
- Depends: PS-03, PS-04, PS-05, PS-06, PS-07.
- Scope: the nightly `databases-live` workflow; a live check that the DDL emitted from each
  platform's profile executes on Fabric Warehouse and Fabric SQL database and comes back with
  the expected columns and keys.
- Owned paths: `.github/workflows/databases-live.yml` (new),
  `plugins/shape-fabric/tests/test_live_type_mapping.py` (new),
  `plugins/shape-databases/tests/test_profile_live.py` (the per-platform tests already landed;
  this package adds the Fabric leg's fixtures), `docs/CI.md`, `docs/plans/live/` (a runbook
  page for the accounts, no secrets).
- Deliverables:
  - `databases-live.yml` copied from `fabric-live.yml` (environment `databases-live`, `schedule`
    and `workflow_dispatch` with `ref`, one run at a time, JUnit step summary, tapes uploaded,
    failing when pytest fails); the environment holds the Appendix A variables.
  - `test_live_type_mapping.py`: for each committed live tape (PostgreSQL, MySQL, Snowflake,
    Databricks `retail`), emit the target DDL for `fabric-warehouse` and `fabric-sql` (PS-07),
    execute it as `shape_live_<hex>` in the Warehouse (`FABRIC_WAREHOUSE_CONNECTION_STRING`)
    and the SQL database (`FABRIC_SQL_CONNECTION_STRING`), read `sys.columns` and
    `sys.key_constraints` back, assert the types and keys of the matrix, drop the tables. Runs
    in `fabric-live.yml` (it is a Fabric test) and needs no new secret.
- Acceptance:
  - `python -c "import yaml; d=yaml.safe_load(open('.github/workflows/databases-live.yml')); print(d['jobs']['databases-live']['environment'], sorted(d['on']))"`
    prints `databases-live ['schedule', 'workflow_dispatch']`, exit 0.
  - `python scripts/check_release_workflows.py` and `python scripts/check_secrets.py` exit 0.
  - The first nightly run of `databases-live` after the environment exists shows, in the step
    summary, `passed 4 / failed 0 / skipped 0` for `test_profile_live.py`; a run before the
    environment exists shows `failed 4` with each failure naming its variable (this is the
    required behaviour, not a flake).
  - `pytest -q -m live plugins/shape-fabric/tests/test_live_type_mapping.py` exits 0 in
    `fabric-live`; `env -u FABRIC_WAREHOUSE_CONNECTION_STRING pytest -q -m live plugins/shape-fabric/tests/test_live_type_mapping.py -k warehouse`
    exits 1 with `AssertionError: FABRIC_WAREHOUSE_CONNECTION_STRING is not set (live tests need it)`.
- Fixes: none.

---

## 4. Type mapping

### 4.1 Canonical model

```python
@dataclass(frozen=True)
class DeclaredType:
    platform: str            # "postgres" | "mysql" | "snowflake" | "databricks" | "mssql"
    text: str                # the catalog's own text, kept verbatim: "numeric(10,2)", "int unsigned", "NUMBER(38,0)"
    base: str                # normalised family name: "integer", "decimal", "float", "string", "boolean",
                             # "date", "time", "timestamp", "binary", "uuid", "json", "duration", "enum"
    arrow: pa.DataType       # the Arrow type values are read as (what the profile's statistics describe)
    dtype: str               # the Shape column type: integer | float | date | datetime | boolean | string
    precision: int | None    # decimals; bits for integers are implied by `arrow`
    scale: int | None
    length: int | None       # characters for strings, bytes for binary; None = unbounded
    unsigned: bool
    tz: str                  # "none" (naive), "utc" (instant), "offset" (instant with its offset kept)
    nested: str | None       # "array" | "object" | "map" | "struct" | "variant" when the value is stored as JSON text
    identity: bool           # serial / auto_increment / IDENTITY / GENERATED AS IDENTITY
    enum: tuple[str, ...] | None   # the declared members of an enum or set type (exact, from the catalog)
```

Two rules decide `arrow` and `dtype` everywhere: **nothing is lost silently** (when a source
type has no exact Arrow equivalent the value becomes text and `text`/`nested` say what it was),
and **the statistics describe the Arrow values** (so a `NUMBER(38,0)` read as `int64` has
integer min/max/mean; one read as `decimal128` has decimal ones).

### 4.2 Canonical to target DDL

Nullability is a separate clause (`NULL`/`NOT NULL`, key columns always `NOT NULL`). String
lengths follow the sinks' rule: the declared length when there is one, else the longest sampled
value with a floor of 255, else the target's unbounded type. `p`/`s` are the declared precision
and scale; `n` a length in characters.

| Canonical (Arrow → dtype) | PostgreSQL | MySQL | Snowflake | Databricks | Fabric Warehouse | Fabric SQL database |
|---|---|---|---|---|---|---|
| bool → boolean | `BOOLEAN` | `TINYINT(1)` | `BOOLEAN` | `BOOLEAN` | `BIT` | `BIT` |
| int8 → integer | `SMALLINT` | `TINYINT` | `NUMBER(3,0)` | `TINYINT` | `SMALLINT` | `TINYINT` (0..255 only: `SMALLINT` when the sample holds a negative) |
| int16 → integer | `SMALLINT` | `SMALLINT` | `NUMBER(5,0)` | `SMALLINT` | `SMALLINT` | `SMALLINT` |
| int32 → integer | `INTEGER` | `INT` | `NUMBER(10,0)` | `INT` | `INT` | `INT` |
| int64 → integer | `BIGINT` | `BIGINT` | `NUMBER(19,0)` | `BIGINT` | `BIGINT` | `BIGINT` |
| uint8 → integer | `SMALLINT` | `TINYINT UNSIGNED` | `NUMBER(3,0)` | `SMALLINT` | `SMALLINT` | `TINYINT` |
| uint16 → integer | `INTEGER` | `SMALLINT UNSIGNED` | `NUMBER(5,0)` | `INT` | `INT` | `INT` |
| uint32 → integer | `BIGINT` | `INT UNSIGNED` | `NUMBER(10,0)` | `BIGINT` | `BIGINT` | `BIGINT` |
| uint64 → integer | `NUMERIC(20,0)` | `BIGINT UNSIGNED` | `NUMBER(20,0)` | `DECIMAL(20,0)` | `DECIMAL(20,0)` | `DECIMAL(20,0)` |
| float32 → float | `REAL` | `FLOAT` | `FLOAT` | `FLOAT` | `REAL` | `REAL` |
| float64 → float | `DOUBLE PRECISION` | `DOUBLE` | `FLOAT` | `DOUBLE` | `FLOAT` | `FLOAT` |
| decimal128(p,s), p ≤ 38 → float | `NUMERIC(p,s)` | `DECIMAL(p,s)` | `NUMBER(p,s)` | `DECIMAL(p,s)` | `DECIMAL(p,s)` | `DECIMAL(p,s)` |
| decimal, p > 38 (MySQL up to 65; PostgreSQL unbounded) → string | `NUMERIC(p,s)` | `DECIMAL(p,s)` | `VARCHAR(p+2)` + facet | `STRING` + facet | `VARCHAR(p+2)` | `VARCHAR(p+2)` |
| string(n) → string | `VARCHAR(n)` | `VARCHAR(n)` (n ≤ 16383 in utf8mb4, else `TEXT`) | `VARCHAR(n)` | `STRING` | `VARCHAR(n)` (n ≤ 8000, else refused by column name) | `NVARCHAR(n)` (n ≤ 4000, else `NVARCHAR(MAX)`) |
| string, unbounded → string | `TEXT` | `LONGTEXT` | `VARCHAR` | `STRING` | `VARCHAR(8000)` (a sampled value over 8000 characters is refused by column name) | `NVARCHAR(MAX)` |
| uuid (facet) → string | `UUID` | `CHAR(36)` | `VARCHAR(36)` | `STRING` | `VARCHAR(36)` | `UNIQUEIDENTIFIER` |
| json / nested (facet) → string | `JSONB` | `JSON` | `VARIANT` (`OBJECT`/`ARRAY` when that was the source) | `VARIANT` (`STRING` when the warehouse runtime has no `VARIANT`; §6 Q10) | `VARCHAR(8000)` (longer refused) | `NVARCHAR(MAX)` |
| enum (facet) → string | `VARCHAR(longest member)` with a `CHECK (col IN (...))` | `ENUM(members)` | `VARCHAR(longest)` | `STRING` | `VARCHAR(longest)` | `NVARCHAR(longest)` + `CHECK` |
| binary → string | `BYTEA` | `LONGBLOB` | `BINARY` | `BINARY` | `VARBINARY(8000)` | `VARBINARY(MAX)` |
| date32 → date | `DATE` | `DATE` | `DATE` | `DATE` | `DATE` | `DATE` |
| time64(us) → string | `TIME` | `TIME(6)` | `TIME(6)` | `STRING` (Delta has no time type) | `TIME(6)` | `TIME(7)` |
| timestamp(us), tz none → datetime | `TIMESTAMP` | `DATETIME(6)` | `TIMESTAMP_NTZ(6)` | `TIMESTAMP_NTZ` | `DATETIME2(6)` | `DATETIME2(7)` |
| timestamp(us, UTC), tz utc or offset → datetime | `TIMESTAMPTZ` | `DATETIME(6)` in UTC (`TIMESTAMP` stops at 2038) | `TIMESTAMP_TZ(6)` (`TIMESTAMP_LTZ` when the source was `LTZ`) | `TIMESTAMP` | `DATETIME2(6)` in UTC (no `DATETIMEOFFSET` in the Warehouse) | `DATETIMEOFFSET(7)` |
| duration / interval → string | `INTERVAL` | `VARCHAR(64)` | `VARCHAR(64)` | `INTERVAL DAY TO SECOND` | `VARCHAR(64)` | `VARCHAR(64)` |
| identity (facet) | `GENERATED BY DEFAULT AS IDENTITY` | `AUTO_INCREMENT` | `AUTOINCREMENT` | `GENERATED BY DEFAULT AS IDENTITY` | none (no identity semantics; a `-- NOTE` as today) | `IDENTITY(start, step)` |

This matrix reproduces every row the sinks emit today (`src/shape/builtins/sinks/sql.py`
`_TYPES`, `plugins/shape-fabric/src/shape_fabric/_tsql.py`, the DuckDB table in
`docs/SINKS.md`) and adds the source side; where a sink's current choice differs from the row
above (none found in this session), the sink's choice wins and the row is corrected, so no
generated DDL changes under PS-07.

### 4.3 Source types to canonical, and the hard cases

**Snowflake**

| Source | Canonical | Decision |
|---|---|---|
| `NUMBER(38,0)` (what `INT`, `INTEGER`, `BIGINT` declare) | `int64`, dtype integer, `precision: 38` | §6 Q4: read as `int64`; the sample must fit (`abs(v) < 2**63`), else `decimal128(38,0)`; `declared_type.text` keeps `NUMBER(38,0)`; targets emit `BIGINT`. Option B emits `DECIMAL(38,0)` everywhere |
| `NUMBER(p,0)`, p ≤ 18 | `int64` | always an integer |
| `NUMBER(p,s)`, s > 0 | `decimal128(p,s)`, dtype float | statistics in decimal; targets `DECIMAL(p,s)` |
| `FLOAT` (`DOUBLE`, `REAL` are synonyms) | `float64` | |
| `VARCHAR(n)` / `VARCHAR` (default 16 MB) | `string`, `length: n` or `None` | the Warehouse target checks the sampled length against 8000 |
| `VARIANT`, `OBJECT`, `ARRAY` | `string`, `nested: "variant"/"object"/"array"` | statistics on the JSON text; targets per §4.2 |
| `TIMESTAMP_NTZ(p)` | `timestamp("us")`, `tz: "none"` | naive everywhere |
| `TIMESTAMP_LTZ(p)` | `timestamp("us", tz="UTC")`, `tz: "utc"` | the connector returns the instant; the session zone is not recorded in the value |
| `TIMESTAMP_TZ(p)` | `timestamp("us", tz="UTC")`, `tz: "offset"` | the instant is kept; the original offset survives only where the target has an offset type (`TIMESTAMPTZ`, `TIMESTAMP_TZ`, `DATETIMEOFFSET`); elsewhere UTC |
| `BOOLEAN` | `bool` | |
| `BINARY(n)` | `binary`, `length: n` | |
| `DATE`, `TIME(p)` | `date32`, `time64("us")` | |
| `GEOGRAPHY`, `GEOMETRY` | `string` (GeoJSON / WKT text), `base: "string"` | kept as text; no spatial target type |
| keys | `enforced: false` | Snowflake declares and never enforces primary and foreign keys (NOT NULL excepted) |

**Databricks (Delta on Unity Catalog)**

| Source | Canonical | Decision |
|---|---|---|
| `TINYINT`, `SMALLINT`, `INT`, `BIGINT` | `int8`, `int16`, `int32`, `int64` | |
| `FLOAT`, `DOUBLE` | `float32`, `float64` | |
| `DECIMAL(p,s)` (p ≤ 38) | `decimal128(p,s)` | |
| `STRING` | `string`, unbounded | length from the sample for bounded targets |
| `BOOLEAN`, `BINARY`, `DATE` | `bool`, `binary`, `date32` | |
| `TIMESTAMP` | `timestamp("us", tz="UTC")`, `tz: "utc"` | stored as an instant; the session zone is not part of the value |
| `TIMESTAMP_NTZ` | `timestamp("us")`, `tz: "none"` | |
| `ARRAY<T>`, `MAP<K,V>`, `STRUCT<...>`, `VARIANT` | `string`, `nested: "array"/"map"/"struct"/"variant"` | JSON text; the element types are kept in `text` |
| `INTERVAL ...` | `string`, `base: "duration"` | |
| constraints | `enforced: false`; `RELY` as a facet of the relationship | informational primary and foreign keys; the sampled data still gets the data-evidence pass, so a declared key the data contradicts is visible in `fk_evidence` and the `sampled_integrity` note (§6 Q11) |
| identity | `identity: true` | `GENERATED ALWAYS/BY DEFAULT AS IDENTITY` |

**PostgreSQL**

| Source | Canonical | Decision |
|---|---|---|
| `smallint`, `integer`, `bigint`; `serial*` and identity | `int16`, `int32`, `int64`; `identity: true` | |
| `numeric(p,s)` | `decimal128(p,s)` when p ≤ 38, else `string` with `precision`/`scale` kept | |
| `numeric` (no precision) | `decimal128(38, s_obs)`, `scale` = the largest scale seen in the sample, capped at 9; `precision: None` | the only lossless answer needs a scale; PostgreSQL targets get bare `NUMERIC` back; others `DECIMAL(38, s_obs)`; the facet says it was unbounded. A sample with no value gives scale 2 |
| `real`, `double precision` | `float32`, `float64` | |
| `boolean` | `bool` | |
| `character varying(n)`, `character(n)`, `text` | `string`, `length: n` or `None` | `char(n)` values keep their padding (`text` says `character(n)`) |
| `uuid` | `string`, `base: "uuid"` | |
| `json`, `jsonb` | `string`, `nested: "object"` | |
| `T[]` (any array, any dimension) | `string`, `nested: "array"`, `text: "integer[]"` | JSON text of the array; the element type stays in `text`; round trip to PostgreSQL re-emits the array type |
| `timestamp` (without time zone) | `timestamp("us")`, `tz: "none"` | |
| `timestamptz` | `timestamp("us", tz="UTC")`, `tz: "utc"` | psycopg returns zone-aware values; converted to UTC |
| `date`, `time`, `timetz` | `date32`, `time64("us")`, `string` (`timetz` has no Arrow type) | |
| `interval` | `string`, `base: "duration"` | |
| `bytea` | `binary` | |
| enum types (`typtype = 'e'`) | `string`, `enum: labels` | exact members from `pg_enum`; `enum_values` statistics still come from the sample |
| domains | the base type, `text` keeps the domain name | as SQL Server alias types |
| `money`, `inet`, `cidr`, `macaddr`, `bit(n)`, `tsvector`, `xml`, PostGIS `geometry` | `string` | text; `money` as `decimal128(19,2)` |

**MySQL**

| Source | Canonical | Decision |
|---|---|---|
| `tinyint`, `smallint`, `mediumint`, `int`, `bigint` | `int8`, `int16`, `int32`, `int32`, `int64` | |
| `... unsigned` | `uint8`, `uint16`, `uint32`, `uint32`, `uint64` | `bigint unsigned` reaches targets as `DECIMAL(20,0)` (§4.2), as `_tsql.py` does today |
| `tinyint(1)` | `bool` **only when** the sample holds nothing but 0, 1 and NULL; else `int8` | MySQL's `BOOL` is an alias; the display width alone proves nothing; `declared_type.text` keeps `tinyint(1)` either way |
| `bit(1)` / `bit(n)` | `bool` / `binary` | |
| `decimal(p,s)` | `decimal128(p,s)` when p ≤ 38, else `string` with precision and scale kept (MySQL allows 65) | |
| `float`, `double` | `float32`, `float64` | |
| `varchar(n)`, `char(n)` in `utf8mb4` | `string`, `length: n` (characters) | the MySQL **target** rule: n ≤ 16383 stays `VARCHAR(n)` (row limit 65,535 bytes), longer becomes `TEXT`; a key column over 768 characters is refused by name (3,072-byte index limit); other targets use `n` as is |
| `text`, `mediumtext`, `longtext` | `string`, unbounded | |
| `enum('a','b')`, `set('a','b')` | `string`, `enum: members` (`set` gets `base: "string"`, `text` keeps the members) | |
| `json` | `string`, `nested: "object"` | |
| `datetime(fsp)` | `timestamp("us")`, `tz: "none"` | |
| `timestamp(fsp)` | `timestamp("us", tz="UTC")`, `tz: "utc"` | MySQL stores UTC and converts on read; the connection runs with `time_zone = '+00:00'` so the value is the instant |
| `date`, `time(fsp)`, `year` | `date32`, `time64("us")`, `int16` | |
| `binary`, `varbinary`, `*blob` | `binary` | |
| `auto_increment` | `identity: true` | |
| keys | `enforced: true` on InnoDB; `false` on MyISAM and other engines | |

**Fabric Warehouse (target; also a source through `mssql`)**

| Constraint (as the repository's writers enforce it today) | Rule in the matrix |
|---|---|
| no `PRIMARY KEY` enforcement; no key in `CREATE TABLE` | `keys=comment` (today's `-- NOTE`) by default; `keys=declare` emits `ALTER TABLE t ADD CONSTRAINT pk PRIMARY KEY NONCLUSTERED (cols) NOT ENFORCED` and the same for foreign keys (§6 Q6: to be verified against Microsoft Learn and listed in `docs/FABRIC_PLATFORM.md` before it ships) |
| `VARCHAR` at most 8000; no `NVARCHAR`, no `(MAX)` | strings longer than 8000 in the declared length or in the sample are refused by column name (today's `_tsql.py` behaviour); JSON and array text share the limit |
| `DATETIME2(p)` with p ≤ 6; no `DATETIMEOFFSET` | instants are written in UTC as `DATETIME2(6)`; `tz: "offset"` loses its offset and the page says so |
| no `TINYINT`, no `UNIQUEIDENTIFIER`, no identity | `SMALLINT`, `VARCHAR(36)`, and identity dropped with a note (today's rules) |
| `DECIMAL` at most 38 digits | precision over 38 is text |

**Fabric SQL database (target; a source through `mssql` today)**

SQL Server semantics (Azure SQL): `NVARCHAR(n)`/`NVARCHAR(MAX)`, `DATETIME2(7)`,
`DATETIMEOFFSET(7)`, `UNIQUEIDENTIFIER`, `TINYINT`, `BIGINT IDENTITY(start, step)`, enforced
primary and foreign keys. It is the `tsql` dialect of the `sql` sink and the `mssql://` sink, so
the matrix adds nothing new to those writers beyond the `sql_type` seam.

### 4.4 Keys, constraints and order

| Target | Primary key | Foreign key | Notes |
|---|---|---|---|
| PostgreSQL | `PRIMARY KEY (cols)` in `CREATE TABLE` | `ALTER TABLE child ADD CONSTRAINT fk FOREIGN KEY (cols) REFERENCES parent (cols)` after every table exists | enforced; parents are created before children (a topological order of the profile's relationships, cycles broken by emitting the FK last) |
| MySQL | same | same; InnoDB creates the index | enforced; a key column's byte length ≤ 3,072 |
| Snowflake | `PRIMARY KEY (cols)` | `FOREIGN KEY (cols) REFERENCES parent (cols)` | declared only; recorded with `enforced: false` when profiled back |
| Databricks | `PRIMARY KEY (cols) NOT ENFORCED` | `FOREIGN KEY (cols) REFERENCES parent (cols) NOT ENFORCED` | needs Unity Catalog; `RELY` when the source relationship carried it |
| Fabric Warehouse | comment (default) or `ALTER TABLE ... NOT ENFORCED` (`keys=declare`) | same | §6 Q6 |
| Fabric SQL database | `CONSTRAINT PK_t PRIMARY KEY (cols)` | `ALTER TABLE ... FOREIGN KEY ...` | enforced, as the `tsql` dialect does today |

Composite keys keep their column order. A relationship whose `evidence` is `data` or `name`
(inferred, `enforced: null`) is emitted only with `keys=declare-inferred`, never by default: an
inferred key on a target that enforces it can refuse the data.

---

## 5. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The extraction changes SQL Server behaviour | parity and the shipped `profile-db` regress silently | the golden freeze in PS-01 (byte-identical profiles for all eight scenarios), every existing SQL Server test unchanged, `sqlserver-e2e` green before merge |
| `count(*)` on very large tables | minutes per table on PostgreSQL and MySQL; credits on Snowflake | §6 Q3: an estimate option later; the page states the cost; the spread query already scans once |
| Hash spread cost on cloud warehouses | one scan plus a top-n sort per table, billed | `--sample-rows 0` profiles the catalog only; the page gives the cost; a `--tables` list limits the run |
| Snowflake `HASH` and Spark `xxhash64` cannot be reproduced in the fake | the emulator-style whole-profile equality is impossible for those two | tapes pin the real conversation; the live test asserts the exact catalog facts and the whole-table statistics |
| Secrets in tapes | a committed tape leaks a password or token | `save` refuses a tape in which `find_secrets` matches; a suite test scans every tape; the live job uploads tapes as artifacts, never commits them |
| Connector version drift (`snowflake-connector-python`, `databricks-sql-connector`, psycopg, PyMySQL) | value types change (`Decimal` versus `float`, zone handling) | pinned ranges in the plugin extras; the contract tests assert the normalised values; the live job runs nightly |
| Additive profile fields | an older reader of `.shape` files meets unknown keys | W1-01's compatibility policy and test corpus (§6 Q7); readers already ignore unknown keys |
| Plugin API growth (`shape.profilers`) | a group added after the v1 promise (W1-05) | additive; documented in `docs/plugins/api-v1.md`; `shape plugins doctor` covers it; §6 Q2 names the fallback |
| Unity Catalog required | `hive_metastore` workspaces cannot be profiled | a clear exit 2 naming the requirement; documented limitation |
| Fabric Warehouse `NOT ENFORCED` constraints | an unverified platform claim | shipped only after a Microsoft Learn source and date are in `docs/FABRIC_PLATFORM.md` (`tests/docs/test_fabric_platform.py` rejects unlisted surfaces) |
| Live accounts cost and flakiness | nightly failures unrelated to code | one run at a time; unique schema names; `finally` cleanup; failures name the variable, so a missing account is distinguishable from a bug |
| Scope creep into table-reading sources (`postgresql://...?table=`) | the packages grow past CR-0087 | Appendix C: out of scope; the connection modules make it a small later package |

---

## 6. Open questions

Each with options and a recommendation. None blocks PS-01.

1. **Where the core lives.** A: core `src/shape/profile/relational/` (pure Python). B: inside
   `shape-databases`. C: inside `shape-sqlserver`. **Recommendation: A** (§2.1): no new
   dependency, one implementation, the test kit runs without any driver.
2. **How the command finds a dialect.** A: a new entry-point group `shape.profilers` and
   `shape profile-db URI` in core. B: per-plugin commands (`profile-postgres`, ...). C: an
   optional `profile()` on `shape.sources` plugins found by `getattr`. **Recommendation: A**:
   one command, one help text; additive to API v1; B is the fallback if W1-05's promise is read
   as freezing the group list; C hides a capability behind duck typing.
3. **Row counts.** A: exact `count(*)` always. B: catalog estimates. C: exact by default,
   `--row-count estimate` opt-in. **Recommendation: A now, C later** when a user has the
   table that makes it necessary; the profile's `row_count` stays exact as documented.
4. **Snowflake `NUMBER(38,0)`.** A: `int64` when the sample fits, `DECIMAL(38,0)` otherwise,
   targets `BIGINT`. B: `decimal128(38,0)` always, targets `DECIMAL(38,0)`. **Recommendation:
   A**: it is what `INT` declares, and integer statistics and `BIGINT` keys are what a replica
   needs; the declared text is kept, so B's information is not lost.
5. **MySQL `tinyint(1)`.** A: boolean only when the sample holds nothing but 0, 1 and NULL.
   B: boolean always (MySQL's own alias). C: never. **Recommendation: A**; the facet keeps the
   declared text either way.
6. **Fabric Warehouse keys.** A: comment only (today). B: `ALTER TABLE ... NOT ENFORCED` behind
   `keys=declare`. C: B by default. **Recommendation: B**, after the Learn verification and the
   platform-inventory entry; a replica whose catalog carries the keys profiles back with
   `evidence: declared`, which is the point of CR-0087.
7. **Profile format.** A: additive fields without a version step. B: a minor format version
   step with a corpus entry. **Recommendation: whatever W1-01's policy says once it lands;
   until then A**, since every reader ignores unknown keys and the fields are optional.
8. **`numeric` without precision.** A: `decimal128(38, observed scale ≤ 9)`. B: `float64`.
   C: text. **Recommendation: A**: lossless for what was sampled, a real number type on every
   target, and bare `NUMERIC` back on PostgreSQL.
9. **Live accounts.** A: Azure Database for PostgreSQL and for MySQL flexible servers in the
   `fabric-live` tenant (same service principal; Entra tokens are already supported by
   `_auth.py`). B: any managed provider with a password. **Recommendation: A**; Snowflake and
   Databricks accounts are the owner's choice (§8).
10. **Nested and semi-structured values.** A: JSON text (dtype `string`, facet `nested`).
    B: skip the column. C: Arrow nested types in the profile. **Recommendation: A**: the
    statistics stay meaningful (null rate, cardinality, lengths), every target has a text or
    JSON type, and C has no profile model today. Databricks `VARIANT` as a target needs a
    runtime that has it; the dialect falls back to `STRING` and says so.
11. **Declared keys that are not enforced.** A: declared keys stay authoritative (as today)
    and carry `enforced: false`; the sample still runs the data pass and a contradiction is
    reported in a `sampled_integrity` note on the relationship. B: treat them as inferred.
    **Recommendation: A**: the designer's intent is the replica's contract; the note tells the
    reader when the data disagrees.

---

## 7. Estimates and order

One builder-day is one focused builder session with its tests, docs and review. Live
acceptance adds owner time (§8) but no builder time beyond the first run.

| WP | Title | Depends | Builder-days | Notes |
|---|---|---|---|---|
| PS-01 | Relational profiling core | none | 2.5 | the golden freeze is half the work |
| PS-02 | `profile-db URI`, `shape.profilers`, scenario kit, tape move | PS-01 | 2 | |
| PS-03 | PostgreSQL profiler | PS-02 | 2 | emulator exists |
| PS-04 | MySQL profiler | PS-02 | 2 | emulator exists |
| PS-05 | Snowflake profiler | PS-02 | 2.5 | tapes; `SHOW` parsing |
| PS-06 | Databricks profiler | PS-02 | 2.5 | tapes; type grammar |
| PS-07 | Type mapping and targets | PS-01 (+ rows from PS-03..06) | 2 | the page is the fixture |
| PS-08 | Live workflow and Fabric round trip | PS-03..07 | 1 | plus the owner's accounts |
| | **Total** | | **16.5** | |

Critical path: PS-01 → PS-02 → (PS-03, PS-04, PS-05, PS-06 in parallel, PS-07's target side in
parallel) → PS-08: about 8 builder-days of wall clock with four parallel lanes, 16.5 sequential.
The path ownership in Appendix B keeps the lanes from editing one another's files.

---

## 8. Owner actions

| ID | Action | Needed by | Fallback |
|---|---|---|---|
| O-PS-1 | A PostgreSQL server reachable from GitHub Actions (recommended: Azure Database for PostgreSQL flexible server in the `fabric-live` tenant, a database `shape_live`, a login or the service principal with `CREATE` on it); `SHAPE_LIVE_POSTGRES_URI` and the password in the `databases-live` environment | PS-03 live acceptance | the emulator tests prove the code; the live test fails by name until the account exists (that is the required behaviour) |
| O-PS-2 | The same for MySQL (Azure Database for MySQL flexible server); `SHAPE_LIVE_MYSQL_URI` and the password | PS-04 live acceptance | same |
| O-PS-3 | A Snowflake account with a database `SHAPE_LIVE`, a small warehouse with auto-suspend, a role that can create schemas there; `SHAPE_TEST_SNOWFLAKE_URI` and `SNOWFLAKE_PASSWORD` or `SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY` | PS-05 live acceptance and the first tapes | the fake proves the code; tapes cannot be recorded without the account |
| O-PS-4 | A Databricks workspace with Unity Catalog, a SQL warehouse with auto-stop, a catalog `shape_live` the token can create schemas in; `SHAPE_TEST_DATABRICKS_URI` and `DATABRICKS_TOKEN` | PS-06 live acceptance and the first tapes | same |
| O-PS-5 | The GitHub environment `databases-live` holding the Appendix A variables and secrets | PS-08 | the workflow runs and fails by name, which is visible and correct |
| O-PS-6 | Decisions on §6 Q4, Q6 and Q11 before PS-05, PS-07 and PS-01's `enforced` field respectively | as named | the recommendations apply until overruled |

---

## Appendix A: live-test variables

| Variable | Kind | Used by | Missing → |
|---|---|---|---|
| `SHAPE_LIVE_POSTGRES_URI` | variable | PS-03 live | `AssertionError: SHAPE_LIVE_POSTGRES_URI is not set (live tests need it)` |
| `SHAPE_POSTGRES_PASSWORD` (or an Entra token through `AZURE_*`) | secret | PS-03 live | the same message with its name |
| `SHAPE_LIVE_MYSQL_URI` | variable | PS-04 live | same pattern |
| `SHAPE_MYSQL_PASSWORD` | secret | PS-04 live | |
| `SHAPE_TEST_SNOWFLAKE_URI` | variable | PS-05 live (and today's sink live test) | |
| `SNOWFLAKE_PASSWORD` or `SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY` | secret | PS-05 live | one of the two; the test names both |
| `SHAPE_TEST_DATABRICKS_URI` | variable | PS-06 live (and today's sink live test) | |
| `DATABRICKS_TOKEN` | secret | PS-06 live | |
| `FABRIC_WAREHOUSE_CONNECTION_STRING`, `FABRIC_SQL_CONNECTION_STRING` | derived / secret (exist in `fabric-live`) | PS-08 Fabric round trip | |
| `SHAPE_RECORD_DIR` | variable | PS-05, PS-06 | optional: tapes are written only when set |

Nothing is skipped by a decorator; a missing value fails its test by name, as in
`plugins/shape-fabric/tests/test_live_commands.py`. Today's skipping `need()` in
`plugins/shape-databases/tests/test_live.py` is replaced by the asserting one in PS-05 (same
file, same variables), so the sink live tests fail by name too.

## Appendix B: path ownership

| Path | Owner |
|---|---|
| `src/shape/profile/relational/{__init__,catalog,dialect,profiler}.py`, `scripts/profile_db_golden.py`, `plugins/shape-sqlserver/tests/golden/` | PS-01 |
| `src/shape/profile/relational/types.py` | PS-01 (model), PS-07 (grammar) |
| `src/shape/profile/relational/testing.py`, `src/shape/testing/recording.py`, `src/shape/cli/profile_db.py`, `src/shape/plugins/api/v1.py`, `src/shape/plugins/registry.py`, `tests/cli/test_profile_db.py` | PS-02 |
| `plugins/shape-sqlserver/src/shape_sqlserver/*` | PS-01 (profiler, catalog, sql), PS-02 (command, pyproject) |
| `plugins/shape-databases/src/shape_databases/{postgres,postgres_connection,profile_postgres}.py`, its tests, `docs/databases/postgresql.md` | PS-03 |
| `.../{mysql,mysql_connection,profile_mysql}.py`, its tests, `docs/databases/mysql.md` | PS-04 |
| `.../{snowflake,snowflake_connection,profile_snowflake}.py`, its tests and tapes, `docs/databases/snowflake.md` | PS-05 |
| `.../{databricks,databricks_connection,profile_databricks}.py`, its tests and tapes, `docs/databases/databricks.md` | PS-06 |
| `plugins/shape-databases/src/shape_databases/testing.py` | PS-03..06 each add their own class; no shared edits |
| `src/shape/profile/relational/targets.py`, `docs/databases/TYPE_MAPPING.md`, `tests/profile/relational/test_type_matrix.py`, the `sql_type` seam in the three DDL writers | PS-07 |
| `.github/workflows/databases-live.yml`, `plugins/shape-fabric/tests/test_live_type_mapping.py`, `docs/CI.md` | PS-08 |
| `.github/workflows/nightly.yml` (`databases-e2e` job only) | PS-03 and PS-04, one line each |
| `docs/SOURCES.md`, `docs/SINKS.md`, `plugins/shape-databases/README.md` | one row or paragraph per package, appended, never rewritten |

## Appendix C: scope boundary

In scope: whole-database profiling sources for the four platforms with profile-db's output,
and the type mapping with its tests. Out of scope, deliberately: table-reading
`shape.sources` for `postgresql://`, `mysql://`, `snowflake://` and `databricks://` (the
connection modules make that a small later package); the replica generation flow itself
(premium, CR-0087's private side); SQL Server and Fabric Warehouse profiling changes beyond
the extraction; estimate-based row counts (§6 Q3); any change to a plan decision, gate or
tolerance.
