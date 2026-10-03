# P6-01e-seed - are the composites' T-21 misses at seed 1042 chance? (lane/P6-01e-seed)

Status: **investigation done. Every one of the 18 cells' misses at seed 1042 is chance (verdict 1): Shape and the baseline draw
from the same distribution. One real difference (verdict 2) was found by the study, in the baseline: CMP-2, the dataset clash in
`campus`. Shape already did the right thing there; the harness's allow-list named the wrong column, and that is corrected. No
engine, strategy or schema change was made, so there is no Shape regression test to add.** Nothing was lowered, skipped or
changed: no seed, floor, tolerance, case, test or harness statistic. The baseline checkout was only read. Raw data:
`docs/plans/evidence/P6-01e-seed/`.

Every number below comes from a run in this session (2026-10-02 and 2026-10-03). The 18 `verify.py` verdicts of P6-01e
(`docs/plans/evidence/P6-01e/verify/`) are unchanged and are **not** claimed to pass: seed 1042 still fails clause (h), (f) or
(b-e) in every cell. This note says why, and that it is the draw.

## Method

Same reference as `verify.py`: every score is `FidelityComparator(real = baseline seed 42, synthetic = the run)`, so the two score
samples are draws of the same statistic.

- `seed_study_wide_stream.py` (new; `seed_study_wide.py` of `lane/P6-01-seed` cannot hold 61 composite runs on disk, so this
  one generates a seed, scores it, deletes it; same scores and same JSON, plus every column's null rate and distinct count as
  `raw`; resumable): baseline seeds 43..72 and Shape seeds 1042..1071 (**30 per tool**), per table and per column, for **all 18 cells**
  (9 composites x small, medium). That is 486 table tests and 3,422 column tests.
- `seed_study_analyze.py`, `pooled_check.py` (copied unchanged from `lane/P6-01-seed`): Mann-Whitney and KS per table and per
  column, **Bonferroni within each level**, alpha 0.05, and where seed 1042 falls.
- `seed_study_floor_misses.py` (new): the per-table floor-miss rate of fresh baseline seeds (47..72, not among the four that set the
  floors) against Shape seeds, per cell.
