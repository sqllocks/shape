# CI-FIX — red CI on build/main-plan (run 37007349103, 7e54b90)

Branch: `lane/CI-FIX`. No gate, tolerance or decision was changed; no test was skipped, xfailed,
retried or marked. `python scripts/check_user_facing.py` is clean (D-13).

Status: items 1, 2, 3, 4, 6 and 7 are fixed. Item 5 (macOS realtime pacing) is **not** a runtime
defect; the owner decided (2026-10-02) to run the realtime-timing tests on Linux only (below). The
green run ids are recorded at the end.

## Root causes and fixes

| # | failure | root cause | fix |
|---|---|---|---|
| 1 | `TypeError: ndtr() takes from 1 to 2 positional arguments but 3 were given`, 17 tests in `tests/fidelity` (plus the 5 errors of item 6) | CI resolved scipy 1.18.1 (numpy 2.5.3). `scipy.stats.kstest(x, "norm", args=(loc, scale))` now resolves the name to the bare `scipy.special.ndtr` ufunc and calls it as `ndtr(x, loc, scale)`, which raises. Not a name collision with `shape.profile.reference.numerics.ndtr`. The call was in `_gap_distribution` (`src/shape/fidelity/tier1.py`). | The normal fit is passed as a frozen distribution's cdf, `stats.norm(loc, scale).cdf`, which is the same arithmetic on every SciPy. No dependency bound was needed: SciPy 1.18 is not otherwise incompatible. Regression test `test_the_gap_distribution_does_not_hand_scipy_a_named_norm_with_args` (a stub `kstest` that raises the 1.18 error for the named form) fails on the old code and passes now. scipy 1.18.1 was not installable in the builder VM's package index, so the SciPy-side behaviour is read from the CI traceback, not reproduced locally. |
| 6 | "5 errors" (`tests/benchmarks/test_fidelity_tiers_1to1.py`, 4 x `test_tier_1_equals_the_baseline`, `test_the_mixture_fits_are_equal_not_merely_close`) | The same TypeError, raised while the module fixture `with_tier1` ran the tier-1 profiler (an error in fixture setup, not in a test body). | Same fix as item 1. |
| 2 | Windows: `AttributeError: module 'signal' has no attribute 'SIGKILL'` and `wait(timeout=30)` returned 1 (`tests/streaming/emit/test_emit_cli.py`) | The tests used POSIX signals. On Windows `Popen.send_signal(SIGTERM)` is `TerminateProcess`: the child cannot run its handler, so no shutdown checkpoint and exit code 1. The product registered handlers only for SIGINT and SIGTERM, and Windows has no catchable SIGTERM. | Tests: hard kill is `Popen.kill()` (SIGKILL on POSIX, `TerminateProcess` on Windows) and the exit code is asserted per platform; the graceful stop is SIGTERM on POSIX and `CTRL_BREAK_EVENT` on Windows, with the child started in its own process group. Product: `shape emit` now also handles `SIGBREAK` (Ctrl-Break) so Windows has a graceful stop that writes the shutdown checkpoint. The checkpoint writer (temp file, fsync, `os.replace`) and the `repair_tail` resume path were read for Windows problems and need no change. The durability tests are unchanged in what they assert (kill, restart, same bytes after de-duplication). |
| 2b | Windows: `test_registry_reindex_rebuilds_and_reports_skips` (not in the brief) | The skipped-file message used `str(Path)`, i.e. backslashes, while the registry's index keys and the test use `crm/orders/...`. | `reindex` reports the path with `as_posix()`. |
| 3 | Windows `stream-plugins`: `DomainNotFoundError: no domain named 'retail'` | The job installed `plugins/shape-kafka` and `plugins/shape-eventhubs` but not `plugins/shape-domains`, where the `retail` domain lives; `default_plan()` loads it. (Ubuntu was cancelled in that run, not green.) | `ci.yml`: the job also installs `-e plugins/shape-domains`, as the `test` job does. The tests are unchanged. |
| 4 | macOS: `test_count_numeric_infinities_and_whole_numbers` (`assert False == True`) | The Python reference twin computed `all_whole` as `np.all(x == x.astype(np.int64))`. Converting a float outside the int64 range (or inf, NaN) is platform-defined: x86 gives INT64_MIN, arm64 saturates to INT64_MAX. On arm64 `2**63` compared equal to `float(INT64_MAX)` and counted as whole, while the native kernel (`is_whole` in `exact.rs`, which emulates x86) said no. The same cast was in `profile/infer.py` (two places) and `profile/reference/column.py`, where it decides "integer" vs "float" for a column. | One platform-independent rule, `all_whole` in `kernel/reference/exact.py`: finite, in `[-2**63, 2**63)`, no fraction (the native kernel's rule). Used at all four sites. Test `test_all_whole_does_not_depend_on_the_platform_int64_conversion` (2**63, 1e300, +-inf, NaN, -2**63, fractions, empty). The Rust side needed no change. |
| 7 | CI queue (175 runs, 5+ hour waits) | Every push to a `lane/*` branch ran the full matrix plus Security, with no cancellation of superseded runs. | `concurrency: {group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true}` in `ci.yml` and `security.yml` (first commit). Superseded runs on the same ref show as cancelled; other refs are untouched. No job or platform was removed. |

## Item 5 — macOS realtime pacing (not a runtime cause; owner decision)

Failing on macOS in both CI runs: `test_realtime_rate_within_five_percent` (`max_lag` 0.084, 0.085,
0.083; limit 0.05) and `test_duration_also_bounds_a_fast_run` (400 events, 4 batches, where 5 to 12
are expected). The tests, the 5% and 0.05 limits and the runtime pacing are unchanged.

Evidence (`scripts/pacing_diagnostic.py`, run by `.github/workflows/pacing-diagnostic.yml`, run
37058625122, job 111009402760, macos-latest = macOS 26.6.2 arm64, Python 3.11.9, 3 vCPU):

* The runner is oversubscribed: load average 14.9 / 18.6 / 10.1 on 3 CPUs at the start of the
  probe. The busiest processes were Spotlight (`mds_stores` 72%, `mdworker_shared` x6),
  `softwareupdated`, `iconservicesagent`, `secinitd`, `storagekitd`, `Runner.Worker`.
* **A shape-free probe shows the same stall.** A thread waiting on absolute 10 ms deadlines (no
  shape code, no allocation) was late by more than 20 ms about 100 times in 6 s in round 0 (worst
  40.8 ms), and worst 89.9 ms and 73.2 ms in rounds 1 and 2. The lateness comes in staircases
  (67, 57, 47, 37, 27 ms: one stall, then the 10 ms deadlines caught up), the signature of the
  whole process not being scheduled.
* **The duration test's own `time.sleep(0.05)`** took 121.9, 121.9, 89.0, 104.8, 54.1, 111.9, 51.1,
  122.2, 101.3, 82.4 ms in round 0 and up to 204 ms in round 1. That sleep is in the test's sink,
  not in the runtime: four iterations in 0.5 s is what the host allows.
* An emit run with a 1 ms ticker thread beside it: `max_lag` 68.5, 76.5 and 75.6 ms, with ticker
  gaps of 22 to 34 ms and `per_second` such as `[1960, 2000, 2040, 1920, 2080, 1920, 80]`. The
  runtime was late by about what the ticker was.
* The Linux job of the same workflow: bare sleep worst 2 ms, `time.sleep(0.05)` 50.2 ms,
  `max_lag` 5 ms. The builder VM: `max_lag` 3.8 ms, ticker worst 4.8 ms.
* Block generation (45 ms for the first 8,000 rows here) does not hold the GIL: the ticker saw no
  gap above 5 ms while it ran.

Conclusion: the macOS runner stops every thread of the process for 20 to 90 ms at a time, with or
without shape code, because Spotlight and system daemons keep it at a load of 15 on 3 cores. The
runtime schedules by absolute deadline, prefetches about one second ahead and now keeps GC and
checkpoint writes off the pacing thread (P5-01b); nothing remains that it can do about a stalled
host. Not done, deliberately: raising the pacing thread's QoS class (`pthread_set_qos_np`) was not
tried, because no evidence here says it would beat the contention and the criterion should not
depend on an undocumented scheduler hint.

### Owner decision (2026-10-02): realtime-timing tests run on Linux only

Implemented exactly that, with no change to any assertion, tolerance or duration:

* `pyproject.toml` registers the marker `realtime`: "wall-clock pacing assertions; run on Linux CI
  runners and the nightly soak".
* Marked tests (all in `tests/streaming/emit`): in `test_runtime.py`
  `test_duration_stops_a_realtime_run`, `test_duration_also_bounds_a_fast_run`,
  `test_realtime_rate_within_five_percent`,
  `test_realtime_rate_holds_through_a_full_collection_of_a_large_heap`,
  `test_realtime_rate_holds_while_the_checkpoint_write_is_slow`, `test_bursts`,
  `test_realtime_resume_starts_its_own_clock`,
  `test_slow_sink_in_realtime_falls_behind_without_dropping`; in `test_soak.py`
  `test_realtime_rate_holds_at_10000_events_per_second` (also `heavy`).
* `ci.yml`: the macOS matrix entries add `and not realtime` to the `-m` selection of both pytest
  steps that can reach those tests (the main suite and the `-m heavy` step, which runs the soak).
  Linux and Windows run them; the nightly `emit-rate-soak` is unchanged; local runs
  (`pytest -m "not emulator and not live and not heavy"`) still run them. Nothing is skipped
  anywhere else.
* `pacing-diagnostic.yml` (a probe only) was removed. `scripts/pacing_diagnostic.py` is kept: run
  it by hand (`python scripts/pacing_diagnostic.py`) on a suspect runner to print scheduler stalls.

## Local checks (builder VM, Python 3.11, numpy 2.4.6 / scipy 1.17.1)

ruff check and `ruff format --check` (`src tests plugins benchmarks/vs_spindle`) clean; mypy strict
(307 files) clean; vulture clean; lint-imports 1 kept, 0 broken; `check_user_facing` clean;
`bandit -q -r src -ll` no findings; `cargo fmt --check`, `cargo clippy --all-targets -D warnings`,
`cargo test` (debug) pass; `pytest -m "not emulator and not live and not heavy"
--ignore=tests/demo/fabric` passes with `SHAPE_KERNEL=rust` and with `SHAPE_KERNEL=python`.

## CI on lane/CI-FIX

(filled in at the end: run ids and the green run)

## Round 3

Branch `lane/CI-FIX`; merged `origin/build/main-plan` first (no rebase). §11 and §2.3 untouched.

### Fixed (root causes)

1. **Fuzz smoke `profile-json` RecursionError** (`src/shape/privacy/safe_validator.py`).
   `json.loads` accepts nesting up to ~990 levels, but the recursive `_walk` needs a few more
   frames than the parser, so input nested to depth ~993 overflowed (earlier in pytest, whose
   stack is deeper). `validate_data` now measures depth iteratively and rejects anything deeper
   than `MAX_NESTING_DEPTH = 64` with the validator's existing `malformed` finding (the same
   rule used for other malformed profile JSON). Seed derivation and the smoke assertion are
   unchanged. Regression tests in `tests/privacy/test_safe_validator.py`: depths 65/500/993/5000
   as list and dict (all `malformed`, no exception) and a depth-60 document still scanned.
   Reproduced before the fix with a depth sweep (RecursionError at depth 993).
   Note: the exact CI inputs (iterations 13 and 17) did not reproduce in this container
   (Python 3.11, shallower stack); the sweep above is the reproduction.
