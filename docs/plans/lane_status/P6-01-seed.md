# P6-01-seed - are the T-21 clause-(h) and column misses at seed 1042 chance? (lane/P6-01-seed)

Status: **investigation done; every miss is chance (verdict 1). No real difference was found, so no engine,
strategy or schema change was made, and no allow-list entry is needed.** Nothing was lowered, skipped or changed: no
seed, floor, tolerance, case, test or harness statistic. The baseline checkout was only read. Raw data:
`docs/plans/evidence/P6-01-seed/`.

## Method

Same reference as `verify.py`: every score is `FidelityComparator(real = baseline seed 42, synthetic = run)`, so the
two score samples are draws of the same statistic.

- `benchmarks/vs_spindle/domain_1to1/seed_study_wide.py` (new): baseline seeds 43..72 and Shape seeds 1042..1071
  (**30 per tool**, the same scale), per-table and **per-column** `FidelityComparator` scores with their components
  (`mean_delta`, `std_ratio`, KS, chi-square, overlap). 14 domain-scale pairs: capital_markets medium; education small
  and medium; financial small and medium; insurance small; supply_chain small; real_estate small and medium; iot
  medium; manufacturing small and medium; marketing small; healthcare medium. That is 130 tables and 926 columns
  (`wide_<domain>_<scale>.json`). Two cells (education small, real_estate small) were repeated with **150 per tool**
  (`wide150_*.json`).
- `seed_study_analyze.py` (new): two-sided Mann-Whitney U and two-sample KS per table and per column, **Bonferroni
  within each level (130 table tests, 926 column tests), alpha 0.05**; where seed 1042 falls in Shape's own
  distribution; the share of each tool's seeds under the verifier's floor (min of baseline 43-46, minus 0.5).
  Output `analysis_30seeds.{json,txt}`, `analysis_150_*.json`.
- `pooled_check.py` (new): rows of one table pooled over the 30 + 30 runs; per column KS or a chi-square test of
  homogeneity, and the per-run distinct counts (`pooled_*.json`).
- Strategy reading: the generation rule of each suspect column in both tools (below).

## Result in one table: no cell differs

No table or column is significant after Bonferroni (`significant: []` in every analysis file). The p-values are
calibrated to no difference: table level 9 of 130 Mann-Whitney tests below 0.05 (6.5 expected), 0 below 0.01; column
level 43 of 756 below 0.05 (37.8 expected), 3 below 0.01 (7.6 expected), with Shape scoring higher in 27 of those 43
(`pvalue_calibration.txt`). The smallest column p-value of all 926 is 0.0009 (financial small
`transaction.account_id`, where Shape scores *higher*, 98.4 against 96.9), which is the minimum expected of ~1000
null tests.

**The floor is where a fresh baseline seed misses too.** The T-21 floor is the minimum of four baseline seeds minus
0.5 per table. Over the 130 tables, a fresh baseline seed (47-72, not among the four that set the floors) is below its
own tables' floors on **8.5 tables on average** (min 3, max 15); Shape seeds 1042-1071 on **8.5** (min 3, max 17);
Mann-Whitney on the per-seed counts p = 0.93 (`aggregate_floor_misses.txt`). Seed 1042 is the worst Shape seed of the
thirty (17 tables, the next is 16): the verdicts quoted in P6-01a-d are that seed's draw, not a property of the
generator. The verifier cannot give "all tables pass" to either tool at these cell counts: a baseline seed would
itself fail clause (h) in every one of these pairs.

## Findings per miss

Scores are `FidelityComparator` table scores; "bl" = baseline seeds 43-72, "sh" = Shape 1042-1071 (mean ± sd);
"pct" = share of Shape's 30 seeds at or below seed 1042's score; "below" = share of seeds under the floor
(baseline 47-72 | Shape). MW/KS = two-sample p-values (uncorrected; none survives Bonferroni).
Full lines: `per_miss_summary.txt`.

