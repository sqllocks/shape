# Shape and dbt

Shape works with dbt in four ways. It is an integration: Shape does not replace dbt (see
[PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md)), and nothing here runs inside dbt but SQL and
`schema.yml`.

| | Command | What it does |
|---|---|---|
| 1 | `shape from-dbt` | a dbt project's sources, seeds or models and their tests become a generation schema |
| 2 | `shape to-dbt-tests` | a contract (or a profile) becomes `schema.yml` tests that run in a dbt job |
| 3 | `shape dbt-seeds` | generated tables become CSV seeds, with a `seeds:` block of column types |
| 4 | `shape dbt-report` | one report for a dbt run and a Shape contract check and drift comparison |

It is file-based and works with dbt Core anywhere (a laptop, CI, the Fabric dbt job). The plugin
reads and writes dbt's files and never imports or runs dbt, so `dbt-core` is not a dependency of
Shape or of the plugin.

```bash
pip install sqllocks-shape-dbt
shape plugins list                      # from-dbt, to-dbt-tests, dbt-seeds, dbt-report, dbt-seeds (sink)
```

A runnable sample, a jaffle-shop style project, is in
[`examples/dbt_jaffle_shop`](../examples/dbt_jaffle_shop). Its test
(`plugins/shape-dbt/tests/test_dbt_build.py`) does everything below against DuckDB.

## 1. `shape from-dbt`: a dbt project as a generation schema

```bash
shape from-dbt my_dbt_project -o shop.gen.json          # a project directory
shape from-dbt target/manifest.json -o shop.gen.json    # or a manifest
shape from-dbt models/staging/_sources.yml -o shop.gen.json
shape from-dbt my_dbt_project --select model            # generate the models instead
shape from-dbt my_dbt_project --profile day1.shape      # weight the enums by a profile
```

The schema is built by the builder `shape from-ddl` uses, so the generators, scale presets and
smart inference are the same (`--no-smart`, `--explain`, `--scale`, `--domain` work alike).

| dbt | generation schema |
|---|---|
| `unique` and `not_null` on one column | primary key (the first, when several qualify; it is noted) |
| `dbt_utils.unique_combination_of_columns` | composite primary key |
| `not_null` test or constraint | `nullable: false` |
| `relationships` (`to`, `field`) | `foreign_key` generator and a relationship |
| `accepted_values` | `weighted_enum`: equal weights, or the frequencies of `--profile` |
| `data_type` (a model contract or a source column) | column type; `varchar(n)` gives `max_length`; `numeric(p,s)` gives `precision` and `scale` |
| model, source and column descriptions | table description, and the metadata file |

