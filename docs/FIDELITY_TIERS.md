# Fidelity tiers 1 to 3

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" FIDELITY_TIERS
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for FIDELITY_TIERS
    ```


`shape fidelity` scores synthetic data column by column (`docs/FIDELITY.md`). The tiers look at what
that score does not: joint structure, formats, drift and privacy. Tiers 1 and 2 compare a reference
and a synthetic table; tier 3 holds research-grade tools. **Tier 3 is experimental.**

[Run this example](#local-example-0).


`REFERENCE` and `SYNTHETIC` are as for the base report (a file, or a directory of one file per
table; tables are paired by name). The report is JSON (`--format json`, the default) or text
(`--format text`) and `-o FILE` also writes the JSON. Exit codes: **0** every gate holds, **1** one
does not (or a reference table is missing from the synthetic data), **2** unusable input.

| Extra | Adds | Needed for |
|---|---|---|
| `[advanced]` | scikit-learn (and so SciPy) | tier 1 mixture fits and the adversarial score; the gap distribution; the KS drift test |
| `[ctgan]` | sdv | `shape ctgan` |

Without scikit-learn, tier 1 still runs: the parts that need it are left out, each is named in the
report's `notes` and on stderr, and the exit code is unaffected. Nothing is dropped silently.

## Tier 1

| Part | What it computes | Gate |
|---|---|---|
| mixture fits | for each numeric column of the reference with at least 20 values, a Gaussian mixture of 1 to 5 components chosen by BIC (means, weights, spreads, BIC, AIC) | none |
| conditional profiles | for each of the first 5 text columns with at most 20 distinct values and each of the first 10 numeric columns: per category mean, standard deviation, count and quartiles (categories with fewer than 5 values are left out) | none |
| adversarial score | a gradient-boosted classifier (50 trees, depth 3, 3-fold cross-validation) is trained to tell reference rows from synthetic rows (at most 2,500 of each); AUC 0.5 means it cannot | AUC below `--max-auc` (default 0.75) |
| temporal profiles | for each timestamp column with at least 10 values: gap mean, spread, minimum and maximum (seconds), lag-1 and lag-7 autocorrelation, and whether exponential or normal fits the gaps better | none |
| periodicity | for each of the first 10 numeric columns with at least 32 values: the five strongest FFT periods and whether the strongest is more than five times the median | none |

Mixture fits, conditional profiles, temporal profiles and periodicity describe the **reference**
table; only the adversarial score uses the synthetic one. Python: `shape.fidelity.Tier1Profiler`
(`profile_pair`, `profile_single`).

Things to know:

* The classifier's features are the columns both tables share, in reference order. A text column is
  coded by sorted value *separately in each table*, so a category that the two tables code
  differently looks like a difference: the score is a distinguishability signal, not a proof of
  quality, and it is not comparable across tables of different vocabularies.
* A missing timestamp enters the adversarial test as the smallest integer, far below every real
  time, so the classifier can tell where a column is null.

## Tier 2

| Check | What | Passes when |
|---|---|---|
| format preservation | the dominant format of each text column of the reference (email, US phone, UUID, URL, IPv4, US ZIP, ISO date, US SSN, card number; at least half the values must match), and the share of synthetic values that match it (samples of 500) | the two shares differ by at most 0.10 |
| string similarity | cosine similarity of the character-trigram counts of the two columns (samples of 1,000; columns with fewer than 10 values are left out) | reported (0 to 100), no gate |
| cardinality | distinct values of each shared column, synthetic over reference | within 20% of the reference |
| anomaly rate | the share of rows flagged `True` in a `_shape_is_anomaly` column of the synthetic table, against `--expected-anomaly-rate` (0 if not given) | within 0.05 |

`--min-pass-rate` (default 1.0) is the share of the format, cardinality and anomaly checks that must
pass. Columns that start with `_shape_` are internal and are not scored for cardinality. Python:
`shape.fidelity.run_tier2`.

## Tier 3 (experimental)

* **Dependency tree** (`--tier 3`, `shape.fidelity.ChowLiuTree`): each column is binned (10
  equal-width bins for numbers, a missing value taking the median first; timestamps likewise as
  integers, but a missing timestamp is the smallest integer, so a timestamp column with any missing
  value is reduced to its null indicator) or coded (text), and the tree is the maximum spanning tree of the pairwise mutual information, over the first
  2,000 rows. The report gives the tree of each side, and `compare_trees`: the Jaccard overlap of the
  edge sets (`--min-edge-overlap` gates it) and the mean and largest difference of mutual
  information. Mutual information of binned data depends on the bins; read it as relative.
* **Drift** (`shape drift`, `shape.fidelity.DriftMonitor`, `psi_report`): with `--psi`, every
  column's population stability index, drifted above `--threshold` (default 0.2): numbers and
  timestamps over 10 equal-width bins of their joint range, text columns over category shares (a
  text column of more than 50 distinct values is left out and named in `skipped`). Without `--psi`:
  numbers get a two-sample KS test, everything else a chi-squared test, both on samples of 5,000, and
  drift is a p-value below `--pvalue` (default 0.05) or, for numbers, a PSI above the threshold. The
  KS test needs SciPy and raises an error naming `[advanced]` without it. A column that cannot be
  tested fails closed (`method: "error"`, drifted): a number column against a text one, or a
  column holding an infinite value (no finite bins, so no PSI). Exit 0 if no column drifted, 1 if one did.
* **Bootstrap** (`shape.fidelity.bootstrap_table`; the `bootstrap` generation strategy): rows
  resampled with replacement from a source table, numbers jittered by normal noise of 1% of their
  standard deviation. The strategy is `{"strategy": "bootstrap", "dataset": "people", "field":
  "income", "jitter": 0.01}`: every `bootstrap` column of a table that names the same dataset takes
  the same source row, so the joint distribution is kept; it is row addressed like every strategy.
  Bootstrapping reproduces the source, including any person in it. It is a baseline, not a privacy
  tool.
* **Differential privacy** (`shape.privacy.dp.DifferentialPrivacy`): see below.
* **CTGAN** (`shape ctgan DATA -n ROWS -o OUT.parquet`, `shape.builtins.ctgan.CtganModel`): needs the
  `[ctgan]` extra (sdv). Without it `shape ctgan` prints the install advice and exits 2, and
  `CtganModel.is_available()` is false; `shape plugins list` shows the command either way. Training
  is slow.

## Differential privacy (experimental)

`DifferentialPrivacy(epsilon=1.0, delta=1e-5, mechanism="laplace", clip_to_range=True).apply(table,
seed=None)` adds noise to every integer and float column: Laplace with scale range/epsilon, or
Gaussian with sigma range * sqrt(2 ln(1.25/delta)) / epsilon, then clips to the column's original
range. Missing values stay missing; other columns are untouched; noised columns become float64.

The noise comes from **operating-system entropy unless `seed` (or `rng`) is passed**, so repeated
calls never repeat; a seed reproduces the noise exactly. There is no default seed.

This is a building block, not a guarantee. The noise is calibrated to each column's range and applied
per value; there is no privacy budget accounting across columns or calls, and Shape does not claim
that the table it returns is (epsilon, delta)-differentially private as a whole.

## How the port was checked

The parity harness under `benchmarks/` in the repository runs both implementations on retail medium (the
reference's seed 42 as the real data, Shape's seed 1042 as the synthetic data, scikit-learn in both
venvs). Every output field is compared: integers, names and flags equal, floats within 1e-9
relative, and the adversarial AUC and accuracy and every mixture-fit field within 0.02. The two
differ on purpose in the places listed in the module docstrings of `shape.fidelity.tier1`, `tier2`
and `tier3`.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape fidelity real/ synthetic/ --tier 1      # mixtures, conditional profiles, adversarial AUC, time
shape fidelity real/ synthetic/ --tier 2      # formats, strings, cardinality, anomaly rate
shape fidelity real/ synthetic/ --tier 3      # dependency trees and how much structure survives
shape drift reference/ current/ --psi         # population stability index per column
```

