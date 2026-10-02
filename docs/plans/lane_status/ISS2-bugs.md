# ISS2-bugs — issues #46, #40, #41, #42 (lane/ISS2-bugs)

Status: **built; awaiting lead verification.** No gate, tolerance, D-xx or T-xx decision was changed; §11 and §2.3
untouched; the pinned Spindle checkout was only read. Issues were not commented on, labelled or closed. No PR. Branched
from `lane/ISS-profile` (94c1e73).

Every issue was reproduced first on this branch (scripts in the session scratchpad, results below) and fixed with a
regression test that fails on the tree without the fix. One extra bug found while reproducing #46 was fixed too (below).

## Per issue

| # | Reproduced on this branch? | Fix | Tests |
|---|---|---|---|
| 46 | **Yes**, exactly as filed: A `integer 1002 \| pyarrow int64`, C `integer 2134`, D `string 02134`. Also: a profile of `member_id,zip,npi,...` typed all three as integer (`min 1571129`, `zip min 0`); `generate --from` and `learn` then produced numbers | See "#46 in detail" | `tests/profile/test_identifier_columns.py` (37 tests, both kernels where the kernel matters): of the first 36, 30 fail on the pre-fix tree and 6 pin behaviour that must not change; the 37th (safe profile) was added after |
| 40 | **Yes**: `FORMATS` had no `ipc`; `write_result(R(), "ipc", "o")` raised `ValueError: unknown format 'ipc'` | `generation/output.py` accepts every installed `shape.sinks` plugin (`available_formats()`); a sink's `extension` names its files (an empty one marks a directory sink, Delta); `shape generate -f` takes the same list (checked when the option is given, so START is unchanged). `SqlSink`, `ExcelSink`, `DeltaSink` got an `extension`. A third-party sink works with no edit in Shape | `tests/generation/test_sink_formats_and_schemes.py` |
| 41 | **Yes, on the in-memory hub; not on the Azure emulator** (no Docker here). Same 4-partition producer as the issue (300 events/s for 20 s, tumbling 2 s, `--follow`). Delivery chunk of 75/150 events per partition read: 6,000 of 6,000; 600: **2,850 of 6,000** (3,150 `late_events`); 1,500: **1,950 of 6,000**. Nothing on stderr. The loss is the filed one: the newest partition outruns the others and a global watermark with lateness 0 calls the rest late | See "#41 in detail" | `tests/streaming/test_partition_watermark.py` (23 tests; 12 fail when the per-partition registration is switched off) |
| 42 | **Yes**: on Linux `parquet` raised `ArrowInvalid`, `delta` `OSError`, and `csv`/`sql`/`jsonl` **wrote nothing and raised nothing, creating directories named `https:`, `mssql:` and `s3:`** | `shape.plugins.schemes`: every built-in sink calls `require_scheme(self, uri)` first, and `write_result`/`write_engine` check the output directory before creating it. The error is `UnsupportedSchemeError` (a `ShapeCapabilityError` and a `ValueError`): `the parquet sink writes only to local files; got abfss://... (OneLake and ADLS Gen2 sinks are not available yet). Sinks by scheme: file: csv, delta, ... A plugin adds a scheme by registering a shape.sinks sink that declares it (see shape plugins list).` A password in the URI is hidden. `sinks_by_scheme(host)` lists which sink handles which scheme | same file as #40 (25 tests) |

### #46 in detail

* **The rule** (`shape.io.identifiers`, kept narrow so a real number is not turned into text): an integer column is text when
  some value has **leading zeros** (two or more digits, first `0`), or when every value is **digits of one width of five or
  more and the column name says identifier** (`zip`, `postal`, `npi`, `ndc`, `mrn`, `member_id`, `patient_number`, `code`, ...).
  `--string-columns`, `--types FILE.json` and `--infer-types off` (`shape.profile(string_columns=, types=, infer_types=)`,
  `shape.io.CsvOptions(string_columns=, column_types=, infer_types=)`) set it by hand. An integer column that only
  looks like an identifier (width of five or more without a name, or a strong name such as `zip`/`phone` without a fixed
  width) stays an integer and `shape profile` warns, naming the option.
* **Not as filed:** the issue also asks for "all values have a fixed width" and "name suggests an identifier" alone. Either alone
  would make `year`, `rating`, epoch seconds and every `customer_id` text, so a fixed width needs the name (and five digits),
  and a name needs the width. Decision for the owner if a wider rule is wanted (one function, `judge`).
* **Where it applies:** `shape.profile` / `shape profile` (reference profiler, both kernels), `shape.io` readers (the fused engine,
  `stream-profile`, `learn` and every other reader built on `open_source`/`read_table`; a streamed
  read of a file above 1 GiB decides from its first block), `shape incremental continue`, the Fabric UDF reader. `mask` already
  read text. The Azure blob CSV source reads a non-seekable stream and was **not** changed.
* **The profiler keeps text as text:** its own detectors re-typed numeric text as `integer` (so a text `zip` came out as
  `dtype: integer`). A column the reader fixed as text carries a marker (`_Col.keep_text`) and skips those detectors.
  `value_counts_ext`, `min_value`, `enum_values` show `00000`; the safe profile describes the column as text of a fixed length.
* **Generation:** the profile's `pattern_rates` has a new `digits` key. `learn` / `generate --from` turn a digit-text column of one
  width into `{digits:N}` (new `pattern` token: random digits, zeros included) or, if the profile lists every value, a
  `weighted_enum` with `output_type: "string"` (the strategy used to turn numeric-looking labels into floats, so `02134` came out
  as `2134.0` -> `2134`). The fit plan reports the format as preserved.
