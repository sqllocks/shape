# Install Shape

Set up the command line for the local starter tutorials.

Status: available.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Output appears beneath each command. `…` elides the wheel byte size and, where shown, elapsed times, session IDs and timestamps. These values vary between builds or runs. The harness validates those fields and compares the remaining output exactly. Temporary paths, job IDs and generated signing keys also vary.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" INSTALL
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for INSTALL
    ```


**Early access 0.9.3.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

Supported Python: 3.11–3.14, the versions exercised by the CI matrix. Install Shape and its domain plugin:

```bash
pip install "sqllocks-shape[domains]"
```


## Extras

[Run this example](#local-example-3).


Core declares `domains` for domain schemas, `duckdb` for the local database adapter,
`postgres` and `mysql` for database drivers, and `fabric` for Fabric commands and writers.
The Snowflake and Databricks drivers are extras of `sqllocks-shape-databases`.
The [database pages](databases/index.md) explain their behavior.
Use only the extras your workflow needs. Installing a plugin gives its code the same
process permissions as core; see [What leaves my machine](WHAT_LEAVES.md).

## Alpine and musllinux

`confluent-kafka` has no musllinux wheels for ARM64 or x86_64, so pip builds it
from source on Alpine Linux. The `[kafka]`, `[streaming]` and `[all]` extras need
librdkafka **2.16 or newer**, its development headers and C build tools.
Alpine's `librdkafka-dev` package currently provides an older version.

Run these commands as root in your Alpine environment before installing Shape.
They build librdkafka 2.16.0 with the same dependencies and source checksum as
`scripts/release_smoke_alpine.sh`. `unixodbc-dev` also supports the SQL Server
driver included in `[all]`.

```sh
apk add --no-cache bash libstdc++ build-base unixodbc-dev curl openssl-dev \
  cyrus-sasl-dev zstd-dev lz4-dev curl-dev
curl --fail --silent --show-error --location \
  https://github.com/confluentinc/librdkafka/archive/refs/tags/v2.16.0.tar.gz \
  --output /tmp/librdkafka.tar.gz
echo 'e6b61de61d3282879a88e4ee3d9f634a8b05bf78a9198dfc25c4820c0c9ed231  /tmp/librdkafka.tar.gz' | sha256sum -c -
tar -xzf /tmp/librdkafka.tar.gz -C /tmp
(
  cd /tmp/librdkafka-2.16.0
  ./configure --prefix=/usr
  make -j2
  make install
)
python -m pip install "sqllocks-shape[all]"
```

For other musllinux environments, follow
[Confluent's librdkafka build instructions](https://github.com/confluentinc/librdkafka#building)
to install the library and development headers before installing the extras.

## Offline installation locks

`python scripts/offline_lock.py generate LOCKDIR` writes a hashed core dependency
lock and one `requirements-<extra>.txt` per extra (`yaml`, `sign`, `delta`, `delta-fallback`, `excel`, `azure`, `pandas`, `pytest`, `scipy`, `advanced`, `faker`, `ctgan`, `kafka`, `eventhubs`, `fabric`, `sqlserver`, `domains`, `simulation`, `dbt`, `healthcare`, `integrations`, `all`, `postgres`, `mysql`, `databases`, `duckdb`, `docs`, `streaming`, `dev`).
Run `python scripts/offline_lock.py check LOCKDIR` to verify the lock files before
using them for an offline installation. Resolve and download dependencies while
network access is available; generating the locks needs a package index.

## Related

[Start here](LEARNING_PATHS.md) · [Troubleshooting](TROUBLESHOOTING.md)

[Run this example](#local-example-4).

[Run this example](#local-example-5).


## Example

[Run this example](#local-example-0).

## Local example

[Run this example](#local-example-1).

## Local example

[Run this example](#local-example-2).


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
python -m venv --copies --system-site-packages install-env
. install-env/bin/activate
python -m pip install --quiet --no-deps --no-build-isolation -e "$SHAPE_DOCS_REPO"
shape doctor
shape conformance
```

