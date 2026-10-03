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

