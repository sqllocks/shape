# BUGS-demo-3: `shape diff` output (#308), faker (#309), the fabric plugin's extras (#310)

Branch `lane/BUGS-demo-3`, from `int/INT-18` at `c9bf35c`; `origin/int/INT-18` was merged in four
times as it moved, last at `b5bddea` (head after it: this commit's parent). The three issues were found by the 2026-10-03 rehearsal
(`docs/plans/lane_status/DEMO-REHEARSAL.md` on `lane/DEMO-REHEARSAL`). Commits carry
`Fixes #308`, `Fixes #309` and `Fixes #310`; the issues close when this reaches the default branch.
No version number was changed and nothing was published (#303 stays the owner's).

## #308: `shape diff` printed 1.1 MB; `--json FILE` printed it as well

**Reproduced** on the base with `demo/make_data.py`: `shape diff o1.shape o2.shape | wc -c` gave
542,917 bytes in this venv (rust kernel; the rehearsal saw 1,155,366 with the pure wheel). Almost
all of it was the `order_total` `new_categorical_values` change (541,344 bytes), whose `baseline`
and `current` list every value. `--json d.json` wrote the file and printed the same document.

**Fix** (`src/shape/cli/main.py`, `src/shape/cli/machine.py`), following `docs/CLI.md` ("where
`--json` takes a file it keeps that meaning, and `--json -` writes the document to standard
output"):

- When standard output is a terminal, a change's `baseline`/`current` list or mapping of more
  than 20 entries shows its first 20, with `values_omitted: {"baseline": n, "current": m}` on the
  change and one stderr line (`shape: 43,728 values left out of long lists (at most 20 per change
  on a terminal); --json FILE or a pipe gets them all`). Short lists are unchanged. The demo diff
  on a pseudo-terminal: **2,310 bytes**. A pipe or redirect still gets the complete result
  (542,917 bytes): the first version cut it there too, and the full suite then showed three tests
  that treat piped stdout as the machine result (the bridge's diff equals the CLI's, the project
  annotations, the capture diff); `shape diff ... | jq` would have lost data the same way.
- `--json FILE` writes the complete result (893,685 bytes for the demo) and prints nothing on
  stdout. The stderr summary (each change, the bump) is unchanged, and the diff's `notes` are now
  also printed there (`shape: note: ...`), since they no longer show on stdout under `--json FILE`.
- The `shape-result` document stays complete: `--json -` and the `ci.json` of `shape.yml`
  (`machine.run` sets `a.result_document`). The action (`action.yml`) uses `--json -`.
- The webhook notifier merged from INT-18 (W6-01) read its findings from stdout; it now gets the
  complete result from `a.result_payload`, so `--notify` with `--json FILE` still counts every
  change (a test fails without it).
- Capture diffs (`shape diff A.json B.json --json OUT`) also write only the file.

Tests: `tests/diff/test_diff_output_bounded.py` (10; the terminal is simulated by replacing
`_stdout_is_terminal`, and a separate test checks that it reads `isatty()`; a pty test was left out
because Windows is in the CI matrix and a skip is not allowed). Before the fix: the bounded-output,
`--json FILE` and capture-diff tests failed (3); the `--json -`, pipe and short-list tests passed
(they guard what must not change). The webhook test with `--json FILE` fails without the
`result_payload` hand-over. The real terminal was checked by hand with `pty.openpty()` on the demo
data (2,310 bytes, exit 0). Docs: `docs/CLI.md`, `docs/DRIFT.md`.

One existing test changed: `tests/cli/test_profile_sampling_cli.py::
test_diff_notes_a_sampled_profile_against_an_unsampled_one` (arrived with INT-18) asserted that
`--json FILE` also prints the result on stdout, which is the behaviour #308 removes. It now asserts
the notes in the file, an empty stdout, the note on stderr, and the notes on a plain diff's stdout.
It is not weaker: every value it checked is still checked, and it failed on the stderr part
before the notes were printed there. Likewise `tests/regressions/test_aud_cli.py::
test_diff_of_captures_writes_json` asserted the old double print for capture diffs: it now takes
the printed changes from a plain run and checks that `--json r.json` prints nothing and writes the
same changes.

## #309: healthcare inference failed in a clean install (faker in no user extra)

**Fix:**
- core `pyproject.toml`: new extra `faker = ["faker>=24"]`, also in `[all]`;
- `scripts/build_pure_wheel.py`: the pure wheel (the demo's core wheel) provides `[faker]` too;
- `plugins/shape-domains`: depends on `faker>=24` (its own telecom schemas use faker strategies,
  and the demo scenarios need the domains plugin), so the documented install brings it;
- the error without faker: `... install it with: pip install 'sqllocks-shape[faker]' (or pip
  install faker)`. The old wording is kept inside it (`tests/generation/test_strategies_p404b.py`
  matches it, unchanged). Output is never degraded: without faker the run still fails and says why.

Tests (`tests/plugins/test_optional_plugin_pieces.py`): the extra and `[all]`; the domains
dependency; the built domains wheel's `Requires-Dist`; the pure wheel's `Provides-Extra`; the
error names the extra; `shape demo run healthcare --rows 1000` in a subprocess where `faker`
cannot be imported exits 1 naming the extra. All failed before the fix.

## #310: the plugins are not on PyPI; the fabric plugin hard-required eventhubs and sqlserver

**Fix** (`plugins/shape-fabric`): `dependencies = ["sqllocks-shape==0.9.0"]`; new extras
`eventhubs = ["sqllocks-shape-eventhubs==0.9.0"]` and `sqlserver = ["sqllocks-shape-sqlserver==0.9.0"]`.
The code allowed it with guarded imports:
- `errors.py`: `MissingPluginError(ImportError)` and `optional_plugin(module)`, which raises it with
  `... need the sqllocks-shape-sqlserver plugin, which is not installed: pip install
  'sqllocks-shape-fabric[sqlserver]' (from the release's wheels: add --find-links with their
  folder, see docs/INSTALL.md)` only when that plugin is the missing module;
- `_tsql.py`: the SQL Server helpers load when a SQL target is used (`_sql()`,
  `build_connection_string`); `sinks.py` and `auth.py` use them, so `shape_fabric.sinks`,
  `targets`, `sqldb`, `warehouse` and `eventhouse_writer` import without the plugin;
- `eventstream.py`: without the Event Hubs plugin the emitter class still exists (the entry point
  loads) and `emit`/`parse` raise the error; with it, it is the same subclass of
  `EventHubsEmitter` as before; `testing.py`: `EventstreamHarness` likewise.
- `shape demo notebook`'s `%pip` line now asks for `sqllocks-shape-fabric[eventhubs,sqlserver]`, so
  a Fabric run keeps the old set; `tests/demo_cmd/test_notebook_and_outputs.py`'s requirement regex
  now accepts `[extras]` (the set it compares is unchanged).
- `src/shape/testdata/seed.py`'s install hint names `'sqllocks-shape-fabric[sqlserver]'`.

Docs: `docs/INSTALL.md` (new "Plugins from the release's wheels": build, `--find-links`, the two
extras, the `faker` extra; `faker` added to the offline-lock list), `docs/DEMO.md` (the install
steps), `plugins/shape-fabric/README.md`, `docs/SINKS.md`, `docs/EMIT.md`,
`docs/GENERATION_STRATEGIES.md`, `CHANGELOG.md`.

Tests: packaging in `tests/plugins/test_optional_plugin_pieces.py` (pyproject, and the built
fabric wheel's `Requires-Dist` carries both only with `extra == ...`; the notebook line), runtime in
`plugins/shape-fabric/tests/test_optional_extras.py` (the CI plugins job installs the fabric
plugin; the core job does not): in a subprocess where `shape_eventhubs`, `shape_sqlserver`,
`pyodbc` and `azure.eventhub` cannot be imported, every `shape_fabric` entry point loads, the
Lakehouse writer writes, and the Eventstream emitter, the harness, the SQL database writer and the
SQL Server sink raise the error naming their extra. All failed before the fix, except the guard
that the full install is unchanged.

## Clean venv, by hand (needs PyPI for third-party packages, so not a test)

Wheels built from this branch (`build_pure_wheel.py`, `pip wheel --no-deps` of the four plugins),
then a fresh `python3 -m venv` and exactly the `docs/DEMO.md` lines:
`pip install --find-links wheels wheels/sqllocks_shape-*.whl wheels/sqllocks_shape_domains-*.whl
wheels/sqllocks_shape_fabric-*.whl` installed core, domains, fabric and Faker 40.40.0, and no
eventhubs, sqlserver, pyodbc or azure-eventhub. Then `shape doctor`: `Result: OK`; `shape plugins
doctor`: 128 plugins, `all plugins load`; `shape demo run healthcare --rows 1000`: exit 0,
`Fidelity score: 98.8%` (no manual `pip install faker`); seeding retail to a local folder: ok;
`SqlServerSink().write(...)`: the `[sqlserver]` message. After `pip install --find-links wheels
'sqllocks-shape-fabric[sqlserver,eventhubs]'`: both plugins, pyodbc 5.3.0 and azure-eventhub
5.15.1 installed, `_tsql.ident('a]b') == '[a]]b]'`, and the emitter subclasses `EventHubsEmitter`.

## Checks run in this session (final code)

All on `a18e90d` (INT-18 `e77d558` merged) unless noted; venv `pip install -e ".[dev]"`, every plugin
editable, `tests/demo/fabric/requirements.txt`, pyarrow 25.0.1, unixODBC, `$REFENGINE_ROOT` at the
pinned commit with its venv (`setup_refengine.sh`).

- `ruff check src tests plugins benchmarks/vs_refengine`: all checks passed; `ruff format --check`
  (same paths): 1718 files formatted; `mypy`: no issues in 637 files; `mypy --strict` on the six
  fabric modules touched: 1 error, `sinks.py` `SynapseSink._write_in_loads` arg-type, present on
  the base too (the base had 7 there).
- `scripts/check_user_facing.py`: clean (D-13); `check_secrets.py`: 0; `check_plugin_skeletons.py`:
  OK (11 distributions, 0.9.0).
- `pytest tests/demo`: **369 passed**.
- Plugin suites (`plugins/shape-{fabric,eventhubs,sqlserver,domains}/tests`, `-m "not emulator and
  not live"`): **1139 passed**.
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` (one run, 48 min): **14232 passed, 38
  skipped, 4 failed**. All four fail on pristine `int/INT-18` `e77d558` too, or are load-dependent:
  - `tests/bridge/test_compat_1_0.py` and `test_compat_1_1.py` `[list]`: the recorded domain
    descriptions differed from the domains plugin's (same on the base). INT-18 `b6fc3d6` fixed the
    vectors; after merging it (`b5bddea`, which changes only those two vectors and the Kafka
    plugin's test helper) both pass, with this lane's new tests (182 passed, rust kernel);
  - `tests/test_removed_modules.py::test_removed_module_is_not_importable[history]`: same on base;
  - `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows`: the 48M-row
    peak came out lower than the 24M-row one (329 vs 379 MB, over the 10% bound). Run alone it
    passes, on the base and on this branch (rust 95 s). This branch touches no profile, kernel or
    io code.
- `SHAPE_KERNEL=python pytest -m "not emulator and not live"`, run in shards because one test takes
  66 minutes under the pure-python kernel (a single process passed the session's 2-hour limit):
  **14232 passed, 38 skipped, 3 failed** (the two bridge `[list]` vectors and
  `test_removed_modules[history]`, as above). The memory test was deselected in the shard and run
  on its own: **1 passed** (66 min; on `59a4bda`, whose profile, kernel and io code is identical to
  the head's). In an earlier sharded run with other suites running beside it, it failed the same
  way as under rust.
- `tests/streaming/emit/test_faults.py::test_emit_to_two_files` failed in two earlier runs and on
  the base, and passed in both final runs.

No test was skipped, deselected (other than the one memory test, run separately as stated) or
weakened. Three existing tests were adapted (two for the behaviour #308 asks for, one for the
extras in the notebook's install line); each is described above.

## Workflows

No `.github/workflows` change is needed: the plugins job installs eventhubs, sqlserver and fabric
together, and the core job installs no fabric plugin (the runtime fabric tests live in the plugin's
own tests for that reason).

## Paths changed

`src/shape/cli/{main,machine}.py`, `src/shape/builtins/strategies/providers.py`,
`src/shape/demo/notebook.py`, `src/shape/testdata/seed.py`, `pyproject.toml`,
`scripts/build_pure_wheel.py`, `plugins/shape-domains/pyproject.toml`, `plugins/shape-fabric/`
(`pyproject.toml`, `README.md`, `src/shape_fabric/{errors,_tsql,sinks,auth,eventstream,testing}.py`,
`tests/test_optional_extras.py`), docs listed above, `CHANGELOG.md`, new tests
`tests/diff/test_diff_output_bounded.py`, `tests/plugins/test_optional_plugin_pieces.py`, and the
three existing tests named above (`tests/cli/test_profile_sampling_cli.py`,
`tests/regressions/test_aud_cli.py`, `tests/demo_cmd/test_notebook_and_outputs.py`). Nothing under `$REFENGINE_ROOT`, `.github/workflows` or the plan's
tables.
