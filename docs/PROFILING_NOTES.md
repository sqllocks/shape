# Profiling notes: inputs, odd values and size

Status: available.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" PROFILING_NOTES
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for PROFILING_NOTES
    ```


What `shape profile` (and `shape.profile`) does with CSV files, non-finite numbers, decimals, time zones, large
inputs, sampling and column types. Each statement here has a test in `tests/profile/test_profile_issues.py` or
`tests/profile/test_audit_profile.py`; the sections on sampling and type inference are tested in
`tests/profile/test_sampling.py`, `tests/profile/test_type_inference.py`, `tests/cli/test_profile_sampling_cli.py` and
`tests/cli/test_types_command.py`.

## CSV files

The delimiter is sniffed from the first 100 rows: comma, semicolon, tab or pipe, whichever splits every row into the
same number (two or more) of fields (quotes are respected, and a comma wins when it qualifies). Set it when sniffing
guesses wrong, along with the other reader options:

[Run this example](#local-example-0).


```python
shape.profile("export.csv", delimiter=";", encoding="latin-1", quotechar="'", header=True)
```

A CSV that still comes out as one column whose name contains `;`, tab, `|` or `,` gives a warning that names the
delimiter to pass. `shape.io.CsvOptions` (the fused engine and `stream-profile`) has the same `delimiter`, `encoding` and
`quotechar`, and sniffs when `delimiter` is not set.

Shapes of file that are read rather than refused:

- A file whose first data row has one field more than the header (a trailing delimiter on every row, for example) has
  its first field read as a row label, not as a column: the header names the remaining fields. A file where only a later
  row has the extra field is refused.
- A file with a header and no rows is a table of no rows, with text columns.
- Whole numbers of up to 76 digits are read (as `dtype: float` past 64 bits); wider ones are refused naming the column.

Clear errors: a file that is not UTF-8 (or not in the `encoding` given) names the file and says to pass `encoding=` /
`--encoding`; an empty file names the file; a `quotechar` or `delimiter` longer than one character is a `ValueError`.
A path may be a `file://` URL or start with `~`.

`PROFILE_THREADS` sets the profiler's thread count (a positive integer; `0` or unset: every core). With `1`, pyarrow's
process-wide thread pools hold one thread while the profile runs and get their sizes back when it returns.

## Identifiers are not numbers

A CSV column of ZIP codes, NDCs, NPIs or member numbers is digits, and plain type inference reads it as an integer:
`02134` becomes 2134 and the placeholder `00000` becomes 0. Shape reads an integer column as **text** when

* some value has a leading zero (two or more digits, the first `0`), whatever the column is called, or
* every value is digits of one width of five or more **and** the column name says it is an identifier (`zip`, `postal`,
  `npi`, `ndc`, `mrn`, `member_id`, `patient_number`, `code`, ...).

A column that only looks like an identifier (a fixed width of five or more digits without such a name, or a name such as
`zip` or `phone` over values of mixed width) stays an integer and `shape profile` warns, naming the columns and the option
that keeps them as text. The options:

