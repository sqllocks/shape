# AUD-portability: cross-platform correctness

Lane branch `lane/AUD-portability` (from `build/main-plan` 5c91ea5, which carries INT-15). Audit
area: path handling, text encodings, line endings, locking and atomic replace, signals and
subprocesses, temp directories, time zones, POSIX-only calls, across `src/` and `plugins/*/src`.
Fixes allowed only for plain occurrences (encoding, `newline="\n"`, pathlib) in
`src/shape/io/`, `src/shape/report/`, `src/shape/registry/` and `plugins/*/src`; everything else is
filed only.

## Method

- Static search of `src/` and `plugins/*/src` for: `open(`, `.open(`, `read_text`, `write_text`,
  `os.fdopen`, `TextIOWrapper`, `csv.*`, `tempfile.*`, `os.replace`/`rename`, `fcntl`/`msvcrt`,
  `signal.*`, `os.kill`, `subprocess`, `resource`, `os.chmod`/`st_mode`, `urlparse`/`urlsplit` of
  file URIs, `split("/")`, `relative_to`/`resolve`, `expanduser`, `datetime.now()`/`localtime`,
  `zoneinfo`, `locale`, `sys.stdout.write`, case comparisons.
- Windows simulated on Linux: `pathlib.PureWindowsPath` for path joins and drives; a
  `windows_text_io` pytest fixture (`os.linesep = "\r\n"`, `io.open`/`open` = `_pyio.open`, locale
  encoding cp1252) for text files; `PYTHONIOENCODING=cp1252` for a redirected Windows stdout.

## Findings

| # | Sev | Where | Reproduction | Expected | Actual | Issue | Status |
|---|---|---|---|---|---|---|---|
| 1 | high | `src/shape/bridge/jobs.py:72` `pid_alive` | `os.kill(pid, 0)` on Windows: `0 == CTRL_C_EVENT` → `GenerateConsoleCtrlEvent` | side-effect-free liveness probe | Ctrl+C sent to the other bridge's process group, or `OSError` → job marked `interrupted` | #239 | filed (bridge area) |
| 2 | high | CLI stdout (`shape cat`, the git textconv) | `PYTHONIOENCODING=cp1252 shape cat u.shape > out` with `Łukasz` in the data | exit 0, UTF-8 output | exit 2, `'charmap' codec can't encode character '\u0141'` | #240 | filed (cli area) |
| 3 | medium | `registry/local.py`, `registry/profiles.py`, plugin writers (fabric notebook/.bim/tapes, simulation stats/manifests, sqlserver `--json`) | `windows_text_io`: log `…}\r\n`, notebook `{\r\n …` | identical bytes on every OS | CRLF, locale encoding, `\` in `_index.json` paths | #237 | **fixed** |
| 4 | medium | `plugins/schemes.py:61` `local_path`, `builtins/sources/files.py:15` | `PureWindowsPath(str(local_path("file:///C:/data/x.csv"))).drive == ""`; UNC host dropped | `C:\data\x.csv`, `\\server\share\x.csv` | `\C:\data\x.csv`, `\share\x.csv` | #241 | filed (plugins/builtins area) |
| 5 | medium | `generation/schema.py:335`, `registry/local.py`, `registry/profiles.py` | tables `Orders` and `orders`; registry names `Sales`/`sales` | rejected as a collision | one silently overwrites the other on NTFS/APFS | #242 | filed (registry part needs a new check, not a plain fix) |
| 6 | medium | `security/names.py:14` `is_safe_name`; `io/store.py:108` `_clean` | `render_path("{table}.{ext}", "orders:evil", …)` → ADS; `CON`, trailing dot; `_clean("C:/x.parquet")` accepted, `PureWindowsPath("D:/out") / "C:/x.parquet"` = `C:\x.parquet` | refused | accepted | #243 | filed (a validation change, not a plain fix) |
| 7 | low | core writers outside io/report/registry (list in the issue), `streaming/emit/contract.py` and `packs/domains.py` without encoding | `windows_text_io` | UTF-8, `\n` | CRLF / cp1252 | #238 | filed |
| 8 | low | every `os.replace` atomic writer | target held open by another process on Windows | bounded retry, clear error | `PermissionError` | #244 | filed |
| 9 | low | `cli/gitcmds.py:100` | `subprocess.run(..., text=True)` decodes git's UTF-8 output with the ANSI code page | UTF-8 | mojibake for non-ASCII repository paths | #240 (related, in the body) | filed |

Checked and found correct (no issue): `validation/fuzz.py` SIGALRM is guarded; `cli/emit.py`
adds `SIGBREAK` on Windows; `scale/router.py` imports `resource` lazily with a fallback;
`security/credrefs.py` skips mode bits off POSIX and universal newlines strip `\r`;
`streaming/file_source.py` handles `file:///C:/`; `io/landing.py` refuses drive and UNC templates;
`io/readers.py` reads a UTF-8 BOM header correctly (`['id', 'name']`); tz-aware code uses
`zoneinfo` with `tzdata` on Windows (T-07); every `tempfile` use is in a target directory or the
system temp dir. Observation, not filed: `quality/gates.py:478` freshness for zone-less columns uses
the local wall clock (`datetime.now()`), which is the documented reading of naive timestamps.

