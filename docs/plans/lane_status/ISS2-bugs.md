# ISS2-bugs — issues #46, #40, #41, #42 (lane/ISS2-bugs)

Status: **built; awaiting lead verification.** No gate, tolerance, D-xx or T-xx decision was changed; §11 and §2.3
untouched; the pinned RefEngine checkout was only read. Issues were not commented on, labelled or closed. No PR. Branched
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

**Allow-list in the profile parity harness** (`benchmarks/vs_refengine/profile_1to1/verify.py`, README entry "Identifier columns stay
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
* `tests/profile/test_infer.py::test_dtype_of_every_column_matches_the_refengine_profiler`: a column that the reader now keeps as text
  while Arrow's own inference says integer is asserted to be `string` in the profile; every other column is compared as before.

## Checks run in this session

Final tree, after merging `origin/build/main-plan` (merge commit, no rebase):

* `ruff check` and `ruff format --check` (src, tests, plugins, benchmarks/vs_refengine): clean. `mypy` (strict): no issues (332 files).
  `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80`: clean. `lint-imports`: 1 kept, 0 broken.
  `python scripts/check_user_facing.py`: clean. `bandit -q -r src -ll`: no findings (only the existing `nosec` notes).
* Rust: no Rust file changed on this lane or in the merge, so `cargo fmt/clippy/test` were not re-run.
* START: median of 10 runs of `shape --version`, 36 ms (limit 300 ms).
* Profile parity `verify.py --impl shape`: exit 0 under `SHAPE_KERNEL=rust` and under `SHAPE_KERNEL=python`; the identifier allow-list
  applied to 12 of 12 columns. Stream profiler equivalence `stream_prof/verify.py`: exit 0 (stream == batch, identical across processes).
* `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`: 4,932 passed under `SHAPE_KERNEL=rust`
  (`.[advanced]` and `-e plugins/shape-domains` installed first). Under `SHAPE_KERNEL=python`: 4,931 passed and 1 failed,
  `tests/streaming/emit/test_runtime.py::test_realtime_rate_within_five_percent` (a wall-clock pacing test, 6.1% off); it passed
  (26/26 in that file) when re-run alone on a quiet machine. Not a code change of this lane (no emit code touched); reported, not hidden.
* `pytest -m heavy tests/kernel tests/profile tests/streaming` (rust): 42 passed. An earlier run, concurrent with the python-kernel suite,
  failed `test_bounded_mode_memory_does_not_grow_with_rows` (12% growth, limit 10%); alone it passed (82 s). A contention artefact.
* Not run: the Event Hubs/Kafka emulator tests (Docker), `pytest -m heavy` under the Python kernel (the lane before this one found its
  bounded-memory test runs for over 40 minutes).


## Proposals for the lead / owner (not built)

1. **Wider identifier rule** (see "Not as filed"): fixed width alone / name alone. One function, `shape.io.identifiers.judge`.
2. **`CHAR(n)` sequence keys zero-padded to `n`** in from-ddl (`sequence` strategy with a `width`): needs `keypos`/foreign-key
   handling and an allow-list entry in the DDL parity harness.
3. **Found, not fixed:** `learn` on a column of NDC codes (`00093-0058-01`) picks the `phone_number` faker because the detected
   pattern is `phone`; the profile's own value list is not used. Values keep text but not their format.
4. **Azure blob CSV source** (`builtins/sources/azure.py`) reads a non-seekable stream, so the identifier rule is not applied there.
5. #41 on the emulator: run `pytest -m emulator plugins/shape-eventhubs/tests` in the nightly job and, if wanted, add a
   skewed-delivery case there.

## Round 2 — merge of `origin/build/main-plan` (26dc2e9, INT-12)

Merge commit `ac43659` plus two follow-up commits (`34d0d27`, `dddfb0c`); no rebase, no force-push. No gate, tolerance, D-xx or T-xx
decision, §11 or §2.3 was touched; `$REFENGINE_ROOT` was only read (its working tree already showed line-ending noise before this lane).

### What conflicted and how it was resolved

| File | Resolution |
|---|---|
| `src/shape/streaming/cli.py` | **One duration parser**: `shape.streaming.runtime.parse_duration` (ISS-stream #34). My `_DURATION` regex and `_UNIT_US` table are gone; `duration_us = parse_duration`, `_duration() -> timedelta` for window sizes. Kept `DEFAULT_IDLE_TIMEOUT`, `LATE_WARN_SHARE`, `_idle_timeout`, `_late_report`, `--partition-idle-timeout`, `--max-partition-skew` (parsed with `duration_us`), `--allowed-lateness` reporting. |
| `docs/plugins/streaming.md` | One rule documented (below) in the `--size/--slide/--gap` row; `--allowed-lateness` and `--max-partition-skew` say "a duration"; `--partition-idle-timeout` says it is a plain number of seconds. All of #33's rows kept (`--start`, `--order`). |
| `src/shape/builtins/sinks/files.py` | Both imports (`render_path`, `require_scheme`); `write()` calls `require_scheme(self, uri)` then `self._target(uri, table, options)`. |
| `src/shape/cli/generation.py` | Kept `DEFAULT_TEMPLATE` (landing); dropped the static `FORMATS` tuple (formats now come from installed sinks, round 1). |
| `src/shape/cli/main.py` | `profile` passes `version`/`as_of` (#36) and the CSV/identifier options (#46); both sets of arguments kept; `_warn_empty` kept. |
| `src/shape/profile/reference/profile.py` | `profile()` takes both option sets; `_profile` runs the Delta version/as-of path (#36) and the CSV format and delimiter warning (#46). |
| `src/shape/profile/reference/sources.py` | `load_columns` keeps the CSV format argument and the row-dicts source. |
| `src/shape/generation/engine.py` | Merged without a textual conflict. Checked: HEAD's `release()` is the round-1 version with `copula_applied`; `release(len(rules), copula_applied=True)` is correct (the branch's side had the older `release(after_rule)` that kept copula tables back, which is the bug round 1 fixed). |

**Semantic conflicts found by `mypy` / tests, not by git:**

* `generation/landing.py` (new on main-plan) imported `EXTENSIONS`/`FORMATS`, which round 1 had replaced by the installed `shape.sinks`. It now uses
  `available_formats()` and a new `output.file_extension(fmt)`; `LANDING_FORMATS` became `landing_formats()` (no other user).
* `tests/streaming/test_partition_watermark.py` (round 1) built profilers with bare-integer durations (`2 * SEC`), which #34 refuses; they now pass `timedelta`. No assertion changed.

### The duration rule (one parser, one rule)

A duration is a string with a unit (`500ms`, `30s`, `5m`, `1h`, `2d`, `250us`), where a bare numeric **string** means seconds, or a `timedelta` (Python API
only). A bare `int`/`float` is refused in the Python API (`0` allowed). The CLI always passes strings, so `--size 60` is 60 s; that is the same string rule, not a
split. `--partition-idle-timeout` is not a window duration: a plain number of seconds (default 30, 0 off). Internal attributes (`max_partition_skew`, snapshots) stay integer microseconds.

### Commands and results (final tree, `source scripts/env.sh`, venv with `.[dev,advanced]` and the domains, kafka and eventhubs plugins editable)

* `ruff check` and `ruff format --check` on `src tests plugins benchmarks/vs_refengine`: clean (872 files). `mypy`: no issues (349 files). `vulture`, `lint-imports`, `check_user_facing`: clean.
* `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`: **5,367 passed, 1 skipped** under `SHAPE_KERNEL=rust`; **5,367 passed, 1 skipped** under `SHAPE_KERNEL=python`. The skip is `tests/profile/test_identifier_columns.py:363` (`faker` not installed here).
* `pytest tests/streaming` (includes the heavy-marked tests): 367 passed. `pytest plugins/shape-kafka plugins/shape-eventhubs -m "not emulator and not live"`: 86 passed.
* `benchmarks/vs_refengine/stream_1to1/verify.py --scale small` (baseline built with `benchmarks/vs_refengine/setup_refengine.sh`; `scripts/setup_refengine.sh` does not exist): **VERDICT PASS**, exit 0.
* Deviations from the task text: the full run excluded `heavy` (as `make check` does, and as round 1 did); heavy was run for `tests/streaming` only. `pytest tests/demo/fabric`, the emulator tests (Docker), the cargo checks (no Rust file changed), `profile_1to1` and `stream_prof` verifiers were not re-run in round 2.

## Round 3 — merge of `origin/build/main-plan` (ce8fe11, INT-13)

One merge commit (`216aa56`, plus the status commit); no rebase, no force-push. No gate, tolerance, D-xx or T-xx decision, §11 or §2.3
was touched; `$REFENGINE_ROOT` was only read (the baseline was built into a fresh container with
`benchmarks/vs_refengine/setup_refengine.sh`). Main-plan brought ISS-profile (#2, #21-#24, #37, CSV options), ISS-gen (#9-#12, #17-#19),
EXCEL (#50, #51), FIN-CAP (`load_table`), P6-07b (`--auth`, credential references, redaction in `errors.fail`) and CI-FIX round 3.

### What conflicted and how it was resolved (both sides kept)

| File | Resolution |
|---|---|
| `CHANGELOG.md` | Both lists of entries kept. |
| `src/shape/cli/main.py` | `profile`: P6-07b's `--auth` check and `source_options(credential=...)` wrapper kept; one `options` dict holds the CSV and identifier options (`string_columns`, `types`, `infer_types`) and the workbook options (`--sheet`, `--include-hidden`); `--delimiter`, `--encoding`, `--quotechar`, `--header` are declared once; `--string-columns`, `--types`, `--infer-types`, `--version`, `--as-of`, `--fail-on-empty`, `--sheet`, `--include-hidden` all present. |
| `src/shape/generation/engine.py` | `release(len(rules), copula_applied=True)` (the round-1 fix, so correlated tables are written) followed by main-plan's `self.finalize(...)` of every table. |
| `src/shape/generation/learn.py` | Both helper sets kept (`_text_enum`, `_digit_identifier_width` from #46; `_zero_padded_width` from EXCEL). Order in `column_generator`: a column whose profile lists every value keeps that value set as text (#46); else a zero-padded text column gets EXCEL's `digit_ids` (unique) / `digits` provider; else a fixed-width digit column gets the `{digits:N}` pattern (#46). |
| `src/shape/generation/output.py` | Both kept: the sink-based `_paths(fmt, sink, ...)`, `_check_destination` (#42, #40) and EXCEL's `_write_workbook`. The workbook writer now also calls `_check_destination`, so `-f excel` to an `abfss://` URI gives the #42 error instead of creating a folder called `abfss:`. |
| `src/shape/io/readers.py` | Both imports (`identifiers` for #46, `excel` for #50). |
| `src/shape/profile/reference/readers.py` | One flag, not two: my `_Col.keep_text` and EXCEL's `_Col.text` meant the same thing; it is `_Col.text` now (reader-fixed identifier text, `string_columns`, Excel text cells). |
| `src/shape/profile/reference/column.py` | The bool-spelling detector skips `c.text` columns (the same line, one flag). |
| `src/shape/profile/reference/profile.py` | `profile()` takes `version`, `as_of`, the CSV options, `string_columns`/`types`/`infer_types` and `sheet`/`include_hidden`; the workbook branch runs first, then `_profile(source, name, version, as_of, fmt)` (main-plan's argument order). |
| `src/shape/profile/reference/sources.py` | Imports of both sides (`is_workbook_spec`, `read_csv_detect`); `load_table`, `source_options` and the Delta paths from main-plan kept. |

**Semantic conflicts found by `mypy` and the tests, not by git (all in this lane's code):**

* `profile/reference/sources.py::_to_cols` (EXCEL) assigned `c.text = kind == "xlsx" and ...` to every column, which **reset** the flag the CSV reader had set, so
  every #46 identifier column came out `integer` again (22 of the 37 tests in `tests/profile/test_identifier_columns.py`, both kernels, plus
  `test_dtype_of_every_column_matches_the_refengine_profiler`). It is now `c.text = c.text or (kind == "xlsx" and ...)`.
* `learn.py`: two variables named `width` of different types (`mypy`); mine is `digits`.
* `learn.py`: a ZIP column (leading zeros, every value listed) went to EXCEL's `digits` provider before the covered-value-set rule, so generated ZIPs were no
  longer drawn from the profile (`test_generate_from_a_profile_keeps_the_zeros_and_the_width`, `test_the_csv_file_itself_quotes_the_text`). The order above fixes it.
* **One assertion of this lane's own test changed**, `test_learn_builds_text_generators`: a `member_id` column of unique, zero-padded ten-digit values now
  gets EXCEL's `{"strategy": "faker", "provider": "digit_ids", "width": 10}` (keeps the key unique) instead of `{"strategy": "pattern", "format": "{digits:10}"}`.
  The property the test is about (text, width 10, zeros kept) is unchanged and still asserted by the generate tests; the pattern is asserted on `npi`
  (ten digits, no zeros). No other lane's test changed. Reported for the lead to confirm that the two generators should coexist this way.

### Commands and results (final tree; venv with `.[dev,advanced]`, the domains, kafka and eventhubs plugins editable)

* `ruff check` and `ruff format --check` on `src tests plugins benchmarks/vs_refengine`: clean (899 files). `mypy`: no issues (356 files). `python scripts/check_user_facing.py`: clean.
* `pytest -m "not emulator and not live" --ignore=tests/demo/fabric` under `SHAPE_KERNEL=rust` (heavy included): **5,628 passed**, 4 deselected.
* Same under `SHAPE_KERNEL=python`: **the heavy tests were not run**: the heavy bounded-memory profile test ran for over 30 CPU-minutes without finishing (as round 1 recorded) and was stopped. `-m "not emulator and not live and not heavy"`: **5,586 passed**, 46 deselected.
* `pytest tests/streaming` (heavy-marked tests included): 367 passed. `pytest plugins/shape-kafka plugins/shape-eventhubs -m "not emulator and not live"`: 86 passed, 31 deselected.
* `benchmarks/vs_refengine/stream_1to1/verify.py --scale small`: **VERDICT PASS**, exit 0.
* `benchmarks/vs_refengine/profile_1to1/verify.py --impl shape` (after `datasets.py` generated the data): exit 0 under `SHAPE_KERNEL=rust` and under `SHAPE_KERNEL=python`; the identifier allow-list applied to 12 of 12 columns.
* Not run: `tests/demo/fabric` (excluded as in earlier rounds), the emulator tests (Docker), cargo checks (no Rust file changed by this lane or this merge), `stream_prof/verify.py`, `pytest -m heavy` under the Python kernel (above).

## Round 4 — merge of `origin/int/INT-15` (ec42441)

Merge commit `5399071` plus `7104bcb` (a duplicated `_paths`/`_check_destination` left by the merge); no rebase, no force-push. No gate,
tolerance, D-xx or T-xx decision, §11 or §2.3 was touched; `$REFENGINE_ROOT` was not modified (the baseline was built into this fresh
container with `benchmarks/vs_refengine/setup_refengine.sh`). Note: this container's checkout of the branch was behind `origin/lane/ISS2-bugs`
and was fast-forwarded (`git merge --ff-only`) before the merge.

### What conflicted and how it was resolved (both sides kept)

| File | Resolution |
|---|---|
| `CHANGELOG.md` | Both lists of entries kept. |
| `benchmarks/vs_refengine/profile_1to1/README.md` | The "Identifier columns stay text" entry kept; the "New fields" paragraph is INT-15's (it adds #47's `placeholders` and `joint`). |
| `src/shape/builtins/sinks/delta.py` | INT-15's file: `delta+abfss`/`delta+abfs` schemes, `DeltaTableWriter` (`commit_rows`/`commit_seconds`), `require_scheme`, `extension = ""`. W2-04's `_utc_timestamps` is applied in the writer constructor and in `write()`, so every Delta path (single commit and micro-batch) writes naive timestamps as UTC (reader 1 / writer 2). |
| `src/shape/builtins/sinks/files.py` | Both: `render_path`/`require_scheme` imports with `DEFAULT_ROLL_TEMPLATE`; `write()` calls `require_scheme` first, then the rolling path, then the plain path. |
| `src/shape/plugins/schemes.py` (add/add) | INT-15's file (it built on the #42 routing: `file` listed first, "provided by the abfss sink" messages). Message text still satisfies this lane's tests. |
| `src/shape/generation/output.py` | Both: this lane's `file_extension`, `_check_destination` (scheme checked before any local folder is created, also for `-f excel`) and INT-15's `TargetOptions`/`write_targets`. |
| `src/shape/generation/landing.py` | This lane's `landing_formats()` (formats come from the installed sinks); INT-15 had only the old static tables. |
| `src/shape/cli/main.py`, `src/shape/profile/reference/profile.py` | Both option sets (`--string-columns`, `--types`, `--infer-types`, `--reference-pair`, `--joint`); the CSV options are declared once. |
| `src/shape/io/readers.py`, `src/shape/profile/reference/sources.py` | Imports of both sides (`identifiers`, `read_selection`, `delta_fallback`, `read_csv_detect`). |
| `tests/generation/test_sink_formats_and_schemes.py` (add/add) | INT-15's two relaxed message assertions (they accept both wordings) plus this lane's `test_a_table_with_correlated_columns_is_written`. |

`mypy` found the one semantic leftover (the duplicated helpers in `output.py`); fixed.

### Commands and results (final tree; venv with `.[dev,advanced]`, domains, kafka, eventhubs, sqlserver, fabric and databases plugins editable)

* `ruff check` and `ruff format --check` on `src tests plugins benchmarks/vs_refengine`: clean (1,090 files). `mypy`: no issues (437 files).
* `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, `SHAPE_KERNEL=rust`: 6,882 passed, 7 failed (first run, fabric/databases plugins not installed). With the plugins installed `tests/demo_cmd` passes (2 of the 7 were this), leaving **5 failures + 1 on INT-15 itself** below.
* Same, `SHAPE_KERNEL=python`: 6,886 passed, **5 failed** (the same 5).
* `SHAPE_KERNEL=rust pytest -m heavy tests/kernel tests/profile tests/streaming`: 42 passed (885 s). Not run under the Python kernel (as in earlier rounds, its bounded-memory test runs for over 30 minutes).
* `plugins/shape-fabric plugins/shape-sqlserver plugins/shape-databases tests/demo_cmd tests/cli/test_generate_to.py tests/streaming/emit/test_table_sink.py` (rust): 893 passed, 1 failed (below).
* Profile parity `verify.py --impl shape`: exit 0 under `SHAPE_KERNEL=rust` and `=python`; identifier allow-list applied to 12 of 12 columns. `stream_1to1/verify.py --scale small`: VERDICT PASS, exit 0. `stream_prof/verify.py`: exit 0 (stream == batch; identical across processes; 12 of 12).
* Not run: `tests/demo/fabric`, emulator and live tests, cargo (no Rust file changed).

### Open failures

1. **Reproduces on `origin/int/INT-15` without this lane** (worktree of `origin/int/INT-15`, own venv):
   `SHAPE_KERNEL=rust pytest plugins/shape-fabric/tests/test_publish.py::test_lakehouse_writes_the_landing_zone_and_the_manifest` fails (the manifest has the W1-03 keys `version`, `reproducibility`, `format`, `dataset_id` that the P6-07c test does not expect). Not fixed here.
2. **Caused by this merge: #46 (this lane) against ISS2-joint's tests. Not fixed; needs the lead/owner.** Five tests fail under both kernels:
   `tests/joint/test_diff_contract_joint.py::{test_the_example_is_drift_that_names_the_dependency_and_the_value, test_contract_rules_pass_the_good_data_and_fail_the_bad, test_no_placeholder_object_form_allows_values_and_shares}`,
   `tests/joint/test_profile_joint.py::{test_the_example_reports_the_dependency_that_broke, test_every_input_kind_gets_the_same_joint_analysis}`.
   Cause: the `city_zip` fixture writes a CSV whose `zip` column holds `00000` placeholders and real ZIPs such as `02134`. #46 reads that column as text, so the placeholder is `'00000'`; the ISS2-joint tests assert `'0'` ("the placeholder, read as 0"), the integer reading that #46 reports as the bug, and one number moves (`confidence` 0.88225 against 0.87575, `violating_groups` 174 against 178, because `02134` and `2134` are no longer one value). Both sets of tests cannot pass unchanged. I did not edit those tests or weaken the identifier rule. Options: (a) update the five expectations to the text values (they would then also pin ZIP-as-text, which the owner asked for in #46); (b) give the fixture a `--types`/`string_columns` override and keep the numbers (still changes the test); (c) narrow the rule. Recommendation: (a).

## Round 5b — lead decision (a) for the ISS2-joint ZIP tests, merge of `origin/int/INT-15` (f99563e)

Merge commit (no rebase, no force-push) plus `b6702e1`'s test commit. No gate, tolerance, D-xx/T-xx, §11 or §2.3 text was touched; `$REFENGINE_ROOT` was not modified
(baseline built into this fresh container with `benchmarks/vs_refengine/setup_refengine.sh`). The merge was conflict-free (INT-15's change is two test lines and `delta_fallback.py`).

### The five ISS2-joint tests (decision 1, option a)

Updated to the text reading of the zero-padded ZIP column, each with a one-line `#46` comment:
`test_the_example_is_drift_that_names_the_dependency_and_the_value`, `test_contract_rules_pass_the_good_data_and_fail_the_bad`,
`test_no_placeholder_object_form_allows_values_and_shares` (tests/joint/test_diff_contract_joint.py);
`test_the_example_reports_the_dependency_that_broke`, `test_every_input_kind_gets_the_same_joint_analysis` (tests/joint/test_profile_joint.py).

* Placeholder `'0'` becomes `'00000'` (diff `placeholders[0].value`, the message, `placeholder_surge.detail.value`, check `observed[0].value`, `allow`, and `violations[0].determinant_value`).
* **No number moved.** I recomputed `zip -> city` on the `bad` fixture three independent ways: (1) plain Python over the CSV text (group by zip text, sum of the majority city per group / rows, groups with more than one city); (2) `shape.profile.dependencies.functional_dependency` on the text rows; (3) Shape's own joint entry. All give confidence 0.87575, 178 violating groups, 320 rows on `'00000'`, 3,501 groups, `implausible_rate` 0.08. The integer reading gives the same, because in this fixture no padded ZIP collides with an unpadded one. So `confidence` 0.87575, `violating_groups` 178 and the 0.08 shares are unchanged; the 0.88225 / 174 shift expected in round 4 does not occur.
* One change beyond the placeholder: `test_every_input_kind_gets_the_same_joint_analysis` builds its table input with `zip` as a string (`pcsv.ConvertOptions(column_types={"zip": pa.string()})`) because the `_csv` helper casts it to int64, which makes the table/parquet inputs differ from the CSV profile (which is text under #46). `_csv` is unchanged (`test_a_dependency_matches_the_fd_command` still uses it and passes).

### Commands and results (venv `$SHAPE_VENV` with `.[dev,advanced]` and the domains, kafka, eventhubs, sqlserver, fabric, databases plugins editable; pyarrow 25.0.1)

* `ruff check` and `ruff format --check` on `src tests plugins benchmarks/vs_refengine`: clean (1,090 files). `mypy`: no issues (437 files).
* `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`: `SHAPE_KERNEL=python` 6,891 passed; `SHAPE_KERNEL=rust` 6,891 passed; 0 failures (55 deselected). #76 (pyarrow < 25) and #77 (credential-refs ordering) did not occur in this environment.
* `SHAPE_KERNEL=rust pytest -m heavy tests/kernel tests/profile tests/streaming`: 42 passed (912 s). Not run under the Python kernel (as in earlier rounds).
* `profile_1to1/verify.py --impl shape`: exit 0 under `SHAPE_KERNEL=rust` and `=python` (datasets built first with `datasets.py`). `stream_1to1/verify.py --scale small`: VERDICT PASS, exit 0. `stream_prof/verify.py`: exit 0 (stream == batch, identical across processes).
* Not run: `tests/demo/fabric`, emulator and live tests, cargo (no Rust file changed).

### Open failures

None.
