# W-safe-diff-1 (0.9.4)

## Start and contract

- `git remote get-url origin` — exit 0: `https://github.com/sqllocks/shape.git`.
- `git fetch origin` — exit 0.
- `git checkout -B lane/W-safe-diff-1 origin/main` — exit 0; base `a8e476c`.
- Read CLAUDE.md, CONTRIBUTING.md, PROJECT.md Baselines, DRIFT.md, EXIT_CODES.md,
  PRIVACY_MODEL.md, CLI routing, profile diff implementation and existing diff tests.
  No applicable AGENTS.md found. W-uv-1 is absent from main; use the current venv/Make workflow.
- Contract: refuse `shape-safe-profile` JSON in `shape diff` and `shape.diff`.
  Safe `.shape` captures remain comparable with `not_evaluable` for withheld metrics.
  REGISTRY.md and `shape.registry.drift` explicitly provide a separate safe-form comparison
  API/command; they do not promise safe JSON support in these two entry points.

## Tests first: red on origin/main

Implementation was unchanged from `a8e476c` when this ran (only new tests added).
All commands below use `PATH="$PWD/.venv/bin:$PATH"`.

`pytest -q tests/diff/test_safe_profile_refusal.py tests/cli/test_safe_profile_refusal.py`
— exit 1: **21 failed**.

Evidence:

- Two safe JSON CLI inputs return 0, including with `--fail-on-drift`, despite a
  null-rate change from 0 to 0.12. Plain output is `[]`.
- `--json - --fail-on-drift` prints
  `{"format": "shape-result", "version": 1, "command": "diff", "exit_code": 0, "payload": []}`.
- Serialized safe mappings passed to `shape.diff` do not raise the required ValueError.
- SafeProfile objects raise an unrelated TypeError, and mixed CLI inputs produce archive/text
  errors that do not identify the safe input or give the required guidance.
- API cases cover null-rate changes of 0.06 and 0.12, either/both safe sides, and both
  SafeProfile objects and serialized mappings. CLI cases cover either/both safe sides,
  ordinary output, `--fail-on-drift`, and JSON output.

## Checks and completion

- New regression tests (same command as red above) — exit 0: **21 passed**.
- `ruff check src/shape/diff/_inputs.py src/shape/contracts/v1.py src/shape/cli/main.py src/shape/cli/exitcodes.py tests/diff/test_safe_profile_refusal.py tests/cli/test_safe_profile_refusal.py`
  — exit 0.
- `ruff format --check` on the same six Python files — exit 0.
- `python scripts/gen_exit_codes.py` — exit 0; regenerated EXIT_CODES.md from its source.
- `NO_MKDOCS_2_WARNING=1 mkdocs build --strict` — exit 0; built in 16.04 seconds.
- `python scripts/check_doc_links.py --site site` — exit 0: 0 broken links.
- `python scripts/check_user_facing.py --site site` — exit 0: clean.
- `git diff --check` — exit 0.
- `pytest -q tests/safecapture tests/registry/test_safe_drift.py tests/project tests/regressions/test_iss_cli_errors.py`
  — exit 0: **511 passed**, 28 warnings (unsigned test artifacts).
- `pytest -q tests/diff tests/drift tests/cli` — exit 1: **1238 passed, 1 failed,
  1 existing dependency skip**, 23 warnings. The sole failure is the live TCP connectivity
  test described separately below. All diff/drift tests and the new CLI/API cases passed.
- `pytest --collect-only -q tests/diff tests/drift tests/cli` — exit 0 (1240 collected).
- `git diff --exit-code origin/main -- tests/cli/test_doctor_checks.py src/shape/cli/doctor_checks.py`
  — exit 0 (unchanged).
- `pytest -q tests/cli/test_doctor_checks.py::test_live_eventhubs_namespace_dns`
  — exit 1: the same direct TCP failure reproduces in isolation.
- `pytest --collect-only -q -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric --ignore=tests/demo/content`
  — exit 0: 18666 selected, 69 deselected. Used to identify the coverage-run failure.
- `git diff --exit-code origin/main -- tests/builtins/test_bugs_builtins_file_sink_atomic.py src/shape/builtins/sinks.py`
  — exit 0 (unchanged).
- `pytest -q tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left`
  — exit 1: existing umask-sensitive assertion fails in isolation.
- `pytest -q tests/docs/test_brand.py::test_branded_site` — exit 1: the strict build
  succeeds, then the unchanged screenshot verifier cannot import `playwright`.
