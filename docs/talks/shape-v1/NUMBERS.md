# Numbers used in the talk (October 3, 2026)

Current Fabric setup is in the [runbook](../../../integrations/fabric/RUNBOOK.md).
The later [live dry-run findings](../../plans/demo_status/DEMO-LIVE.md) record integration
fixes and outstanding live checks; they are not a timing benchmark for this talk.

Every number that appears on a slide or in the speaker notes is listed here with its source
file and, for any measurement, the machine it was measured on. `SCRIPT.md` cites these by ID
(N-xx). If a number is not in this file, don't say it.

This file holds **only committed numbers**. There are no placeholders and no to-be-filled
values: the planned features in section 6 of the talk have no numbers, and the talk gives none.

## Rules

- Quote only numbers from committed files. Nothing is extrapolated, rounded up, or combined
  into a new claim. Every measured value below is **truncated**, never rounded up.
- **Every timing and memory figure is `shape.profile` itself, released exact mode**, measured
  by `benchmarks/measure_product.py` and committed in
  `benchmarks/baselines/2026-09-30-product/product_bench.json`. Each run is a fresh process;
  the talk quotes the median of 3 (7 for start-up). The output row count must match the file
  and the profile output must be identical across the runs before a timing counts (it was,
  for every row).
- **The talk compares Shape with nothing.** No ratio against another tool, version or earlier
  result is given in any form.
- **Bounded mode and the Rust engine are being built and have not been measured.** No speed
  or memory number for either exists, and the talk gives none. In particular there is **no
  measurement of flat memory in bounded mode**.
- **No Fabric timing exists yet.** A committed Fabric dry-run timing record is required from the
  owner's dry run (R11). Local timings must never be presented as Fabric timings. If the dry
  run doesn't happen before October 3, the talk quotes no Fabric timings at all.

## Machine

