# simulation_1to1: parity of `shape-simulation` against the pinned baseline (P6-04)

Two lanes share this directory; each simulator has its own `case_<name>.py`.

| Runner | Cases | Runs in | What it does |
|---|---|---|---|
| `verify.py` (P6-04a) | `file_drop`, `scd2_file_drops`, `stream_emit`, `hybrid`, `state_machine` | the baseline venv | mechanism parity on shared inputs plus T-21 parity on each tool's own retail tables; `--negative-control` |
| `verify_patterns.py` (P6-04b) | `clickstream`, `financial`, `iot`, `operational_log`, `pulse` | the Shape venv | T-21 parity per output table on the same inputs (fixed seeds), invariants, negative controls, allow-list probes |

```bash
source scripts/env.sh
python benchmarks/vs_spindle/simulation_1to1/verify_patterns.py [--quick] [--only NAME ...]
"$SPINDLE_PY" benchmarks/vs_spindle/simulation_1to1/verify.py [--case NAME ...]
```

Both use the fixed seed set of T-21 (baseline 42 as the reference and 43-46 for its own spread,
Shape 1042; there is no option for another), `paths.py` for every path, and a named allow-list of
baseline defects that Shape fixes (the owner's standing decision of 2026-10-01).

`verify_patterns.py` files: `harness.py` (the comparison rules, in its docstring, and the baseline
runner), `names.py` (baseline-to-Shape names and the allow-list `SIM-n`), `inputs.py` (the
deterministic input tables), `pattern_worker.py` (runs the baseline simulators in the baseline venv),
`case_*.py`. Output: `$BENCH_OUT_DIR/simulation_1to1/results.json`; baseline runs are cached there
by job digest. The rules' own negative control, without the baseline, is
`tests/benchmarks/test_simulation_1to1_harness.py`.
