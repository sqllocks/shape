# Shape vs the pinned RefEngine

The comparison harness behind the performance and equivalence gates of
`docs/plans/COMPLETION_PLAN.md` (section 3.4). RefEngine is pinned at git `422e78df2267e73bb2fa976267e48cb437861e2f`
(3.0.1); the checkout is only ever read.

Every path comes from the section 1 environment variables in `scripts/env.sh`
(`REFENGINE_ROOT`, `REFENGINE_VENV`, `SHAPE_VENV`, `BENCH_DATA_DIR`, `BENCH_OUT_DIR`); nothing in
this directory hard-codes a machine path.

| file | purpose |
|---|---|
| `setup_refengine.sh` | Clone the pinned RefEngine, build its venv, write `$BENCH_OUT_DIR/refengine_freeze.txt` |
| `run.py` | `--quick` (D1, D2, retail small + medium; 3 runs) or `--full` (D1-D4, MT; retail medium + large for both implementations, Shape on every other baseline domain at medium; stream; 5 runs): verifiers, then benchmarks, then `results.json`; `--only profile\|generate\|stream` re-measures one family and keeps the other families' records already in the file; `--shard K/N`, `--domain`, `--dataset` select a part (recorded under `selection`) |
| `nightly.py` | The nightly parity suite (P8-02): `run --shard S` runs one shard of `run.py --full` (`shards` lists them; a hosted job runs at most 6 hours), `merge` combines the shards, judges the night (green only when every workload of the full suite is recorded with a verifier that exited 0) and appends it to a JSON-lines history, `streak --need 7` checks the acceptance (consecutive scheduled green nights, no day missing) |
| `../../scripts/gen_performance_page.py` | Builds `docs/PERFORMANCE.md` from a `results.json`: publishes a workload's numbers only when its verifier exited 0, never names the baseline (D-13); `--check` fails when the committed page is stale |
| `results.json`, `results.schema.json` | The committed reference (section 6.2(4)) and its schema. `shape` is `null` until the product path exists |
| `domain_1to1/` | Domain generation: `generate.py`, `verify.py` (T-21), `bench.py`, and the retail reference port |
| `profile_1to1/` | Profiling: `datasets.py`, `verify.py` (T-22), `bench.py`, the reference port, `refengine_dump.py`, `refengine_cli_profile.py` |
| `verify_1to1/` | `shape verify` (P6-09): `verify.py` runs both `verify` CLIs on mutated retail output and compares exit codes and, gate by gate, pass/fail, errors, warnings and details |
| `chaos_1to1/` | `shape.chaos` (P6-02): `verify.py` runs each chaos mutator, each sub-mutation and the engine schedule in both tools on the same input, config and seeds; mutation types exactly, rates within a derived statistical tolerance (documented in its docstring); `--negative-control` proves it fails on a deliberately wrong rate |
| `pack_1to1/` | `shape.scenario` and `shape pack` (P6-14): `verify.py` loads, validates and runs the five reference inputs (`tests/fixtures/packs/`) in both tools at `fabric_demo`; parsed structure, verdicts, run results and manifest keys equal; T-21 (a)-(f) for inputs without chaos (the written tables, baseline seeds 42-46 against Shape 1042); a named allow-list (`pack_common.py`) of the baseline defects Shape fixes, each shown by a probe; `--negative-control` proves it fails on tampered output |
| `stream_1to1/` | `shape stream` (P5-04, STREAM-EMIT): `verify.py` runs both stream commands (retail `order`, whole table and first N events; baseline seeds 42-46, Shape 1042), maps the baseline's event field names to Shape's (`stream_common.FIELD_MAP`) and checks names and order, event count and order, and T-21 (b)-(e) per field; the allow-list (`stream_common.ALLOWED`, one probe each) names the baseline defects Shape fixes; `--negative-control` perturbs Shape's events and requires each to be caught. `bench.py` times both tools (fresh processes, imports excluded) and the two command lines end to end; `run.py --only stream` runs verify, bench, verify |
| `bridge_1to1/` | `shape bridge` (P6-11): `verify.py` drives the baseline's persistent JSON bridge and `shape bridge` as a client does, for the 17 commands, on retail. Metadata is compared exactly (`compare.py`: list, describe in both modes, dry_run for every preset, validate on the baseline's schema file and its Shape form, profile_info ratios and distributions, the shapes of preview, generate, stream and the job commands); generated data is compared under T-21 with the domain verifier on the bridges' own Parquet (`generate`, `scale_generate` local_single and local_mp, baseline seeds 42-46, Shape 1042); each baseline defect Shape fixes is an `ALLOW` entry (BR-1 .. BR-7) that the harness shows in the baseline and shows fixed; `demo_*` are compared with the baseline's (catalog, a run's result and manifest, an unknown session, a dry-run cleanup); `--negative-control` mutates Shape results and requires each comparison to flag them |
| `scale_1to1/` | `shape generate --scale-mode` (P6-13): `verify.py` runs `local_single` and `local_mp` for retail at medium (Shape 1042; baseline seeds 42-46) through the product command, reads the part files back into the layout `domain_1to1/verify.py` reads and runs that verifier unchanged (T-21 (a)-(h)); it also checks every table's rows against the preset and the part size; `--negative-control` tampers with the output (numeric, categorical, foreign keys, dropped rows) and runs the baseline's own `local_mp` output through the same comparison, and requires each to be flagged |
| `fabric_commands_1to1/` | `shape fabric publish\|notebook\|deploy-notebook\|setup\|export-model` and their aliases (P6-07c): `verify.py` (Shape venv) runs both tools' commands and compares the `.bim` (every option; the library on synthetic schemas with every type), the notebook cells, the requests each sends to a fake Fabric service (`baseline_rest.py`, in the baseline venv) and the landing zone, run manifest and reports for each file format; the allow-list `differences.py` names every difference, each shown by a probe; `--negative-control` tampers with each comparison's input and requires a flag |
| `simulation_1to1/` | `shape-simulation` (P6-04): `verify_files.py` runs the cases of lane P6-04a, `files_case_<simulator>.py` (one module per simulator): mechanism parity (the same input tables, configuration and seed give the same files, rows, manifests and events once the baseline's names are mapped to Shape's, `sim_common.NAME_MAP`), the allow-list probes (`ALLOWED` per case, each shown by a probe that fails when the baseline no longer has the defect), T-21 (a)-(e) on each tool's own retail output (baseline seeds 42-46, Shape 1042), and `--negative-control` (tampered output must be caught). Cases: `file_drop`, `scd2_file_drops`, `stream_emit`, `hybrid`, `state_machine` |
| `demo_1to1/` | `shape demo` (P6-12): `verify.py` (baseline venv) drives both `demo` commands and compares, exactly after the baseline's name is mapped to Shape's (`differences.brand_patterns()`), the scenario catalog, the options of every command, a record's `status` and Markdown and HTML report, the cost estimate and dry-run line, the `cleanup --dry-run` lines, the stored profile and the notebooks (Markdown, cell kinds, scenario, mode, rows); the fidelity report row by row and its score on two dataset pairs (one damaged on purpose, so the check cannot pass vacuously); generated data by T-21: an inference run from a file (same learned schema but for P4-08's `truncated_enum`, then T-21 (a)-(d) per column, baseline seeds 42-46 against Shape 1042) and a seeding run through `domain_1to1/verify.py --impl shape` unchanged; the allow-list `differences.py` names every difference, each shown by a probe of the baseline; `--negative-control` tampers with each comparison's input and requires a flag |
| `dump_schema.py` | Serialize every RefEngine domain schema (3nf, star) to `$BENCH_OUT_DIR/schemas/` |
| `check_coverage.py` | Every RefEngine file must be mapped to a work package in `docs/plans/refengine_coverage.tsv` |

