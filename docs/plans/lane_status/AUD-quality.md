# AUD-quality: audit of quality, validation, contracts and drift

Lane branch `lane/AUD-quality` (from `build/main-plan` 5c91ea5). Area: `src/shape/quality/**`,
`src/shape/validation/**`, `src/shape/contracts/**`, `src/shape/drift/**`, their tests,
`docs/DRIFT.md`, `docs/VERIFY.md`.

Status: **done**. 20 defects filed and fixed (each with a regression test committed failing first),
two cleanups and one test-coverage improvement; one issue outside the area filed only (#481).

## Phase 1: findings

Every reproduction ran on this branch at 5c91ea5 with `pip install -e '.[dev,streaming,advanced]'
-e plugins/shape-domains`. "Issue" is the GitHub issue filed in phase 2; "Fix" the commit.

| # | Sev | Issue | Fix | Where | Defect |
|---|---|---|---|---|---|
| 1 | high | #457 | 6f0d2d2 | `quality/utility.py:73` | The utility gate crashes (`ArrowInvalid: Casting from timestamp[us] to timestamp[s] would lose data`) on any timestamp feature with sub-second values. |
| 2 | medium | #461 | 3224144 | `drift/core.py:59-61` | `shape.drift.compare` gives table-level changes of a single table (row count, every joint change) the path `tables.None`; in a dataset every joint change of a table shares the path `tables.<t>`, so `drift.gate` overrides cannot tell them apart. |
| 3 | medium | #462 | 498503f | `contracts/v1.py:88-110,242,322` | A contract with a non-number `row_count.min/max` or `max_null_rate` passes validation and then crashes `shape.check` with `TypeError` (`'<' not supported between 'int' and 'str'`) instead of `ContractError`. |
| 4 | medium | #463 | 105b0bc | `contracts/v1.py:223,226,337` | `nullable`, `unique` and `allow_extra_columns` given as anything but a JSON boolean (`"false"`, `"true"`, `0`) are silently not checked: the contract passes a rule it never tested. |
| 5 | medium | #464 | ae04d33 | `contracts/v1.py:355-374` | A `tables` contract against a dataset silently ignores table rules at its top level (`{"tables": {...}, "row_count": {"min": 1000}}` passes); a sub-contract that is not an object raises `TypeError`; a nested `tables` in a sub-contract is ignored. |
| 6 | medium | #465 | ee820d6 | `quality/gatespec.py:116-153` | Gate schema values are coerced, not validated: `"nullable": "false"` makes the column nullable (`bool("false")`), `"primary_key": "id"` and `"parent_columns": "id"` become `('i', 'd')` (checks nothing), an `enum` list or a `distribution` string crashes the distribution gate (`TypeError`/`AttributeError`), and `columns` as a list raises `AttributeError`. |
| 7 | medium | #466 | c5c1c99 | `quality/gates.py:187` | `referential_integrity` checks a composite foreign key column by column, so a child row `(1, 20)` passes when the parent has `(1, 10)` and `(2, 20)`; `parent_columns`/`child_columns` of different lengths are silently truncated (`zip(strict=False)`). |
| 8 | medium | #467 | 4798f50 | `quality/gates.py:467-473` | A `no_future` entry whose table or column does not exist is skipped without a warning, contradicting `docs/VERIFY.md` ("a misspelt rule cannot be skipped silently"). |
| 9 | medium | #468 | f109c2e | `quality/gates.py:726-730` | The chi-squared enum test never matches an integer or boolean column (JSON object keys are text): it warns every declared value "is missing from data" and never tests. |
| 10 | medium | #469 | 5bbbc27 | `drift/engine.py:559,629,778` | `thresholds={"min_rows": 0}` (allowed: "a number of 0 or more") with an empty column raises `ZeroDivisionError`. |
| 11 | low | #470 | 6f20517 | `drift/engine.py:111-120,181-214` | Thresholds accept NaN and infinity (NaN silently turns a comparison off, docs say "a number of 0 or more"); a policy whose `columns` or `thresholds` is not an object raises `AttributeError`/`TypeError` instead of the documented `ValueError`. |
| 12 | low | #471 | 85dcd66 | `drift/engine.py:912` | Comparing documents with an empty `tables` object raises `ValueError: not enough values to unpack`, which does not say what is wrong. |
| 13 | low | #472 | a33e070 | `quality/gates.py:386-388` | `range_constraint` on a column with no numeric values (a timestamp or text column) checks nothing and says nothing. |
| 14 | low | #474 | 764a1ab | `quality/verifyconfig.py:52-83` | The verify configuration accepts NaN or infinite range bounds (NaN checks nothing), `min` above `max`, and a `date_range` whose start is after its end. |
| 15 | medium | #475 | 1dec79d | `quality/policy.py:38-71` | `validate_rows` (behind `shape quality`) crashes with `TypeError` on a value of the wrong type (`"x"` against `min 0`) or an unhashable value under `unique`; an unknown rule kind passes on empty input; `unique` on an iterator fails only after consuming it. |
| 16 | low | #476 | 30c72ba | `quality/core.py:23-39` | `quality.evaluate` crashes with `TypeError` when a summary value cannot be compared with `min`/`max`. |
| 17 | low | #477 | 38c4e29 | `contracts/core.py:80,177` | `evaluate_contract` with `columns` that is not an object raises `AttributeError`; `compatibility(mode=...)` with a bad mode raises `ValueError("mode")`, which names no valid mode. |
| 18 | low | #478 | 08ce24f | `drift/engine.py:337` | A naive ISO min/max is read in the machine's local time zone, so a column's span (which gates `day_of_week_change`) depends on `TZ` (13.979 days in UTC, 13.9375 in America/New_York for the same column). |
| 19 | low | #479 | c76a9f5 | `quality/verify.py:85` | `load_tables` on a directory: an entry such as a sub-directory named `a.csv` makes the whole load fail; files with upper-case extensions (`B.CSV`) are not loaded, although a single such file is. |
| 20 | low | #480 | 75a8232 | `quality/verify.py:220-223` | The Markdown report's methodology says the distribution gate uses α=0.05 whatever `distribution_alpha` was configured. |
| 21 | low (cleanup) | - | 94f7ddb, e79ad7d | `validation/fuzz.py:425`, `validation/invariants.py`, `validation/__init__.py:8-13` | Dead code: `fuzz._pick` and the whole `validation.invariants` module are never used or tested; `shape.validation` re-exports `dataclass` and `random` by accident. |
| 22 | low (tests) | - | 3497bf3 | `validation/statistics.py`, `quality/quarantine.py` | Uncovered paths: `validate_cardinality`, `validate_quantiles`, `relative_error` have no test. |
| 23 | low (outside area) | #481 | open (outside area) | `tests/builtins/test_cloud_sources.py` | Parametrize ids embed gzip bytes with an mtime, so `pytest -n` (xdist) collection differs between workers. Filed only. |

Checked and found sound: quarantine path names (traversal refused), memorization null/NaN keys,
fuzz time limit, verify configuration unknown keys, contract joint rules, drift noise floors.

## Phase 2 and 3 notes

- Issues #457, #461-#472, #474-#481 filed in `sqllocks/shape` (#473 is not this lane's).
- Each fix: a `regression test for #N` commit with the failing output in its message, then a
  `fix #N` commit, pushed. Fix commits are in the table above.
- #470 scope: a NaN threshold is refused; an infinite threshold is kept as valid (it turns its
  comparison off, as `docs/DRIFT.md` now says). In the verify configuration (#474) a NaN bound is
  refused and an infinite one is accepted (it bounds nothing).
- #461 changes `Drift.path` for table-level changes: `rows` / `tables.<t>.rows` for the row count
  (was `tables.None` / `tables.<t>`) and `[tables.<t>.]joint.<label>[.<measure>]` for joint
  changes. Table added/removed and column paths are unchanged. Documented in `docs/DRIFT.md`.
- #464: a dataset contract may hold only `tables` and `drift` at its top level. The `tables`
  contract form is described in docs outside this lane's paths; no page there contradicts this.
- No existing test expectation was changed, no test skipped; no tolerance, gate or D-xx/T-xx
  touched. No output compared by the equivalence verifiers changed (drift, contracts and the
  verify gates are not verified outputs), so no verifier run was needed.

## Left open

- #481 (outside the area): `tests/builtins/test_cloud_sources.py` ids break `pytest -n`.
- `validation.invariants` stays (now tested): deleting it would leave an unused
  `[[tool.mypy.overrides]]` entry in `pyproject.toml`, which is outside this lane's paths.
- `shape.drift.policy`, `shape.quality.core`, `shape.quality.policy` are on the T-12 mypy ratchet
  list in `pyproject.toml`; `quality.core` and `quality.policy` now pass `mypy --strict`, so the lead
  can drop them from the list.
- No `.github/workflows` change is needed.

## Checks run (this session)

Environment: Python 3.11, `pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
-r tests/demo/fabric/requirements.txt`, unixODBC; no `$SPINDLE_ROOT` checkout.

- `ruff check src tests plugins benchmarks/vs_spindle`: all checks passed.
- `ruff format --check src tests plugins benchmarks/vs_spindle`: 1093 files already formatted.
- `mypy`: no issues in 436 source files.
- `python scripts/check_user_facing.py`: clean.
- `pytest -m "not emulator and not live"`, before the lane (5c91ea5, rust): 7078 passed, 6 failed.
- Same on the lane head, `SHAPE_KERNEL=rust`: 7167 passed, 7 failed; `SHAPE_KERNEL=python`: 7168
  passed, 6 failed. The 6 fail identically before the lane and are environmental, outside this
  area: `tests/kernel/test_hashing.py` (2, float16 with the pyarrow 19 that the fabric test
  requirements install), `tests/demo_cmd/test_notebook_and_outputs.py` (2, needs the shape-fabric
  plugin), `tests/iss_gaps/test_landing_and_batches.py` (1, pyarrow 19 reads the partition column
  back), `tests/security/test_credential_refs.py` (1, `azure-functions` is installed by those
  requirements). The 7th in the rust run, `tests/profile/test_engine.py::
  test_bounded_mode_memory_does_not_grow_with_rows`, measures peak memory and ran while the
  python-kernel suite ran beside it; alone it passes (rust: 1 passed).
- Coverage of the area (rust run): 94% before, 96% after (`validation.invariants` and
  `validation.statistics` 0%/30% to 100%).
- `origin/build/main-plan` had no new commits to merge at the end of the lane.
