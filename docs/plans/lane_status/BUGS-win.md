# BUGS-win — Windows failures of the INT-17/INT-18 tests — status

Branch `lane/BUGS-win`, from `int/INT-18` at b5bddeaa. Issues: #770 (bridge vectors, bench
scripts, chaos, kerberos, behavior scale) and #769 (DuckDB URI of an absolute Windows path).
Evidence: CI run 37236438299 (`int/INT-18` at fcbdb1a5); baseline CI run 37229627967
(`build/main-plan` at bb536f4a).

No D-xx/T-xx decision, gate or tolerance touched; §11 and §2.3 unedited; nothing escalated;
`$REFENGINE_ROOT` untouched; no workflow edited; no test skipped, xfailed or loosened to hide a
bug (one documented POSIX-only feature is marked, below).

## Causes and fixes

| Failure (Windows) | Cause | Fix |
|---|---|---|
| `tests/bridge/test_compat_1_0.py`, `test_compat_1_1.py`: `$.result.files`, `$.error.message`, `$.result.project.file`, ... | The vectors write a path under `${DIR}` with `/`; the bridge answers with the platform's paths (`C:\...\work\shape.yml`), as it does on POSIX. The runner substituted `${DIR}` only. | Test: `vectors_lib.substitute` (and its inverse `abstract`) put the platform separator in a path written under `${DIR}`; the 1.0 and 1.1 replays use it. The documented contract (`vectors_lib` docstring) now says so. No vector regenerated. New test simulates Windows (`sep="\\"`). |
| `[preview]` (1.0 and 1.1): `city: expected 'Memphis', got 'Memphis\r'` | **Product bug.** The reference pools (`src/shape/builtins/strategies/pools/*.txt`) are split on `\n` from their bytes; a Windows checkout (`core.autocrlf`) gives CRLF files, so every pool value kept a `\r`. Reading them as text, as before, had dropped it. | `providers._lines` treats `\r\n` as a line end. Reproduced on Linux by converting `us_cities.txt` to CRLF: the 1.0 preview gave `['Memphis\r', 'Cincinnati\r']` before, `['Memphis', 'Cincinnati']` after. New test: every shipped pool with CRLF endings equals the LF pool. The `_plain` reference in `test_provider_pools.py` now uses the same CRLF rule (it had pinned the `\r`). |
| `tests/bridge/test_chaos_1_2.py::test_chaos_on_a_generated_folder_gives_what_the_cli_prints` | Test: it replaced `str(root)` in the JSON text, where Windows paths are escaped (`C:\\Users`). | Replace the root as JSON writes it. |
| `tests/benchmarks/test_bench_scripts.py::test_peak_rss_is_megabytes_on_every_platform[...]` (`No module named 'resource'`) | The bench scripts imported `resource` (POSIX only) at module level. | `benchmarks/vs_refengine/common.py`: `rusage()` (`resource` on POSIX; `os.times()` and `GetProcessMemoryInfo` via ctypes on Windows; children not measured there: NaN). `generate.py`, both `bench_cli.py`, both stream workers and `measure_product.py` use it. Tests: the Linux/macOS units with a fake `resource`, a simulated Windows run, and every script loads with `resource` absent. |
| `test_live_fidelity_overhead_is_timed_under_the_benchmark_lock` (`{None} == {'1'}`) | `bench_lock()` held no lock at all on Windows (§1.4: timed benchmarks hold the exclusive lock). | `bench_lock()` takes `msvcrt.locking` on Windows (waiting while it is held), `flock` elsewhere. New test simulates Windows with a fake `msvcrt`. |
| `plugins/shape-fabric/tests/test_kerberos.py` (15) | The fake `kinit` is a shebang script, not found by `shutil.which` on Windows, so the runner's real Java `kinit` ran; and on Windows the product (correctly) refuses `--keytab`. | Product: `kerberos.find_kinit()` is the one `kinit` lookup; the fixture replaces it, so no test can reach a real `kinit` on `PATH` (also on POSIX). `test_kerberos_needs_a_keytab_and_a_principal_off_windows` simulates the platform (`sys.platform = "linux"`) and runs everywhere. The 15 tests that sign in with a keytab or read POSIX mode bits are `posix_only` (see below). |
| `plugins/shape-behavior/tests/test_primitives_scale.py::test_ten_thousand_devices_hourly_for_a_year_in_bounded_memory` | Test: its Windows branch imported `psutil`, which is not installed. | The Windows peak working set comes from `shape.scale.router._windows_peak_working_set_bytes` (ctypes). Bound unchanged (1500 MB). |
| #769 `plugins/shape-databases/tests/test_duckdb.py::test_an_absolute_path_has_four_slashes_and_a_relative_one_three` | Test: its `uri()` helper pasted `C:\...` after `duckdb:///` and asserted four slashes. The sink already documents and accepts `duckdb:///C:/data/out.duckdb`. | `uri()` builds the documented form (`duckdb:///` + `as_posix()`); the test asserts four slashes for a POSIX absolute path, `duckdb:///X:/` for a Windows one, and that the file read back is the same file. New platform-independent test: `duckdb:////var/...`, `duckdb:///C:/...` and a relative URI parse to the right paths (absolute on POSIX / Windows respectively). |

