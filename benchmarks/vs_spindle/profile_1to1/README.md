# Spindle DataProfiler vs a 1:1 vectorised port

A benchmark of profiling speed where both sides do the **same work and produce the same
output**. `port.py` re-implements `sqllocks_spindle/inference/profiler.py` (`DataProfiler`,
`ColumnProfile`, `TableProfile`, `DatasetProfile`) with numpy + pyarrow + stdlib only. It
computes every field Spindle computes, using Spindle's sampling rules, estimators,
thresholds and tie-breaks. `verify.py` checks the result field by field. On all 30 dataset
variants tested, **every field of every column is bitwise-identical** to Spindle's output.

Spindle source: the pinned checkout in `$SPINDLE_ROOT` @ `422e78d`, `inference/profiler.py` md5
`4b253e4f7ff20b5dacc80f02e76bf49c` (read only, not modified).
Spindle venv: Python 3.11.15, pandas 3.0.6, numpy 2.4.6, scipy 1.17.1, pyarrow 25.0.1.
Port venv: Python 3.11.15, numpy 2.4.6, pyarrow 23.0.1.

## Files

| file | purpose |
|---|---|
| `datasets.py` | Deterministic generator. Writes CSV and Parquet files to `$BENCH_DATA_DIR/profile/` (D1-D4, MT, EDGE); `--rows N` sets the D3 row count |
| `port.py` | The port: `profile_csv(path)`, `profile_parquet(path)`, `profile_table(pa.Table)`, `profile_dataset({name: table or path})` |
| `spindle_dump.py` | Runs Spindle in its own venv and writes a normalised JSON profile. The same normaliser is used for the port |
| `verify.py` | Field-by-field comparison and pass/fail matrix |
| `bench.py` | Timing harness (a fresh process per run, median of 5, load-gated) |

## How to run

```bash
source scripts/env.sh                                   # from the repository root
P="$SHAPE_VENV/bin/python"
"$P" benchmarks/vs_spindle/profile_1to1/datasets.py     # ~35 s, ~1.3 GB of files; or: datasets.py D2 EDGE
"$P" benchmarks/vs_spindle/profile_1to1/verify.py --impl reference_port --refresh   # ~12 min (Spindle re-run on every dataset)
"$P" benchmarks/vs_spindle/profile_1to1/bench.py --impl reference_port              # ~45 min, holds $BENCH_OUT_DIR/bench.lock
"$P" benchmarks/vs_spindle/profile_1to1/bench.py --table "$BENCH_OUT_DIR/profile/bench_results.json"  # reprint the table
```

`verify.py --impl X` exits 1 on any field outside T-22, and 2 if a dataset file is missing.
`--impl shape` checks the product API (`shape.profile`). Spindle's normalised output is cached
under `$BENCH_OUT_DIR/profile_cache/`.

The large generated files are not in git. Regenerate them with `datasets.py`; the output is
deterministic, with fixed seeds. Every dataset, D1 included, is always regenerated from its
seed and never read from anywhere else. (The D1 numbers in the recorded results below were
measured on a copy of an earlier 200k-row scratch file with the same schema; the harness now
regenerates D1, and `results.json` supersedes those rows.)

## Datasets