- `make check` — exit 2 after manual interruption of its already failing coverage run:
  **4690 passed, 14 failed, 20 existing dependency skips, 69 deselected** in 24m02s.
  All static checks completed successfully before pytest. The run was interrupted once the
  unrelated environment failures were diagnosed; it did not complete the full core suite
  or reach the heavy, Python-kernel and Rust targets. No passing full-suite claim is made.
- `umask 0022; pytest -q tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left`
  — exit 0: **1 passed**, confirming the failure depends on the inherited umask.
  The make-check runs retain the executor default `0077`.
- `PYTEST_ADDOPTS=--maxfail=1 make check` — exit 2: all static checks passed;
  pytest stopped with **2066 passed, 1 failed, 1 existing dependency skip, 69 deselected**
  in 3m03s. The failure is the unchanged umask-dependent file-sink assertion. The make
  recipe's pytest exit was 1; make returned 2. The coverage threshold remains 86.
  Heavy, Python-kernel and Rust targets were not reached because the core test gate failed.
- Final regression rerun (same two-file command as red/green) — exit 0: **21 passed**.
- Final `git diff --check` — exit 0.

The GitHub Action docs do not mention safe baselines; no change was needed there.
The exit-code source was updated so generated docs and command help stay consistent.
Safe-profile format and golden corpus are unchanged.

## Sandbox artefacts

The 14 failures observed before interrupting the first broad run:

| Test/page | Observed failure |
|---|---|
| File-sink permissions | umask `0077` creates mode `0600`, forbidden by the existing assertion |
| Branded site | missing `playwright` |
| Domain DuckDB tutorial; SINKS | databases plugin absent; no DuckDB sink |
| CONTRIBUTING; INSTALL; PROJECT; SIGNING; TESTING_WITH_SHAPE | example editable builds cannot locate the Rust toolchain and fail importing `puccinialin` or `httpx` |
| CLI | doctor transcript expects NumPy 2.5.3, Arrow 25.0.1 and tzdata; executor has 2.5.4, 26.0.0 and no tzdata |
| CONTAINER | example container build fails at Debian package installation (exit 100) |
| DBT | `/tmp/docs-dbt-packages` fixture absent |
| IMPORTERS | Pydantic v2 absent |
| SIMULATION_FILES_EVENTS | simulation plugin absent; `simulate` command unavailable |

These diagnostics concern unchanged test/example paths. No transcript, dependency gate,
format or golden fixture was edited to make them pass.

- The branded-site test cannot import `playwright` (not installed). Its strict MkDocs
  build succeeds before the screenshot verifier fails.

- The executor inherits umask `0077`. The unchanged file-sink test
  `test_new_file_has_umask_permissions_and_no_temp_left` asserts that the created file is
  not mode `0600`; honoring this executor umask produces exactly `0600`. This is the
  first coverage-run failure, also reproduced in isolation.

- `tests/cli/test_doctor_checks.py::test_live_eventhubs_namespace_dns` expects a direct TCP
  connection to `management.azure.com:443`. DNS passed but TCP failed in this executor.
  The full module run included the existing `live` marker; no tests were skipped or
  changed to conceal the failure. The test and its implementation are unchanged from main.
- The module run also reported the existing `pytest.importorskip("shape_databases")` in
  `tests/cli/test_generate_to.py:162`; that optional plugin is not installed. No skip or
  xfail was added by this change.

## Files changed

- `src/shape/diff/_inputs.py`: shared format/object refusal with actionable guidance.
- `src/shape/contracts/v1.py`: reject either API input and document ValueError.
- `src/shape/cli/main.py`: reject safe exports before profile/document routing.
- `src/shape/cli/exitcodes.py`: document the refusal under existing input-error code 2.
- `tests/diff/test_safe_profile_refusal.py`: API regression cases.
- `tests/cli/test_safe_profile_refusal.py`: CLI regression cases.
- `docs/DRIFT.md`: distinguish refused JSON exports from comparable safe captures.
- `docs/EXIT_CODES.md`: regenerated refusal description.
- `CHANGELOG.md`: requested Unreleased fix entry.
- `docs/plans/lane_status/W-safe-diff-1.md`: this record.

## Open questions

None.

## Merge of updated main (2026-10-15)

- Before merge: `lane/W-safe-diff-1` at `93ede80ba132336e830708ab57f07465d9e1bb3c`;
  clean working tree and expected origin verified (inspection commands exit 0).
