# sqllocks-shape-dbt

Shape plugin: dbt integration. File-based and dbt-Core-generic: it reads and writes dbt's files and
never imports or runs dbt (`dbt-core` is not a dependency).

```
pip install sqllocks-shape-dbt          # or: pip install 'sqllocks-shape[dbt]'

shape from-dbt my_dbt_project -o shop.gen.json            # a dbt project as a generation schema
shape to-dbt-tests contract.json --model orders -o models/_shape_tests.yml
shape dbt-seeds shop.gen.json --project my_dbt_project    # generated data as dbt seeds
shape dbt-report --run-results target/run_results.json --manifest target/manifest.json
```

| Entry point | Name | |
|---|---|---|
| `shape.commands` | `from-dbt` | `manifest.json`, `schema.yml` or `sources.yml` to a generation schema: `unique` and `not_null` give keys, `relationships` give foreign keys, `accepted_values` give enums, `data_type` gives types (decimal precision and scale too) |
| `shape.commands` | `to-dbt-tests` | a v1 contract or a profile to `schema.yml` data tests (`dbt_utils`, `dbt_expectations`), with `--merge` into an existing file |
| `shape.commands` | `dbt-seeds` | generate a schema's tables as `seeds/*.csv` with a `seeds:` block of column types |
| `shape.commands` | `dbt-report` | one report for a dbt run (`run_results.json`, `manifest.json`) and a Shape check and drift comparison |
| `shape.sinks` | `dbt-seeds` | `dbt://PROJECT_DIR`: one table as a seed |

Everything is described in [docs/DBT.md](../../docs/DBT.md): the mapping tables, what dbt tests
cannot express, seed size guidance, and the Fabric pipeline pattern. A runnable sample project
is in `examples/dbt_jaffle_shop`.

Tests: `pytest -m "not dbt" plugins/shape-dbt/tests`; with `dbt-duckdb` installed,
`pytest -m dbt plugins/shape-dbt/tests` builds the sample against DuckDB.
