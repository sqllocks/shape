# BUGS-demo-2: demo fidelity score (#522) and the bridge demo commands (#541)

Lane branch `lane/BUGS-demo-2` (from `build/main-plan` 5c91ea5). Builder notes; the lead merges. No
issue was commented on, labelled or closed. No workflow, plan §11/§2.3 or pinned-baseline file was
touched. Each fix is one commit, test-first, pushed after the commit.

## #522: a fidelity report that compared no column claimed 100%

Lead decision: fix. `FidelityReport.overall_score()` returns `0.0` when no column was compared;
`render()` prints `Fidelity score: n/a (no columns compared)`; the inference mode reports the same
(dashboard line, charts page, and `fidelity_score` `None` in the result and the manifest metrics,
which the CLI and the notebook already skip). Files: `src/shape/demo/{fidelity,charts}.py`,
`src/shape/demo/modes/inference.py`.

Tests (`tests/demo_cmd/test_inference_streaming.py`): the empty-report assertion changes from
`== 1.0` to `== 0.0`, with a comment (a 100% claim needs at least one compared column); the other
assertions are unchanged. Three new tests (render of an empty report, render of a full one, the
inference run with no compared column). Before the fix: 3 failed, 12 passed; after: 15 passed.

`demo_1to1/verify.py` (run in the baseline venv, against Shape in `~/.venvs/shape`): **exit 0,
`VERDICT: PASS`**, no edit to the verifier.

## #541: `demo_list`, `demo_run`, `demo_status`, `demo_cleanup` were refused as pending

Lead decision: fix. Each handler (`src/shape/bridge/handlers/demo.py`) calls the matching
`shape.demo.api` function. The run's progress goes to standard error (`DemoRuntime(out=sys.stderr)`),
so standard output carries only the reply, dry runs included. A session, scenario, mode or setting
the demo cannot use is `input.invalid_value` (the bridge's existing mapping of `DemoError`).
`demo_cleanup` returns `removed` as a list of `{target, names}` entries, because the published
result schema declares `removed` as an array (the API returns a mapping). The published request and
result schemas are not changed; `docs/bridge/schema/index.json` only drops `"pending": "P6-12"`
(now `null`, like every other command).

- Retired: `test_a_valid_demo_request_waits_for_shape_demo`,
  `test_the_demo_commands_are_marked_pending_in_the_command_table_and_nothing_else_is`.
- New (`tests/bridge/test_demo.py`): list equals the API's; dry run; run, status, dry-run cleanup and
  cleanup end to end; stdout clean (run and dry run); input errors for an unknown session, scenario
  and mode; a Spark status without a token; `test_no_command_is_marked_pending`. Before the fix:
  10 failed, 12 passed; after: 22+ passed.
- Vectors: the four vector files gain success cases (`tests/bridge/make_vectors.py`; a `sessions`
  fixture and an isolated `SHAPE_HOME=${DIR}/shape-home` in `vectors_lib.py`, `test_vectors.py`);
  `test_every_command_has_a_success_and_a_failure_vector` no longer has a pending exception.
- `docs/BRIDGE.md` demo paragraph rewritten; `benchmarks/vs_refengine/README.md` line updated.
- `benchmarks/vs_refengine/bridge_1to1/verify.py` asserted the interim refusal (4 checks failed after
  the wiring). It now compares the demo commands with the baseline's: the catalog, a run's result
  (fields, score, artifact count equal), the manifest, an unknown session, and a dry-run cleanup;
  each bridge runs in an isolated home. Result: `--skip-data` 38/38; with `--negative-control`
  48/48, exit 0. (The data checks were not re-run: nothing there is affected.)

## Environment and commands

`python3 -m venv ~/.venvs/shape`; `pip install -e ".[dev]"`; `pip install -e plugins/*`
(databases, domains, eventhubs, kafka, simulation, sqlserver, then fabric);
`pip install -r tests/demo/fabric/requirements.txt` and `apt-get install unixodbc`; baseline per §1.2
(`~/refengine` at 422e78d, `~/.venvs/refengine`, unmodified). The UDF SDK pins pyarrow 19.0.1; the main
matrix runs on the newest, so the venv was then set to `pyarrow==25.0.1` (the benchmark pin).

| Command | Result |
|---|---|
| `pytest tests/demo tests/demo_cmd` (after #522, pyarrow 19) | 461 passed |
| `pytest tests/bridge tests/demo_cmd` (after #541) | 523 passed |
| `make check`, every step | ruff check, ruff format --check, mypy, compileall, vulture, lint-imports, check_requirements, check_secrets, **check_user_facing**, check_shipped_data, check_plugin_skeletons, check_conformance_coverage, cargo fmt, clippy, cargo test: exit 0 |
| `make check` pytest step (coverage gate 86) | 6750 passed, 1 failed (see below), 17 skipped |
| `pytest -m heavy tests/kernel tests/profile tests/streaming` | exit 0 |
| `SHAPE_KERNEL=python pytest tests/kernel` | exit 0 |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | 7046 passed, 1 failed (same test), 17 skipped |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 7046 passed, 1 failed (same test), 17 skipped |

The first `make check` pytest run (pyarrow 19.0.1, left by the Fabric requirements) also failed
`tests/kernel/test_hashing.py::...[float16]` (`if_else` has no halffloat kernel in that pyarrow);
it passes on 25.0.1.

### The one failing test is not from this lane

`tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references` fails
only in a whole-suite run and passes alone. It asserts that no `azure*` module is in `sys.modules`
for the whole process. `tests/demo/fabric/test_adf.py::test_committed_definitions_match_the_generator`
is the first test that leaves one there (it imports `azure.functions` from the Fabric UDF SDK that
`tests/demo/fabric/requirements.txt` installs). Neither lane file touches it; I did not change it.
It is an order dependence between two existing tests when the SDK is installed. Suggested fix for
the lead (not made here): have that test check a fresh interpreter's `sys.modules`, as
`test_recognising_a_reference_imports_nothing` does for its own imports.

No workflow edits were needed, so there are no diffs to record here.
