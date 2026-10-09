# `shape demo`: demos for talks, clients and workshops

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Output appears beneath each command. `…` elides the wheel byte size and, where shown, elapsed times, session IDs and timestamps. These values vary between builds or runs. The harness validates those fields and compares the remaining output exactly. Temporary paths, job IDs and generated signing keys also vary.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" DEMO
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for DEMO
    ```


`shape demo` runs a **scenario** in one of three **modes** and records every run as a **session**
that can be reported on and cleaned up.

[Run this example](#local-example-0).


Install Shape with `pip install "sqllocks-shape[domains]"` for local domain scenarios.
Fabric scenarios also need the Fabric plugin and a Fabric account. The commands below show
how to build and install wheels from this checkout for a source run.

[Run this example](#local-example-1).


That is enough for every scenario and mode, the Lakehouse and Eventhouse targets and the
notebooks. The domains plugin brings `faker`, which the `healthcare` scenario's inference mode
needs (its learned schema uses fake-data providers). The SQL database and Warehouse targets need
the Fabric plugin's `[sqlserver]` extra (the SQL Server plugin and `pyodbc`; on Linux also the
`unixodbc` system package and the Microsoft ODBC Driver 18), and the Eventstream emitter its
`[eventhubs]` extra. Both come from the same wheels folder:

[Run this example](#local-example-2).


Without an extra, the target that needs it fails and names the extra to install.

## Scenarios

| scenario | modes | domains | default rows |
|---|---|---|---|
| `retail` | inference, streaming, seeding | retail | 100,000 |
| `adventureworks` | inference, seeding | retail (its tables, under the AdventureWorks talk title) | 50,000 |
| `healthcare` | inference, streaming, seeding | healthcare | 50,000 |
| `enterprise` | seeding | retail, hr, financial | 200,000 |

A scenario runs **its own domains**. `--domain NAME` runs one other domain and `--domains A,B,C`
a composite (each name is an installed domain or a generation schema file). A domain that is
not installed fails the run and names it.

`--rows N` picks the scale preset that is generated: `small` up to 2,000, `medium` up to 50,000,
`large` up to 500,000, `xlarge` above. It is not an exact count: the tables have the rows of the
preset, and the run, `--dry-run` and `--estimate` print the rows of the preset (`small scale
preset: 21,750 rows`). The presets are much larger than the number that picks them; for the
`retail` domain, all tables together:

| `--rows` | preset | rows generated (retail) |
|---|---|---:|
| up to 2,000 | `small` | 21,750 |
| up to 50,000 | `medium` | 1,965,400 |
| up to 500,000 (the scenario's default, 100,000) | `large` | 19,625,400 |

These are the seeding counts. Inference generates from the schema it learns, whose presets
are smaller (for `retail`, 21,800 rows at `small` and 2,180,000 at `large`); its `--dry-run`
learns that schema to count them. On stage, give `--rows 1000`: the default generates the
`large` preset. Streaming always generates the `small` preset.

## Modes

* **inference**: profiles the source (the domain's own small dataset, or `--input-file` with a CSV,
  Parquet or JSON Lines file, or `--input-file live-db` with a connection profile that names a
  Warehouse or SQL database), learns a generation schema from it, generates a synthetic dataset
  and compares the two column by column. A column passes when its null rate is within five points
  and, unless it is a key, a date, a float or a unique pattern, its cardinality is close. The
  score is the share of columns that pass. `--output` takes `terminal` (the report, the
  default), `charts` (one self-contained HTML page), `semantic_model` (a Power BI `.bim` of
  the learned schema) or `all`; files go to `--output-dir` (the working folder). The page shows
  the value shares of small categorical columns, except for a column the safe profile classifies
  (a personal-data pattern, or nearly every value distinct): that column shows its null rate and
  distinct count, never its values (#663). A file that is already there (`<scenario>_charts.html`,
  `<scenario>_model.bim`) is never overwritten: the run fails and says so, so remove it (or clean
  up the session that wrote it) or choose another folder.
* **seeding**: generates the scenario and writes it to the targets of a connection profile
  (below). With no profile the rows are generated and counted and nothing is written.
  `--scale-mode local` runs here; `spark` submits the run to a Fabric Spark notebook that writes
  Delta tables to the Lakehouse; `auto` (the default) picks `spark` when the profile has a
  Lakehouse and the rows reach 500,000.
* **streaming**: generates a small dataset of one domain and streams, as JSON lines, the events of
  the first table (in dependency order) that has an event time (a date or timestamp column), in
  event-time order (`--max-events`, default 100). When no table has one, the first table is streamed in row
  order and its events carry no `_shape_event_time`. Nothing is written to a target.
  Inference and streaming take one domain; `--domains` with more is refused.

`--dry-run` plans a run and `--estimate` prints the cost estimate; neither generates anything, in
any mode.

Every scenario generates reserved identifiers (e-mail and URI hosts, phone numbers and social
security numbers that cannot belong to a real person). `--identifiers realistic` (the setting
`identifiers` of `demo_run`) turns realistic ones on for the run, in every mode, says so once on
standard error and is kept in the session record; never use such data outside a test system.

## Connection profiles

`shape demo init --name NAME` saves a profile in `connections.json` under `$SHAPE_HOME` (default
`~/.shape`), readable by its owner only. Its settings are the targets:

| setting | target |
|---|---|
| `--local-path DIR` | Parquet files in `DIR/<session>/<table>/` |
| `--workspace-id`, `--lakehouse-id` | a Lakehouse: Parquet files under `Files/shape_demo/<session>/` |
| `--warehouse-conn`, `--warehouse-staging-path` | a Warehouse, loaded with `COPY INTO` from the staging folder |
| `--sql-db-conn` | a SQL database |
| `--eventhouse-uri`, `--eventhouse-database` | an Eventhouse |
| `--auth cli\|msi\|spn\|sql\|device-code\|fabric`, `--tenant-id`, `--client-id`, `--client-secret` | the sign-in |

**A profile never holds a secret.** `--client-secret` takes a credential reference
(`env://NAME`, `file://PATH` or `kv://VAULT/SECRET`), a connection string may be a reference too,
and one that holds a password or key is refused. `shape demo preflight` connects to every
target and says what it found; a target it cannot reach is a failure (exit 1), and a target
whose settings are incomplete is a failure, never a pass.

