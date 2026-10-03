# Joint distributions and plausibility

A value can be valid for its column and still be wrong **together with the other columns**: a ZIP
of the right format that belongs to another city, `00000` in a ZIP column, a drug that is never
given for the diagnosis. `shape profile` records what holds across columns, `shape diff` and
`shape check` use it, and generation can keep it.

## What a profile holds

Every table profile has an optional `joint` entry, and a column profile has an optional
`placeholders` list. Both are additive: a profile without them is still valid, and nothing else in
the profile changes. They cost a bounded amount (below). The `placeholders` list always comes
with the column; the `joint` entry follows this rule:

| Profile | Default | Turn on | Turn off |
|---|---|---|---|
| One table (a file, a folder read as one table, a table, a data frame) | on | `joint=True`, `--joint` | `joint=False`, `--no-joint` |
| A dataset (several tables: a dict of sources, `--dataset`, a workbook) | **off** | `joint=True`, `--joint` | `joint=False`, `--no-joint` |

When the call does not choose, `SHAPE_PROFILE_JOINT` does: `0`, `false` or `no` switches the
analysis off, any other non-empty value (`1`) switches it on, for a dataset too. An explicit
`joint=` or `--joint` / `--no-joint` always wins over the variable. A dataset profile without the
entry is otherwise identical to one with it, so `shape profile --dataset DIR --joint` can add it
when the dependencies, associations or `fd` contract rules are wanted. A contract
rule that needs the entry (`fd`, `implies`, `max_implausible_rate`) reports "not measured" for a
profile that lacks it, never a pass, and `shape diff` reports joint changes only where both
profiles carry it.

### Placeholders (column `placeholders`)

Values far more frequent than the rest of the column implies, with the share of rows and the
evidence: `00000`, `99999`, `1900-01-01`, `9999-12-31`, `-1`, `N/A`, `UNKNOWN`, `TEST`, strings of
one repeated digit or letter (`kind` is `zeros`, `numeric_sentinel`, `date_sentinel`,
`text_literal`, `repeated_digits`, `repeated_letters`). A *listed* placeholder is reported when it is
at least 0.2% of the non-null values. An *unlisted* value is reported as a `spike` when the column
has many distinct values and one value is at least 2% of them and at least five times as frequent
as the next; a dominant value of a low-cardinality column (a status that is 70% `active`) is not a
placeholder. Detection reads the profile's own value counts, so it costs nothing extra.

### Joint analysis (table `joint`)

Computed on a deterministic sample of at most 20,000 rows and at most 16 columns of each role (a larger table: 5,000 rows, 10 columns, 40 dependency pairs)
(categorical, numeric), so the cost does not grow with the table.

