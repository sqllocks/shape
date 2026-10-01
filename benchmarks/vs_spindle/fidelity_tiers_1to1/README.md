# fidelity_tiers_1to1: the fidelity tiers against the baseline's

P4-11 harness. Shape's tiers (`shape fidelity --tier 1|2|3`, `shape drift`, the bootstrap resample,
`shape.privacy.dp`) must give the baseline's output field by field on retail medium.

```bash
source scripts/env.sh
"$SHAPE_VENV/bin/python" benchmarks/vs_spindle/fidelity_tiers_1to1/run.py [--scale medium] [--reuse]
PYTHONHASHSEED=0 "$SPINDLE_PY" benchmarks/vs_spindle/fidelity_tiers_1to1/golden.py --check
```

* `run.py` (Shape's venv) takes the baseline's seed 42 as the real data and Shape's seed 1042 as the
  synthetic data (`domain_1to1/generate.py` makes missing runs), checks that scikit-learn is
  installed in **both** venvs (exit 2 if not), runs the baseline's tiers in its own venv
  (`baseline_tiers.py`, `PYTHONHASHSEED=0`; about 15 minutes at medium, dominated by the mixture
  fits of the 1.25M-row table), runs Shape's in process, and compares every field with
  `tiers_common.compare`. `--reuse` reuses the baseline's saved output. It also checks 1000
  unseeded differential-privacy calls for 1000 distinct noises. Exit 0 only when every field
  matches; the report goes to `$BENCH_OUT_DIR/fidelity_tiers_1to1/report_<scale>.json`.
* Comparison rules (`tiers_common.py`): integers, booleans, strings and list lengths equal; floats
  within 1e-9 relative (T-22); the adversarial AUC and accuracy, the classifier's feature
  importances and every mixture-fit field within 0.02 of the baseline's. Keys only Shape has are
  ignored.
* For tables of at most 5,000 rows (`--small-rows`) the harness also compares the bootstrap resample
  (seed 7, 1000 rows) and the differential-privacy noise (seed 7, Laplace and Gaussian, epsilon 0.5)
  with the baseline's, bit for bit: both draw from numpy's `default_rng(seed)` in the same order.
* The baseline's PSI of each numeric column (`baseline_tiers.py --psi-only`) is compared with
  `shape.fidelity.tier3.psi_report`.
* `golden_data.py` builds small deterministic tables that hit every branch (nulls, integers, a
  two-component mixture, booleans, text formats, timestamps, dates and decimals, an all-null column,
  a 12-row table, a table above every sampling cap, an anomaly flag). `golden.py` records the
  baseline's outputs for them in `fixtures/expected_tiers.json`; `--check` fails if the file no
  longer matches. `tests/benchmarks/test_fidelity_tiers_1to1.py` checks Shape against that file and
  needs no baseline venv.

Why the AUC is within 0.02 rather than equal: the baseline picks the classifier's features in the
iteration order of a Python `set` of column names, which changes from process to process
(`PYTHONHASHSEED`); Shape uses the reference's column order, so the gradient-boosted trees break ties
differently. Everything else is equal, and the mixture fits are bit for bit when the input dtype is
the same (a column of integers must reach scikit-learn as integers).

The baseline checkout is only read.