### The one platform mark

`test_kerberos.py` marks 15 tests `posix_only` (`skipif(os.name != "posix")`). Keytab sign-in is
documented POSIX only: `shape_fabric/kerberos.py` ("On Windows there is no ``kinit`` and no
keytab: the connection uses the signed-in account ... ``--keytab`` is an error") and
`shape_fabric/auth.py` (`_kerberos_options` refuses `--keytab`/`--principal` on `win32` and
never constructs a `KerberosSession`). These tests run the keytab flow (a fake POSIX `kinit`
script) or check POSIX mode bits (0o700 cache directory, 0o600 keytab; `credrefs` checks modes
on POSIX only). The Windows behaviour keeps its own tests, which run on every platform
(`test_keytab_on_windows_is_refused_and_the_signed_in_account_is_used`,
`test_keytab_on_windows_is_exit_2`), as do the refusals that never reach `kinit`.

## Verification

Linux, this session (`TMPDIR` private):
- `ruff check src tests plugins benchmarks/vs_refengine benchmarks/measure_product.py`, `ruff
  format --check` (same paths), `mypy` (636 files), `python scripts/check_user_facing.py`: clean.
- `pytest -m "not emulator and not live" tests/bridge tests/generation/test_provider_pools.py
  tests/benchmarks plugins/shape-fabric/tests/test_kerberos.py plugins/shape-databases/tests`:
  1794 passed (default kernel) and 1794 passed with `SHAPE_KERNEL=python`;
  `tests/benchmarks` 212 passed after the second and third commits.
- `plugins/shape-behavior/tests/test_primitives_scale.py` (heavy): passed, peak 820 MB.
- Tests first: with the old `benchmarks/` and `kerberos.py`, the new bench tests (5) fail and
  the kerberos fixture errors (no `find_kinit`); the CRLF preview was reproduced (above).

Windows CI (`gh api .../ci.yml/dispatches -f ref=lane/BUGS-win`):
- Run 37247494569 on d40c0383: `database-plugins (windows-latest)` **success** (#769);
  `stream-plugins (windows-latest)`: 14 failed, exactly the 14 that fail on bb536f4a (kerberos
  and behavior fixed); `test (windows-latest, 3.11/3.14)`: 8 failed, the 7 that fail on bb536f4a
  plus my new `test_the_scripts_load_where_there_is_no_resource_module` (it took another
  folder's cached `compare` module, depending on test order; reproduced locally and fixed in
  c15b10ff). All compat 1.0/1.1, chaos and bench failures of #770 are gone.
- Run 37248359008 on c15b10ff: `database-plugins (windows-latest)` success; `stream-plugins
  (windows-latest)` the same 14 as bb536f4a; the Windows test legs the 7 of bb536f4a plus my new
  test again, now `UnicodeDecodeError` (`read_text()` is cp1252 on Windows; fixed in a8a226b6,
  checked locally with `LC_ALL=C python -X utf8=0`). Linux and macOS legs compared job by job
  with run 37236438299 (fcbdb1a5): no failure added by this lane (see "Not this lane" below).
- Run 37251224349 on a8a226b6: `test (windows-latest, 3.11)` and `(3.14)`: **7 failed, 882
  passed**, exactly the 7 that fail on bb536f4a; `stream-plugins (windows-latest)`: **14
  failed, 1861 passed**, exactly the 14 that fail on bb536f4a; `plugin-skeletons
  (windows-latest)` success. `database-plugins (windows-latest)` was cancelled by the matrix
  (fail-fast) when `database-plugins (ubuntu-latest)` hit an unrelated flake (below); it passed
  on the two runs before, and a8a226b6 changed only `tests/benchmarks/test_bench_scripts.py`.

So no Windows failure introduced by INT-17/INT-18 is left in the Windows test legs or the
stream-plugins/database-plugins Windows jobs.

## Out of scope: failures that also happen on bb536f4a (CI run 37229627967)

`test (windows-latest, 3.11/3.14)`:
- `tests/bridge/test_cli.py::test_a_job_killed_with_its_process_reads_as_interrupted`
  (`signal.SIGKILL` does not exist on Windows).
- `tests/bridge/test_generate.py::test_generate_writes_the_files_the_cli_writes[csv|parquet|jsonl|tsv]`
  (absolute `C:\...` paths compared with names).
- `tests/bridge/test_generate.py::test_a_large_preview_part_is_returned_as_a_file` and
  `tests/bridge/test_jobs.py::test_a_job_is_a_versioned_file_under_the_jobs_directory`
  (`0o666 == 0o600`: POSIX mode bits).
- The job still stops with a `KeyboardInterrupt` right after `tests/bridge/test_jobs.py`, at about
  6% of the suite, as on bb536f4a. **Every test after that point never runs on Windows**, so this
  lane could not confirm on Windows the tests collected later (among them
  `tests/bridge/test_vectors.py`, which uses the same separator-aware `substitute`, and
  `tests/generation/test_provider_pools.py`). Worth its own issue.

`stream-plugins (windows-latest)`:
- `plugins/shape-dbt/tests/test_seeds.py` (11) and `test_skeleton.py::test_the_sink_conforms`
  (`WinError 32`, file still open).
- `plugins/shape-healthcare-standards/tests/fhir/test_fhir_sink_emitter.py::test_ndjson_several_tables_share_a_directory_and_utf8`
  and `tests/ncpdp/test_ncpdp_sink.py::test_option_overrides_and_response`
  (`file://C:\...` paths).

## Not this lane (seen on the lane's CI runs, not Windows, not caused here)

- `plugins/shape-databases/tests/test_snowflake.py::test_values_are_never_part_of_a_statement`
  (run 37251224349, ubuntu): the random stage folder name was `..._sn1_/`, which contains the
  value `n1` the test looks for. A flake of the test's check (it searches the whole statement
  text, the generated temporary path included); worth an issue.
- `tests/generation/test_w1_15_pinned_fixtures.py` distribution-* dataset ids: fail on one
  Linux Python leg per run (3.14 on fcbdb1a5, 3.13 on c15b10ff), already on fcbdb1a5; no
  generation code touched here (the pool change is a no-op on LF input).
- `tests/bridge/test_scale.py::test_a_running_scale_job_can_be_cancelled` (macOS 3.11, run
  37248359008): the job finished all 600,000 rows before the cancel arrived (timing).

## Commits

- d40c0383 `BUGS-win: Windows failures of the INT-17/INT-18 tests (...)` (Fixes #769, #770)
- c15b10ff `BUGS-win: the no-resource load test does not take another folder's compare module`
- 3f98a341 `BUGS-win: lane status`
- a8a226b6 `BUGS-win: the no-resource load test reads the scripts as UTF-8`
- this file's update
