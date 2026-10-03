# AUD-perf: audit of performance and memory hot spots (lane/AUD-perf)

Area: `benchmarks/**`, `tests/benchmarks/**`, `docs/SCALE.md`. The engine was profiled read-only.
Branch started from `origin/build/main-plan` 5c91ea5.

Status: **in progress.**

## Environment used

`scripts/env.sh`, a fresh Shape venv (`pip install -e '.[dev]'` plus every `plugins/*` with
`--no-deps`, and scikit-learn for the fidelity-tier tests) and the pinned baseline from
`benchmarks/vs_spindle/setup_spindle.sh` (git 422e78df). 4 cores, Python 3.11.15.

## Phase 1: what was measured (engine, read-only)

Peak RSS is `ru_maxrss` of a fresh child process; wall clock includes imports. One run each, so
these are evidence for where memory goes, not gate numbers.

| workload | Shape | baseline |
|---|---|---|
| `shape.profile` D2 CSV (1M x 20, exact) | 2.3 s, 887 MB | |
| `shape.profile` D3 Parquet (5M x 10, exact) | 3.4 s, 2,110 MB | 43.7 s, 2,122 MB |
| `shape.profile` D3 CSV (exact) | 4.5 s, 2,217 MB | |
| `profile_table(mode="bounded")` D2 Parquet / D3 Parquet / D3 CSV | 458 / 418 / 346 MB | |
| `generate.py --impl shape` retail medium (timed region 0.37 s) | 462 MB | 610 MB (4.79 s) |
| `generate.py --impl shape` retail large (timed region 5.39 s) | 1,982 MB | 3,427 MB (86.4 s) |

Exact mode holds the table, as the baseline does, and peaks at the baseline's level. Bounded mode
stays near 400 MB from 1M to 5M rows, so it is bounded as T-15 says. Generation peaks below the
baseline at both scales. The overlapped writer (`_write_overlapped` in
`src/shape/generation/output.py`) queues batches without a bound, but those batches are the
same buffers as the result `Engine.generate` keeps whole anyway, so it adds no memory: not a
defect. No engine memory defect was found.

The kernel microbenchmark's prefix property was checked for all 11 kernels: the first N rows of
a native call on more rows equal the twin's N-row output (this is what the fix of finding 5
relies on).

## Findings

| # | sev | where | defect | issue | fix |
|---|---|---|---|---|---|
| 1 | medium | `benchmarks/vs_spindle/run.py:562-598` | `run.py --only profile\|generate\|stream` writes `results.json` from scratch, so every record of the other families (the section 6.2(4) reference) is dropped. Repro: a `results.json` with profile and generate records, then `run.py --quick --only stream`: only the stream records are left. Expected: the other families' records are kept. | #357 | |
| 2 | medium | `benchmarks/vs_spindle/stream_prof/bench.py:135-136` | STREAM-PROF takes the row count from the batch side only. A replay that profiled half the rows in the same time is reported as `100.0% ... PASS`. Expected: the run is refused when the two sides saw different row counts. | #358 | |
| 3 | low | `benchmarks/vs_spindle/run.py:480-498` | `--full`'s other-domain baseline loop does not check the exit code of a timed run: a failing run ends the whole job with a bare `StopIteration` (no domain, no output) after every other workload ran. The runs also skip the load gate (section 1.4). Expected: the record stays without numbers, a line names the workload, the job goes on. | #359 | |
| 4 | low | `benchmarks/measure_product.py:78-79` | Talk numbers are timed with no benchmark lock and no load gate (section 1.4); a profile that differs between runs is recorded with `output_identical_across_runs: false` instead of refusing; the row check is an `assert` (gone under `-O`) whose message is only the dataset name. | #360 | |
| 5 | low | `benchmarks/vs_spindle/kernel_bench.py:171-175,203` | The docstring says each kernel is compared with its twin "on the very output being timed", but the timed call's output (`got`) is never compared: a separate 100k-row call is. Every kernel also rebuilds all 11 cases (`cases(a.ref_rows)` in the loop), and a missing `--out` file is a `FileNotFoundError` after all the timing. | #361 | |
| 6 | low | `benchmarks/live_fidelity/run.py:338-397` | The tee overhead and realtime timings run without the benchmark lock or the load gate (section 1.4), so a concurrent benchmark skews both. | #362 | |
| 7 | low | `generate.py:61`, `stream_1to1/*_worker.py`, `measure_product.py`, `profile_1to1/bench.py` | `ru_maxrss` is KiB on Linux but bytes on macOS; the harness divides by 1024 everywhere, so a macOS run records peak RSS 1024x too high. | #363 | |
| 8 | low | `benchmarks/vs_spindle/profile_1to1/datasets.py:650-658` | An unknown dataset name is a `KeyError` traceback; `--rows 0` silently means the default and a negative `--rows` writes nothing but prints `wrote d3`. | #364 | |
| 9 | low (doc) | `benchmarks/vs_spindle/domain_1to1/bench_cli.py:12-15` | The docstring says `verify.py --impl shape` checks what the CLI wrote; the CLI runs go to `<impl>-cli`, which only `verify.py --cli` reads. | #365 | |

## Commands and results

- `pytest tests/benchmarks` before any change: 179 passed, 5 skipped (sklearn missing at the time).
- Coverage of the harness from its tests (`--cov=benchmarks`): 62% of the imported files;
  `run.py` 49%, `common.py` 54%, `stream_prof/bench.py` 62%, `measure_product.py` 0%.
