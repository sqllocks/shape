# INT-16 — integration fixes (cross-lane test failures)

Branch `int/INT-16`, based on `build/main-plan` (5c91ea50, INT-15 merged; it has not moved since, so there was
no new merge to make). No edit to §11 or §2.3 of the plan, to `.github/workflows`, or to `$REFENGINE_ROOT` (cloned at
the pinned commit 422e78d and only read). No gate, tolerance or D-xx/T-xx decision changed; no test skipped,
deselected or xfailed.

## Lanes merged (earlier INT-16 sessions)

CI-FIX, BF-77, W1-01, W1-04, W1-05, W1-06, W2-01, W2-02, W3-04, W3-06, W3-09, W3-10, W4-03, W5-01, W1-16,
PLUG-INT, BF-78, W1-09, P6-01e-seed, NIGHTLY-FIX, then `build/main-plan` (INT-15). One commit from those sessions,
9385c53 ("workflow changes proposed by PLUG-INT"), applies PLUG-INT's `ci.yml` diff from
`docs/plans/lane_status/PLUG-INT.md`. This session did not edit any workflow.

## Cross-lane fixes

| Commit | Fix |
|---|---|
| 207f452 | `shape verify --source DATA` is not read as a `shape.yml` source name (W1-04 x INT-15 merge resolution) |
| fe728c6 | ruff format of the merged generate/composite options (P6-01e-seed merge resolution) |
| e780bc4 | `demo_cmd` failed-domain tests use a scenario whose own domain cannot exist |
| ace3414 | W1-06 spec contract covers `conditional_table`, `hierarchy`, `hierarchy_field` and `locale` |
| 3ce515c | `shape.behaviors` is under plugin API v1's promise (W1-05 x PLUG-INT) |
| b2bda02 | `shape-project-v1` schema accepts every drift-engine threshold (W1-04 x INT-15) |
| 7b520d0 | sink-scheme message test expects the message naming both ADLS providers |
| 0bc534e | `shape doctor`'s HTTPS probe goes through the explicit fetch module |
| 8d40fa8 | ruff format of W1-09's demo tests |
| 4d7f51f | healthcare-codes docs name no payer domain |
| c153e9c | bridge vectors list the 14 installed domains (P6-01e-seed) |
| 69df49a | composite dump test treats `export_domains.UNREAD_KEYS` as a named difference |
| 4edaeb1 | **this session.** `emit --to file://` keeps the built-in `file` emitter. The healthcare-standards `fhir` emitter lists the `file` scheme and its name sorts before `file`, so with PLUG-INT's plugins installed every emit to `file://` went to the FHIR emitter (`tests/streaming/emit/test_faults.py::test_emit_to_two_files` exited 2: "table 'member' lacks required column(s)"). `open_sink` now tries the emitter named after the scheme first, the rule `find_source` already uses for stream sources. Regression test `test_a_plugin_emitter_on_file_does_not_take_over_the_file_emitter` fails before the fix and passes after it. |
| 9770d13 | **this session.** W1-01 stamps the run manifest with `shape_version` and `min_shape_version`. INT-15's fabric commands parity harness and the shape-fabric publish test accepted exactly W1-03's four Shape-only keys, so the parity run failed on `publish` ("manifest keys ... != ...") and `plugins/shape-fabric/tests/test_publish.py::test_lakehouse_writes_the_landing_zone_and_the_manifest` failed. Both pass on `build/main-plan`. The fix follows INT-15's pattern: the harness accepts exactly those six keys, requires `shape_version == engine_version` and `min_shape_version` to equal `shape.compat`'s first release for the declared `version`, and gains three negative controls (writing release removed, writing release changed, minimum reading release changed). `docs/SCENARIO_PACKS.md` lists the two keys. |

Choice recorded (spec silent): the harness change is the same kind of change INT-15's lead made for W1-03's keys
(§2.3, INT-15 row). Exact-value checks and negative controls keep it strict. Every key the baseline also writes is
still compared as before.

## Environment (this session)