| ID | Machine | Used for |
|---|---|---|
| M1 | 4 cores, `Linux-6.18.44-fc-v50-x86_64-with-glibc2.39`, Python 3.11.15, pyarrow 25.0.1, numpy 2.4.6, shape 0.9.0. Started 2026-09-30 22:15:50 (this is the talk's machine for every number in N-20 to N-27) | `benchmarks/baselines/2026-09-30-product/product_bench.json` |

M1 is not Fabric. It is a shared 4-core builder, so run-to-run spread is real: D2 Parquet ran
in 2.68 s, 1.96 s and 1.81 s (median 1.96 s). That's why the talk quotes medians and never a
best run.

## Correctness and quality

| ID | Number | Meaning | Source |
|---|---|---|---|
| N-02 | **31 fields** in a profile's per-field matrix: 27 per column plus 4 per table (row count, primary key, detected FKs, correlation matrix) | What one profile holds (slide 8) | Row labels of the per-field matrix in `docs/plans/demo_status/DM-01_verify_output.txt` |
| N-03 | **1e-9** relative (mean, std, quantiles, enum weights), **1e-6** relative (distribution parameters); exact match for dtype, null counts, cardinality, uniqueness, enums, top-500 value counts, PK/FK, pattern | The profiling tolerances written into the plan (slide 26) | Plan §2.2 T-22 |
| N-09 | **12 / 12** CI jobs green on `b965672` (Linux 3.11–3.14, macOS and Windows 3.11/3.14, audit, build, fabric-demo, bench-quick) | Gate G0 (a snapshot) | `docs/plans/demo_status/GATE-G0.md` §1 |
| N-10 | **699 tests**, coverage **86.55%** (floor 86%) in `make check` at G0 | Gate G0 (a snapshot) | `GATE-G0.md` §2 |
| N-26 | Profile output **identical across the 3 fresh-process runs**, on all 5 benchmark datasets (`output_identical_across_runs: true`) | Determinism (slide 15, 24) | `product_bench.json` |

## Timings: profiling, `shape.profile` exact mode

Source: `benchmarks/baselines/2026-09-30-product/product_bench.json`, machine **M1**. Median of
3 runs, fresh process per run, wall-clock excluding `import shape`. Rows per second is rows
divided by the median wall-clock. Also in `demo/BENCHMARKS.md` (generated from the same file).

| ID | Dataset | Wall-clock | Rows per second | Peak memory |
|---|---|---:|---:|---:|
| N-20 | D1, 200k × 6, Parquet | 0.38 s | 519,131 | 224 MB |
| **N-21** | **D2, 1M × 20, Parquet** | **1.96 s** | **509,495** | **638 MB** |
| N-22 | D2, 1M × 20, CSV | 2.02 s | 494,842 | 733 MB |
| N-23 | D3, 5M × 10, Parquet | 4.89 s | 1,021,983 | 2,107 MB |
| N-24 | D4, 100k × 200, Parquet | 4.40 s | 22,704 | 508 MB |

Say it the way `docs/talks/shape-v1/SCRIPT.md` does: "On a 4-core machine, profiling a 1M-row, 20-column
Parquet file took 1.9 s and peaked at 638 MB. Five million rows by ten columns took 4.8 s and
2,107 MB." Memory grows with the data in exact mode; slide 21 says so.

N-24 is the slowest per row: 200 columns. The talk says it's per-column work, not a claim
about why beyond that.

Raw runs (seconds), for backup slide B2:

| Dataset | Run 1 | Run 2 | Run 3 |
|---|---:|---:|---:|
| D1 Parquet | 0.37 | 0.38 | 0.40 |
| D2 Parquet | 2.68 | 1.96 | 1.81 |
| D2 CSV | 2.34 | 2.00 | 2.02 |
| D3 Parquet | 5.09 | 4.63 | 4.89 |
| D4 Parquet | 4.22 | 4.54 | 4.40 |

## Start-up

Source: `product_bench.json` → `startup`, machine **M1**, median of 7 fresh processes.

| ID | What | Wall-clock |
|---|---|---:|
| N-27 | `python -c pass`: **11 ms**; `python -c "import shape"`: **239 ms**; `shape version`: **294 ms** | as listed |

## Design parameters (not measurements)

| ID | Value | Source |
|---|---|---|
| N-60 | HLL p = 14; KLL k = 200; SpaceSaving capacity 64 | Plan T-14 |
| N-61 | HLL relative error ≤ 3 × 1.04/√2¹⁴ at the 99th percentile over 200 trials; KLL rank error ≤ 1% | P1-03 acceptance criteria (plan §7), met per the `build/main-plan` tracker (P1-03 done, `2f05b17`) |
| N-62 | Seeded XXH3-64; `1` and `1.0` hash equal; NaN and null excluded; strings as UTF-8; timestamps as int64 µs | Plan T-13; P1-02 acceptance criteria (done on `build/main-plan`, `3a3cc22`) |
| N-63 | Zero-copy acceptance: a 1M-row, 10-column batch round-trips through Rust with identical buffer addresses | P1-01a acceptance; `tests/kernel/test_native_kernel.py` on `build/main-plan` |

## Profiler rules (design parameters from the code, for slide 9)

These are the profiler's rules. Source: `src/shape/profile/reference/` on `main`.

| ID | Rule | Source |
|---|---|---|
| N-65 | Distribution fitting: a sample of **2,000** values (seed 42); candidates **normal, uniform, exponential, lognormal**; the best KS statistic among fits with **p > 0.05**; none if fewer than **20** values | `numerics.py` (`detect_distribution`, `_CANDIDATES`) |
| N-66 | Pattern detection: a sample of **1,000** strings (seed 42); **12** families: email, uuid, ssn, mac, ipv4, ipv6, iban, postal, date, phone, currency, language | `column.py` (`_PATTERNS`, `detect_pattern`) |
| N-67 | Enum: cardinality **< 200**, or cardinality ratio **< 0.30** with cardinality **< 50,000**; and the values repeat: distinct values **<= 0.5 x** non-null values, so a unique column is never an enum | `column.py` (`is_enum`) |
| N-68 | Value counts kept: top **500** per column (`value_counts_ext`), with real values. That's why a raw profile isn't safe to share | `column.py` (`top_n = 500`); plan T-22 |
| N-69 | PK: no nulls, cardinality = row count, integer or UUID-pattern string, id-like names preferred. FK across tables: a column named `<table>_id` matching that table's key | `table.py` (`_detect_primary_key`); `DM-01.md` notes |

## Demo data and drift (deterministic, seed 42, medium scale)

Source: `demo/DRIFT.md`, verified by `tests/demo/content/` and on 2026-09-30 by
`verify_snippets.sh`.

| ID | Number | Source |
|---|---|---|
| N-70 | `customers.email` null rate **4.97% → 20%** (contract `max_null_rate` 0.08) | `DRIFT.md` |
| N-71 | `orders.status` gains **`lost`** on **2%** of rows | `DRIFT.md` |
| N-72 | `orders.order_total` × **1.40**: mean **105.94 → 148.31**; max **5135.63 → 7189.882** (contract `max` 6000) | `DRIFT.md`; recomputed from `demo/make_data.py` output 2026-09-30 |
| N-73 | `products.sku`: **5,050 rows, 5,000 distinct** | `DRIFT.md` |
| N-74 | The +40% shift is **0.39** baseline standard deviations (measured 0.3876 = (148.3128 − 105.9377) / 109.3338, from the day-1 and day-2 profiles); the default `mean_shift_std` threshold is **0.5**; the demo uses **0.25** | `DRIFT.md`; plan §12.3 |
| N-75 | The "production" stand-in (slide 10): **4 tables, 640,000 rows** (customers 50,000; orders 500,000; products 5,000; returns 85,000), Shape-generated by `demo/make_data.py --scale medium --seed 42` (day 1) | `demo/make_data.py` (`SCALES`); `verify_snippets.sh` part 1 asserts the total (run 2026-09-30) |
| N-76 | The multi-table profile of the stand-in finds **3** foreign keys (slide 10): `order.customer_id`, `return.order_id`, `return.product_id`. `product.category_id` has no parent table. Live output of a deterministic run (seed 42), not a measurement | `verify_snippets.sh` part 1 (run 2026-09-30) |
| N-77 | The saved `retail_prod.shape` of the stand-in contains every distinct `first_name` (**30**), `last_name` (**30**), `city` (**15**) and `state` (**14**) of the customers table, and **2** of the **47,515** non-null email addresses (the min and max: a text column whose values are nearly all different keeps no top values, #37). The values are synthetic, made by `demo/make_data.py` | `verify_snippets.sh` part 1 (run 2026-10-03); N-68 |

## Fabric platform limits (Microsoft Learn, retrieved 2026-09-30; not measurements)

Source: plan §12.1.

| ID | Limit |
|---|---|
| N-80 | Python notebook default **2 vCores / 16 GB**; the demo sets `%%configure {"vCores": 8}` |
| N-81 | UDF: **240 s** execution limit (100 s via the public endpoint); 4 MB request; 30 MB response |
| N-82 | UDF private libraries: platform-independent `.whl` under **28.6 MB** (the 0.9.0 pure wheel is **~185 KB**, `MORNING_SUMMARY.md`) |
| N-83 | Shape's UDF refuses files above **50 MB** by default (DM-5, a parameter); table reads cap at **1,000,000** rows (DM-6) |
| N-84 | Environment publish: Quick **~5 s** (notebooks only); Full **3–6 min** plus 1–3 min at session start |

## Live Fabric timings

None on 2026-09-30. A committed Fabric dry-run timing record had not been recorded for this talk. After
the owner's dry run (R11), add rows here as N-9x, copying the SKU, vCores, runtime, row
counts and seconds exactly as recorded, and cite a committed Fabric dry-run timing record at its commit. Until
then the talk quotes no Fabric timing (fallback path, `DEMO.md`).

## Live Fabric timings

None on 2026-09-30. A committed Fabric dry-run timing record had not been recorded for this talk. After
the owner's dry run (R11), add rows here as N-9x, copying the SKU, vCores, runtime, row
counts and seconds exactly as recorded, and cite a committed Fabric dry-run timing record at its commit. Until
then the talk quotes no Fabric timing (fallback path, `DEMO.md`).

## Planned and being-built features: no numbers

The "how it will work" slides (25–27) carry no numbers: no engine timings, no bounded-mode
memory, no safe-profile sizes, no fidelity scores, no distributed-profiling timings. Add a
number here only when a committed file contains it, with its source and machine.

## Numbers from *Stop Borrowing Contoso*

None is used. They have no committed source here. The list is in `STATUS.md`, finding F7.
