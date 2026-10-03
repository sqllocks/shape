# AUD-builtins: audit of the built-in generators, domains and providers

Lane `lane/AUD-builtins`, started from `int/INT-15` (f99563e). Brief: `docs/plans/AUDIT_BRIEF.md`.

Scope: `src/shape/builtins/` with the focus the area names (generators, domains, providers):
`strategies/` (and its pools), `distributions/`, `calendars/`, `_rng.py`, `catalog.py`,
`dimensional.py`, `detectors/`, `fitters/`, `chaos/` and `emitters/` were read in full and
probed. `sinks/`, `sources/`, `transforms/` and `reports/` are I/O, masking and reporting
code with their own docs (`SINKS.md`, `MASK.md`); they were not audited in depth here.

## Phase 1: findings

Reproductions use the engine on a one-table schema (`Engine(GenSchema.from_dict(doc))`); the
script is the regression test named in each fix commit.

| # | Sev | Where | Defect |
|---|---|---|---|
| 1 | high | `strategies/temporal.py:189-212` | `temporal` with `pattern: seasonal` (month, weekday or hour profiles) ignores the time of day of `start` and of an exclusive `end`: values fall before the start and after the end |
| 2 | medium | `distributions/families.py:796-813` | `truncated` fails for ordinary intervals (any table larger than about 1/(1-p)^64 rows), and its early "almost none" error depends on the chunk size |
| 3 | medium | `strategies/reference_hierarchy.py:69-88` | the hierarchy sampler cache is keyed by `id(dataset)`: after a dataset is re-registered, a reused id returns the old dataset's sampler (wrong records, or a subset of them) |
| 4 | medium | `strategies/numeric.py:139-143` | `empirical` with `interpolation: cubic` leaves the outermost anchors (doc: "values never leave the outermost anchors") |
| 5 | medium | `strategies/providers.py:328-333` | `native`/`faker` provider `digits` with `width` 16 to 18 is not uniform: the last digits come from a float with 53 bits (last digit only even for width 18) |
| 6 | medium | `strategies/temporal.py:229-238` | `hour_of_day` keys are matched as text: `"00"`, `"07"` are dropped silently and the weight goes to the other hours |
| 7 | low | `calendars/rules.py:27-37`, `:161-178` | `nth_weekday` with `n` past the month's last occurrence returns a day of the next month (5th Monday of Feb 2021 = 2021-03-01); `n` = 0 or < -1 silently means "last" |
| 8 | low | `calendars/effects.py:107-121` | `Payday(kind="monthly")` without `days` pays on the 1st and 15th; its docstring says the monthly default is the 28th |
| 9 | low | `strategies/formula.py:124-128`, `:146-169` | a formula that names a function as a value (`abs + x`) raises a bare `KeyError`; a deeply nested expression (`-` x 1500) raises `RecursionError`; neither names the column |
| 10 | low | several | spec errors that are not `StrategyError` and do not name the column: `constant` without `value`, `uniform`/`normal` without their keys (KeyError), `histogram`/`mixture` without keys (KeyError/TypeError), `lifecycle`/`weighted_enum`/`choice` with non-numeric or infinite weights (ValueError from the alias table), `digits` `width` not a number, `derived` `days` not a number, `address` unknown `field`, `pattern` width over the limit, `foreign_key` `fan_out` with list values (TypeError: unhashable) |
| 11 | low | `strategies/providers.py:357-372` | `faker` resolves any attribute of a Faker object as a provider: `seed_instance` silently yields nulls, dunder names are called |
| 12 | low | `distributions/families.py` `fit_family` | fitting a degenerate sample crashes with ZeroDivisionError/IndexError/numpy errors (gamma or beta on a constant sample, any family on an empty sample, triangular on one value) instead of `FamilyError` |
| 13 | low | `docs/GENERATION_STRATEGIES.md:174-176` | the `faker` section says every value is distinct while the table fits the pool; pool entries repeat (`color_name`: 135 distinct of 500) |
| 14 | low | `strategies/temporal.py:39-44` | a time-zone-aware `start`/`end` is converted to UTC through numpy's deprecated path (UserWarning "no explicit representation of timezones"); the conversion is not documented |
| 15 | low | `_rng.py` | dead code: `chunk_generator` is used nowhere and keys a stream by chunk, which the strategy rules forbid |
| 16 | low | `strategies/basic.py`, `__init__.py:35-40` | `sequence` overflows int64 silently (start 2**62, step 2**62 wraps to negative keys) |
| 17 | low | `calendars/effects.py:233-235` | `Trend(annual_growth=nan)` is accepted and gives NaN factors |
| 18 | low | `calendars/composite.py:95-99` | `RuleCalendar.factors(start, end)` with `end < start` raises numpy's "negative dimensions" (only `lift` checks the range) |
| 19 | low | `strategies/temporal.py:60-71` | unknown `month`/`day_of_week` profile keys (`January`) are ignored silently: the profile becomes uniform |