- `git fetch origin && git merge --no-ff origin/main` — exit 1: fetch succeeded;
  merge of `2d4e86e06f1f3ff90aae65aeefa54820a3f5bd3e` stopped on CHANGELOG.md only.
- Resolved CHANGELOG.md by retaining both main entries (Playground documentation and
  the integrations extra withdrawal) and this lane's safe-diff refusal entry under Unreleased.
  All other merged files retain main's version.
- `git diff --cached origin/main -- README.md docs/QUICKSTART.md docs/index.md docs/plans/lane_status/W-anon-1.md docs/plans/lane_status/W-play-1.md docs/plugins/authoring.md docs/plugins/integrations.md plugins/shape-integrations`
  — exit 0 with no differences: main's changes outside lane scope preserved exactly.
- `git diff --check` — exit 0.
- `git diff --cached --check` — exit 0.
- `.venv/bin/python -m pip install pytest-xdist` — exit 0; installed test runner only
  in the existing virtualenv so the additional docs/release suite can use three workers,
  matching the docs workflow. No repository dependency files changed.
- `pytest -q tests/diff/test_safe_profile_refusal.py tests/cli/test_safe_profile_refusal.py`
  — exit 0: **21 passed**. The original tests-first red evidence above remains applicable;
  the merge does not alter the fix or its tests.

Merge-validation commands below use the existing
`PATH="$PWD/.venv/bin:$PATH"`; no test skips or xfails were added, and no threshold or
format/golden corpus was changed.

- `NO_MKDOCS_2_WARNING=1 mkdocs build --strict` — exit 0; 27.01 seconds.
- `python scripts/check_doc_links.py --site site` — exit 0: no broken links.
- `python scripts/check_user_facing.py --site site` — exit 0: clean.
- `pytest -q tests/safecapture tests/registry/test_safe_drift.py tests/project tests/regressions/test_iss_cli_errors.py`
  — exit 0: **511 passed**, 28 warnings.
- `pytest -q tests/diff tests/drift tests/cli` — exit 1: **1238 passed, 1 failed,
  1 existing dependency skip**, 23 warnings (326.80 seconds). The failure remains
  `test_live_eventhubs_namespace_dns`: DNS passed but direct TCP did not. No diff/drift
  regression failed. The skip remains the missing optional databases plugin.
- `PYTEST_ADDOPTS=--maxfail=1 make check` — exit 2: all static checks passed;
  **2066 passed, 1 failed, 1 existing dependency skip, 69 deselected** in its core pytest
  gate (314.90 seconds). The first failure remains the unchanged umask-dependent file
  permission assertion. The coverage floor remains 86; later make targets were not reached.
- `pytest -q -n 3 tests/docs tests/release` — exit 1: **540 passed, 17 failed,
  3 setup errors**, 2 warnings in 425.99 seconds. Completed the entire requested suite;
  no tests were excluded or interrupted. The three-worker option matches docs CI.

### Merge-validation environment diagnostics

- The live TCP test and umask failure match the original lane checks above.
- Docs failures: CONTRIBUTING, branded site, DuckDB tutorial, CLI, CONTAINER, DBT,
  SIGNING, SIMULATION_FILES_EVENTS, SINKS, TESTING_WITH_SHAPE, plugin authoring,
  behavior, healthcare-codes, simulation, IMPORTERS, INSTALL and PROJECT.
- The previously recorded docs causes recur: missing playwright, databases plugin,
  Pydantic v2, DBT fixtures, Rust build prerequisites (`puccinialin`/`httpx`), container
  apt installation failure, and environment-specific doctor transcript versions/tzdata.
- Additional plugin example failures identify missing `sqllocks-shape-dbt`, behavior,
  healthcare-codes and simulation installations; no plugin/docs implementation was changed
  to bypass those failures.
- Three release setup errors are in `test_packaging.py`: core sdist contents, build inputs
  and user-facing checks share a fixture whose `maturin sdist` fails because `cargo` is
  absent from PATH. Other release tests completed. No packaging code was changed.

### Merge resolution completion

Only CHANGELOG.md required conflict resolution. It retains all main Unreleased entries
and the lane fix. Main's files outside lane scope remain byte-for-byte identical to the
merged main commit. The lane implementation, tests, safe-profile format and golden corpus
are unchanged by the merge. No rebase, force-push, tag or new PR is used. Open questions: none.
