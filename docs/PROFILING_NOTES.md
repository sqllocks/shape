# Profiling notes: inputs, odd values and size

What `shape profile` (and `shape.profile`) does with CSV files, non-finite numbers, decimals, time zones and large
inputs. Each statement here has a test in `tests/profile/test_profile_issues.py`.

## CSV files

The delimiter is sniffed from the first 100 rows: comma, semicolon, tab or pipe, whichever splits every row into the
same number (two or more) of fields (quotes are respected, and a comma wins when it qualifies). Set it when sniffing
guesses wrong, along with the other reader options:

```bash
shape profile export.csv -o export.shape --delimiter ';' --encoding latin-1
shape profile raw.csv -o raw.shape --no-header          # columns are f0, f1, ...
```

```python
shape.profile("export.csv", delimiter=";", encoding="latin-1", quotechar="'", header=True)
```

A CSV that still comes out as one column whose name contains `;`, tab, `|` or `,` gives a warning that names the
delimiter to pass. `shape.io.CsvOptions` (the fused engine and `stream-profile`) has the same `delimiter`, `encoding` and
`quotechar`, and sniffs when `delimiter` is not set.

## NaN, infinity and single values

A float column keeps three things apart: nulls (`null_count`), NaN (`nan_count`) and +/- infinity (`inf_count`). The last
two are left out of cardinality, min, max, mean, standard deviation, quantiles and the distribution fit, and the profile
only carries them when they are not zero. `std` is `null` for a column with fewer than two values.

## Decimals

