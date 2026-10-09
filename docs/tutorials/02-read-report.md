---
status: available
post:
video:
---
# Read the report

Read a saved report before using a profile as a baseline.

Status: available. Generation from a profile is being hardened.

## What you'll learn

Find counts, nulls and capture policy in a saved profile.

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

## 2. Build the report

```bash {.runnable}
shape profile orders.csv --name orders -o orders.shape --html report.html --json summary.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "39d2b2de2537bf1e2794d25f3ff74c79cc300d6388b3d1b9c5f8e616accef4c5", "written": "orders.shape"}
    ```

## 3. Read its structure

```bash {.runnable}
python - <<'PYCODE'
import shape
from pathlib import Path
p = shape.load("orders.shape")
table = p.tables["orders"]
print("Report exists:", Path("report.html").is_file())
print("Rows:", table["row_count"])
print("Columns:", ", ".join(table["columns"]))
print("Null rates:", {name: col["null_rate"] for name, col in table["columns"].items()})
print("Capture:", p.capture["mode"])
PYCODE
```

??? info "Output (exit 0)"

    ```text {.expected}
    <stdin>:3: ArtifactNotVerifiedWarning: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    Report exists: True
    Rows: 40
    Columns: id, status, amount
    Null rates: {'id': 0.0, 'status': 0.0, 'amount': 0.0}
    Capture: safe
    ```

Open `report.html` in your browser. Use [the report guide](../READ_REPORT.md) to read every section. Reports use inline styles and SVG, with no external assets.

## What's next

[Continue](03-drift.md).

## Related

[Concepts](../CONCEPTS.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