Python 3.11.15, `~/.venvs/shape`: `pip install -e ".[dev,advanced,streaming]"`, all eleven `plugins/*` editable
(`plugins/shape-healthcare-standards[test]`), `dbt-duckdb`, `-r tests/demo/fabric/requirements.txt`, unixODBC
(apt). Rust kernel built by maturin. Baseline venv from `benchmarks/vs_refengine/setup_refengine.sh`. Resolved versions
include numpy 2.4.6, pandas 3.0.6 and **pyarrow 19.0.1**. `fabric-user-data-functions` (from
`tests/demo/fabric/requirements.txt`) requires `pyarrow>=19.0.1,<20.0.0`.

## Commands and results (final tree unless noted)

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check` (same paths) | 1344 files already formatted |
| `mypy` | no issues in 476 source files |
| `compileall`, `vulture`, `lint-imports` | clean; contracts 1 kept, 0 broken |
| `check_requirements`, `check_secrets`, `check_user_facing`, `check_shipped_data`, `check_plugin_skeletons`, `check_conformance_coverage` | all OK (89 requirements; 33 data files; 11 distributions; 32/32 statements) |
| `python scripts/check_user_facing.py` | clean |
| make check: `pytest -m "not emulator and not live and not heavy" ... --cov=shape --cov-fail-under=86` | 8466 passed, **2 failed** (#76 below); coverage 92.72% |
| make check: `pytest -m heavy tests/kernel tests/profile tests/streaming` (run after 4edaeb1; `src` unchanged since) | 41 passed, **1 failed** (#76: `float16` hashing) |
| make check: `SHAPE_KERNEL=python pytest tests/kernel` (same) | 263 passed, **2 failed** (#76) |
| `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test` | clean, clean, 34 passed |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` (includes `tests/demo/fabric` and `tests/demo/content`) | 8779 passed, **3 failed** (#76), 16 deselected (2033 s) |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 8779 passed, **3 failed** (#76, same three as rust), 16 deselected (6399 s) |
| `pytest -m "not emulator and not live"` in each `plugins/*` (`SHAPE_DBT_PACKAGES_FILE` local, see below) | behavior 86, databases 155, dbt 115, domains 6, eventhubs 42, fabric 463 + **1 failed**, healthcare-codes 100, healthcare-standards 138, kafka 46, simulation 155 + **1 failed**, sqlserver 150 passed |
| `fabric_commands_1to1/verify.py` | VERDICT: PASS (exit 0); before 9770d13: FAIL on `publish` manifest keys |
| `fabric_commands_1to1/verify.py --negative-control` | with pyarrow 19.0.1: exit 2, "restoring the landing zone did not restore equality". Same exit 2 on `build/main-plan` in this venv. With pyarrow 25.0.1 put first on `PYTHONPATH` (`pip install --no-deps --target`): **exit 0, PASS**, which includes the three new manifest controls |

shape-dbt's `dbt` marker tests need `dbt deps`. This network policy blocks the hub's tarball host (codeload, 403),
so the run used `SHAPE_DBT_PACKAGES_FILE`, which the test documents for this case: a `packages.yml` with `local:`
clones of dbt-utils 1.4.1, dbt-expectations 0.10.10 and dbt-date 0.21.0, made from github.com in the session
scratchpad, outside the repository. Without it the 7 `dbt` tests error at `dbt deps` ("not a gzip file").

## Failures not fixed here (all pre-existing, all pyarrow < 25)

Each one fails on `origin/build/main-plan` (5c91ea50) in the same venv: a worktree with `PYTHONPATH` set to its
`src` and its `plugins/*/src`. Each one passes on INT-16 with pyarrow 25.0.1 first on `PYTHONPATH`.

| Test | Error | Owner |
|---|---|---|
| `tests/kernel/test_hashing.py::test_one_and_one_point_zero_hash_equal` | `ArrowTypeError: Expected np.float16 instance` | issue #76, lane/BF-76 (not merged into `build/main-plan`) |
| `tests/kernel/test_hashing.py::test_rust_equals_reference_on_a_million_values[float16]` (heavy) | same | #76, BF-76 |
| `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date` | read-back gains a dictionary `ingest_date` hive partition column | #76, BF-76 |
| `plugins/shape-fabric/tests/test_lakehouse.py::test_parquet_to_a_local_folder_round_trips` | dictionary indices `int32` vs `int8` on read-back | same pyarrow < 25 class; not in BF-76's three tests |
| `plugins/shape-simulation/tests/test_scd2_file_drops.py::test_deltas_have_the_snapshot_columns_in_the_snapshot_order` | extra `dt` hive partition column on read-back | same class; not in BF-76's three |
| `fabric_commands_1to1/verify.py --negative-control` (landing restore) | `pq.read_table` of `.../dt=latest/part-0001.parquet` adds a `dt` column, so the restored file differs | same class |

pyarrow 19.0.1 checked directly: `pq.read_table('h/dt=latest/p.parquet').column_names == ['a', 'dt']`.
For the lead: BF-76 covers the first three. The last three need the same treatment (read a single file without
partition discovery, or compare with the dictionary index width normalized). Alternatively the Fabric test
requirements should stop pinning pyarrow below 25 in the core suite's venv.

## Not run here

Emulator and live tests (the 16 deselected in core; the deselected ones in the plugins); the Windows and macOS
legs; a CI run of the branch.

## Landing (2026-10-04)

`int/INT-16` at 13b8a03a merged into `build/main-plan` (5c91ea50, unchanged since INT-15) as a merge commit
("INT-16: land"); the plan's §2.3, §7, §9, §11 and §13 are updated in the next commit. The merged tree equals
`int/INT-16`'s.

### Fixes in the final sessions

| Commit | Fix |
|---|---|
| 75b2d2c | an existing table in create mode is a `WriteError` in the SQL writers (sqlserver sink) |
| 131513b | the scorecard reads column owners from W1-04's project file layout (W3-06 x W1-04) |
| ad328fa | `verify_types` skips the datasets on which the baseline raised (no dtype to compare; `verify.py` checks the error category) |
| 3a36088 | the bridge `list` describes each domain by its own description; BR-8, a named baseline defect checked both ways (P6-11 x P6-01a-e) |
| 0a82fe5 | the demo parity probe expects the healthcare scenario to run the healthcare domain (P6-12 x P6-01b) |
| 293cf7b | the domain description test leaves the baseline-name check to `check_user_facing` (D-13 gate, which scans every file under `plugins/`) |
| 13b8a03 | safe-profile parity checks ISS-profile's PII rates on both sides (below) |

**Safe-profile parity (13b8a03).** `safe_profile_1to1/verify.py` failed every mapper case (20 mismatches each):
the safe columns carry `pattern_rates` and `pattern_contains_rates`, which ISS-profile added for issue #2 (integrated
in INT-13; commit 20456436). It failed the same way on `build/main-plan` 5c91ea50 (120 mismatches, same keys), and its
early return on a key mismatch hid every other field of those columns. Decision: the keys stay in the safe profile.
They are aggregate shares (no values), the PII gate reads them, and a sparse SSN or e-mail leak is exactly what #2
asks the safe profile to keep visible. The harness now names them and checks them before setting them aside: absent
from every baseline column (never stripped from the baseline), present in every product column, and equal to the raw
profile's rates. Every other field is compared as before. Tests first: `tests/benchmarks/test_safe_profile_verify.py`
(4 of 5 failed before the change). Result: `PARITY OK`, exit 0; a tampered e-mail rate is caught in all six mapper
cases.

