---
status: available
post:
video:
---
# Generate a domain and load it into DuckDB

Write a shipped domain into a local database and read one table.

Status: available. Generation from a profile is being hardened.

## What you'll learn

Generate seeded domain tables, use a sink and profile a DuckDB URI.

## Prerequisites

Use the repository's 0.9.1 core. Read [Install](../INSTALL.md) for the public package blocker.
You need Python, the domains plugin, the databases and integrations plugins with DuckDB, and Git for the drift
page. Start in an empty working directory. You do not need an account or production data.

## Time

Allow 10 minutes for reading and reviewing the output. This is a suggested allocation.

## 1. Generate and load retail

```bash {.runnable}
python - <<'PYCODE'
import shape
from shape_databases import DuckDbSink
result = shape.generate("retail", scale="small", seed=42)
sink = DuckDbSink()
for name, table in sorted(result.tables.items()):
    sink.write("duckdb:///retail.duckdb", name, table.to_batches())
    print(f"{name}: {table.num_rows} rows")
print("Wrote retail.duckdb")
PYCODE
```

??? info "Output (exit 0)"

    ```text {.expected}
    address: 1500 rows
    customer: 1000 rows
    order: 5000 rows
    order_line: 12500 rows
    product: 500 rows
    product_category: 50 rows
    promotion: 200 rows
    return: 850 rows
    store: 150 rows
    Wrote retail.duckdb
    ```

## 2. Read a table back

```bash {.runnable}
shape profile "duckdb://retail.duckdb?table=customer" --name customer -o customer.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "bb0ad274b2fc0875e1adb2e8cb76c6a586dbfdbe6a47cae2d0d703111093a209", "written": "customer.shape"}
    ```

## 3. Check the single-table capture

```bash {.runnable}
shape profile validate --safe customer.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: customer.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    CLEAN: no leaks found in customer.shape
    ```

The write URI has three slashes: `duckdb:///retail.duckdb`. The read URI has two and selects a table: `duckdb://retail.duckdb?table=customer`. Safe validation of a multi-table `--dataset` profile fails in 0.9.1; this tutorial validates a single table.

## What's next

[Continue](../LEARNING_PATHS.md).

## Related

[Concepts](../CONCEPTS.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
