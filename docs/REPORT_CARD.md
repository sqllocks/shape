# The report card: is this synthetic dataset fit to use?

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" REPORT_CARD
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for REPORT_CARD
    ```


`shape report-card` runs the checks that are spread over `shape fidelity`, `shape fidelity --tier`
and `shape verify --source`, adds a membership-inference test, and states, per section, what
passed, what failed and what was not run. It is local: it reads files and writes a file.

[Run this example](#local-example-0).


`REAL` and `SYNTHETIC` are read as `shape fidelity` reads them: a file, or a directory with one file
per table (two single files are compared whatever they are called). `--holdout` has the layout of
`REAL`. The configuration is the `shape-verify-config` file of [VERIFY.md](VERIFY.md); the card has
no configuration format of its own.

| Flag | Meaning |
|---|---|
| `--config VERIFY.json` | the verify configuration (`classifications`, `memorization`, `utility`, `privacy`; other keys belong to `shape verify` and are ignored) |
| `--tiers 1,2` | the fidelity tiers to run (default `1,2`; `""` runs none; tier 3 is experimental and is not part of the card) |
| `--holdout HOLDOUT` | real rows that were not given to the generator: turns on the membership-inference test |
| `--manifest RUN_MANIFEST.json` | the run manifest of the generation: adds its reproducibility tuple and recorded dataset id |
| `-o CARD` | write the card; `.json`, `.md` or `.html` by extension; repeatable |
| `--json` | print the card as JSON instead of Markdown |
| `--require SECTION,...` | sections (`fidelity`, `utility`, `privacy`) that must have run |

Exit codes: `0` every section that ran passed; `1` a section failed, or a required one was not run;
`2` unusable input (a missing path, no table in common, a bad configuration, an unknown tier or
section, or `utility` required while scikit-learn is missing).

From Python:

```python
from shape.quality import report_card

card = report_card("real/", "synthetic/", config="verify.json", tiers=(1, 2),
                   holdout="holdout/", manifest="out/RUN_MANIFEST.json")
card.to_dict(); card.to_markdown(); card.to_html()
```

The inputs may also be mappings of table name to `pyarrow.Table`. `require=("utility",)` is the one
keyword beyond the arguments above. The renderers are also `shape.quality.reportcard.render_markdown`
and `render_html`, which take a card read with `shape.quality.load_report_card(path)`.

## The sections

A section is `pass` when none of its gates failed and at least one passed, `fail` when one failed,
and `not_run` (with a `reason`) when none ran. The card is `fail` when any section that ran failed.

### fidelity

* The base scores of `shape fidelity` (`shape.generation.report.compare_tables`), overall, per table
  and per column, with its pass marks (85 overall, 70 per table). Gates: `overall_score`,
  `table_score` per table, and `coverage` (a table or column missing from the synthetic data, or a
  reference with no rows, fails).
* **Tier 1** (`docs/FIDELITY_TIERS.md`): the adversarial test, a classifier that tries to tell real
  rows from synthetic rows. Gate `tier1_adversarial_auc` passes below 0.75 (the default of
  `shape fidelity --tier 1 --max-auc`); 0.5 means the two cannot be told apart. It needs scikit-learn
  (`pip install "sqllocks-shape[advanced]"`); without it the gate is `not_run`, not failed. The card
  also lists which columns have mixture fits and temporal profiles, never their numbers.
* **Tier 2**: format preservation, string similarity and cardinality, per table. Gate
  `tier2_pass_rate` passes at 1.0 (the default of `--min-pass-rate`).

The numbers are those of the commands, on the same inputs; a test compares them.

### utility

The utility gate of `shape verify --source` (train on synthetic, test on real; see
[VERIFY.md](VERIFY.md)). It runs when the configuration has a `utility` section and scikit-learn is
installed; gate `utility_retention`. Without a `utility` section, or without scikit-learn, the section
is `not_run` and its reason says which, with the `pip install` command. Retention measures how much
of the real model's score the synthetic model keeps for **one** target you chose; it says nothing
about other targets.

### privacy

* **Memorization** (`memorization` gate of `shape verify --source`): the exact-match rate, the
  closest-row distance (min, p05, median) and the indices of reproduced rows. It fails on a reproduced
  row in a column classified at or above `memorization.fail_at`. Without `classifications` it cannot
  fail, and a note says so.
* **Membership inference** (`membership_inference`, new). With `--holdout`, for up to
  `memorization.max_rows` (default 5,000) rows of `REAL` (members) and of the holdout (non-members) it
  takes the distance to the closest synthetic row (the standardised distance of the memorization
  gate, over the numeric columns shared by the three tables) and reports the **AUC** of telling
  members from non-members by that distance, with the p05 and median of both distance distributions.
  0.5 means the distance carries no signal; 1.0 means every member is closer to the synthetic data
  than every non-member. The test fails when the AUC is **above** `privacy.max_membership_auc`
  (default **0.6**, this test's own threshold; it is not a standard). The rows sampled depend on
  `privacy.seed` (default 0) when a side has more than `max_rows` rows; the result is deterministic
  for a seed. Without `--holdout` it is `not_run` (the reason says so); it is also `not_run` for a
  table with fewer than 10 rows on either side, with no common numeric column, or with no holdout
  table of that name.

```json
{"format": "shape-verify-config", "version": 1,
 "classifications": {"people.name": "CONFIDENTIAL"},
 "utility": {"table": "people", "target": "churned", "min_retention": 0.8},
 "memorization": {"max_rows": 5000},
 "privacy": {"max_membership_auc": 0.6, "seed": 0}}