- `seed_study_findings.py` (new): each clause (h) finding of the 18 verify reports against the study of its cell.
- Targeted checks of the column and rate misses: `column_miss_check.py` (null rate, distinct ratio, with the verifier's own
  tolerances), `rate_check.py` (clause (f) rates over seeds 42..72 and 1042..1071, with the verifier's floor), `bridge_check.py`
  (cross-domain bridge columns against the exact iid expectation), `fk_marginal_check.py` (pooled per-parent counts of a Pareto
  foreign key) and `column_moments_check.py` (per-run moments of the money columns).

## The result in one place

- **Significance.** After Bonferroni the only table that differs is `hr_department` of `campus` (the real difference, below); at
  column level the only ones are `campus.hr_department.department_name` (the same), and three Pareto foreign-key columns where **Shape
  scores higher** (`retail_order.customer_id` at small and medium, `financial_transaction.account_id` at medium; see "Observation").
  `analysis_all.txt`, `analysis_{small,medium}.json`. Counting each distinct draw about once (identical score vectors merged), column-level p-values are calibrated
  (95 of 2,019 below 0.05, null 101; 21 below 0.01, null 20). At table level 35 of 434 are below 0.05 (null 22): 15 of the 35 have
  Shape higher; the smallest are `hr_department` (the real difference) then 0.003 (`marketing_campaign`, see below); none of the
  rest survives correction (`pvalue_calibration.txt`).
- **The floor is where a fresh baseline seed misses too** (question (b)). Over the 18 cells a fresh baseline seed is under its own
  tables' floors on **1.59 tables per cell on average** (Shape seeds: 1.66; Shape higher in 11 of 18 cells; Wilcoxon p = 0.43).
  Summed over the 18 cells, fresh baseline seeds miss on **28.7 tables** (sd 7.3, range 16-49), Shape seeds on **29.9**
  (sd 11.7, range 11-56), Mann-Whitney p = 0.99. **Seed 1042 is the third-worst of Shape's 30 seeds (53 misses)**, after 1048 and
  1061 (56 each). Only 19% of the baseline's fresh seeds, and 24% of Shape's, have no table under its floor in a cell:
  clause (h) as defined is failed by four fresh baseline seeds in five. `aggregate_summary.txt`, `floor_misses_{small,medium}.txt`.
- **Why all 18 cells fail together.** Shape draws every column from a stream keyed by (seed, table, column), so a prefixed table such
  as `hr_training` is *the same table* in every composite that holds it, at one seed. The 53 clause (h) findings of the 18 cells are
  only 44 distinct (scale, table) draws on 19 distinct tables (`hr_training` at 90.30 in 8 cells, `financial_branch` at 84.79 in
  four and 85.92 in a fifth because that cell's reference run differs, `marketing_web_visit` and `retail_order` in three). A bad Shape seed is therefore bad in every cell at once, and seed 1042
  is a bad Shape seed (above). The baseline's foreign-key columns draw from one generator shared by all tables
  (`IDManager._rng`, seeded once and advanced in table order), so its foreign keys depend on which tables come before them and its
  draws are less correlated between composites (sd of the summed misses 7.3 against 11.7). It is a property of how the seeds are
  derived, not a defect: the two samples have the same mean.

## Per cell (seed 1042; the full table with numbers is `analysis/findings_h.md`)

Verdict 1 (chance) for every entry. (h) = `FidelityComparator` table score under the floor; (f) = strategy-semantics rate;
(b-e) = column check.

| Cell | Findings at seed 1042 |
|---|---|
| campus small | (f) `hr_training_enrollment` date offset 0.8455 < 0.8460; (h) `hr_termination`, `hr_training` |
| campus medium | (h) `education_student`, `hr_training` |
| capital_markets-marketing-education small | (h) `capital_markets_split`, `marketing_campaign`, `marketing_web_visit` |
| capital_markets-marketing-education medium | (h) `capital_markets_split`, `education_student` |
| digital_commerce small | (h) `financial_account`, `marketing_campaign`, `marketing_industry`, `marketing_lead`, `marketing_web_visit` |
| digital_commerce medium | **(b-e) `retail_order.shipping_address_id` null rate**; (h) `financial_branch`, `retail_order`, `retail_order_line`, `retail_return` |
| enterprise small | (f) `hr_training_enrollment`; (h) `financial_branch`, `financial_loan_payment`, `hr_training` |
| enterprise medium | (h) `financial_branch`, `hr_termination`, `hr_training`, `retail_order`, `retail_order_line`, `retail_return` |
| healthcare_system small | **(b-e) `healthcare_patient.email` distinct ratio**; (f) `hr_training_enrollment`; (h) `hr_termination`, `hr_training`, `insurance_claim`, `insurance_policy_type` |
| healthcare_system medium | (h) `hr_termination`, `hr_training`, `insurance_policy_type` |
| retail-hr-financial small | (f) `hr_training_enrollment`; (h) `hr_termination`, `hr_training` |
| retail-hr-financial medium | (h) `hr_training`, `retail_order`, `retail_order_line`, `retail_return` |
| smart_factory small | (f) `iot_alert.resolved_at - triggered_at in [0,7] days` 0.7000 < 0.7230 |
| smart_factory medium | (f) `iot_alert.resolved_at - triggered_at` 0.7300 < 0.7386 |
| supply_chain-real_estate-manufacturing small | (h) `manufacturing_equipment` 96.45 < 96.46, `real_estate_appraisal` |
| supply_chain-real_estate-manufacturing medium | (h) `manufacturing_equipment` 96.45 < 96.46 |
| telecom_bundle small | (h) `financial_account`, `financial_branch`, `financial_loan_payment`, `marketing_campaign`, `marketing_industry`, `marketing_web_visit`, `telecom_device_model` |
| telecom_bundle medium | (h) `financial_branch`, `marketing_industry`, `telecom_device_model` |

The number of tables under their floor in each cell for a fresh baseline seed (mean, range) against Shape (mean, range) and where
seed 1042 falls are in `analysis/floor_misses_small.txt` and `floor_misses_medium.txt`: for example `enterprise` small 1.46 (0-4) /
1.57 (0-4), seed 1042 with 3 (27% of Shape seeds as bad or worse); `digital_commerce` small 1.50 (0-4) / 1.83 (0-5), seed 1042 with 5
(3%); `telecom_bundle` small 1.81 (0-5) / 1.60 (0-7), seed 1042 with 7 (3%); `smart_factory` 1.54 / 1.27 (small) and 1.65 / 1.03 (medium),
seed 1042 with none. Mann-Whitney on the per-seed counts: smallest p = 0.03 (`smart_factory` medium, where Shape has *fewer* misses).

## Findings per miss

### Clause (h) table scores (all cells except the two `smart_factory` ones)

`analysis/findings_h.md` has every one of the 53 findings (floor, seed 1042, baseline and Shape mean +- sd, the percentile of
seed 1042 in Shape's own scores, the share of seeds under the floor and a Mann-Whitney p). The pattern is the single-domain one:

- Shape's mean and spread equal the baseline's for the table (for example `hr_training` 91.23 +- 0.68 against 91.34 +- 0.83, p =
  0.31; `financial_branch` 86.43 +- 1.01 against 86.27 +- 0.99, p = 0.59; `manufacturing_equipment` 97.07 +- 0.97 against 96.92 +- 0.71,
  p = 0.09), and seed 1042 sits in the lower tail of Shape's own distribution (3rd to 33rd percentile; 37 of the 53 findings are at or below
  the 10th, as expected for tables chosen because seed 1042 scored low on them), with **fresh baseline seeds under the same floor 0-58% of the time** (for example `hr_training` 23% against Shape 13%,
  `telecom_device_model` 58% against 30%).
- `manufacturing_equipment` 96.45 < 96.46 (the closest miss): baseline seeds fall under the floor 15% of the time, Shape's 23%.
- The tables with a raw p below 0.05 are, with the direction: `marketing_campaign` small (Shape lower; 0.003 / 0.004 / 0.014 in three
  composites, one Shape draw against three baseline contexts), `marketing_web_visit` small (Shape *higher*; 0.012-0.025), and at medium
  `retail_order` / `retail_order_line` / `retail_return` (Shape lower; 0.02-0.05). None survives Bonferroni (smallest 0.003 against 434
  draws). The medium retail ones come through the money chain `unit_price` -> `line_total` -> `order_total` -> `refund_amount`, whose scores swing
  by 13-20 points in both tools; I tested that chain directly (below) and the two tools do not differ. The 150-seed repeat of
  `marketing_campaign` and `marketing_web_visit` follows.

- **150-seed repeat of the two marketing tables** (`wide150_marketing_small.json`, single domain `marketing`, small, 150 baseline +
  150 Shape seeds, so it tests the generator's rule, not the composite's draw; 10 table and 60 column tests, Bonferroni): **nothing
  is significant**; `campaign` **97.24 +- 0.78 against 97.26 +- 0.76** (p = 0.85) and `web_visit` **93.90 +- 0.21 against
  93.89 +- 0.23** (p = 0.98); 0 of 60 column p-values below 0.05 (3.0 expected). The 30-seed raw signals did not replicate.
  `analysis/analysis150_marketing_small.txt`.

### Clause (f) rates

- `hr_training_enrollment.completion_date - enrollment_date in [1,90] days` (campus, enterprise, healthcare_system and retail-hr-financial,
  all small; one draw): 0.8455 against a floor of 0.8460. Over seeds 47..72 and 1042..1071 the rate is **0.8478 +- 0.0109 for the
  baseline and 0.8472 +- 0.0075 for Shape** (Mann-Whitney p = 0.86, KS p = 0.36), and **54% of the baseline's fresh seeds are under the
  same floor** (Shape 43%). The floor is the minimum of five baseline draws, so a sixth draw falls under it one time in six by chance,
  for each rate (7 in that cell). Evidence: `rates/rates_composite_enterprise_small.json`.
- `iot_alert.resolved_at - triggered_at in [0,7] days` (smart_factory): small **0.7609 +- 0.0306 baseline, 0.7503 +- 0.0271 Shape**
  (p = 0.29; fresh baseline seeds under the floor 8%, Shape 20%), medium **0.7523 +- 0.0069 and 0.7517 +- 0.0099** (p = 0.83; 0% and
  10%). The means agree; seed 1042 (0.70 and 0.73) is the low tail of the same distribution. Evidence:
  `rates/rates_composite_smart_factory_{small,medium}.json`.

### Column misses

- **`healthcare_patient.email` distinct ratio (healthcare_system small).** The generation rule is the same in both tools:
  `faker email`, 500 rows, `null_rate` 0.15, from the same name and domain pools. Distinct count over 30 seeds: **baseline 426.3 +- 8.0
  [398, 443], Shape 425.7 +- 8.7 [411, 449]** (Mann-Whitney p = 0.40, KS p = 0.59); the reference run (435) is a high draw of the
  baseline (+1.1 sd above its own mean), and seed 1042 (421) is a low one; the ratio 421/435 is 3.2% from 1 against a tolerance of 3.1%.
  Outside the verifier's tolerance: 5 of 26 fresh baseline seeds, 12 of 30 Shape seeds (Fisher p = 0.14); treating each tool's
  distinct count as normal with its own mean and sd, about 27-31% of seeds are expected outside it. Null rate: baseline 0.1475, Shape 0.1485, none of either tool's seeds is out of tolerance.
  Verdict 1. `analysis/column_miss_healthcare_patient_email.txt`.
- **`retail_order.shipping_address_id` null rate (digital_commerce medium).** The column is `foreign_key` to `address.address_id` with
  `constrained_by customer_id` and `nullable`; **both tools** give a null to every order whose customer has no address (baseline
  `get_constrained_fks(nullable=True)`, Shape `ForeignKey._constrained`; the schema's `null_rate` is 0.0 and plays no part). The null
  rate of a run is therefore a share of *customers*, not of independent rows, and its run-to-run spread is not binomial: **sd 0.0018
  (baseline) and 0.0024 (Shape) against 0.0006 for 500,000 independent rows**. The verifier's tolerance (5 binomial sd of a
  difference, 0.0042) does not describe this column; it fails seed 1042 (0.2279 against the reference 0.2236, a difference of
  0.0043) while the distributions are the same: **baseline 0.2233 +- 0.0018 [0.2196, 0.2289], Shape 0.2235 +- 0.0024 [0.2181, 0.2281]**,
  Mann-Whitney p = 0.67, KS p = 0.81; outside tolerance: 1 of 26 fresh baseline seeds, 4 of 30 Shape (Fisher p = 0.36). Seed 1042's
  value is within Shape's own range. Cross-domain: this column is a foreign key *inside* retail (to `retail_address`), not one of the
  composite's cross-domain links; the composite adds no rule to it. Verdict 1. `analysis/column_miss_retail_order_shipping_address_id.txt`.

### Cross-domain links, shared reference data, key and seed offsets (question (c))

- **Cross-domain bridge columns** (`shared_person_*`, `shared_organization_*`, `shared_location_*`; a foreign key to the primary
  domain's table, drawn uniformly with replacement, null rate 0.0 and `nullable` per the link's `optional` in both tools). 36 distinct
  columns in the 18 cells (33 with a varying distinct count): null rate 0.000 in every run of both tools. The mean distinct count of each tool against the exact expectation
  of iid uniform draws (`P (1 - (1 - 1/P)^n)`), as a z-score: **Shape mean z -0.19, sd 0.78, |z| > 2 in 0 of 33; baseline mean z -0.04,
  sd 1.09, |z| > 2 in 3 of 33** (1.5 expected). Shape is on the expectation. `analysis/bridge_check.txt`. The comparator scores of the bridge columns show 7 of 36 below
  p = 0.05 (1.8 expected; smallest 0.0009 at `real_estate_neighborhood.shared_location_manufacturing_production_line_id` medium, 50 rows; 4
  with Shape lower, 3 higher), so I tested the two lowest directly, pooling the values of 100 + 100 runs (`fk_marginal_check.py`,
  `fk/fk_bridge_*.json`): `marketing_industry.shared_organization_education_department_id` (25 draws from 25 parents): pooled
  homogeneity chi-square **p = 0.96**, mean distinct 16.46 +- 1.57 baseline, **16.11 +- 1.54 Shape**, expectation 15.99; and
  `real_estate_neighborhood.shared_location_manufacturing_production_line_id` (50 draws from 20 parents): homogeneity p = 0.045, Shape
  against uniform **p = 0.64**, baseline against uniform p = 0.11, mean distinct 18.51 baseline, 18.31 Shape, expectation 18.46. The value
  distributions are the same uniform in both; if anything it is the baseline that strays from the expectation (its mean distinct count
  of 16.46 is 3 standard errors above 15.99), by under one parent per run, which moves a 25-row table's score by a few tenths. A
  bridge column is not null-handled differently by either tool (both draw it as a plain `foreign_key`; null rate 0.0).
- **Seed derivation per sub-domain.** A composite has one seed (the first child's, as in the baseline: `_resolve_seed`); no per-domain offset
  exists in either tool. The tables' streams are keyed by the *prefixed* table name in both (Shape `blake2b(seed, table, column,
  label)`; baseline `seed ^ sha256(table)` for the table's own rng). That is why a table is the same draw in every composite (Shape) and
  why `hr_training` repeats; it is not a merge defect, and the baseline does the same for everything but its foreign keys (above).
- **Key offsets and schema merge.** There is none to offset: keys are per table and the prefixes keep them apart. The merged
  schema, row counts, order, levels, relationships and rules equal the baseline's apart from the two named allow-list entries
  (`test_composite_p601e.py`, P6-01e).
- **Shared reference data.** The identical datasets are shared and the clashing ones are kept apart; the clash is CMP-2, below.

## The real difference: CMP-2 (verdict 2, baseline defect)

`campus` = education + hr. Both ship a dataset `department_names` (university departments; company departments). The baseline finds a
reference dataset by its name and caches it for the process, so the first one loaded, education's, serves **both** columns.

- Evidence, from the baseline's own output (campus small, seed 42): **every value of `hr_department.department_name` is a university
  department** ('Biology', 'Nursing', 'Political Science'); only 6.7% of its values are in HR's dataset (`verify` now reports "Shape
  values in hr/department_names 1.0000, baseline's 0.0667"). `education_department.department_name` is right in both.
- Effect on the study: `hr_department.department_name` scores **57.18 +- 2.84 (baseline) against 41.89 +- 1.23 (Shape)** (Mann-Whitney
  p = 3e-11), and the table 88.18 against 84.71 (p = 2e-6), because the reference run has the wrong pool. These are the only
  significant table and column in the study that are not Pareto-foreign-key columns. It is not a Shape defect: Shape's values all come
  from HR's file (`test_each_domain_reads_its_own_dataset_when_names_clash`). At seed 1042 it does not produce a (h) finding in either scale
  (the floor is a minimum over baseline seeds, which carry the same defect).
- **The harness's allow-list named the wrong column.** `allowlist.py` (CMP-2) listed `education_department.department_name` as the
  column the baseline gets wrong, with a description ("education departments named 'Supply Chain'") that is the reverse of what the
  baseline does, so its check ("Shape's values are in the education file") was vacuous, and the real defect, `hr_department.department_name`,
  was compared as an ordinary column and passed only because its tolerance (1.80 for a total-variation distance) allowed values
  with TVD 0.97. Fixed in `allowlist.py`: `pool_columns` now names `("hr_department", "department_name") -> ("hr", "department_names")`
  and the text states what was measured. This is the owner's standing decision of 2026-10-01 (fix in Shape, narrow named allow-list entry);
  Shape needed no fix. No statistic, floor or tolerance of the harness changed.
- Re-run after the change, as the instruction asks: `verify.py --domain composite_campus --impl shape` small and medium
  (`verify/verify_shape_composite_campus_{small,medium}.txt`): columns equivalent 122/122 in both; the findings are the same as before
  (small: `hr_training_enrollment` (f), `hr_termination`, `hr_training` (h); medium: `education_student`, `hr_training` (h)); exit 1 as before.

## Observation (not a defect, no change): Pareto foreign keys are less variable in Shape

`retail_order.customer_id` (Pareto 1.16, cap 50) and `financial_transaction.account_id` (Pareto 1.2, cap 200) score **higher and
more steadily in Shape** than in the baseline (small: 99.23 +- 0.36 against 98.23 +- 0.89; medium 99.95 +- 0.03 against 99.86 +- 0.08;
`account_id` medium 99.77 +- 0.10 against 99.42 +- 0.38). The pooled marginal is the same (`fk_marginal_check.py`: pooled
chi-square over 1,000 and 1,020 parents p = 0.93 and 0.23; the largest-parent share, top-10 share, distinct count and mean rank have
equal means), but the baseline's run-to-run sd of the mean parent rank is **4 times Shape's** (0.0134 against 0.0033; 0.0115 against
0.0025 for `account_id`; a variance ratio of 16 and 20, F-test p < 1e-6 on those sds with 29 + 29 degrees of freedom): the baseline
normalises each run's Pareto draw by the 99.5th percentile *of that run's own sample* (`IDManager.get_random_fks`), so its
concentration scale varies from run to run; Shape cuts at the theoretical percentile of the distribution (`pareto_index`, a fixed
constant), which is documented in its docstring. The
expectation is the same, the run-to-run variability is not. It makes Shape's scores higher, so it explains none of the misses; it is
recorded because it is a real, measured difference in a generator. Changing Shape's Pareto strategy to reproduce a baseline's
sample-dependent scale would change every domain and the strategy baselines, so I did not. `product_id` (medium, `retail_order_line`)
does not show it: pooled chi-square p = 0.19, mean rank 0.01063 against 0.01065, equal spreads (`fk/`).

The money chain (`unit_price` -> `line_total` -> `order_total` -> `refund_amount`) was tested at digital_commerce medium over 30 + 30
runs (`column_moments_check.py`): mean, median, 99th percentile and sum per run, two-sample tests: **every Mann-Whitney and KS p-value
is above 0.2**; Shape's per-run weighted means vary more (sd 23.8 against 13.6 for `unit_price`, Levene p = 0.08; `refund_amount`
Levene 0.007-0.034 over four moments), which is what makes the medium retail tables' scores swing. The catalogue's own mean is the
same (`retail_product.unit_price` 32.99 +- 0.51 against 33.16 +- 0.56, p = 0.30). No difference in a distribution is established; the
variance difference is not significant after the 20 tests of that check.

## What was not done, and what the owner should know

- **No code change in `src/`, no new regression test.** The only edits outside docs and evidence are the harness's allow-list
  (CMP-2), a docstring in `tests/generation/test_composite_p601e.py`, and the study scripts under `benchmarks/vs_spindle/domain_1to1/`
  (three copied unchanged from `lane/P6-01-seed`; new: `seed_study_wide_stream`, `seed_study_floor_misses`, `seed_study_findings`,
  `column_miss_check`, `rate_check`, `bridge_check`, `fk_marginal_check`, `column_moments_check`).
- **No allow-list entry for the misses.** There is no baseline defect behind any of the 18 cells' misses; the one entry changed is
  the existing CMP-2.
- **T-21 itself (clause (h) as defined, and clauses (f) and null-rate as tolerances) cannot be passed by a seed.** One seed against
  the minimum of four baseline seeds minus 0.5 is failed by a *baseline* seed on 1.59 tables per cell and by every cell's fresh seed
  in four cells out of five; clause (f) takes the minimum of five baseline draws, which a sixth draw undercuts one time in six for
  each rate; and the null-rate tolerance assumes independent rows for a column that is clustered. The two-sample test of this note
  (Mann-Whitney per table over >= 20 seeds per tool with Bonferroni, which finds the dataset clash and nothing else) or a floor from
  >= 20 baseline seeds are the defensible forms, as P6-01-seed already said. I did not change any clause.

## Checks run in this session

`SHAPE_KERNEL` as stated; Python 3.11.15, `~/.venvs/shape` (`pip install -e ".[dev,advanced]" -e plugins/shape-domains`, **pyarrow 25.0.1**),
the pinned baseline 3.0.1 (`422e78df`) built by `setup_spindle.sh`.

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_spindle` | passed |
| `ruff format --check` (same paths) | 742+ files already formatted (re-run after the last edit of each script) |
| `mypy` strict | no issues in 307 source files |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | no output, exit 0 |
| `lint-imports` | 1 contract kept, 0 broken |
| `python scripts/check_user_facing.py` | clean |
| `bandit -q -r src -ll` | exit 0 (only `nosec` warnings) |
| `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, `SHAPE_KERNEL=rust` | 4730 passed, 46 deselected, exit 0 (`checks/suite_rust.log`) |
| same, `SHAPE_KERNEL=python` | 4730 passed, 46 deselected, exit 0 (`checks/suite_python.log`) |
| `pytest tests/generation/test_composite_p601e.py plugins/shape-domains`, both kernels | 82 passed each |
| `verify.py --domain composite_campus --impl shape`, small and medium | 122/122 columns; findings as before; exit 1 (as before) |

Not re-run, because nothing in `src/`, the plugin, the strategies or the data changed: the strategy baselines, `pytest -m heavy`,
`tests/demo`, `export_domains.py --check`, `export_retail.py --check`, `rowcounts.py`, `plan_fixtures.py --check`, and `verify.py` of
the other 16 cells (the allow-list entry I changed is read only for `composite_campus`, `allowlist.pool_columns(domain)`; every other
composite gets an empty mapping, so their reports are unchanged). `tests/demo/fabric` was not run (no unixODBC in this container), as in P6-01e.

## Open

- The T-21 verdict of the composites at seed 1042 is for the owner, as in P6-01e: all 18 cells still exit 1, and this note explains why
  it is the draw. If the owner wants the composites to pass at seed 1042 the options are the ones above (a statistic, not a seed or a floor);
  none was applied.
- The baseline's Pareto foreign keys vary more from run to run than Shape's (Observation). Left as is; recorded.