A table is written in the default write mode, which refuses a table that already exists: a demo
never overwrites your data. A run that fails is rolled back, and says how many artifacts it
removed. A composite run puts each domain in its own SQL schema (named for the domain), folder
and KQL table prefix.

## Sessions and cleanup

`shape demo run` ends with `Session: ID`. The record (`$SHAPE_HOME/sessions/demo-ID.json`) lists
every artifact (target, name, rows, where) and the metrics. `shape demo cleanup ID` removes
exactly those:

* a local folder only inside the session folder the run made (it holds a marker with the session
  id), and a single file (a chart, a model) only inside the output folder the record names;
* a table by its recorded `schema.table`, quoted; a KQL table by name; OneLake files by path.

It lists what it removed, what it left alone and why, and what it could not remove (exit 1).
`--dry-run` lists what it would remove, and what it would leave alone and why.

## From Python

The commands call plain functions (`shape.demo`), which return JSON-safe dicts:

```python
from shape.demo import demo_run, demo_status, demo_cleanup

result = demo_run({"scenario": "retail", "mode": "seeding", "rows": 1000})
demo_status(result["session_id"])["manifest"]["artifacts"]
demo_cleanup(result["session_id"])
```

| function | returns |
|---|---|
| `demo_list()` | `{"scenarios": [...], "count": n}` |
| `demo_run(params, *, runtime=None)` | `success`, `session_id`, `scenario`, `mode`, `fidelity_score`, `error`, `artifact_count`; a Spark run adds `fabric_run_id`, `status`; `scale_mode` |
| `demo_status(session_id, *, token=None, runtime=None)` | `{"session_id", "manifest", ["fabric"]}`; a Spark run adds the live job status |
| `demo_cleanup(session_id, *, dry_run=False, connection=None, runtime=None)` | `removed`, `failed`, `skipped`, `ok` |
| `demo_preflight(connection=None, *, runtime=None)` | `profiles`: checks with `ok`, `fail` or `skipped`; `ok` |
| `demo_init(name, *, ..., runtime=None)` | `{"name", "path", "targets"}` |
| `demo_notebook(scenario, mode="inference", output=None)` | `{"path", ...}` |
| `demo_report(session_id, fmt="md", output=None, *, runtime=None)` | `content`, `path` |