A decimal column is reported with `dtype: float` (the profile's dtype vocabulary has no decimal), and `precision` and
`scale` beside it, as declared by the source (`decimal128(10, 2)` gives 10 and 2). The fields are `null` for every other
column.

## Time zones

A timestamp column with a time zone is profiled on its wall clock, with the UTC offset kept in the min, max and value
keys. UTC and fixed offsets (`+05:30`) need no time-zone database. A named zone (`America/New_York`) does: on a system
without one (Windows without the `tzdata` package) the profile stops with an error that names the zone and says
`pip install tzdata`.

## Personal-data patterns

For every text column the profile records `pattern_rates` (the share of non-null values that are wholly an SSN, email
address, IP address or IBAN, and of the detected `pattern`) and `pattern_contains_rates` (the share that contain an SSN, an
email address or a Luhn-valid card number inside longer text). Both are measured on every distinct value, up to 50,000 per
column and an evenly spaced sample of them beyond that, so 1% sparse values show. `pattern` itself is still one label for
a column that is at least 90% one pattern. `shape profile safe` keeps a column pattern-only when any of those families
reaches 0.1% (`SafeConfig.pii_pattern_floor`). See [PRIVACY_MODEL.md](PRIVACY_MODEL.md).

## Size

| Item | Size |
|---|---|
| A text column of unique long documents | 1.8 KB for the profile of 2,000 documents of 20 KB each (the values are not stored: a text column with more than 500 distinct values, at least 95% of the non-null count, lists none) |
| A stored text value (count key, minimum, maximum) | cut to 256 characters, with an ellipsis |
| `.shape`, 1,000 distinct integer values per column | about 23 KB per column (`enum_values` plus the top 500 values) |
| `shape profile safe` JSON | about 2.2 KB per column, 0.9 KB per column with `--compact` (integer columns with 1,000 distinct values) |
| Correlation matrix | grows with the square of the numeric columns, so past 256 of them each column keeps its 25 strongest partners (a pair stays when either column keeps it) and the table is marked `correlation_truncated` |

For a profile kept per run, write the safe form with `--compact` (one line, no null fields; it reads back the same) and
pick the columns with `--columns A,B,*_id` or `--exclude 'raw_*'`:

```bash
shape profile safe daily.shape -o daily.safe.json --compact --exclude 'notes,raw_*'
```

## Joint analysis and placeholders

Beside the per-column statistics, `shape profile` records what holds across columns: placeholder
values (`00000`, `-1`, `N/A`, `1900-01-01`, ...), approximate functional dependencies, two-column
keys, association measures, conditional tables and the share of implausible rows (`docs/JOINT.md`).
The cross-column analysis reads a deterministic sample (at most 20,000 rows for a table that
small, 5,000 for a larger one) and a bounded number of columns and pairs, so its cost does not grow
with the table; it is on for a single table and off for a dataset (several tables), `--joint` /
`joint=True` turn it on and `--no-joint` / `joint=False` off, and `SHAPE_PROFILE_JOINT=0|1` decides when
the call does not. `--reference-pair COLS=REFERENCE` checks
that columns hold real combinations against a reference file.


## Univariate depth: model selection, zeros, heaping, Benford, tail index

Every numeric column with at least **20 finite values** (integers, floats, and decimals read as
numbers) gains the fields below, each only where it applies; a text, boolean or date column gains
none, and a column with fewer values gains none at all. They are computed once, in Python, over the
column's values, so `SHAPE_KERNEL=rust` and `python` give the same numbers, and they do not need
scipy. They sit beside `distribution`, `distribution_params` and `fit_score`, which are unchanged
(the best of normal, uniform, exponential and lognormal by KS statistic on 2,000 values, still what
generation uses); nothing here feeds generation, contract rules or the share-safe profile (its
allow-list is unchanged, so none of these fields leave with a safe profile).

**What is read.** The count of zeros, the range, and for a count column the mean and variance read
every value, in steps of 262,144 values (no copy of the column). The rest reads a deterministic
sample: at most **50,000** values for heaping, Benford and the tail index, and at most **10,000**
for model selection. A column at or under a cap is read whole; a longer one is cut into as many runs
of consecutive rows as the cap (as equal as possible) and one row is drawn at random from each, by
`default_rng(42)`, so the sample is the same on every run, every row has the same chance of being
in it, and a column that repeats with a period cannot line up with it. The caps are well under the
100,000 values the issue allows because every numeric column pays for them on every profile. Cost is bounded in the number of rows: a test
(`tests/profile/test_univariate_profile.py`, marked `heavy`) runs a 10-million-row column in under
10 s with a peak of temporary memory under 20% of the column's own size.

`shape.profile.univariate.univariate_stats(values, integer=...)` computes the fields for any array
of numbers, and `describe(column)` gives a column's fields as short text lines.

### Model selection: `distribution_candidates`, `distribution_by_bic`

Six families are fitted by maximum likelihood to the model sample (n values), unlike the existing
fit, which also allows a free location. The lognormal, exponential, gamma and Weibull are fitted
only when **every** value is positive.

| Family | Parameters (k) | Fit |
|---|---|---|
| `normal` | `mu`, `sigma` (2) | mean and population standard deviation |
| `lognormal` | `mu`, `sigma` of ln x (2) | mean and population standard deviation of ln x |
| `exponential` | `scale` = mean (1) | the mean (no location) |
| `uniform` | `low`, `high` (2) | minimum and maximum |
| `gamma` | `shape`, `scale` (2) | `ln k - psi(k) = ln(mean) - mean(ln x)` solved by Newton's method, `scale = mean / k` |
| `weibull` | `shape`, `scale` (2) | the profile likelihood equation in the shape, solved by a bracketed Newton method |

For each family: `params`, `log_likelihood`, `aic` = `2k - 2 LL`, `bic` = `k ln n - 2 LL`, and `ks`,
the KS statistic of the model against the sample's empirical CDF. `distribution_by_bic` is the
family with the lowest BIC (the first listed on a tie). The difference between two BICs says how
much better one family is than another: a gap above 10 is strong evidence.

Not computed (no fields): fewer than 20 finite values, a constant column, and a family whose fit is
degenerate (a gamma or Weibull shape above 100,000, which is a point mass for practical purposes).
Selection is by likelihood on the sample, so a heavily rounded or discrete column can favour the
uniform or a skewed family by accident: read `distribution_by_bic` with `heaping` and `zero_share`.

### Zeros: `zero_share`, `zero_inflation`

`zero_share` is the share of zeros among the finite values, for float columns and for integer
columns of non-negative values (an integer column with a negative value has neither field).
`zero_inflation` is for integer columns of non-negative values only:

```json
{"observed": 0.335, "poisson_expected": 0.122, "nb_expected": 0.22, "inflated": true}
```

`poisson_expected` is `exp(-mean)`. `nb_expected` is the zero probability of a negative binomial
fitted by moments: with mean m and sample variance v, `r = m^2 / (v - m)` and
`(r / (r + m))^r`; a column whose variance does not exceed its mean has no overdispersion, and its
negative binomial is its Poisson. `inflated` is true when the observed share exceeds **both**
expected shares by more than three standard errors (`sqrt(p(1 - p) / n)` at that model's own `p`)
**and** is at least 0.05. A column of all zeros is not inflated. The test is conservative: the
overdispersion that zero inflation causes is partly absorbed by the negative binomial.

### Heaping: `heaping`

Values piled on round numbers (manual entry, estimation) are measured for the units 5, 10, 100
and 1,000, on the non-zero values of the sample (a zero is a zero, not a round number):

```json
{"resolution": 1, "unit": 5, "observed_share": 0.68, "expected_share": 0.205, "ratio": 3.3171, "heaped": true}
```

- `resolution` is the coarsest of 0.001, 0.01, 0.1, 1, 5, 10, 100 and 1,000 that every value is a
  multiple of. A column of whole hundreds has resolution 100 and is **not** heaped on 5, 10 or 100:
  only a unit above its resolution is tested (here 1,000). A column whose values are on no grid of
  0.001 or coarser (continuous measurements) has no `heaping` field.
- `observed_share` is the share of values that are multiples of the unit.
- `expected_share` is the share a column spread evenly over its grid would have: the number of
  multiples of the unit between the sample's minimum and maximum (zero excluded), over the number of
  grid points between them. A unit is tested only when the grid has at least 20 points and the
  range holds at least two multiples of it, so a 1 to 10 rating scale is never "heaped on 5".
- `ratio` is `observed_share / expected_share`. `heaped` is true when some unit has a ratio of at
  least 2 and an observed share of at least 0.1. The reported unit is, among the units that satisfy
  that, the one with the largest excess `observed_share - expected_share` (among all tested units
  when none does), so values heaped on tens report 10, not the 5 and 100 that contain it.
- An integer-valued float column counts as an integer column.

A skewed column (many small values) can show a ratio above 1 with no manual rounding; the 2 and
0.1 limits keep that rare, and the field is evidence, not proof.

### Benford: `benford`

```json
{"applicable": true, "digits": [0.301, 0.176, ...], "mad": 0.0021, "conformity": "close"}
```

Computed when the column has **at least 100** finite values, **every value is positive**, and the
largest is at least **100 times** the smallest (two orders of magnitude); otherwise
`{"applicable": false, "reason": ...}` with the reason (`fewer than 100 values`, `contains values
that are not positive`, `spans fewer than two orders of magnitude`). `digits` are the shares of the
first significant digits 1 to 9 in the sample. `mad` is the mean absolute difference from Benford's
shares `log10(1 + 1/d)`, and `conformity` is Nigrini's class: `close` below 0.006, `acceptable` below
0.012, `marginal` below 0.015, else `nonconformity`. Amounts spread over several orders of
magnitude follow the law; ages, ids, prices set by hand and anything with a fixed range do not, and
the field then says nothing about fraud. The classes are bands of a noisy statistic: the MAD of 1,000
values varies by about 0.003.

### Tail index: `tail_index`

```json
{"alpha": 1.52, "k": 71, "se": 0.18, "heavy": true}
```

Computed from the **positive** values of the sample when there are at least **50**. With the values
sorted, `k = max(10, round(sqrt(n)))` and `X(k+1)` the (k+1)-th largest, the Hill estimator is
`alpha = 1 / mean(ln(X(i) / X(k+1)))` over the `k` largest, with standard error `alpha / sqrt(k)`.
`heavy` is true when `alpha` is below 2 (the variance is infinite). Not computed for fewer than 50
positive values or when the top values are all equal. The Hill estimator assumes a Pareto-like
tail, and `alpha` depends on `k`: a lognormal or exponential column gets a finite `alpha` that
means "tail heavier than normal", not a true power law.

`shape diff` reports changes in these fields as `zero_inflation_change`, `heaping_change`,
`benford_change` and `tail_change` (`docs/DRIFT.md`). `shape show` and `shape cat` print every field, and the `--html` report states them in each
column's card (`Profile.summary()` and `--json` keep their pinned key set). A profile written before these
fields existed loads, displays and diffs as before, with the fields absent and the four kinds not
reported.

## Mixtures and seasonality: `mixture`, `seasonality`

Two more fields describe a numeric column beyond one family: whether it is a blend of populations
(`mixture`) and whether it repeats over time (`seasonality`). Both are computed in Python over numpy,
so `SHAPE_KERNEL=rust` and `python` give the same numbers. Neither is in the share-safe profile: a
component mean or a standard deviation is a value, so `shape profile safe` leaves both out (its
allow-list is unchanged, and the leak scanner passes a safe profile of a table that has both).
`distribution`, `distribution_candidates` and the other fields are unchanged, and nothing here feeds
generation. `shape diff` reports changes as `mixture_change` and `seasonality_change`
(`docs/DRIFT.md`).

### Gaussian mixture: `mixture`

```json
{"k": 2, "components": [{"weight": 0.3, "mean": 0.0, "sd": 1.0}, {"weight": 0.7, "mean": 4.0, "sd": 1.0}],
 "bic_by_k": {"1": 43083.9, "2": 39350.2, "3": 39373.3, "4": 39399.0}, "multimodal": true}
```

**Minimum sample:** at least **200 finite values**; a constant column has none. **Sample:** a column
of more than **4,000** values is read through the same deterministic stratified sample as the rest
of the univariate depth (`default_rng(42)`, one row per run of consecutive rows, so the same on every
run); a shorter one is read whole. The cap is under the 100,000 values the specification allows
because EM runs four times per numeric column on every profile.

**Fit.** For each `k` from 1 to 4 a mixture of `k` normal densities is fitted by expectation
maximisation on the sample, standardised first. The start is deterministic and quantile-based: the
sorted sample is cut into `k` runs of equal size, and each run's share, mean and standard deviation
are the starting weight, mean and spread (no random state). Each iteration computes the
responsibilities `r_ij = w_j N(x_i; mu_j, sd_j) / sum_l w_l N(x_i; mu_l, sd_l)` and sets
`w_j = mean_i r_ij`, `mu_j = sum_i r_ij x_i / sum_i r_ij` and `sd_j^2 = sum_i r_ij (x_i - mu_j)^2 /
sum_i r_ij`. A standard deviation never falls below 0.1% of the column's (so a spike of repeated
values does not make the likelihood infinite). EM stops after **200 iterations**, or when the mean
log-likelihood per value improves by less than **1e-6**. The model has `3k - 1` parameters and
`BIC = (3k - 1) ln n - 2 LL`, with `LL` the log-likelihood of the values on their own scale; `bic_by_k`
lists the four, and `k` is the lowest (the smaller `k` on a tie).

`components` are in order of mean, each with its `weight`, `mean` and `sd`. `multimodal` is true
when `k >= 2` and every component's weight is at least **0.05**. It says "BIC prefers several
components, none of them marginal", not that the density has several peaks: a skewed column
(a log-normal, an exponential) is often fitted with three or four overlapping components and reads
`multimodal`. Read it with `distribution_by_bic` and the component means: components a fraction of a
standard deviation apart are one skewed population, not two.

### Seasonality: `seasonality`

```json
{"applicable": true, "time_column": "day", "granularity": "day", "period": 7,
 "strength": 0.81, "acf": 0.66, "seasonal": true}
```

**The time column** is the table's only date or timestamp column, or the one named by the profile
option `time_column` (`shape.profile(..., time_column="day")`, `shape profile --time-column day`).
For a dict of tables the name applies to each table that has it, and is an error when none does; a
name that is not a date or timestamp column is an error. Workbook sheets are not analysed. Rows with
a null time and values that are not finite are left out; a time zone is ignored (the stored instant
is used).

**Aggregation, in one pass.** Each numeric column is averaged per **day**, or per **hour** when the
time column spans fewer than 14 days. The pass reads the table 262,144 rows at a time, so its cost
is linear in the rows and its memory does not grow with them (`tests/profile/test_seasonality.py`,
marked `heavy`, runs a 10-million-row table under 10 s with a peak of temporary memory under 20% of
one column). A period with no value is filled by linear interpolation; a series in which fewer than
half of the periods hold a value is not computed.

**Candidate periods.** 24 on the hourly series, 7 on the daily series, 12 on the monthly means of
daily data (the mean of all values in each calendar month) and 52 on the weekly means (consecutive
runs of 7 days from the first day); a candidate counts only when **at least three full periods**
exist (72 hours, 21 days, 36 months, 156 weeks).

**Statistics for each candidate.** `acf` is the sample autocorrelation of the series at the period,
`sum (y_t - m)(y_{t+p} - m) / sum (y_t - m)^2`. `strength` comes from a classical decomposition
`y = trend + seasonal + remainder`: the trend is the centred moving average of window `p` (a 2 x p
average when `p` is even), the seasonal figure is the mean of the detrended values at each position
of the period, centred to sum to zero, and

`strength = max(0, 1 - var(remainder) / var(seasonal + remainder))`

so 0 means no repeating pattern and 1 a pattern with no noise. The candidate with the largest
strength is reported (the smaller period on a tie), with its `granularity` (`hour`, `day`, `week`
or `month`); `seasonal` is true when its strength is **at least 0.6**. A trend alone is not seasonal,
because the moving average removes it. White noise gives a strength near `1 / (periods)` and is not
seasonal.

**Not computed** (`applicable: false` and a `reason`, a column still gets the entry): no date or
timestamp column; more than one and none named; fewer than three full periods of any candidate; a
constant series; more than half of the periods empty; a time column spanning more than 2,000,000
days. A text, boolean or date column has no `seasonality`, and neither has the time column itself.

A profile written before these fields existed loads, displays and diffs as before, with the fields
absent and the two diff kinds not reported. `shape show` prints both fields, and the `--html` report
states them in each column's card as `mixture: k=2 ...` and `seasonality: period 7 days, strength
0.81 (seasonal)`.
