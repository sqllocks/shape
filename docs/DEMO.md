# `shape demo`: demos for talks, clients and workshops

`shape demo` runs a **scenario** in one of three **modes** and records every run as a **session**
that can be reported on and cleaned up.

```bash
shape demo list                                            # the scenarios
shape demo run retail --rows 1000                          # inference: learn, generate, compare
shape demo init --name here --local-path ./landing         # a connection profile: a folder
shape demo run retail --mode seeding --connection here     # write the tables there
shape demo status SESSION                                  # what a session made
shape demo report SESSION --format html --output report.html
shape demo cleanup SESSION                                 # remove exactly what it made
shape demo notebook retail --mode seeding -o retail.ipynb  # a Fabric notebook for the scenario
```

Install the domains (`pip install 'sqllocks-shape[domains]'`) for the scenarios that generate
a domain, and the Fabric plugin (`pip install sqllocks-shape-fabric`) for the Lakehouse,
Warehouse, SQL database and Eventhouse targets, the semantic model and the Spark mode.

## Scenarios

| scenario | modes | domains | default rows |
|---|---|---|---|
| `retail` | inference, streaming, seeding | retail | 100,000 |
| `adventureworks` | inference, seeding | retail | 50,000 |
| `healthcare` | inference, streaming, seeding | healthcare | 50,000 |
| `enterprise` | seeding | retail, hr, financial | 200,000 |

A scenario runs **its own domains**. `--domain NAME` runs one other domain and `--domains A,B,C`
a composite (each name is an installed domain or a generation schema file). A domain that is
not installed fails the run and names it.

`--rows N` picks the scale preset that is generated: `small` up to 2,000, `medium` up to 50,000,
`large` up to 500,000, `xlarge` above. It is not an exact count: the tables have the rows of the
preset.

## Modes

* **inference**: profiles the source (the domain's own small dataset, or `--input-file` with a CSV,
  Parquet or JSON Lines file, or `--input-file live-db` with a connection profile that names a
  Warehouse or SQL database), learns a generation schema from it, generates a synthetic dataset
  and compares the two column by column. A column passes when its null rate is within five points
  and, unless it is a key, a date, a float or a unique pattern, its cardinality is close. The
  score is the share of columns that pass. `--output` takes `terminal` (the report, the
  default), `charts` (one self-contained HTML page), `semantic_model` (a Power BI `.bim` of
  the learned schema) or `all`; files go to `--output-dir` (the working folder).
* **seeding**: generates the scenario and writes it to the targets of a connection profile
  (below). With no profile the rows are generated and counted and nothing is written.
  `--scale-mode local` runs here; `spark` submits the run to a Fabric Spark notebook that writes
  Delta tables to the Lakehouse; `auto` (the default) picks `spark` when the profile has a
  Lakehouse and the rows reach 500,000.
* **streaming**: generates a small dataset and streams the first table's events as JSON lines,
  in event-time order (`--max-events`, default 100). Nothing is written to a target.

`--dry-run` plans a run and `--estimate` prints the cost estimate; neither generates anything, in
any mode.

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
`--dry-run` lists what it would remove.

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
