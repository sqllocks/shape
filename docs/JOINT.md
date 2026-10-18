# Joint distributions and plausibility

Status: experimental.


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

Computed on a deterministic sample of at most 20,000 rows and at most 16 columns of each role
(categorical, numeric); a table of more than 20,000 rows is analysed on 5,000 sampled rows and at
most 10 columns of each role. The cost does not grow with the table: once a role is full, a column that could only fill that
role is skipped without being read (a wide all-text table costs the same as one of 16 text
columns, plus one pass over its column list). The budget (`Budget` in
`shape.profile.joint.analyze`):

| | at most 20,000 rows | more than 20,000 rows |
|---|---|---|
| sample rows | 20,000 | 5,000 |
| columns per role | 16 | 10 |
| `max_fd_pairs`: (determinant, dependent) pairs tried for single-column dependencies | 240 | 40 |
| `max_fd_multi`: (determinant pair, dependent) triples tried for two-column determinants | 400 | 80 |

The multivariate entries (below) read the same sample and columns. Everything runs on numpy with
fixed seeds, so the result is the same on every run and in both kernel modes (`SHAPE_KERNEL`).

| Field | Contents |
|---|---|
| `dependencies` | approximate functional dependencies `a -> b` (confidence at least 0.8): `confidence` (the share of rows whose `b` is the modal `b` of their `a`), `baseline` (guessing the dependent's most frequent value), `support`, `groups`, `violating_groups` and the worst `violations`. The same numbers as `shape fd`. Two-column determinants `(a, b) -> c` follow the single-column ones (below) |
| `keys` | two-column candidate keys (unique together, neither alone); `exact` is false on a sample |
| `associations` | per type pair: Pearson, Spearman and Kendall for numbers; Cramer's V (bias-corrected), Theil's U in both directions and normalised mutual information for categories; the correlation ratio for a category and a number; mutual information for all |
| `conditionals` | `P(target \| given)` tables for strongly associated categorical pairs of few values |
| `implausible_rate` | the share of rows in the exceptions of a near-exact dependency (confidence 0.95 to below 1) or holding a placeholder; `implausible_by_dependency` and `implausible_by_placeholder` split it |
| `reference_pairs` | only with `reference_pairs` (below) |
| `multivariate_outliers`, `pca`, `cohorts`, `copula` | the multivariate depth entries (below) |
| `categorical_columns`, `multi_determinant_pairs_evaluated`, `multi_determinant_capped` | which columns could be determinants, how many pair triples were tried, and whether the budget ran out (a profile without them was made before pairs were analysed) |

A determinant whose values are all different (a key) determines every column trivially and is not
listed; `diff` knows that and treats the baseline's confidence as 1.

The privacy-safe profile (`docs/PRIVACY_MODEL.md`) is built from an allow-list of fields and carries
neither `joint` nor `placeholders`: both hold values (violating groups, conditional tables).

### Multivariate depth (W3-08, #232)

**Opt-in.** The four multivariate entries are computed only when asked for:
`shape profile --multivariate`, `shape.profile(..., multivariate=True)`. Without it a profile's
`joint` holds every other entry and is otherwise the same: they add a fixed cost per table that
the profiling benchmark gate of the default profile does not allow
(the implementation tests). The two-column determinants are always computed.

Five analyses over several columns at once. Each is part of `joint`, so it follows the same rule:
on for one table, off for a dataset unless `--joint`, off with `--no-joint` or
`SHAPE_PROFILE_JOINT=0`; each is left out of the privacy-safe profile (its allow-list is
unchanged). None stores a row; `cohorts` and `copula` hold category labels (a modal value, the
stored order of the categories), so they are values in the sense of the conditional tables.
A safe capture (the default `.shape`, `docs/PRIVACY_MODEL.md`) keeps none of the four
multivariate entries, nor `categorical_columns`, and records that in its `redaction_manifest`
(`"joint": "removed: ..."`); `--mixed-copula` and the multivariate drift kinds need full captures.

**Two-column determinants** (`dependencies`, `"determinant": ["a", "b"]`). Over the categorical
columns of the analysis, `(a, b) -> c` is listed when its confidence is at least 0.8, neither
column alone determines `c` with a confidence within 0.01 of it, the pair is not a candidate key
(unique together) and, like the single-column ones, it beats guessing the dependent (lift 0.3), has
at least five repeated groups and at least half the rows in repeated groups (a pair whose
combinations mostly occur once determines anything). The fields are those of a single-column
dependency; the determinant is in column-name order and `violations[].determinant_value` is the
pair of values. They follow the single-column entries (at most 20), and the numbers equal
`shape fd --determinant a b --dependent c` on the same rows. At most `max_fd_multi` triples are
tried (table above); `multi_determinant_capped` says when that cut the search short. `shape diff`
reports `dependency_broken` for them; the `fd` contract rule with a list `determinant` reads them
(a pair outside the analysed columns, a profile without pair analysis, or a capped search is
`not measured`; a single column that holds the rule alone, or a pair that is a key, passes it).

**Multivariate outliers** (`multivariate_outliers`). For at least two numeric columns with at
least 10 distinct values and at least 100 complete rows in the sample. Columns that are a linear
combination of earlier ones are dropped. The location and scatter are the Minimum Covariance
Determinant (FAST-MCD with a fixed seed, `h = floor((n + p + 1) / 2)`, random elemental starts
and concentration steps on a subsample, refined on all rows, the covariance scaled to be
consistent at the normal distribution, then one reweighting step as in the reference
implementation), implemented with numpy. A row is an outlier when its squared robust Mahalanobis
distance exceeds the 0.999 quantile of chi-square with `p` degrees of freedom. The entry is
`{columns, method: "mcd", h, rows, threshold, outliers, rate, contributions}`; `contributions` is,
for each column, the mean over the outliers of its share of the squared distance (the squared
standardised residual of the column given the others, normalised to sum to 1; all 0 when no row
is an outlier). A skewed table has a higher rate than a normal one (the distance assumes an
elliptical shape). Diff kind: `multivariate_outlier_rate_change` (low), when the rate rises by more
than `multivariate_outlier_rate` = 0.02, never below six binomial standard errors of the two rates,
and both profiles analyse the same columns.

**PCA** (`pca`). For at least two numeric columns: the principal components of the standardised
columns (a missing value is the column mean). `{columns, rows, explained_variance_ratio,
components, effective_dimension}`: the ratios of all components, `components` the loadings (one
row per component, one entry per column, unit length) of the components needed to reach 95% of the
variance, `effective_dimension` the number that reach 90%. The sign of each component is fixed:
its largest absolute loading is positive. Diff kind: `structure_change` (low), when the effective
dimension changes by two or more (same columns), or the largest principal angle between the
leading subspaces of the baseline's effective dimension, over the shared columns, exceeds
`structure_angle` = 30 degrees and the sampling noise of that subspace (three times the asymptotic
angle of the baseline's spectrum; a flat spectrum at the cut makes it 90 degrees).

**Cohorts** (`cohorts`). For at least 100 rows and two columns: k-means with k-means++ seeding
(fixed seed, two initialisations per `k`) on the standardised numeric columns and the one-hot
encoded categorical columns of at most 20 levels (a missing category is all zeros); `k` is chosen
from 2 to 8 by the silhouette on a deterministic subsample of at most 2,000 rows, the best silhouette
wins (ties to the smaller `k`), and the centres are then refined on all the sampled rows. The
cohorts are stored when the best silhouette is at least 0.25 **and** it beats the best silhouette
of a single Gaussian with the same covariance by 0.10; otherwise the entry is `{"found": false,
"silhouette": S}`. (A low-dimensional unclustered cloud already scores above 0.25: a single normal
column about 0.56, two about 0.33, three about 0.23, so the silhouette alone would find cohorts in
every one- and two-column table.) A found entry is `{found: true, k, silhouette, rows, numeric,
categorical, cohorts}`; each cohort has its `share` and `rows` and a `summary` per column
(numeric: `mean` and `std`; categorical: the modal value `mode` and its `share` within the
cohort), the largest cohort first. Diff kind: `cohort_shift` (low), when the baseline's cohorts,
each matched to the nearest current cohort by their summaries (numeric means in units of the
baseline's spread, a different modal value counts as two one-hot steps), change their shares by a
total variation distance above `cohort_tvd` = 0.10, never below three standard errors summed over
the cohorts; both profiles must have found cohorts.

**Mixed-type copula** (`copula`). A Gaussian copula over at most 16 numeric and 16 categorical
columns of the analysis (at least 100 rows; key-like names, columns with a value in every row and
categoricals of more than 200 levels are left out). `{format: "shape.copula", version: 1, rows,
columns, numeric, categorical, categories, correlation}`. A category sits on the latent normal
scale at the mid-rank of its place in `categories` (by frequency, ties by value, stored); the
latent correlation matrix is estimated from the normal scores of the columns (mid-ranks), corrected
for the steps that ties and categories make (so that reordering by a correlated latent normal gives
back the profile's normal-score correlations, instead of a weaker one), and made positive definite.
It is used by generation (below).

### Reference membership

```python
shape.profile("orders.csv", reference_pairs=[
    {"columns": ["city", "state", "zip"], "reference": "zips.csv"}])   # or a registered dataset
```

`shape profile orders.csv --reference-pair city,state,zip=zips.csv -o orders.shape` does the same.
The reference may also be the name of a reference pack's dataset: with `sqllocks-shape-domains`
installed, `--reference-pair zip,city,state=us_zip_city` checks against the shipped US ZIP table,
offline (`docs/REFERENCE_PACKS.md`).
The profile stores the share of rows whose tuple occurs in the reference (values compare as text,
trimmed and case folded; digit strings without leading zeros) and the commonest mismatches. This is
what catches a real ZIP that belongs to another city: format and dependency checks cannot.

## Drift

`shape.diff` reports these kinds (`docs/DRIFT.md`), each naming the columns and the value:

* `dependency_broken` (high): `zip -> city: confidence 1.000 -> 0.876 (178 violating groups; worst:
  zip='0' maps to 307 city values); placeholder '0' in zip (8.0% of rows)`. `detail` holds the
  violating groups and the placeholders responsible.
* `placeholder_surge` (medium): a placeholder rose by more than `placeholder_share` (0.01) of rows.
* `implausible_rate_change` (medium): more than `implausible_rate` (0.02) more implausible rows.
* `association_shift` (low): an association measure moved by more than `association_shift` (0.2).
* `reference_match_change` (high): the share of rows in a reference fell by more than
  `reference_match_rate` (0.02).
* `multivariate_outlier_rate_change`, `structure_change` and `cohort_shift` (low): see the
  multivariate depth entries above, and `docs/DRIFT.md` for their thresholds.

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

`fd` takes a list `determinant` of one or two columns (`["store_id", "register_no"]`, in any order)
and reads the profile's two-column determinants. `no_placeholder` is `true` or `{"max_share": 0.01,
"allow": ["N/A"]}`. A rule the profile holds no
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
* **A mixed-type copula** (`shape generate --from PROFILE --mixed-copula`; `shape plan
  PROFILE --mixed-copula`; `shape.generate(profile, mixed_copula=True)`): with `joint.copula` in
  the profile, the fitted schema carries a `generation.output.copula_mixed` block (`format`
  `shape.copula-mixed`, `version` 1) and the engine reorders each column's own generated values by
  the rank of a correlated latent normal (a row-addressed stream, label `copula_mixed`), after the
  numeric copula. A category takes its place from the stored order (a value the profile never saw
  goes last). Every column's marginal is exactly preserved: the value counts are those of
  generation without the copula, and the links between a category and a number, and between two
  categories, follow the profile (a category-to-number correlation ratio of 0.6 and a Cramer's V of
  0.5 stay within 0.05 at 20,000 rows when the dependence is of the Gaussian-copula kind: ordered
  along the frequency order). Left alone: key-like columns (`id`, `pk`, names ending `_id`, `_pk`,
  `_fk`), primary keys, and columns another column's generator reads (a hierarchy, a derived or
  computed column, a lookup, a foreign key). Columns drawn from another by a conditional table
  move together with it (the rows of the group are permuted as one, so the conditional tables
  still hold), and a column the mixed copula orders is taken out of the numeric copula's pairs
  (the mixed matrix holds those correlations). The pass is **off by default**: without the flag,
  or without `joint.copula` in the profile, the schema and the generated data are what they were,
  and `shape plan` reports the entry as not modelled. With it, `shape plan` lists the columns the
  copula orders.
* **Joint fidelity** (`shape.generation.joint_fidelity(target, generated)`): total variation and
  Hellinger distance between the two tables' distributions of each column pair.

## Not covered yet

Vine copulas, isolation forests, determinants of three or more columns in the profile (`shape fd`
takes them on request), generating rows per cohort, contract rules for the multivariate entries,
and the stream profiler's windows (their profiles have no `joint` entry, so `diff` has no joint
changes for them). Univariate model selection, heaping and Benford checks are in the column
profile (`docs/PROFILING_NOTES.md`).