An input an operation cannot use raises `DemoError` (a `ValueError`); a missing session raises
`SessionNotFoundError`. A run that fails is a result with `success: false`. `DemoRuntime` carries
where messages go (`out`), where state lives and the network seams.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape demo list
shape demo run retail --rows 1000 --json > inference-session.json
shape demo init --name here --local-path ./landing
shape demo run retail --mode seeding --connection here --rows 1000 --json > seed-session.json
SESSION=$(python -c 'import json; print(json.load(open("seed-session.json"))["session_id"])')
shape demo status "$SESSION"
shape demo report "$SESSION" --format html --output report.html
shape demo cleanup "$SESSION"
shape demo notebook retail --mode seeding --output retail.ipynb
```

??? info "Output (exit 0)"

    ```text {.expected}
    Name             Modes                          Domains                  Default rows  Description
    retail           inference, streaming, seeding  retail                        100,000  Retail domain — customers, products, orders, order lines.
    adventureworks   inference, seeding             retail                         50,000  Conference 'Retire AdventureWorks' demo: the retail domain's...
    healthcare       inference, streaming, seeding  healthcare                     50,000  Healthcare domain — patients, encounters, medications, claim...
    enterprise       seeding                        retail, hr, financial         200,000  Enterprise composite — retail + hr + financial across all 4 ...
    === Shape Demo — retail (inference) ===
      >> […] Profiling source data: domain defaults
         Profiled 9 table(s)
         Built schema: 9 tables
      >> […] Generating synthetic data: small scale preset: 21,800 rows
         Generated 21,800 total rows
      >> […] Comparing distributions
         Fidelity score: 96.7%
    Fidelity Report
    Table                Column                    Type        Pass
    -----------------------------------------------------------------
    address              address_id                integer       OK
    address              address_type              string        OK
    address              city                      string      FAIL
    address              customer_id               integer       OK
    address              is_primary                boolean       OK
    address              lat                       float         OK
    address              lng                       float         OK
    address              state                     string        OK
    address              street                    string        OK
    address              zip_code                  integer       OK
    customer             customer_id               integer       OK
    customer             email                     string        OK
    customer             first_name                string        OK
    customer             gender                    string        OK
    customer             is_active                 boolean       OK
    customer             last_name                 string        OK
    customer             loyalty_tier              string        OK
    customer             signup_date               datetime      OK
    order                customer_id               integer       OK
    order                order_date                datetime      OK
    order                order_id                  integer       OK
    order                order_total               float         OK
    order                promotion_id              integer       OK
    order                shipping_address_id       integer       OK
    order                status                    string        OK
    order                store_id                  integer       OK
    order_line           discount_percent          integer       OK
    order_line           line_total                float         OK
    order_line           order_id                  integer       OK
    order_line           order_line_id             integer       OK
    order_line           product_id                integer       OK
    order_line           promotion_id              integer       OK
    order_line           quantity                  integer       OK
    order_line           unit_price                float         OK
    product              category_id               integer       OK
    product              cost                      float         OK
    product              product_id                integer       OK
    product              product_name              string        OK
    product              product_status            string        OK
    product              unit_price                float         OK
    product_category     category_id               integer       OK
    product_category     category_name             string      FAIL
    product_category     level                     integer       OK
    product_category     parent_category_id        integer       OK
    promotion            discount_pct              integer       OK
    promotion            end_date                  datetime      OK
    promotion            promo_name                string        OK
    promotion            promo_type                string        OK
    promotion            promotion_id              integer       OK
    promotion            start_date                datetime      OK
    return               order_id                  integer       OK
    return               reason                    string        OK
    return               refund_amount             float         OK
    return               return_date               datetime      OK
    return               return_id                 integer       OK
    store                city                      string        OK
    store                state                     string        OK
    store                store_id                  integer       OK
    store                store_name                string        OK
    store                store_type                string        OK

    Fidelity score: 96.7%
      >> […] Complete
    === Done in …s ===
    Connection profile 'here' saved. Use with: shape demo run SCENARIO --connection here
    === Shape Demo — retail (seeding) ===
      >> […] Generating synthetic data: small scale preset: 21,750 rows (local)
         retail: 21,750 rows in 9 tables
      >> […] Complete
    === Done in …s ===
    Session: …
    Scenario: retail (seeding)
    Status: Success
    Started: …
    Artifacts: 9
    Report written to: report.html
      Removed: file/customer
      Removed: file/product_category
      Removed: file/promotion
      Removed: file/store
      Removed: file/address
      Removed: file/order_line
      Removed: file/order
      Removed: file/product
      Removed: file/return
    Notebook written to: retail.ipynb
    ```

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
python scripts/build_pure_wheel.py --out wheels
pip wheel --quiet --no-build-isolation --no-deps -w wheels plugins/shape-domains plugins/shape-fabric \
    plugins/shape-eventhubs plugins/shape-sqlserver
pip install --quiet --no-deps --find-links wheels wheels/sqllocks_shape-*.whl \
    wheels/sqllocks_shape_domains-*.whl wheels/sqllocks_shape_fabric-*.whl
```

??? info "Output (exit 0)"

    ```text {.expected}
    sqllocks_shape-0.9.1-py3-none-any.whl  …
    checks passed: tag py3-none-any, < 28,600,000 bytes, no compiled code, RECORD valid
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
pip install --quiet --no-deps --find-links wheels "sqllocks-shape-fabric[sqlserver,eventhubs]"
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```
