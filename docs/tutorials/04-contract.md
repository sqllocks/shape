---
status: available
post:
video:
---
# Write a contract and check it

State your requirements and check them against a profile.

Status: available. Generation from a profile is being hardened.

## What you'll learn

Check uniqueness, nullability and a lower bound.

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

## 3. Write the contract

```bash {.runnable}
python - <<'PYCODE'
import json
from pathlib import Path
contract = {"columns": {"id": {"unique": True, "nullable": False}, "amount": {"min": 0}}}
Path("contract.json").write_text(json.dumps(contract, indent=2) + "\n")
print(Path("contract.json").read_text(), end="")
PYCODE
```

??? info "Output (exit 0)"

    ```text {.expected}
    {
      "columns": {
        "id": {
          "unique": true,
          "nullable": false
        },
        "amount": {
          "min": 0
        }
      }
    }
    ```

## 4. Check the contract

```bash {.runnable}
shape check orders.shape contract.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"passed": true, "violations": []}
    ```

## What's next

[Continue](05-generate-profile.md).

## Related

[Concepts](../CONCEPTS.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
