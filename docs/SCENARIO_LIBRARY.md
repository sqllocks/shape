# The starter scenario library

Shape ships a small library of named, versioned scenarios, each with an **answer key**, and named
**suites** that run several of them and compare every outcome with its key. They are for CI: plant
a known problem in a built-in domain and check that your gates, or Shape's own, see it.

```bash
shape pack list --library                    # the scenarios and suites
shape pack run library:nulls_injected        # run one; exit 0 when it meets its answer key
shape suite run smoke                        # a suite; exit 0 when every scenario met its key
shape suite run schema-evolution --scale tiny --json
```

All the scenarios use the `retail` domain (the one built-in domain; `pip install
'sqllocks-shape[domains]'`). A scenario runs at the scale you give (a preset of the domain, such as
`fabric_demo` or `small`, or `tiny`: 100 rows per table) and with its own seed unless you pass
`--seed`. A scenario does not depend on the seed or the scale: its answer key holds at every one.

## The scenarios

| Scenario | What happens | The answer key |
|---|---|---|
| `clean_baseline` | retail as generated | no gate fails |
| `nulls_injected` | about 8% of `customer.last_name` is null though the column is not nullable | `null_check` fails, the other gates pass |
| `duplicate_rows` | `return` rows take the primary key of another return | `uniqueness` fails, the others pass |
| `orphaned_foreign_keys` | about 3% of `order.customer_id` point at no customer | `referential_integrity` fails, the others pass |
| `late_arriving_data` | about 10% of `order.order_date` are 45 days earlier than the rest of the batch | no gate fails; the late rows are planted |
| `schema_add_column` | `order.channel` appears on day 5 | the diff of day 0 and day 10 reports `order.channel` added |
| `schema_rename_column` | `order.status` becomes `order_status` on day 5 | the diff reports `order.status` removed and `order.order_status` added (one rename) |
| `schema_drop_column` | `store.state` disappears on day 5 | the diff reports it removed |
| `schema_retype_column` | `customer.is_active` turns from text into an integer on day 5 | the diff reports a `dtype_change` |
| `schema_evolution_schedule` | add on day 5, rename on day 10, drop on day 15, retype on day 20 | four diffs, each against day 0, report what has happened by then |

The library index, `library/index.json` in the package, holds a one-paragraph description of each.

## Scenarios for the failure mode catalog

W6-03 adds the scenarios that the [failure mode catalog](FAILURE_MODES.md) reproduces its entries
with. Each has an answer key like the starter ones. A data scenario plants defects in one batch; a
drift scenario is a change over time.

| Scenario | What it plants |
|---|---|
| `null_flood` | 60% of customer last names are null |
| `unit_change` | the last half of the order totals is 100 times larger |
| `truncated_strings` | 40% of customer emails are cut to five characters |
| `placeholder_values` | 20% of customer first names are `N/A` |
| `encoding_corruption` | 30% of customer emails end in the mojibake of a Latin-1 read |
| `volume_spike` | the order table has ten times its rows; keys repeat |
| `empty_load` | the order table has no rows |
| `partial_load` | the order table has one row |
| `out_of_order_events` | every order date is swapped with another's (no check sees it) |
| `timezone_offset` | every order date is eight hours later |
| `dst_boundary` | 30% of order dates sit on daylight-saving boundary hours |
| `concept_drift` | address cities are swapped between rows |
| `late_backfill` | 20% of order dates are 200 days earlier |
| `class_imbalance_shift` | from day 5 most orders are cancelled (drift) |
| `new_category_values` | from day 5 a new order status appears (drift) |
| `null_rate_creep` | the null share of customer last names climbs to 40% (drift) |
| `numeric_shift` | product unit prices are 1.8 times larger from day 5 (drift) |
| `detective_text_trouble`, `detective_renovations`, `detective_clocks_and_keys` | several defects at once, for the [detective packs](DETECTIVE.md) |

The data defects are built on the chaos mutators (`chaos_temporal`, `chaos_volume`) and on defect
kinds of their own (`defects.py`: `shuffle_column`, `scale_values`, `shift_hours`,
`truncate_strings`, `placeholder_values`, `corrupt_encoding`, `rename_column`, `drop_column`,
`retype_column`, `add_column`); the drift scenarios use `DriftPlan` events.

## The suites

| Suite | Scenarios |
|---|---|
| `smoke` | `clean_baseline`, `nulls_injected`, `duplicate_rows`, `orphaned_foreign_keys`, `schema_add_column` |
| `schema-evolution` | the five `schema_*` scenarios |
| `failure-modes` | the scenario of every entry of the failure mode catalog; the suite also checks that every check an entry names fires |