* **from-ddl `CHAR`/`VARCHAR` ids:** already text end to end (`string` type, `{seq:6}` patterns zero-padded, faker ZIPs); the new
  test pins that through the CSV, TSV, Parquet, JSONL and IPC sinks and reading back. `sequence` on a `CHAR(10)` key still gives
  `1, 2, 3` (not zero-padded to the width): changing it touches key positions and the DDL parity harness; not done (see Proposals).
* **Writers:** `pyarrow` CSV writers (`csv`/`tsv` sinks, `dimensional`, `quarantine`, `mask`, chaos) write text quoted (`"02134"`),
  so another reader does not see a number; tests pin the `csv`/`tsv` sinks, the dimensional writer and quarantine (`mask` and chaos use the same pyarrow writer and were not given a test of their own).

**Allow-list in the profile parity harness** (`benchmarks/vs_spindle/profile_1to1/verify.py`, README entry "Identifier columns stay
text"): `IDENTIFIER_RULE` names exactly 12 columns: `zip` of `d1.csv`, `zip5` of the ten `edge/e*.csv`, `leading_zero` of
`edge/x_csv_numbers.csv`. The baseline reads them as integers, and its detectors turn numeric text back into numbers, so there is no
text version of the baseline to compare with. For exactly these columns the expectation is computed from the file's text (dtype
`string`; null count and rate, cardinality, uniqueness, min, max and length statistics; no mean, std, distribution, quantiles,
outliers or fit); pattern, enum and value counts are taken from Shape and not compared; the baseline's correlation matrix loses the
column. Every other column of the same files is compared as before, and a full run fails if the rule did not apply to all 12.
Probe: the test file above.

### #41 in detail

* The watermark is now per partition when the partitions are known. `decode_messages` marks a batch with its partition (schema
  metadata `shape.partition`; both the Kafka and Event Hubs plugins already call it with one partition per batch, so **no plugin
  and no plugin API changed**); the consumer registers the partitions from the `{partition: next offset}` position and passes each
  batch's partition to the profiler. Watermark = smallest, over the partitions, of the newest event time, minus `allowed_lateness`;
  never moves back. A source that names no partitions keeps the old single watermark (the stream-profile equivalence harness is
  unaffected: one partition is the old rule exactly).
* **Defaults (new options):** `--max-partition-skew` **10m** of event time: a silent partition, or one further behind than that,
  stops holding windows open; this is also what keeps open windows bounded (about 130 KB each for a three-column table, measured).
  `--partition-idle-timeout` **30 s** with `--follow` (processing time, the only place wall-clock time enters; off for bounded
  reads, which stay deterministic).
* **Reporting:** `late_events` was already in the summary. Now, when any event was late: a stderr line (`warning` from 1% of events,
  `note` below) with the count, share, how far behind the watermark the worst row was, and the `--allowed-lateness` that would have
  kept them (a test re-runs with the suggestion and loses nothing); the summary gains `late_share` and `late_lag_seconds`.
* **Trade-off, bounded reads:** a bounded read takes partitions one after the other, so event time starts over at each. No window
  can close while a partition is unread (up to the skew cap), so memory grows with the event-time range of the read, capped by the
  skew; data that spans more than `--max-partition-skew` in a later partition is late and reported. Documented in
  `docs/specs/STREAMING_SEMANTICS.md` (section 3) and `docs/plugins/streaming.md`.
* Snapshots carry the partitions (older snapshots restore to the global watermark); restore equivalence is tested.
* **Not verified on the real emulator** (needs Docker; the nightly job runs `-m emulator`). The in-memory hub reproduces the
  loss with the filed producer and shows none after the fix; the exact 15% of the issue depends on the emulator's delivery pattern.

### Found while reproducing #46 (not in the four issues)

`shape generate --from X.shape` (and any schema with correlated columns) printed `Wrote 1 csv files` and **wrote none**: the
copula tables were never handed to the writer (`Engine._generate`'s `release()` kept them "for the copula" after it had run). Fixed
(`generation/engine.py`), with `test_a_table_with_correlated_columns_is_written` (fails without the fix).

## Existing tests changed (intentional behaviour change, none skipped, xfailed or loosened elsewhere)

* `tests/io/test_readers.py::test_csv_values_are_typed_not_strings` pinned `zip` (`02134`) as `int64` ("override it"); now `string`.
* `tests/profile/test_infer.py::test_dtype_of_every_column_matches_the_spindle_profiler`: a column that the reader now keeps as text
  while Arrow's own inference says integer is asserted to be `string` in the profile; every other column is compared as before.

## Checks run in this session

RESULTS_PLACEHOLDER

## Proposals for the lead / owner (not built)

1. **Wider identifier rule** (see "Not as filed"): fixed width alone / name alone. One function, `shape.io.identifiers.judge`.
2. **`CHAR(n)` sequence keys zero-padded to `n`** in from-ddl (`sequence` strategy with a `width`): needs `keypos`/foreign-key
   handling and an allow-list entry in the DDL parity harness.
3. **Found, not fixed:** `learn` on a column of NDC codes (`00093-0058-01`) picks the `phone_number` faker because the detected
   pattern is `phone`; the profile's own value list is not used. Values keep text but not their format.
4. **Azure blob CSV source** (`builtins/sources/azure.py`) reads a non-seekable stream, so the identifier rule is not applied there.
5. #41 on the emulator: run `pytest -m emulator plugins/shape-eventhubs/tests` in the nightly job and, if wanted, add a
   skewed-delivery case there.
