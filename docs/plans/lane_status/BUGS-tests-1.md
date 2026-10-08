# BUGS-tests-1: test-suite and test-tooling bugs

Lane branch `lane/BUGS-tests-1` (from `build/main-plan` 5c91ea5, `origin/int/INT-18` merged in
c62e167). Every change below carries a regression test that failed before it.

Status: **done**, with one escalation (#337 part 2, below).

## Fixed

| Issue | Commit | Change |
|---|---|---|
| #554 #558 #560 #77 | a7ad106 | The credential-reference "no cloud SDK imported" check runs in a fresh interpreter. INT-18 carries a stricter version of the same test; the merge (c62e167) kept INT-18's. |
| #481 | c07b3c4 | Cloud-source parametrize ids no longer embed the gzip header bytes. |
| #512 | cad40d1 | `shape_databases.testing.FakeServer` state is guarded by one re-entrant lock. Kept through the merge alongside INT-18's new Snowflake and Databricks fakes. |
| #557 | 8378f00 | The fuzz smoke run scales its per-input time budget only while a tracer is attached. |
| #337 part 3 | 4cd4c30 | `shape.quality.evaluate` fails a range rule whose observed value cannot be compared, instead of raising `TypeError`. INT-18 carries the same fix (typed `_within` helper), and the merge f9860c9 kept it; this lane's `tests/quality/test_evaluate.py` stays. |
| #337 part 1 | d60d6db | `[tool.coverage.run] patch = ["subprocess"]` and `coverage>=7.10` in the `dev` extra; `.coverage.*` is ignored. Test: `tests/release/test_coverage_config.py`. |

### #337 part 1: coverage before and after

Measured with `make check`'s own coverage step (`pytest -q -m "not emulator and not live and not
heavy" --ignore=tests/demo/fabric --ignore=tests/demo/content --cov=shape --cov-fail-under=86`),
both runs on the merged tree (after c62e167), with the default kernel:

| | Before | After |
|---|---|---|
| Total | 93.38% (67 641 / 72 440 lines) | 94.05% (68 128 / 72 440 lines) |
| `shape/plugins/cli.py` | 48.3% (99 / 205) | 92.2% (189 / 205) |
| `shape/scale/chunk_worker.py` | 28.6% (8 / 28) | 100.0% (28 / 28) |
| `shape/cli/gitcmds.py` | 56.3% (58 / 103) | 94.2% (97 / 103) |
| `--cov-fail-under=86` | reached | reached (gate unchanged) |

The issue's figures (28%, 29%, 33%) are from before INT-18. `tests/plugins/test_plugin_cli.py` on its
own measures `plugins/cli.py` at 50% with the patch and 0% ("module was never imported") without it.
The test outcomes are identical before and after (see "Inherited failures"), and no `.coverage.*`
file is left behind: pytest-cov combines the child data files.

Lockfile: no Python lockfile is committed. The offline locks are generated in CI by
`scripts/offline_lock.py generate`, and `ci/constraints-min.txt` pins only the runtime floors
(numpy, pyarrow), so nothing needed regenerating.

## Escalation: #337 part 2 not done (`shape.validation.invariants` kept)

The lead's GO depended on the module being imported by nothing. That no longer holds after INT-18,
and one premise of the issue was wrong from the start:

- INT-18 (AUD-quality, 3497bf3) added `tests/validation/test_statistics_invariants.py`, which imports
  `referential_integrity`, `uniqueness`, `deterministic_partitions` and `finite_shape` and tests each
  one. The module measures 100% covered in the baseline run above.
- `deterministic_partitions` works. `GenerationPlan` provides `rows` and `rows_at`
  (`src/shape/generation/strategies.py:164-168`), as it already did at 8378f00, and
  `test_deterministic_partitions` passes.
- AUD-quality recorded "`validation.invariants` stays (now tested)" in
  `docs/plans/lane_status/AUD-quality.md`.
- Nothing public-facing exports it. It is not in `shape.__all__`, `docs/` or the API docs. The only
  other reference is the T-12 mypy ratchet entry in `pyproject.toml`.

Deleting the module now means deleting another lane's passing tests, so this lane left it in place.
Lead decision needed: either (a) keep the module and close part 2, or (b) delete it together with
`tests/validation/test_statistics_invariants.py`'s four invariants tests and the
`"shape.validation.invariants"` mypy override, with a `ModuleNotFoundError` regression test
(plan §6.2 item 3).

## Inherited failures (not this lane's)

`origin/int/INT-18` at 004762f is not green in this container. The coverage runs above show 132
failures and 4 errors, all in tests this lane does not touch, for example `tests/history/*`
(`HistoryError: ... has no sketch state, which --coarse needs`), `tests/plugins/test_scaffold.py`
(`no template for shape.behaviors`), `tests/dictionary/test_dictionary.py`,
`tests/test_removed_modules.py::...[history]` (INT-18 adds `shape.history` back) and
`tests/diff/test_drift_sweep.py` (errors). All 136 node ids fail identically on a clean
`git worktree` of `origin/int/INT-18`, and the set is the same before and after this lane's change.
`plugins/shape-databases/tests` has 10 failures (`refusing to write to non-local target ... pass --yes
or set SHAPE_CONFIRM_REMOTE=1`), which also reproduce with INT-18's own `testing.py`.

## Checks run in this session

- `ruff check src tests plugins benchmarks/vs_refengine`: clean.
- `ruff format --check src tests plugins benchmarks/vs_refengine`: clean.
- `mypy`: clean once `tests/demo/fabric/requirements.txt` is installed (it brings in `pydantic`,
  which `shape.importers.pydantic_models` from INT-18 needs).
- `python scripts/check_user_facing.py` (D-13): clean.
- `pytest -m "not emulator and not live"` (extra installs: `tests/demo/fabric/requirements.txt`,
  unixODBC, `-e plugins/shape-databases`, `-e plugins/shape-dbt`):
  - `SHAPE_KERNEL=rust`: 139 failed, 14 035 passed, 22 skipped, 4 errors. The 136 inherited ones
    above, plus 7 in `tests/demo/fabric/test_dbt.py` (`No module named 'shape_dbt'`). Those 7 pass
    (16 passed) once `plugins/shape-dbt` is installed, as the CI job does.
  - `SHAPE_KERNEL=python` (run in two halves to stay within the session's time limit): 132 failed,
    14 042 passed, 22 skipped, 4 errors. Exactly the 136 inherited node ids.
  - No failure is new against `origin/int/INT-18`.

No `.github/workflows` change is needed.