```

## `--require`

`--require utility,privacy` turns what was not run into a failure, so CI cannot pass a card that
skipped something silently. A required section fails when it is `not_run`, **and also when one of its
tests was not run**: `--require privacy` without `--holdout` fails because the membership test did
not run, and `--require fidelity` fails when scikit-learn is missing and tier 1 could not run. The
reasons are in `overall_reasons`. The one exception is `utility` with scikit-learn missing, which is
exit `2`: the environment cannot do what was asked.

## The card

`{"format": "shape-report-card", "version": 1, ...}`:

| Key | Content |
|---|---|
| `shape_version` | the Shape that made the card |
| `inputs` | for `real`, `synthetic` and `holdout`: the `dataset_id` (`shape.repro.dataset_id`, [REPRODUCIBILITY.md](REPRODUCIBILITY.md)) and the row count per table; with `--manifest`, `manifest`: the run's `reproducibility` tuple, its recorded `dataset_id` and `dataset_id_matches` (`false` when the recorded id differs from the synthetic input's, `null` when none was recorded) |
| `sections` | `fidelity`, `utility`, `privacy`, each with `status`, `metrics`, `gates` (name, `status`, `table`, `value`, `threshold`, `relation`, `messages`, `reason`), `notes` and, for `not_run`, `reason` |
| `require`, `overall`, `overall_reasons` | the required sections, `pass` or `fail`, and why it failed |

A card has no timestamp, so the same inputs give the same card. A newer `version` is refused with a
message that says to upgrade; a verify configuration without a `privacy` section still loads.

No value from the data is written to any output: the card holds scores, counts, distances, table and
column names, and the **indices** of reproduced rows. A test builds a card from data with known values
and scans the JSON, Markdown and HTML for them. A dataset id that differs from the manifest's is
flagged, not failed: it can be a real difference or only a different file format.

## What a card does not say

A passing card is evidence, not a guarantee.

* A membership test with an AUC near 0.5 means this attack, with this distance, on these numeric
  columns, found no signal. A stronger attack, other features, or an attacker who knows more may do
  better. Text columns do not enter the distance (the memorization gate covers exact reproduction).
* The holdout must be real rows the generator never saw and that come from the same population as
  `REAL`. A holdout that overlaps `REAL`, or differs from it, makes the AUC meaningless.
* The AUC is estimated from the rows given; with few rows it is noisy, so give hundreds of rows per
  side at least.
* Fidelity is judged on marginals, pairs and one classifier; passing it does not mean every use of
  the data works. Utility is one model on one target.
* The card is not a differential-privacy statement and does not test attribute inference.
* The card is a local file. Nothing is published or attached anywhere.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape report-card real/ synthetic/ --config verify.json --holdout holdout/ \
    --manifest out/RUN_MANIFEST.json -o card.json -o card.md -o card.html
shape report-card real.parquet synthetic.parquet --json
shape report-card real/ synthetic/ --config verify.json --require utility,privacy   # for CI
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: error: file not found: out/RUN_MANIFEST.json
    {"format": "shape-result", "version": 1, "shape_version": "0.9.1", "inputs": {"real": {"dataset_id": "sha256:fb7614bf671a52ca551e8338196da66ba9bc2f56b8440fe06ddc885c1e228a32", "tables": {"real": 100}}, "synthetic": {"dataset_id": "sha256:c5deb2bc2fa5320cbea3043d39ba80557455fa97d4f002c9eff672251f299e35", "tables": {"synthetic": 100}}, "holdout": null, "manifest": null}, "sections": {"fidelity": {"status": "pass", "metrics": {"overall_score": 94.40993788819877, "thresholds": {"min_overall": 85.0, "min_table": 70.0, "min_column": null}, "missing_tables": [], "extra_tables": [], "issues": [], "tables": {"real": {"score": 94.40993788819877, "present": true, "row_count_real": 100, "row_count_synth": 100, "missing_columns": [], "extra_columns": [], "issues": [], "columns": {"order_id": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "customer_id": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "customer_email": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "status": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "amount": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "order_total": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "placed_at": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "shipped_at": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "order_date": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "discount_code": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "is_gift": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "region": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "tier": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "churned": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "zip": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 0.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "city": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "state": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "country": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "iban": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "notes": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "token": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "ssn": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "salary": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}}}}, "tier1": {"real": {"adversarial": {"auc_roc": 0.49598930481283426, "accuracy": 0.4950248756218905, "top_features": [["order_id", null], ["customer_id", null], ["customer_email", null], ["status", null], ["amount", null], ["order_total", null], ["placed_at", null], ["shipped_at", null], ["order_date", null], ["discount_code", null]], "n_samples": 200, "passed": true, "distinguishability_score": -0.8}, "mixture_fit_columns": ["amount", "customer_id", "order_id", "order_total", "salary"], "conditional_profiles": 25, "temporal_columns": [], "periodic_columns": ["amount", "customer_id", "order_id", "order_total", "salary"]}}, "tier2": {"real": {"format_preservation": {"customer_email": {"column": "customer_email", "detected_format": "email", "real_format_rate": 1.0, "synth_format_rate": 1.0, "delta": 0.0, "passed": true}, "zip": {"column": "zip", "detected_format": "zip_us", "real_format_rate": 1.0, "synth_format_rate": 1.0, "delta": 0.0, "passed": true}, "token": {"column": "token", "detected_format": "ssn_us", "real_format_rate": 1.0, "synth_format_rate": 1.0, "delta": 0.0, "passed": true}, "ssn": {"column": "ssn", "detected_format": "ssn_us", "real_format_rate": 1.0, "synth_format_rate": 1.0, "delta": 0.0, "passed": true}}, "string_similarity": {"customer_email": {"column": "customer_email", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "status": {"column": "status", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "placed_at": {"column": "placed_at", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "shipped_at": {"column": "shipped_at", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "order_date": {"column": "order_date", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "discount_code": {"column": "discount_code", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "region": {"column": "region", "ngram_n": 3, "cosine_similarity": 1.0000000000000002, "score": 100.0}, "tier": {"column": "tier", "ngram_n": 3, "cosine_similarity": 1.0000000000000002, "score": 100.0}, "zip": {"column": "zip", "ngram_n": 3, "cosine_similarity": 1.0000000000000002, "score": 100.0}, "city": {"column": "city", "ngram_n": 3, "cosine_similarity": 1.0000000000000002, "score": 100.0}, "state": {"column": "state", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "country": {"column": "country", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "iban": {"column": "iban", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "notes": {"column": "notes", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "token": {"column": "token", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "ssn": {"column": "ssn", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}}, "cardinality": {"order_id": {"column": "order_id", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "customer_id": {"column": "customer_id", "real_cardinality": 20, "synth_cardinality": 20, "ratio": 1.0, "deviation": 0.0, "passed": true}, "customer_email": {"column": "customer_email", "real_cardinality": 20, "synth_cardinality": 20, "ratio": 1.0, "deviation": 0.0, "passed": true}, "status": {"column": "status", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "amount": {"column": "amount", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "order_total": {"column": "order_total", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "placed_at": {"column": "placed_at", "real_cardinality": 28, "synth_cardinality": 28, "ratio": 1.0, "deviation": 0.0, "passed": true}, "shipped_at": {"column": "shipped_at", "real_cardinality": 28, "synth_cardinality": 28, "ratio": 1.0, "deviation": 0.0, "passed": true}, "order_date": {"column": "order_date", "real_cardinality": 28, "synth_cardinality": 28, "ratio": 1.0, "deviation": 0.0, "passed": true}, "discount_code": {"column": "discount_code", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "is_gift": {"column": "is_gift", "real_cardinality": 2, "synth_cardinality": 2, "ratio": 1.0, "deviation": 0.0, "passed": true}, "region": {"column": "region", "real_cardinality": 2, "synth_cardinality": 2, "ratio": 1.0, "deviation": 0.0, "passed": true}, "tier": {"column": "tier", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "churned": {"column": "churned", "real_cardinality": 2, "synth_cardinality": 2, "ratio": 1.0, "deviation": 0.0, "passed": true}, "zip": {"column": "zip", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "city": {"column": "city", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "state": {"column": "state", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "country": {"column": "country", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "iban": {"column": "iban", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "notes": {"column": "notes", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "token": {"column": "token", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "ssn": {"column": "ssn", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "salary": {"column": "salary", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}}, "anomaly_rate": null, "passing_rate": 1.0}}}, "gates": [{"name": "overall_score", "status": "pass", "value": 94.40993788819877, "threshold": 85.0, "relation": ">="}, {"name": "table_score", "status": "pass", "table": "real", "value": 94.40993788819877, "threshold": 70.0, "relation": ">="}, {"name": "coverage", "status": "pass"}, {"name": "tier1_adversarial_auc", "status": "pass", "table": "real", "value": 0.49598930481283426, "threshold": 0.75, "relation": "<"}, {"name": "tier2_pass_rate", "status": "pass", "table": "real", "value": 1.0, "threshold": 1.0, "relation": ">="}], "notes": []}, "utility": {"status": "not_run", "metrics": {}, "gates": [{"name": "utility_retention", "status": "not_run", "reason": "no verify configuration was given (--config), so there is no `utility` section"}], "notes": [], "reason": "no verify configuration was given (--config), so there is no `utility` section"}, "privacy": {"status": "pass", "metrics": {"memorization": {"fail_at": "CONFIDENTIAL", "min_nn_distance": null, "tables": {"real": {"rows": 100, "source_rows": 100, "columns": ["churned", "city", "country", "customer_email", "discount_code", "iban", "is_gift", "notes", "order_date", "placed_at", "region", "shipped_at", "ssn", "state", "status", "tier", "token", "zip"], "restricted": false, "reproduced_rows": 100, "exact_match_rate": 1.0, "reproduced_row_indices": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99], "nn_distance": {"columns": ["order_id", "customer_id", "amount", "order_total", "salary"], "rows_checked": 100, "min": 0.0, "p05": 0.0, "median": 0.0}}}}, "membership_inference": {"status": "not_run", "reason": "no holdout was given (--holdout): membership inference needs real rows that were not given to the generator"}}, "gates": [{"name": "memorization", "status": "pass", "value": 1.0}, {"name": "membership_inference", "status": "not_run", "reason": "no holdout was given (--holdout): membership inference needs real rows that were not given to the generator"}], "notes": ["real: no column is classified CONFIDENTIAL or above, so reproduced rows are reported but cannot fail the gate (set \"classifications\" in the verify configuration)"]}}, "require": [], "overall": "pass", "overall_reasons": [], "command": "report-card", "exit_code": 0, "payload": {"format": "shape-report-card", "version": 1, "shape_version": "0.9.1", "inputs": {"real": {"dataset_id": "sha256:fb7614bf671a52ca551e8338196da66ba9bc2f56b8440fe06ddc885c1e228a32", "tables": {"real": 100}}, "synthetic": {"dataset_id": "sha256:c5deb2bc2fa5320cbea3043d39ba80557455fa97d4f002c9eff672251f299e35", "tables": {"synthetic": 100}}, "holdout": null, "manifest": null}, "sections": {"fidelity": {"status": "pass", "metrics": {"overall_score": 94.40993788819877, "thresholds": {"min_overall": 85.0, "min_table": 70.0, "min_column": null}, "missing_tables": [], "extra_tables": [], "issues": [], "tables": {"real": {"score": 94.40993788819877, "present": true, "row_count_real": 100, "row_count_synth": 100, "missing_columns": [], "extra_columns": [], "issues": [], "columns": {"order_id": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "customer_id": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "customer_email": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "status": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "amount": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "order_total": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "placed_at": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "shipped_at": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "order_date": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "discount_code": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "is_gift": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "region": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "tier": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "churned": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "zip": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 0.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}, "city": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "state": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "country": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "iban": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "notes": {"score": 85.71428571428571, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": 1.0}, "token": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "ssn": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": null, "std_ratio": null, "ks_statistic": null, "chi2_statistic": 0.0, "chi2_pvalue": 1.0, "value_overlap": 1.0}, "salary": {"score": 100.0, "present": true, "dtype_match": true, "null_rate_delta": 0.0, "cardinality_ratio": 1.0, "mean_delta": 0.0, "std_ratio": 1.0, "ks_statistic": 0.0, "chi2_statistic": null, "chi2_pvalue": null, "value_overlap": null}}}}, "tier1": {"real": {"adversarial": {"auc_roc": 0.49598930481283426, "accuracy": 0.4950248756218905, "top_features": [["order_id", null], ["customer_id", null], ["customer_email", null], ["status", null], ["amount", null], ["order_total", null], ["placed_at", null], ["shipped_at", null], ["order_date", null], ["discount_code", null]], "n_samples": 200, "passed": true, "distinguishability_score": -0.8}, "mixture_fit_columns": ["amount", "customer_id", "order_id", "order_total", "salary"], "conditional_profiles": 25, "temporal_columns": [], "periodic_columns": ["amount", "customer_id", "order_id", "order_total", "salary"]}}, "tier2": {"real": {"format_preservation": {"customer_email": {"column": "customer_email", "detected_format": "email", "real_format_rate": 1.0, "synth_format_rate": 1.0, "delta": 0.0, "passed": true}, "zip": {"column": "zip", "detected_format": "zip_us", "real_format_rate": 1.0, "synth_format_rate": 1.0, "delta": 0.0, "passed": true}, "token": {"column": "token", "detected_format": "ssn_us", "real_format_rate": 1.0, "synth_format_rate": 1.0, "delta": 0.0, "passed": true}, "ssn": {"column": "ssn", "detected_format": "ssn_us", "real_format_rate": 1.0, "synth_format_rate": 1.0, "delta": 0.0, "passed": true}}, "string_similarity": {"customer_email": {"column": "customer_email", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "status": {"column": "status", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "placed_at": {"column": "placed_at", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "shipped_at": {"column": "shipped_at", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "order_date": {"column": "order_date", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "discount_code": {"column": "discount_code", "ngram_n": 3, "cosine_similarity": 0.9999999999999999, "score": 100.0}, "region": {"column": "region", "ngram_n": 3, "cosine_similarity": 1.0000000000000002, "score": 100.0}, "tier": {"column": "tier", "ngram_n": 3, "cosine_similarity": 1.0000000000000002, "score": 100.0}, "zip": {"column": "zip", "ngram_n": 3, "cosine_similarity": 1.0000000000000002, "score": 100.0}, "city": {"column": "city", "ngram_n": 3, "cosine_similarity": 1.0000000000000002, "score": 100.0}, "state": {"column": "state", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "country": {"column": "country", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "iban": {"column": "iban", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "notes": {"column": "notes", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "token": {"column": "token", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}, "ssn": {"column": "ssn", "ngram_n": 3, "cosine_similarity": 1.0, "score": 100.0}}, "cardinality": {"order_id": {"column": "order_id", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "customer_id": {"column": "customer_id", "real_cardinality": 20, "synth_cardinality": 20, "ratio": 1.0, "deviation": 0.0, "passed": true}, "customer_email": {"column": "customer_email", "real_cardinality": 20, "synth_cardinality": 20, "ratio": 1.0, "deviation": 0.0, "passed": true}, "status": {"column": "status", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "amount": {"column": "amount", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "order_total": {"column": "order_total", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "placed_at": {"column": "placed_at", "real_cardinality": 28, "synth_cardinality": 28, "ratio": 1.0, "deviation": 0.0, "passed": true}, "shipped_at": {"column": "shipped_at", "real_cardinality": 28, "synth_cardinality": 28, "ratio": 1.0, "deviation": 0.0, "passed": true}, "order_date": {"column": "order_date", "real_cardinality": 28, "synth_cardinality": 28, "ratio": 1.0, "deviation": 0.0, "passed": true}, "discount_code": {"column": "discount_code", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "is_gift": {"column": "is_gift", "real_cardinality": 2, "synth_cardinality": 2, "ratio": 1.0, "deviation": 0.0, "passed": true}, "region": {"column": "region", "real_cardinality": 2, "synth_cardinality": 2, "ratio": 1.0, "deviation": 0.0, "passed": true}, "tier": {"column": "tier", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "churned": {"column": "churned", "real_cardinality": 2, "synth_cardinality": 2, "ratio": 1.0, "deviation": 0.0, "passed": true}, "zip": {"column": "zip", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "city": {"column": "city", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "state": {"column": "state", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "country": {"column": "country", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "iban": {"column": "iban", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "notes": {"column": "notes", "real_cardinality": 1, "synth_cardinality": 1, "ratio": 1.0, "deviation": 0.0, "passed": true}, "token": {"column": "token", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "ssn": {"column": "ssn", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}, "salary": {"column": "salary", "real_cardinality": 100, "synth_cardinality": 100, "ratio": 1.0, "deviation": 0.0, "passed": true}}, "anomaly_rate": null, "passing_rate": 1.0}}}, "gates": [{"name": "overall_score", "status": "pass", "value": 94.40993788819877, "threshold": 85.0, "relation": ">="}, {"name": "table_score", "status": "pass", "table": "real", "value": 94.40993788819877, "threshold": 70.0, "relation": ">="}, {"name": "coverage", "status": "pass"}, {"name": "tier1_adversarial_auc", "status": "pass", "table": "real", "value": 0.49598930481283426, "threshold": 0.75, "relation": "<"}, {"name": "tier2_pass_rate", "status": "pass", "table": "real", "value": 1.0, "threshold": 1.0, "relation": ">="}], "notes": []}, "utility": {"status": "not_run", "metrics": {}, "gates": [{"name": "utility_retention", "status": "not_run", "reason": "no verify configuration was given (--config), so there is no `utility` section"}], "notes": [], "reason": "no verify configuration was given (--config), so there is no `utility` section"}, "privacy": {"status": "pass", "metrics": {"memorization": {"fail_at": "CONFIDENTIAL", "min_nn_distance": null, "tables": {"real": {"rows": 100, "source_rows": 100, "columns": ["churned", "city", "country", "customer_email", "discount_code", "iban", "is_gift", "notes", "order_date", "placed_at", "region", "shipped_at", "ssn", "state", "status", "tier", "token", "zip"], "restricted": false, "reproduced_rows": 100, "exact_match_rate": 1.0, "reproduced_row_indices": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99], "nn_distance": {"columns": ["order_id", "customer_id", "amount", "order_total", "salary"], "rows_checked": 100, "min": 0.0, "p05": 0.0, "median": 0.0}}}}, "membership_inference": {"status": "not_run", "reason": "no holdout was given (--holdout): membership inference needs real rows that were not given to the generator"}}, "gates": [{"name": "memorization", "status": "pass", "value": 1.0}, {"name": "membership_inference", "status": "not_run", "reason": "no holdout was given (--holdout): membership inference needs real rows that were not given to the generator"}], "notes": ["real: no column is classified CONFIDENTIAL or above, so reproduced rows are reported but cannot fail the gate (set \"classifications\" in the verify configuration)"]}}, "require": [], "overall": "pass", "overall_reasons": []}}
    # Report card

    **Overall: FAIL** · Shape 0.9.1 · format shape-report-card version 1

    - utility: required but not run (the verify configuration has no `utility` section)
    - privacy: membership_inference required but not run (no holdout was given (--holdout): membership inference needs real rows that were not given to the generator)

    ## Inputs

    | input | tables | dataset id |
    |---|---|---|
    | real | customers (20 rows), orders (100 rows) | sha256:59a56e8bc9111065b76d922acd6e101e1b337b71c7f0ac2c7e26dd53d1d0364d |
    | synthetic | customers (20 rows), orders (100 rows) | sha256:59a56e8bc9111065b76d922acd6e101e1b337b71c7f0ac2c7e26dd53d1d0364d |


    ## Fidelity: PASS


    ### Scores (0-100)

    | table | real rows | synthetic rows | score |
    |---|---|---|---|
    | customers | 20 | 20 | 97.1429 |
    | orders | 100 | 100 | 94.4099 |
    | (overall) | – | – | 95.7764 |

    ### Columns

    | table | column | score | type matches | null-rate difference | KS statistic | value overlap |
    |---|---|---|---|---|---|---|
    | customers | customer_id | 100 | yes | 0 | 0 | – |
    | customers | id | 100 | yes | 0 | 0 | – |
    | customers | age | 100 | yes | 0 | 0 | – |
    | customers | name | 100 | yes | 0 | – | 1 |
    | customers | email | 100 | yes | 0 | – | 1 |
    | customers | region | 100 | yes | 0 | – | 1 |
    | customers | city | 85.7143 | yes | 0 | – | 1 |
    | customers | born | 85.7143 | yes | 0 | 0 | – |
    | customers | income | 100 | yes | 0 | 0 | – |
    | customers | churned | 100 | yes | 0 | – | 1 |
    | orders | order_id | 100 | yes | 0 | 0 | – |
    | orders | customer_id | 100 | yes | 0 | 0 | – |
    | orders | customer_email | 100 | yes | 0 | – | 1 |
    | orders | status | 85.7143 | yes | 0 | – | 1 |
    | orders | amount | 100 | yes | 0 | 0 | – |
    | orders | order_total | 100 | yes | 0 | 0 | – |
    | orders | placed_at | 100 | yes | 0 | 0 | – |
    | orders | shipped_at | 100 | yes | 0 | 0 | – |
    | orders | order_date | 100 | yes | 0 | 0 | – |
    | orders | discount_code | 85.7143 | yes | 0 | – | 1 |
    | orders | is_gift | 100 | yes | 0 | – | 1 |
    | orders | region | 100 | yes | 0 | – | 1 |
    | orders | tier | 85.7143 | yes | 0 | – | 1 |
    | orders | churned | 100 | yes | 0 | – | 1 |
    | orders | zip | 85.7143 | yes | 0 | 0 | – |
    | orders | city | 85.7143 | yes | 0 | – | 1 |
    | orders | state | 85.7143 | yes | 0 | – | 1 |
    | orders | country | 85.7143 | yes | 0 | – | 1 |
    | orders | iban | 85.7143 | yes | 0 | – | 1 |
    | orders | notes | 85.7143 | yes | 0 | – | 1 |
    | orders | token | 100 | yes | 0 | – | 1 |
    | orders | ssn | 100 | yes | 0 | – | 1 |
    | orders | salary | 100 | yes | 0 | 0 | – |

    ### Tier 1: adversarial test

    | table | AUC | accuracy | rows | result |
    |---|---|---|---|---|
    | customers | 0.492063 | 0.474359 | 40 | PASS |
    | orders | 0.495989 | 0.495025 | 200 | PASS |

    ### Tier 2: formats and cardinality

    | table | passing rate | format checks passed | cardinality checks passed |
    |---|---|---|---|
    | customers | 1 | 2/2 | 10/10 |
    | orders | 1 | 4/4 | 23/23 |

    ### Gates

    | gate | table | status | value | threshold | detail |
    |---|---|---|---|---|---|
    | overall_score | – | pass | 95.7764 | &gt;= 85 | – |
    | table_score | customers | pass | 97.1429 | &gt;= 70 | – |
    | table_score | orders | pass | 94.4099 | &gt;= 70 | – |
    | coverage | – | pass | – | – | – |
    | tier1_adversarial_auc | customers | pass | 0.492063 | &lt; 0.75 | – |
    | tier1_adversarial_auc | orders | pass | 0.495989 | &lt; 0.75 | – |
    | tier2_pass_rate | customers | pass | 1 | &gt;= 1 | – |
    | tier2_pass_rate | orders | pass | 1 | &gt;= 1 | – |

    ## Utility: NOT RUN

    Not run: the verify configuration has no `utility` section


    ### Gates

    | gate | table | status | value | threshold | detail |
    |---|---|---|---|---|---|
    | utility_retention | – | not_run | – | – | the verify configuration has no `utility` section |

    ## Privacy: PASS

    - customers: no column is classified CONFIDENTIAL or above, so reproduced rows are reported but cannot fail the gate (set "classifications" in the verify configuration)
    - orders: no column is classified CONFIDENTIAL or above, so reproduced rows are reported but cannot fail the gate (set "classifications" in the verify configuration)

    ### Memorization

    | table | synthetic rows | exact-match rate | reproduced rows | closest-row distance min | p05 | median |
    |---|---|---|---|---|---|---|
    | customers | 20 | 1 | 20 | 0 | 0 | 0 |
    | orders | 100 | 1 | 100 | 0 | 0 | 0 |

    ### Gates

    | gate | table | status | value | threshold | detail |
    |---|---|---|---|---|---|
    | memorization | – | pass | 1 | – | – |
    | membership_inference | – | not_run | – | – | no holdout was given (--holdout): membership inference needs real rows that were not given to the generator |

    A passing card is evidence, not a guarantee: see docs/REPORT_CARD.md for what each number means and where it stops.
    ```

This command exits nonzero. Read the diagnostic; this transcript shows a refusal or failed check, not a passing gate.
