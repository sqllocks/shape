# Testing your rules: mutation testing and backtesting

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


A contract (`shape check`, contract format v1) can look strict and still miss the faults that
matter, and a new rule cannot be tried against the past before it is enforced. Two commands
answer those questions:

* `shape rules mutate` plants known faults in your data and reports which rules catch them.
* `shape rules backtest` replays a contract over every committed version of a registry name.

Neither changes your data, your contract or your registry.

## `shape rules mutate`

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`DATA` is a data file or a folder of them (the files `shape profile` reads; one table per file).
Each **mutant** is one corruption of `shape chaos` (`duplicates`, `orphan_keys`, `date_shift`,
`negative_amounts`, `case_whitespace`, `pii_fill`, `type_change`, `null_creep`; see `CHAOS.md`)
applied to one table and column, at `--rate` (default 0.05, the share of rows changed). By default
every applicable corruption of every table and column is a mutant; `--plan` names the ones to run.

Each mutant is profiled in memory and checked against the contract, rule by rule. With `--diff`,
or when the contract has a `drift` section, it is also compared with the unmutated profile
(`shape.diff`) under the contract's drift policy. A mutant is **killed** when at least one rule
fails or one drift change is reported; it **survived** otherwise. The same `--seed` gives the same
report.

* A mutant whose ground-truth log is empty changed nothing (no positive amount to flip, a rate of
  0, ...). It is `not_applicable` and is left out of the score.
* A rule that already fails on the **unmutated** data cannot show that a mutant was caught, so it is
  not allowed to kill; the report lists it under `baseline_failed_rules`.
* A rule the profile cannot evaluate is never counted as a catch.

### The mutation score

The score is killed mutants divided by applicable mutants, overall, per corruption kind and per
table. An empty contract scores 0. The text output lists the surviving mutants first, then the
killed ones, the scores, and the rules that killed no mutant. `--min-score S` (0 to 1) exits 1 when
the score is below S.

Rules are named `table.column.rule` (`orders.order_id.unique`); a rule without a column is
`table.rule` (`orders.row_count.min`). A drift change is named `drift:KIND`
(`drift:null_rate_change`).

Exit codes: 0 after a report (and when `--min-score` is met), 1 when the score is below
`--min-score`, 2 for input Shape cannot use: missing data, an unreadable contract, an unknown
corruption kind, a plan that names a missing table or column, data with nothing to corrupt.

### The plan file

```json
{
  "format": "shape-mutation-plan",
  "version": 1,
  "corruptions": [
    {"kind": "null_creep", "table": "orders", "column": "status", "rate": 0.2},
    {"kind": "duplicates", "table": "orders"}
  ]
}
```

`kind` is required. `table` and `column` are optional: without them the kind runs on every
table or column it applies to. `rate` defaults to `--rate`. `duplicates` works on a whole table,
so its `column` is ignored. A newer `version` than this Shape reads is refused. Schema:
`src/shape/schemas/shape-mutation-plan-v1.schema.json`.

### The report

```json
{
  "format": "shape-mutation-report",
  "version": 1,
  "seed": 1, "rate": 0.05, "diff": false,
  "mutants": [
    {"id": "duplicates.order", "kind": "duplicates", "table": "order", "column": null,
     "rate": 0.05, "seed": 1, "cells_changed": 250, "status": "killed", "killed": true,
     "killed_by": ["order.order_id.unique"]}
  ],
  "score": {
    "overall": {"killed": 8, "applicable": 13, "score": 0.615385},
    "by_kind": {"duplicates": {"killed": 1, "applicable": 1, "score": 1.0}},
    "by_table": {"order": {"killed": 8, "applicable": 13, "score": 0.615385}}
  },
  "rules": {"order.order_id.unique": {"killed": ["duplicates.order"]}},
  "rules_killed_none": ["order.order_id.dtype"],
  "baseline_failed_rules": []
}
```

`status` is `killed`, `survived` or `not_applicable`; `cells_changed` comes from the ground-truth
log (a whole column for `type_change`); `score` is `null` when no mutant is applicable.
Schema: `src/shape/schemas/shape-mutation-report-v1.schema.json`.

### A worked example

Generate a domain and keep one table:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

A first contract for `data/order.csv`, `weak.json`, looks reasonable:

```json
{"columns": {"order_id": {"dtype": "integer", "nullable": false},
             "order_total": {"dtype": "float"}}}
```

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Not one of the 13 faults is caught. Adding one rule, `"unique": true` on `order_id`, kills the
`duplicates` mutant and lifts the score to 7.7% (1 of 13). Rules that match what each surviving
mutant breaks do the rest (`better.json`):

```json
{"columns": {
  "order_id": {"dtype": "integer", "nullable": false, "unique": true},
  "status": {"allowed_values": ["completed", "returned", "cancelled", "processing", "shipped"],
             "nullable": false},
  "order_total": {"dtype": "float", "min": 0, "nullable": false},
  "order_date": {"nullable": false}}}
```

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