### Verification of the landed tree

The run used the `int/INT-16` tree in the lead's venv (Python 3.11.15, numpy 2.4.6, pyarrow 25.0.1, the eleven
plugins installed in editable mode), the baseline venv from §1.2 and the pinned checkout (read only). It had 91 steps.
Every step exited 0 except `verify_types` (pre-existing, below). Some part-c verifiers ran on earlier heads of this
branch (2026-10-03 23:31 to 2026-10-04 13:50) and were not repeated: the profile verifiers in both kernels and the
CLI, DDL, db parity, retail export and T-21 small, tiers, simulation files and patterns, incremental, pack, stream
profile and stream parity, chaos, and learn. Later commits changed only the scorecard, the SQL writer's create mode,
domain descriptions and the bridge catalog, test files and harnesses that were themselves re-run.

| Check | Result |
|---|---|
| ruff, ruff format, mypy | clean; 1344 files formatted; no issues in 476 files |
| compileall, vulture, import contracts, requirements, secrets, user-facing (tree and wheel), shipped data, plugin skeletons, conformance, bandit (core and plugins) | all OK (89 requirements, 33 data files, 11 distributions) |
| cargo fmt, clippy, test | clean, clean, 34 passed |
| make-check suites (verification run) | 8472 passed, coverage 92.73%; heavy 42 passed; `SHAPE_KERNEL=python tests/kernel` 265 passed |
| `make check` on the landed commit (18ba0f4, `scripts/env.sh` with `REFENGINE_ROOT` at the pinned checkout) | exit 0: ruff, format (1345 files), mypy (476), the script checks, 8477 passed with coverage 92.74%, heavy 42, Python kernel 265, cargo fmt, clippy and 34 tests. Two earlier runs: one without `REFENGINE_ROOT` set (6 composite schema tests need the pinned checkout: `ModuleNotFoundError`), one where the realtime `tests/streaming/emit/test_runtime.py::test_bursts` failed (`[1000, 2840, 1160]` per second: about 50 ms of the burst second slipped into the next, the residual host-stall class of the 2026-10-02 P5-01b row; the test passed in the five other runs that include it and three times alone; nothing changed) |
| full suite, `SHAPE_KERNEL=rust` and `python` (`-m "not emulator and not live"`) | 8791 passed each, 16 deselected |
| plugin suites | behavior 86, databases 155, dbt 115, domains 7, eventhubs 42, fabric 465, healthcare-codes 100, healthcare-standards 138, kafka 46, simulation 156, sqlserver 150 |
| `tests/demo`; `tests/plugins` (installed, after uninstalling fabric and sqlserver, reinstalled); eleven plugin kits | 368 passed; 197 passed three times; all OK |
| strategy baselines (three), plan fixtures, row counts, coverage map | exit 0 |
| profile verify (rust, python), verify CLI, DDL, db parity, retail export and T-21 small, tiers | exit 0 |
| simulation files and `--negative-control`, patterns, incremental, pack, stream profile, stream parity | exit 0 |
| chaos parity | exit 0 (20,083 runs per tool, outputs identical cell for cell) |
| bridge parity and `--negative-control` | exit 0 (64/64 checks with negative control) |
| demo, learn, mask, safe profile (after 13b8a03), transform, writers, scale parity | exit 0 |
| `verify_1to1` (`shape verify` parity) | exit 0, 20/20 scenarios (after regenerating its reference-port retail input with `domain_1to1/generate.py`; the first run exited 2 because the input was missing) |
| fabric commands parity with `--negative-control` | VERDICT: PASS |
| start-up (`shape --version`, 5 runs) | 53 to 81 ms |