| id | shape | contents |
|---|---|---|
| D1 | 200k x 6 | the original "easy" shape: id, age, zip, state, email, amount |
| D2 | 1M x 20 | int PK; ints; normal, uniform, exponential and lognormal floats; decimals (float in CSV, **string** in Parquet, so Spindle's `to_numeric` coercion path runs); phone, email, zip+4, uuid, ipv4, currency codes; low-cardinality enums; dates; timestamps; bools with and without nulls; null rates from 0.5% to 20% |
| D3 | 5M x 10 | long table: 5M-unique id, 200k-cardinality FK-like int, normal, lognormal, enum, date, timestamp, bool, email, float with 30% nulls |
| D4 | 100k x 200 | wide table built from 10 cycling archetypes (about 120 numeric columns, so the correlation matrix and distribution fitting dominate) |
| MT | customer 20k / product 800 / orders 200k | `profile_dataset` with cross-table FK detection (`orders.customer_id` has 2% nulls and 3% orphans; `orders.product_id`) |
| EDGE | n = 3, 15, 60, 130, 3000, each x 2 variants (int PK and uuid-only PK), CSV and Parquet | Edge cases: n < 4 (no quantiles), n < 20 (no fit), n <= 140 (the exact KS DMTW and Pomeranz branches), constant and all-null columns, yes/no and true/false/1/0 strings, numeric strings, ssn, mac, ipv6, iban, language codes, `2020/1/5` and `01/05/2020` dates, signed zeros, fractional-second timestamps, bools with nulls |

## Equivalence: what the port reproduces, and how

| Spindle behaviour | Port implementation | Verified |
|---|---|---|
| `pd.read_csv` dtype inference (int with nulls -> float64, bool with nulls -> object, text dates stay text, pandas NA tokens, `True/TRUE/true` booleans) | `pyarrow.csv` with pandas' NA and bool token lists and timestamp inference disabled. Inferred date32/time32 columns are cast back to their exact canonical text. A per-column "pandas kind" is carried alongside the data | bitwise |
| `pd.read_parquet` / `Table.to_pandas()` (int with nulls -> float, date32 -> object `datetime.date`, timestamp -> datetime64, bool with nulls -> object) | `_arrow_cols` kind mapping | bitwise |
| `_infer_column_type`: bool dtype; int; float with the all-whole check; datetime with the all-midnight check; object/str: bool-like set, `to_numeric`, `to_datetime(format="mixed")` | Same decision tree. The bool-like set check and numeric cast run vectorised on the distinct values | bitwise |
| null_count (NaN counts as null), null_rate, cardinality (`nunique`, -0.0 == 0.0), cardinality_ratio, is_unique, all rounding | same | bitwise |
| `enum_values` and `value_counts_ext` (top 500): pandas `value_counts` order (first appearance, then a *stable* descending sort) and `str(key)` formatting of int, float, bool, Timestamp and date keys, including which of `-0.0` or `0.0` is printed | Arrow hash counts for strings, bools and low-cardinality numerics. For numerics above 50k distinct values: sort-based counts plus a chunked first-appearance scan that reproduces pandas' tie order exactly | bitwise, including key order |
| min/max with pandas' Python types (`int`, `float`, `str`, `bool`, `Timestamp`, `datetime.date`) | `pc.min_max` / numpy, converted to the same Python types (a `Timestamp` subclass of `datetime` stands in for pandas') | bitwise, including type |
| mean, std (ddof=1, pandas nanops summation) | the same numpy reductions | bitwise |
| quantiles p1..p99, p0_5, p99_5 (`np.percentile`, linear) and 1.5 x IQR outlier_rate | One sort, then numpy's exact virtual-index and `_lerp` arithmetic on the sorted array. Outliers are counted with `searchsorted` | bitwise |
| `_detect_distribution`: `default_rng(42).choice(..., 2000, replace=False)` sample; norm, uniform, expon and lognorm fits; `kstest` with an exact `kstwo.sf` p-value > 0.05 gate; lowest D wins | Same sample. Closed-form norm, uniform and expon MLE as in scipy. scipy 1.17's lognorm fit ported line by line: the dL/dloc bracket search, **brentq** (the zeros.c port was checked bitwise on 3000 random problems), and the fallback to the generic MLE fit (`_fitstart`, penalised NLL, **Nelder-Mead** `fmin` ported verbatim). Also ported: cephes `ndtr` (max abs error vs scipy 1.1e-16), the KS D statistic, and `_kolmogn` (Ruben-Gambino, DMTW, Pomeranz, Pelz-Good, Smirnov branches) | bitwise params. On D4, 66 of 175 lognorm fits take the Nelder-Mead fallback, the same counts as in Spindle's cProfile |
| `fit_score`: refit **on the full column** plus KS on the full column | same (reuses the sort) | bitwise |
| `_detect_pattern`: `RandomState(42)` sample of 1000, 12 regexes in order at a 90% threshold, the nunique <= 200 guard | Same sample. The regexes are rewritten exactly as pandas' Arrow backend rewrites them for `fullmatch` and run through the same RE2 engine (`pc.match_substring_regex`) | bitwise |
| string_length (min, mean rounded to 2, max, p95) | `pc.utf8_length` + numpy | bitwise |
| hour, dow and temporal histograms (`to_datetime(errors="coerce")`, winsorised p1/p99 years, 12 month weights) | Arrow strptime with the format guessed from the first element. The year percentiles are computed from the year histogram with numpy's arithmetic | bitwise |
| `_detect_primary_key` (unique, non-null, integer or uuid, name preference `id/_id/pk/key`) | same, reusing the column results | bitwise |
| `_detect_foreign_keys` (`*_id` name -> table, parent PK, >= 0.9 overlap of distinct values; int == float equality) | same, with `pc.is_in` on distinct values cast to float64 | bitwise |
| `correlation_matrix` (`select_dtypes(number).corr()`, pairwise-complete Pearson, rounded to 4) | BLAS: columns centred on the global mean, and pairwise sums over the rows where both values are present computed as matrix products with the null masks | bitwise after rounding on all datasets (see deviations) |
| `relationships` in `profile_dataset` | same | bitwise |

