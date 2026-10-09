---
status: available
post:
video:
---
# Generate a domain and load it into DuckDB

Write retail tables to a local database, inspect their keys and profile a table.

Status: available. Generation from a profile is being hardened.

You can run this walkthrough in an empty working directory without a cloud account. Each command below is followed by the complete output from a repository build. Run the steps in order: later commands use the files written earlier. When a command intentionally fails, the expected exit code is shown beside its output. Read the explanation after the output before moving on; the verdict and its evidence matter more than the presence of a new file.

## What you'll learn

Write retail tables to a local database, inspect their keys and profile a table.

## Prerequisites

Install Shape with the domains extra; see [Install](../INSTALL.md). For the DuckDB walkthrough, install the databases and integrations plugins with their DuckDB extras. You need Python 3.11 or newer; the drift walkthrough also uses Git. Start in an empty directory and keep the generated rows local.

## Time

About 15 minutes.

## 1. Generate into DuckDB

```bash {.runnable}
shape generate retail --scale small --seed 42 --to duckdb:///retail.duckdb --json > duckdb-generation.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

This command uses the same retail domain, small scale and seed 42 as the other starters, but writes to DuckDB instead of a CSV directory. The databases plugin handles the write target. The three slashes in `duckdb:///retail.duckdb` select a relative local file; they are part of the write URI form, not a typographical detail. The JSON result goes to a file, so no terminal output is expected here. Use a new working directory for the exercise so the destination is disposable and you do not confuse the new database with an existing development store.

## 2. Inspect the tables and joins

```bash {.runnable}
python - <<'PYDATA'
import duckdb
con = duckdb.connect('retail.duckdb', read_only=True)
print('Tables:', ', '.join(r[0] for r in con.execute('SHOW TABLES').fetchall()))
print('Orders:', con.execute('SELECT count(*) FROM "order"').fetchone()[0])
print('Orders without customers:', con.execute('SELECT count(*) FROM "order" o LEFT JOIN customer c ON o.customer_id = c.customer_id WHERE c.customer_id IS NULL').fetchone()[0])
print('Order lines without orders:', con.execute('SELECT count(*) FROM order_line l LEFT JOIN "order" o ON l.order_id = o.order_id WHERE o.order_id IS NULL').fetchone()[0])
con.close()
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Tables: address, customer, order, order_line, product, product_category, promotion, return, store
    Orders: 5000
    Orders without customers: 0
    Order lines without orders: 0
    ```

The table list shows all nine retail tables. The order count is 5,000. The two left joins count orders without a matching customer and order lines without a matching order; both counts are zero for this run. These checks inspect stored rows, so they test the write result as well as the generator's relationships. Quote the `order` table name because it is a SQL keyword. The connection is opened read-only for inspection and closed afterward. A zero orphan count covers these two links, not every possible integrity or business requirement. Add the checks your consumer needs instead of treating one join as a complete validation suite.

## 3. Read a table back into a profile

```bash {.runnable}
shape profile "duckdb://retail.duckdb?table=customer" --name customer -o customer.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "bb0ad274b2fc0875e1adb2e8cb76c6a586dbfdbe6a47cae2d0d703111093a209", "written": "customer.shape"}
    ```

The read form is `duckdb://retail.duckdb?table=customer`: two slashes and an explicit table query. The integrations plugin reads the selected table. This differs from the write URI, which selects the database file as a target. Giving the profile the name customer keeps the artifact's identity clear when you compare it with later captures. The command reads the stored customer rows; it does not profile the entire database or automatically discover every table through this URI. To investigate a different table, select that table and save a separate observation with suitable naming.

## 4. Validate the saved capture

```bash {.runnable}
shape profile validate --safe customer.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: customer.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    CLEAN: no leaks found in customer.shape
    ```

This single-table validation passes. The origin-verification note appears because the example artifact is unsigned; the data-minimisation verdict is a separate result. The successful single-table path contrasts with the documented multi-table dataset validator limitation you saw in the first tutorial. Before sharing a profile from your own data, review its retained information and the validation result. You now have a complete local round trip: a shipped domain produces related rows, the database writer stores them, SQL checks inspect them, and a source adapter reads a table for profiling. Reuse that sequence when you need a small database fixture for development.

## What's next

[Choose your learning path](../LEARNING_PATHS.md). Keep the artifacts you reviewed as evidence for the next decision. If a verdict differs on your machine, check that the input, seed, profile options and target file match before changing a threshold or replacing a baseline. Do not make a failing gate pass by deleting the rule that identified the problem.

## Related

[Concepts](../CONCEPTS.md) · [Reading the HTML report](../READ_REPORT.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
