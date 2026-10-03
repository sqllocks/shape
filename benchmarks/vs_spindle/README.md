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
| `pack_1to1/` | `shape.scenario` and `shape pack` (P6-14): `verify.py` loads, validates and runs the five reference inputs (`tests/fixtures/packs/`) in both tools at `fabric_demo`; parsed structure, verdicts, run results and manifest keys equal; T-21 (a)-(f) for inputs without chaos (the written tables, baseline seeds 42-46 against Shape 1042); a named allow-list (`pack_common.py`) of the baseline defects Shape fixes, each shown by a probe; `--negative-control` proves it fails on tampered output |
| `stream_1to1/` | `shape stream` (P5-04, STREAM-EMIT): `verify.py` runs both stream commands (retail `order`, whole table and first N events; baseline seeds 42-46, Shape 1042), maps the baseline's event field names to Shape's (`stream_common.FIELD_MAP`) and checks names and order, event count and order, and T-21 (b)-(e) per field; the allow-list (`stream_common.ALLOWED`, one probe each) names the baseline defects Shape fixes; `--negative-control` perturbs Shape's events and requires each to be caught. `bench.py` times both tools (fresh processes, imports excluded) and the two command lines end to end; `run.py --only stream` runs verify, bench, verify |
| `bridge_1to1/` | `shape bridge` (P6-11): `verify.py` drives the baseline's persistent JSON bridge and `shape bridge` as a client does, for the 17 commands, on retail. Metadata is compared exactly (`compare.py`: list, describe in both modes, dry_run for every preset, validate on the baseline's schema file and its Shape form, profile_info ratios and distributions, the shapes of preview, generate, stream and the job commands); generated data is compared under T-21 with the domain verifier on the bridges' own Parquet (`generate`, `scale_generate` local_single and local_mp, baseline seeds 42-46, Shape 1042); each baseline defect Shape fixes is an `ALLOW` entry (BR-1 .. BR-7) that the harness shows in the baseline and shows fixed; `demo_*` are reported as pending P6-12; `--negative-control` mutates Shape results and requires each comparison to flag them |
| `scale_1to1/` | `shape generate --scale-mode` (P6-13): `verify.py` runs `local_single` and `local_mp` for retail at medium (Shape 1042; baseline seeds 42-46) through the product command, reads the part files back into the layout `domain_1to1/verify.py` reads and runs that verifier unchanged (T-21 (a)-(h)); it also checks every table's rows against the preset and the part size; `--negative-control` tampers with the output (numeric, categorical, foreign keys, dropped rows) and runs the baseline's own `local_mp` output through the same comparison, and requires each to be flagged |
| `simulation_1to1/` | `shape-simulation` (P6-04): `verify_files.py` runs the cases of lane P6-04a, `files_case_<simulator>.py` (one module per simulator): mechanism parity (the same input tables, configuration and seed give the same files, rows, manifests and events once the baseline's names are mapped to Shape's, `sim_common.NAME_MAP`), the allow-list probes (`ALLOWED` per case, each shown by a probe that fails when the baseline no longer has the defect), T-21 (a)-(e) on each tool's own retail output (baseline seeds 42-46, Shape 1042), and `--negative-control` (tampered output must be caught). Cases: `file_drop`, `scd2_file_drops`, `stream_emit`, `hybrid`, `state_machine` |
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
