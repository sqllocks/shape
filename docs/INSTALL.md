# Install Shape

Supported Python: 3.11–3.13.

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

## Offline and air-gapped installs

Shape does not require raw data to leave the environment, and it downloads nothing at run time.
Every reference data file a feature reads (name pools, JSON schemas, the Spark worker notebook,
the requirement statements) is inside the wheel; `python scripts/check_shipped_data.py --wheel
dist/*.whl` fails the build if one is missing or a module outside the explicit HTTP transport
(`shape.scale.http`) imports a network client. A command that fetches (an emitter or sink you
point at a service) says so in its own documentation.

To install into a disconnected environment:

1. **Take the pinned lock files.** CI's `offline-lock` job writes `requirements-core.txt` and one
   `requirements-<extra>.txt` per extra (`yaml`, `sign`, `delta`, `excel`, `azure`, `pandas`,
   `scipy`, `advanced`, `ctgan`, `kafka`, `eventhubs`, `sqlserver`, `domains`, `simulation`,
   `streaming`, `dev`) as the `offline-lock` artifact. Each file pins every package, including
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
