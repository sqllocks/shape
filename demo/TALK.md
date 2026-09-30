# Talk kit: Shape in Microsoft Fabric

Profile data where it lives, gate pipelines on what the data looks like, and catch drift
before it reaches a report. About 20 minutes live.

**Numbers rule.** Every number in this document is copied from a committed file and cites
its source and the machine it was measured on. Nothing is extrapolated. Live Fabric timings
are only shown as measured, from [`LIVE_TIMINGS.md`](LIVE_TIMINGS.md), which is empty until
the dry run. The full table is [`BENCHMARKS.md`](BENCHMARKS.md).

## Before you go on stage

- [ ] Owner dry run done, `LIVE_TIMINGS.md` filled in (`integrations/fabric/RUNBOOK.md`, section 11).
- [ ] Day-1 and day-2 tables loaded: `orders_day1`, `orders_day2` (from `demo/make_data.py`).
- [ ] A pre-run of every step, and the artifacts (HTML report, `.shape` files) saved in the
      lakehouse `Files/shape/` folder for the fallbacks below.
- [ ] Python notebook session already started with `vCores: 8`; the Environment already
      published in Quick mode.

## Storyline

| # | Beat | Show | Say |
|---|---|---|---|
| 1 | The problem | Slide | A pipeline succeeds while the data quietly changes: nulls creep up, a new status appears, an amount drifts. Row counts and schemas still look fine. |
| 2 | Profile in a notebook | `shape_profile` on `orders_day1` | `shape.profile` reads the Delta table with `deltalake` and returns a profile: types, null rates, cardinality, distributions, patterns, keys. |
| 3 | The HTML report | The inline report | A self-contained page: per-column table, distributions, patterns. It works offline and is saved next to the `.shape` artifact. |
| 4 | Install once, run in PySpark | The Environment, then `shape_profile_spark` | Upload the wheel to a Fabric Environment once; the PySpark notebook produces the same result JSON. |
| 5 | Contract gate passes on day 1 | Pipeline `shape_gate_notebook`, default parameters | The contract (`demo/contracts/orders.json`) is a small JSON file. Day 1 passes; the pipeline goes green. |
| 6 | Day 2 fails, with the reason | Same pipeline, `tableName = orders_day2` | Fails at the gate. The message names the rules (see the table below), not just "failed". |
| 7 | The same check as a UDF | Pipeline `shape_gate_udf`: `profileLakehouseFile`, then `checkProfile` with `failOnViolation` | Profile and check without a notebook session; for files up to the UDF size cap (default 50 MB); larger data goes through the notebook. |
| 8 | Measured numbers | The table below | Wall-clock, rows per second and peak memory, as measured. Quote only what is on the sheet. |

## What day 2 breaks (beat 6)

From [`DRIFT.md`](DRIFT.md), verified in `tests/demo/content/`:

| Table | Rule that fails | Day 1 | Day 2 |
|---|---|---|---|
| `customers` | `email` `max_null_rate` 0.08 | null rate 4.97% | 20% |
| `orders` | `status` `allowed_values` | 5 values | new value `lost` |
| `orders` | `order_total` `max` 6000 | max 5135.63 | max 7189.882 (every value x 1.40) |
| `products` | `sku` `unique` | 5,000 unique | 5,050 rows, 5,000 distinct |

The pipeline on stage gates the `orders` table, so beat 6 shows the `orders` rows. Say the
`customers` and `products` rows only if you run those tables too.

Caveat if you show `shape.diff` live: the default mean-shift threshold (0.5 standard
deviations) does not flag the +40% `order_total` shift, which is 0.43 standard deviations.
The demo diff uses `thresholds={"mean_shift_std": 0.25}`; the contract gate is unaffected.
See `DRIFT.md`.

## Benchmark sheet (beat 8)

Quote only these. Machine for all rows: **4 cores, Linux, Python 3.11.15**, not Fabric.
Source: `benchmarks/baselines/2026-09-30-product/product_bench.json` (median of 3 runs, fresh
process each, `shape.profile` in exact mode, values truncated). Full sheet:
[`BENCHMARKS.md`](BENCHMARKS.md).

| Workload | Wall-clock | Rows per second | Peak memory |
|---|---:|---:|---:|
| Profile D1 (200k rows x 6 columns, Parquet) | 0.38 s | 519,131 | 224 MB |
| Profile D2 (1M rows x 20 columns, Parquet) | 1.96 s | 509,495 | 638 MB |
| Profile D3 (5M rows x 10 columns, Parquet) | 4.89 s | 1,021,983 | 2,107 MB |
| Profile D4 (100k rows x 200 columns, Parquet) | 4.40 s | 22,704 | 508 MB |

| Start-up (median of 7) | Wall-clock |
|---|---:|
| `import shape` | 239 ms |
| `shape version` | 294 ms |

Say exactly this, and no more:

- "On a 4-core machine, profiling a 1M-row, 20-column Parquet file took 1.9 s and peaked at
  638 MB. Five million rows by ten columns took 4.8 s and 2,107 MB."
- "Memory grows with the data in this mode. A bounded mode whose memory stays flat is being
  built; I have no measurement of it yet."
- "Fabric notebooks default to 2 vCores, so we set 8. Here is what we measured live:" and
  then only the numbers in `LIVE_TIMINGS.md`.

Do **not** say: any number on Fabric that is not in `LIVE_TIMINGS.md`; any number for other
data sizes or core counts; any speed or memory figure for bounded mode or the Rust engine.

## Fallbacks if something is slow or breaks live

| Symptom | Do this |
|---|---|
| Notebook session slow to start | Start it before the talk. If it is still cold, open the saved HTML report from `Files/shape/orders_day1/<timestamp>/` and narrate; run the notebook while taking questions. |
| `%%configure` did not apply (2 vCores) | Stop the session, rerun that cell first in a fresh session. If out of time, proceed: profiling `orders` still finishes, more slowly. Do not quote timings. |
| Environment publish is slow | Use the Python notebook path (beats 2 to 3), and show the pre-published Environment. Quick mode is about 5 s; Full mode takes minutes, so do not publish on stage. |
| Pipeline run queued or slow | Show the last completed run for day 1 and day 2 in the run history, then explain the gate. |
| Pipeline expression error (`exitValue`) | The exact expression is flagged in the runbook to verify on first run; open the notebook activity output and read `passed` and `violations` there. |
| UDF cold start or timeout (240 s limit) | Show the saved result from a prior run; the UDF only profiles small files by design, so choose the small file, not `orders_day2`, if you rerun. |
| Nothing works | Run locally: `shape profile orders.parquet -o o.shape --html o.html`, then `shape check o.shape demo/contracts/orders.json` (exit 1 on day 2), from the data written by `demo/make_data.py`. |

## Wording

Shape is early access: profiling is available now; data generation and pipeline integration
are in progress. Don't claim more.

## Where things are

- Data and drift: `demo/make_data.py`, `demo/DRIFT.md`, `demo/contracts/`
- Fabric artifacts: `integrations/fabric/` (notebooks, `environment/`, `udf/`, `pipelines/`,
  `RUNBOOK.md`)
- Benchmarks: `benchmarks/baselines/2026-09-30-product/`, `benchmarks/measure_product.py` and `demo/BENCHMARKS.md`
