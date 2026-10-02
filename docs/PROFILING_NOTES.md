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

## Identifiers are not numbers

A CSV column of ZIP codes, NDCs, NPIs or member numbers is digits, and plain type inference reads it as an integer:
`02134` becomes 2134 and the placeholder `00000` becomes 0. Shape reads an integer column as **text** when

* some value has a leading zero (two or more digits, the first `0`), whatever the column is called, or
* every value is digits of one width of five or more **and** the column name says it is an identifier (`zip`, `postal`,
  `npi`, `ndc`, `mrn`, `member_id`, `patient_number`, `code`, ...).

A column that only looks like an identifier (a fixed width of five or more digits without such a name, or a name such as
`zip` or `phone` over values of mixed width) stays an integer and `shape profile` warns, naming the columns and the option
that keeps them as text. The options:

```bash
shape profile members.csv -o members.shape --string-columns zip,npi        # keep these as text
shape profile members.csv -o members.shape --types types.json              # {"zip": "string", "amount": "float"}
shape profile members.csv -o members.shape --infer-types off               # read every column as text
```

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
