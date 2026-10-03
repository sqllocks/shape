# AUD-quality: audit of quality, validation, contracts and drift

Lane branch `lane/AUD-quality` (from `build/main-plan` 5c91ea5). Area: `src/shape/quality/**`,
`src/shape/validation/**`, `src/shape/contracts/**`, `src/shape/drift/**`, their tests,
`docs/DRIFT.md`, `docs/VERIFY.md`.

Status: **phase 1 (hunt) done**; phases 2 and 3 below.

## Phase 1: findings

Every reproduction ran on this branch at 5c91ea5 with `pip install -e '.[dev,streaming,advanced]'
-e plugins/shape-domains`. "Issue" is the GitHub issue filed in phase 2; "Fix" the commit.

| # | Sev | Where | Defect |
|---|---|---|---|
| 1 | high | `quality/utility.py:73` | The utility gate crashes (`ArrowInvalid: Casting from timestamp[us] to timestamp[s] would lose data`) on any timestamp feature with sub-second values. |
| 2 | medium | `drift/core.py:59-61` | `shape.drift.compare` gives table-level changes of a single table (row count, every joint change) the path `tables.None`; in a dataset every joint change of a table shares the path `tables.<t>`, so `drift.gate` overrides cannot tell them apart. |
| 3 | medium | `contracts/v1.py:88-110,242,322` | A contract with a non-number `row_count.min/max` or `max_null_rate` passes validation and then crashes `shape.check` with `TypeError` (`'<' not supported between 'int' and 'str'`) instead of `ContractError`. |
| 4 | medium | `contracts/v1.py:223,226,337` | `nullable`, `unique` and `allow_extra_columns` given as anything but a JSON boolean (`"false"`, `"true"`, `0`) are silently not checked: the contract passes a rule it never tested. |
| 5 | medium | `contracts/v1.py:355-374` | A `tables` contract against a dataset silently ignores table rules at its top level (`{"tables": {...}, "row_count": {"min": 1000}}` passes); a sub-contract that is not an object raises `TypeError`; a nested `tables` in a sub-contract is ignored. |
| 6 | medium | `quality/gatespec.py:116-153` | Gate schema values are coerced, not validated: `"nullable": "false"` makes the column nullable (`bool("false")`), `"primary_key": "id"` and `"parent_columns": "id"` become `('i', 'd')` (checks nothing), an `enum` list or a `distribution` string crashes the distribution gate (`TypeError`/`AttributeError`), and `columns` as a list raises `AttributeError`. |
| 7 | medium | `quality/gates.py:187` | `referential_integrity` checks a composite foreign key column by column, so a child row `(1, 20)` passes when the parent has `(1, 10)` and `(2, 20)`; `parent_columns`/`child_columns` of different lengths are silently truncated (`zip(strict=False)`). |
| 8 | medium | `quality/gates.py:467-473` | A `no_future` entry whose table or column does not exist is skipped without a warning, contradicting `docs/VERIFY.md` ("a misspelt rule cannot be skipped silently"). |
| 9 | medium | `quality/gates.py:726-730` | The chi-squared enum test never matches an integer or boolean column (JSON object keys are text): it warns every declared value "is missing from data" and never tests. |
| 10 | medium | `drift/engine.py:559,629,778` | `thresholds={"min_rows": 0}` (allowed: "a number of 0 or more") with an empty column raises `ZeroDivisionError`. |
| 11 | low | `drift/engine.py:111-120,181-214` | Thresholds accept NaN and infinity (NaN silently turns a comparison off, docs say "a number of 0 or more"); a policy whose `columns` or `thresholds` is not an object raises `AttributeError`/`TypeError` instead of the documented `ValueError`. |
| 12 | low | `drift/engine.py:912` | Comparing documents with an empty `tables` object raises `ValueError: not enough values to unpack`, which does not say what is wrong. |
| 13 | low | `quality/gates.py:386-388` | `range_constraint` on a column with no numeric values (a timestamp or text column) checks nothing and says nothing. |
| 14 | low | `quality/verifyconfig.py:52-83` | The verify configuration accepts NaN or infinite range bounds (NaN checks nothing), `min` above `max`, and a `date_range` whose start is after its end. |
| 15 | medium | `quality/policy.py:38-71` | `validate_rows` (behind `shape quality`) crashes with `TypeError` on a value of the wrong type (`"x"` against `min 0`) or an unhashable value under `unique`; an unknown rule kind passes on empty input; `unique` on an iterator fails only after consuming it. |
| 16 | low | `quality/core.py:23-39` | `quality.evaluate` crashes with `TypeError` when a summary value cannot be compared with `min`/`max`. |
| 17 | low | `contracts/core.py:80,177` | `evaluate_contract` with `columns` that is not an object raises `AttributeError`; `compatibility(mode=...)` with a bad mode raises `ValueError("mode")`, which names no valid mode. |
| 18 | low | `drift/engine.py:337` | A naive ISO min/max is read in the machine's local time zone, so a column's span (which gates `day_of_week_change`) depends on `TZ` (13.979 days in UTC, 13.9375 in America/New_York for the same column). |
| 19 | low | `quality/verify.py:85` | `load_tables` on a directory: an entry such as a sub-directory named `a.csv` makes the whole load fail; files with upper-case extensions (`B.CSV`) are not loaded, although a single such file is. |
| 20 | low | `quality/verify.py:220-223` | The Markdown report's methodology says the distribution gate uses α=0.05 whatever `distribution_alpha` was configured. |
| 21 | low (cleanup) | `validation/fuzz.py:425`, `validation/invariants.py`, `validation/__init__.py:8-13` | Dead code: `fuzz._pick` and the whole `validation.invariants` module are never used or tested; `shape.validation` re-exports `dataclass` and `random` by accident. |
| 22 | low (tests) | `validation/statistics.py`, `quality/quarantine.py` | Uncovered paths: `validate_cardinality`, `validate_quantiles`, `relative_error` have no test. |
| 23 | low (outside area) | `tests/builtins/test_cloud_sources.py` | Parametrize ids embed gzip bytes with an mtime, so `pytest -n` (xdist) collection differs between workers. Filed only. |

Checked and found sound: quarantine path names (traversal refused), memorization null/NaN keys,
fuzz time limit, verify configuration unknown keys, contract joint rules, drift noise floors.