The survivors are the finding. A contract has no cross-table rule, so orphan foreign keys pass a
per-table check; and the dtype and nullable rules of `order_id` never fire because the mutants do
not touch that column. With `--diff` (or a `drift` section in the contract) the comparison with
the unmutated profile catches the remaining five, and the score is 100.0%.

## `shape rules backtest`

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Replays the contract over every committed version of `NAME` in a registry (`shape registry`,
`REGISTRY.md`), oldest first. A version's date is its `business_date` metadata
(`shape registry ROOT commit NAME FILE --business-date 2026-03-01`), else its commit date (UTC).
`--since` and `--until` (inclusive) limit the versions.

Each version is read as `shape check` reads it: a raw profile (a `.shape` file) is loaded and
checked. A share-safe profile keeps fewer statistics, so a rule that needs one it does not hold
(`unique`, `min`, `max`, `nullable`, `allowed_values`, ...) is **not measured**. A rule that was
not measured is counted apart; it is never a pass. A version that is not a profile at all is
`not_measured` with a reason, and the replay goes on.

An entry is `fail` when a measured rule fails, `pass` when every rule was measured and passed, and
`not_measured` when none failed but some could not be measured.

### Windows

With `--window week` (ISO weeks, Monday to Sunday) or `--window month`, the versions of each
window are merged with `shape.profile.merge_profiles` (`PROFILE_MERGE.md`) and the contract runs
on the merged profile, without reading any data again. Exact statistics (row counts, null counts and
rates, minimum, maximum, dtype) give the same answer as a profile of the window's union. Statistics a
merge leaves unknown (patterns, distribution fits, allowed values, true rates, joint rules) are
`not_measured`; `unique` is measured only when every version of the window carries sketch state
(`shape profile --sketches`). A window that cannot be merged (a share-safe version, columns that
differ) is `not_measured` with the reason. A window with one version is still merged, so a rule is
measured the same way in every window. `--since` and `--until` apply to versions before they are
grouped.

### Incidents

`--incidents FILE` adds, per known incident, whether the replay caught it:

```json
{"format": "shape-incidents", "version": 1,
 "incidents": [{"id": "late-feed", "from": "2026-03-04", "to": "2026-03-09", "note": "nulls in note"},
               {"id": "one-day", "from": "2026-03-12"}]}
```

Dates are inclusive and `to` is optional (a one-day incident). An incident is **caught** when a
failing version or window falls inside it (a window counts when the dates of its versions overlap
the incident) and the first one is named; otherwise it is **missed**. Failing versions or windows
outside every incident are listed as `alarms_outside_incidents`. `--fail-on-miss` exits 1 when an
incident is missed. A feed made with `shape generate-drift` writes an answer key
(`ground_truth.json`, see `DRIFT.md`); converting its events (`start`, `end`) to incidents tests a
contract against the drift that was planted. Schema: `src/shape/schemas/shape-incidents-v1.schema.json`.

### Comparing two contracts

`--compare OLD_CONTRACT.json` runs the older contract on the same versions and adds `compare`: the
versions or windows on which the two disagree (a different status), with the failed rules of each.

### The report

`{"format": "shape-backtest-report", "version": 1, ...}` holds `summary` (counts of `pass`, `fail`
and `not_measured`), `entries` (each version or window: `id`, `window`, `from`, `to`,
`first_date`, `last_date`, `versions` as content ids, `status`, rule counts, `failed_rules`,
`not_measured_rules` and, when there is one, `reason`), `rules` (per rule, how often it failed
and was not measured), and, when asked, `incidents`, `incident_summary`, `alarms_outside_incidents`
and `compare`. Text output prints the same. Schema:
`src/shape/schemas/shape-backtest-report-v1.schema.json`.

Exit codes: 0 after a report, 1 with `--fail-on-miss` when an incident is missed, 2 for input Shape
cannot use: an unknown name, no version in range, an unreadable contract or file, a bad date or
window, a contract that does not fit the stored profiles (a `tables` contract for a single table
or the reverse).

## Python API

```python
from shape.rules import mutation_test, backtest

result = mutation_test("data/order.csv", "better.json", plan=None, seed=1, rate=0.05, diff=False)
result.to_dict()          # the mutation report
result.score              # killed / applicable, or None

result = backtest("registry/", "orders", "contract.json", since=None, until=None,
                  window="day", incidents="incidents.json", compare=None)
result.to_dict()          # the backtest report
result.missed             # ids of the incidents no failing entry fell into
```

`data` may also be an Arrow table or a dict of Arrow tables; contracts, plans and incidents may be
dicts or file paths. Unusable input raises `shape.rules.mutation.MutationError` or
`shape.rules.history.BacktestError` (both are `ValueError`).

## Persisted formats

The plan, the mutation report, the incidents file and the backtest report each declare `format`
and an integer `version`. A reader accepts every version up to the one it knows and refuses a newer
one with an error; the frozen version-1 documents in `tests/rules/data/` are checked against the
schemas in `src/shape/schemas/` by the test suite.
