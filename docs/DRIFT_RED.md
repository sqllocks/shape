# My drift check went red. Now what?

Review the finding before you accept a new baseline.

Status: available.

A red check means reported changes meet the comparison rules; it does not tell you whether
those changes are intended. [The drift tutorial](tutorials/03-drift.md) shows a real exit 1.
Start with the column, change kind, baseline value, current value and severity.
A dropped or retyped column needs a schema review. A null-rate or distribution change needs
a data review. Check the source version and sampling before changing a threshold.

## Default thresholds

This table is generated from `src/shape/drift/engine.py`, `DEFAULT_THRESHOLDS`.
Rates are absolute changes unless the name describes a ratio. A mean shift uses the baseline
standard deviation. Distribution comparisons use noise floors and minimum population sizes.

| Setting | Default |
|---|---|
| `null_rate` | `0.05` |
| `cardinality_ratio_max` | `1.5` |
| `cardinality_ratio_min` | `0.67` |
| `mean_shift_std` | `0.5` |
| `min_severity` | `low` |
| `category_tvd` | `0.1` |
| `true_rate` | `0.1` |
| `std_ratio_max` | `1.5` |
| `std_ratio_min` | `0.67` |
| `ks_distance` | `0.1` |
| `range_margin_std` | `2.0` |
| `length_ratio` | `0.25` |
| `outlier_rate` | `0.02` |
| `uniqueness_rate` | `0.05` |
| `temporal_tvd` | `0.2` |
| `min_rows` | `30` |
| `dependency_confidence` | `0.02` |
| `placeholder_share` | `0.01` |
| `implausible_rate` | `0.02` |
| `association_shift` | `0.2` |
| `reference_match_rate` | `0.02` |
| `row_count_ratio_max` | `2.0` |
| `row_count_ratio_min` | `0.5` |
| `zero_share` | `0.05` |
| `heaping_ratio` | `2.0` |
| `benford_class_steps` | `2` |
| `tail_alpha_drop` | `0.3` |
| `tail_alpha_max` | `3.0` |
| `multivariate_outlier_rate` | `0.02` |
| `structure_angle` | `30.0` |
| `cohort_tvd` | `0.1` |
| `mixture_weight` | `0.1` |
| `seasonality_strength` | `0.2` |

## Accept or fix

Accept a change when the producer confirms it is intended, consumers agree, the contract still
matches their requirements and the new data is representative. Review the profile before
committing it as the new baseline. Record why you accepted the change in the pull request.

Fix the data when nulls, missing columns, changed types or categories reflect a pipeline defect.
Keep the baseline and rerun profiling after the fix. Do not weaken thresholds to erase an
unexplained failure. If a known change is irrelevant, the drift policy supports ignore lists
and per-column thresholds; [the drift reference](DRIFT.md) describes them.

## Related

[Contracts](CONTRACTS.md) · [Troubleshooting](TROUBLESHOOTING.md)