## Fixes (issue #237)

- `085cc05` regression tests (8 failing before the fix; failing output in the commit message):
  `tests/registry/test_registry_portability.py`, `plugins/shape-{simulation,sqlserver,fabric}/tests/test_portability.py`,
  `windows_text_io` fixtures in the simulation and fabric plugin conftests.
- `62e445f` `src/shape/registry/local.py`; `b7381a5` `src/shape/registry/profiles.py` (index
  `path` via `as_posix()`; its test cannot fail on Linux, it pins the Windows CI leg);
  `a78da39` notebook.py; `ab1f664` recording.py; `2ebe882` semantic_model.py; `762593c`
  `_patterns.py`; `ae7c912` file_drop.py; `a03f30e` scd2_file_drops.py; `0bb484c` sqlserver
  command.py. Output bytes on Linux are unchanged, so no equivalence verifier input changes.

## Left open (for the lead)

#238–#244 are outside this lane's fix scope (other areas own them, or the fix is a behaviour
change rather than a plain occurrence fix). No `.github/workflows` change is needed.

## Commands and results

Final run 2026-10-03 on `904c2ae` (`origin/build/main-plan` had not moved from `5c91ea5`, so no merge
was needed). Environment: Python 3.11.15, `~/.venvs/shape` with `pip install -e ".[dev,advanced]"`
and `-e` of all seven first-party plugins (`shape-domains`, `shape-fabric`, `shape-simulation`,
`shape-sqlserver`, `shape-databases`, `shape-eventhubs`, `shape-kafka`), maturin-built kernel, Rust
1.97.0.

| Command | Result |
|---|---|
| `make check` (every step: ruff check, ruff format --check, mypy, compileall, vulture, lint-imports, the six `scripts/check_*.py`, the coverage run, heavy, `SHAPE_KERNEL=python pytest tests/kernel`, cargo fmt/clippy/test), newest pyarrow | exit 0; 6792 passed (coverage 92.62% ≥ 86), heavy 42 passed, python kernel 265 passed, cargo 34 passed; mypy clean on 436 files |
| `python scripts/check_user_facing.py` | `check_user_facing: clean`, exit 0 |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | 4 failed, 7084 passed, 13 deselected (the marker expression) |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | the same 4 failed, 7084 passed, 13 deselected |

The full suite collects `tests/demo/fabric`, which needs `tests/demo/fabric/requirements.txt`
(`nbformat`, `pyspark`, `delta-spark`, `fabric-user-data-functions`, ...) and the system package
`unixodbc`. Those were installed so that nothing is left out; `fabric-user-data-functions` pins
`pyarrow<20`, so the two full runs used pyarrow 19.0.1 (inside T-07's `>=14.0.1`, the version CI's
`fabric-demo` job uses). `make check` ran first, on the newest pyarrow without the UDF SDK.

The 4 failures are not this lane's: each also fails on `origin/build/main-plan` (`5c91ea5`, a
worktree run with the same venv and `PYTHONPATH` set to the worktree's `src` and `plugins/*/src`),
none of them touches a file this lane changed, and every one is already filed:

- `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date`
  (pyarrow 19 `read_table` adds the hive column `ingest_date`), and
  `tests/kernel/test_hashing.py::test_rust_equals_reference_on_a_million_values[float16]`,
  `::test_one_and_one_point_zero_hash_equal` (pyarrow 19 has no float16 `if_else` kernel /
  `Expected np.float16 instance`): base run "3 failed, 1 passed"; filed as #333 (and #76).
- `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`:
  order-dependent; it passes alone on both, and fails on both when
  `tests/demo/fabric/test_udf.py` runs first (it imports `fabric.functions`, which imports `azure`):
  `pytest tests/demo/fabric/test_udf.py <that test>` gives "1 failed, 34 passed" on the lane and on
  base. Filed as #77, #554 and #558.

Not done here: emulator and live tests (CI nightly only, plan §1.3).
