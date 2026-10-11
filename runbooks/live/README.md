# Live test playbooks (Shape 0.9.1)

One playbook per platform under `runbooks/live/`. Each gives the prerequisites, where the
credentials go (environment variable names only), the exact install line, numbered test steps
with the command and what success looks like, cleanup and cost, and a results table. Every
command was checked against the plugin code and docs on `release/0.9.1` (2026-10-11); where a
step has no Shape command, the playbook says so under "Gaps" rather than inventing one.

## Recommended order (cheapest and quickest first)

| # | Platform | Playbook | Shape surface at 0.9.1 | Time | Cost | Why this position |
|---|---|---|---|---|---|---|
| 1 | PostgreSQL | `postgresql.md` | write (`--to`), seed, stream; profile via export | 30 min | none (Docker) | free, local, exercises the database sink end to end |
| 2 | MySQL | `mysql.md` | write, seed, stream; profile via export | 30 min | none (Docker) | same harness, second dialect |
| 3 | SQL Server / Azure SQL | `azure-sql.md` | `profile-db`, `mssql://` source, write, upsert | 45 min | none (Docker) or cents (Basic tier) | the only released live **source**; needs the ODBC driver |
| 4 | Azure Event Hubs | `eventhubs.md` | `stream-profile`, emit | 45 min | under 1 USD (Basic, 1 TU) | first streaming check; emulator available |
| 5 | Kafka (Confluent Cloud) | `kafka.md` | `stream-profile`, emit (emulator only from the CLI) | 45 min | cents (Basic cluster) | second transport; exposes the SASL gap |
| 6 | ADLS Gen2 | `adls.md` | profile `abfss://`, write files and Delta, stream files | 30 min | under 1 cent | cloud storage path shared with OneLake |
| 7 | Microsoft Fabric | `fabric.md` | Lakehouse profile and write, Warehouse `profile-db` and `COPY INTO`, Eventhouse and Eventstream emit | 2 h | none (trial) or about 0.36 USD/h (F2, pause it) | the main target; four surfaces in one capacity |
| 8 | Snowflake | `snowflake.md` | write (`COPY INTO` from the table stage); profile via unload | 45 min | under 1 USD (X-Small, auto-suspend) | cloud warehouse sink |
| 9 | Databricks | `databricks.md` | write (bound `INSERT`s into Unity Catalog); profile via export | 45 min | a few USD (2X-Small serverless) | cloud warehouse sink |
| 10 | Azure Synapse dedicated pool | `synapse.md` | write (`COPY INTO` from ADLS); `profile-db` expected to work | 1 h | about 1.20 USD/h (DW100c, pause it) | most expensive per hour |
| 11 | BigQuery | `bigquery.md` | **no plugin**: core on exported Parquet | 45 min | free tier | documents the gap (W9-13) |
| 12 | Amazon Redshift | `redshift.md` | **no plugin**: core on unloaded Parquet | 1 h | a few USD (Serverless 8 RPU) | documents the gap (W9-14) |

Times assume the account and tools already exist. Cost figures are list-price orders of
magnitude for the playbook's few minutes of compute; every playbook ends with the command that
stops the meter and the check that proves it stopped.

Other released surfaces: DuckDB (`duckdb://`, local file, covered by the plugin's own tests, not
live), dbt (file based, `docs/DBT.md`), Fabric SQL database and semantic models (in `fabric.md`).

## Install

Shape's plugins are not on PyPI at 0.9.1, and on 2026-10-11 PyPI serves `sqllocks-shape` 0.9.0
only; `v0.9.1` has no tag or GitHub release yet. Every playbook therefore installs from the
0.9.1 wheel set in `$SHAPE_WHEELS`. Build it once from the release branch (as `docs/INSTALL.md`
describes), or point `$SHAPE_WHEELS` at the release's assets once they are published:

```bash
git clone https://github.com/sqllocks/shape && cd shape && git checkout release/0.9.1
python -m venv .venv && . .venv/bin/activate && pip install -U pip
python scripts/build_pure_wheel.py --out wheels
pip wheel --no-deps -w wheels plugins/shape-domains plugins/shape-databases plugins/shape-sqlserver \
    plugins/shape-fabric plugins/shape-kafka plugins/shape-eventhubs
export SHAPE_WHEELS="$PWD/wheels"
```

The pure wheel (`py3-none-any`) runs the Python kernel (`SHAPE_KERNEL=python`); results are
the same as with the Rust kernel (T-03 differential tests). Then run the playbook's own
`pip install --find-links "$SHAPE_WHEELS" ...` line in a fresh virtual environment per
platform. `shape --version` must print `shape 0.9.1`.

## Before you start

- Set `SHAPE_CONFIRM_REMOTE=1` (or pass `--yes`): every write to a non-local target asks first
  and exits 2 otherwise (`docs/SINKS.md`, "Confirming a non-local target").
- Secrets are environment variables or `env://`, `file://` (mode 600), `kv://` references;
  a secret-looking literal on the command line is refused. Never paste a value into a
  playbook, a results table or an issue.
- Work in an empty directory per platform; profiles written by `shape profile` are safe
  captures (statistics and formats) unless you pass `--capture full`, which the playbooks do
  only on synthetic data.
- Use the platform's dedicated test schema, database or folder named in the playbook, and run
  the cleanup section even when a step fails.

## When a step fails

1. **Read the exit code.** `2` is bad input, a refused option or a failed connection (the
   message names what; it never contains a secret); `1` is a check that ran and found
   something (a violated rule, drift, a breaking change); any other code is a crash. The
   per-command exit codes are in `docs/EXIT_CODES.md` and in each command's `--help`.
2. **Rerun with `--debug`** (`shape --debug <command> ...`) for the traceback, and with
   `--dry-run` where the command has it, to see what it would do without connecting.
3. **Check the environment.** `shape doctor` (and `shape doctor --fabric` / `--broker` for the
   cloud targets), `shape plugins list` for the entry point the step needs,
   `shape plugins doctor` for a plugin that fails to load, the driver (`odbcinst -q -d`) for
   SQL Server targets.
4. **Separate the platform from Shape.** Run the platform's own client (`psql`, `bq`, a KQL
   query) with the same identity; a permission or network error there is not a Shape failure.
5. **Decide whether it is a gap.** If the playbook's "Gaps" section already lists it, record
   it in the results table as the known gap. Otherwise it is a finding.
6. **Report** a finding as a GitHub issue on `sqllocks/shape` with: the platform and playbook
   step, the exact command with secrets replaced by the variable names, the exit code and the
   last 20 lines of output (redact hosts if needed), `shape --version`, the plugin version,
   Python and OS, and what the platform's own client showed. Attach the results table. Label it
   `platform coverage`. A failure of a step the "Gaps" section attributes to a W9 work package
   goes as a comment on that package's issue (#2 to #17) instead of a new issue.
7. **Keep going.** A failed step does not block the later ones unless they consume its
   output; mark the dependent steps "blocked by step N" in the results table.

## Results

Fill the table at the end of each playbook and keep it with the issue or the release notes.
Pass means the step's "Success" condition was met exactly; anything else is Fail with a note.