Details:

1. `{"strategy": "temporal", "pattern": "seasonal", "start": "2024-01-01T12:00:00", "end":
   "2024-01-03T06:00:00", "profiles": {"month": {"Jan": 1}}}`, 1000 rows: min 2024-01-01
   00:02:51, max 2024-01-03 23:24:07. Expected: every value in [start, end). Same with only an
   `hour_of_day` profile (min 2024-01-01 10:00:09, max 2024-01-03 20:59:37). The `uniform`
   pattern is correct. `shape learn` writes exactly this form (`start`/`end` from the profile's
   min and max timestamps, seasonal with an hour profile), so learned schemas produce timestamps
   outside the observed range.
2. `{"distribution": "truncated", "base": "normal", "base_params": {"mean": 0, "std_dev": 1},
   "low": 1.3, "high": 10}` (9.7% of the mass) at 20,000 rows: `StrategyError: the truncation
   interval holds too little of the distribution`. Each row gets 64 attempts, so (1-p)^64 of the
   rows fail. With `chunk_rows=1`, 50 rows fail at attempt 16 ("almost none") because the check
   is "more than 90% of the chunk still pending". Expected: the exact truncated distribution
   (doc), the same result for any chunking.
3. Register a 1-row dataset, sample, unregister, register a 4-row dataset with the same name,
   repeat: after an `id()` is reused, the 4-row dataset draws only `S0` (reproduced at iteration
   155 of 300).
4. Quantiles 0 (p1..p90), 100 (p95), 101 (p99), cubic: values from -46.4 to 117.7.
5. 20,000 rows of `digits` width 18: last-digit counts `[3972, 15, 4009, 20, 3950, 11, 3960, 19,
   4027, 17]`; 435 distinct last-three-digit values of 1000.
6. `profiles.hour_of_day = {"00": 1, "5": 0.0001}`: every value at 05:xx.
7. `rule_from_spec({"month": 2, "weekday": "mon", "n": 5}).on(2021)` is 2021-03-01; expected no
   date that year (the rule has none) and a clear error for `n` outside 1..5 and -1.
8. `Payday(1.5, "monthly")._dates(2024-03-01, 2024-03-31)` gives the 1st and 15th.
9. `{"strategy": "formula", "expression": "abs + x"}`: `KeyError: 'abs'`.
10. Each raises the listed Python error; expected `StrategyError` naming `table.column`
    (strategy rule 4).
11. `{"strategy": "faker", "provider": "seed_instance"}` gives null rows; `__class__` builds a
    Faker. Expected: only public provider methods; anything else is "unknown faker provider".
12. `fit_family("gamma", [0.5] * 10)`: ZeroDivisionError.
16. Expected: a `StrategyError` when the last row's value does not fit int64.

## Phase 2: issues

(filled in below as issues are filed)

## Phase 3: fixes

(filled in below)
