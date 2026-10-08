# Install Shape

Supported Python: 3.11–3.14.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install sqllocks-shape
shape doctor
shape conformance
```

Optional streaming transports (`shape stream-profile`, `docs/plugins/streaming.md`):

```bash
pip install 'sqllocks-shape[kafka]'
pip install 'sqllocks-shape[eventhubs]'
```

dbt (`shape from-dbt`, `shape to-dbt-tests`, `shape dbt-seeds`, `shape dbt-report`, `docs/DBT.md`); it
reads and writes dbt's files and does not install dbt:

```bash
pip install sqllocks-shape-dbt
```

Generating from a learned schema (`shape learn`, `shape generate`, `shape demo` inference) can use
fake-data providers for columns such as names, e-mail addresses and states; they need `faker`:

```bash
pip install 'sqllocks-shape[faker]'
```

## Plugins from the release's wheels

The first-party plugins (`sqllocks-shape-domains`, `sqllocks-shape-fabric`, ...) are not on PyPI
yet, and the `sqllocks-shape` 0.9.0 on PyPI is the early-access profiler. Install the release's
wheels instead: build them from the release checkout (or take them from the release), then point
pip at their folder with `--find-links`. Third-party dependencies still come from PyPI.

```bash
python scripts/build_pure_wheel.py --out wheels
pip wheel --no-deps -w wheels plugins/shape-domains plugins/shape-fabric \
    plugins/shape-eventhubs plugins/shape-sqlserver
pip install --find-links wheels wheels/sqllocks_shape-*.whl \
    wheels/sqllocks_shape_domains-*.whl wheels/sqllocks_shape_fabric-*.whl
```

The domains plugin brings `faker`. The Fabric plugin needs only the core for the Lakehouse and
Eventhouse targets, the notebooks and its commands. Two parts are extras of it:

| Extra | Brings | For |
|---|---|---|
| `sqllocks-shape-fabric[sqlserver]` | `sqllocks-shape-sqlserver`, `pyodbc` (on Linux also the `unixodbc` system package and the Microsoft ODBC Driver 18) | the SQL database, Warehouse, Synapse and SQL Server targets |
| `sqllocks-shape-fabric[eventhubs]` | `sqllocks-shape-eventhubs`, `azure-eventhub` | the Eventstream emitter and writer |

```bash
pip install --find-links wheels 'sqllocks-shape-fabric[sqlserver,eventhubs]'
```

Without an extra, the part that needs it fails with the line to run, for example `the SQL
database, Warehouse, Synapse and SQL Server targets need the sqllocks-shape-sqlserver plugin,
which is not installed: pip install 'sqllocks-shape-fabric[sqlserver]'`. `shape demo` is described
in `docs/DEMO.md`.

## Offline and air-gapped installs

Shape does not require raw data to leave the environment, and it downloads nothing at run time.
Every reference data file a feature reads (name pools, JSON schemas, the Spark worker notebook,
the requirement statements) is inside the wheel; `python scripts/check_shipped_data.py --wheel
dist/*.whl` fails the build if one is missing or a module outside the explicit HTTP transport
(`shape.scale.http`) imports a network client. A command that fetches (an emitter or sink you
point at a service) says so in its own documentation.

To install into a disconnected environment:

1. **Take the pinned lock files.** CI's `offline-lock` job writes `requirements-core.txt` and one
   `requirements-<extra>.txt` per extra (`yaml`, `sign`, `delta`, `delta-fallback`, `excel`,
   `azure`, `pandas`, `pytest`, `scipy`, `advanced`, `faker`, `ctgan`, `kafka`, `eventhubs`, `fabric`,
   `sqlserver`, `domains`, `simulation`, `dbt`, `healthcare`, `integrations`, `all`, `postgres`,
   `mysql`, `databases`, `duckdb`, `streaming`, `docs`, `dev`) as the `offline-lock`
   artifact. A plugin extra's file holds the plugin's third-party dependencies, including those
   of the plugin extras it names (`postgres` holds the PostgreSQL driver). Each file pins every package, including
   transitive ones, to one version with `sha256` hashes (Python 3.11 and up, all platforms). Each
   extra's file also holds core. Generate them yourself with
   `pip install uv packaging && python scripts/offline_lock.py generate lock/`.
2. **Check them against the declared dependencies** (no network needed):
   `python scripts/offline_lock.py check lock/`. It fails when a file is missing, an entry is not
   pinned or has no hash, or a dependency in `pyproject.toml` is absent from the lock or locked
   outside its declared range.
3. **Build the wheelhouse in a connected enclave** for the platform you install on:
   `pip download --require-hashes -r lock/requirements-core.txt -d wheelhouse` (add the files of
   the extras you need, or use the one file for the largest extra), and build Shape and any
   first-party plugin wheels (`python -m build --wheel`, with `plugins/shape-*` for the plugins)
   into the same folder. First-party packages are not in the lock files.
4. **Transfer the wheelhouse and the lock files** through the organization's approved process.
5. **Install without an index:**
   `pip install --no-index --find-links wheelhouse --require-hashes -r requirements-core.txt`,
   then `pip install --no-index --find-links wheelhouse --no-deps sqllocks-shape`.

The test suite proves the offline claim: every test that needs no network carries the
`zero_network` marker (applied automatically to all but `emulator` and `live` tests), and a
fixture in `tests/conftest.py` fails any connection that leaves the machine or any name lookup
for a non-loopback host. CI also runs that set inside an empty network namespace
(`make zero-network` locally, Linux only).
