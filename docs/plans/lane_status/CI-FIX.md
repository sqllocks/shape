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

## Round 4

Branch `lane/CI-FIX`; merged `origin/build/main-plan` and `origin/lane/CI-FIX` first (merge commits, no
rebase). §11 and §2.3 untouched. Failures from CI run 37081340730 (lane/CI-FIX @ 4a6b857). No test was
skipped, xfailed or weakened, and no bound or tolerance was changed.

### Fixed (root causes)

1. **Windows mypy** (`src/shape/scale/router.py`): `resource` does not exist on Windows. `peak_rss_gb`
   now branches on `sys.platform == "win32"` (which mypy understands): Windows reads the peak working
   set from `GetProcessMemoryInfo` (`psapi`, via `ctypes`, in `_windows_peak_working_set_bytes`);
   POSIX keeps `ru_maxrss`. Any failure still returns 0.0, the documented "platform cannot say".
2. **Windows 3.14**
   a. `file:///C:/...` (what `Path.as_uri()` writes): `local_path` in `builtins/sources/files.py`
      kept the leading `/` of the URI path (`/C:/x`), so `Delta` and the other file sources never found
      the table. A `/X:` prefix is now the drive path (same rule `streaming/file_source.py` already used);
      the same fix is in `builtins/emitters/uri_path`. Test: `test_a_file_uri_with_a_drive_letter_is_the_drive_path`
      (platform independent).
   b. `git diff` exit 128: git runs a textconv command through `sh`, and the test passed
      `sys.executable` with backslashes unquoted, which `sh` strips (the CI log shows only the exit code,
      not git's stderr; the test's `_git` helper now reports stderr). Product: new
      `shape.cli.gitcmds.python_textconv_command()` writes the interpreter with forward slashes in
      double quotes; `shape git-setup` uses it when `shape` is not on PATH, and the test uses it. This is
      the diagnosis from reading the code and the log; it could not be run on Windows here, so the CI run
      below is the check.
   c. Job store privacy: Windows ignores POSIX modes. `JobStore` now calls
      `restrict_to_current_user(root)` once per store: `icacls <dir> /inheritance:r /grant:r
      DOMAIN\user:(OI)(CI)F`, so the directory has one entry (the current user) and every file created in
      it inherits that. `icacls` ships with Windows (no pywin32 dependency). Failure to set the ACL raises
      `OSError` (a store that cannot be private is not used). The test keeps its intent on every platform:
      POSIX asserts mode 0600 (file) and 0700 (directory); Windows asserts, from `icacls`, that the file
      has exactly one ACL entry and that it is the current user; both assert that the file holds no token
      and that no temp files remain.
   d. Fuzz harness hang detection (`validation/fuzz.py`): SIGALRM does not exist on Windows (nor off the
      main thread), so the time limit was not enforced, the hang ran to completion and was reported as
      `slow: over the limit`. Without SIGALRM the target now runs on a worker thread that is waited on
      for the limit; a call still running is a hang: reported as `timeout`, the worker gets an
      asynchronous `_Timeout` (ends a Python-level loop; a C-level sleep ends when it returns) and is
      abandoned as a daemon. The worker has a 64 MB stack so deep-recursion inputs behave as on the main
      thread. The existing test is parametrized to run both mechanisms on every platform
      (`sigalrm`, `worker-thread`).
3. **Ubuntu 3.12 `test_realtime_rate_holds_through_a_full_collection_of_a_large_heap`**: not a host stall.
   Python 3.12 starts with objects already in the permanent generation (`gc.get_freeze_count()` is 375
   at startup on 3.12.3; 0 on 3.10, 3.11 and 3.13), and `_gc_frozen` took "the freeze count is not zero"
   to mean the host had frozen, so on 3.12 it never froze and the collection scanned the whole heap
   (`max_lag` equals the full-collection time, `per_second` `[1880, 2120, 2000, 2000]`, the same shape as
   before P5-01b). Reproduced on 3.12 (5 of 5 runs, `max_lag` 0.21 to 0.32 s, with and without
   `--cov`, under 6 CPU burners on 4 cores; a gen-2 collection of 0.21 to 0.32 s during the run seen
   with `gc.callbacks`); not on 3.11. Fix in `streaming/emit/runtime.py`: a freeze counts as the host's
   only when it is larger than the count when the module was imported (`_GC_FROZEN_AT_IMPORT`); a run
   that starts while another paces freezes again (it used to skip, so objects the host built in between
   stayed unfrozen). After the fix: 20 of 20 runs pass on 3.12 under the same load. The bound
   (`max(0.1, full / 2)`) is unchanged. The test's last assertion was `not gc.get_freeze_count()`, which
   is false at startup on 3.12; it is now `<=` the count at the start of the test (the freeze is lifted).
   New tests: `test_the_run_freezes_when_the_interpreter_already_froze_some_objects` (fails on the old
   code on 3.12) and `test_a_freeze_made_by_the_host_is_left_alone`.