2. **OneLake paths on Windows** (`plugins/shape-fabric/src/shape_fabric/onelake.py`): `join` and
   `parent` used `pathlib.Path` for non-URI bases, giving backslashes on Windows. They now use
   `posixpath`, and `PureWindowsPath` only for a base that already has a drive letter or
   backslash. Remote (`abfss://`, `onelake://`) paths were already `/`-joined. No other
   `os.path`/`Path` use on remote paths in shape-fabric, shape-eventhubs, shape-sqlserver or
   shape-kafka (`_storage.py` uses `Path` for genuinely local files only). Not run on Windows
   here; the POSIX run of `test_onelake.py` passes.
3. **bench-quick**: the Shape venv step in `.github/workflows/ci.yml` now installs
   `-e plugins/shape-domains` next to `.[dev]`. That venv serves every later bench step
   (`run.py --quick`, domain_1to1, verify_1to1), so they all get the domains.
4. **Secret scan**: the scrubber tests that carry synthetic secret literals moved from
   `plugins/shape-fabric/tests/test_recorded.py` to
   `plugins/shape-fabric/tests/security/test_tape_redaction.py` (exempt by the existing
   "tests" + "security" rule). `check_secrets.py` and the literals are unchanged. The contract
   tests stay in `test_recorded.py`. The stream-plugins job runs the whole `tests` directory,
   so the moved tests still run (17 collected there).