### Intentional difference: the enum rule (P1-18)

The baseline marks a column an enum when `cardinality < 200 or (ratio < 0.30 and cardinality < 50_000)`,
so every column of a table under 200 rows is one, unique keys and free text included. Shape also
requires that the values repeat: distinct values <= 0.5 x non-null values, and a unique column is
never an enum. `verify.py` turns the baseline column into what that rule gives, from the baseline's
own `cardinality`, `null_count` and `is_unique` (`enum_rule_baseline`), and Shape must equal it. The
allow-list is exactly two fields, `is_enum` and `enum_values`; nothing else is derived from them
(`value_counts_ext` keeps the first 500 values whether the column is an enum or not, and no other field
reads `is_enum`). Every other field is compared with the baseline as it is. A full run fails if the rule
never turned a baseline enum off, or never kept one.

### Verification result (`verify.py --refresh`, all 30 variants)

Every cell is `n/n*`: all within tolerance and **all bitwise-identical**, for every field
listed above, on D1-D4 (CSV and Parquet), MT (CSV and Parquet) and all 20 EDGE files.
"Within tolerance but not bitwise identical" is empty. The tolerances in `verify.py` (1e-9
relative for mean and std, 1e-6 relative for distribution params, 1e-9 absolute for
proportions and histograms) were never needed.

## Deviations, stated plainly

Nothing below was triggered by any dataset here. These are the places where equivalence
rests on reasoning rather than on a test, or where the port is known to be narrower.

1. **`to_datetime(format="mixed")` for text columns: closed in P1-08.** `shape.profile` ports
   pandas' string rules (`dtparse.py`: the ISO-8601 reader, delimited dates, year/quarter/month
   abbreviations, `guess_datetime_format` and the strict parse that follows it) on a port of
   dateutil's parser (`_dateutil_parser.py`). They were fuzzed against pandas 3.0.6 on 14,305
   strings and 6,000 columns with 0 differences apart from the exceptions below; the golden
   subset is in `tests/profile/data/`. EDGE variants: `x_dates_*`, `x_time_*`. Time-only text
   is dated today by dateutil, so those two datasets are never served from the Spindle cache.
   Remaining, stated exceptions: time-zone-bearing text raises `NotImplementedError` (pandas
   returns tz-aware values); nanosecond fractions are cut to microseconds; year 0000; and the
   literals "now"/"today" (time-dependent).
2. **CSV parser: closed in P1-08.** Arrow's integers are corrected to pandas' rules (`+` sign,
   uint64 up to 2**64-1, wider integers as Python-int object columns), and pandas' low-memory
   chunked inference is reproduced (chunks of the largest power of two below `2**20 // ncols`
   rows; a column whose chunks disagree becomes an object column of Python ints/floats/bools/
   strings). `inf` in a float column makes Spindle raise `ValueError` (its whole-number test); Shape
   raises the same for *file* sources, and the verifier checks the error category. In-memory
   Arrow tables and DataFrames still profile such columns (std `NaN`, min `-inf`), which the
   `.shape` artifact tests rely on. EDGE: `x_csv_*`. Not modelled:
   integers wider than 38 digits (`NotImplementedError`).
