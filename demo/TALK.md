# Talk kit: Shape in Microsoft Fabric

Profile data where it lives, gate pipelines on what the data looks like, and catch drift
before it reaches a report. Timing and cuts: `docs/talks/shape-v1/OUTLINE.md`.

**Numbers rule.** Every number in this document is copied from a committed file and cites
its source and the machine it was measured on. Nothing is extrapolated. Live Fabric timings
are only shown as measured, from [`LIVE_TIMINGS.md`](LIVE_TIMINGS.md), which is empty until
the dry run. The full table is [`BENCHMARKS.md`](BENCHMARKS.md).

## Before you go on stage

- [ ] Owner dry run done, `LIVE_TIMINGS.md` filled in (`integrations/fabric/RUNBOOK.md`, section 11).
- [ ] Day-1 and day-2 tables loaded: `orders_day1`, `orders_day2` (from `demo/make_data.py`).
- [ ] A pre-run of every step, and the artifacts (HTML report, `.shape` files) saved in the
      lakehouse `Files/shape/` folder for the fallbacks below.
- [ ] Python notebook session already started with `vCores: 8` (`integrations/fabric/RUNBOOK.md`,
      section 4); the Environment already published in Quick mode.

## Storyline

| # | Beat | Show | Say |
|---|---|---|---|
| 1 | The problem | Slide | A pipeline succeeds while the data quietly changes: nulls creep up, a new status appears, an amount drifts. Row counts and schemas still look fine. |
| 2 | Profile in a notebook | `shape_profile` on `orders_day1` | `shape.profile` reads the Delta table with `deltalake` and returns a profile: types, null rates, cardinality, distributions, patterns, keys. |
| 3 | The HTML report | The inline report | A self-contained page: per-column table, distributions, patterns. It works offline and is saved next to the `.shape` artifact. |
| 4 | Install once, run in PySpark | The Environment, then `shape_profile_spark` | Upload the wheel to a Fabric Environment once; the PySpark notebook produces the same result JSON. |
| 5 | Contract gate passes on day 1 | Pipeline `shape_gate_notebook`, default parameters | The contract (`demo/contracts/orders.json`) is a small JSON file. Day 1 passes; the pipeline goes green. |
| 6 | Day 2 fails, with the reason | Same pipeline, `tableName = orders_day2` | Fails at the gate. The message names the rules (see the table below), not just "failed". |
| 7 | The same check as a UDF | Pipeline `shape_gate_udf`: `profileLakehouseFile`, then `checkProfile` with `failOnViolation` | Profile and check without a notebook session; for files up to the UDF size cap (default 50 MB: `max_megabytes` in `src/shape/integrations/fabric/udf.py`, RUNBOOK section 1.1); larger data goes through the notebook. |
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

The notebook's own diff (day 2 with the saved day-1 artifact as `baselinePath`) shows only
`status`'s new value `lost`: the artifact is a safe capture, which leaves out what `order_total`'s
comparisons need (`DRIFT.md`, "In the Fabric notebook"). Show `order_total`'s drift through the
contract violation (`max`), or with the full-capture diff on the command line below.

Caveat if you show `shape.diff` live: the default mean-shift threshold (0.5 standard
deviations) does not flag the +40% `order_total` shift, which is 0.39 standard deviations (0.3876,
derived in `DRIFT.md` from the stage diff's means and the day-1 profile's standard deviation).
The demo diff uses `thresholds={"mean_shift_std": 0.25}`; the contract gate is unaffected.
See `DRIFT.md`. On the command line, do not run a plain `shape diff` of the two days: on the
fallback's profiles below it prints 1,155,856 bytes of JSON (`DRIFT.md`, "On the command line"),
because every day-2 `order_total` value is a new categorical value. Run
`shape diff o1.shape o2.shape --mean-shift-std 0.25 --min-severity medium`, which prints 942
bytes (same source): the `mean_shift` and `category_shift` changes only (`status`'s new value `lost` is low severity,
so the contract check shows it). `shape check` and `shape diff` also print a note on standard
error that the `.shape` file is not signed; that is expected for the demo files.

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
- "Memory grows with the data in this mode. Bounded profiling is available in 0.9.1; this sheet
  measures exact mode only."
- "Fabric notebooks default to 2 vCores, so we set 8. Here is what we measured live:" and
  then only the numbers in `LIVE_TIMINGS.md`. (The 2-vCore default is Microsoft's documented
  limit, recorded in `integrations/fabric/RUNBOOK.md` section 1.1.)

Do **not** say: any number on Fabric that is not in `LIVE_TIMINGS.md`; any number for other
data sizes or core counts; any speed or memory figure for bounded mode or the Rust engine.

## Fallbacks if something is slow or breaks live

| Symptom | Do this |
|---|---|
| Notebook session slow to start | Start it before the talk. If it is still cold, open the saved HTML report from `Files/shape/orders_day1/<timestamp>/` and narrate; run the notebook while taking questions. |
| `%%configure` did not apply (2 vCores, RUNBOOK section 1.1) | Stop the session, rerun that cell first in a fresh session. If out of time, proceed: profiling `orders` still finishes, more slowly. Do not quote timings. |
| Environment publish is slow | Use the Python notebook path (beats 2 to 3), and show the pre-published Environment. Quick mode is about 5 s; Full mode takes minutes (both RUNBOOK section 1.1), so do not publish on stage. |
| Pipeline run queued or slow | Show the last completed run for day 1 and day 2 in the run history, then explain the gate. |
| Pipeline expression error (`exitValue`) | The exact expression is flagged in the runbook to verify on first run; open the notebook activity output and read `passed` and `violations` there. |
| UDF cold start or timeout (240 s limit, RUNBOOK section 1.1) | Show the saved result from a prior run; the UDF only profiles small files by design, so choose the small file, not `orders_day2`, if you rerun. |
| Nothing works | Run locally, from the repository root: `python demo/make_data.py --out data`, `shape profile data/day1/orders.parquet -o o1.shape --html o1.html --capture full`, `shape check o1.shape demo/contracts/orders.json` (exit 0), `shape profile data/day2/orders.parquet -o o2.shape --html o2.html --capture full`, `shape check o2.shape demo/contracts/orders.json` (exit 1, the `status` and `order_total` violations), then the diff command above. The local demo data is synthetic, so it is profiled with full capture: the contract's `order_total` `min` and `max` need the real values, which the default safe capture withholds (the check would then exit 2). |

## Wording

Shape 0.9.1 is early access. Profiling, generation and the released sink plugins are
available. Describe only the installed plugins and the behavior demonstrated here.

## Where things are

- Data and drift: `demo/make_data.py`, `demo/DRIFT.md`, `demo/contracts/`
- Fabric artifacts: `integrations/fabric/` (notebooks, `environment/`, `udf/`, `pipelines/`,
  `RUNBOOK.md`)
- Benchmarks: `benchmarks/baselines/2026-09-30-product/`, `benchmarks/measure_product.py` and `demo/BENCHMARKS.md`