??? info "Output (exit 0)"

    ```text {.expected}
    Shape 0.9.3
      python    3.12.14  (Linux-6.18.44-x86_64-with-glibc2.41)
      kernel    python  (pure Python; set SHAPE_KERNEL=rust)

    Required
      OK      numpy         2.5.3
      OK      pyarrow       25.0.1

    Optional
      OK      cryptography  50.0.2
      OK      yaml          6.0.3
      OK      pandas        3.0.6
      OK      scipy         1.18.1
      OK      deltalake     1.6.6
      OK      openpyxl      3.1.5
      OK      sklearn       1.9.1
      OK      tzdata        2026.3

    Result: OK
    [{"detail": "", "name": "artifact", "passed": true}, {"detail": "", "name": "capture", "passed": true}, {"detail": "", "name": "generation", "passed": true}, {"detail": "", "name": "SH2-001", "passed": true}, {"detail": "", "name": "SH2-002", "passed": true}, {"detail": "", "name": "SH2-003", "passed": true}, {"detail": "", "name": "SH2-004", "passed": true}, {"detail": "", "name": "SH2-005", "passed": true}, {"detail": "", "name": "SH2-006", "passed": true}, {"detail": "", "name": "SH2-007", "passed": true}, {"detail": "", "name": "SH2-008", "passed": true}, {"detail": "", "name": "SH2-009", "passed": true}, {"detail": "", "name": "SH2-010", "passed": true}, {"detail": "", "name": "SH2-011", "passed": true}, {"detail": "", "name": "SH2-012", "passed": true}, {"detail": "", "name": "SH2-013", "passed": true}, {"detail": "", "name": "SH2-014", "passed": true}, {"detail": "", "name": "SH2-015", "passed": true}, {"detail": "", "name": "SH2-016", "passed": true}, {"detail": "", "name": "SH2-017", "passed": true}, {"detail": "", "name": "SH2-018", "passed": true}, {"detail": "", "name": "SH2-019", "passed": true}, {"detail": "", "name": "SH2-020", "passed": true}, {"detail": "", "name": "SH2-021", "passed": true}, {"detail": "", "name": "SH2-022", "passed": true}, {"detail": "", "name": "SH2-023", "passed": true}, {"detail": "", "name": "SH2-024", "passed": true}, {"detail": "", "name": "SH2-025", "passed": true}, {"detail": "", "name": "SH2-026", "passed": true}, {"detail": "", "name": "SH2-027", "passed": true}, {"detail": "", "name": "SH2-028", "passed": true}, {"detail": "", "name": "SH2-029", "passed": true}, {"detail": "", "name": "SH2-030", "passed": true}, {"detail": "", "name": "SH2-031", "passed": true}, {"detail": "", "name": "SH2-032", "passed": true}]
    ```

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
pip install --quiet --no-deps --no-build-isolation -e "$SHAPE_DOCS_REPO/plugins/shape-kafka" -e "$SHAPE_DOCS_REPO/plugins/shape-eventhubs"
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
pip install --quiet --no-deps --no-build-isolation -e "$SHAPE_DOCS_REPO/plugins/shape-dbt"
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

<a id="local-example-3"></a>

### Example 4

<!-- example: 3 -->

```bash {.runnable-reference}
pip install --quiet --no-deps "sqllocks-shape[faker]"
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

<a id="local-example-4"></a>

### Example 5

<!-- example: 4 -->

```bash {.runnable-reference}
python scripts/build_pure_wheel.py --out wheels
pip wheel --quiet --no-build-isolation --no-deps -w wheels plugins/shape-domains plugins/shape-fabric \
    plugins/shape-eventhubs plugins/shape-sqlserver
pip install --quiet --no-deps --find-links wheels wheels/sqllocks_shape-*.whl \
    wheels/sqllocks_shape_domains-*.whl wheels/sqllocks_shape_fabric-*.whl
```

??? info "Output (exit 0)"

    ```text {.expected}
    sqllocks_shape-0.9.3-py3-none-any.whl  …
    checks passed: tag py3-none-any, < 28,600,000 bytes, no compiled code, RECORD valid
    ```

<a id="local-example-5"></a>

### Example 6

<!-- example: 5 -->

```bash {.runnable-reference}
pip install --quiet --no-deps --find-links wheels "sqllocks-shape-fabric[sqlserver,eventhubs]"
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```
