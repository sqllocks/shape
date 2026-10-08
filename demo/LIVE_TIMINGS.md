# Live Fabric timings — PLACEHOLDER

> **Not measured yet.** Every cell below is empty until the owner's live dry run
> (`integrations/fabric/RUNBOOK.md`, section 11). Nothing here may be filled in from the
> local benchmarks, extrapolated, or estimated. Record what the Fabric run shows, as measured.

Capacity SKU: `TBD` · Notebook vCores (`%%configure`): `TBD` · Runtime: `TBD` · Date: `TBD`

| Step | Input (table or file, rows) | Wall-clock s | Result | Notes |
|---|---|---:|---|---|
| `shape_profile` Python notebook, day 1 | `orders_day1`, TBD rows | TBD | TBD | |
| `shape_profile` Python notebook, day 2 | `orders_day2`, TBD rows | TBD | TBD | |
| `shape_profile_spark` PySpark notebook, day 1 | `orders_day1`, TBD rows | TBD | TBD | |
| Pipeline `shape_gate_notebook`, day 1 | | TBD | TBD | |
| Pipeline `shape_gate_notebook`, day 2 | | TBD | TBD | |
| UDF `profileLakehouseFile`, day 1 | `demo/day1/orders.parquet` | TBD | TBD | |
| UDF `checkProfile`, day 2 | | TBD | TBD | |
| Pipeline `shape_gate_udf`, day 2 | | TBD | TBD | |

Once filled in, `demo/TALK.md` may quote these rows, citing this file.