`smoke` is held to under 60 seconds at the `small` scale (a test enforces it), so it can run on
every CI run; run the
full library on a schedule (`shape suite run smoke` and `shape suite run schema-evolution`, or
`pytest` with the plugin's marker, `docs/TESTING_WITH_SHAPE.md`).

`shape suite run NAME|FILE [--scale small] [--seed N] [-o DIR] [--json]` runs every scenario of the
suite, compares each outcome with its key and prints one line per scenario; a scenario that missed
its key prints the scenario, what the key expected and what was observed. `-o DIR` writes each
scenario's tables under `DIR/<scenario>/` (a drift scenario writes `day_N/` folders); without it
nothing is written. `--json` prints the whole result. Exit codes:

| Code | Meaning |
|---|---|
| 0 | every scenario met its answer key |
| 1 | at least one did not |
| 2 | a malformed suite or answer key, or an unknown scenario, suite or scale (nothing ran) |

A suite names all its scenarios before anything runs, so one typo never costs a partial run.

## Formats

All four files declare `format` and an integer `version` (now 1). A file of another format, with no
version, with an unknown key, or from a newer Shape (a higher version than the one that reads it)
is refused with a message that names the file.

**Scenario**, `scenarios/<id>/scenario.json`:

```json
{"format": "shape-scenario", "version": 1, "id": "nulls_injected", "domain": "retail",
 "scale": "small", "seed": 42,
 "gates": ["schema_conformance", "referential_integrity", "row_count", "null_check", "uniqueness"],
 "defects": [{"kind": "inject_nulls", "table": "customer", "column": "last_name", "fraction": 0.08}]}
```

A scenario is a *data* scenario (`gates`, optional `defects`) or a *drift* scenario (`drift`, no
gates or defects). The gates are those of `docs/SCENARIO_PACKS.md`. The defects, planted
deterministically from the seed and the defect's position:

| `kind` | Keys | Plants |
|---|---|---|
| `inject_nulls` | `table`, `column`, `fraction` | nulls in that share of the rows (at least one) |
| `duplicate_keys` | `table`, `column` (default: the single-column primary key), `fraction` | the key of another row |
| `orphan_keys` | `table`, `column`, `fraction` | foreign key values no parent has |
| `late_arrivals` | `table`, `column`, `fraction`, `days` | timestamps `days` earlier |

A drift scenario is `"drift": {"plan": {...}, "compare": [[0, 10]]}`: a `DriftPlan`
(`docs/DRIFT.md`) and the pairs of days to compare. For each pair the scenario profiles the tables
the plan touches on both days and takes the changes `shape.diff` reports.

**Answer key**, `scenarios/<id>/expect.json`:

```json
{"format": "shape-scenario-expect", "version": 1, "scenario": "schema_rename_column",
 "gates_fail": [],
 "drift": [{"between": [0, 10], "changes": [
   {"column": "order.status", "kinds": ["column_removed"]},
   {"column": "order.order_status", "kinds": ["column_added"]}]}]}
```

* `gates_fail` (required, may be empty): these gates must fail and every other gate of the scenario
  must pass. A name the scenario does not check is an error.
* `defects`: `{"inject_nulls": {"min": 1}}`: at least that many rows of the defect were planted.
* `drift`: for each window, the changes the diff must report: `column` is `table.column`, and any
  one of `kinds` counts. Any *structural* change the diff reports in the window (`column_added`,
  `column_removed`, `dtype_change`) that no entry covers is a mismatch too. Other kinds, such as a
  small shift in a distribution, are not checked.

**Library index**, `index.json`: `{"format": "shape-scenario-library", "version": 1, "scenarios":
[{"id", "domain", "description"}, ...]}`. **Suite**: `{"format": "shape-suite", "version": 1,
"name", "description", "scenarios": ["clean_baseline", ...]}`; `shape suite run FILE` takes your
own file, whose scenarios are library names.

## Adding a scenario

1. Create `src/shape/scenario/library/scenarios/<id>/` with `scenario.json` and `expect.json`.
   Write the key by hand, from what the scenario is meant to show, not from a run.
2. Add the scenario to `index.json` with a one-paragraph description, and to the suites it belongs
   in (`suites/*.json`).
3. Run `pytest tests/scenario/test_library.py`: every scenario must meet its key at its own scale
   and at `tiny`, and a drift key must agree with what the plan's own `expected_changes` says.

A scenario that does not meet its key is a bug in the scenario, the key or Shape; do not loosen the
key to make it pass.
