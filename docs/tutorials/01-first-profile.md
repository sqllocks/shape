---
status: available
post:
video:
---
# Your first profile

Save a local table as a profile you can review.

Status: available. Generation from a profile is being hardened.

## What you'll learn

Create rows, save a safe capture and validate it.

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

## 2. Profile the table

```bash {.runnable}
shape profile orders.csv --name orders -o orders.shape --html report.html --json summary.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "39d2b2de2537bf1e2794d25f3ff74c79cc300d6388b3d1b9c5f8e616accef4c5", "written": "orders.shape"}
    ```

## 3. Check the saved capture

```bash {.runnable}
shape profile validate --safe orders.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    CLEAN: no leaks found in orders.shape
    ```

## What's next

[Continue](02-read-report.md).

## Related

[Concepts](../CONCEPTS.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
