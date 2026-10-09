# Shape and dbt

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" DBT
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for DBT
    ```


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

[Run this example](#local-example-0).


A runnable sample, a jaffle-shop style project, is in
[`examples/dbt_jaffle_shop`](../examples/dbt_jaffle_shop). Its test
(`plugins/shape-dbt/tests/test_dbt_build.py`) does everything below against DuckDB.

## 1. `shape from-dbt`: a dbt project as a generation schema

[Run this example](#local-example-1).


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
the drift defaults; unchanged) or a profile compiles to `schema.yml`
data tests, so the checks run where the data is built, with no Python.

[Run this example](#local-example-2).


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

[Run this example](#local-example-4).


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

[Run this example](#local-example-6).


`pytest -m dbt` runs `dbt deps`, which needs the dbt package hub. On a runner without that access,
point `SHAPE_DBT_PACKAGES_FILE` at a `packages.yml` with `local:` or `git:` entries for
`dbt_utils`, `dbt_expectations` and `dbt_date`.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
pip install --quiet --no-deps --no-build-isolation -e "$SHAPE_DOCS_REPO/plugins/shape-dbt"
shape plugins list
```

??? info "Output (exit 0)"

    ```text {.expected}
    179 plugin(s)
      shape.behaviors:entity_lifecycle        unloaded [sqllocks-shape-behavior]
      shape.behaviors:equipment_maintenance   unloaded [sqllocks-shape-behavior]
      shape.behaviors:event_sequence          unloaded [sqllocks-shape-behavior]
      shape.behaviors:file_arrival            unloaded [sqllocks-shape-behavior]
      shape.behaviors:healthcare_screening    unloaded [sqllocks-shape-behavior]
      shape.behaviors:subscription            unloaded [sqllocks-shape-behavior]
      shape.behaviors:telemetry_series        unloaded [sqllocks-shape-behavior]
      shape.behaviors:transaction_stream      unloaded [sqllocks-shape-behavior]
      shape.calendars:composite               unloaded [sqllocks-shape]
      shape.calendars:us_federal              unloaded [sqllocks-shape]
      shape.calendars:us_retail               unloaded [sqllocks-shape]
      shape.chaos:file                        unloaded [sqllocks-shape]
      shape.chaos:referential                 unloaded [sqllocks-shape]
      shape.chaos:schema                      unloaded [sqllocks-shape]
      shape.chaos:temporal                    unloaded [sqllocks-shape]
      shape.chaos:value                       unloaded [sqllocks-shape]
      shape.chaos:volume                      unloaded [sqllocks-shape]
      shape.commands:behave                   unloaded [sqllocks-shape-behavior]
      shape.commands:check-answers            unloaded [sqllocks-shape-fabric]
      shape.commands:ctgan                    unloaded [sqllocks-shape]
      shape.commands:dbt-report               unloaded [sqllocks-shape-dbt]
      shape.commands:dbt-seeds                unloaded [sqllocks-shape-dbt]
      shape.commands:deploy-notebook          unloaded [sqllocks-shape-fabric]
      shape.commands:evaluate                 unloaded [sqllocks-shape-integrations]
      shape.commands:export-model             unloaded [sqllocks-shape-fabric]
      shape.commands:fabric                   unloaded [sqllocks-shape-fabric]
      shape.commands:from-dbt                 unloaded [sqllocks-shape-dbt]
      shape.commands:healthcare-codes         unloaded [sqllocks-shape-healthcare-codes]
      shape.commands:known-answer             unloaded [sqllocks-shape-fabric]
      shape.commands:lineage                  unloaded [sqllocks-shape-integrations]
      shape.commands:mlflow                   unloaded [sqllocks-shape-integrations]
      shape.commands:notebook                 unloaded [sqllocks-shape-fabric]
      shape.commands:profile-db               unloaded [sqllocks-shape-sqlserver]
      shape.commands:profile-model            unloaded [sqllocks-shape-fabric]
      shape.commands:publish                  unloaded [sqllocks-shape-fabric]
      shape.commands:publish-report           unloaded [sqllocks-shape-fabric]
      shape.commands:setup-fabric             unloaded [sqllocks-shape-fabric]
      shape.commands:simulate                 unloaded [sqllocks-shape-simulation]
      shape.commands:to-dbt-tests             unloaded [sqllocks-shape-dbt]
      shape.detectors:cpt                     unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:email                   unloaded [sqllocks-shape]
      shape.detectors:hcpcs                   unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:icd10                   unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:ipv4                    unloaded [sqllocks-shape]
      shape.detectors:mbi                     unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:member_id               unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:ndc                     unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:npi                     unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:phone                   unloaded [sqllocks-shape]
      shape.detectors:presidio                unloaded [sqllocks-shape-integrations]
      shape.detectors:us_ssn                  unloaded [sqllocks-shape]
      shape.distributions:bernoulli           unloaded [sqllocks-shape]
      shape.distributions:beta                unloaded [sqllocks-shape]
      shape.distributions:exponential         unloaded [sqllocks-shape]
      shape.distributions:gamma               unloaded [sqllocks-shape]
      shape.distributions:geometric           unloaded [sqllocks-shape]
      shape.distributions:histogram           unloaded [sqllocks-shape]
      shape.distributions:log_normal          unloaded [sqllocks-shape]
      shape.distributions:lognormal           unloaded [sqllocks-shape]
      shape.distributions:mixture             unloaded [sqllocks-shape]
      shape.distributions:negative_binomial   unloaded [sqllocks-shape]
      shape.distributions:normal              unloaded [sqllocks-shape]
      shape.distributions:pareto              unloaded [sqllocks-shape]
      shape.distributions:poisson             unloaded [sqllocks-shape]
      shape.distributions:power_law_cutoff    unloaded [sqllocks-shape]
      shape.distributions:triangular          unloaded [sqllocks-shape]
      shape.distributions:truncated           unloaded [sqllocks-shape]
      shape.distributions:uniform             unloaded [sqllocks-shape]
      shape.distributions:weibull             unloaded [sqllocks-shape]
      shape.distributions:zipf                unloaded [sqllocks-shape]
      shape.domains:capital_markets           unloaded [sqllocks-shape-domains]
      shape.domains:education                 unloaded [sqllocks-shape-domains]
      shape.domains:financial                 unloaded [sqllocks-shape-domains]
      shape.domains:healthcare                unloaded [sqllocks-shape-domains]
      shape.domains:hr                        unloaded [sqllocks-shape-domains]
      shape.domains:insurance                 unloaded [sqllocks-shape-domains]
      shape.domains:iot                       unloaded [sqllocks-shape-domains]
      shape.domains:manufacturing             unloaded [sqllocks-shape-domains]
      shape.domains:marketing                 unloaded [sqllocks-shape-domains]
      shape.domains:pulse                     unloaded [sqllocks-shape-domains]
      shape.domains:real_estate               unloaded [sqllocks-shape-domains]
      shape.domains:retail                    unloaded [sqllocks-shape-domains]
      shape.domains:supply_chain              unloaded [sqllocks-shape-domains]
      shape.domains:telecom                   unloaded [sqllocks-shape-domains]
      shape.emitters:console                  unloaded [sqllocks-shape]
      shape.emitters:eventhouse               unloaded [sqllocks-shape-fabric]
      shape.emitters:eventhubs                unloaded [sqllocks-shape-eventhubs]
      shape.emitters:eventstream              unloaded [sqllocks-shape-fabric]
      shape.emitters:fhir                     unloaded [sqllocks-shape-healthcare-standards]
      shape.emitters:file                     unloaded [sqllocks-shape]
      shape.emitters:jsonl                    unloaded [sqllocks-shape]
      shape.emitters:kafka                    unloaded [sqllocks-shape-kafka]
      shape.fitters:auto                      unloaded [sqllocks-shape]
      shape.reports:html                      unloaded [sqllocks-shape]
      shape.reports:json                      unloaded [sqllocks-shape]
      shape.reports:md                        unloaded [sqllocks-shape]
      shape.sinks:abfss                       unloaded [sqllocks-shape]
      shape.sinks:csv                         unloaded [sqllocks-shape]
      shape.sinks:databricks                  unloaded [sqllocks-shape-databases]
      shape.sinks:dbt-seeds                   unloaded [sqllocks-shape-dbt]
      shape.sinks:delta                       unloaded [sqllocks-shape]
      shape.sinks:duckdb                      unloaded [sqllocks-shape-databases]
      shape.sinks:excel                       unloaded [sqllocks-shape]
      shape.sinks:fabric-mirror               unloaded [sqllocks-shape]
      shape.sinks:fhir-bundle                 unloaded [sqllocks-shape-healthcare-standards]
      shape.sinks:fhir-ndjson                 unloaded [sqllocks-shape-healthcare-standards]
      shape.sinks:ipc                         unloaded [sqllocks-shape]
      shape.sinks:jsonl                       unloaded [sqllocks-shape]
      shape.sinks:mysql                       unloaded [sqllocks-shape-databases]
      shape.sinks:ncpdp                       unloaded [sqllocks-shape-healthcare-standards]
      shape.sinks:omop                        unloaded [sqllocks-shape-healthcare-standards]
      shape.sinks:parquet                     unloaded [sqllocks-shape]
      shape.sinks:postgres                    unloaded [sqllocks-shape-databases]
      shape.sinks:snowflake                   unloaded [sqllocks-shape-databases]
      shape.sinks:sql                         unloaded [sqllocks-shape]
      shape.sinks:sqlserver                   unloaded [sqllocks-shape-fabric]
      shape.sinks:synapse                     unloaded [sqllocks-shape-fabric]
      shape.sinks:tsv                         unloaded [sqllocks-shape]
      shape.sinks:warehouse                   unloaded [sqllocks-shape-fabric]
      shape.sinks:x12-277ca                   unloaded [sqllocks-shape-healthcare-standards]
      shape.sinks:x12-834                     unloaded [sqllocks-shape-healthcare-standards]
      shape.sinks:x12-835                     unloaded [sqllocks-shape-healthcare-standards]
      shape.sinks:x12-837i                    unloaded [sqllocks-shape-healthcare-standards]
      shape.sinks:x12-837p                    unloaded [sqllocks-shape-healthcare-standards]
      shape.sources:abfss                     unloaded [sqllocks-shape]
      shape.sources:csv                       unloaded [sqllocks-shape]
      shape.sources:delta                     unloaded [sqllocks-shape]
      shape.sources:duckdb                    unloaded [sqllocks-shape-integrations]
      shape.sources:hl7v2                     unloaded [sqllocks-shape-healthcare-standards]
      shape.sources:ipc                       unloaded [sqllocks-shape]
      shape.sources:json                      unloaded [sqllocks-shape]
      shape.sources:jsonl                     unloaded [sqllocks-shape]
      shape.sources:mssql                     unloaded [sqllocks-shape-sqlserver]
      shape.sources:onelake                   unloaded [sqllocks-shape-fabric]
      shape.sources:parquet                   unloaded [sqllocks-shape]
      shape.sources:semantic-model            unloaded [sqllocks-shape-fabric]
      shape.sources:x12                       unloaded [sqllocks-shape-healthcare-standards]
      shape.sources:xml                       unloaded [sqllocks-shape]
      shape.strategies:address                unloaded [sqllocks-shape]
      shape.strategies:bootstrap              unloaded [sqllocks-shape]
      shape.strategies:choice                 unloaded [sqllocks-shape]
      shape.strategies:composite_fk_field     unloaded [sqllocks-shape]
      shape.strategies:composite_foreign_key  unloaded [sqllocks-shape]
      shape.strategies:computed               unloaded [sqllocks-shape]
      shape.strategies:conditional            unloaded [sqllocks-shape]
      shape.strategies:conditional_table      unloaded [sqllocks-shape]
      shape.strategies:constant               unloaded [sqllocks-shape]
      shape.strategies:correlated             unloaded [sqllocks-shape]
      shape.strategies:derived                unloaded [sqllocks-shape]
      shape.strategies:distribution           unloaded [sqllocks-shape]
      shape.strategies:empirical              unloaded [sqllocks-shape]
      shape.strategies:faker                  unloaded [sqllocks-shape]
      shape.strategies:first_per_parent       unloaded [sqllocks-shape]
      shape.strategies:foreign_key            unloaded [sqllocks-shape]
      shape.strategies:formula                unloaded [sqllocks-shape]
      shape.strategies:hierarchy              unloaded [sqllocks-shape]
      shape.strategies:hierarchy_field        unloaded [sqllocks-shape]
      shape.strategies:lifecycle              unloaded [sqllocks-shape]
      shape.strategies:locale                 unloaded [sqllocks-shape]
      shape.strategies:lookup                 unloaded [sqllocks-shape]
      shape.strategies:native                 unloaded [sqllocks-shape]
      shape.strategies:normal                 unloaded [sqllocks-shape]
      shape.strategies:pattern                unloaded [sqllocks-shape]
      shape.strategies:record_field           unloaded [sqllocks-shape]
      shape.strategies:record_sample          unloaded [sqllocks-shape]
      shape.strategies:reference_data         unloaded [sqllocks-shape]
      shape.strategies:scd2                   unloaded [sqllocks-shape]
      shape.strategies:self_ref_field         unloaded [sqllocks-shape]
      shape.strategies:self_referencing       unloaded [sqllocks-shape]
      shape.strategies:sequence               unloaded [sqllocks-shape]
      shape.strategies:temporal               unloaded [sqllocks-shape]
      shape.strategies:uniform                unloaded [sqllocks-shape]
      shape.strategies:uuid                   unloaded [sqllocks-shape]
      shape.strategies:weighted_enum          unloaded [sqllocks-shape]
      shape.stream_sources:eventhubs          unloaded [sqllocks-shape-eventhubs]
      shape.stream_sources:kafka              unloaded [sqllocks-shape-kafka]
      shape.transforms:cdm                    unloaded [sqllocks-shape]
      shape.transforms:mask                   unloaded [sqllocks-shape]
      shape.transforms:star                   unloaded [sqllocks-shape]
    ```

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape from-dbt my_dbt_project -o shop.gen.json          # a project directory
shape from-dbt my_dbt_project/target/manifest.json -o shop.gen.json    # or a manifest
shape from-dbt my_dbt_project/models/staging/_sources.yml -o shop.gen.json
shape from-dbt my_dbt_project --select model            # generate the models instead
shape from-dbt my_dbt_project --profile day1.shape      # weight the enums by a profile
```

??? info "Output (exit 0)"

    ```text {.expected}
    Shape dbt import

      Output: shop.gen.json
      Metadata: shop.gen.dbt-meta.json
      Tables: 3
      Relationships: 2
      raw_customers: 3 columns (PK: customer_id)
      raw_orders: 4 columns (PK: order_id)
      raw_payments: 4 columns (PK: payment_id)
    Shape dbt import

      Output: shop.gen.json
      Metadata: shop.gen.dbt-meta.json
      Tables: 3
      Relationships: 2
      raw_customers: 3 columns (PK: customer_id)
      raw_orders: 4 columns (PK: order_id)
      raw_payments: 4 columns (PK: payment_id)
    Shape dbt import

      Output: shop.gen.json
      Metadata: shop.gen.dbt-meta.json
      Tables: 3
      Relationships: 2
      raw_customers: 3 columns (PK: customer_id)
      raw_orders: 4 columns (PK: order_id)
      raw_payments: 4 columns (PK: payment_id)
    Shape dbt import

      Output: dbt_import.gen.json
      Metadata: dbt_import.gen.dbt-meta.json
      Tables: 5
      Relationships: 1
      customers: 7 columns (PK: customer_id)
      orders: 5 columns (PK: order_id)
      stg_customers: 0 columns
      stg_orders: 0 columns
      stg_payments: 0 columns
    shape: note: day1.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    Shape dbt import

      Output: dbt_import.gen.json
      Metadata: dbt_import.gen.dbt-meta.json
      Tables: 3
      Relationships: 2
      raw_customers: 3 columns (PK: customer_id)
      raw_orders: 4 columns (PK: order_id)
      raw_payments: 4 columns (PK: payment_id)
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
shape to-dbt-tests orders.contract.json --model orders -o models/marts/_shape_tests.yml
shape to-dbt-tests marts.shape -o models/marts/_marts.yml --merge models/marts/_marts.yml --distribution
shape to-dbt-tests contract.json --kind sources --source-name raw --model orders -o models/staging/_sources.yml --merge models/staging/_sources.yml
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote models/marts/_shape_tests.yml
    These tests need dbt packages; add to packages.yml and run `dbt deps`:
    packages:
    - package: dbt-labs/dbt_utils
      version:
      - '>=1.1.0'
      - <2.0.0

    shape: note: marts.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    Wrote models/marts/_marts.yml
    These tests need dbt packages; add to packages.yml and run `dbt deps`:
    packages:
    - package: dbt-labs/dbt_utils
      version:
      - '>=1.1.0'
      - <2.0.0
    - package: metaplane/dbt_expectations
      version:
      - '>=0.10.0'
      - <0.11.0

      note: orders.order_id.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.customer_id.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.customer_email.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.status.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.amount.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.amount.distribution: dbt tests cannot state it; kept as meta, not tested
      note: orders.order_total.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.order_total.distribution: dbt tests cannot state it; kept as meta, not tested
      note: orders.placed_at.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.shipped_at.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.order_date.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.discount_code.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.is_gift.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.region.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.tier.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.churned.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.zip.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.city.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.state.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.country.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.iban.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.notes.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.token.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.ssn.dtype: dbt tests cannot state it; kept as meta, not tested
      note: orders.salary.dtype: dbt tests cannot state it; kept as meta, not tested
    Wrote models/staging/_sources.yml
    These tests need dbt packages; add to packages.yml and run `dbt deps`:
    packages:
    - package: dbt-labs/dbt_utils
      version:
      - '>=1.1.0'
      - <2.0.0
    ```

<a id="local-example-4"></a>

### Example 5

<!-- example: 4 -->

```bash {.runnable-reference}
shape dbt-seeds shop.gen.json --project my_dbt_project --rows raw_orders=2000 --dialect duckdb \
    --metadata shop.gen.dbt-meta.json
