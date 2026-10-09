---
status: available
post:
video:
---
# Commit a shape and catch drift

Keep a reviewed profile in Git and detect a changed extract.

Status: available. Generation from a profile is being hardened.

## What you'll learn

Commit a baseline and understand a nonzero drift exit.

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

## 2. Save the baseline

```bash {.runnable}
shape profile orders.csv --name orders -o orders.shape --html report.html --json summary.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "39d2b2de2537bf1e2794d25f3ff74c79cc300d6388b3d1b9c5f8e616accef4c5", "written": "orders.shape"}
    ```

## 3. Commit in an isolated Git repository

```bash {.runnable}
git init -q --initial-branch=main
git config user.name "Tutorial User"
git config user.email "tutorial@example.invalid"
git add orders.shape
git -c core.hooksPath=/dev/null commit -q -s -m "Record orders baseline"
git status --short -- orders.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

## 4. Change the extract

```bash {.runnable}
python - <<'PYCODE'
from pathlib import Path
Path("next.csv").write_text("id,status,amount\n" + "".join(f"{i},paid,\n" for i in range(1, 41)))
print("Wrote next.csv: 40 rows, amount is null")
PYCODE
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote next.csv: 40 rows, amount is null
    ```

## 5. Profile the new extract

```bash {.runnable}
shape profile next.csv --name orders -o next.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "a6ef2eaf01409633202c7da28e980fc73f0cf5746bbf2e90ca779d61b940d6c7", "written": "next.shape"}
    ```

## 6. Catch the change

```bash {.runnable}
shape diff orders.shape next.shape --fail-on-drift
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: note: next.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: amount: dtype_change [breaking]
    shape: amount: null_rate_change [breaking]
    shape: amount: cardinality_change [cosmetic]
    bump: major (2 breaking, 0 additive, 1 cosmetic)
    {"changes": [{"baseline": "integer", "class": "breaking", "class_reason": "the column has another type", "column": "amount", "current": "float", "kind": "dtype_change", "score": 1.0, "severity": "high"}, {"baseline": 0.0, "class": "breaking", "class_reason": "a column that never had nulls has them now (readers may not expect)", "column": "amount", "current": 1.0, "kind": "null_rate_change", "score": 1.0, "severity": "medium"}, {"baseline": 5, "class": "cosmetic", "class_reason": "the schema and the constraints are unchanged, values moved", "column": "amount", "current": 0, "kind": "cardinality_change", "score": 1.0, "severity": "medium"}], "drifted": true, "semver": {"additive": 0, "breaking": 2, "bump": "major", "cosmetic": 1}}
    ```

Exit 1 is expected: the amount column changes. Review [what to do when drift goes red](../DRIFT_RED.md) before you replace the baseline. The tutorial changes only its isolated Git repository.

## What's next

[Continue](04-contract.md).

## Related

[Concepts](../CONCEPTS.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