### Checks run (this session, Python 3.11, venv `$SHAPE_VENV`)

- `ruff check` / `ruff format --check` on src tests plugins benchmarks/vs_spindle: clean.
- `mypy`: no issues in 330 files.
- `pytest -m "not emulator and not live and not heavy"` (ignoring tests/demo/fabric, content):
  4747 passed.
- Fuzz smoke (`tests/validation`): pass. `scripts/fuzz_artifacts.py`, 60 iterations, seeds
  today and today ±1, 3, 5, 7, 10, 12 days: 0 findings each.
- `python scripts/check_secrets.py`: OK.
- Plugins (kafka, eventhubs, sqlserver, fabric) `-m "not emulator and not live"`: 417 passed.
- `stream_1to1/verify.py --scale small` and `--scale medium` against the pinned Spindle
  (set up with `benchmarks/vs_spindle/setup_spindle.sh`): both exit 0, VERDICT PASS.
  My venv held all plugins, so it does not prove the CI venv alone; the CI change mirrors the
  stream-plugins job.

## Round 5 — bench-quick, macOS kill test, intermittent abort at exit (run 37104138958 on 435348b)

No gate, tolerance or decision changed; no test skipped or weakened; `$SPINDLE_ROOT` untouched.

| # | failure | root cause | fix |
|---|---|---|---|
| 1 | `bench-quick`: `reference_port profile:d1/d2 .csv/.parquet` verifier=fail (also red on build/main-plan) | Reproduced locally the way CI runs it (pinned Spindle venv, `run.py --quick --only profile`): 12 mismatches in `value_counts_ext` / `value_counts_ext_order`, all `spindle=null port={...}` is wrong way round. ISS-profile #37 (near-unique text lists no values) was added to `shape.profile.reference` and to the allow-list in `verify.py` (the baseline column becomes "no list"), but not to the benchmark's standalone `profile_1to1/port.py`, which still listed the top 500. `--impl shape` passed because the product has the rule. | `port.py` applies the same rule (string column, more than 500 distinct values, at least 95% of the non-null count: no `value_counts_ext`). The verifier is unchanged. Test `tests/profile/test_reference_port_near_unique.py` fails on the old port, passes now. After the fix `run.py --quick --only profile` exits 0: all eight profile rows pass. |
| 2 | macOS 3.14 `test_kill_9_then_restart_equals_an_uninterrupted_run[21000]`: `assert 21750 < 21750` | At 6000 events/s the stream has 750 events (125 ms) left after the 21000 kill point. A stalled runner (see item 5 above) delays the kill past the end of the run, so the process had already written everything. | Each kill point has its own rate (`KILL_RATES`); the late one runs at 750 events/s, and the test asserts at least a second of stream is left after the kill point. What the test proves is unchanged: kill -9, restart from the checkpoint, same bytes after de-duplication. |
| 3 | ubuntu 3.11 `test_a_failed_program_run_still_reports`: exit -6, "terminate called without an active exception" | Not gc: it reproduces with gc enabled. After `load_target('retail')` the process has 4 Arrow CPU-pool native threads (`/proc/self/task`; no Python threads), and an interpreter teardown with them alive aborts about 1 in 100 runs under load (2/300, 14/600 with 16 parallel runs). A successful `generate` never tears down (`os._exit` after flushing); a failed one did. Importing pyarrow or the engine alone does not reproduce it (400 runs each). | `generate` sets `lifecycle.exit_on_return` once it may start native pools; `main()` then ends the process through `exit_now` (flush, `logging.shutdown`, `os._exit`) with the command's own exit code, as a finished run does. Still off when logging or metrics are on, and for `main(argv)` calls from tests. Tests: `test_a_failed_generate_ends_without_the_interpreter_teardown` (no atexit handler runs) and `test_a_failed_program_run_exits_2_every_time` (160 runs, 8 parallel; it failed on the old code with -6). After the fix: 400 runs, all exit 2. |

Checks (Python 3.11): `ruff check`, `ruff format --check`, `mypy` clean; `pytest tests/cli tests/profile`
470 passed; the kill_9 tests and the generation CLI tests pass.
