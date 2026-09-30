# Numbers used in the talk

Every number that appears on a slide or in the speaker notes is listed here with its source
file and, for any measurement, the machine it was measured on. `SCRIPT.md` cites these by ID
(N-xx). If a number is not in this file, don't say it.

## Rules (from plan §6.4 and decision DM-2)

- Quote only numbers from committed files. Nothing is extrapolated, rounded up, or combined
  into a new claim.
- **Every timing below is from the benchmark ports, not the product.** "Port" means the
  vectorised numpy + pyarrow reference port (`benchmarks/vs_spindle/profile_1to1/port.py`,
  and the retail port for generation). `shape.profile` was ported from that code, but
  `shape.profile` itself has **not** been timed against Spindle: `results.json` has
  `"shape": null`.
- **The Rust kernel has not been benchmarked.** No speed number for it exists, and the talk
  gives none.
- **No Fabric timing exists yet.** `demo/LIVE_TIMINGS.md` is an empty placeholder until the
  owner's dry run. Local timings must never be presented as Fabric timings.
- Targets (10x minimum, 30x stretch) are shown only labelled as **targets**.

## Machines

| ID | Machine | Used for |
|---|---|---|
| M1 | 4 cores, Intel(R) Xeon(R) Processor @ 2.10GHz, `Linux-6.18.44-fc-v37-x86_64-with-glibc2.39`, Python 3.11.15; Spindle venv pandas 3.0.6 / numpy 2.4.6 / pyarrow 25.0.1; port venv pyarrow 25.0.1. Started 2026-09-29 22:22:57 | `benchmarks/baselines/2026-09-29/*` (plan §3, `demo/BENCHMARKS.md`) |
| M2 | 4 cores, Intel(R) Xeon(R) Processor @ 2.10GHz, `Linux-6.18.44-fc-v50-x86_64-with-glibc2.39`, Python 3.11.15; both venvs numpy 2.4.6 / pyarrow 25.0.1. Started 2026-09-30 13:39:08 | `benchmarks/vs_spindle/results.json` (`--quick`, 3 runs) |

Neither machine is Fabric. Both are shared 4-core builders; Gate G0 notes that ratios on them
vary by about 10% (`docs/plans/demo_status/GATE-G0.md`).

Baseline under test: **Spindle 3.0.1, git `422e78df2267e73bb2fa976267e48cb437861e2f`**
(plan T-20; `results.json` → `spindle_commit`).

## Correctness and parity

| ID | Number | Meaning | Source |
|---|---|---|---|
| N-01 | **30 / 30 datasets** | `shape.profile` vs Spindle's `DataProfiler`: every dataset PASS, every field bitwise-identical (every matrix cell `n/n*`), and nothing in the "within tolerance but not bitwise identical" section. Datasets: D1–D4 (CSV and Parquet), MT and MT Parquet, 20 EDGE variants | `docs/plans/demo_status/DM-01_verify_output.txt` (ends `EXIT=0`); `DM-01.md`; re-run by the lead, `MORNING_SUMMARY.md` |
| N-02 | **34 fields** per column/table in the parity matrix (dtype, null_count, null_rate, cardinality, … quantiles, histograms, value_counts_ext, fit_score) | What "field by field" covers | Row labels of the per-field matrix in `DM-01_verify_output.txt` |
| N-03 | **1e-9** relative (mean, std, quantiles, enum weights), **1e-6** relative (distribution parameters); exact match for dtype, null counts, cardinality, uniqueness, enums, top-500 value counts, PK/FK, pattern | T-22 profiling parity **tolerances** (the run in N-01 was tighter: bitwise) | Plan §2.2 T-22 |
| N-04 | **60 / 60 columns** equivalent, at small, medium and large scale | Retail generation port vs Spindle under T-21 (statistical, not bitwise) | `benchmarks/baselines/2026-09-29/retail_verify_{small,medium,large}.txt`; plan §3.2 |
| N-05 | Spindle's own seed-to-seed KS for `order.order_total` at large scale: **0.14–0.37** (seeds 43–47) | Why generation equivalence uses Spindle's own spread and a fixed seed set | Plan §2.2 T-21; `retail_large_seed_study.json` |
| N-06 | Baseline seeds **exactly 43, 44, 45, 46**; reference seed 42; Shape seed 1042 | The fixed T-21 seed set (no seed shopping) | Plan §2.2 T-21 |
| N-07 | **41 mismatches** on each of `d1.csv` and `d1.parquet`; `run.py` exit 1 | The stale Spindle-output cache bug, reproduced before the fix | Commit `3b7c1f0` message (branch `build/main-plan`), "P0-07 fix: key the Spindle profile cache by input content" |
| N-08 | **277** Spindle files, all mapped to a work package | Coverage map is machine-checked | `GATE-G0.md` (`check_coverage.py`: OK); `docs/plans/spindle_coverage.tsv` |
| N-09 | **12 / 12** CI jobs green on `b965672` (Linux 3.11–3.14, macOS and Windows 3.11/3.14, audit, build, fabric-demo, bench-quick) | Gate G0 | `GATE-G0.md` §1 |
| N-10 | **699 tests**, coverage **86.55%** (floor 86%) in `make check` at G0 | Gate G0 | `GATE-G0.md` §2 |
| N-11 | **96 passed** in `tests/kernel`, under `SHAPE_KERNEL=rust` and again under `SHAPE_KERNEL=python` | Rust kernel and its Python twin agree (hashing, sketches, zero-copy) on `build/main-plan` @ `ef6a8cd` | Run in this session; see `STATUS.md`. Not a committed file: say "the kernel test suite passes in both modes", not the count, unless re-run |

