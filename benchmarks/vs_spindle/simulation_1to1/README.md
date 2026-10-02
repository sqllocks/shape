# simulation_1to1: the simulators against the pinned baseline (P6-04)

Two lanes share this directory. Each simulator has its own case module, and each lane its own
runner, workers and helpers (the other lane's files are prefixed `files_`):

| Runner | Cases | Runs in | What it does |
|---|---|---|---|
| `verify_files.py` (P6-04a) | `files_case_file_drop`, `files_case_scd2_file_drops`, `files_case_stream_emit`, `files_case_hybrid`, `files_case_state_machine` | the baseline venv | mechanism parity on shared inputs plus T-21 parity on each tool's own retail tables; `--negative-control` |
| `verify_patterns.py` (P6-04b) | `case_clickstream`, `case_financial`, `case_iot`, `case_operational_log`, `case_pulse` | the Shape venv | T-21 parity per output table on the same inputs (fixed seeds), invariants, negative controls, allow-list probes |

```bash
source scripts/env.sh
"$SPINDLE_PY" benchmarks/vs_spindle/simulation_1to1/verify_files.py [--case NAME ...] [--negative-control]
python benchmarks/vs_spindle/simulation_1to1/verify_patterns.py [--quick] [--only NAME ...]
```

Exit 0 when every check passed, 1 when a check failed, 2 when an input or a worker is missing.
Both use the fixed seed set of T-21 (the baseline at 42 as the reference and 43-46 for its own
spread, Shape at 1042; there is no option for another set), `paths.py` for every path, and a named
allow-list of baseline defects that Shape fixes (the owner's standing decision of 2026-10-01).

## `verify_files.py` (P6-04a)

Runs in the baseline venv; each side's code runs in its own venv through
`files_baseline_worker.py` and `files_shape_worker.py`. The retail inputs are the ones
`domain_1to1/generate.py` makes (generated here when missing), at `small`: the baseline slices a
whole frame once per time slot, so a medium table would take it hours.

* `verify_files.py`: the runner. It discovers `files_case_*.py`; a case module defines `NAME`,
  `ALLOWED`, `baseline_side(job)`, `shape_side(job)`, `run(ctx)` and `negative_controls(ctx)`.
* `files_common.py`: the fixed seeds, `NAME_MAP` (the baseline's names and the Shape names they
  stand for, D-13, recorded in the report), `Checks`.
* `files_compare.py`: frame equality, `t21_columns` (T-21 (a)-(e) through `domain_1to1/verify.py`'s
  `compare_column`) and `count_within`.
* `files_trees.py`: comparing the directory trees two file drops wrote.

What a case checks: (1) mechanism parity (same input, configuration and seed: the same files, rows,
manifests and events; random values checked for form); (2) the allow-list, each entry shown by a
probe; (3) T-21 on each tool's own generated retail tables; (4) a negative control that tampers
with Shape's output. Report: `$BENCH_OUT_DIR/simulation/report_<scale>.json`.

## `verify_patterns.py` (P6-04b)

Runs in the Shape venv; the baseline runs in its own venv through `pattern_worker.py`.

* `harness.py`: the comparison rules (in its docstring) and the baseline runner (cached by job
  digest under `$BENCH_OUT_DIR/simulation_1to1`).
* `names.py`: the baseline-to-Shape names and the allow-list `SIM-n`; `inputs.py`: the
  deterministic input tables both sides read.
* `case_<name>.py`: configurations (a default and variants that exercise the rare features), how to
  run Shape, what to compare, negative controls and the allow-list probes.
* `--chance-rate` is a diagnostic, never a verdict: Shape at seeds 1043-1049 against the same
  baseline runs, to show how often a correct Shape fails a check by chance.

The rules' own negative control, without the baseline, is
`tests/benchmarks/test_simulation_1to1_harness.py`. Output: `$BENCH_OUT_DIR/simulation_1to1/results.json`.
