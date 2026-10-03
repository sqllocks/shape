# Testing with Shape: the pytest plugin and `shape seed`

## The pytest plugin

```bash
pip install 'sqllocks-shape[pytest,domains]'
```

The `pytest` extra installs pytest. The plugin itself is registered by the package through the
`pytest11` entry point (`shape = "shape.testdata.pytest_plugin"`), as pytest plugins are, so it loads
in every pytest run in that environment. Turn it off with `-p no:shape`: then its fixtures, marker
and option do not exist. It writes nothing outside `tmp_path_factory`.

### `shape_dataset`

A fixture that returns a function. The tables are generated once per session for each
`(spec digest, scale, seed)`; the digest is a hash of the generation schema itself, so a changed
domain or schema file is a new key.

```python
def test_orders(shape_dataset):
    data = shape_dataset("retail", scale="tiny", seed=7, tables=["orders"])
    orders = data["orders"]        # a pyarrow.Table
    frame = data.as_pandas("orders")   # a DataFrame; data.as_pandas() converts every table
```

* `spec`: a domain name, the path of a generation schema file, a `GenSchema` or its document.
* `scale`: a preset of the domain, or `"tiny"` (the default): 100 rows per table. An unknown scale
  is an error that lists the choices.
* `seed`: default is the schema's own seed.
* `tables`: keep only these, in this order; a name the schema lacks is a `ShapeError`.

The result is a read-only mapping of table name to Arrow table, with `.digest`, `.scale`, `.seed`
and `.tables`. `as_pandas()` needs pandas (`pip install 'sqllocks-shape[pandas]'`); without it, it
raises a `ShapeError` that says so, and the Arrow tables still work.

### `shape_scenario` and the marker

```python
import pytest

@pytest.mark.shape_scenario("library:nulls_injected", scale="tiny")
def test_my_null_gate_sees_it(shape_scenario):
    assert shape_scenario.met                          # the scenario met its own answer key
    assert shape_scenario.outcome.gates["null_check"] is False
```

The marker names a library scenario (`docs/SCENARIO_LIBRARY.md`) as `library:NAME`, with optional
`scale` and `seed`; the `shape_scenario` fixture runs it and returns its `ScenarioResult` (`.met`,
`.mismatches`, `.outcome` with `gates`, `defects`, `drift` and the `files` it wrote under
`tmp_path_factory`). A scenario runs once per session for a given name, scale and seed. A marker
that is missing, malformed or names an unknown scenario fails the test at setup with a message.

### `--shape-seed`

`pytest --shape-seed 123` overrides every seed: the `seed=` of `shape_dataset` (even when a test
passes none) and of every scenario marker. Use it to rerun a suite on other data.

## `shape seed`

```bash
shape seed retail --target postgresql://shape@localhost:5432/shape --scale small --seed 7
shape seed schema.json --target mssql://localhost:1433/test --mode truncate
shape seed retail --target mysql://shape@localhost/shape --dry-run
shape seed retail --target sql://./seed-scripts --scale tiny
```

`SPEC|DOMAIN` is an installed domain or a generation schema file (`shape from-ddl` writes one).
`--target` is a database URI whose scheme picks the installed sink (`mssql` or `sqlserver`,
`postgres` or `postgresql`, `mysql`; `docs/SINKS.md`), or `sql://DIR`. `--scale` is a preset or
`tiny`; `--seed` defaults to the schema's. Credentials are never part of a command line: the sinks
read their password from the environment (`SHAPE_POSTGRES_PASSWORD`, `SHAPE_MYSQL_PASSWORD`; see
each sink), and a password in a URI is never printed.

Every table is written in foreign-key order: parents before the tables that point at them.

| `--mode` | |
|---|---|
| `create` (default) | makes the tables and refuses an existing one: **every table is checked before anything is written**, so a refusal writes nothing (exit 1) |
| `truncate` | empties each table first (it is created when missing); the same spec, scale and seed leave byte-identical contents |
| `append` | adds the rows (created when missing) |

`--dry-run` prints the plan (the order, the rows of each table and the totals) and connects to
nothing, not even to check which tables exist. `--json` prints the plan or the result as JSON.

**A `sql://DIR` target** writes one INSERT script per table through the `sql` sink, named
`01_customer.sql`, `02_address.sql`, ... so the scripts sort in the order to run them (T-SQL by
default, `GO` separators; `sql://DIR?dialect=postgres` or `mysql`). `create` refuses when any of the
scripts exists; `truncate` writes them again with a `DROP TABLE` first; `append` writes the rows
without a table definition. Rerunning `truncate` with the same seed writes identical bytes.

**Transactions.** A table is written in one transaction: the sinks commit after each table, so
there is no single transaction across tables. A failure part way leaves the tables written before
it; the error names them. Rerun with `--mode truncate` to replace them.

| Exit | Meaning |
|---|---|
| 0 | seeded, or the plan was printed |
| 1 | `create` found an existing table or script; nothing was written |
| 2 | bad input (target, scale, spec, mode), a sink that is not installed, or a failed write |

The same call is available from Python: `shape.testdata.seed.seed_target(spec, target, scale=...,
seed=..., mode=..., dry_run=..., sink_options=...)`, where `sink_options` carries what the command
line cannot (`password`, `user`, `schema_name`, `credential`).
