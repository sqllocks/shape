---
status: available
post:
video:
---
# Generate dev data from a profile

Generate a small local CSV from the profile you saved.

Status: available. Generation from a profile is being hardened.

## What you'll learn

Load a profile, choose a seed and inspect written row counts.

## Prerequisites

Use the repository's 0.9.1 core. Read [Install](../INSTALL.md) for the public package blocker.
You need Python, the domains plugin, the databases and integrations plugins with DuckDB, and Git for the drift
page. Start in an empty working directory. You do not need an account or production data.

## Time

Allow 10 minutes for reading and reviewing the output. This is a suggested allocation.

## 1. Create the input

```bash {.runnable}
python - <<'PYDATA'
from pathlib import Path
Path("orders.csv").write_text("id,status,amount\n" + "".join(f"{i},paid,{10 + i % 5}\n" for i in range(1, 41)))
print("Wrote orders.csv: 40 rows")
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote orders.csv: 40 rows
    ```

## 2. Save the profile

```bash {.runnable}
shape profile orders.csv --name orders -o orders.shape --html report.html --json summary.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "39d2b2de2537bf1e2794d25f3ff74c79cc300d6388b3d1b9c5f8e616accef4c5", "written": "orders.shape"}
    ```

## 3. Generate and write rows

```bash {.runnable}
python - <<'PYCODE'
import shape
import pyarrow.csv as csv
from pathlib import Path
p = shape.load("orders.shape")
result = shape.generate(p, 12, seed=42)
Path("generated").mkdir()
for name, table in result.tables.items():
    csv.write_csv(table, f"generated/{name}.csv")
    print(f"Wrote generated/{name}.csv: {table.num_rows} rows")
print("Columns:", ", ".join(result.tables["orders"].column_names))
PYCODE
```

??? info "Output (exit 0)"

    ```text {.expected}
    <stdin>:4: ArtifactNotVerifiedWarning: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    Wrote generated/orders.csv: 12 rows
    Columns: id, status, amount
    ```

The Python API writes the rows explicitly here. In the CLI, `shape generate --from X.shape` writes nothing without `--format`. A seed does not prove every statistic or relationship is reproduced.

## What's next

[Continue](06-domain-duckdb.md).

## Related

[Concepts](../CONCEPTS.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