### Checks run (this session)

- Python 3.11 venv (`$SHAPE_VENV`): `ruff check` and `ruff format --check` on `src tests plugins
  benchmarks/vs_spindle` clean; `mypy` clean (347 files); `check_user_facing` clean.
- Targeted tests on 3.11 and 3.12: `tests/builtins/test_cloud_sources.py`, `tests/cli/test_shape_as_code.py`,
  `tests/scale/test_jobs.py`, `tests/validation/test_fuzz_smoke.py`, `tests/streaming/emit`.
- Full `pytest -m "not emulator and not live and not heavy"` (ignoring `tests/demo/fabric` and
  `tests/demo/content`) on 3.11 with `--cov=shape` (before the fixes in 2.c and 3: 5198 passed, 16 skipped
  for lack of scikit-learn in that venv) and on 3.12: see the CI section below.
- Not runnable here: the Windows and macOS behaviour; the CI run is the check.

### CI on lane/CI-FIX (round 4)

Run 37094298568 (cd78ad3): every job green except the two Windows test jobs, including **ubuntu 3.12**
(the realtime GC test, item 3), both macOS test jobs, mypy on Windows (item 1) and the Windows
`stream-plugins`. Windows 3.14 passed items 2a, 2b and 2d (3.14 had only the two failures below); Windows
3.11 ran its tests for the first time (mypy now passes) and had three failures. Remaining after this run:

* **`tests/scale/test_jobs.py::test_store_files_are_private_and_hold_no_token`** (3.11 and 3.14): the file
  still carried inherited entries (3.11: SYSTEM, Administrators and the user; 3.14: those plus OWNER
  RIGHTS, which is the ACL Python 3.13+ gives `mkdir(mode=0o700)`), so the directory-level `icacls` did
  not leave the user alone as an inheritable entry. I could not see the directory ACL in that log, so the
  store no longer depends on inheritance: each file's ACL is set with `icacls <tmp> /inheritance:r
  /grant:r user:F` before the atomic replace (the ACL travels with the file), the directory keeps its
  own restriction, and a failing assertion now prints both ACLs.
* **`tests/quality/test_verify_config.py::test_the_report_names_the_config`** (3.11 and 3.14): the test read
  the report with the locale encoding (cp1252); the product writes it as UTF-8 explicitly
  (`cli/main.py`). The test reads UTF-8.
* **`tests/validation/test_fuzz_smoke.py::test_smoke_run_has_no_findings`** (3.11 only):
  `pack-yaml` iteration 12 (the input is `[` x 10 000) hit the 5 s limit. Root cause: PyYAML's scanner
  revisits every open flow collection per token, so deep flow nesting is quadratic (0.9 s here on
  3.11; under `--cov` on a slower Windows runner over 5 s) before the composer's recursion limit refuses
  it. `shape.security.yamlsafe` now refuses flow nesting over `MAX_FLOW_DEPTH = 100` with a linear
  pre-scan (quoted scalars and comments skipped; same "nested too deeply" error as before). The limit
  of the harness is unchanged. Test: `test_deeply_nested_yaml_is_refused_in_linear_time`.

Checks before the next push: ruff, ruff format --check, mypy (also `--platform win32` on the two files
with Windows-only code) clean; `tests/validation tests/security tests/scenario tests/scale
tests/quality/test_verify_config.py` pass.
