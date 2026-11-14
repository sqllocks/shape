# Migrate scripts that parse shape plan

Status: available.

Since 0.9.4, `shape plan` prints **text by default**. Scripts that parsed its former JSON
output must add `--json`. The JSON keys are unchanged. Keep standard error separate:
origin-verification notes and other diagnostics belong there.

These local commands run in an empty directory in the
[example environment](contributing/EXAMPLES.md). They use synthetic values only.

<!-- example: 0 -->

```bash {.runnable-reference}
python - <<'PYDATA'
import pyarrow as pa
import shape
shape.save(shape.profile(pa.table({"amount": [1, 2, 3]}), name="orders"), "plan.shape", capture="full")
print("Wrote synthetic plan.shape")
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote synthetic plan.shape
    ```

## Human-readable default

<!-- example: 1 -->

```bash {.runnable-reference}
shape plan plan.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: plan.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    Executable: yes
    Summary: preserved: 15, approximate: 3, not_modelled: 5
    Status           Evidence                                         Reason
    preserved        orders.amount.name                               kept as it is
    preserved        orders.amount.dtype                              kept as it is
    preserved        orders.amount.is_primary_key                     kept as it is
    preserved        orders.amount.is_foreign_key                     kept as it is
    preserved        orders.amount.null_rate                          each row is null with the column's null rate, independently of the other columns
    preserved        orders.amount.null_count                         each row is null with the column's null rate, independently of the other columns
    preserved        orders.amount.is_unique                          a sequence is unique and not an enum
    preserved        orders.amount.is_enum                            a sequence is unique and not an enum
    preserved        orders.amount.value_counts_ext                   a sequence is unique and not an enum
    preserved        orders.amount.value_counts_ext_order             a sequence is unique and not an enum
    preserved        orders.amount.cardinality                        a sequence with the profile's first value
    preserved        orders.amount.cardinality_ratio                  a sequence with the profile's first value
    preserved        orders.amount.min_value                          the sequence starts at the profile's minimum
    approximate      orders.amount.max_value                          a gap-free sequence: the profile's keys may have gaps
    approximate      orders.amount.mean                               a gap-free sequence: the profile's keys may have gaps
    approximate      orders.amount.std                                a gap-free sequence: the profile's keys may have gaps
    not_modelled     orders.amount.adequacy                           how much of the data the profile saw (values seen, the error of the null rate): a property of the profile, not of the data, so nothing is generated from it
    not_modelled     orders.amount.type_inference                     how the column's type was found and how well the values fit it: the generated column takes the profile's type, and nothing is inferred
    preserved        orders.row_count                                 the `profile` scale preset has the profile's row count
    preserved        orders.primary_key                               generated as a unique key
    not_modelled     dataset.missingness_joint                        which values are missing together: every column's nulls are independent
    not_modelled     dataset.dependencies                             functional dependencies and conditional distributions between columns: columns are independent except for the correlations above
    not_modelled     dataset.row_order                                the order of the rows is not reproduced
    ```

## Machine-readable migration

Replace a parsed invocation with the following form; do not parse the text display.

<!-- example: 2 -->

```bash {.runnable-reference}
shape plan plan.shape --json > plan.json
python - <<'PYDATA'
import json
from pathlib import Path
plan = json.loads(Path("plan.json").read_text())
print("Parsed JSON format:", plan["format"])
print("Command:", plan["command"])
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: plan.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    Parsed JSON format: shape-result
    Command: plan
    ```

[CLI](CLI.md) · [New feature workflows](NEW_FEATURES.md)