| Cell / table (rows) | Floor | bl | sh | 1042 (pct) | Below floor (bl / sh) | MW / KS | Verdict |
|---|---|---|---|---|---|---|---|
| capital_markets medium `company` (500) | 84.96 | 85.90±0.86 | 86.40±0.86 | 84.58 (0.03) | 0.10 / 0.07 | 0.022 / 0.035 | chance (Shape is *higher*) |
| education small and medium `department` (25) | 83.63 | 84.72±1.50 | 84.51±2.17 | 80.08 (0.07) | 0.20 / 0.33 | 0.88 / 0.81 | chance |
| education small `enrollment` (16000) | 99.16 | 99.41±0.52 | 99.46±0.45 | 98.82 (0.17) | 0.23 / 0.20 | 0.63 / 0.81 | chance |
| financial small `account` (2200) | 95.83 | 96.66±0.50 | 96.78±0.44 | 95.67 (0.03) | 0.07 / 0.03 | 0.49 / 0.96 | chance |
| financial small and medium `branch` (200) | 84.91 | 85.57±0.82 | 85.94±0.73 | 84.59 (0.07) | 0.27 / 0.10 | 0.064 / 0.24 | chance |
| financial small `loan` (400) | 96.98 | 97.64±0.62 | 97.59±0.57 | 96.95 (0.13) | 0.17 / 0.17 | 0.56 / 0.59 | chance |
| insurance small `claim` (540) | 95.46 | 97.13±1.05 | 96.86±1.10 | 95.42 (0.13) | 0.03 / 0.13 | 0.34 / 0.59 | chance |
| supply_chain small `purchase_order_line` (6000) | 97.67 | 98.31±0.89 | 98.31±0.56 | 97.65 (0.10) | 0.10 / 0.10 | 0.59 / 0.81 | chance |
| real_estate small and medium `neighborhood` (50) | 74.25 | 75.50±1.12 | 74.72±1.04 (150 seeds: 75.03±1.21 / 74.82±1.14) | 72.75 (0.07) | 0.17 / 0.27 | 0.012 / 0.035 (150 seeds: 0.069 / 0.18) | chance (see below) |
| iot medium `sensor` (12500) | 99.24 | 99.46±0.54 | 99.37±0.50 | 99.13 (0.43) | 0.30 / 0.47 | 0.40 / 0.59 | chance |
| manufacturing small and medium `production_line` (20) | 94.01 | 97.05±1.59 | 96.76±1.85 | 92.47 (0.07) | 0.03 / 0.07 | 0.50 / 0.59 | chance |
| manufacturing small `work_order` (500) | 97.29 | 97.72±0.48 | 97.78±0.60 | 96.33 (0.03) | 0.20 / 0.23 | 0.51 / 0.39 | chance |
| marketing small `lead` (2000) | 98.40 | 98.83±0.36 | 98.94±0.32 | 98.06 (0.03) | 0.13 / 0.07 | 0.30 / 0.81 | chance |
| healthcare medium `provider.last_name` distinct ratio | tol 0.024 | 1648.2±10.8 distinct | 1643.3±12.7 | 1670 | share of seeds failing the same check: 0.13 / 0.10 | 0.073 / 0.135 | chance |

For every table the mean and spread of Shape's score is the baseline's, and seed 1042 sits in the lower tail of
Shape's own distribution (3rd to 43rd percentile), as one seed of thirty must for some tables; the tables at the 3rd
percentile are those where 1042 happened to score low on the column named in "Drivers" below.

### (b) Which column drives each miss, and its generation rule in both tools

At seed 1042 the miss is carried by one or two columns whose score is far below *both* tools' own distribution of
that column, and for which Shape's and the baseline's distributions agree (column Mann-Whitney p in brackets):

