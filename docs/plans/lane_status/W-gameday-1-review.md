# W-gameday-1 — independent review

**Verdict: APPROVE**

- Lane: `lane/W-gameday-1`, head reviewed `1e63882cbcafcec07fcc5cb1bef8f596410e4eab` (matches expected).
- Base: `origin/main` `c10923be9278bc63f2713ac0ed32a548b59a7afb`. Diff: `git diff origin/main...HEAD`
  (CHANGELOG.md, `src/shape/cli/gameday.py`, `src/shape/cli/machine.py`,
  `tests/scenario/test_gameday.py`, the lane status file).
- Read: promotion plan Decisions and section 4.2 (`origin/plan/promotion-093`), CLAUDE.md,
  CONTRIBUTING.md, `docs/plans/lane_status/W-gameday-1.md`.
- Environment: Python 3.11 venv, `make bootstrap`, plus the five plugins editable.

## Commands and exit codes

| Command | Exit |
| --- | --- |
| `python3.11 -m venv .venv && make bootstrap && pip install -e plugins/…` (5 plugins) | 0 |
| `ruff check src tests plugins benchmarks/vs_refengine` | 0 |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 (2079 files already formatted) |
| `mypy` | 0 (no issues in 698 source files) |
| `python scripts/cli_surface.py --check` | 0 (0 breaking changes, 0 notes) |
| `pytest -q tests/scenario tests/cli -p no:cov` | 0 (1475 passed, 1 skipped: pre-existing `shape_databases` importorskip in `tests/cli/test_generate_to.py:162`, not in this diff) |
| `pytest -q tests/docs/test_reference_examples.py -k GAMEDAY -p no:cov` | 0 (1 passed) |
| Red check: `src/shape/cli/{gameday,machine}.py` reset to `origin/main`, new tests run | 1 (2 failed: `KeyError: 'actions'` and key-list mismatch — same as the builder's recorded red) |
| Only `machine.py` reset to `origin/main` (gameday.py change kept), new tests run | 1 (document wrapped in the `shape-result` envelope: extra `exit_code`, `payload` keys) |
| `git diff origin/main...HEAD` grep for added skip/xfail/gate/tolerance/noqa/type: ignore | none in code; only `# fmt: skip` on two test call lines (formatter layout directive, not a test skip) |

Full core-stage coverage and the docs/docs_site failures were not counted (known sandbox
artefacts, also red on clean `origin/main` here); the lead proves the full suite in GitHub CI.

## Behaviour checked by hand (`shape gameday run … --dry-run`)

| Case | Exit | stdout |
| --- | --- | --- |
| valid plan, new `-o`, `--json` | 0 | `{"actions": [{"action": "create", "target": <out>}], "command": "gameday run", "format": "shape-dry-run", "version": 1}`; folder not created; stderr empty |
| valid plan, existing empty `-o`, `--json` | 0 | same, `"action": "write"` (from `_file_action`) |
| valid plan, text | 0 | unchanged (`round … would plant …`, `dry run: nothing was written or run`) |
| disallowed command (`bash`) | 2 | `actions: []`, `error` key, plus `shape: error:` on stderr |
| non-local data (`abfss://…?sig=SECRET…`) | 2 | `error` shows `sig=***` — redacted in both stdout and stderr |
| table not in the data folder | 2 | `actions: []`, `error` key |
| remote `-o` with `sig=` | 2 | `error` is the fixed "local folder" message, no URI echoed |
| non-empty `-o` | 2 | `actions: []`, `error` key |
| missing plan file | 2 | `actions: []`, `error` key |

Success document keys are exactly `format, version, command, actions`; error document adds only
`error`. Both are printed with `sort_keys=True`. The error path matches `machine._dry_run`
(`errors.fail` → stderr line and exit code; `redact_text(errors.describe(exc))`; `--debug` re-raises).
`gameday run` is still in `NATIVE_DRY_RUN`; `SPECS` untouched; `cli_surface` reports no change.

## `NATIVE_DRY_RUN_DOCUMENT`

Correct and minimal. `machine.run` (`src/shape/cli/machine.py:390-394`) only skips the result
envelope for a native dry run when the path is in `NATIVE_DRY_RUN_DOCUMENT`; without the entry the
command's own document is wrapped in `shape-result` (proved by the second red check above). One
set member is the smallest change that gets the decided output; the non-JSON and non-dry-run paths
of `gameday run` are unaffected.

## CHANGELOG

One line, under `## Unreleased` only (hunk `@@ -6,7 +6,7 @@`, replacing "No unreleased changes.").

## Findings

No blocking findings.

1. Non-blocking — `src/shape/cli/machine.py:325-326`: the comment on `NATIVE_DRY_RUN_DOCUMENT`
   says these commands print the document "with their plan under ``plan``". `gameday run` has no
   `plan` key (by decision). Reword to e.g. "print the ``shape-dry-run`` document themselves, so
   ``--json`` is not wrapped again" in a later touch of this file.
2. Non-blocking — `tests/scenario/test_gameday.py:251-257`: the error test covers only a disallowed
   command and asserts `doc["error"]` is truthy. Consider (W-gameday-2 or later) parametrising over
   non-local data, a missing table and a non-empty `-o`, asserting the exact key set
   (`actions, command, error, format, version`), that a secret in a non-local `data` URI is redacted,
   and the `"write"` action for an existing empty `-o`. All of these behave correctly today (table above).
3. Non-blocking — `src/shape/cli/gameday.py:65-82`: imports `machine._file_action`, a private helper,
   across modules. This is what the lead decided; noted only so a later rename of `_file_action`
   keeps `gameday` in mind.