| Field | Contents |
|---|---|
| `dependencies` | approximate functional dependencies `a -> b` (confidence at least 0.8): `confidence` (the share of rows whose `b` is the modal `b` of their `a`), `baseline` (guessing the dependent's most frequent value), `support`, `groups`, `violating_groups` and the worst `violations`. The same numbers as `shape fd` |
| `keys` | two-column candidate keys (unique together, neither alone); `exact` is false on a sample |
| `associations` | per type pair: Pearson, Spearman and Kendall for numbers; Cramer's V (bias-corrected), Theil's U in both directions and normalised mutual information for categories; the correlation ratio for a category and a number; mutual information for all |
| `conditionals` | `P(target \| given)` tables for strongly associated categorical pairs of few values |
| `implausible_rate` | the share of rows in the exceptions of a near-exact dependency (confidence 0.95 to below 1) or holding a placeholder; `implausible_by_dependency` and `implausible_by_placeholder` split it |
| `reference_pairs` | only with `reference_pairs` (below) |

A determinant whose values are all different (a key) determines every column trivially and is not
listed; `diff` knows that and treats the baseline's confidence as 1.

The privacy-safe profile (`docs/PRIVACY_MODEL.md`) is built from an allow-list of fields and carries
neither `joint` nor `placeholders`: both hold values (violating groups, conditional tables).

### Reference membership

```python
shape.profile("orders.csv", reference_pairs=[
    {"columns": ["city", "state", "zip"], "reference": "zips.csv"}])   # or a registered dataset
```

`shape profile orders.csv --reference-pair city,state,zip=zips.csv -o orders.shape` does the same.
The profile stores the share of rows whose tuple occurs in the reference (values compare as text,
trimmed and case folded; digit strings without leading zeros) and the commonest mismatches. This is
what catches a real ZIP that belongs to another city: format and dependency checks cannot.

## Drift

`shape.diff` reports four new kinds (`docs/DRIFT.md`), each naming the columns and the value:

* `dependency_broken` (high): `zip -> city: confidence 1.000 -> 0.876 (178 violating groups; worst:
  zip='0' maps to 307 city values); placeholder '0' in zip (8.0% of rows)`. `detail` holds the
  violating groups and the placeholders responsible.
* `placeholder_surge` (medium): a placeholder rose by more than `placeholder_share` (0.01) of rows.
* `implausible_rate_change` (medium): more than `implausible_rate` (0.02) more implausible rows.
* `association_shift` (low): an association measure moved by more than `association_shift` (0.2).
* `reference_match_change` (high): the share of rows in a reference fell by more than
  `reference_match_rate` (0.02).

## Contract rules

New optional rules of contract format v1 (a contract that does not use them reads exactly as
before; a reader that predates them rejects them as unknown rules, never ignores them):

```json
{
  "fd": [{"determinant": "zip", "dependent": "city", "min_confidence": 0.99}],
  "implies": [{"if": {"column": "state", "equals": "CA"},
               "then": {"column": "country", "equals": "US"}, "min_confidence": 1.0}],
  "reference_pair": [{"columns": ["city", "state", "zip"], "reference": "zips.csv",
                      "min_match_rate": 0.99}],
  "max_implausible_rate": 0.02,
  "columns": {"zip": {"no_placeholder": true}}
}
```

`no_placeholder` is `true` or `{"max_share": 0.01, "allow": ["N/A"]}`. A rule the profile holds no
evidence for (an old profile, a pair outside the analysed columns, no reference measured) is a
violation that says `not measured`, so a contract never passes a rule it did not test.

## Generation

* **Hierarchical sampling** (`shape.generation.HierarchicalSampler`; strategies `hierarchy` and
  `hierarchy_field`, `docs/GENERATION_STRATEGIES.md`): a reference dataset of records is walked as a
  tree (state, county, city, ZIP), one level at a time, and a row takes the fields (coordinates
  included) of one record under the leaf it reached. A ZIP is always inside its city. `top_weights`
  sets the top level (a profile's state shares); `weighting` is `records` (every record equally
  likely) or `uniform` (every child of a node equally likely).
* **Categorical joint tables from a profile** (strategy `conditional_table`): `shape generate
  --from` draws an enumeration given another one from the profile's `P(target | given)` table, and
  `shape plan` lists it as approximate.
* **A learned joint model** (`shape.generation.fit_joint`): a Chow-Liu tree over mixed columns
  (numbers are cut into quantile bins) with smoothed tables. `score(data)` is the per-row negative
  log-likelihood (the plausibility score), `report(data)` the share of implausible rows, the
  impossible combinations (a value pair of a tree edge never seen in training) and the least
  plausible rows, `sample(n, seed)` joint sampling that keeps the pairs seen in training.
* **Joint fidelity** (`shape.generation.joint_fidelity(target, generated)`): total variation and
  Hellinger distance between the two tables' distributions of each column pair.

## Not covered yet

Univariate model selection (AIC/BIC), heaping and Benford checks, multivariate outliers, vine
copulas, multi-column determinants, and the stream profiler's windows (their profiles have no
`joint` entry, so `diff` has no joint changes for them).
