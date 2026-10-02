# simulation_1to1: the simulators against the pinned baseline (P6-04)

```bash
source scripts/env.sh
"$SPINDLE_PY" benchmarks/vs_spindle/simulation_1to1/verify_files.py                   # every case, small
"$SPINDLE_PY" benchmarks/vs_spindle/simulation_1to1/verify_files.py --case file_drop  # one case
"$SPINDLE_PY" benchmarks/vs_spindle/simulation_1to1/verify_files.py --negative-control
```

Exit 0 when every check passed, 1 when a check failed, 2 when an input or a worker is missing.
The verifier runs in the baseline venv; each side's code runs in its own venv through
`files_baseline_worker.py` and `files_shape_worker.py`. The retail inputs are the ones `domain_1to1/generate.py`
makes (generated here when missing), at `small`: the baseline slices a whole frame once per
time slot, so a medium table would take it hours.

* `verify_files.py`: the runner of this lane. It discovers `files_case_*.py`; a case module defines `NAME`, `ALLOWED`,
  `baseline_side(job)`, `shape_side(job)`, `run(ctx)` and `negative_controls(ctx)`.
* `files_common.py`: the fixed seeds (T-21: the baseline at 42 with 43-46 as its own spread, Shape at
  1042; there is no option for another set), `NAME_MAP` (the baseline's names and the Shape names
  they stand for, D-13, recorded in the report), `Checks`.
* `files_compare.py`: frame equality (columns by name, numbers to a relative tolerance, dates by
  instant, nulls equal), `t21_columns` (T-21 (a)-(e) through `domain_1to1/verify.py`'s
  `compare_column`) and `count_within` (a random count within max(5 sigma, 1.5 x the baseline's
  range)).
* `files_trees.py`: comparing the directory trees two file drops wrote.
* `files_case_file_drop.py`, `files_case_scd2_file_drops.py`, `files_case_stream_emit.py`,
  `files_case_hybrid.py`, `files_case_state_machine.py`: lane P6-04a. The cases of lane P6-04b
  (clickstream, financial, iot, operational_log, pulse) are `case_*.py` files run by `verify.py`.

## What a case checks

1. **Mechanism parity.** Both tools get the same input (the baseline's retail at seed 42), the same
   configuration and the same seed. The simulators draw their random numbers in the same order, so
   the outputs are equal: the same files, rows in the same order, manifests, events. Wall-clock and
   random values (ids, times) are checked for form, never for equality.
2. **Allow-list.** A trust-harming defect of the baseline is fixed in Shape (owner's standing
   decision, 2026-10-01) and named in the case's `ALLOWED`. A difference that is not allowed fails
   the run. Each entry has a probe that observes the baseline's behaviour and Shape's, so an entry
   the baseline no longer shows fails the run too.
3. **T-21.** Each tool simulates its own generated retail tables; the output columns satisfy T-21
   (a)-(e) with the baseline's own seed-to-seed spread, and output counts lie within the spread.
4. **Negative control.** Tampering with Shape's output (a value, a file, a name, a flag) must fail
   the comparison every time.

The allow-lists are in the `ALLOWED` of each case and in the report JSON
(`$BENCH_OUT_DIR/simulation/report_<scale>.json`).
