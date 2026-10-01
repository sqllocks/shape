# Gate G1 evidence (builder, 2026-10-01 EDT): speed gates NOT met; escalated in §2.3

Machine: 4 vCPU Xeon @ 2.1 GHz, 16 GB, Linux; Rust kernel; pyarrow 25.0.1 in both venvs; Spindle 3.0.1
(422e78d, read only). Equivalence first: `profile_1to1/verify.py --impl shape` exit 0 before each timing
(T-22 parity: every field of every default dataset). 5 fresh-process runs per cell, median, interleaved
with Spindle, exclusive lock, load gate. Harness outputs are in `docs/plans/demo_status/g1/`.

## PROF-IN, speedup vs Spindle (gate: >= 10x on every workload)
| dataset | 1. as found (4253808^) | 2. imports excluded (4253808) | 3. after round 1 (a3 fit work) |
|---|---|---|---|
| d1.csv | 2.6x | 7.0x | 11.1x |
| d1.parquet | 2.2x | 7.1x | **6.5x** |
| d2.csv | 8.8x | 10.3x | **9.6x** |
| d2.parquet | 9.0x | 10.2x | 10.0x |
| d3.csv | 10.6x | 11.1x | 11.2x |
| d3.parquet | 6.3x | 6.6x | **6.7x** |
| d4.csv | 5.7x | 6.1x | **8.8x** |
| d4.parquet | 5.5x | 6.2x | **9.4x** |
| mt | 2.6x | 3.9x | **5.1x** |

Column 2 changes only the harness worker (`bench.py`): it imports `shape.profile`'s implementation
before the timer, as Spindle's worker imports pandas and its profiler (T-19: imports excluded for both
tools; no warm-up call). Column 1 is the result without that change. Run-to-run noise is visible
(d1.csv 7.0x -> 11.1x with 0.21 s -> 0.14 s; d2.parquet 10.2x -> 10.0x).

## PROF-CLI and START (after round 1)
- START: median 39 ms (gate 300): pass.
- PROF-CLI D2: 8.8x (as found 8.9x): **miss**. D3: 10.8x (10.7x): pass.

## Round 1 (§6.5): hotspots and what was done
Single-thread D4 before: `fit_distribution` 9.1 s of 14.6 s; `searchsorted` in `_top_by_first_seen` 1.0 s;
CSV read 1.7 s; `_correlation` 0.7 s. D3 parquet (1T 16.7 s): fit 4.5 s, `searchsorted` 4.0 s, pyarrow
compute 2.6 s, read 0.8 s.
Done: the lognormal likelihood (Nelder-Mead fallback; hundreds of evaluations over the full column)
reuses scratch buffers and takes both logs through numpy's array `log` (the function scipy evaluates);
numpy's fp-error state is entered once per fit. Same arithmetic order: parity exit 0, 120 kernel tests
pass. D4 fit total 13.0 s -> 5.4 s.

## Why more tuning will not close the gate
- The product `shape.profile` is `src/shape/profile/reference/` (numpy/pandas-token semantics, 4,500
  lines); only distribution fitting runs in Rust. P1-08 says to port the semantics "on the Rust kernel".
  The fused engine (`profile/engine.py`) in exact mode is *slower* than `shape.profile` on D2 (6.2 s vs
  2.8 s) and D3 (11.7 s vs 7.3 s), so it is not a drop-in.
- D1 and MT need 0.15 s and 0.19 s per call. What remains is first-call work in a fresh process (pool
  start-up, CSV read, table-to-dict), not per-value work.
- D3 parquet needs 7.5 s -> 5.0 s: ~2.5 s of serial work (read, correlation) plus per-column Python
  (`searchsorted` first-appearance scan, Arrow `value_counts`) that scales poorly across threads.
- The remaining per-value work (first-appearance ordering, value counts, correlation, CSV/Parquet
  decoding with pandas semantics) would need a native implementation of the exact-mode profile, which
  is a rewrite of the product profiler, not a tuning round.

## Other G1 items (checked in this session after merging e12d632)
- P1-14 done (8bac288); `check_user_facing.py` clean.
- T-22 parity: `verify.py --impl shape` exit 0 (re-run after each kernel change).
- Phase-1 P-bug regression gate (`tests/regressions/test_phase1_bugs.py`, with `tests/profile`): 84 passed.
- Profile modules mypy strict: done in e12d632 (another session); `mypy` reports no issues in 157 files.
- A second session's independent run 1 (`docs/plans/evidence/G1/`, before its pre-import change) agrees with the
  as-found column above: PROF-IN misses on 8 of 9 workloads, PROF-CLI D2 8.7x, D3 10.5x, START 41 ms.