| Table | Column(s) that drive seed 1042's shortfall (1042 vs bl mean±sd / sh mean±sd) |
|---|---|
| capital_markets `company` | `sector_name` 88.9 vs 96.6±4.1 / 98.4±3.4 (0.04, Shape higher); `hq_state` 81.6 vs 85.1±5.3 / 88.3±6.6 (0.10) |
| education `department` | `college` 87.2 vs 98.1±2.8 / 95.9±4.9 (0.09); `budget` 79.5 vs 85.5±5.2 / 87.2±4.5 (0.25) |
| education `enrollment` | `grade` 93.7 vs 98.2±3.3 / 98.8±2.7 (0.53) |
| financial `account` | `current_balance` 89.6 vs 95.6±2.5 / 96.0±2.6 (0.23) |
| financial `branch` | `branch_name` 73.9 vs 79.7±5.8 / 81.1±5.5 (0.25); `lng` 90.5 vs 95.0±2.5 / 95.0±2.4 (0.88) |
| financial `loan` | `principal_amount` 92.0 vs 95.7±2.2 / 96.2±2.1 (0.39); `outstanding_balance` 92.6 vs 95.7±2.0 / 95.7±2.2 (0.88) |
| insurance `claim` | `cause` 81.8 vs 90.0±6.9 / 88.0±6.6 (0.22); `claim_amount` 86.8 vs 94.6±4.2 / 94.1±4.9 (0.91) |
| supply_chain `purchase_order_line` | `line_total` 88.1 vs 96.0±3.9 / 96.7±3.3 (0.81) |
| real_estate `neighborhood` | `median_income` 79.5 vs 92.1±3.1 / 88.2±5.9 (30 seeds, 0.01); 150 seeds 89.9±5.5 / 89.1±5.5 (0.16) |
| iot `sensor` | `sensor_type` 95.6 vs 98.8±2.5 / 98.8±2.1 (0.77) |
| manufacturing `production_line` (20 rows) | `is_active` 78.6 vs 96.4±8.1 / 95.7±8.7 (0.57); `line_type` 91.4 vs 99.3±1.9 / 97.7±4.8 (0.17) |
| manufacturing `work_order` | `quantity_planned` 90.8 vs 95.7±1.5 / 96.3±1.7 (0.03); `quantity_produced` 91.0 vs 95.8±1.6 / 96.5±1.8 (0.04) |
| marketing `lead` | `status` 96.8 vs 98.9±1.9 / 99.3±1.6 (0.53); `campaign_id` 96.1 vs 97.2±1.3 / 97.4±1.1 (0.73) |

Every one of these columns is a plain draw whose rule is the same in both tools: a categorical column of
`weighted_enum` / `reference_data` (an iid draw with replacement from the declared weights; the comparator scores it by a
chi-square of the run's counts against **the reference run's counts**, a draw of its own, with a 0.05 cut-off, so a
rejection costs the column 4-20 points on either tool); a numeric column of a `distribution` (the same families, the
same `min`/`max` clipping, `np.maximum`/`np.minimum` in both; the comparator's `mean_delta` and KS are against the
single reference sample). I read the strategy code of both tools for the three families behind the fixed-size tables:

- `reference_data`: the baseline draws `rng.integers` (list of strings or a requested field) or `rng.choice(p=weights)`
  (name/weight records), always **with replacement**; Shape draws a uniform row or an alias-table weighted row,
  **with replacement** (`src/shape/builtins/strategies/reference_data.py`). Same strategy, same parameters, same data
  (checked set-equal for the datasets involved).
- `record_sample` / `record_field` (the `neighborhood` city, state, zip): uniform record with replacement in both;
  `unique` is off in this schema and means the same in both when on (a permutation without replacement while the
  table fits the dataset). Pooled over 30 + 30 runs the `neighborhood` columns do not differ (`pooled_real_estate_small_neighborhood.json`:
  KS `median_income` p = 0.33, chi-square state p = 0.78, zip p = 0.45, city p = 0.45).
- `distribution log_normal` with `min`/`max`: `lognormal(mean, sigma)` clipped (baseline) and `exp(mu + sigma z)` clipped
  (Shape). The 25-row `department.budget` clips about a third of its values at the minimum (z = -0.47 for
  `min` 500,000), in both tools; the 50-row `neighborhood.median_income` clips about 4%.

### Fixed-size tables: `neighborhood` (50), `department` (25), `branch` (200), `production_line` (20)

These are the cells with the largest margins, because a score is the mean of 4-9 column scores and each of those is a
noisy chi-square/KS at 20-200 rows.

- **real_estate `neighborhood`** is the only table whose 30-seed comparison reached p = 0.012 (MW) / 0.035 (KS) before
  correction, with Shape's mean 0.78 lower and `median_income`'s sd 5.9 against 3.1 (Levene p = 0.003). I treated it as
  the candidate for a real difference and **repeated it with 150 seeds per tool**: the means are 75.03 and 74.82 (the
  baseline's moved down 0.47, Shape's up 0.10), `median_income` 89.9±5.5 against 89.1±5.5 (sd equal), table MW p = 0.069,
  KS p = 0.18. The 30-seed difference did not replicate. Pooled over 30 + 30 runs the values of every column match.
  One more observation, not significant: the number of distinct `state` values per run is 26.7 for the baseline and
  28.3 for Shape (MW p = 0.0145 uncorrected, one of ~40 distinct-count comparisons); the exact expectation for 50 uniform
  records of the 40,977-zip dataset is 28.2 (simulation sd of a 30-run mean: 0.43), so Shape is on the expectation
  and it is the *baseline's* 30 runs that sit low (pooled counts per state agree with the dataset in both: chi-square p = 0.62 and 0.56).
  It does not affect a clause: it is not a column score difference (state score MW p = 0.27; 150 seeds p = 0.08).