??? info "Output (exit 0)"

    ```text {.expected}
    {
      "notes": [],
      "passed": true,
      "tables": {
        "customers": {
          "adversarial": {
            "accuracy": 0.4743589743589744,
            "auc_roc": 0.4920634920634921,
            "distinguishability_score": -1.59,
            "n_samples": 40,
            "passed": true,
            "top_features": [
              [
                "customer_id",
                null
              ],
              [
                "id",
                null
              ],
              [
                "age",
                null
              ],
              [
                "name",
                null
              ],
              [
                "email",
                null
              ],
              [
                "region",
                null
              ],
              [
                "city",
                null
              ],
              [
                "born",
                null
              ],
              [
                "income",
                null
              ],
              [
                "churned",
                null
              ]
            ]
          },
          "conditional_profiles": [
            {
              "conditioned_on": "region",
              "primary_col": "customer_id",
              "stats_by_value": {
                "north": {
                  "count": 10.0,
                  "mean": 10.0,
                  "p25": 5.5,
                  "p75": 14.5,
                  "std": 6.0553007081949835
                },
                "south": {
                  "count": 10.0,
                  "mean": 11.0,
                  "p25": 6.5,
                  "p75": 15.5,
                  "std": 6.0553007081949835
                }
              }
            },
            {
              "conditioned_on": "region",
              "primary_col": "id",
              "stats_by_value": {
                "north": {
                  "count": 10.0,
                  "mean": 10.0,
                  "p25": 5.5,
                  "p75": 14.5,
                  "std": 6.0553007081949835
                },
                "south": {
                  "count": 10.0,
                  "mean": 11.0,
                  "p25": 6.5,
                  "p75": 15.5,
                  "std": 6.0553007081949835
                }
              }
            },
            {
              "conditioned_on": "region",
              "primary_col": "age",
              "stats_by_value": {
                "north": {
                  "count": 10.0,
                  "mean": 30.0,
                  "p25": 25.5,
                  "p75": 34.5,
                  "std": 6.0553007081949835
                },
                "south": {
                  "count": 10.0,
                  "mean": 31.0,
                  "p25": 26.5,
                  "p75": 35.5,
                  "std": 6.0553007081949835
                }
              }
            },
            {
              "conditioned_on": "region",
              "primary_col": "income",
              "stats_by_value": {
                "north": {
                  "count": 10.0,
                  "mean": 110.0,
                  "p25": 105.5,
                  "p75": 114.5,
                  "std": 6.0553007081949835
                },
                "south": {
                  "count": 10.0,
                  "mean": 111.0,
                  "p25": 106.5,
                  "p75": 115.5,
                  "std": 6.0553007081949835
                }
              }
            },
            {
              "conditioned_on": "city",
              "primary_col": "customer_id",
              "stats_by_value": {
                "New York": {
                  "count": 20.0,
                  "mean": 10.5,
                  "p25": 5.75,
                  "p75": 15.25,
                  "std": 5.916079783099616
                }
              }
            },
            {
              "conditioned_on": "city",
              "primary_col": "id",
              "stats_by_value": {
                "New York": {
                  "count": 20.0,
                  "mean": 10.5,
                  "p25": 5.75,
                  "p75": 15.25,
                  "std": 5.916079783099616
                }
              }
            },
            {
              "conditioned_on": "city",
              "primary_col": "age",
              "stats_by_value": {
                "New York": {
                  "count": 20.0,
                  "mean": 30.5,
                  "p25": 25.75,
                  "p75": 35.25,
                  "std": 5.916079783099616
                }
              }
            },
            {
              "conditioned_on": "city",
              "primary_col": "income",
              "stats_by_value": {
                "New York": {
                  "count": 20.0,
                  "mean": 110.5,
                  "p25": 105.75,
                  "p75": 115.25,
                  "std": 5.916079783099616
                }
              }
            },
            {
              "conditioned_on": "born",
              "primary_col": "customer_id",
              "stats_by_value": {
                "1990-01-01": {
                  "count": 20.0,
                  "mean": 10.5,
                  "p25": 5.75,
                  "p75": 15.25,
                  "std": 5.916079783099616
                }
              }
            },
            {
              "conditioned_on": "born",
              "primary_col": "id",
              "stats_by_value": {
                "1990-01-01": {
                  "count": 20.0,
                  "mean": 10.5,
                  "p25": 5.75,
                  "p75": 15.25,
                  "std": 5.916079783099616
                }
              }
            },
            {
              "conditioned_on": "born",
              "primary_col": "age",
              "stats_by_value": {
                "1990-01-01": {
                  "count": 20.0,
                  "mean": 30.5,
                  "p25": 25.75,
                  "p75": 35.25,
                  "std": 5.916079783099616
                }
              }
            },
            {
              "conditioned_on": "born",
              "primary_col": "income",
              "stats_by_value": {
                "1990-01-01": {
                  "count": 20.0,
                  "mean": 110.5,
                  "p25": 105.75,
                  "p75": 115.25,
                  "std": 5.916079783099616
                }
              }
            }
          ],
          "gmm_fits": {
            "age": {
              "aic": 122.83863727172792,
              "bic": 124.8301018188359,
              "column": "age",
              "means": [
                30.499999999999993
              ],
              "n_components": 1,
              "stds": [
                5.766281384046393
              ],
              "weights": [
                1.0
              ]
            },
            "customer_id": {
              "aic": 122.83863727172792,
              "bic": 124.8301018188359,
              "column": "customer_id",
              "means": [
                10.499999999999998
              ],
              "n_components": 1,
              "stds": [
                5.766281384046393
              ],
              "weights": [
                1.0
              ]
            },
            "id": {
              "aic": 122.83863727172792,
              "bic": 124.8301018188359,
              "column": "id",
              "means": [
                10.499999999999998
              ],
              "n_components": 1,
              "stds": [
                5.766281384046393
              ],
              "weights": [
                1.0
              ]
            },
            "income": {
              "aic": 122.83863727172792,
              "bic": 124.8301018188359,
              "column": "income",
              "means": [
                110.49999999999999
              ],
              "n_components": 1,
              "stds": [
                5.766281384046393
              ],
              "weights": [
                1.0
              ]
            }
          },
          "notes": [],
          "periodicity": {},
          "row_count": 20,
          "table_name": "customers",
          "temporal_profiles": {}
        },
        "orders": {
          "adversarial": {
            "accuracy": 0.4950248756218905,
            "auc_roc": 0.49598930481283426,
            "distinguishability_score": -0.8,
            "n_samples": 200,
            "passed": true,
            "top_features": [
              [
                "order_id",
                null
              ],
              [
                "customer_id",
                null
              ],
              [
                "customer_email",
                null
              ],
              [
                "status",
                null
              ],
              [
                "amount",
                null
              ],
              [
                "order_total",
                null
              ],
              [
                "placed_at",
                null
              ],
              [
                "shipped_at",
                null
              ],
              [
                "order_date",
                null
              ],
              [
                "discount_code",
                null
              ]
            ]
          },
          "conditional_profiles": [
            {
              "conditioned_on": "customer_email",
              "primary_col": "order_id",
              "stats_by_value": {
                "person0@example.test": {
                  "count": 5.0,
                  "mean": 60.0,
                  "p25": 40.0,
                  "p75": 80.0,
                  "std": 31.622776601683793
                },
                "person10@example.test": {
                  "count": 5.0,
                  "mean": 50.0,
                  "p25": 30.0,
                  "p75": 70.0,
                  "std": 31.622776601683793
                },
                "person11@example.test": {
                  "count": 5.0,
                  "mean": 51.0,
                  "p25": 31.0,
                  "p75": 71.0,
                  "std": 31.622776601683793
                },
                "person12@example.test": {
                  "count": 5.0,
                  "mean": 52.0,
                  "p25": 32.0,
                  "p75": 72.0,
                  "std": 31.622776601683793
                },
                "person13@example.test": {
                  "count": 5.0,
                  "mean": 53.0,
                  "p25": 33.0,
                  "p75": 73.0,
                  "std": 31.622776601683793
                },
                "person14@example.test": {
                  "count": 5.0,
                  "mean": 54.0,
                  "p25": 34.0,
                  "p75": 74.0,
                  "std": 31.622776601683793
                },
                "person15@example.test": {
                  "count": 5.0,
                  "mean": 55.0,
                  "p25": 35.0,
                  "p75": 75.0,
                  "std": 31.622776601683793
                },
                "person16@example.test": {
                  "count": 5.0,
                  "mean": 56.0,
                  "p25": 36.0,
                  "p75": 76.0,
                  "std": 31.622776601683793
                },
                "person17@example.test": {
                  "count": 5.0,
                  "mean": 57.0,
                  "p25": 37.0,
                  "p75": 77.0,
                  "std": 31.622776601683793
                },
                "person18@example.test": {
                  "count": 5.0,
                  "mean": 58.0,
                  "p25": 38.0,
                  "p75": 78.0,
                  "std": 31.622776601683793
                },
                "person19@example.test": {
                  "count": 5.0,
                  "mean": 59.0,
                  "p25": 39.0,
                  "p75": 79.0,
                  "std": 31.622776601683793
                },
                "person1@example.test": {
                  "count": 5.0,
                  "mean": 41.0,
                  "p25": 21.0,
                  "p75": 61.0,
                  "std": 31.622776601683793
                },
                "person2@example.test": {
                  "count": 5.0,
                  "mean": 42.0,
                  "p25": 22.0,
                  "p75": 62.0,
                  "std": 31.622776601683793
                },
                "person3@example.test": {
                  "count": 5.0,
                  "mean": 43.0,
                  "p25": 23.0,
                  "p75": 63.0,
                  "std": 31.622776601683793
                },
                "person4@example.test": {
                  "count": 5.0,
                  "mean": 44.0,
                  "p25": 24.0,
                  "p75": 64.0,
                  "std": 31.622776601683793
                },
                "person5@example.test": {
                  "count": 5.0,
                  "mean": 45.0,
                  "p25": 25.0,
                  "p75": 65.0,
                  "std": 31.622776601683793
                },
                "person6@example.test": {
                  "count": 5.0,
                  "mean": 46.0,
                  "p25": 26.0,
                  "p75": 66.0,
                  "std": 31.622776601683793
                },
                "person7@example.test": {
                  "count": 5.0,
                  "mean": 47.0,
                  "p25": 27.0,
                  "p75": 67.0,
                  "std": 31.622776601683793
                },
                "person8@example.test": {
                  "count": 5.0,
                  "mean": 48.0,
                  "p25": 28.0,
                  "p75": 68.0,
                  "std": 31.622776601683793
                },
                "person9@example.test": {
                  "count": 5.0,
                  "mean": 49.0,
                  "p25": 29.0,
                  "p75": 69.0,
                  "std": 31.622776601683793
                }
              }
            },
            {
              "conditioned_on": "customer_email",
              "primary_col": "customer_id",
              "stats_by_value": {
                "person0@example.test": {
                  "count": 5.0,
                  "mean": 1.0,
                  "p25": 1.0,
                  "p75": 1.0,
                  "std": 0.0
                },
                "person10@example.test": {
                  "count": 5.0,
                  "mean": 11.0,
                  "p25": 11.0,
                  "p75": 11.0,
                  "std": 0.0
                },
                "person11@example.test": {
                  "count": 5.0,
                  "mean": 12.0,
                  "p25": 12.0,
                  "p75": 12.0,
                  "std": 0.0
                },
                "person12@example.test": {
                  "count": 5.0,
                  "mean": 13.0,
                  "p25": 13.0,
                  "p75": 13.0,
                  "std": 0.0
                },
                "person13@example.test": {
                  "count": 5.0,
                  "mean": 14.0,
                  "p25": 14.0,
                  "p75": 14.0,
                  "std": 0.0
                },
                "person14@example.test": {
                  "count": 5.0,
                  "mean": 15.0,
                  "p25": 15.0,
                  "p75": 15.0,
                  "std": 0.0
                },
                "person15@example.test": {
                  "count": 5.0,
                  "mean": 16.0,
                  "p25": 16.0,
                  "p75": 16.0,
                  "std": 0.0
                },
                "person16@example.test": {
                  "count": 5.0,
                  "mean": 17.0,
                  "p25": 17.0,
                  "p75": 17.0,
                  "std": 0.0
                },
                "person17@example.test": {
                  "count": 5.0,
                  "mean": 18.0,
                  "p25": 18.0,
                  "p75": 18.0,
                  "std": 0.0
                },
                "person18@example.test": {
                  "count": 5.0,
                  "mean": 19.0,
                  "p25": 19.0,
                  "p75": 19.0,
                  "std": 0.0
                },
                "person19@example.test": {
                  "count": 5.0,
                  "mean": 20.0,
                  "p25": 20.0,
                  "p75": 20.0,
                  "std": 0.0
                },
                "person1@example.test": {
                  "count": 5.0,
                  "mean": 2.0,
                  "p25": 2.0,
                  "p75": 2.0,
                  "std": 0.0
                },
                "person2@example.test": {
                  "count": 5.0,
                  "mean": 3.0,
                  "p25": 3.0,
                  "p75": 3.0,
                  "std": 0.0
                },
                "person3@example.test": {
                  "count": 5.0,
                  "mean": 4.0,
                  "p25": 4.0,
                  "p75": 4.0,
                  "std": 0.0
                },
                "person4@example.test": {
                  "count": 5.0,
                  "mean": 5.0,
                  "p25": 5.0,
                  "p75": 5.0,
                  "std": 0.0
                },
                "person5@example.test": {
                  "count": 5.0,
                  "mean": 6.0,
                  "p25": 6.0,
                  "p75": 6.0,
                  "std": 0.0
                },
                "person6@example.test": {
                  "count": 5.0,
                  "mean": 7.0,
                  "p25": 7.0,
                  "p75": 7.0,
                  "std": 0.0
                },
                "person7@example.test": {
                  "count": 5.0,
                  "mean": 8.0,
                  "p25": 8.0,
                  "p75": 8.0,
                  "std": 0.0
                },
                "person8@example.test": {
                  "count": 5.0,
                  "mean": 9.0,
                  "p25": 9.0,
                  "p75": 9.0,
                  "std": 0.0
                },
                "person9@example.test": {
                  "count": 5.0,
                  "mean": 10.0,
                  "p25": 10.0,
                  "p75": 10.0,
                  "std": 0.0
                }
              }
            },
            {
              "conditioned_on": "customer_email",
              "primary_col": "amount",
              "stats_by_value": {
                "person0@example.test": {
                  "count": 5.0,
                  "mean": 44.260000000000005,
                  "p25": 32.84,
                  "p75": 55.68,
                  "std": 18.056605439561444
                },
                "person10@example.test": {
                  "count": 5.0,
                  "mean": 38.55,
                  "p25": 27.13,
                  "p75": 49.97,
                  "std": 18.056605439561444
                },
                "person11@example.test": {
                  "count": 5.0,
                  "mean": 39.121,
                  "p25": 27.701,
                  "p75": 50.541,
                  "std": 18.056605439561444
                },
                "person12@example.test": {
                  "count": 5.0,
                  "mean": 39.69199999999999,
                  "p25": 28.272,
                  "p75": 51.112,
                  "std": 18.056605439561444
                },
                "person13@example.test": {
                  "count": 5.0,
                  "mean": 40.263,
                  "p25": 28.843,
                  "p75": 51.683,
                  "std": 18.056605439561448
                },
                "person14@example.test": {
                  "count": 5.0,
                  "mean": 40.834,
                  "p25": 29.414,
                  "p75": 52.254,
                  "std": 18.056605439561444
                },
                "person15@example.test": {
                  "count": 5.0,
                  "mean": 41.405,
                  "p25": 29.985,
                  "p75": 52.825,
                  "std": 18.056605439561448
                },
                "person16@example.test": {
                  "count": 5.0,
                  "mean": 41.976000000000006,
                  "p25": 30.556,
                  "p75": 53.396,
                  "std": 18.056605439561444
                },
                "person17@example.test": {
                  "count": 5.0,
                  "mean": 42.547000000000004,
                  "p25": 31.127,
                  "p75": 53.967,
                  "std": 18.056605439561444
                },
                "person18@example.test": {
                  "count": 5.0,
                  "mean": 43.118,
                  "p25": 31.698,
                  "p75": 54.538,
                  "std": 18.056605439561444
                },
                "person19@example.test": {
                  "count": 5.0,
                  "mean": 43.689,
                  "p25": 32.269,
                  "p75": 55.109,
                  "std": 18.056605439561444
                },
                "person1@example.test": {
                  "count": 5.0,
                  "mean": 33.411,
                  "p25": 21.991,
                  "p75": 44.831,
                  "std": 18.056605439561448
                },
                "person2@example.test": {
                  "count": 5.0,
                  "mean": 33.982000000000006,
                  "p25": 22.562,
                  "p75": 45.402,
                  "std": 18.056605439561444
                },
                "person3@example.test": {
                  "count": 5.0,
                  "mean": 34.553,
                  "p25": 23.133,
                  "p75": 45.973,
                  "std": 18.056605439561444
                },
                "person4@example.test": {
                  "count": 5.0,
                  "mean": 35.124,
                  "p25": 23.704,
                  "p75": 46.544,
                  "std": 18.056605439561444
                },
                "person5@example.test": {
                  "count": 5.0,
                  "mean": 35.695,
                  "p25": 24.275,
                  "p75": 47.115,
                  "std": 18.056605439561444
                },
                "person6@example.test": {
                  "count": 5.0,
                  "mean": 36.266,
                  "p25": 24.846,
                  "p75": 47.686,
                  "std": 18.056605439561444
                },
                "person7@example.test": {
                  "count": 5.0,
                  "mean": 36.837,
                  "p25": 25.417,
                  "p75": 48.257,
                  "std": 18.056605439561444
                },
                "person8@example.test": {
                  "count": 5.0,
                  "mean": 37.408,
                  "p25": 25.988,
                  "p75": 48.828,
                  "std": 18.056605439561448
                },
                "person9@example.test": {
                  "count": 5.0,
                  "mean": 37.979,
                  "p25": 26.559,
                  "p75": 49.399,
                  "std": 18.056605439561448
                }
              }
            },
            {
              "conditioned_on": "customer_email",
              "primary_col": "order_total",
              "stats_by_value": {
                "person0@example.test": {
                  "count": 5.0,
                  "mean": 44.260000000000005,
                  "p25": 32.84,
                  "p75": 55.68,
                  "std": 18.056605439561444
                },
                "person10@example.test": {
                  "count": 5.0,
                  "mean": 38.55,
                  "p25": 27.13,
                  "p75": 49.97,
                  "std": 18.056605439561444
                },
                "person11@example.test": {
                  "count": 5.0,
                  "mean": 39.121,
                  "p25": 27.701,
                  "p75": 50.541,
                  "std": 18.056605439561444
                },
                "person12@example.test": {
                  "count": 5.0,
                  "mean": 39.69199999999999,
                  "p25": 28.272,
                  "p75": 51.112,
                  "std": 18.056605439561444
                },
                "person13@example.test": {
                  "count": 5.0,
                  "mean": 40.263,
                  "p25": 28.843,
                  "p75": 51.683,
                  "std": 18.056605439561448
                },
                "person14@example.test": {
                  "count": 5.0,
                  "mean": 40.834,
                  "p25": 29.414,
                  "p75": 52.254,
                  "std": 18.056605439561444
                },
                "person15@example.test": {
                  "count": 5.0,
                  "mean": 41.405,
                  "p25": 29.985,
                  "p75": 52.825,
                  "std": 18.056605439561448
                },
                "person16@example.test": {
                  "count": 5.0,
                  "mean": 41.976000000000006,
                  "p25": 30.556,
                  "p75": 53.396,
                  "std": 18.056605439561444
                },
                "person17@example.test": {
                  "count": 5.0,
                  "mean": 42.547000000000004,
                  "p25": 31.127,
                  "p75": 53.967,
                  "std": 18.056605439561444
                },
                "person18@example.test": {
                  "count": 5.0,
                  "mean": 43.118,
                  "p25": 31.698,
                  "p75": 54.538,
                  "std": 18.056605439561444
                },
                "person19@example.test": {
                  "count": 5.0,
                  "mean": 43.689,
                  "p25": 32.269,
                  "p75": 55.109,
                  "std": 18.056605439561444
                },
                "person1@example.test": {
                  "count": 5.0,
                  "mean": 33.411,
                  "p25": 21.991,
                  "p75": 44.831,
                  "std": 18.056605439561448
                },
                "person2@example.test": {
                  "count": 5.0,
                  "mean": 33.982000000000006,
                  "p25": 22.562,
                  "p75": 45.402,
                  "std": 18.056605439561444
                },
                "person3@example.test": {
                  "count": 5.0,
                  "mean": 34.553,
                  "p25": 23.133,
                  "p75": 45.973,
                  "std": 18.056605439561444
                },
                "person4@example.test": {
                  "count": 5.0,
                  "mean": 35.124,
                  "p25": 23.704,
                  "p75": 46.544,
                  "std": 18.056605439561444
                },
                "person5@example.test": {
                  "count": 5.0,
                  "mean": 35.695,
                  "p25": 24.275,
                  "p75": 47.115,
                  "std": 18.056605439561444
                },
                "person6@example.test": {
                  "count": 5.0,
                  "mean": 36.266,
                  "p25": 24.846,
                  "p75": 47.686,
                  "std": 18.056605439561444
                },
                "person7@example.test": {
                  "count": 5.0,
                  "mean": 36.837,
                  "p25": 25.417,
                  "p75": 48.257,
                  "std": 18.056605439561444
                },
                "person8@example.test": {
                  "count": 5.0,
                  "mean": 37.408,
                  "p25": 25.988,
                  "p75": 48.828,
                  "std": 18.056605439561448
                },
                "person9@example.test": {
                  "count": 5.0,
                  "mean": 37.979,
                  "p25": 26.559,
                  "p75": 49.399,
                  "std": 18.056605439561448
                }
              }
            },
            {
              "conditioned_on": "customer_email",
              "primary_col": "salary",
              "stats_by_value": {
                "person0@example.test": {
                  "count": 5.0,
                  "mean": 50060.0,
                  "p25": 50040.0,
                  "p75": 50080.0,
                  "std": 31.622776601683793
                },
                "person10@example.test": {
                  "count": 5.0,
                  "mean": 50050.0,
                  "p25": 50030.0,
                  "p75": 50070.0,
                  "std": 31.622776601683793
                },
                "person11@example.test": {
                  "count": 5.0,
                  "mean": 50051.0,
                  "p25": 50031.0,
                  "p75": 50071.0,
                  "std": 31.622776601683793
                },
                "person12@example.test": {
                  "count": 5.0,
                  "mean": 50052.0,
                  "p25": 50032.0,
                  "p75": 50072.0,
                  "std": 31.622776601683793
                },
                "person13@example.test": {
                  "count": 5.0,
                  "mean": 50053.0,
                  "p25": 50033.0,
                  "p75": 50073.0,
                  "std": 31.622776601683793
                },
                "person14@example.test": {
                  "count": 5.0,
                  "mean": 50054.0,
                  "p25": 50034.0,
                  "p75": 50074.0,
                  "std": 31.622776601683793
                },
                "person15@example.test": {
                  "count": 5.0,
                  "mean": 50055.0,
                  "p25": 50035.0,
                  "p75": 50075.0,
                  "std": 31.622776601683793
                },
                "person16@example.test": {
                  "count": 5.0,
                  "mean": 50056.0,
                  "p25": 50036.0,
                  "p75": 50076.0,
                  "std": 31.622776601683793
                },
                "person17@example.test": {
                  "count": 5.0,
                  "mean": 50057.0,
                  "p25": 50037.0,
                  "p75": 50077.0,
                  "std": 31.622776601683793
                },
                "person18@example.test": {
                  "count": 5.0,
                  "mean": 50058.0,
                  "p25": 50038.0,
                  "p75": 50078.0,
                  "std": 31.622776601683793
                },
                "person19@example.test": {
                  "count": 5.0,
                  "mean": 50059.0,
                  "p25": 50039.0,
                  "p75": 50079.0,
                  "std": 31.622776601683793
                },
                "person1@example.test": {
                  "count": 5.0,
                  "mean": 50041.0,
                  "p25": 50021.0,
                  "p75": 50061.0,
                  "std": 31.622776601683793
                },
                "person2@example.test": {
                  "count": 5.0,
                  "mean": 50042.0,
                  "p25": 50022.0,
                  "p75": 50062.0,
                  "std": 31.622776601683793
                },
                "person3@example.test": {
                  "count": 5.0,
                  "mean": 50043.0,
                  "p25": 50023.0,
                  "p75": 50063.0,
                  "std": 31.622776601683793
                },
                "person4@example.test": {
                  "count": 5.0,
                  "mean": 50044.0,
                  "p25": 50024.0,
                  "p75": 50064.0,
                  "std": 31.622776601683793
                },
                "person5@example.test": {
                  "count": 5.0,
                  "mean": 50045.0,
                  "p25": 50025.0,
                  "p75": 50065.0,
                  "std": 31.622776601683793
                },
                "person6@example.test": {
                  "count": 5.0,
                  "mean": 50046.0,
                  "p25": 50026.0,
                  "p75": 50066.0,
                  "std": 31.622776601683793
                },
                "person7@example.test": {
                  "count": 5.0,
                  "mean": 50047.0,
                  "p25": 50027.0,
                  "p75": 50067.0,
                  "std": 31.622776601683793
                },
                "person8@example.test": {
                  "count": 5.0,
                  "mean": 50048.0,
                  "p25": 50028.0,
                  "p75": 50068.0,
                  "std": 31.622776601683793
                },
                "person9@example.test": {
                  "count": 5.0,
                  "mean": 50049.0,
                  "p25": 50029.0,
                  "p75": 50069.0,
                  "std": 31.622776601683793
                }
              }
            },
            {
              "conditioned_on": "status",
              "primary_col": "order_id",
              "stats_by_value": {
                "paid": {
                  "count": 100.0,
                  "mean": 50.5,
                  "p25": 25.75,
                  "p75": 75.25,
                  "std": 29.011491975882016
                }
              }
            },
            {
              "conditioned_on": "status",
              "primary_col": "customer_id",
              "stats_by_value": {
                "paid": {
                  "count": 100.0,
                  "mean": 10.5,
                  "p25": 5.75,
                  "p75": 15.25,
                  "std": 5.795330757244024
                }
              }
            },
            {
              "conditioned_on": "status",
              "primary_col": "amount",
              "stats_by_value": {
                "paid": {
                  "count": 100.0,
                  "mean": 38.8355,
                  "p25": 24.70325,
                  "p75": 52.96775,
                  "std": 16.56556191822863
                }
              }
            },
            {
              "conditioned_on": "status",
              "primary_col": "order_total",
              "stats_by_value": {
                "paid": {
                  "count": 100.0,
                  "mean": 38.8355,
                  "p25": 24.70325,
                  "p75": 52.96775,
                  "std": 16.56556191822863
                }
              }
            },
            {
              "conditioned_on": "status",
              "primary_col": "salary",
              "stats_by_value": {
                "paid": {
                  "count": 100.0,
                  "mean": 50050.5,
                  "p25": 50025.75,
                  "p75": 50075.25,
                  "std": 29.011491975882016
                }
              }
            },
            {
              "conditioned_on": "discount_code",
              "primary_col": "order_id",
              "stats_by_value": {
                "SAVE": {
                  "count": 100.0,
                  "mean": 50.5,
                  "p25": 25.75,
                  "p75": 75.25,
                  "std": 29.011491975882016
                }
              }
            },
            {
              "conditioned_on": "discount_code",
              "primary_col": "customer_id",
              "stats_by_value": {
                "SAVE": {
                  "count": 100.0,
                  "mean": 10.5,
                  "p25": 5.75,
                  "p75": 15.25,
                  "std": 5.795330757244024
                }
              }
            },
            {
              "conditioned_on": "discount_code",
              "primary_col": "amount",
              "stats_by_value": {
                "SAVE": {
                  "count": 100.0,
                  "mean": 38.8355,
                  "p25": 24.70325,
                  "p75": 52.96775,
                  "std": 16.56556191822863
                }
              }
            },
            {
              "conditioned_on": "discount_code",
              "primary_col": "order_total",
              "stats_by_value": {
                "SAVE": {
                  "count": 100.0,
                  "mean": 38.8355,
                  "p25": 24.70325,
                  "p75": 52.96775,
                  "std": 16.56556191822863
                }
              }
            },
            {
              "conditioned_on": "discount_code",
              "primary_col": "salary",
              "stats_by_value": {
                "SAVE": {
                  "count": 100.0,
                  "mean": 50050.5,
                  "p25": 50025.75,
                  "p75": 50075.25,
                  "std": 29.011491975882016
                }
              }
            },
            {
              "conditioned_on": "region",
              "primary_col": "order_id",
              "stats_by_value": {
                "north": {
                  "count": 50.0,
                  "mean": 50.0,
                  "p25": 25.5,
                  "p75": 74.5,
                  "std": 29.154759474226502
                },
                "south": {
                  "count": 50.0,
                  "mean": 51.0,
                  "p25": 26.5,
                  "p75": 75.5,
                  "std": 29.154759474226502
                }
              }
            },
            {
              "conditioned_on": "region",
              "primary_col": "customer_id",
              "stats_by_value": {
                "north": {
                  "count": 50.0,
                  "mean": 11.0,
                  "p25": 6.0,
                  "p75": 16.0,
                  "std": 5.802884574739972
                },
                "south": {
                  "count": 50.0,
                  "mean": 10.0,
                  "p25": 5.0,
                  "p75": 15.0,
                  "std": 5.802884574739972
                }
              }
            },
            {
              "conditioned_on": "region",
              "primary_col": "amount",
              "stats_by_value": {
                "north": {
                  "count": 50.0,
                  "mean": 38.55,
                  "p25": 24.560499999999998,
                  "p75": 52.539500000000004,
                  "std": 16.64736765978333
                },
                "south": {
                  "count": 50.0,
                  "mean": 39.121,
                  "p25": 25.1315,
                  "p75": 53.1105,
                  "std": 16.64736765978333
                }
              }
            },
            {
              "conditioned_on": "region",
              "primary_col": "order_total",
              "stats_by_value": {
                "north": {
                  "count": 50.0,
                  "mean": 38.55,
                  "p25": 24.560499999999998,
                  "p75": 52.539500000000004,
                  "std": 16.64736765978333
                },
                "south": {
                  "count": 50.0,
                  "mean": 39.121,
                  "p25": 25.1315,
                  "p75": 53.1105,
                  "std": 16.64736765978333
                }
              }
            },
            {
              "conditioned_on": "region",
              "primary_col": "salary",
              "stats_by_value": {
                "north": {
                  "count": 50.0,
                  "mean": 50050.0,
                  "p25": 50025.5,
                  "p75": 50074.5,
                  "std": 29.154759474226502
                },
                "south": {
                  "count": 50.0,
                  "mean": 50051.0,
                  "p25": 50026.5,
                  "p75": 50075.5,
                  "std": 29.154759474226502
                }
              }
            },
            {
              "conditioned_on": "tier",
              "primary_col": "order_id",
              "stats_by_value": {
                "standard": {
                  "count": 100.0,
                  "mean": 50.5,
                  "p25": 25.75,
                  "p75": 75.25,
                  "std": 29.011491975882016
                }
              }
            },
            {
              "conditioned_on": "tier",
              "primary_col": "customer_id",
              "stats_by_value": {
                "standard": {
                  "count": 100.0,
                  "mean": 10.5,
                  "p25": 5.75,
                  "p75": 15.25,
                  "std": 5.795330757244024
                }
              }
            },
            {
              "conditioned_on": "tier",
              "primary_col": "amount",
              "stats_by_value": {
                "standard": {
                  "count": 100.0,
                  "mean": 38.8355,
                  "p25": 24.70325,
                  "p75": 52.96775,
                  "std": 16.56556191822863
                }
              }
            },
            {
              "conditioned_on": "tier",
              "primary_col": "order_total",
              "stats_by_value": {
                "standard": {
                  "count": 100.0,
                  "mean": 38.8355,
                  "p25": 24.70325,
                  "p75": 52.96775,
                  "std": 16.56556191822863
                }
              }
            },
            {
              "conditioned_on": "tier",
              "primary_col": "salary",
              "stats_by_value": {
                "standard": {
                  "count": 100.0,
                  "mean": 50050.5,
                  "p25": 50025.75,
                  "p75": 50075.25,
                  "std": 29.011491975882016
                }
              }
            }
          ],
          "gmm_fits": {
            "amount": {
              "aic": 839.0681197691922,
              "bic": 852.0939706991327,
              "column": "amount",
              "means": [
                52.62535324313608,
                25.045646756863952
              ],
              "n_components": 2,
              "stds": [
                9.028488898080917,
                9.028488898080925
              ],
              "weights": [
                0.4999999999999995,
                0.5000000000000004
              ]
            },
            "customer_id": {
              "aic": 598.1931863586397,
              "bic": 603.4035267306158,
              "column": "customer_id",
              "means": [
                10.5
              ],
              "n_components": 1,
              "stds": [
                5.766281384046394
              ],
              "weights": [
                1.0
              ]
            },
            "order_id": {
              "aic": 920.3210784797317,
              "bic": 925.5314188517078,
              "column": "order_id",
              "means": [
                50.5
              ],
              "n_components": 1,
              "stds": [
                28.86607006504349
              ],
              "weights": [
                1.0
              ]
            },
            "order_total": {
              "aic": 839.0681197691922,
              "bic": 852.0939706991327,
              "column": "order_total",
              "means": [
                52.62535324313608,
                25.045646756863952
              ],
              "n_components": 2,
              "stds": [
                9.028488898080917,
                9.028488898080925
              ],
              "weights": [
                0.4999999999999995,
                0.5000000000000004
              ]
            },
            "salary": {
              "aic": 920.3210784797317,
              "bic": 925.5314188517078,
              "column": "salary",
              "means": [
                50050.5
              ],
              "n_components": 1,
              "stds": [
                28.86607006504349
              ],
              "weights": [
                1.0
              ]
            }
          },
          "notes": [],
          "periodicity": {
            "amount": {
              "column": "amount",
              "dominant_frequency": 0.01,
              "dominant_period": 100.0,
              "dominant_power": 908.9242297197374,
              "is_periodic": true,
              "top_periods": [
                [
                  100.0,
                  908.9242297197374
                ],
                [
                  50.0,
                  454.686475187892
                ],
                [
                  33.333333333333336,
                  303.373835883883
                ],
                [
                  25.0,
                  227.79273452122308
                ],
                [
                  20.0,
                  182.50453947381533
                ]
              ]
            },
            "customer_id": {
              "column": "customer_id",
              "dominant_frequency": 0.05,
              "dominant_period": 20.0,
              "dominant_power": 319.62266107498306,
              "is_periodic": true,
              "top_periods": [
                [
                  20.0,
                  319.62266107498306
                ],
                [
                  10.0,
                  161.80339887498948
                ],
                [
                  6.666666666666667,
                  110.13446322926333
                ],
                [
                  5.0,
                  85.06508083520397
                ],
                [
                  4.0,
                  70.71067811865476
                ]
              ]
            },
            "order_id": {
              "column": "order_id",
              "dominant_frequency": 0.01,
              "dominant_period": 100.0,
              "dominant_power": 1591.8112604548812,
              "is_periodic": true,
              "top_periods": [
                [
                  100.0,
                  1591.8112604548812
                ],
                [
                  50.0,
                  796.2985554954325
                ],
                [
                  33.333333333333336,
                  531.3026898141559
                ],
                [
                  25.0,
                  398.93648777797387
                ],
                [
                  20.0,
                  319.622661074983
                ]
              ]
            },
            "order_total": {
              "column": "order_total",
              "dominant_frequency": 0.01,
              "dominant_period": 100.0,
              "dominant_power": 908.9242297197374,
              "is_periodic": true,
              "top_periods": [
                [
                  100.0,
                  908.9242297197374
                ],
                [
                  50.0,
                  454.686475187892
                ],
                [
                  33.333333333333336,
                  303.373835883883
                ],
                [
                  25.0,
                  227.79273452122308
                ],
                [
                  20.0,
                  182.50453947381533
                ]
              ]
            },
            "salary": {
              "column": "salary",
              "dominant_frequency": 0.01,
              "dominant_period": 100.0,
              "dominant_power": 1591.8112604548812,
              "is_periodic": true,
              "top_periods": [
                [
                  100.0,
                  1591.8112604548812
                ],
                [
                  50.0,
                  796.2985554954325
                ],
                [
                  33.333333333333336,
                  531.3026898141559
                ],
                [
                  25.0,
                  398.93648777797387
                ],
                [
                  20.0,
                  319.622661074983
                ]
              ]
            }
          },
          "row_count": 100,
          "table_name": "orders",
          "temporal_profiles": {}
        }
      },
      "tier": 1
    }
    {
      "notes": [],
      "passed": true,
      "tables": {
        "customers": {
          "anomaly_rate": null,
          "cardinality": {
            "age": {
              "column": "age",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 20,
              "synth_cardinality": 20
            },
            "born": {
              "column": "born",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "churned": {
              "column": "churned",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 2,
              "synth_cardinality": 2
            },
            "city": {
              "column": "city",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "customer_id": {
              "column": "customer_id",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 20,
              "synth_cardinality": 20
            },
            "email": {
              "column": "email",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 20,
              "synth_cardinality": 20
            },
            "id": {
              "column": "id",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 20,
              "synth_cardinality": 20
            },
            "income": {
              "column": "income",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 20,
              "synth_cardinality": 20
            },
            "name": {
              "column": "name",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 20,
              "synth_cardinality": 20
            },
            "region": {
              "column": "region",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 2,
              "synth_cardinality": 2
            }
          },
          "format_preservation": {
            "born": {
              "column": "born",
              "delta": 0.0,
              "detected_format": "date_iso",
              "passed": true,
              "real_format_rate": 1.0,
              "synth_format_rate": 1.0
            },
            "email": {
              "column": "email",
              "delta": 0.0,
              "detected_format": "email",
              "passed": true,
              "real_format_rate": 1.0,
              "synth_format_rate": 1.0
            }
          },
          "passing_rate": 1.0,
          "string_similarity": {
            "born": {
              "column": "born",
              "cosine_similarity": 1.0000000000000002,
              "ngram_n": 3,
              "score": 100.0
            },
            "city": {
              "column": "city",
              "cosine_similarity": 1.0000000000000002,
              "ngram_n": 3,
              "score": 100.0
            },
            "email": {
              "column": "email",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            },
            "name": {
              "column": "name",
              "cosine_similarity": 0.9999999999999998,
              "ngram_n": 3,
              "score": 100.0
            },
            "region": {
              "column": "region",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            }
          }
        },
        "orders": {
          "anomaly_rate": null,
          "cardinality": {
            "amount": {
              "column": "amount",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 100,
              "synth_cardinality": 100
            },
            "churned": {
              "column": "churned",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 2,
              "synth_cardinality": 2
            },
            "city": {
              "column": "city",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "country": {
              "column": "country",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "customer_email": {
              "column": "customer_email",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 20,
              "synth_cardinality": 20
            },
            "customer_id": {
              "column": "customer_id",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 20,
              "synth_cardinality": 20
            },
            "discount_code": {
              "column": "discount_code",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "iban": {
              "column": "iban",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "is_gift": {
              "column": "is_gift",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 2,
              "synth_cardinality": 2
            },
            "notes": {
              "column": "notes",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "order_date": {
              "column": "order_date",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 28,
              "synth_cardinality": 28
            },
            "order_id": {
              "column": "order_id",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 100,
              "synth_cardinality": 100
            },
            "order_total": {
              "column": "order_total",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 100,
              "synth_cardinality": 100
            },
            "placed_at": {
              "column": "placed_at",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 28,
              "synth_cardinality": 28
            },
            "region": {
              "column": "region",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 2,
              "synth_cardinality": 2
            },
            "salary": {
              "column": "salary",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 100,
              "synth_cardinality": 100
            },
            "shipped_at": {
              "column": "shipped_at",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 28,
              "synth_cardinality": 28
            },
            "ssn": {
              "column": "ssn",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 100,
              "synth_cardinality": 100
            },
            "state": {
              "column": "state",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "status": {
              "column": "status",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "tier": {
              "column": "tier",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            },
            "token": {
              "column": "token",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 100,
              "synth_cardinality": 100
            },
            "zip": {
              "column": "zip",
              "deviation": 0.0,
              "passed": true,
              "ratio": 1.0,
              "real_cardinality": 1,
              "synth_cardinality": 1
            }
          },
          "format_preservation": {
            "customer_email": {
              "column": "customer_email",
              "delta": 0.0,
              "detected_format": "email",
              "passed": true,
              "real_format_rate": 1.0,
              "synth_format_rate": 1.0
            },
            "ssn": {
              "column": "ssn",
              "delta": 0.0,
              "detected_format": "ssn_us",
              "passed": true,
              "real_format_rate": 1.0,
              "synth_format_rate": 1.0
            },
            "token": {
              "column": "token",
              "delta": 0.0,
              "detected_format": "ssn_us",
              "passed": true,
              "real_format_rate": 1.0,
              "synth_format_rate": 1.0
            },
            "zip": {
              "column": "zip",
              "delta": 0.0,
              "detected_format": "zip_us",
              "passed": true,
              "real_format_rate": 1.0,
              "synth_format_rate": 1.0
            }
          },
          "passing_rate": 1.0,
          "string_similarity": {
            "city": {
              "column": "city",
              "cosine_similarity": 1.0000000000000002,
              "ngram_n": 3,
              "score": 100.0
            },
            "country": {
              "column": "country",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            },
            "customer_email": {
              "column": "customer_email",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            },
            "discount_code": {
              "column": "discount_code",
              "cosine_similarity": 0.9999999999999999,
              "ngram_n": 3,
              "score": 100.0
            },
            "iban": {
              "column": "iban",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            },
            "notes": {
              "column": "notes",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            },
            "order_date": {
              "column": "order_date",
              "cosine_similarity": 0.9999999999999999,
              "ngram_n": 3,
              "score": 100.0
            },
            "placed_at": {
              "column": "placed_at",
              "cosine_similarity": 0.9999999999999999,
              "ngram_n": 3,
              "score": 100.0
            },
            "region": {
              "column": "region",
              "cosine_similarity": 1.0000000000000002,
              "ngram_n": 3,
              "score": 100.0
            },
            "shipped_at": {
              "column": "shipped_at",
              "cosine_similarity": 0.9999999999999999,
              "ngram_n": 3,
              "score": 100.0
            },
            "ssn": {
              "column": "ssn",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            },
            "state": {
              "column": "state",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            },
            "status": {
              "column": "status",
              "cosine_similarity": 0.9999999999999999,
              "ngram_n": 3,
              "score": 100.0
            },
            "tier": {
              "column": "tier",
              "cosine_similarity": 1.0000000000000002,
              "ngram_n": 3,
              "score": 100.0
            },
            "token": {
              "column": "token",
              "cosine_similarity": 1.0,
              "ngram_n": 3,
              "score": 100.0
            },
            "zip": {
              "column": "zip",
              "cosine_similarity": 1.0000000000000002,
              "ngram_n": 3,
              "score": 100.0
            }
          }
        }
      },
      "tier": 2
    }
    {
      "notes": [],
      "passed": true,
      "tables": {
        "customers": {
          "comparison": {
            "columns_compared": 10,
            "edge_overlap": 1.0,
            "mutual_information_max_abs_diff": 0.0,
            "mutual_information_mean_abs_diff": 0.0
          },
          "reference": {
            "column_order": [
              "customer_id",
              "id",
              "age",
              "name",
              "email",
              "region",
              "city",
              "born",
              "income",
              "churned"
            ],
            "edges": [
              {
                "child": "email",
                "mutual_information": 2.9957322731540414,
                "parent": "name"
              },
              {
                "child": "income",
                "mutual_information": 2.3025850928941454,
                "parent": "id"
              },
              {
                "child": "age",
                "mutual_information": 2.3025850928941454,
                "parent": "id"
              },
              {
                "child": "income",
                "mutual_information": 2.3025850928941454,
                "parent": "customer_id"
              },
              {
                "child": "income",
                "mutual_information": 2.302585092794146,
                "parent": "name"
              },
              {
                "child": "churned",
                "mutual_information": 0.6931471805564454,
                "parent": "region"
              },
              {
                "child": "region",
                "mutual_information": 0.6931471805204458,
                "parent": "name"
              },
              {
                "child": "city",
                "mutual_information": 0.0,
                "parent": "region"
              },
              {
                "child": "born",
                "mutual_information": 0.0,
                "parent": "region"
              }
            ],
            "mutual_info_matrix": {
              "age": {
                "born": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_id": 2.3025850928941454,
                "email": 2.302585092794146,
                "id": 2.3025850928941454,
                "income": 2.3025850928941454,
                "name": 2.302585092794146,
                "region": 0.0
              },
              "born": {
                "age": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_id": 0.0,
                "email": 0.0,
                "id": 0.0,
                "income": 0.0,
                "name": 0.0,
                "region": 0.0
              },
              "churned": {
                "age": 0.0,
                "born": 0.0,
                "city": 0.0,
                "customer_id": 0.0,
                "email": 0.6931471805204458,
                "id": 0.0,
                "income": 0.0,
                "name": 0.6931471805204458,
                "region": 0.6931471805564454
              },
              "city": {
                "age": 0.0,
                "born": 0.0,
                "churned": 0.0,
                "customer_id": 0.0,
                "email": 0.0,
                "id": 0.0,
                "income": 0.0,
                "name": 0.0,
                "region": 0.0
              },
              "customer_id": {
                "age": 2.3025850928941454,
                "born": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "email": 2.302585092794146,
                "id": 2.3025850928941454,
                "income": 2.3025850928941454,
                "name": 2.302585092794146,
                "region": 0.0
              },
              "email": {
                "age": 2.302585092794146,
                "born": 0.0,
                "churned": 0.6931471805204458,
                "city": 0.0,
                "customer_id": 2.302585092794146,
                "id": 2.302585092794146,
                "income": 2.302585092794146,
                "name": 2.9957322731540414,
                "region": 0.6931471805204458
              },
              "id": {
                "age": 2.3025850928941454,
                "born": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_id": 2.3025850928941454,
                "email": 2.302585092794146,
                "income": 2.3025850928941454,
                "name": 2.302585092794146,
                "region": 0.0
              },
              "income": {
                "age": 2.3025850928941454,
                "born": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_id": 2.3025850928941454,
                "email": 2.302585092794146,
                "id": 2.3025850928941454,
                "name": 2.302585092794146,
                "region": 0.0
              },
              "name": {
                "age": 2.302585092794146,
                "born": 0.0,
                "churned": 0.6931471805204458,
                "city": 0.0,
                "customer_id": 2.302585092794146,
                "email": 2.9957322731540414,
                "id": 2.302585092794146,
                "income": 2.302585092794146,
                "region": 0.6931471805204458
              },
              "region": {
                "age": 0.0,
                "born": 0.0,
                "churned": 0.6931471805564454,
                "city": 0.0,
                "customer_id": 0.0,
                "email": 0.6931471805204458,
                "id": 0.0,
                "income": 0.0,
                "name": 0.6931471805204458
              }
            }
          },
          "synthetic": {
            "column_order": [
              "customer_id",
              "id",
              "age",
              "name",
              "email",
              "region",
              "city",
              "born",
              "income",
              "churned"
            ],
            "edges": [
              {
                "child": "email",
                "mutual_information": 2.9957322731540414,
                "parent": "name"
              },
              {
                "child": "income",
                "mutual_information": 2.3025850928941454,
                "parent": "id"
              },
              {
                "child": "age",
                "mutual_information": 2.3025850928941454,
                "parent": "id"
              },
              {
                "child": "income",
                "mutual_information": 2.3025850928941454,
                "parent": "customer_id"
              },
              {
                "child": "income",
                "mutual_information": 2.302585092794146,
                "parent": "name"
              },
              {
                "child": "churned",
                "mutual_information": 0.6931471805564454,
                "parent": "region"
              },
              {
                "child": "region",
                "mutual_information": 0.6931471805204458,
                "parent": "name"
              },
              {
                "child": "city",
                "mutual_information": 0.0,
                "parent": "region"
              },
              {
                "child": "born",
                "mutual_information": 0.0,
                "parent": "region"
              }
            ],
            "mutual_info_matrix": {
              "age": {
                "born": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_id": 2.3025850928941454,
                "email": 2.302585092794146,
                "id": 2.3025850928941454,
                "income": 2.3025850928941454,
                "name": 2.302585092794146,
                "region": 0.0
              },
              "born": {
                "age": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_id": 0.0,
                "email": 0.0,
                "id": 0.0,
                "income": 0.0,
                "name": 0.0,
                "region": 0.0
              },
              "churned": {
                "age": 0.0,
                "born": 0.0,
                "city": 0.0,
                "customer_id": 0.0,
                "email": 0.6931471805204458,
                "id": 0.0,
                "income": 0.0,
                "name": 0.6931471805204458,
                "region": 0.6931471805564454
              },
              "city": {
                "age": 0.0,
                "born": 0.0,
                "churned": 0.0,
                "customer_id": 0.0,
                "email": 0.0,
                "id": 0.0,
                "income": 0.0,
                "name": 0.0,
                "region": 0.0
              },
              "customer_id": {
                "age": 2.3025850928941454,
                "born": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "email": 2.302585092794146,
                "id": 2.3025850928941454,
                "income": 2.3025850928941454,
                "name": 2.302585092794146,
                "region": 0.0
              },
              "email": {
                "age": 2.302585092794146,
                "born": 0.0,
                "churned": 0.6931471805204458,
                "city": 0.0,
                "customer_id": 2.302585092794146,
                "id": 2.302585092794146,
                "income": 2.302585092794146,
                "name": 2.9957322731540414,
                "region": 0.6931471805204458
              },
              "id": {
                "age": 2.3025850928941454,
                "born": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_id": 2.3025850928941454,
                "email": 2.302585092794146,
                "income": 2.3025850928941454,
                "name": 2.302585092794146,
                "region": 0.0
              },
              "income": {
                "age": 2.3025850928941454,
                "born": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_id": 2.3025850928941454,
                "email": 2.302585092794146,
                "id": 2.3025850928941454,
                "name": 2.302585092794146,
                "region": 0.0
              },
              "name": {
                "age": 2.302585092794146,
                "born": 0.0,
                "churned": 0.6931471805204458,
                "city": 0.0,
                "customer_id": 2.302585092794146,
                "email": 2.9957322731540414,
                "id": 2.302585092794146,
                "income": 2.302585092794146,
                "region": 0.6931471805204458
              },
              "region": {
                "age": 0.0,
                "born": 0.0,
                "churned": 0.6931471805564454,
                "city": 0.0,
                "customer_id": 0.0,
                "email": 0.6931471805204458,
                "id": 0.0,
                "income": 0.0,
                "name": 0.6931471805204458
              }
            }
          }
        },
        "orders": {
          "comparison": {
            "columns_compared": 23,
            "edge_overlap": 1.0,
            "mutual_information_max_abs_diff": 0.0,
            "mutual_information_mean_abs_diff": 0.0
          },
          "reference": {
            "column_order": [
              "order_id",
              "customer_id",
              "customer_email",
              "status",
              "amount",
              "order_total",
              "placed_at",
              "shipped_at",
              "order_date",
              "discount_code",
              "is_gift",
              "region",
              "tier",
              "churned",
              "zip",
              "city",
              "state",
              "country",
              "iban",
              "notes",
              "token",
              "ssn",
              "salary"
            ],
            "edges": [
              {
                "child": "ssn",
                "mutual_information": 4.6051701759881025,
                "parent": "token"
              },
              {
                "child": "order_date",
                "mutual_information": 3.322441370150877,
                "parent": "shipped_at"
              },
              {
                "child": "shipped_at",
                "mutual_information": 3.322441370150877,
                "parent": "placed_at"
              },
              {
                "child": "token",
                "mutual_information": 3.322441368150873,
                "parent": "shipped_at"
              },
              {
                "child": "token",
                "mutual_information": 2.9957322715540413,
                "parent": "customer_email"
              },
              {
                "child": "salary",
                "mutual_information": 2.3025850928941454,
                "parent": "order_total"
              },
              {
                "child": "salary",
                "mutual_information": 2.3025850928941454,
                "parent": "order_id"
              },
              {
                "child": "amount",
                "mutual_information": 2.3025850928941454,
                "parent": "order_id"
              },
              {
                "child": "customer_email",
                "mutual_information": 2.302585092794146,
                "parent": "customer_id"
              },
              {
                "child": "salary",
                "mutual_information": 2.302585091994144,
                "parent": "token"
              },
              {
                "child": "churned",
                "mutual_information": 0.6931471805564454,
                "parent": "region"
              },
              {
                "child": "region",
                "mutual_information": 0.6931471805204458,
                "parent": "customer_email"
              },
              {
                "child": "is_gift",
                "mutual_information": 0.3250829733522685,
                "parent": "customer_email"
              },
              {
                "child": "token",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "state",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "notes",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "iban",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "country",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "city",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "zip",
                "mutual_information": 0.0,
                "parent": "tier"
              },
              {
                "child": "zip",
                "mutual_information": 0.0,
                "parent": "status"
              },
              {
                "child": "discount_code",
                "mutual_information": 0.0,
                "parent": "status"
              }
            ],
            "mutual_info_matrix": {
              "amount": {
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471803604438,
                "customer_id": 0.5545177443485566,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 1.0198562776771611,
                "order_id": 2.3025850928941454,
                "order_total": 2.3025850928941454,
                "placed_at": 1.0198562776771611,
                "region": 0.0,
                "salary": 2.3025850928941454,
                "shipped_at": 1.0198562776771611,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "churned": {
                "amount": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471805204458,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.07488176161930438,
                "notes": 0.0,
                "order_date": 0.6931471805044447,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.6931471805044447,
                "region": 0.6931471805564454,
                "salary": 0.0,
                "shipped_at": 0.6931471805044447,
                "ssn": 0.6931471803604438,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.6931471803604438,
                "zip": 0.0
              },
              "city": {
                "amount": 0.0,
                "churned": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "country": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "customer_email": {
                "amount": 0.6931471803604438,
                "churned": 0.6931471805204458,
                "city": 0.0,
                "country": 0.0,
                "customer_id": 2.302585092794146,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.3250829733522685,
                "notes": 0.0,
                "order_date": 1.7130034579569269,
                "order_id": 0.6931471803604438,
                "order_total": 0.6931471803604438,
                "placed_at": 1.7130034579569269,
                "region": 0.6931471805204458,
                "salary": 0.6931471803604438,
                "shipped_at": 1.7130034579569269,
                "ssn": 2.9957322715540413,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.9957322715540413,
                "zip": 0.0
              },
              "customer_id": {
                "amount": 0.5545177443485566,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 2.302585092794146,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.18645353726037928,
                "notes": 0.0,
                "order_date": 1.019856277677161,
                "order_id": 0.5545177443485565,
                "order_total": 0.5545177443485566,
                "placed_at": 1.019856277677161,
                "region": 0.0,
                "salary": 0.5545177443485566,
                "shipped_at": 1.019856277677161,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "discount_code": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "iban": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "is_gift": {
                "amount": 0.0,
                "churned": 0.07488176161930438,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.3250829733522685,
                "customer_id": 0.18645353726037928,
                "discount_code": 0.0,
                "iban": 0.0,
                "notes": 0.0,
                "order_date": 0.11034285769703006,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.11034285769703006,
                "region": 0.07488176161930438,
                "salary": 0.0,
                "shipped_at": 0.11034285769703006,
                "ssn": 0.3250829731922686,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.3250829731922686,
                "zip": 0.0
              },
              "notes": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "order_date": {
                "amount": 1.0198562776771611,
                "churned": 0.6931471805044447,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 1.7130034579569269,
                "customer_id": 1.019856277677161,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.11034285769703006,
                "notes": 0.0,
                "order_id": 1.0198562776771611,
                "order_total": 1.0198562776771611,
                "placed_at": 3.322441370150877,
                "region": 0.6931471805044447,
                "salary": 1.01985627767716,
                "shipped_at": 3.322441370150877,
                "ssn": 3.322441368150873,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 3.322441368150873,
                "zip": 0.0
              },
              "order_id": {
                "amount": 2.3025850928941454,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471803604438,
                "customer_id": 0.5545177443485565,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 1.0198562776771611,
                "order_total": 2.3025850928941454,
                "placed_at": 1.0198562776771611,
                "region": 0.0,
                "salary": 2.3025850928941454,
                "shipped_at": 1.0198562776771611,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "order_total": {
                "amount": 2.3025850928941454,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471803604438,
                "customer_id": 0.5545177443485566,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 1.0198562776771611,
                "order_id": 2.3025850928941454,
                "placed_at": 1.0198562776771611,
                "region": 0.0,
                "salary": 2.3025850928941454,
                "shipped_at": 1.0198562776771611,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "placed_at": {
                "amount": 1.0198562776771611,
                "churned": 0.6931471805044447,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 1.7130034579569269,
                "customer_id": 1.019856277677161,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.11034285769703006,
                "notes": 0.0,
                "order_date": 3.322441370150877,
                "order_id": 1.0198562776771611,
                "order_total": 1.0198562776771611,
                "region": 0.6931471805044447,
                "salary": 1.01985627767716,
                "shipped_at": 3.322441370150877,
                "ssn": 3.322441368150873,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 3.322441368150873,
                "zip": 0.0
              },
              "region": {
                "amount": 0.0,
                "churned": 0.6931471805564454,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471805204458,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.07488176161930438,
                "notes": 0.0,
                "order_date": 0.6931471805044447,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.6931471805044447,
                "salary": 0.0,
                "shipped_at": 0.6931471805044447,
                "ssn": 0.6931471803604438,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.6931471803604438,
                "zip": 0.0
              },
              "salary": {
                "amount": 2.3025850928941454,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471803604438,
                "customer_id": 0.5545177443485566,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 1.01985627767716,
                "order_id": 2.3025850928941454,
                "order_total": 2.3025850928941454,
                "placed_at": 1.01985627767716,
                "region": 0.0,
                "shipped_at": 1.01985627767716,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "shipped_at": {
                "amount": 1.0198562776771611,
                "churned": 0.6931471805044447,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 1.7130034579569269,
                "customer_id": 1.019856277677161,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.11034285769703006,
                "notes": 0.0,
                "order_date": 3.322441370150877,
                "order_id": 1.0198562776771611,
                "order_total": 1.0198562776771611,
                "placed_at": 3.322441370150877,
                "region": 0.6931471805044447,
                "salary": 1.01985627767716,
                "ssn": 3.322441368150873,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 3.322441368150873,
                "zip": 0.0
              },
              "ssn": {
                "amount": 2.302585091994144,
                "churned": 0.6931471803604438,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 2.9957322715540413,
                "customer_id": 2.302585091994144,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.3250829731922686,
                "notes": 0.0,
                "order_date": 3.322441368150873,
                "order_id": 2.302585091994144,
                "order_total": 2.302585091994144,
                "placed_at": 3.322441368150873,
                "region": 0.6931471803604438,
                "salary": 2.302585091994144,
                "shipped_at": 3.322441368150873,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 4.6051701759881025,
                "zip": 0.0
              },
              "state": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "status": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "tier": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "token": {
                "amount": 2.302585091994144,
                "churned": 0.6931471803604438,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 2.9957322715540413,
                "customer_id": 2.302585091994144,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.3250829731922686,
                "notes": 0.0,
                "order_date": 3.322441368150873,
                "order_id": 2.302585091994144,
                "order_total": 2.302585091994144,
                "placed_at": 3.322441368150873,
                "region": 0.6931471803604438,
                "salary": 2.302585091994144,
                "shipped_at": 3.322441368150873,
                "ssn": 4.6051701759881025,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "zip": 0.0
              },
              "zip": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0
              }
            }
          },
          "synthetic": {
            "column_order": [
              "order_id",
              "customer_id",
              "customer_email",
              "status",
              "amount",
              "order_total",
              "placed_at",
              "shipped_at",
              "order_date",
              "discount_code",
              "is_gift",
              "region",
              "tier",
              "churned",
              "zip",
              "city",
              "state",
              "country",
              "iban",
              "notes",
              "token",
              "ssn",
              "salary"
            ],
            "edges": [
              {
                "child": "ssn",
                "mutual_information": 4.6051701759881025,
                "parent": "token"
              },
              {
                "child": "order_date",
                "mutual_information": 3.322441370150877,
                "parent": "shipped_at"
              },
              {
                "child": "shipped_at",
                "mutual_information": 3.322441370150877,
                "parent": "placed_at"
              },
              {
                "child": "token",
                "mutual_information": 3.322441368150873,
                "parent": "shipped_at"
              },
              {
                "child": "token",
                "mutual_information": 2.9957322715540413,
                "parent": "customer_email"
              },
              {
                "child": "salary",
                "mutual_information": 2.3025850928941454,
                "parent": "order_total"
              },
              {
                "child": "salary",
                "mutual_information": 2.3025850928941454,
                "parent": "order_id"
              },
              {
                "child": "amount",
                "mutual_information": 2.3025850928941454,
                "parent": "order_id"
              },
              {
                "child": "customer_email",
                "mutual_information": 2.302585092794146,
                "parent": "customer_id"
              },
              {
                "child": "salary",
                "mutual_information": 2.302585091994144,
                "parent": "token"
              },
              {
                "child": "churned",
                "mutual_information": 0.6931471805564454,
                "parent": "region"
              },
              {
                "child": "region",
                "mutual_information": 0.6931471805204458,
                "parent": "customer_email"
              },
              {
                "child": "is_gift",
                "mutual_information": 0.3250829733522685,
                "parent": "customer_email"
              },
              {
                "child": "token",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "state",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "notes",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "iban",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "country",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "city",
                "mutual_information": 0.0,
                "parent": "zip"
              },
              {
                "child": "zip",
                "mutual_information": 0.0,
                "parent": "tier"
              },
              {
                "child": "zip",
                "mutual_information": 0.0,
                "parent": "status"
              },
              {
                "child": "discount_code",
                "mutual_information": 0.0,
                "parent": "status"
              }
            ],
            "mutual_info_matrix": {
              "amount": {
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471803604438,
                "customer_id": 0.5545177443485566,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 1.0198562776771611,
                "order_id": 2.3025850928941454,
                "order_total": 2.3025850928941454,
                "placed_at": 1.0198562776771611,
                "region": 0.0,
                "salary": 2.3025850928941454,
                "shipped_at": 1.0198562776771611,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "churned": {
                "amount": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471805204458,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.07488176161930438,
                "notes": 0.0,
                "order_date": 0.6931471805044447,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.6931471805044447,
                "region": 0.6931471805564454,
                "salary": 0.0,
                "shipped_at": 0.6931471805044447,
                "ssn": 0.6931471803604438,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.6931471803604438,
                "zip": 0.0
              },
              "city": {
                "amount": 0.0,
                "churned": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "country": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "customer_email": {
                "amount": 0.6931471803604438,
                "churned": 0.6931471805204458,
                "city": 0.0,
                "country": 0.0,
                "customer_id": 2.302585092794146,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.3250829733522685,
                "notes": 0.0,
                "order_date": 1.7130034579569269,
                "order_id": 0.6931471803604438,
                "order_total": 0.6931471803604438,
                "placed_at": 1.7130034579569269,
                "region": 0.6931471805204458,
                "salary": 0.6931471803604438,
                "shipped_at": 1.7130034579569269,
                "ssn": 2.9957322715540413,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.9957322715540413,
                "zip": 0.0
              },
              "customer_id": {
                "amount": 0.5545177443485566,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 2.302585092794146,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.18645353726037928,
                "notes": 0.0,
                "order_date": 1.019856277677161,
                "order_id": 0.5545177443485565,
                "order_total": 0.5545177443485566,
                "placed_at": 1.019856277677161,
                "region": 0.0,
                "salary": 0.5545177443485566,
                "shipped_at": 1.019856277677161,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "discount_code": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "iban": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "is_gift": {
                "amount": 0.0,
                "churned": 0.07488176161930438,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.3250829733522685,
                "customer_id": 0.18645353726037928,
                "discount_code": 0.0,
                "iban": 0.0,
                "notes": 0.0,
                "order_date": 0.11034285769703006,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.11034285769703006,
                "region": 0.07488176161930438,
                "salary": 0.0,
                "shipped_at": 0.11034285769703006,
                "ssn": 0.3250829731922686,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.3250829731922686,
                "zip": 0.0
              },
              "notes": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "order_date": {
                "amount": 1.0198562776771611,
                "churned": 0.6931471805044447,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 1.7130034579569269,
                "customer_id": 1.019856277677161,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.11034285769703006,
                "notes": 0.0,
                "order_id": 1.0198562776771611,
                "order_total": 1.0198562776771611,
                "placed_at": 3.322441370150877,
                "region": 0.6931471805044447,
                "salary": 1.01985627767716,
                "shipped_at": 3.322441370150877,
                "ssn": 3.322441368150873,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 3.322441368150873,
                "zip": 0.0
              },
              "order_id": {
                "amount": 2.3025850928941454,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471803604438,
                "customer_id": 0.5545177443485565,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 1.0198562776771611,
                "order_total": 2.3025850928941454,
                "placed_at": 1.0198562776771611,
                "region": 0.0,
                "salary": 2.3025850928941454,
                "shipped_at": 1.0198562776771611,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "order_total": {
                "amount": 2.3025850928941454,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471803604438,
                "customer_id": 0.5545177443485566,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 1.0198562776771611,
                "order_id": 2.3025850928941454,
                "placed_at": 1.0198562776771611,
                "region": 0.0,
                "salary": 2.3025850928941454,
                "shipped_at": 1.0198562776771611,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "placed_at": {
                "amount": 1.0198562776771611,
                "churned": 0.6931471805044447,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 1.7130034579569269,
                "customer_id": 1.019856277677161,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.11034285769703006,
                "notes": 0.0,
                "order_date": 3.322441370150877,
                "order_id": 1.0198562776771611,
                "order_total": 1.0198562776771611,
                "region": 0.6931471805044447,
                "salary": 1.01985627767716,
                "shipped_at": 3.322441370150877,
                "ssn": 3.322441368150873,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 3.322441368150873,
                "zip": 0.0
              },
              "region": {
                "amount": 0.0,
                "churned": 0.6931471805564454,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471805204458,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.07488176161930438,
                "notes": 0.0,
                "order_date": 0.6931471805044447,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.6931471805044447,
                "salary": 0.0,
                "shipped_at": 0.6931471805044447,
                "ssn": 0.6931471803604438,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.6931471803604438,
                "zip": 0.0
              },
              "salary": {
                "amount": 2.3025850928941454,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.6931471803604438,
                "customer_id": 0.5545177443485566,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 1.01985627767716,
                "order_id": 2.3025850928941454,
                "order_total": 2.3025850928941454,
                "placed_at": 1.01985627767716,
                "region": 0.0,
                "shipped_at": 1.01985627767716,
                "ssn": 2.302585091994144,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 2.302585091994144,
                "zip": 0.0
              },
              "shipped_at": {
                "amount": 1.0198562776771611,
                "churned": 0.6931471805044447,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 1.7130034579569269,
                "customer_id": 1.019856277677161,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.11034285769703006,
                "notes": 0.0,
                "order_date": 3.322441370150877,
                "order_id": 1.0198562776771611,
                "order_total": 1.0198562776771611,
                "placed_at": 3.322441370150877,
                "region": 0.6931471805044447,
                "salary": 1.01985627767716,
                "ssn": 3.322441368150873,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 3.322441368150873,
                "zip": 0.0
              },
              "ssn": {
                "amount": 2.302585091994144,
                "churned": 0.6931471803604438,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 2.9957322715540413,
                "customer_id": 2.302585091994144,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.3250829731922686,
                "notes": 0.0,
                "order_date": 3.322441368150873,
                "order_id": 2.302585091994144,
                "order_total": 2.302585091994144,
                "placed_at": 3.322441368150873,
                "region": 0.6931471803604438,
                "salary": 2.302585091994144,
                "shipped_at": 3.322441368150873,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 4.6051701759881025,
                "zip": 0.0
              },
              "state": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "status": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "tier": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "tier": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "token": 0.0,
                "zip": 0.0
              },
              "token": {
                "amount": 2.302585091994144,
                "churned": 0.6931471803604438,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 2.9957322715540413,
                "customer_id": 2.302585091994144,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.3250829731922686,
                "notes": 0.0,
                "order_date": 3.322441368150873,
                "order_id": 2.302585091994144,
                "order_total": 2.302585091994144,
                "placed_at": 3.322441368150873,
                "region": 0.6931471803604438,
                "salary": 2.302585091994144,
                "shipped_at": 3.322441368150873,
                "ssn": 4.6051701759881025,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "zip": 0.0
              },
              "zip": {
                "amount": 0.0,
                "churned": 0.0,
                "city": 0.0,
                "country": 0.0,
                "customer_email": 0.0,
                "customer_id": 0.0,
                "discount_code": 0.0,
                "iban": 0.0,
                "is_gift": 0.0,
                "notes": 0.0,
                "order_date": 0.0,
                "order_id": 0.0,
                "order_total": 0.0,
                "placed_at": 0.0,
                "region": 0.0,
                "salary": 0.0,
                "shipped_at": 0.0,
                "ssn": 0.0,
                "state": 0.0,
                "status": 0.0,
                "tier": 0.0,
                "token": 0.0
              }
            }
          }
        }
      },
      "tier": 3
    }
    {
      "drifted": false,
      "method": "psi",
      "missing_tables": [],
      "tables": {
        "customers": {
          "columns": {
            "age": {
              "column": "age",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "born": {
              "column": "born",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "churned": {
              "column": "churned",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "city": {
              "column": "city",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "customer_id": {
              "column": "customer_id",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "email": {
              "column": "email",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "id": {
              "column": "id",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "income": {
              "column": "income",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "name": {
              "column": "name",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "region": {
              "column": "region",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            }
          },
          "drift_fraction": 0.0,
          "drifted_columns": [],
          "overall_drift_score": 0.0,
          "skipped": {}
        },
        "orders": {
          "columns": {
            "amount": {
              "column": "amount",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "churned": {
              "column": "churned",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "city": {
              "column": "city",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "country": {
              "column": "country",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "customer_email": {
              "column": "customer_email",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "customer_id": {
              "column": "customer_id",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "discount_code": {
              "column": "discount_code",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "iban": {
              "column": "iban",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "is_gift": {
              "column": "is_gift",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "notes": {
              "column": "notes",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "order_date": {
              "column": "order_date",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "order_id": {
              "column": "order_id",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "order_total": {
              "column": "order_total",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "placed_at": {
              "column": "placed_at",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "region": {
              "column": "region",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "salary": {
              "column": "salary",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "shipped_at": {
              "column": "shipped_at",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "state": {
              "column": "state",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "status": {
              "column": "status",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "tier": {
              "column": "tier",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            },
            "zip": {
              "column": "zip",
              "drift_score": 0.0,
              "is_drifted": false,
              "method": "psi",
              "p_value": null,
              "psi": 0.0,
              "test_statistic": 0.0
            }
          },
          "drift_fraction": 0.0,
          "drifted_columns": [],
          "overall_drift_score": 0.0,
          "skipped": {
            "ssn": "more than 50 distinct values",
            "token": "more than 50 distinct values"
          }
        }
      }
    }
    ```