3. **Parquet types: closed in P1-08.** Decimal, dictionary/categorical (unused categories and
   category order included), timezone-aware timestamps (wall-clock histograms, offset in
   keys), time, binary, duration (also counted by `corr`, as in pandas 3) and uint64 are profiled
   like pandas objects; nested types raise `TypeError` like Spindle. EDGE: `x_pq_*`. Not
   modelled: float16 (pandas reduces in half precision) and two instants that share a wall-clock
   time in one DST zone.
4. **The correlation algorithm differs.** pandas uses Welford per pair; the port uses
   globally centred BLAS sums. Before rounding they differ by about 1e-15. The 4-decimal
   rounding makes a flip at a rounding boundary theoretically possible (probability around
   1e-11 per entry); none occurred.
5. **`smirnov`** (used only when the KS p-value is far below the 0.05 gate) is the exact
   Birnbaum-Tingey sum, not scipy's C routine. It was checked against scipy's
   `kstwo.sf` on a grid (n = 20..2000, 300 D values): 0 gate disagreements, relative
   error < 1e-6. The p-value itself is never output.
6. `utf8_lower` (the bool-like check) differs from Python's `str.lower` only for special
   Unicode casing; this is irrelevant for the ASCII set being tested.
7. **Redundant work is not replicated.** Spindle repeats work whose results are
   identical: `_infer_column_type` and `_detect_pattern` run again for every unique column
   inside `_detect_primary_key`; `profile_dataset` re-detects every table's PK once per
   table; `value_counts` runs twice (enum and ext); `to_numeric` runs up to 4 times per
   column; `np.percentile` runs 3 times; `astype(str)` runs on the full column before
   sampling. The port computes each once. The port also stops a failing numeric or datetime
   parse after the first value, as pandas does. **The outputs match; the port just does not
   repeat work.** This is part of why it is faster. It is an implementation-quality
   difference, not missing work.

## Results

Machine: 4 cores (Intel Xeon @ 2.10GHz, Linux 6.18). Run on 2026-09-29 under
the exclusive benchmark lock (`$BENCH_OUT_DIR/bench.lock`), with runs interleaved. The 1-minute load average before
every run was between 1.00 and 1.50; about 1.0 of that is the previous benchmark process
itself, and no run had to wait for the load gate. Each value is the median of 5 runs, each
in a fresh process. The timed region is read + full profile; interpreter start-up and
imports are excluded. Run-to-run spread is within about ±10% of the median (one port-1T D4 Parquet outlier:
16.3 s against about 13.3 s). The raw runs are in `benchmarks/baselines/2026-09-29/profile_bench.json`.

| dataset | Spindle s | port MT s | port 1T s | speedup MT | speedup 1T | peak RSS MB: Spindle / port MT (+largest fork child) / port 1T |
|---|---:|---:|---:|---:|---:|---|
| D1 csv (200k x 6) | 1.53 | 0.19 | 0.22 | 8.0x | 6.8x | 272 / 164 (+0) / 149 |
| D1 parquet | 1.42 | 0.18 | 0.23 | 8.1x | 6.3x | 313 / 180 (+0) / 153 |
| D2 csv (1M x 20) | 31.12 | 1.82 | 4.99 | 17.1x | 6.2x | 1122 / 617 (+703) / 845 |
| D2 parquet | 26.51 | 1.82 | 4.83 | 14.6x | 5.5x | 991 / 523 (+547) / 685 |
| D3 csv (5M x 10) | 78.28 | 5.57 | 14.51 | 14.1x | 5.4x | 2813 / 2725 (+0) / 2183 |
| D3 parquet | 47.91 | 5.35 | 13.73 | 9.0x | 3.5x | 2037 / 1986 (+0) / 1146 |
| D4 csv (100k x 200) | 32.08 | 5.42 | 14.15 | 5.9x | 2.3x | 610 / 473 (+453) / 677 |
| D4 parquet | 28.31 | 4.23 | 13.34 | 6.7x | 2.1x | 604 / 417 (+271) / 475 |
| MT (3 tables, 220k rows) | 2.01 | 0.38 | 0.36 | 5.3x | 5.6x | 289 / 186 (+0) / 163 |

