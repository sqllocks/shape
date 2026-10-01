# G1-mt: PROF-IN multi-table speed (lane/G1-mt)

Status: **PROF-IN MT is 12.0x (gate 10x); every other PROF-IN workload, PROF-CLI D2/D3 and START pass.**
Gates, tolerances, decisions and the harness were not changed. §11 and §2.3 were not edited (the lead does).
Base `c5d10b7` (G1-rerun), merged with `origin/build/main-plan` (`f698324`) before the checks and timings below.
Evidence and raw JSON: `docs/plans/evidence/G1-mt/`. Machine: 4 vCPU Intel Xeon 2.80 GHz, Python 3.11.15, pinned
Spindle `422e78df` built by `setup_spindle.sh` into its own venv (checkout untouched).

## What was wrong (measured, not assumed)
The previous builder's candidates were partly stale: the shared one-pool-for-all-tables schedule already existed
(P1-17), and every heavy column already runs in the Rust kernel (the MT path uses the same `_profile_column` as the
single-table path; `order_ts` is parsed by Arrow, not per value in Python; `customer_id` in `orders` is a float column
because it has nulls, so its fit is required and its output is unchanged). Measured on this VM, one MT call:

- Stage split (0.30 s typical): reading the three CSVs 0.04-0.24 s, column pool 0.15-0.18 s, `dataset_to_dict` 0.004 s.
- Serial per-column times add to about 0.2 s, but the pool took 0.55 CPU-s: user CPU 0.55 s, 400 involuntary context
  switches. **Cause 1: the per-table correlation (`X.T @ X` on 200k x 5) runs on its own thread while the column pool is
  busy, and OpenBLAS wakes all 4 of its threads for each product and then spins.** With `OPENBLAS_NUM_THREADS=1`
  user CPU fell from 0.55 s to 0.32 s.
- **Cause 2: scheduling.** The shared pool's order was "float first, then int and text, ties by row count", so the
  200k-row text-to-timestamp column `order_ts` (0.055 s serial) started after the cheap int columns and finished last.
- Not a cause here: the rayon pool size (`RAYON_NUM_THREADS` 1, 2, 4 changed nothing), thread count of the Python pool
  (2 and 3 workers were not better), allocator settings (no reproducible gain).
- Residual noise (not removable in code): `read_csv` of `orders.csv` has steady user time (0.045 s) but system time
  between 0.00 and 0.17 s from page-fault cost on this VM. This is why single MT runs range 0.16-0.30 s.

## What was changed
- `src/shape/profile/reference/_blas.py` (new): `single_thread_blas()`, a scoped, refcounted setting of the loaded
  OpenBLAS pool to one thread (found through `/proc/self/maps`, called through ctypes; the pool size found by the
  first scope in is restored by the last scope out; a no-op where OpenBLAS is not found, with the same profile).
  No new dependency. Used by `_quiet_correlation` in `table.py` (the correlation thread that overlaps the column pool).
- `src/shape/profile/reference/table.py`: the multi-table pool orders columns by estimated work
  `rows x kind weight` (`_col_work`: float 4, text 3, timestamp 2, others 1; weights from the serial timings above)
  instead of kind class then rows. Order of work only: results are stored by column index. The single-table order
  (`_col_cost`) is untouched, so D1-D4 scheduling is unchanged.
- `tests/profile/test_multi_table_pool.py` (new, 6 tests): the BLAS scope restores the pool size (also after an error,
  nested, and shared between two threads), is a no-op without OpenBLAS, `_col_work` orders as intended, and a
  multi-table profile with nulls is equal inside and outside the scope.
- Kernel (`rust/`) not changed. Shape's own output is byte-identical before and after the change: the repr of the full
  profile of `mt`, `d1.csv` and `d4.parquet` hashes the same for the original and the new code in both kernels
  (`SHAPE_KERNEL=rust` and `python`).

## Equivalence (before any timing; on the committed code that was timed)
- `verify.py --impl shape`, `SHAPE_KERNEL=rust`: exit 0, 49/49 PASS (`verify_shape_rust.txt`).
- `verify.py --impl shape`, `SHAPE_KERNEL=python`: exit 0, 49/49 PASS (`verify_shape_python.txt`).
- `bench_cli.py` checked CLI equivalence per dataset before timing: "pass" on d2.csv and d3.csv.

## Checks (this session, after the merge of `origin/build/main-plan`)
- `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`: SHAPE_KERNEL=rust 2977 passed;
  SHAPE_KERNEL=python 2977 passed (`pytest_rust.txt`, `pytest_python.txt`).