Environment notes: the shape-dbt `dbt` tests need `dbt deps`, and the hub's tarball host is blocked here. The first
plugin run used a local mirror whose dbt-expectations still declared its hub dependency on dbt_date ("duplicate
project dbt_date", 7 errors). The re-run used a mirror (session scratchpad, outside the repository) without that
declaration, through `SHAPE_DBT_PACKAGES_FILE`: 115 passed.

### Pre-existing, not changed

- `profile_1to1/verify_types.py`: 1036 columns checked, 3 differ (`x_csv_mixed_chunks.bool_then_ints`,
  `x_csv_numbers.big_int`, `x_pq_types.bin_digits`: dtype inference against the baseline's). The same 3 differ on
  `build/main-plan` 5c91ea50.
- #76: the three tests that fail with pyarrow < 25 (BF-76). They pass in this venv (pyarrow 25.0.1). The plugin
  read-back failures listed above for pyarrow 19.0.1 belong to the same class.

### Nightly run 37202803596 (int/INT-16 at 4a5e2214)

16 of 18 jobs passed: sqlserver-e2e, kafka-e2e, eventhubs-e2e, azurite-e2e, databases-e2e, emulator-compose,
emit-rate-soak, emit-live, fabric-live, fabric-git-sync-live, abfss-live, dbt-build, drift-sweep, artifact-fuzz,
benchmarks-full and healthcare-standards-validator. Two failed and are filed: fabric-demo-windows (Spark tests on
Windows, "Can not create a Path from an empty string", #763; W1-09 stays wip) and fabric-emit-e2e (Eventhouse writer
against the Kusto emulator: table not ready, quoted column name, #764).

### Issues

Closed by this landing, each with its covering test on `build/main-plan`: #52, #55, #59, #60, #61, #62, #64, #68, #70,
#71, #72, #73, #74, #77. Still open: #67, #76, #78, #763, #764.
