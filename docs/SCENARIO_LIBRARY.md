# The starter scenario library

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" SCENARIO_LIBRARY
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for SCENARIO_LIBRARY
    ```


Shape ships a small library of named, versioned scenarios, each with an **answer key**, and named
**suites** that run several of them and compare every outcome with its key. They are for CI: plant
a known problem in a built-in domain and check that your gates, or Shape's own, see it.

[Run this example](#local-example-0).


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
every CI run. The two built-in suites together do not cover `late_arriving_data`, so the full
library is run from a suite file that lists every scenario of `library/index.json`. From a checkout,
`python scripts/build_library_suite.py library-suite.json` writes it and
`python -m shape suite run library-suite.json --scale small` runs it (the nightly workflow does
this in both kernel modes, and a test checks that every index entry is covered). The plugin's marker
runs one scenario inside pytest, `docs/TESTING_WITH_SHAPE.md`.

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


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape pack list --library                    # the scenarios and suites
shape pack run library:nulls_injected        # run one; exit 0 when it meets its answer key
shape suite run smoke                        # a suite; exit 0 when every scenario met its key
shape suite run schema-evolution --scale tiny --json
```

??? info "Output (exit 0)"

    ```text {.expected}
    domain    id                          description
    retail    clean_baseline              Retail generated as is: every validation gate passes
    retail    nulls_injected              About 8% of customer last names are null although the column is not nullable: the null check must fail and nothing else may
    retail    duplicate_rows              Return rows take over the primary key of another return, so keys repeat: the uniqueness gate must fail; the references into the table stay intact
    retail    orphaned_foreign_keys       About 3% of orders point at a customer that does not exist: the referential integrity gate must fail
    retail    late_arriving_data          About 10% of orders carry an order date 45 days before the rest of the batch, as when a late feed lands behind newer data
    retail    schema_add_column           A column appears on a schedule: orders gain a channel on day 5
    retail    schema_rename_column        A column is renamed on a schedule: order status becomes order_status on day 5
    retail    schema_drop_column          A column disappears on a schedule: stores lose their state on day 5
    retail    schema_retype_column        A column changes type on a schedule: the customer active flag turns from the text true or false into an integer on day 5
    retail    schema_evolution_schedule   All four schema changes on one schedule: a column is added on day 5, one renamed on day 10, one dropped on day 15 and one retyped on day 20
    retail    null_flood                  About 60% of customer last names are null although the column is not nullable: far beyond a stray missing value, as when a source column stops being filled
    retail    unit_change                 From the middle of the batch on, order totals are 100 times larger, as when a feed switches from dollars to cents
    retail    truncated_strings           About 40% of customer emails are cut to five characters, as when a column is loaded into a narrower one
    retail    placeholder_values          About 20% of customer first names are the text N/A, as when a form fills a required field with a stand-in
    retail    encoding_corruption         About 30% of customer emails end in the characters that UTF-8 text turns into when it is read as Latin-1
    retail    volume_spike                The order table carries ten times as many rows as it should, as when a retry loop re-sends a batch
    retail    empty_load                  The order table arrives with no rows, as when an upstream job succeeds without writing
    retail    partial_load                The order table arrives with a single row, as when a job is cut short
    retail    out_of_order_events         Every order date is swapped with another order's, so the dates no longer follow the order in which the rows arrived
    retail    timezone_offset             Every order date moves eight hours later, as when a writer starts to emit UTC instead of local time
    retail    dst_boundary                About 30% of order dates are moved onto the hours around the daylight-saving changes of 2024 and 2025
    retail    concept_drift               Customer addresses keep their cities and states, but the cities are swapped between rows, so a city no longer implies its state
    retail    class_imbalance_shift       Orders that were mostly completed become mostly cancelled from day 5: the mix of order statuses moves, and no value is new
    retail    new_category_values         A status that no order had before, lost, appears on day 5 for 15% of the orders
    retail    null_rate_creep             The share of customers without a last name climbs to 40% over ten days, starting on day 5
    retail    numeric_shift               Product unit prices are 1.8 times larger from day 5, as when prices are raised or a unit changes
    retail    late_backfill               About 20% of orders carry an order date 200 days before the rest of the batch, as when a backfill lands in the live table
    retail    detective_text_trouble      Customer emails end in the characters that UTF-8 text turns into when it is read as Latin-1, and half of the customer last names are cut to three characters
    retail    detective_renovations       Four schema changes at once, one in each of four tables: order status is renamed, store state is dropped, the customer active flag turns into an integer and products gain a channel
    retail    detective_clocks_and_keys   Order dates arrive 200 days late for 20% of the rows and every date is eight hours off; return ids repeat; address cities are swapped between rows
    suites: failure-modes, schema-evolution, smoke (shape suite run NAME)
    nulls_injected (retail, scale small, seed 42)
      gate schema_conformance: PASS
      gate referential_integrity: PASS
      gate row_count: PASS
      gate null_check: FAIL (customer.last_name has nulls)
      gate uniqueness: PASS
      defect inject_nulls: 80 rows
    answer key: met
    suite smoke (scale small)
      ok   clean_baseline (0.5s)
      ok   nulls_injected (0.1s)
      ok   duplicate_rows (0.1s)
      ok   orphaned_foreign_keys (0.1s)
      ok   schema_add_column (0.5s)
    5 of 5 scenarios met their answer key
    {"met": true, "scale": "tiny", "scenarios": [{"met": true, "mismatches": [], "outcome": {"defects": {}, "domain": "retail", "drift": [{"between": [0, 10], "changes": [{"column": "order.(rows)", "kind": "implausible_rate_change"}, {"column": "order.channel", "kind": "column_added"}, {"column": "order.customer_id", "kind": "cardinality_change"}, {"column": "order.customer_id", "kind": "mean_shift"}, {"column": "order.customer_id", "kind": "new_categorical_values"}, {"column": "order.customer_id", "kind": "spread_change"}, {"column": "order.customer_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "order.customer_id ~ store_id", "kind": "association_shift"}, {"column": "order.order_date", "kind": "day_of_week_change"}, {"column": "order.order_date", "kind": "hour_of_day_change"}, {"column": "order.order_date", "kind": "placeholder_surge"}, {"column": "order.order_total", "kind": "spread_change"}, {"column": "order.promotion_id ~ status", "kind": "association_shift"}, {"column": "order.shipping_address_id", "kind": "distribution_change"}, {"column": "order.shipping_address_id", "kind": "distribution_shift"}, {"column": "order.shipping_address_id", "kind": "mean_shift"}, {"column": "order.shipping_address_id", "kind": "null_rate_change"}, {"column": "order.shipping_address_id ~ order_total", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ store_id", "kind": "association_shift"}, {"column": "order.status", "kind": "category_shift"}, {"column": "order.status ~ order_total", "kind": "association_shift"}, {"column": "order.store_id", "kind": "new_categorical_values"}, {"column": "order.store_id ~ customer_id", "kind": "association_shift"}, {"column": "order.store_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.store_id ~ shipping_address_id", "kind": "association_shift"}]}], "elapsed_seconds": 0.496, "files": [], "gate_messages": {}, "gates": {}, "scale": "tiny", "scenario": "schema_add_column", "seed": 42}, "scenario": "schema_add_column"}, {"met": true, "mismatches": [], "outcome": {"defects": {}, "domain": "retail", "drift": [{"between": [0, 10], "changes": [{"column": "order.(rows)", "kind": "implausible_rate_change"}, {"column": "order.customer_id", "kind": "cardinality_change"}, {"column": "order.customer_id", "kind": "mean_shift"}, {"column": "order.customer_id", "kind": "new_categorical_values"}, {"column": "order.customer_id", "kind": "spread_change"}, {"column": "order.customer_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "order.customer_id ~ store_id", "kind": "association_shift"}, {"column": "order.order_date", "kind": "day_of_week_change"}, {"column": "order.order_date", "kind": "hour_of_day_change"}, {"column": "order.order_date", "kind": "placeholder_surge"}, {"column": "order.order_status", "kind": "column_added"}, {"column": "order.order_total", "kind": "spread_change"}, {"column": "order.shipping_address_id", "kind": "distribution_change"}, {"column": "order.shipping_address_id", "kind": "distribution_shift"}, {"column": "order.shipping_address_id", "kind": "mean_shift"}, {"column": "order.shipping_address_id", "kind": "null_rate_change"}, {"column": "order.shipping_address_id ~ order_total", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ store_id", "kind": "association_shift"}, {"column": "order.status", "kind": "column_removed"}, {"column": "order.store_id", "kind": "new_categorical_values"}, {"column": "order.store_id ~ customer_id", "kind": "association_shift"}, {"column": "order.store_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.store_id ~ shipping_address_id", "kind": "association_shift"}]}], "elapsed_seconds": 0.204, "files": [], "gate_messages": {}, "gates": {}, "scale": "tiny", "scenario": "schema_rename_column", "seed": 42}, "scenario": "schema_rename_column"}, {"met": true, "mismatches": [], "outcome": {"defects": {}, "domain": "retail", "drift": [{"between": [0, 10], "changes": [{"column": "store.state", "kind": "column_removed"}]}], "elapsed_seconds": 0.082, "files": [], "gate_messages": {}, "gates": {}, "scale": "tiny", "scenario": "schema_drop_column", "seed": 42}, "scenario": "schema_drop_column"}, {"met": true, "mismatches": [], "outcome": {"defects": {}, "domain": "retail", "drift": [{"between": [0, 10], "changes": [{"column": "customer.is_active", "kind": "dtype_change"}, {"column": "customer.is_active", "kind": "true_rate_change"}]}], "elapsed_seconds": 0.135, "files": [], "gate_messages": {}, "gates": {}, "scale": "tiny", "scenario": "schema_retype_column", "seed": 42}, "scenario": "schema_retype_column"}, {"met": true, "mismatches": [], "outcome": {"defects": {}, "domain": "retail", "drift": [{"between": [0, 7], "changes": [{"column": "order.(rows)", "kind": "implausible_rate_change"}, {"column": "order.channel", "kind": "column_added"}, {"column": "order.customer_id", "kind": "new_categorical_values"}, {"column": "order.customer_id ~ order_date", "kind": "association_shift"}, {"column": "order.customer_id ~ order_total", "kind": "association_shift"}, {"column": "order.customer_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.customer_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "order.customer_id ~ status", "kind": "association_shift"}, {"column": "order.customer_id, store_id -> shipping_address_id", "kind": "dependency_broken"}, {"column": "order.order_date", "kind": "day_of_week_change"}, {"column": "order.order_date", "kind": "hour_of_day_change"}, {"column": "order.order_total", "kind": "new_categorical_values"}, {"column": "order.order_total", "kind": "placeholder_surge"}, {"column": "order.order_total ~ promotion_id", "kind": "association_shift"}, {"column": "order.order_total ~ shipping_address_id", "kind": "association_shift"}, {"column": "order.order_total ~ store_id", "kind": "association_shift"}, {"column": "order.promotion_id ~ customer_id", "kind": "association_shift"}, {"column": "order.shipping_address_id", "kind": "mean_shift"}, {"column": "order.shipping_address_id", "kind": "null_rate_change"}, {"column": "order.shipping_address_id -> customer_id", "kind": "dependency_broken"}, {"column": "order.shipping_address_id ~ order_date", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ order_total", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ store_id", "kind": "association_shift"}, {"column": "order.status ~ order_total", "kind": "association_shift"}, {"column": "order.status ~ shipping_address_id", "kind": "association_shift"}, {"column": "order.store_id", "kind": "new_categorical_values"}, {"column": "order.store_id ~ customer_id", "kind": "association_shift"}, {"column": "order.store_id ~ order_date", "kind": "association_shift"}, {"column": "order.store_id ~ order_total", "kind": "association_shift"}, {"column": "order.store_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.store_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "store.city ~ state", "kind": "association_shift"}, {"column": "store.state", "kind": "new_categorical_values"}, {"column": "store.store_type ~ state", "kind": "association_shift"}]}, {"between": [0, 12], "changes": [{"column": "order.channel", "kind": "column_added"}, {"column": "order.customer_id", "kind": "new_categorical_values"}, {"column": "order.customer_id ~ order_date", "kind": "association_shift"}, {"column": "order.customer_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "order.order_date", "kind": "cardinality_change"}, {"column": "order.order_date", "kind": "day_of_week_change"}, {"column": "order.order_date", "kind": "hour_of_day_change"}, {"column": "order.order_date", "kind": "placeholder_surge"}, {"column": "order.order_status", "kind": "column_added"}, {"column": "order.promotion_id ~ order_date", "kind": "association_shift"}, {"column": "order.shipping_address_id", "kind": "cardinality_change"}, {"column": "order.shipping_address_id", "kind": "category_shift"}, {"column": "order.shipping_address_id", "kind": "new_categorical_values"}, {"column": "order.shipping_address_id", "kind": "null_rate_change"}, {"column": "order.shipping_address_id", "kind": "outlier_rate_change"}, {"column": "order.shipping_address_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ store_id", "kind": "association_shift"}, {"column": "order.status", "kind": "column_removed"}, {"column": "order.store_id", "kind": "new_categorical_values"}, {"column": "order.store_id ~ order_total", "kind": "association_shift"}, {"column": "order.store_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.store_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "store.state", "kind": "new_categorical_values"}]}, {"between": [0, 17], "changes": [{"column": "order.channel", "kind": "column_added"}, {"column": "order.customer_id", "kind": "cardinality_change"}, {"column": "order.customer_id", "kind": "new_categorical_values"}, {"column": "order.customer_id ~ order_date", "kind": "association_shift"}, {"column": "order.customer_id ~ order_total", "kind": "association_shift"}, {"column": "order.customer_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.customer_id ~ store_id", "kind": "association_shift"}, {"column": "order.order_date", "kind": "cardinality_change"}, {"column": "order.order_date", "kind": "day_of_week_change"}, {"column": "order.order_date", "kind": "hour_of_day_change"}, {"column": "order.order_status", "kind": "column_added"}, {"column": "order.order_total", "kind": "new_categorical_values"}, {"column": "order.order_total", "kind": "spread_change"}, {"column": "order.promotion_id ~ customer_id", "kind": "association_shift"}, {"column": "order.promotion_id ~ order_total", "kind": "association_shift"}, {"column": "order.shipping_address_id", "kind": "cardinality_change"}, {"column": "order.shipping_address_id", "kind": "category_shift"}, {"column": "order.shipping_address_id", "kind": "distribution_change"}, {"column": "order.shipping_address_id", "kind": "mean_shift"}, {"column": "order.shipping_address_id", "kind": "new_categorical_values"}, {"column": "order.shipping_address_id", "kind": "null_rate_change"}, {"column": "order.shipping_address_id ~ order_total", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ store_id", "kind": "association_shift"}, {"column": "order.status", "kind": "column_removed"}, {"column": "order.store_id", "kind": "new_categorical_values"}, {"column": "order.store_id ~ order_total", "kind": "association_shift"}, {"column": "order.store_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.store_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "store.state", "kind": "column_removed"}]}, {"between": [0, 29], "changes": [{"column": "customer.is_active", "kind": "dtype_change"}, {"column": "customer.is_active", "kind": "true_rate_change"}, {"column": "order.channel", "kind": "column_added"}, {"column": "order.customer_id", "kind": "cardinality_change"}, {"column": "order.customer_id", "kind": "new_categorical_values"}, {"column": "order.customer_id ~ order_date", "kind": "association_shift"}, {"column": "order.customer_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "order.customer_id, store_id -> shipping_address_id", "kind": "dependency_broken"}, {"column": "order.order_date", "kind": "cardinality_change"}, {"column": "order.order_date", "kind": "day_of_week_change"}, {"column": "order.order_date", "kind": "hour_of_day_change"}, {"column": "order.order_status", "kind": "column_added"}, {"column": "order.order_total", "kind": "new_categorical_values"}, {"column": "order.order_total", "kind": "spread_change"}, {"column": "order.promotion_id ~ order_total", "kind": "association_shift"}, {"column": "order.shipping_address_id", "kind": "mean_shift"}, {"column": "order.shipping_address_id", "kind": "new_categorical_values"}, {"column": "order.shipping_address_id", "kind": "null_rate_change"}, {"column": "order.shipping_address_id -> customer_id", "kind": "dependency_broken"}, {"column": "order.shipping_address_id ~ order_date", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ order_total", "kind": "association_shift"}, {"column": "order.shipping_address_id ~ store_id", "kind": "association_shift"}, {"column": "order.status", "kind": "column_removed"}, {"column": "order.store_id", "kind": "new_categorical_values"}, {"column": "order.store_id ~ customer_id", "kind": "association_shift"}, {"column": "order.store_id ~ order_date", "kind": "association_shift"}, {"column": "order.store_id ~ order_total", "kind": "association_shift"}, {"column": "order.store_id ~ promotion_id", "kind": "association_shift"}, {"column": "order.store_id ~ shipping_address_id", "kind": "association_shift"}, {"column": "store.state", "kind": "column_removed"}]}], "elapsed_seconds": 0.905, "files": [], "gate_messages": {}, "gates": {}, "scale": "tiny", "scenario": "schema_evolution_schedule", "seed": 42}, "scenario": "schema_evolution_schedule"}], "suite": "schema-evolution", "format": "shape-result", "version": 1, "command": "suite run", "exit_code": 0}
    ```