## Rules

* **Equivalence before timing.** A workload's numbers are recorded only after its verifier
  exits 0 on the timed output; otherwise `median_s` is `null` and the verifier status says why.
* **Fixed seeds** (T-21): RefEngine 42 as the reference, 43-46 as its self-baseline, the
  implementation at 1042. The verifiers have no option to change them.
* `--impl reference_port` is the numpy + pyarrow 1:1 port kept in this directory (retail
  domain, and the profiler). `--impl shape` is the product code in `src/shape`.
* Timed runs use fresh processes, the exclusive lock `$BENCH_OUT_DIR/bench.lock`, and wait for a
  1-minute load average of at most 1.5 (section 1.4).

## From scratch

```bash
source scripts/env.sh
bash benchmarks/vs_refengine/setup_refengine.sh                     # RefEngine checkout + venv
python3 -m venv "$SHAPE_VENV" && "$SHAPE_VENV/bin/pip" install -e '.[dev]' 'pyarrow==25.0.1'
python benchmarks/vs_refengine/check_coverage.py
python benchmarks/vs_refengine/run.py --quick                     # writes benchmarks/vs_refengine/results.json
python scripts/gen_performance_page.py                           # regenerate docs/PERFORMANCE.md from it
```

## Nightly parity suite (P8-02)

```bash
source scripts/env.sh
for s in $(python benchmarks/vs_refengine/nightly.py shards | python -c 'import json,sys; print(*json.load(sys.stdin))'); do
    python benchmarks/vs_refengine/nightly.py run --shard "$s"     # $BENCH_OUT_DIR/nightly/<shard>/
done
python benchmarks/vs_refengine/nightly.py merge --in-dir "$BENCH_OUT_DIR/nightly" \
    --out "$BENCH_OUT_DIR/parity/results.json" --history "$BENCH_OUT_DIR/nightly.jsonl"
python scripts/gen_performance_page.py --results "$BENCH_OUT_DIR/parity/results.json" \
    --history "$BENCH_OUT_DIR/nightly.jsonl" --out "$BENCH_OUT_DIR/parity/PERFORMANCE.md"
python benchmarks/vs_refengine/nightly.py streak --history "$BENCH_OUT_DIR/nightly.jsonl" --need 7
```

Options after `--` in `nightly.py run` select a subset (`--dataset`, `--domain`); a subset is
recorded but never counts as a nightly run. Only scheduled runs of the whole suite count toward
the streak.