## Timings: profiling (port, not product)

Source: `benchmarks/baselines/2026-09-29/profile_bench.json`, machine **M1**. Median of 5
runs, fresh process per run. "MT" is the port's default threading on 4 cores; "1T" is one
thread. Equivalence first: `profile_verify.txt`, every field bitwise-identical, 30/30 PASS.
Also in `demo/BENCHMARKS.md` (generated from the same files).

| ID | Dataset | Spindle 3.0.1 | Port MT | Port 1T | Speedup MT / 1T |
|---|---|---:|---:|---:|---:|
| N-20 | D1, 200k × 6, Parquet | 1.42 s | 0.18 s | 0.23 s | 8.1x / 6.3x |
| **N-21** | **D2, 1M × 20, Parquet** | **26.51 s** | **1.82 s** | **4.83 s** | **14.6x / 5.5x** |
| N-22 | D2, 1M × 20, CSV | 31.12 s | 1.82 s | 4.99 s | 17.1x / 6.2x |
| N-23 | D3, 5M × 10, Parquet | 47.91 s | 5.35 s | 13.73 s | 9.0x / 3.5x |
| N-24 | D4, 100k × 200, Parquet | 28.31 s | 4.23 s | 13.34 s | 6.7x / 2.1x |
| N-25 | MT, 3 tables, FK detection | 2.01 s | 0.38 s | 0.36 s | 5.3x / 5.6x |

Say it the way `demo/TALK.md` does: "On a 4-core machine, after the outputs were checked
identical, profiling a 1M-row, 20-column Parquet file took 26.5 s with Spindle and 1.8 s
with the vectorised port. These are timings of the port that `shape.profile` was built
from, not of the library, and not measured in Fabric."

N-24 and N-25 show that the port **misses** the 10x target on wide data and small
multi-table data. Plan §3.3 says so, and the talk says so too.

## Timings: harness run (`results.json`)

Source: `benchmarks/vs_spindle/results.json`, machine **M2**, `mode: quick`, median of 3
runs. Every row's verifier status is `pass` before its timing counts. `"shape": null`: the
product itself is not yet in the harness.

| ID | Workload | Spindle | Reference port | Ratio |
|---|---|---:|---:|---:|
| N-30 | profile D1 CSV | 1.55 s | 0.33 s | 4.7x |
| N-31 | profile D1 Parquet | 1.33 s | 0.34 s | 3.9x |
| N-32 | profile D2 CSV | 31.73 s | 2.86 s | 11.1x |
| N-33 | profile D2 Parquet | 26.30 s | 2.27 s | 11.6x |
| N-34 | retail small (21,750 rows) | 0.25 s | 0.13 s | 1.9x |
| N-35 | retail medium (1,965,400 rows) | 5.79 s | 0.98 s | 5.9x |

Use: the backup slide, and the point that two runs a day apart on similar machines give
different ratios (D2 Parquet: 14.6x in N-21 on M1 with 5 runs, 11.6x in N-33 on M2 with 3
runs). That is why the gates are same-job ratios (T-19), not absolute numbers.

