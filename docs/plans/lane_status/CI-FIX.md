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

ruff check and `ruff format --check` (`src tests plugins benchmarks/vs_refengine`) clean; mypy strict
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

- `ruff check` / `ruff format --check` on src tests plugins benchmarks/vs_refengine: clean.
- `mypy`: no issues in 330 files.
- `pytest -m "not emulator and not live and not heavy"` (ignoring tests/demo/fabric, content):
  4747 passed.
- Fuzz smoke (`tests/validation`): pass. `scripts/fuzz_artifacts.py`, 60 iterations, seeds
  today and today ±1, 3, 5, 7, 10, 12 days: 0 findings each.
- `python scripts/check_secrets.py`: OK.
- Plugins (kafka, eventhubs, sqlserver, fabric) `-m "not emulator and not live"`: 417 passed.
- `stream_1to1/verify.py --scale small` and `--scale medium` against the pinned RefEngine
  (set up with `benchmarks/vs_refengine/setup_refengine.sh`): both exit 0, VERDICT PASS.
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
  benchmarks/vs_refengine` clean; `mypy` clean (347 files); `check_user_facing` clean.
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

Run 37097912266 (26cbb4d): every job green except the two Windows test jobs, and on both of those the
whole main suite passed (3.11: 5188 passed, 2 skipped; 3.14: 5188 passed, 2 skipped), so items 1 and 2
and the three round-4 follow-ups above are confirmed on Windows. What failed is the *heavy* step, which
had never run on Windows before (the earlier runs stopped at the first step):
`tests/streaming/emit/test_soak.py::test_realtime_rate_holds_at_10000_events_per_second` (10 minutes at
10,000 events/s, a Linux-and-Windows test by the owner decision):

| job | first bad 10 s window | overall rate | `max_lag` |
|---|---|---|---|
| Windows 3.11 | at 100 s: 6,260 events/s | 10,000.0 (within 5%) | 5,030 ms |
| Windows 3.14 | at 140 s: 6,500 events/s | 10,000.0 (within 5%) | 5,618 ms |

One stall of about 5 s in the run (the next window shows 13,500 to 13,740 events/s: the schedule caught
up, nothing was dropped). Ubuntu and macOS legs of the same step pass (Linux max lag 50 ms in the P5-01
measurement). Nothing in the runtime waits for 5 s (`q.put`/`q.get` time out at 0.05 s, the delivery
retries total at most 0.7 s, `max_queue_depth` and `retries` were not printed), so the cause is either a
whole-process stall (host) or something in the process holding the GIL; the log does not say which.
No fix was made on a guess. The soak test now carries evidence for its own failure, with no change to
any assertion or bound: a sleeping probe thread (gaps over 0.25 s, with the second into the run), a
`gc` callback (collections over 0.1 s, with generation), `faulthandler` stack dumps when the probe has
not run for 3 s (they name the code holding the GIL), the list of seconds off the rate, the retries
and the queue depth, all printed in the assertion message. The next Windows run decides: a gap in the
probe with `none` for dumps and no long collection is the host (then this item goes to the owner, as
for macOS); a dump naming shape or numpy code, or a long collection, is a runtime cause to fix.

### Windows soak stall: evidence (run 37100249946, 167e6e7)

Every job was green on the evidence push except Windows 3.11, and there only the soak failed (the main
suite passed again: 5188 passed). Windows 3.14 passed the same soak in this run (so it is intermittent:
3.11 failed in two of two runs, 3.14 in one of two). The failure message of the 3.11 run:

* `per_second` off the rate: `[(42, 8600), (43, 0), (44, 5700), (45, 1300), (46, 34400)]`: events stop
  for about 4 s from second 42 and the schedule then catches up (34,400 in one second); the overall
  rate and every other window are exact (`min 9990, max 10010`); `max_lag` 2.46 s.
* The probe thread, which does nothing but `sleep(0.02)`, woke 1.66 s late and 0.97 s late (45.1 s into
  the run, and one earlier gap): the whole process was not running, not just the pacing thread.
* `gc` collections over 0.1 s: none. `faulthandler` dump after 3 s without the probe running: none
  (each gap was under 3 s, so no stack was captured; the gaps are intermittent pieces of one stall).
* Not the runtime's own memory or queue: on the builder VM (Linux) a 150 s run holds a flat 139 MB RSS
  (no growth) and `max_queue_depth` 100 (the generator stays a full queue ahead of the sink), so the
  pacing thread was not waiting for data, and no thread has heavy Python or C work to hold the GIL for
  a second at a time. `q.put`/`q.get` time out at 0.05 s and delivery retries total 0.7 s at most.

Conclusion: a thread that only sleeps is scheduled 1 to 1.7 s late, in pieces, several times in one
10-minute run on the Windows hosted runner, with no collection, no memory growth and a full prefetch
queue: the host (the same signature as the macOS runner in item 5 of round 2, which stopped every thread
for 20 to 90 ms). It is outside the process. No runtime change was made on this item and no bound,
tolerance or duration was touched. **For the owner:** the realtime soak
(`test_realtime_rate_holds_at_10000_events_per_second`, marker `realtime` and `heavy`) is not reliable on
`windows-latest`; the macOS decision (run the realtime tests on Linux only) would extend to the
Windows leg of the heavy step, or the soak is allowed to retry on a stalled host. I did neither. The
probe stays in the test: any future failure prints its own evidence.

### Final state of this round

* Fixed and confirmed in CI (runs 37097912266 and 37100249946): items 1, 2a, 2b, 2c, 2d, 3, and the
  three extra Windows 3.11 findings (verify-report encoding, job-store ACL, YAML flow depth). macOS and
  Linux legs, `stream-plugins`, `bench-quick`, `build`, `rust`, `audit`, `pure-wheel`, `fabric-demo` and
  `plugin-skeletons`: green.
* Open, owner's decision: the Windows realtime soak above (host stall).

## Round 5 — bench-quick, macOS kill test, intermittent abort at exit (run 37104138958 on 435348b)

No gate, tolerance or decision changed; no test skipped or weakened; `$REFENGINE_ROOT` untouched.

| # | failure | root cause | fix |
|---|---|---|---|
| 1 | `bench-quick`: `reference_port profile:d1/d2 .csv/.parquet` verifier=fail (also red on build/main-plan) | Reproduced locally the way CI runs it (pinned RefEngine venv, `run.py --quick --only profile`): 12 mismatches in `value_counts_ext` / `value_counts_ext_order`, all `refengine=null port={...}`: the port listed values where the baseline column has none. ISS-profile #37 (near-unique text lists no values) was added to `shape.profile.reference` and to the allow-list in `verify.py` (the baseline column becomes "no list"), but not to the benchmark's standalone `profile_1to1/port.py`, which still listed the top 500. `--impl shape` passed because the product has the rule. | `port.py` applies the same rule (string column, more than 500 distinct values, at least 95% of the non-null count: no `value_counts_ext`). The verifier is unchanged. Test `tests/profile/test_reference_port_near_unique.py` fails on the old port, passes now. After the fix `run.py --quick --only profile` exits 0: all eight profile rows pass. |
| 2 | macOS 3.14 `test_kill_9_then_restart_equals_an_uninterrupted_run[21000]`: `assert 21750 < 21750` | At 6000 events/s the stream has 750 events (125 ms) left after the 21000 kill point. A stalled runner (see item 5 above) delays the kill past the end of the run, so the process had already written everything. | Each kill point has its own rate (`KILL_RATES`); the late one runs at 750 events/s, and the test asserts at least a second of stream is left after the kill point. What the test proves is unchanged: kill -9, restart from the checkpoint, same bytes after de-duplication. |
| 3 | ubuntu 3.11 `test_a_failed_program_run_still_reports`: exit -6, "terminate called without an active exception" | Not gc: it reproduces with gc enabled. After `load_target('retail')` the process has 4 Arrow CPU-pool native threads (`/proc/self/task`; no Python threads), and an interpreter teardown with them alive aborts about 1 in 100 runs under load (2/300, 14/600 with 16 parallel runs). A successful `generate` never tears down (`os._exit` after flushing); a failed one did. Importing pyarrow or the engine alone does not reproduce it (400 runs each). | `generate` sets `lifecycle.exit_on_return` once it may start native pools; `main()` then ends the process through `exit_now` (flush, `logging.shutdown`, `os._exit`) with the command's own exit code, as a finished run does. Still off when logging or metrics are on, and for `main(argv)` calls from tests. Tests: `test_a_failed_generate_ends_without_the_interpreter_teardown` (no atexit handler runs) and `test_a_failed_program_run_exits_2_every_time` (160 runs, 8 parallel; it failed on the old code with -6). After the fix: 400 runs, all exit 2. |

Checks (Python 3.11): `ruff check`, `ruff format --check`, `mypy` clean; `pytest tests/cli tests/profile`
470 passed; the kill_9 tests and the generation CLI tests pass.

Green run (round 5): CI run 37108639014 on b540138 — all 19 jobs succeeded, including bench-quick,
test (macos-latest, 3.14) and test (ubuntu-latest, 3.11).

## Round 6 — red CI on build/main-plan faf5ddb7 (CI run 37313323165, Security run 37313323499)

Branch `lane/CI-FIX`, fast-forwarded to `origin/build/main-plan` (faf5ddb7, which already contained
the old `origin/lane/CI-FIX` 3aa18366; no reset, no force-push). No gate, tolerance, bound or
decision changed; no test skipped, xfailed or deselected; no recorded hash or digest changed;
`$REFENGINE_ROOT` untouched. Times are US Eastern (EDT).

| # | failure (run 37313323165 unless noted) | root cause | fix | commit | green on lane/CI-FIX |
|---|---|---|---|---|---|
| 1 | test (windows, 3.11): mypy `bridge/jobs.py:90,110` unused `type: ignore` | `ctypes.WinDLL` / `ctypes.get_last_error` exist in the Windows stubs, so `[attr-defined]` is unused there and needed on Linux. | `# type: ignore[attr-defined,unused-ignore]`, the repo's existing form for platform-dependent ignores (`generation/future.py`, `shape-sqlserver/auth.py`). | 4bc919db | (pending) |
| 2 | Security run 37313323499: collection error `tests/scale/test_kql_sink_uri.py` (`import shape_fabric`) | `security.yml` installed only `.[dev,streaming]`. | `security.yml` (its one job) also installs `-e plugins/shape-fabric`; the test runs. | 95df772e | (pending) |
| 3 | `test_composite_p601e.py::test_the_merged_schema_is_the_baselines_apart_from_the_named_differences[<6 presets>]`: `ModuleNotFoundError: sqllocks_refengine` (test, min-versions, zero-network, all OSes) | Not a test that needs the baseline: its docstring says the composites tests run from the committed fixtures (`benchmarks/vs_refengine/fixtures/composites/`, kept equal to the baseline by `plan_fixtures.py --check` in bench-quick). `_expected_differences` called `composites.children(spec)`, which for a preset imports the baseline (`sqllocks_refengine.presets`). It passed on builder VMs only because they have the pin at `~/refengine`. | The test reads a preset's domains from its fixture (`FIXTURES[key]["preset"]["domains"]`, which `test_each_preset_is_the_baselines` already asserts equal to the preset); ad-hoc specs keep `composites.children`. Passes here with no `$REFENGINE_ROOT` on disk. No marker, no move, no deselection. | 0a081be6 | (pending) |
| 4a | ubuntu (all Pythons): `test_w8_06_identifiers.py::test_the_default_output_of_a_domain_is_what_it_was[{rust,python}-{healthcare,hr}]` (retail passes) | **#768 class (CPU-dependent float path), left for W8-04b.** Evidence below. | none (no hash changed) | — | escalated: W8-04b |
| 4b | macOS (3.11, 3.14) and Windows 3.14: `tests/vault/test_generate.py::test_shape_only_generation_is_byte_identical_to_before`, `::test_a_profile_with_a_vault_generates_the_same_bytes_without_the_flag` | **Same class (platform float path), left for W8-04b.** The input `gen.csv` is digest-checked (`SOURCE_DIGEST` passes on every OS); the output differs only off Linux x86. | none | — | escalated: W8-04b |
| 5 | stream-plugins (windows) 38 failures (`shape-healthcare-standards` golden bytes `\n` vs `\r\n`, ...), test (windows, 3.14) timecapsule "modified"/"corrupt", `test_name_pools_are_frozen`, `test_shipped_files_match_the_manifest` (locales), `test_a_frozen_version_1_file_loads_and_is_rewritten_byte_identical`, `test_description_equals_the_sdist`, `test_trust_compat` (capsule `capsule_mod.py` "changed after installation") | No `.gitattributes`: the Windows runner's `core.autocrlf=true` checked out every LF text file with CRLF, so golden files, corpora, vault/timecapsule fixtures, data files hashed by manifests and README.md were not the committed bytes. | `.gitattributes`: `* text=auto eol=lf` (files committed with CRLF stay CRLF: text=auto leaves them alone) and `binary` for parquet/arrow/zip/bin/shapevault. Verified: a clone checked out with `core.autocrlf=true` is byte-identical to the Linux checkout for all 4475 tracked files (without the file, 4032 differ); `git status` stays clean. | d98b5ac6 | (pending) |
| 6a | windows 3.14: `tests/w5_10/test_fingerprint*.py` (14), `PermissionError [WinError 5]` on `*.fingerprint.tmp -> *.parquet` | Product: `embed_text` kept the source `pq.ParquetFile` open while `os.replace` replaced it; Windows refuses to replace an open file. | `fingerprint._atomic_parquet` reads the source inside `with pq.ParquetFile(...)` and replaces after it is closed. | d77fa7e7 | (pending) |
| 6b | windows 3.14: `tests/io/test_hunt2_io.py` (failed write leaves `.shape-*.tmp`), `test_bugs_builtins_file_sink_atomic.py::test_failed_write_leaves_no_temp_file` | Product: the CSV/TSV/IPC writers were handed a path; their `close()` does not close the file pyarrow opened, which stays open until garbage collection, and a raised error keeps it alive (the traceback holds the writer), so `replace_atomically` could not unlink the temp file on Windows. | `_FileSink._write_file` opens the file itself (`pa.OSFile`) and closes it in `finally` after the writer. Linux output byte-identical: `shape generate retail --scale small --seed 3` in csv, tsv, parquet, jsonl and ipc before and after the change: `diff -r` identical. | d77fa7e7 | (pending) |
| 6c | windows 3.14: `tests/regressions/test_aud_cli.py::test_a_closed_pipe_is_not_an_error`, `tests/streaming/test_hunt2_regressions.py::test_696_emit_to_a_closed_pipe_stops_quietly` (`OSError: [Errno 22]` at `cli/main.py` `sys.stdout.flush()`) | Product: on Windows a write or flush to a pipe whose reader has gone raises `OSError` EINVAL, not `BrokenPipeError`, so the closed-pipe handling (exit quietly) did not apply. | `cli.errors.reader_gone(exc)`: `BrokenPipeError`, or on Windows an EINVAL `OSError` that names no file (an EINVAL naming a file is a bad path and stays an error). Used by the final flush in `main`, by `guarded`, and by the emit stdout sink. Linux behaviour unchanged (the Windows branch is `sys.platform == "win32"` only). | d77fa7e7 | (pending) |
| 6d | windows 3.14: `tests/streaming/test_file_source.py::test_a_file_is_a_stream_source_by_path_and_by_file_uri` (`no such file: '//C:\\...'`) | Product: `file://C:\path` (a drive path written straight after `file://`) was parsed as a UNC host `C:\path`. | `plugins.schemes.file_uri_path` (Windows branch): a host that is a drive letter (`C:` / `C|`) is the drive. Tests added to `tests/plugins/test_file_uri_paths.py` (`file://C:/data/x.csv`, `file://C:\data\x.csv`, `file://c|/data/x.csv`), platform independent. | d77fa7e7 | (pending) |
| 6e | windows 3.14: `tests/bridge/test_generate.py` (file lists, `0o666 == 0o600`), `test_jobs.py` (`0o600`), `test_uncovered_paths.py::test_pid_alive`, `test_project_1_1.py` (`/abs/data`) | Test: file names split on `/` from native paths. Product: the bridge's job records and spilled results relied on the profile directory's ACL on Windows, which ignores `chmod`. Test: `test_pid_alive` exercises the POSIX `os.kill` mapping, which Windows does not use. Test: `/abs/data` is not absolute on Windows (Python 3.13+ `isabs`). | Tests use `Path(p).name`; the bridge restricts its job directory and each job/result file to the current user with `icacls` on Windows (`restrict_on_windows`, the scale job store's `restrict_to_current_user`, round 4), and the tests assert privacy per platform (`bridge_helpers.assert_private`: mode on POSIX, a one-entry ACL naming the user on Windows); `test_pid_alive` sets `sys.platform` to `linux` for the POSIX branch (as `test_pid_alive_windows.py` sets `win32`); the project test uses an absolute `tmp_path` path. | d77fa7e7 | (pending) |
| 6f | windows 3.14: `tests/plugins/test_trust.py` (4: "INSTALLER was changed"), `test_hunt2_plugins.py` (1 error), `tests/security/test_aud_privacy.py` (11 errors) | Test: the fake installer wrote `INSTALLER` with `write_text`, which is `pip\r\n` on Windows while RECORD hashes `pip\n` (pip writes bytes). Test: the 14 errors are `ValueError: the environment variable is longer than 32767 characters`: pytest puts the test id in `PYTEST_CURRENT_TEST`, and these parametrize ids were the 100-400 KB inputs. | `write_bytes(b"pip\n")`; short `ids=` for the three adversarial parametrizations (inputs unchanged). | d77fa7e7 | (pending) |
| 6g | windows 3.14: `tests/demo_cmd/test_notebook_and_outputs.py:116`, `tests/quality/test_report_card.py::test_outputs_are_chosen_by_extension`, `tests/benchmarks/test_ddl_import.py` (2: `—` vs `�`) | Test/harness: UTF-8 files (written by the product with `encoding="utf-8"`) and the CLI's stdout (reconfigured to UTF-8 by `_utf8_streams`) were read with the Windows locale code page. | `read_text(encoding="utf-8")`; `ddl_1to1/verify.py` runs `shape from-ddl` with `encoding="utf-8"`. | d77fa7e7 | (pending) |
| 6h | windows 3.14: `tests/chaos/test_input_check.py` (2), `tests/cli/test_dry_run.py` (2), `tests/project/test_baseline.py::test_empty_registry_is_a_clear_error`, `tests/io/test_hunt2_io.py` (2 directory reads, 1 symlink mode), `tests/w5_10/test_share_bundle.py[data\\evil.parquet]` | Tests built expected paths with `/` where the product reports native paths; the symlink test asserted a POSIX mode Windows cannot store. `share_bundle`: on Windows `zipfile` turns `\` into `/` both when the test wrote the archive and in `ZipInfo.filename` when the product read it, so the product's "no backslash" rule never saw one. | Tests compare with native paths (`src / "orders.csv"`, `str(Path(...))`, `as_posix()` on relative listings, regex `shapes[/\\]registry`); the symlink test records the mode the platform stored and asserts it is kept (0o640 on POSIX, as before). Product: `share_bundle._check_names` also refuses a member whose stored name (`orig_filename`) holds a backslash (same message as on Linux); the test writes the raw name. | d77fa7e7 | (pending) |
| 6i | windows 3.14: `tests/consumers/test_contract.py::test_unreadable_files_are_exit_2` ("Permission denied" for a folder), `tests/integrations/test_adf_gate_errors.py::test_a_missing_shape_command_is_an_error_gate`, `tests/profile/test_audit_profile.py::test_file_urls_and_home_paths_are_read`, `tests/cli/test_bugs_cli_conformance_quiet.py`, `tests/plugins/test_scaffold.py` (3) | Product: reading a folder raises `PermissionError` on Windows, so `consumers.load` said "Permission denied", not "is a folder". Product (ADF gate): Windows' `FileNotFoundError` for a missing program does not name it. Tests: `expanduser` reads `USERPROFILE` on Windows; the console script is in `Scripts\shape.exe`, not next to `python.exe`; `pip --python` needs `python.exe`. | `consumers.load` checks `is_dir()` first; `run_gate._shape` sets the missing program as the error's file name when the OS gave none (Linux message unchanged) and decodes the child's output as UTF-8; tests set `USERPROFILE`, use `sysconfig.get_path("scripts")`, and `Scripts/python.exe`. | d77fa7e7 | (pending) |
| 6j | masking: `test_masking_api.py::test_key_file_readable_by_others_is_refused`, `test_mask_keyed_cli.py::test_bad_keys_exit_2` (loose-key part) | The key-file check reads POSIX mode bits by design (`masking/_core.py`: `os.name == "posix"`); `chmod(0o644)` sets nothing on Windows. | The repo's convention for mode-bit checks (`tests/security/test_credential_refs.py`, `tests/artifact/test_aud_privacy.py`): `posix_only`. The loose-key case moved to its own test `test_a_key_file_readable_by_others_exits_2` so the short-key and missing-env cases of `test_bad_keys_exit_2` still run on Windows. **For the lead:** this is the one place a check runs on fewer platforms than before; the product has no Windows ACL check for key files (adding one would refuse keys in a normal profile Temp folder, whose ACL also lists SYSTEM and Administrators). | d77fa7e7 | (pending) |
| 7 | macOS: `tests/kernel/test_gen_kernel.py::test_oversized_calls_are_value_errors_not_aborts` (`ValueError: current limit exceeds maximum limit`); Windows: same test (no `resource`) | Darwin refuses an RLIMIT_AS below the address space the process already reserves (and does not enforce RLIMIT_AS); Windows has no `resource`. | The child sets the 4 GiB cap where it can and raises if that fails on Linux; on macOS/Windows it runs without it. The assertions (every oversized call is a `ValueError` naming chunks/width, no abort) are unchanged. | c8dcead5 | (pending) |
| 8a | zero-network: `tests/packaging/test_pure_wheel_metadata.py` (13), `tests/release/test_packaging.py` (3) | Not network: the job runs pytest as root (`sudo ... unshare --net`), and root has no rustup default toolchain, so `maturin sdist` failed at `cargo metadata` ("rustup could not choose a version of cargo to run"). Offline `cargo metadata` also needs the locked crates in the cargo cache. | `ci.yml` zero-network: a `cargo fetch --locked` step before the namespace, and the test step passes `RUSTUP_HOME`, `CARGO_HOME` (the runner's) and `CARGO_NET_OFFLINE=true` through `sudo env`. Checked locally: `maturin sdist` inside `unshare --net` succeeds with a populated cargo cache. The tests still run in the job, offline. | bce677fb | (pending) |
| 8b | zero-network: `tests/integrations/test_fabric_spark.py` (14 errors) | Spark looks up the host's canonical name and network interfaces at start (`Utils.localCanonicalHostName`): in the empty namespace that fails (reproduced locally under `unshare --net`: "No network interfaces configured"). | The `spark` fixture sets `SPARK_LOCAL_IP=127.0.0.1` and `SPARK_LOCAL_HOSTNAME=localhost` (local[2] runs in-process; loopback is all it uses). The 14 tests pass normally with them (14 passed here). This sandbox's own namespace has no loopback address, so the namespace run itself is confirmed by CI only. | bce677fb | (pending) |
| 9 | ubuntu 3.11: `tests/profile/test_audit_profile.py::test_a_long_text_value_is_tokenized_in_linear_time` (`5.10 < 5.0`) | Product: the dateutil tokenizer grew each token one character at a time (`token += c`). For one 400 KB token on a cold heap every growth is a reallocation (glibc `mremap`): first call 2.75 s, a repeat 0.5 s; under `--cov` on 3.11 4.8 to 5.1 s against the 5.0 s bound. | `_timelex.get_token` collects the characters in a list and joins once. Same tokens: old and new tokenizer compared on 200,010 inputs (fixed cases and random strings over digits, letters, `.,:-/T+`, NUL, tab), all identical. Cold 400 KB: 0.14 s; the test under `--cov` 2.7 to 2.8 s. The test and its bound are unchanged. | da4a37cc | (pending) |

### Item 4 evidence (#768 class, left for W8-04b)

* CI (ubuntu-latest, all Pythons, numpy resolved to the latest 2.x): healthcare `sha256:743b78ae…`, hr
  `sha256:fb092965…` for both kernels; recorded `df46193f…` and `68c1d3ec…`; retail matches.
* Builder VM (Intel Xeon with AVX-512, numpy 2.4.6): all six pass. With
  `NPY_DISABLE_CPU_FEATURES="X86_V4"` (numpy's AVX-512 dispatch off) the same four fail with **exactly
  the CI hashes** (`743b78ae…`, `fb092965…`).
* numpy 2.3.0 (the min-versions job's pin) on the builder VM: the recorded hashes with and without
  `X86_V4` disabled. But the min-versions job itself passed these tests on run 37313323165 and
  failed them (same `743b78ae…`/`fb092965…`) on run 37339176931, so that local probe does not
  reproduce every CPU difference of the hosted runners; the numpy version is not the explanation.
* The same commit passes or fails these tests depending on the runner it lands on: in run
  37328511049 ubuntu 3.11 passed and 3.12/3.14 failed; in run 37339176931 ubuntu 3.12, 3.14 and
  zero-network passed and min-versions failed.
* macOS (arm64) and Windows give a third value (healthcare `500dfda1…` on both), and the vault
  digests in 4b differ there in the same way while Linux x86 passes with and without AVX-512.
* This is the CPU/platform-dependent float path of #768; making generators bit-identical across CPUs
  is lane W8-04b. No recorded hash was changed here.

### First CI run on lane/CI-FIX: run 37328511049 (CI) and 37328511107 (Security) on f335b484

f335b484 = the commits above + merge of `origin/lane/CI-FIX` 3aa18366 (recorded with `-s ours`: every
change on that line is already in build/main-plan, which has since advanced each file it touched;
checked file by file) + merge of `origin/build/main-plan` 7fedc4d9 (P8-02).

Green: Security (item 2: job 37328511107 success), build, rust, audit, offline-lock, pure-wheel,
fabric-demo, bench-quick, vscode-extension, plugin-skeletons (3 OS), database-plugins (2 OS).

* Item 1: confirmed: test (windows-latest, 3.11) job 111825290176, step "Run mypy": success (ruff,
  vulture, lint-imports and the check scripts passed there too).
* Item 3: no `sqllocks_refengine` failure on any leg (min-versions, zero-network, ubuntu, macOS, Windows).
* Item 5: stream-plugins (windows) 38 → 14 failures, all of them new product causes (6k, 6l below);
  test (windows, 3.14) timecapsule, corpus, README, locales, name pools, frozen v1 files: all pass.
* Item 6: test (windows, 3.14) 95 failed + 14 errors → 10 failed: 4 W8-06 + 2 vault (item 4,
  escalated), docs_site (new, below), 3 fixed in the next push (6m, 6n).
* Item 7: macOS 3.11 and 3.14 pass the oversized-call test; Windows too.
* Item 8: zero-network: packaging (16) and Spark (14) pass; left: W8-06 (item 4) and docs_site.
* Item 9: the dateutil test passes on ubuntu 3.11.
* Item 4, more evidence: on this run ubuntu **3.11 passed** the W8-06 domain hashes while 3.12,
  3.13 (where it ran) and 3.14 failed them with the same code: the hosted ubuntu runners differ in
  CPU, and the result follows the CPU.

### Round 6, second push: what the first run found

| # | failure (run 37328511049) | root cause | fix |
|---|---|---|---|
| 6k | stream-plugins (windows): `plugins/shape-dbt/tests/test_seeds.py` (11), `test_skeleton.py::test_the_sink_conforms`: `PermissionError [WinError 32]` replacing `seeds/.x-*.tmp` | Product (shape-dbt): same as 6b: `pacsv.CSVWriter(str(tmp))` leaves the file open after `close()`. | `seeds.py` opens the temp file as `pa.OSFile` and closes it after the writer. 108 shape-dbt tests pass. |
| 6l | stream-plugins (windows): `test_fhir_sink_emitter.py::test_ndjson_several_tables_share_a_directory_and_utf8`, `test_ncpdp_sink.py::test_option_overrides_and_response` | Product (shape-healthcare-standards): `common.output_path` and `sources._path` read `file://` URIs with `urlparse(...).path`, which drops a Windows drive. | Both use `shape.plugins.schemes.local_path` (public plugin API; the reading fixed in 6d). Linux results identical. 632 healthcare/dbt tests pass. |
| 6m | windows 3.14: `tests/bridge/test_jobs.py`, `test_generate.py` (privacy) | Test (mine, 6e): on Python 3.13+ `mkdir(mode=0o700)` gives a directory a protected ACL of the user, SYSTEM, Administrators and OWNER RIGHTS (CPython's own owner-only ACL), which files created in it carry; the helper wanted exactly one entry. | `assert_private` on Windows: the current user must be there and no principal other than the user, SYSTEM, Administrators or OWNER RIGHTS may be (no Users, Everyone, Authenticated Users or other account). |
| 6n | windows 3.14: `tests/bridge/test_cli.py::test_a_job_killed_with_its_process_reads_as_interrupted` (`signal.SIGKILL`) | Test: POSIX signal. | `proc.kill()` (SIGKILL on POSIX, `TerminateProcess` on Windows), as the emit tests (round 2). |
| 6o | ubuntu 3.12: `tests/cli/test_w8_06_identifiers_cli.py::test_generate_refuses_an_unknown_value` (3) | Test: argparse on Python 3.12.14 prints `choose from reserved, realistic`, other versions quote the choices. | The assertion accepts either form (regex); the refusal and exit 2 are asserted as before. |
| 6p | macOS 3.11: `tests/bridge/test_scale.py::test_a_running_scale_job_can_be_cancelled` (`600000 < 600000`) | Test: the cancel is accepted between chunks; on the fast macOS runner the first large table (`order`, 600k rows) was already complete when it landed and `order_line` was being written. The test assumed it would land inside `order`. | It asserts that the run stopped before its end (rows of `order` + `order_line` < 1,200,000), with the cancel accepted and the status `cancelled` as before. **For the lead:** the assertion no longer says which table the cancel lands in. |

### Escalated: `tests/docs_site/test_docs_site.py::test_every_site_page_is_in_the_nav_exactly_once`

Red on every test leg, min-versions and zero-network since build/main-plan 7fedc4d9 (P8-02 merge,
after the brief's diagnosis). P8-02 committed `docs/PERFORMANCE.md` (written by
`scripts/gen_performance_page.py` from `benchmarks/vs_refengine/results.json`). The docs site (P8-03)
requires every `docs/*.md` outside `plans/` and `talks/` to be in the mkdocs nav exactly once, and
already builds its own performance page, `reference/performance.md`, from the same `results.json`
at build time (`scripts/mkdocs_hooks.py`). P8-02's status calls publishing to the docs site a lead
decision. Options (I did none):

1. Keep one page: drop the committed `docs/PERFORMANCE.md` and point `gen_performance_page.py`'s
   default output outside `docs/` (the nightly already writes to `bench-out/parity/`); the site keeps
   `reference/performance.md`.
2. Publish both: add `PERFORMANCE.md` to the nav (two performance pages on the site).
3. Keep the file but not on the site: list it in mkdocs `exclude_docs` and have the test's
   `_site_sources` honour the same exclusion.

### Second CI run on lane/CI-FIX: run 37339176931 (CI) and 37339176757 (Security) on 1af8057a

Green: Security, build, rust, audit, offline-lock, pure-wheel, fabric-demo, bench-quick,
vscode-extension, plugin-skeletons (3 OS), database-plugins (2 OS), **stream-plugins (ubuntu and
windows)** (items 5, 6k, 6l), Wheels (run 37328511117 on f335b484; this push did not trigger it).

Every other finished job fails only on the two escalated items:

| job | failures | escalated item |
|---|---|---|
| test (windows, 3.14) 111862044381 | docs_site; W8-06 ×4; vault ×2 (17204 passed) | docs_site; item 4 |
| test (macos, 3.14) 111862044032 | docs_site; W8-06 ×4; vault ×2 | docs_site; item 4 |
| test (ubuntu, 3.12) 111862044195 | docs_site only (W8-06 passed on this runner) | docs_site |
| test (ubuntu, 3.14) 111862044679 | docs_site only (W8-06 passed on this runner) | docs_site |
| zero-network 111862044130 | docs_site only (items 8a, 8b green; W8-06 passed here) | docs_site |
| min-versions 111862044676 | docs_site; W8-06 ×4 | docs_site; item 4 |

Legs finished later in the same run: ubuntu 3.11 111862044253 docs_site only; ubuntu 3.13
111862044140 docs_site + W8-06 ×4; macOS 3.11 111862044148 docs_site + W8-06 ×4 + vault ×2;
Windows 3.11 111862044139 the same as Windows 3.14 plus one new failure, fixed in the next push:

| # | failure | root cause | fix |
|---|---|---|---|
| 6q | windows 3.11: `tests/integrations/test_fabric_spark.py::test_executors_on_the_python_kernel_give_the_same_profile` (`JSONDecodeError` on `SUCCESS: The process with PID … has been terminated.`) | Test: on Windows the JVM's shutdown (`taskkill`) writes to the child's standard output. First fix (7ea6b12f, parse the last line starting with `{`) was not enough: run 37350222614 showed the message glued to the end of the JSON line, because the child's 27 KB result was still in its stdout buffer when the shutdown wrote. | The child prints its result with `flush=True` (whole, before shutdown), and the test reads only the JSON object at the start of that line (`raw_decode`). Passes here. |

So items 1, 2, 3, 5, 6 (a–q), 7, 8 and 9 are green on lane/CI-FIX; nothing red remains but the
docs_site nav test (P8-02 × P8-03, needs the lead's choice) and the CPU-dependent W8-06/vault
digests (W8-04b).

### Third CI run on lane/CI-FIX: run 37350222614 on 7ea6b12f

Every job finished; the only failures are the two escalated items (docs_site everywhere; W8-06 on
min-versions, zero-network, ubuntu 3.14, Windows and macOS; vault on Windows and macOS), plus 6q
on Windows 3.11, whose first fix was incomplete (see 6q). ubuntu 3.11 and 3.13 failed docs_site only.
Green as before: build, rust, audit, offline-lock, pure-wheel, fabric-demo, bench-quick,
vscode-extension, plugin-skeletons (3 OS), database-plugins (2 OS), stream-plugins (2 OS).

### Fourth CI run on lane/CI-FIX: run 37364616729 on 166ac09c

Most jobs of this run were cancelled between 19:52 and 19:57 UTC, nearly all before their tests
ran; no push was made from this lane in that window (not by this session). The jobs that completed:

* **test (windows-latest, 3.11) 111946717448: 6q fixed** (the Spark python-kernel test passes);
  failures: docs_site, W8-06 ×4, vault ×2 only (17204 passed). mypy and every check step green.
* test (ubuntu-latest, 3.12) 111946717345: docs_site only.
* min-versions 111946717076: docs_site, W8-06 ×4.
* Green: audit, rust, offline-lock, vscode-extension, fabric-demo, stream-plugins (ubuntu),
  plugin-skeletons (ubuntu, macOS).

## Round 6: final state

Every item of the brief is fixed and confirmed on lane/CI-FIX (runs 37328511049, 37339176931,
37350222614, 37364616729; Security 37339176757; Wheels 37328511117), except the escalated:

1. **docs_site** `test_every_site_page_is_in_the_nav_exactly_once` (P8-02 `docs/PERFORMANCE.md` vs
   P8-03's nav rule): needs the lead's choice of the three options above. Red on every test leg,
   min-versions and zero-network until then.
2. **Item 4** (W8-06 domain hashes, vault digests): CPU/platform-dependent float path (#768),
   lane W8-04b. Red on macOS and Windows every run, and on whichever ubuntu legs land on a runner
   without the CPU path the recorded values came from.

For the lead's review: the masking key-file checks now `posix_only` (6j) and the scale-cancel test
asserting that the run stopped early instead of which table it stopped in (6p).