- **MT** is the port's default mode. It uses the Arrow thread pools and a column pool:
  a fork process pool for tables with at least 3 columns per worker (D2, D4), threads
  otherwise.
- **1T** is the same code with `PROFILE_THREADS=1`: pyarrow cpu and io pools set to 1,
  single-threaded CSV and Parquet readers, a sequential column loop, and
  `OPENBLAS_NUM_THREADS=1`. The 1T column separates the algorithmic gain from the
  multicore gain.
- **Spindle** runs as shipped. It is single-threaded, except that `pd.read_parquet` uses
  pyarrow's thread pool.
- **RSS** is `ru_maxrss` measured after imports. The baseline after imports is about
  163 MB for Spindle and 68 MB for the port. For fork-pool runs, the largest child's peak
  is listed separately; children share the parent's pages copy-on-write, so the parent and
  child figures must not be simply added.

### Where the time goes (cProfile)

**Spindle**
- D2 (37 s under the profiler): `_infer_column_type` takes 21 s. Of that, about 10 s is
  the Python set comprehension `str(v).lower()` over every distinct value of every string
  column (7M calls); the rest is repeated `to_numeric` / `to_datetime`.
  `_detect_primary_key` takes 4.3 s, because it re-runs type inference and pattern
  detection for the unique columns. `read_csv` takes 5.6 s, `value_counts` 2.1 s and
  `kstest` 2.0 s.
- D4 (36 s): lognorm fitting takes 13.7 s, of which 12 s is the generic Nelder-Mead
  fallback fit, 66 times. `DataFrame.corr` takes 4.6 s (Welford loop over about 7k
  column pairs), `kstest` 4.7 s, `read_csv` 2.6 s and type inference 3.2 s.

**Port (1T)**
- D2 (5 s): Arrow hash `value_counts` on about 7M distinct strings takes about 2 s,
  the CSV read 0.9 s, and correlation plus KS/ndtr the rest.
- D3 (14.5 s): string hashing and strptime of the 4.8M-distinct timestamp and email
  columns take about 5 s. The two full-column lognorm fit_score refits (5M rows, about 60
  dL/dloc evaluations each) take about 4 s. The CSV read takes 2.4 s.
- D4 (14 s): the same 31k penalised-NLL evaluations as scipy's Nelder-Mead take about
  7.5 s; this is identical algorithmic work. The CSV read takes 1.4 s and correlation
  0.3-0.8 s.

### What makes the comparison unfair, and in which direction

- **Favours the port:**
  - The MT column uses 4 cores against Spindle's 1. Use the 1T column for a
    like-for-like comparison.
  - The port does not repeat Spindle's redundant computations (deviation 7). The outputs
    are identical, but Spindle does roughly 2-4x the necessary work on string columns.
    This is Spindle's real cost, but it is not intrinsic to the profile.
  - Sort-based counting for high-cardinality numerics, and vectorised `is_in` / `cast`
    on distinct values instead of Python loops.
- **Favours Spindle, or neutral:**
  - The port replicates Spindle's expensive algorithms exactly, including full-data
    lognorm refits and the 600-evaluation Nelder-Mead fallback. That is why D4 1T is
    only 2.1-2.3x faster. A port free to pick better estimators would be much faster, but
    it would not be 1:1.
  - The pandas CSV parser is single-threaded in Spindle, while pyarrow's reader is
    multi-threaded in port MT. Port 1T also uses a single-threaded reader.
  - Spindle's `read_parquet` is multi-threaded in every mode.
  - Page cache is warm for both.
- **Environment:** a shared 4-core VM. Another agent's benchmark was queued on the same
  lock and did not overlap. Background load from other sessions may have added noise of a
  few percent to both sides equally, since runs were interleaved.
