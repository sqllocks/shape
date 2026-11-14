# Check a daily order file

Status: available.

## What you'll learn

Check the spelling of a load timestamp with a whole-value regex, enforce a batch-relative
order date, and distinguish missing evidence from a passing check.

## Prerequisites

Use the [locked example environment](contributing/EXAMPLES.md) from a repository checkout.
It supplies `SHAPE_DOCS_REPO`, Shape and PyArrow. Start in an empty directory.
No account is needed. All 2,078 orders below are synthetic.

## Time

About 10 minutes.

## 1. Create the batch and contract

The load timestamp is text; the order date is an Arrow timestamp. The contract requires an
ISO-shaped timestamp and an order date on or after the explicit batch date. This regex
checks spelling, not calendar validity. The relative minimum checks a calendar boundary.

We save a **full** profile only because these rows are synthetic. Safe capture can redact
the date extremes, and exact data does not override that privacy refusal for relative bounds.
A full profile can still have incomplete category evidence when cardinality is high.

<!-- example: 0 -->

```bash {.runnable-reference}
python - <<'PYDATA'
import datetime as dt
import json
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
import shape

start = dt.datetime(2026, 10, 7)
count = 2078
loads = [(start + dt.timedelta(seconds=i)).isoformat() for i in range(count)]
dates = [start + dt.timedelta(hours=12)] * count
orders = pa.table({"order_id": range(1, count + 1), "load_date": loads, "order_date": dates})
pq.write_table(orders, "orders.parquet")
shape.save(shape.profile(orders, name="orders"), "orders.shape", capture="full")
loads[-1] = "not-a-timestamp"
dates[-104:] = [start - dt.timedelta(days=1)] * 104
bad = pa.table({"order_id": range(1, count + 1), "load_date": loads, "order_date": dates})
pq.write_table(bad, "bad-orders.parquet")
contract = {"columns": {
    "load_date": {"regex": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"},
    "order_date": {"min": {"as_of_offset_days": 0}}
}}
Path("daily.contract.json").write_text(json.dumps(contract, indent=2))
print("Wrote 2078 synthetic orders, a damaged batch and daily.contract.json")
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote 2078 synthetic orders, a damaged batch and daily.contract.json
    ```

## 2. Check the complete file

`--data` tests every non-null load timestamp and supplies the exact order-date extreme.
`--as-of` is explicit: rerunning this example tomorrow does not silently move its boundary.
The default timezone is UTC; set contract `as_of_timezone` to an IANA name when the batch
uses a different calendar. `min` includes local midnight; `max` includes the entire final day.

<!-- example: 1 -->

```bash {.runnable-reference}
shape check orders.shape daily.contract.json --data orders.parquet --as-of 2026-10-07
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"as_of": "2026-10-07", "passed": true, "violations": []}
    ```

## 3. Read both violations

One damaged timestamp fails the default match rate of 1.0. The 104 backdated orders fail
`min`. The violation reports the earliest date, not a count of every backdated row.
The profile is the same in both checks: these value checks use the supplied file.
Privacy rules still control failure samples; an empty `non_matching_values` list does not
mean zero failures. Use the aggregate count and match rate to read the verdict.

<!-- example: 2 -->

```bash {.runnable-reference}
shape check orders.shape daily.contract.json --data bad-orders.parquet --as-of 2026-10-07
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"as_of": "2026-10-07", "passed": false, "violations": [{"column": "load_date", "expected": {"expression": "\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}", "min_match_rate": 1.0}, "observed": {"match_rate": 0.9995187680461982, "non_matching_count": 1, "non_matching_values": [], "tested_count": 2078}, "rule": "regex"}, {"column": "order_date", "expected": {"as_of": "2026-10-07", "as_of_offset_days": 0, "bound": "2026-10-07T00:00:00+00:00"}, "observed": "2026-10-06T00:00:00", "rule": "min"}]}
    ```

## 4. Observe a blind check

Without `--data`, the high-cardinality load timestamps are not all present in the stored
value counts. A full capture does not guarantee a complete list. Shape reports `not_evaluable`,
`passed: false` and exit 2, rather than claiming that unobserved values matched. The date
minimum can still use the retained extreme. Supply the exact batch to resolve this blind rule.
A complete stored value list could evaluate regex without `--data`; this example deliberately
exceeds that evidence. A known stored nonmatch at rate 1.0 can also prove a failure.

<!-- example: 3 -->

```bash {.runnable-reference}
shape check orders.shape daily.contract.json --as-of 2026-10-07
```

??? info "Output (exit 2)"

    ```text {.expected}
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: error: not evaluable: regex for load_date has incomplete or withheld stored values; pass --data or re-profile with capture="full"
    {"as_of": "2026-10-07", "not_evaluable": [{"column": "load_date", "reason": "regex for load_date has incomplete or withheld stored values; pass --data or re-profile with capture=\"full\"", "rule": "regex"}], "passed": false, "violations": []}
    ```

## What's next

Use `nullable: false` if nulls are forbidden: regex ignores nulls and an all-null column
passes regex alone. Use `{"expression": "...", "min_match_rate": 0.99}` only when the
business requirement permits mismatches. RE2 refuses lookaround and backreferences;
invalid rules are input errors before data evaluation. Missing `--as-of` is also an input error.

## Related

[Contracts](CONTRACTS.md) · [Starter contract tutorial](tutorials/04-contract.md) ·
[New feature workflows](NEW_FEATURES.md)