- **What is imported.** The sources and the seeds; when a project has neither, its models.
  `--select source|seed|model` (repeatable) chooses. `manifest.json` (dbt's own, from `dbt parse`,
  `dbt compile` or `dbt docs generate`) and `schema.yml` / `sources.yml` give the same result;
  both the `tests:` and `data_tests:` keys, and test arguments inline or under `arguments:`, are read.
  A seed that has the name of a source is the same table (a project whose sources are loaded as
  seeds by `shape dbt-seeds`, as the sample's are): the source is kept, because its tests describe it.
- **Decimals.** A `numeric(18,2)` column becomes `type: decimal` with `precision: 18` and `scale: 2`.
  In a profile the column stays `dtype: float` (the profiler's vocabulary does not have a decimal),
  and generation writes it as a double rounded to the scale. The seeds sink declares
  `numeric(p,s)` again.
- **Column descriptions** have no place in the generation schema document, so the command writes
  `shop.gen.dbt-meta.json` beside it (per table: the dbt kind, the description and, per column, the
  description, the declared `data_type` and the names of its tests). `dbt-seeds --metadata` passes
  the descriptions on to the seeds block.
- **A key without a `data_type`** is typed an integer (or its parent's type, for a foreign key); a
  column with no type is typed from its name (`*_at` a timestamp, `*_date` a date, `is_*` a boolean,
  `amount` and `price` a decimal), otherwise text. Each such guess, and a `data_type` Shape does not
  know, is printed as a note.
- A `relationships` test to a table that is not imported is noted, and no foreign key is made.

## 2. `shape to-dbt-tests`: a contract or a profile as dbt tests

"Capture once, compile for purpose": the contract (format v1,
[the plan's §12.3](plans/COMPLETION_PLAN.md); unchanged) or a profile compiles to `schema.yml`
data tests, so the checks run where the data is built, with no Python.

```bash
shape to-dbt-tests orders.contract.json --model orders -o models/marts/_shape_tests.yml
shape to-dbt-tests marts.shape -o models/marts/_marts.yml --merge models/marts/_marts.yml --distribution
shape to-dbt-tests contract.json --kind sources --source-name raw -o models/staging/_sources.yml --merge models/staging/_sources.yml
```

| Contract rule | dbt test | Package |
|---|---|---|
| `nullable: false` | `not_null` | dbt |
| `unique: true` | `unique` | dbt |
| `allowed_values` | `accepted_values` | dbt |
| `min`, `max` (numbers) | `dbt_utils.accepted_range` | `dbt_utils` |
| `max_null_rate` | `dbt_utils.not_null_proportion` (`at_least` is one minus the rate) | `dbt_utils` |
| `row_count` `min` / `max` | `dbt_expectations.expect_table_row_count_to_be_between` | `dbt_expectations` |
| `required_columns` | `dbt_expectations.expect_column_to_exist` | `dbt_expectations` |
| `allow_extra_columns: false` | `dbt_expectations.expect_table_columns_to_match_set` | `dbt_expectations` |
| a profile's mean (`--distribution`) | `dbt_expectations.expect_column_mean_to_be_between` | `dbt_expectations` |
| a profile's standard deviation | `dbt_expectations.expect_column_stdev_to_be_between` | `dbt_expectations` |
| a profile's quartiles | `dbt_expectations.expect_column_quantile_values_to_be_between` | `dbt_expectations` |

The packages a file needs are named in its header, and `--packages-out packages.yml` writes the
entries (`dbt-labs/dbt_utils` `>=1.1.0,<2.0.0`, `metaplane/dbt_expectations` `>=0.10.0,<0.11.0`);
run `dbt deps`. Every test carries the tag `shape`: `dbt test --select tag:shape`.

- **From a profile**, the contract is captured first (`contract_from_profile`): the dtype,
  `nullable: false` where no value was null (else `max_null_rate`: the rate plus 0.01), `unique` for
  a key (a primary key, or a name ending in `_id`, `_key`, `_uuid`), numeric `min` and `max` widened
  by `--margin` (5%) of the observed range, and `allowed_values` for a text column with a complete
  value set. The distribution bounds default to the drift defaults (the mean within 0.5 standard
  deviations; the standard deviation within 0.67 to 1.5 times; the quartiles within the same band as
  the mean), so a dbt test fails where `shape diff` would report a shift. A key has no bounds.
- **dbt allows one entry per model.** Tests for a model that already has an entry in a
  `schema.yml` go into that entry: `--merge FILE` adds them (a test that is already there is not
  repeated; the file's `tests`/`data_tests` key is kept) and `-o` with the same path updates the
  file in place. Comments in the written file are lost.
- `--tests-key tests` is for dbt before 1.8; `--args-style inline` for dbt before 1.10
  (the default writes arguments under `arguments:`).
- `--kind models|seeds|sources` says where the tests go.

### What dbt cannot express (and the round trip)

The round trip is contract, dbt tests, contract. It is exact for what dbt tests can state:

| Rule | In dbt |
|---|---|
| `dtype` | **not expressible**: a data type is adapter-specific. Use a model contract's `data_type`. |
| `pattern` | **not expressible** |
| `distribution` (a family name) | **not expressible** |
| `min_true_rate`, `max_true_rate` | **not expressible** (no portable aggregate test of a boolean) |
| `min`, `max` that are not numbers | **not expressible** (`accepted_range` is numeric) |
| `nullable: true`, `unique: false`, `allow_extra_columns: true` | say nothing; they normalise away |

These rules are not lost: they are written on the column as `meta.shape` (and noted in the
output), so `contract_from_dbt_tests(text)` gives the whole contract back. **dbt does not test
them**; reading the tests alone (`use_meta=False`) gives exactly `expressible(contract)`. The
null rate is kept to nine decimals and `required_columns` is a set, so contracts are compared
after `normalize_contract`. The distribution bounds have no place in contract v1 (and the format
does not change): `contract_from_schema_yaml` returns them as a second value. A test
(`test_any_contract_round_trips`) checks this for generated contracts, and
`test_a_compiled_test_set_round_trips_and_passes_in_dbt` runs a compiled set in dbt.

```python
from shape_dbt.totests import compile_tests, contract_from_dbt_tests, expressible

compiled = compile_tests(contract, model="orders")        # .doc, .yaml(), .packages, .notes
assert contract_from_dbt_tests(compiled.yaml(), use_meta=False) == expressible(contract)
```

## 3. `shape dbt-seeds`: generated data as dbt seeds

```bash
shape dbt-seeds shop.gen.json --project my_dbt_project --rows raw_orders=2000 --dialect duckdb \
    --metadata shop.gen.dbt-meta.json
dbt build
```

Each table becomes `seeds/<table>.csv`, and `seeds/_shape_seeds.yml` gets a `seeds:` entry with
`config.column_types` for every column (and the descriptions, from the metadata). It is also the
`dbt-seeds` sink of the plugin API (`dbt://PROJECT_DIR`). Writing a table again replaces its
entry only.

- **Leading zeros.** Text columns are always declared `varchar`, never inferred, so a ZIP code,
  NDC or member number such as `02134` stays text in dbt. (Profiling the CSV with Shape is a
  separate matter: see issue #46; profile the built table instead, as the sample's test does.)
- **Types per dialect** (`--dialect`): `ansi` (the default), `duckdb`, `tsql` (Fabric Data
  Warehouse, SQL Server: `bit`, `datetime2(6)`, `varchar(n)`) and `spark` (Fabric Lakehouse:
  `string`). A decimal is `numeric(p,s)` with its precision and scale; a double is rounded to its scale.

### Size guidance

Seeds are for **small, static files**: dev, CI and demos. dbt Core hashes a seed only up to
`--maximum-seed-size-mib` (default 1 MiB, checked in dbt-core 1.12.5): a larger seed is compared
by its path, so `state:modified` no longer sees its content change. The sink therefore **refuses a
file over 1 MiB** (`max_bytes`) unless you pass `--allow-large`. A dbt seed is loaded by the
adapter through the warehouse's connection, not a bulk loader, so it gets slower with every row
and is the wrong tool for volume.

Measured once on this repository's sample (DuckDB, four vCPU, dbt-core 1.12.5, dbt-duckdb 1.11.0,
one run each, `dbt seed` wall time including about 3 s of dbt start-up, on a local file database; not a
benchmark, and not Fabric):

| Orders | Rows in all three tables | CSV size | `dbt seed` |
|---:|---:|---:|---:|
| 1,000 | 3,750 | 0.10 MB | 3.3 s |
| 10,000 | 37,500 | 1.1 MB (over the limit) | 3.4 s |
| 100,000 | 375,000 | 11.8 MB | 5.5 s |

DuckDB is a local embedded database; a warehouse over the network is much slower per row.
**[VERIFY]** the seed load time on the Fabric dbt job (adapters `dbt-fabric`, `dbt-fabricspark`).
For more than about a megabyte, write Parquet or Delta (`shape generate -f parquet`, `-f delta`) and
declare the files as a dbt source.

## 4. A Fabric pipeline: dbt job, then Shape, then one gate

```text
RunDbt (dbt job)  ->  ProfileDbtOutputs (notebook shape_profile_dbt)  ->  CheckGate (If) -> FailGate
        Completed (even when a dbt test failed)              Succeeded
```

- **`integrations/fabric/pipelines/shape_dbt_gate.DataPipeline`**, built from the same builder as
  the other Shape pipelines (`build_pipelines.py`). Parameters: `dbtCommand`, `models`,
  `tableRoot`, `contractPath`, `baselinePath`, `runResultsPath`, `manifestPath`, `outputDir`,
  `failOnDrift`.
- **`shape_profile_dbt.ipynb`** reads the models' Delta tables, profiles them together, checks them
  against a multi-table contract (`{"tables": {model: contract}}`, which `shape to-dbt-tests`
  also compiles to dbt tests) and diffs them against a baseline, then reads the dbt job's
  `run_results.json` and `manifest.json` and writes **one report** (`report.md`, `report.json`)
  in which a failed dbt test and a Shape contract or drift finding about the same column appear
  together. It installs Shape and the plugin with `%pip` like the other notebooks (so the pipeline
  passes `_inlineInstallationEnabled`, issue #7).
- The notebook runs after the dbt job **completes**, not only when it succeeds: a failed dbt test
  still gives the combined report, and the gate fails the run (`ShapeDbtGateFailed`).
- **The helper outside Fabric**: `shape dbt-report --run-results target/run_results.json
  --manifest target/manifest.json --profile now.shape --contract c.json --baseline day1.shape`
  gives the same report (exit 1 when a dbt test or model failed, a contract rule was violated, or,
  with `--fail-on-drift`, the data drifted); `--check-result` and `--diff-result` take the JSON that
  `shape check --json` and `shape diff --json` wrote. In Python: `shape_dbt.report.build_report`.
- **Run it on DuckDB** with the sample: `plugins/shape-dbt/tests/test_dbt_build.py` makes a model
  drift, fails a compiled dbt test and reads the report from the real `run_results.json`.

**Needs a live Fabric workspace, not verified here ([VERIFY]):** the dbt job activity (its type
name `DbtJob` and its property names are the builder's reading of the preview; replace the activity
with one exported from a workspace that has the "dbt jobs (preview)" tenant setting); where the
dbt job writes `run_results.json` and `manifest.json` in OneLake (the Fabric documentation names
`manifest.json` and `catalog.json` for `docs generate`; the paths are parameters); how a
notebook reads the model tables of a Fabric Data Warehouse (`tableRoot`); the exit-value
expressions; and the seed load time. The owner's checklist is
[`integrations/fabric/RUNBOOK.md`](../integrations/fabric/RUNBOOK.md), section 13.

## Running the tests

```bash
pip install -e '.[dev]' -e plugins/shape-dbt
pytest -m "not dbt" plugins/shape-dbt/tests          # no dbt needed
pip install dbt-duckdb                               # the dbt job in CI installs it; core never does
pytest -m dbt plugins/shape-dbt/tests                # dbt build against DuckDB
```

`pytest -m dbt` runs `dbt deps`, which needs the dbt package hub. On a runner without that access,
point `SHAPE_DBT_PACKAGES_FILE` at a `packages.yml` with `local:` or `git:` entries for
`dbt_utils`, `dbt_expectations` and `dbt_date`.