- `pytest -m heavy tests/kernel tests/profile tests/streaming` (the Makefile's paths, rust): 41 passed
  (`pytest_heavy_rust.txt`). A first call of `pytest -m heavy` without paths collected `tests/demo/fabric`, which needs
  `nbformat` (not installed here); that is a collection error unrelated to this change, and the Makefile never runs it.
- ruff check and `ruff format --check` (src tests benchmarks/vs_spindle plugins): clean. mypy (project config, strict
  where configured): no issues in 237 files. `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80`:
  clean. `lint-imports`: 1 kept, 0 broken. `scripts/check_user_facing.py`: clean. `bandit -q -r src -ll`: no issues
  (`static_checks.txt`).
- Rust: no Rust changes in this lane; `cargo fmt --check`, `clippy --all-targets -D warnings`, `cargo test`
  (32 passed) are clean on the merged tree (`cargo.txt`).

## PROF-IN (`bench.py --impl shape`, T-19 method; median of 5 fresh-process runs, imports excluded, 4 vCPU,
exclusive lock, load gate 1.5; every run reported, none repeated or dropped; `prof_in.json`, `prof_in.txt`)
| workload | Spindle s | Shape s | ratio | Shape 1 thread s | ratio 1T | gate |
|---|---:|---:|---:|---:|---:|---|
| d1.csv | 1.97 | 0.14 | 14.0x | 0.22 | 8.8x | PASS |
| d1.parquet | 1.77 | 0.13 | 13.4x | 0.23 | 7.6x | PASS |
| d2.csv | 39.05 | 2.10 | 18.6x | 5.50 | 7.1x | PASS |
| d2.parquet | 32.68 | 1.87 | 17.4x | 4.08 | 8.0x | PASS |
| d3.csv | 109.13 | 6.14 | 17.8x | 15.62 | 7.0x | PASS |
| d3.parquet | 67.20 | 3.88 | 17.3x | 8.81 | 7.6x | PASS |
| d4.csv | 39.92 | 3.02 | 13.2x | 10.14 | 3.9x | PASS |
| d4.parquet | 34.80 | 2.13 | 16.3x | 7.10 | 4.9x | PASS |
| **mt** | 2.67 | 0.22 | **12.0x** | 0.38 | 6.9x | **PASS** |

MT raw (s): Spindle 2.598 2.740 2.722 2.571 2.669; Shape 0.274 0.227 0.198 0.222 0.163 (median 0.222; one run, the first,
was above the 0.27 s level, so the margin is on the median, not on every run). Shape 1 thread: 0.332 0.342 0.672 0.385 0.398.
All other workloads' raw runs: `prof_in.json`.
For comparison, a same-VM A/B of 15 alternating fresh processes outside the harness (original code against this change,
`cpu.py`-style single MT call): median 0.231 s against 0.174 s (min 0.196 / 0.136, max 0.340 / 0.276).

## PROF-CLI and START (`bench_cli.py`; equivalence "pass" before timing; `prof_cli.json`, `prof_cli.txt`)
| check | Spindle | Shape | ratio / value | gate |
|---|---:|---:|---:|---|
| PROF-CLI d2.csv | 41.11 s | 2.86 s | 14.4x | PASS (>= 10x) |
| PROF-CLI d3.csv | 104.97 s | 5.95 s | 17.7x | PASS (>= 10x) |
| START (`shape --version`, median of 10) | | 39.8 ms | 39.8 ms | PASS (<= 300 ms) |

Raw: PROF-CLI d2 Spindle 41.108 41.706 40.705 40.086 41.306, Shape 2.938 2.898 2.589 2.715 2.863; d3 Spindle 102.935
103.483 104.974 105.215 106.324, Shape 5.702 6.216 7.243 5.947 5.597; START runs (ms) 38.95 39.97 39.68 43.36 50.46 49.25
38.42 40.26 39.25 37.98.

## Notes for the lead
- Both fixes are in the reference profiler shared by both kernels, so the `SHAPE_KERNEL=python` twin has the same
  speed-up and identical output.
- The correlation also runs inline (small tables, or one thread) without the BLAS scope; that case is unchanged.
- Remaining MT cost is mostly pyarrow's CSV read (user 0.045 s, plus a large and variable system-time part on this VM)
  and the `amount` column's fit (about 0.09 s) on the critical path. Nothing else larger than a few ms is left in Python.
- d4.csv (13.2x here, 10.6x in G1-rerun) and the other workloads were not touched by this lane; the differences from the
  G1-rerun table are run-to-run variation on this VM plus the merged upstream code.
