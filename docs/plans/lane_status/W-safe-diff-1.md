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
