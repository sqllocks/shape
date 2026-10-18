# Fidelity report

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" FIDELITY
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for FIDELITY
    ```


`shape fidelity REFERENCE SYNTHETIC` (alias `shape compare`) scores how closely synthetic tables
follow reference tables: every column from 0 to 100, every table as the mean of its columns, and
the whole as the mean of its tables. The same function is `shape.generation.report.compare_tables`.

[Run this example](#local-example-0).


`REFERENCE` and `SYNTHETIC` are a file or a directory of one file per table (Parquet, CSV or
JSONL; `--input-format` forces one). Tables are paired by file name; two single files are paired
whatever they are called. Exit codes: **0** every pass mark is met, **1** one is not, **2** the
input is unusable (a missing path, no data files, an unknown report extension).

`shape fidelity REFERENCE SYNTHETIC --tier 1|2|3` runs the deeper checks (mixture fits, adversarial
score, formats, dependency trees); see `docs/FIDELITY_TIERS.md`.

`shape fidelity PROFILE.json DATA.csv` (a first argument ending in `.json`) certifies a CSV file
against a captured profile instead, with `--tolerance`, and exits 3 on failure. A column the data
lacks scores 0 there too, and a profile that describes no columns fails. The reference must be a
capture document (`shape capture`, every column with a `kind`); any other JSON, a profile included,
is an error.

## Pass marks

The defaults pass the datasets the engine is held to: the baseline's own seeds score 89 to 92 overall
and 78 to 99 per table on retail.

| Option | Default | Fails when |
|---|---|---|
| `--min-score` | 85 | the overall score is below it |
| `--min-table-score` | 70 | any reference table scores below it |
| `--min-column-score` | none | any present column scores below it |

Whatever the marks, these always fail: a reference table or column missing from the synthetic data,
a reference with no tables, and a reference table with no columns or no rows. Columns and tables
only the synthetic data has are listed and do not change the score.

## The score of a column (100 points)

| Points | What | Full marks when |
|---|---|---|
| 10 | kind | both are numeric, both datetime, or both neither (text, booleans) |
| 10 | null rate | the null rates are equal (`10 * (1 - delta)`); NaN counts as null |
| 10 | cardinality | the distinct non-null counts are equal (`10 * (1 - abs(1 - ratio))`, floor 0) |
| 20 | mean (numeric, datetime) | the means are equal; `20 * (1 - delta / reference std)`, floor 0 |
| 10 | spread (numeric, datetime) | the standard deviations are equal, same form |
| 10 | Kolmogorov-Smirnov (numeric, datetime) | `10 * (1 - KS statistic)`; 5 when either side has fewer than 5 values |
| 20 | overlap (other kinds) | Jaccard similarity of the sets of distinct values |
| 20 | chi-squared (other kinds) | 20 when p > 0.05, else `20 * (1 - min(chi2 / 100, 1))`; 10 when it cannot be run |

Kinds: integers, floats and decimals are numeric; timestamps and dates are datetime; booleans and
other text are categorical. A text column whose every value parses as a number is numeric, and one
whose values are at least 95% ISO 8601 dates or datetimes (`2024-05-01`, `2024-05-01 12:30:00`,
`2024-05-01T12:30:00`) is datetime. Datetimes are compared as nanoseconds since the epoch, whatever
their Arrow unit, in wall-clock time for zoned timestamps.

## What differs from comparing only the shared columns

* A column the reference has and the synthetic data lacks scores **0** and counts in its table's
  mean, so a table that lost a column cannot score as if nothing happened.
* A table that is missing scores 0 and counts in the overall mean.
* An empty reference fails (score 0).
* Two datetime columns of the same instants in different units score as equal.

For every other pair, the scores equal those of the comparator the engine's equivalence standard
(plan T-21 clause h) asserts per table. The comparison harness (`fidelity_1to1/run.py` in the
repository's benchmarks) runs both on retail's datasets and on edge pairs and reports every
table's two scores.

## Reports

`-o FILE` writes the report in the `shape.reports` format for its extension: `.json`, `.md` or
`.html` (repeat `-o` for several). `--format` chooses what is printed (default `json`). A plugin
adds a format with the `shape.reports` entry-point group (`render(report) -> bytes`, deterministic;
`docs/plugins/authoring.md`). The report is the mapping `FidelityReport.to_dict()` returns: the
overall score, the verdict and its `failures`, the pass marks, and per table and column every
metric above (non-finite numbers are `null`).

## Live fidelity

`shape emit --live-target ...` computes this same score on a running event stream and alerts when it
drifts; at any moment it equals `shape fidelity` of the reference against the events delivered so
far (`docs/EMIT.md`, "Live fidelity"; checked by `benchmarks/live_fidelity/run.py`).


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape fidelity real/ synthetic/ -o report.html -o report.json --min-score 85
```

??? info "Output (exit 0)"

    ```text {.expected}
    {
      "extra_tables": [],
      "failures": [],
      "issues": [],
      "missing_tables": [],
      "overall_score": 95.77639751552795,
      "passed": true,
      "tables": {
        "customers": {
          "columns": {
            "age": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "born": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": 0.0,
              "value_overlap": null
            },
            "churned": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "city": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "customer_id": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "email": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "id": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "income": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "name": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "region": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            }
          },
          "extra_columns": [],
          "issues": [],
          "missing_columns": [],
          "present": true,
          "row_count_real": 20,
          "row_count_synth": 20,
          "score": 97.14285714285714
        },
        "orders": {
          "columns": {
            "amount": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "churned": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "city": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "country": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "customer_email": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "customer_id": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "discount_code": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "iban": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "is_gift": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "notes": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "order_date": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "order_id": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "order_total": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "placed_at": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "region": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "salary": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "shipped_at": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": 1.0,
              "value_overlap": null
            },
            "ssn": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "state": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "status": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "tier": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "token": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": 1.0,
              "chi2_statistic": 0.0,
              "dtype_match": true,
              "ks_statistic": null,
              "mean_delta": null,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 100.0,
              "std_ratio": null,
              "value_overlap": 1.0
            },
            "zip": {
              "cardinality_ratio": 1.0,
              "chi2_pvalue": null,
              "chi2_statistic": null,
              "dtype_match": true,
              "ks_statistic": 0.0,
              "mean_delta": 0.0,
              "null_rate_delta": 0.0,
              "present": true,
              "score": 85.71428571428571,
              "std_ratio": 0.0,
              "value_overlap": null
            }
          },
          "extra_columns": [],
          "issues": [],
          "missing_columns": [],
          "present": true,
          "row_count_real": 100,
          "row_count_synth": 100,
          "score": 94.40993788819877
        }
      },
      "thresholds": {
        "min_column": null,
        "min_overall": 85.0,
        "min_table": 70.0
      }
    }
    ```