[Run this example](#local-example-2).


`shape.profile(path, string_columns=["zip"], types={"amount": "float"}, infer_types="off")` is the same from Python, and
`shape.io.CsvOptions(string_columns=, column_types=, infer_types=)` for the readers. Types are `string`, `integer`, `float`,
`boolean`, `date`, `datetime` (or an Arrow type name). A text column the reader fixed stays text: its digits are not
re-typed as numbers by the profiler's own detectors, and `value_counts_ext` and the minimum show `00000`. The safe profile describes the column as text of a fixed length (no mean, bounds or quantiles of a "ZIP number").
With `--infer-types off` the profiler's detectors type the text (dates, booleans, numbers), except that digits with
leading zeros stay text.

`shape learn` and `shape generate --from` turn a column of digit text of one width into a `{digits:N}` pattern (random
digits, zeros included) or, when the profile lists every value, a value set whose labels stay text, so generated ZIPs
and NPIs keep their width and leading zeros. Writers quote text, and every reader in Shape applies the same rule, so a
generated CSV reads back as the same text.

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
`pip install tzdata`. A zone name the database does not know (`Mars/Base`) is reported as an unknown time zone.

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

[Run this example](#local-example-3).


### Input budget for untrusted files

A Parquet file of a few kilobytes can declare a hundred million rows and take gigabytes once read.
Before a Parquet file is read, two optional budgets are checked against its footer (no data page is
read for the check), summed over the files of one source:

- `SHAPE_MAX_INPUT_ROWS`: the rows the footers declare;
- `SHAPE_MAX_INPUT_BYTES`: the decoded size they imply (rows times the width of each fixed-width
  column, and for string, binary and nested columns at least their uncompressed bytes and offsets).

A file over either budget is refused with an error that names the variable. Unset or blank, nothing
is checked; anything but a positive integer is an error. The decoded size of a dictionary-encoded
string column depends on its values, which the footer does not hold, and compressed CSV or JSONL
files declare no size, so for those the row budget (Parquet) or the process memory limit is the
control. Set both budgets, and a memory limit, when profiling files you did not produce.

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

The fields below are **opt-in**: `shape profile --univariate` (`shape.profile(...,
univariate=True)`) computes them; without it a profile carries none of them and is otherwise the
same. They add Python work for every numeric column, more than the benchmark gate of the default
profile allows, so they are not computed by default (the implementation tests).

With it, every numeric column with at least **20 finite values** (integers, floats, and decimals
read as numbers) gains the fields below, each only where it applies; a text, boolean or date column gains
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
100,000 values the issue allows because every numeric column pays for them when they are asked for. Cost is bounded in the number of rows: a test
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
(`mixture`) and whether it repeats over time (`seasonality`). They are part of the opt-in
univariate depth above (`shape profile --univariate`, `shape.profile(..., univariate=True)`). Both are computed in Python over numpy,
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

## Sampling

A profile says how much of the data it saw. Nothing is sampled unless you ask, and every table's profile records what was
done, whether you asked or not.

[Run this example](#local-example-10).


```python
shape.profile("orders.csv", sample=100_000)                       # an int is a number of rows
shape.profile("orders.csv", sample=0.1, sample_method="head")     # a float above 0 and up to 1 is a fraction
shape.profile(frame, sample="10%", sample_seed=7)
```

| Method | The rows |
|---|---|
| `random` (default) | a uniform sample without replacement: numpy's legacy `RandomState(seed).choice`, then sorted, so the rows keep their order |
| `systematic` | `k` rows evenly spread with a step of `N / k` (every step-th row; the step need not be whole) from a seeded random start |
| `head` | the first `k` rows (the seed has no effect) |

The seed is 42 unless you give one, and it is recorded. The rows depend on nothing else: the same rows are chosen on every
run, in both kernel modes (`SHAPE_KERNEL=rust` and `python`), and for a file, a folder, a glob, a Delta table (with
`version` or `as_of`), an Arrow table and a data frame; the position is counted in the order the source reads back (a folder
is its files in name order). An invalid value (0 rows, a fraction outside (0, 1], an unknown method, a seed outside
0 to 4294967295) is an input error (exit 2). Asking for as many rows as the table has, or more, reads it whole.
The table is read whole before it is sampled: sampling cuts the work of the statistics, not the read.

With `--dataset` (or a dict of tables) each table is sampled on its own. Foreign keys are detected on the sampled child
rows against the parent's whole key column, because independent samples of a parent and a child share few keys and
would hide a relationship that is there. A reference-pair check (`--reference-pair`) always reads the whole table.

### The sampling record

Every table profile has `sampling`:

| Key | Meaning |
|---|---|
| `method` | `random`, `systematic`, `head`, or `none` when the whole table was read |
| `seed` | the seed of a sampled profile, else `null` |
| `requested` | what was asked for: `{"rows": N}` or `{"fraction": F}`, else `null` |
| `population_rows` | the rows the source had (exact: the source is read whole before it is sampled) |
| `sampled_rows` | the rows profiled; `row_count` is the same number |
| `internal` | the samples the statistics take of their own, below |
| `adequacy`, `adequacy_reason` | `adequate`, `limited` or `insufficient`, and why |

`shape profile` prints `shape: note: profiled a random sample of 100000 of 2500000 rows (seed 42)` to stderr when
`--sample` is given (one line per table of a dataset, ending `, table NAME`), and never otherwise. `shape show` prints the
record per table (`not recorded` for a profile written by an older Shape), the `--json` summary and the HTML report carry
it, and `shape diff` adds a note to its result (`notes` in `--json`, present only when there is one) when the two profiles
were sampled by different methods, or one was sampled and the other not. `row_count_change` compares the sources'
`population_rows` when both profiles state it, not the sizes of their samples.

### Adequacy

Each column has `adequacy`:

* `non_null`: the values seen;
* `null_rate_se`: the standard error of the null rate, `sqrt(p (1 - p) / n)` with `n` the rows profiled, times the finite
  population correction `sqrt((N - n) / (N - 1))` when the source's `N` rows are known; 0 when every row was read;
* `min_detectable_share`: `1 - 0.05^(1/n)` over the column's `non_null` values: the smallest share a value needs to be
  seen at least once with 95% probability.

The table's `sampling.adequacy` follows from the rows profiled `n` (a table read whole counts too: exact about its rows,
it says little about values it has not got):

| Level | When |
|---|---|
| `insufficient` | fewer than 30 rows (the drift engine's `min_rows`) |
| `limited` | `min_detectable_share(n)` is above 0.01: from 30 to 298 rows, a value must be more than 1% of the rows to be seen |
| `adequate` | otherwise (299 rows or more) |

### Internal samples

Some statistics sample inside themselves, whatever you asked for. The record lists the ones the profile's statistics used:

| `analysis` | When | Rows, method, seed |
|---|---|---|
| `pattern_detection` | a string column of more than 1,000 values | 1,000, `random`, 42 |
| `pattern_rates` | a string column of more than 50,000 distinct values | 50,000 distinct values, `systematic`, no seed |
| `type_inference` | a string column of more than 50,000 distinct values | 50,000 distinct values, `systematic`, no seed |
| `distribution_fit` | a numeric column of more than 2,000 values | 2,000, `random`, 42 |
| `joint` | a table of more than 20,000 rows | 5,000 rows, `systematic` with a seeded offset in each stride (seed 7) |
| `kendall_tau` | a numeric association of more than 500 rows | 500, `systematic`, no seed |
| `reference_pairs` | a reference-pair check on more than a million rows | 1,000,000, `systematic`, no seed |
| `univariate` | `--univariate`, a numeric column of more than 10,000 finite values | 50,000 (10,000 for model selection), `stratified`: one value at random from each run of consecutive rows, seed 42 |
| `multivariate_outliers` | the joint analysis's multivariate outliers over more than 600 rows | 600 rows for FAST-MCD's starts, `random`, seed 20260327 (the fit and the rate read every analysed row) |
| `cohorts` | cohorts over more than 2,000 analysed rows | 2,000, `random`, seed 20260328 |

Each entry has `analysis`, `rows`, `method`, `seed` and, for the column analyses, the `columns` it applied to. The
statistics of a sampled table sample inside the rows that were kept. A test fails when a code path draws a sample that is not
registered here (`shape.profile.sampling.INTERNAL`).

## Type inference

Every column has `type_inference`: where its type came from and how well the values fit it.

| Key | Meaning |
|---|---|
| `type` | the profile's `dtype` |
| `source` | `declared` (a typed source: Parquet, Delta, Excel, an Arrow table, a data frame), `inferred` (CSV, JSON, text, Python values), `option` (`--types`, `--string-columns`, `--infer-types off`) or `identifier_rule` (a column the identifier rule kept as text) |
| `confidence` | the share of non-null values that parse as `type`; 1.0 for `option`; `null` for no values |
| `parse_shares` | the share of non-null values that parse as each of `integer`, `float`, `boolean`, `date`, `datetime` |
| `identifier` | the identifier rule's reason (why it kept the column as text, or why an integer column is only a suspect), else `null` |
| `declared`, `inferred` | for a `declared` column: its declared type, and the narrowest type every value parses as |
| `candidate` | for an inferred text column that is mostly one narrower type: that type (the `confidence` is then its share) |

The shares are counted over the distinct values weighted by their counts for text, and over the values for numbers and
timestamps. They are exact, except for a text column of more than 50,000 distinct values, whose shares are measured on
50,000 evenly spread distinct values (each weighted by its count) so that the cost does not grow with the number of
distinct values; the record lists that column under `internal` as `type_inference`.

Integers parse as floats; `integer` also counts a float that is a whole number; `boolean` is the six words
`true false yes no 0 1` in any case; `date` is an ISO `YYYY-MM-DD` that is a real date; `datetime` is ISO 8601 with an
optional time and zone. The profiler types text as numbers, booleans or datetimes only when every value parses (its own
rules for dates are wider than ISO), so such a column has confidence 1.0.

A column that is 97% integers with 3% stray text is not an integer to the reader: its type is `string`, its `confidence` is
0.97, its `candidate` is `integer` and `parse_shares.integer` is 0.97. `shape types` reports it.

### `shape types`

`shape types PROFILE.shape [--contract CONTRACT.json] [--min-confidence C] [--json]` lists the columns whose types need a
second look, with the table, the column, both types, the confidence and the option that would change it (`--types`,
`--string-columns`); exit 0 with no findings, 1 with findings, 2 for bad input. `shape.types_report(profile, contract=None,
min_confidence=0.99)` returns the same as a list of dicts. The findings:

* `declared_differs`: a `string` that holds integers, floats, booleans, dates or datetimes, or a `float` that holds whole
  numbers;
* `identifier_suspect`: an integer column the identifier rule calls a suspect (every value has five or more digits, or its
  name says it holds identifiers), or would have kept as text;
* `low_confidence`: a type inferred from text with a `confidence` below `C` (default 0.99, strict: a column at exactly `C`
  is not reported);
* `contract_differs`: a column whose contract `dtype` differs from the profile's.

A profile written before type inference was recorded has no `type_inference`: `shape types` says so on stderr and compares
only the contract. The findings become `type` proposals with `shape proposals propose --kinds type` (`docs/PROPOSALS.md`).


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape profile export.csv -o export.shape --delimiter ';' --encoding latin-1
shape profile raw.csv -o raw.shape --no-header          # columns are f0, f1, ...
```

??? info "Output (exit 0)"

    ```text {.expected}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: export.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "6d093e05eddaf930c70203ffa852139c4007a26df4b083236be3515bb40f059d", "written": "export.shape"}
    {"shape_content_id": "a605edae085e7c45531e83ad09147e7ab2b5b8237d5c8494cc8b0ecc15dc112a", "written": "raw.shape"}
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
shape profile members.csv -o members.shape --string-columns zip,npi        # keep these as text
shape profile members.csv -o members.shape --types types.json              # {"zip": "string", "amount": "float"}
shape profile members.csv -o members.shape --infer-types off               # read every column as text
```

??? info "Output (exit 0)"

    ```text {.expected}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: members.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "d3fd1943d642e07d8b12312432fc4bd78c95d3f2d4575d052ddb8f9c1623f6ca", "written": "members.shape"}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: members.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "0f819d1eb8d21d6e3383a0d2333bcf76bf04dc31291fe1f27d6adcde7913cc30", "written": "members.shape"}
    {"shape_content_id": "5983c8d28223c591e9620e2bc7f9bea100abbf66e0dfa41361f50d888c66a4f9", "written": "members.shape"}
    ```

<a id="local-example-3"></a>

### Example 4

<!-- example: 3 -->

```bash {.runnable-reference}
shape profile safe daily.shape -o daily.safe.json --compact --exclude 'notes,raw_*'
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: daily.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"unsafe": false, "written": "daily.safe.json"}
    ```

<a id="local-example-10"></a>

### Example 11

<!-- example: 10 -->

```bash {.runnable-reference}
shape profile orders.csv -o orders.shape --sample 100000                       # 100,000 rows
shape profile orders.csv -o orders.shape --sample 10% --sample-method systematic --sample-seed 7
shape profile data/ --dataset -o shop.shape --sample 20%                       # each table
```

??? info "Output (exit 0)"

    ```text {.expected}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    shape: note: profiled a random sample of 100 of 100 rows (seed 42)
    {"shape_content_id": "2dfcbd3536f3d02c1b30242e1c213f718f9793e9ee16ea6e17ab38776e25ba09", "written": "orders.shape"}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    shape: note: profiled a systematic sample of 10 of 100 rows (seed 7)
    {"shape_content_id": "b5e327e3c20efdddc04d35b81ea4c6a5a57549389a1580eb139d571f2d904aef", "written": "orders.shape"}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    shape: note: profiled a random sample of 4 of 20 rows (seed 42), table customers
    shape: note: profiled a random sample of 20 of 100 rows (seed 42), table orders
    {"shape_content_id": "e61f01b12dfbf067d390c120e7e78ec5a08e94c833ebdabb71f70c57ce96baf3", "written": "shop.shape"}
    ```