- **education `department`** (the same table at small and medium): 150 seeds: baseline 84.80±1.66, Shape 84.81±2.11
  (MW p = 0.46, KS p = 0.23). The distinct `department_name` count per 25-row run is 16.1 (baseline) and 15.3 (Shape)
  against an exact expectation of 15.72 from the dataset's weights.
- **financial `branch`** (200 rows; the same output at small and medium): the baseline itself is below the floor in 27% of its
  fresh seeds (Shape 10%); the floor is a 4-seed minimum of a statistic with sd 0.8.
- **manufacturing `production_line`** (20 rows): `is_active` (a two-value column over 20 rows) has sd 8 in both tools.

### (c) `healthcare.provider.last_name` distinct ratio (clause (b-e)), seed 1042

`faker last_name` over 2,000 providers. Both tools serve the provider from the **same pool**: the baseline's
`NativeStrategy` `last_names` and Shape's `pools/last_names.txt` are the same 5,000 distinct names (checked
set-equal), and both draw `uniform index into the pool, with replacement` (`rng.choice` and `RowStream.uniform`).
The distinct count of 2,000 draws from 5,000 has the exact mean 1648.5 and sd 14.4. Observed over 30 seeds:
baseline 1648.2±10.8, Shape 1643.3±12.7 (MW p = 0.073, KS p = 0.135, Levene p = 0.29). The reference run (baseline
seed 42) has 1,623 distinct (z = -1.8 from the expectation) and Shape seed 1042 has 1,670 (z = +1.5); the verifier's
tolerance is max(0.02, 1.5 x the baseline's own deviation over seeds 43-46) = 0.024. Share of seeds whose distinct
count is more than 2.4% away from the reference's: **13% of the baseline's 30 seeds and 10% of Shape's**; the baseline's
own maximum over its 30 seeds is also 1,670. Nothing differs but the draw.

## Decisions and what was not done

- **Verdict 1 (chance) for every listed miss.** The evidence is the 30 + 30 seed study (no table or column significant
  after Bonferroni; the p-value distribution is the null's; seed 1042 the worst of Shape's 30 seeds on the
  number of tables under the floor), the repeat with 150 + 150 seeds of the two tables with a raw signal, the
  per-column decomposition (each driver column has the same rule and the same score distribution in both tools) and
  the strategy reading (same pools, with replacement, same clipping, same weights).
- **No code change.** Hence no regression test, and the strategy baselines, mypy, vulture, lint-imports, bandit and
  the two kernel suites were not re-run for this lane: the only changes are three scripts under
  `benchmarks/vs_spindle/domain_1to1/` (ruff check and format clean, `check_user_facing` clean), the evidence and this note.
  The "re-run verify for every domain small and medium" step is for a fix and there is none; the verdicts of
  P6-01a-d at seed 1042 are unchanged and are not claimed to pass.
- **No allow-list entry** in the harness: there is no baseline defect to name here. The only baseline observation
  (fewer distinct states per run in `real_estate.neighborhood`) is not a clause and not significant.
- **What the owner should know about T-21 itself** (not changed, per the rules): clause (h) as defined (one seed against
  the minimum of four baseline seeds minus 0.5) is failed by a *baseline* seed on 8.5 of these 130 tables on average
  and by at least 3 on every one of its 26 fresh seeds, so no seed of any tool can be expected to pass it on whole
  domain-scale pairs. If the owner wants clause (h) to discriminate, the defensible forms are the two-sample test in
  this note (Mann-Whitney per table over >= 20 seeds per tool with Bonferroni) or a floor from >= 20 baseline seeds. I
  did not change the clause.

## Checks run in this session

`ruff check src tests plugins benchmarks/vs_spindle` (all passed); `ruff format --check` (721 files formatted);
`python scripts/check_user_facing.py` (clean). `origin/build/main-plan` was merged once (merge commit; one
README table row conflict, both rows kept).
