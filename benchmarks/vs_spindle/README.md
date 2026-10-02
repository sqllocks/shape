# Shape vs the pinned Spindle

The comparison harness behind the performance and equivalence gates of
`docs/plans/COMPLETION_PLAN.md` (section 3.4). Spindle is pinned at git `422e78df2267e73bb2fa976267e48cb437861e2f`
(3.0.1); the checkout is only ever read.

Every path comes from the section 1 environment variables in `scripts/env.sh`
(`SPINDLE_ROOT`, `SPINDLE_VENV`, `SHAPE_VENV`, `BENCH_DATA_DIR`, `BENCH_OUT_DIR`); nothing in
this directory hard-codes a machine path.

| file | purpose |
|---|---|
| `setup_spindle.sh` | Clone the pinned Spindle, build its venv, write `$BENCH_OUT_DIR/spindle_freeze.txt` |
| `run.py` | `--quick` (D1, D2, retail small + medium; 3 runs) or `--full` (all of section 3.4; 5 runs): verifiers, then benchmarks, then `results.json` |
| `results.json`, `results.schema.json` | The committed reference (section 6.2(4)) and its schema. `shape` is `null` until the product path exists |
| `domain_1to1/` | Domain generation: `generate.py`, `verify.py` (T-21), `bench.py`, and the retail reference port |
| `profile_1to1/` | Profiling: `datasets.py`, `verify.py` (T-22), `bench.py`, the reference port, `spindle_dump.py`, `spindle_cli_profile.py` |
| `verify_1to1/` | `shape verify` (P6-09): `verify.py` runs both `verify` CLIs on mutated retail output and compares exit codes and, gate by gate, pass/fail, errors, warnings and details |
| `chaos_1to1/` | `shape.chaos` (P6-02): `verify.py` runs each chaos mutator, each sub-mutation and the engine schedule in both tools on the same input, config and seeds; mutation types exactly, rates within a derived statistical tolerance (documented in its docstring); `--negative-control` proves it fails on a deliberately wrong rate |
| `dump_schema.py` | Serialize every Spindle domain schema (3nf, star) to `$BENCH_OUT_DIR/schemas/` |
| `check_coverage.py` | Every Spindle file must be mapped to a work package in `docs/plans/spindle_coverage.tsv` |

## Rules

* **Equivalence before timing.** A workload's numbers are recorded only after its verifier
  exits 0 on the timed output; otherwise `median_s` is `null` and the verifier status says why.
* **Fixed seeds** (T-21): Spindle 42 as the reference, 43-46 as its self-baseline, the
  implementation at 1042. The verifiers have no option to change them.
* `--impl reference_port` is the numpy + pyarrow 1:1 port kept in this directory (retail
  domain, and the profiler). `--impl shape` is the product code in `src/shape`.
* Timed runs use fresh processes, the exclusive lock `$BENCH_OUT_DIR/bench.lock`, and wait for a
  1-minute load average of at most 1.5 (section 1.4).

## From scratch

```bash
source scripts/env.sh
bash benchmarks/vs_spindle/setup_spindle.sh                     # Spindle checkout + venv
python3 -m venv "$SHAPE_VENV" && "$SHAPE_VENV/bin/pip" install -e '.[dev]' 'pyarrow==25.0.1'
python benchmarks/vs_spindle/check_coverage.py
python benchmarks/vs_spindle/run.py --quick                     # writes benchmarks/vs_spindle/results.json
```