## Timings: retail generation (port, not product)

Source: `benchmarks/baselines/2026-09-29/retail_bench.json`, machine **M1**, seed 42, median
of 3 runs, generate + write Parquet. Equivalence: N-04. Generation is **not** part of the
early-access product; this port produces the demo data.

| ID | Scale | Rows | Spindle 3.0.1 | Port | Speedup |
|---|---|---:|---:|---:|---:|
| N-40 | medium | 1,965,400 | 5.29 s | 1.26 s | 4.2x |
| N-41 | large | 19,625,400 | 102.64 s | 14.89 s | 6.9x |

Plan §3.2 point: at medium scale, vectorising gives about 8x on the generate step but only
4.2x in total, because Parquet writing does not speed up (N-42: generate 4.65 s → 0.55 s,
write 0.65 s → 0.71 s; `retail_bench.json` → `scales.medium.summary`). This is the case for
the Rust kernel **and** pipelined writing (T-17). It is not a claim about Rust speed.

## Targets (label them as targets, every time)

| ID | Target | Source |
|---|---|---|
| N-50 | **≥10x** Spindle (minimum), **≥30x** (stretch), for profiling and generation, measured 1:1 on equivalent work | Plan D-04, §3.4 |
| N-51 | Example: D2 Parquet 10x target ≤ 2.65 s (Spindle 26.5 s) | Plan §3.3 |
| N-52 | CLI start-up ≤ 300 ms (stretch ≤ 150 ms) | Plan §3.4 START, T-18 |
| N-53 | Stream profiling ≥ 80% of batch throughput | Plan §3.4 STREAM-PROF |

None of these has been met by the product yet, and the talk must not suggest otherwise.

## Design parameters (not measurements)

| ID | Value | Source |
|---|---|---|
| N-60 | HLL p = 14; KLL k = 200; SpaceSaving capacity 64 | Plan T-14 |
| N-61 | HLL relative error ≤ 3 × 1.04/√2¹⁴ at the 99th percentile over 200 trials; KLL rank error ≤ 1% | P1-03 acceptance criteria (plan §7), met per the `build/main-plan` tracker (P1-03 done, `2f05b17`) |
| N-62 | Seeded XXH3-64; `1` and `1.0` hash equal; NaN and null excluded; strings as UTF-8; timestamps as int64 µs | Plan T-13; P1-02 acceptance; checked in this session (`STATUS.md`) |
| N-63 | Zero-copy acceptance: a 1M-row, 10-column batch round-trips through Rust with identical buffer addresses | P1-01a acceptance; `tests/kernel/test_native_kernel.py` on `build/main-plan` |
| N-64 | Python → native call overhead ~7 µs; Parquet writing ~50% of vectorised generation time | Plan T-01 rationale ("Measured"). The plan does not name the machine for the 7 µs figure, so **keep it off the slides**; the ~50% figure matches N-42 (write 0.71 s of the port's 1.26 s total) |

## Demo data and drift (deterministic, seed 42, medium scale)

Source: `demo/DRIFT.md`, verified by `tests/demo/content/` and in this session by
`verify_snippets.sh`.

| ID | Number | Source |
|---|---|---|
| N-70 | `customers.email` null rate **4.97% → 20%** (contract `max_null_rate` 0.08) | `DRIFT.md` |
| N-71 | `orders.status` gains **`lost`** on **2%** of rows | `DRIFT.md` |
| N-72 | `orders.order_total` × **1.40**: mean **110.93 → 155.30**; max **5135.63 → 7189.882** (contract `max` 6000) | `DRIFT.md` |
| N-73 | `products.sku`: **5,050 rows, 5,000 distinct** | `DRIFT.md` |
| N-74 | The +40% shift is **0.43** baseline standard deviations; the default `mean_shift_std` threshold is **0.5**; the demo uses **0.25** | `DRIFT.md`; plan §12.3 |
| N-75 | Retail medium: **1,965,400 rows, 9 tables** | Plan §3.2 |

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

None. `demo/LIVE_TIMINGS.md` is a placeholder (every cell `TBD`). After the owner's dry run,
add rows here as N-9x, copying the SKU, vCores, row counts and seconds exactly as recorded.