dbt build --project-dir my_dbt_project --profiles-dir my_dbt_project
```

??? info "Output (exit 0)"

    ```text {.expected}
      raw_customers: 1,000 rows -> seeds/raw_customers.csv
      raw_orders: 2,000 rows -> seeds/raw_orders.csv
      raw_payments: 12,500 rows -> seeds/raw_payments.csv
    Seeds written to my_dbt_project/seeds; run `dbt seed` or `dbt build`
    17:28:10  Running with dbt=1.12.5
    17:28:11  Registered adapter: duckdb=1.11.0
    17:28:11  Found 5 models, 23 data tests, 3 seeds, 3 sources, 883 macros
    17:28:11  
    17:28:11  Concurrency: 1 threads (target='dev')
    17:28:11  
    17:28:12  1 of 31 START seed file main.raw_customers ..................................... [RUN]
    17:28:12  1 of 31 OK loaded seed file main.raw_customers ................................. [INSERT 1000 in 0.08s]
    17:28:12  2 of 31 START seed file main.raw_orders ........................................ [RUN]
    17:28:12  2 of 31 OK loaded seed file main.raw_orders .................................... [INSERT 2000 in 0.08s]
    17:28:12  3 of 31 START seed file main.raw_payments ...................................... [RUN]
    17:28:12  3 of 31 OK loaded seed file main.raw_payments .................................. [INSERT 12500 in 0.19s]
    17:28:12  4 of 31 START test source_accepted_values_raw_raw_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
    17:28:12  4 of 31 PASS source_accepted_values_raw_raw_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.07s]
    17:28:12  5 of 31 START test source_accepted_values_raw_raw_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [RUN]
    17:28:12  5 of 31 PASS source_accepted_values_raw_raw_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [PASS in 0.03s]
    17:28:12  6 of 31 START test source_not_null_raw_raw_customers_customer_id ............... [RUN]
    17:28:12  6 of 31 PASS source_not_null_raw_raw_customers_customer_id ..................... [PASS in 0.02s]
    17:28:12  7 of 31 START test source_not_null_raw_raw_orders_customer_id .................. [RUN]
    17:28:12  7 of 31 PASS source_not_null_raw_raw_orders_customer_id ........................ [PASS in 0.02s]
    17:28:12  8 of 31 START test source_not_null_raw_raw_orders_order_date ................... [RUN]
    17:28:12  8 of 31 PASS source_not_null_raw_raw_orders_order_date ......................... [PASS in 0.02s]
    17:28:12  9 of 31 START test source_not_null_raw_raw_orders_order_id ..................... [RUN]
    17:28:12  9 of 31 PASS source_not_null_raw_raw_orders_order_id ........................... [PASS in 0.02s]
    17:28:12  10 of 31 START test source_not_null_raw_raw_orders_status ...................... [RUN]
    17:28:12  10 of 31 PASS source_not_null_raw_raw_orders_status ............................ [PASS in 0.02s]
    17:28:12  11 of 31 START test source_not_null_raw_raw_payments_amount .................... [RUN]
    17:28:12  11 of 31 PASS source_not_null_raw_raw_payments_amount .......................... [PASS in 0.02s]
    17:28:12  12 of 31 START test source_not_null_raw_raw_payments_order_id .................. [RUN]
    17:28:12  12 of 31 PASS source_not_null_raw_raw_payments_order_id ........................ [PASS in 0.02s]
    17:28:12  13 of 31 START test source_not_null_raw_raw_payments_payment_id ................ [RUN]
    17:28:12  13 of 31 PASS source_not_null_raw_raw_payments_payment_id ...................... [PASS in 0.02s]
    17:28:12  14 of 31 START test source_not_null_raw_raw_payments_payment_method ............ [RUN]
    17:28:12  14 of 31 PASS source_not_null_raw_raw_payments_payment_method .................. [PASS in 0.02s]
    17:28:12  15 of 31 START test source_relationships_raw_raw_orders_customer_id__customer_id__source_raw_raw_customers_  [RUN]
    17:28:12  15 of 31 PASS source_relationships_raw_raw_orders_customer_id__customer_id__source_raw_raw_customers_  [PASS in 0.03s]
    17:28:12  16 of 31 START test source_relationships_raw_raw_payments_order_id__order_id__source_raw_raw_orders_  [RUN]
    17:28:12  16 of 31 PASS source_relationships_raw_raw_payments_order_id__order_id__source_raw_raw_orders_  [PASS in 0.03s]
    17:28:12  17 of 31 START test source_unique_raw_raw_customers_customer_id ................ [RUN]
    17:28:12  17 of 31 PASS source_unique_raw_raw_customers_customer_id ...................... [PASS in 0.02s]
    17:28:12  18 of 31 START test source_unique_raw_raw_orders_order_id ...................... [RUN]
    17:28:12  18 of 31 PASS source_unique_raw_raw_orders_order_id ............................ [PASS in 0.02s]
    17:28:12  19 of 31 START test source_unique_raw_raw_payments_payment_id .................. [RUN]
    17:28:12  19 of 31 PASS source_unique_raw_raw_payments_payment_id ........................ [PASS in 0.02s]
    17:28:12  20 of 31 START sql view model main.stg_customers ............................... [RUN]
    17:28:12  20 of 31 OK created sql view model main.stg_customers .......................... [OK in 0.05s]
    17:28:12  21 of 31 START sql view model main.stg_orders .................................. [RUN]
    17:28:12  21 of 31 OK created sql view model main.stg_orders ............................. [OK in 0.02s]
    17:28:12  22 of 31 START sql view model main.stg_payments ................................ [RUN]
    17:28:12  22 of 31 OK created sql view model main.stg_payments ........................... [OK in 0.02s]
    17:28:12  23 of 31 START sql table model main.orders ..................................... [RUN]
    17:28:13  23 of 31 OK created sql table model main.orders ................................ [OK in 0.12s]
    17:28:13  24 of 31 START test accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
    17:28:13  24 of 31 PASS accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.02s]
    17:28:13  25 of 31 START test not_null_orders_customer_id ................................ [RUN]
    17:28:13  25 of 31 PASS not_null_orders_customer_id ...................................... [PASS in 0.02s]
    17:28:13  26 of 31 START test not_null_orders_order_id ................................... [RUN]
    17:28:13  26 of 31 PASS not_null_orders_order_id ......................................... [PASS in 0.02s]
    17:28:13  27 of 31 START test unique_orders_order_id ..................................... [RUN]
    17:28:13  27 of 31 PASS unique_orders_order_id ........................................... [PASS in 0.02s]
    17:28:13  28 of 31 START sql table model main.customers .................................. [RUN]
    17:28:13  28 of 31 OK created sql table model main.customers ............................. [OK in 0.04s]
    17:28:13  29 of 31 START test not_null_customers_customer_id ............................. [RUN]
    17:28:13  29 of 31 PASS not_null_customers_customer_id ................................... [PASS in 0.02s]
    17:28:13  30 of 31 START test relationships_orders_customer_id__customer_id__ref_customers_  [RUN]
    17:28:13  30 of 31 PASS relationships_orders_customer_id__customer_id__ref_customers_ .... [PASS in 0.02s]
    17:28:13  31 of 31 START test unique_customers_customer_id ............................... [RUN]
    17:28:13  31 of 31 PASS unique_customers_customer_id ..................................... [PASS in 0.02s]
    17:28:13  
    17:28:13  Finished running 3 seeds, 2 table models, 23 data tests, 3 view models in 0 hours 0 minutes and 1.28 seconds (1.28s).
    17:28:13  
    17:28:13  Completed successfully
    17:28:13  
    17:28:13  Done. PASS=31 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=31
    ```

<a id="local-example-6"></a>

### Example 7

<!-- example: 6 -->

```bash {.runnable-reference}
pip install --quiet --no-deps --no-build-isolation -e "$SHAPE_DOCS_REPO" -e "$SHAPE_DOCS_REPO/plugins/shape-dbt"
python -m pytest -q -m "not dbt" plugins/shape-dbt/tests          # no dbt needed
python -c 'import dbt; print("dbt-duckdb is installed")'                               # the dbt job in CI installs it; core never does
python -m pytest -q -m dbt plugins/shape-dbt/tests                # dbt build against DuckDB
```

??? info "Output (exit 0)"

    ```text {.expected}
    ............................................................................................ [ 85%]
    ................                                                                             [100%]
    ========================================= warnings summary =========================================
    plugins/shape-dbt/tests/test_report.py::test_the_cli_computes_the_check_and_the_drift_from_profiles
      /workspace/shape/plugins/shape-dbt/src/shape_dbt/commands.py:353: ArtifactNotVerifiedWarning: /tmp/pytest-of-agent/pytest-14/test_the_cli_computes_the_chec0/now.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
        current = shape.load(args.profile)

    plugins/shape-dbt/tests/test_report.py::test_the_cli_computes_the_check_and_the_drift_from_profiles
      /workspace/shape/plugins/shape-dbt/src/shape_dbt/commands.py:357: ArtifactNotVerifiedWarning: /tmp/pytest-of-agent/pytest-14/test_the_cli_computes_the_chec0/base.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
        drift = shape.diff(shape.load(args.baseline), current).to_dict()

    plugins/shape-dbt/tests/test_to_dbt_tests.py::test_the_cli_compiles_a_profile_with_distribution_bounds
      /workspace/shape/plugins/shape-dbt/src/shape_dbt/commands.py:194: ArtifactNotVerifiedWarning: /tmp/pytest-of-agent/pytest-14/test_the_cli_compiles_a_profil0/orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
        profile = shape.load(src)

    -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
    108 passed, 7 deselected, 3 warnings in 4.16s
    dbt-duckdb is installed
    .......                                                                                      [100%]
    ========================================= warnings summary =========================================
    plugins/shape-dbt/tests/test_dbt_build.py::test_a_failed_dbt_test_and_a_shape_drift_finding_appear_in_one_report
      /workspace/shape/plugins/shape-dbt/src/shape_dbt/commands.py:353: ArtifactNotVerifiedWarning: /tmp/pytest-of-agent/pytest-15/jaffle0/current.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
        current = shape.load(args.profile)

    plugins/shape-dbt/tests/test_dbt_build.py::test_a_failed_dbt_test_and_a_shape_drift_finding_appear_in_one_report
      /workspace/shape/plugins/shape-dbt/src/shape_dbt/commands.py:357: ArtifactNotVerifiedWarning: /tmp/pytest-of-agent/pytest-15/jaffle0/baseline.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
        drift = shape.diff(shape.load(args.baseline), current).to_dict()

    -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
    7 passed, 108 deselected, 2 warnings in 16.64s
    ```
