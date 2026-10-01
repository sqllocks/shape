# Fidelity tiers 1 to 3

`shape fidelity` scores synthetic data column by column (`docs/FIDELITY.md`). The tiers look at what
that score does not: joint structure, formats, drift and privacy. Tiers 1 and 2 compare a reference
and a synthetic table; tier 3 holds research-grade tools. **Tier 3 is experimental.**

```bash
shape fidelity real/ synthetic/ --tier 1      # mixtures, conditional profiles, adversarial AUC, time
shape fidelity real/ synthetic/ --tier 2      # formats, strings, cardinality, anomaly rate
shape fidelity real/ synthetic/ --tier 3      # dependency trees and how much structure survives
shape drift reference/ current/ --psi         # population stability index per column
```

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
* A timestamp column with missing values is filled with its median before the adversarial test.

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
  equal-width bins for numbers and timestamps, a missing value taking the median first) or coded
  (text), and the tree is the maximum spanning tree of the pairwise mutual information, over the first
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
  tested fails closed (`method: "error"`, drifted). Exit 0 if no column drifted, 1 if one did.
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
