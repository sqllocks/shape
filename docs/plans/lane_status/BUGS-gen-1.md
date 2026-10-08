# BUGS-gen-1: issues #219 and #312 (lane/BUGS-gen-1)

Status: **#219 fixed; #312 fixed in part, the rest escalated to the lead (below).** Branch started
from `build/main-plan` 5c91ea5. No gate, tolerance, D-xx or T-xx decision was changed; no test was
skipped, xfailed or had its expectation changed. No `.github/workflows` file was edited. The issues
were not commented on or closed.

## #219: seasonal `temporal` and the end day; a list profile

Reproduced on this branch before the fix (`tests/generation/test_bugs_gen_temporal.py`, commit
03f2ac6, failing output in its message): January 2024 with `profiles.month {"Feb": 11}`, 31,000 rows,
day 31 drew 7 rows and the other days about 1,000; `profiles.month: [10, 1, ...]` raised
`AttributeError: 'list' object has no attribute 'get'`.

Fix (commit f9346f2, `src/shape/builtins/strategies/temporal.py`):
- `_day_weights` spreads the probability of the (month, weekday) buckets with no day in the range
  evenly over every day, the end day included (it used to leave the last day out).
- `profiles`, `month`, `day_of_week` and `hour_of_day` must be mappings, and every weight a number;
  otherwise a `StrategyError` that names `table.column`. The existing weight errors now name the
  column too. Unknown month names stay with #149.
- Bytes: a range that covers every (month, weekday) bucket has no empty mass, so its output is
  unchanged. Retail small at seed 42 hashes the same before and after
  (`ae99590c…`); every seasonal column in the shipped schema and the fixture schemas spans at least a
  year. The baseline equivalence cases (`test_strategies_p404c.py`, which includes
  `temporal/seasonal_short_range`, the one case with empty buckets) pass.
- Docs: `docs/GENERATION_STRATEGIES.md` (`temporal`), CHANGELOG (Fixed).

## #312: the streaming demo's data

Reproduced: `shape demo run retail --mode streaming --max-events 5` prints `Liiel`, `Niee`, `Cenor`,
`Nguland`, `DaNeil`, `Marcus` with `"gender":"F"`, and `"is_active":"true"`. `shape demo run
healthcare ...` cannot run on this repository: only the `retail` domain ships
(`no domain named 'healthcare' (installed: retail)`), so `bed_count` was not reproduced here.

Fixed (`src/shape/demo/modes/streaming.py`, regression test
`tests/demo_cmd/test_bugs_gen_streaming.py`, commit b5916a1 with the failing output; fix 6896a38): the streaming
mode streams the first table, in dependency order, whose rows have an event time (a date or
timestamp column), so its events are in event-time order and carry `_shape_event_time`, as
`docs/DEMO.md` says; only when no table has one does it stream the first table, in row order. Retail
still streams `customer` (it has `signup_date`). `docs/DEMO.md` updated.

### Escalated to the lead (not changed: each needs a decision this lane may not take)

1. **Names** (`Liiel`, `DaNeil`, ...). They are entries of `src/shape/builtins/strategies/pools/
   first_names.txt` and `last_names.txt`, which are the baseline's own pools: 
   `tests/generation/test_strategies_p404b.py::test_shape_pools_are_the_baselines` pins them, the
   strategy fixtures hash them (`"first_names": "183a523e…"`), and T-21 (e) measures `first_name`
   against the baseline's output plus its pools. Replacing them is changing an existing test's
   expectation. Proposal, the pattern of owner issues 11 and 12: a curated pool of real given and
   family names becomes the default, the baseline pool stays behind an opt-in that the equivalence
   cases use, and `retail customer.first_name`/`last_name` get a narrow entry in
   `domain_1to1/domain_differences.py` (only `vocab` may fail, and only if every value is in the new
   pool). The curated list needs a source whose licence allows shipping (`scripts/check_shipped_data.py`).
2. **Gender consistent with the first name.** `customer.gender` is an independent `weighted_enum`
   (M 0.49, F 0.51) and the pools carry no gender. It needs gendered name pools (item 1) and a new
   dependency between the two columns in the retail domain; its TVD stays comparable under T-21 (d)
   only if the pools keep the 0.49/0.51 split.
3. **`is_active` as text.** The retail domain declares `is_active` `type: string` with values
   `"true"`/`"false"`. T-21 (a) requires the baseline's Arrow types exactly, so a boolean column is a
   change to a fixed decision. Proposal: leave the domain, and if wanted, add a deliberate
   type difference with its own rule to the harness (owner decision).
4. **`bed_count` fractional; healthcare `facility`.** The healthcare domain is not in this repository,
   so neither can be fixed or tested here. With the fix above, a healthcare run would stream its first
   table that has an event time instead of `facility`.

## Checks run in this session

Environment: fresh venv (`pip install -e '.[dev,streaming,advanced]'` plus every plugin under
`plugins/`); `tests/demo/fabric` in its own venv (`.[dev]` + `tests/demo/fabric/requirements.txt`,
unixODBC installed), as CI does, because those requirements pin pyarrow 19.0.1.

- `make check` (ruff, format, mypy, compileall, vulture, lint-imports, requirement, secret,
  user-facing (D-13), shipped-data, skeleton and conformance checks; tests with coverage; heavy;
  python kernel; cargo fmt, clippy, test): **exit 0**. 6801 passed (coverage 92.62%), heavy 42
  passed, `SHAPE_KERNEL=python tests/kernel` 265 passed, cargo 34 passed.
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric`: **6881
  passed, 0 failed**.
- `tests/demo/fabric` (its venv): 216 passed with `SHAPE_KERNEL=rust`, 216 passed with
  `SHAPE_KERNEL=python`.
- `SHAPE_KERNEL=python pytest -m "not emulator and not live" --ignore=tests/demo/fabric`: **6881
  passed, 0 failed** (1 h 44 min).
- `build/main-plan` had not moved since 5c91ea5 (fetched), so there was nothing to merge.
- Not run: the vs-baseline verifiers that need the pinned baseline checkout (not present in this
  container). The retail domain's bytes are unchanged by the #219 fix (hash above) and retail's
  streamed table is unchanged by the #312 fix, so the domain and demo verifiers see the same output.
