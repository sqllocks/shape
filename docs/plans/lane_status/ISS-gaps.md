# ISS-gaps — capability-gap issues #13, #15, #16 (lane/ISS-gaps)

Status: **built; awaiting lead verification.** Branch `lane/ISS-gaps`, from `build/main-plan` at
`4051235` (it had not moved when last fetched). No gate, tolerance or decision (D-xx, T-xx) was
changed or needs to change; §11 and §2.3 were not edited; `$REFENGINE_ROOT` was not touched; no test was
skipped or xfailed. The issues were not commented on, labelled or closed.

New behaviour is additive: new options, a new command and new log files. Output of `shape generate`,
`shape continue` (change mode) and every sink without the new options is unchanged
(`test_generate_without_the_options_is_unchanged`, `test_a_sink_without_the_option_writes_as_before`,
`test_the_landing_bytes_are_the_plain_writers_bytes`).

## Coordination with the lanes in progress

| Lane | What it builds that touches these issues | What I did |
|---|---|---|
| `lane/P6-13` (scale router and sinks) | `ParquetSink` writing `<dir>/<table>/part-NNNNNN.parquet`, the Fabric lakehouse, warehouse, SQL and KQL sinks (cloud targets) | Did **not** build cloud targets (ADLS Gen2, OneLake) or part-file layouts: issue #15's "natural next step" belongs to that lane. My layout is local, one file per table per date, and works for the dated template; a part-file name is expressible as a literal in `--path-template` (`table={table}/date={date}/part-000000.{ext}`). Doc says so (`docs/LANDING.md`, "What this does not do"). |
| `lane/P6-04a`, `lane/P6-04b` (simulation) | `shape simulate` file-drop: dated partition folders (`dt=YYYY-MM-DD`), a `file_naming` template with `{domain} {entity} {dt} {seq} {ext}`, formats list, `_done` flags, manifests, late arrivals, duplicates, backfills over a date range | Did **not** duplicate a range simulator. Mine writes the plain drop of one date from `generate`, `continue` and `chaos`. The token names differ (`{table}`/`{date}` here, `{entity}`/`{dt}` there); unifying them is a decision for when both are on the main line. Both lanes add argument registration to `cli/main.py`; I added three small, separate hunks there (a `chaos` import and dispatch), so a merge conflict, if any, is textual. |

Neither lane was merged into this branch (they are not on `build/main-plan`). When they land, `git
merge origin/build/main-plan` here and re-run the checks.

## Issue #13: a chaos generator with a ground-truth log

| Requirement | Before | Now | Evidence |
|---|---|---|---|
| Named corruptions with a rate and a seed | `shape.chaos` (P6-02): six randomised categories whose scheduler picks rows and rates; no exact rate, no CLI to corrupt a table | `shape.chaos.groundtruth`: `Corruption(kind, rate, table, column, start_batch, end_batch, options)`, exact rate `round(rows x rate)`, per-corruption seeded streams; `shape chaos` | `tests/chaos/test_groundtruth.py` (45), `tests/iss_gaps/test_cli_e2e.py` |
| Duplicates (a source delivering rows twice) | `duplicate_pks` (referential) repeats key values, not rows | `duplicates`: row copies appended at the end, `source_row` in the log | `test_duplicates_append_copies_and_name_their_source` |
| Orphan keys | `orphan_fks` (referential), randomised, no row log | `orphan_keys`, exact rate, matches no parent (parent key range is read when the reference is known); reuses `_write_orphans` | `test_orphan_keys_match_no_parent`, `..._on_a_text_key` |
| Shifted dates | `late_arrivals` (temporal), tz-naive timestamps only | `date_shift`: date and timestamp (any unit, with or without zone), up to `days`, `direction` both/late/early; each shift logged | `test_date_shift_*` |
| Negative amounts | `negative_amounts` (value), randomised | `negative_amounts`: flips positive values only, defaults skip keys | `test_negative_amounts_*` |
| Status case and trailing whitespace | missing | `case_whitespace` (upper, lower, leading, trailing; every logged row really changes) | `test_case_whitespace_makes_inconsistent_categories` |
| A free-text column filled with SSN-format values | missing | `pii_fill` (`ssn` with never-issued 9xx areas, `email`, `phone`) | `test_pii_fill_writes_ssn_shaped_values`, e2e |
| Type change (numbers delivered as strings) | `retype_column`/`wrong_types` (randomised, scheduler) | `type_change`, active in a batch window (`from`, `to`); logged as a column-scope record | `test_type_change_*` |
| Null rate that ramps over days | `inject_nulls` at a fixed rate | `null_creep`: `rate + step x (batch - from)`; `--start-date/--batch-date` derive the batch | `test_null_creep_ramps_with_the_batch`, `test_the_daily_pipeline_generate_corrupt_land` |
| **Ground-truth log** (which rows, which column, what, with the seed) | `MutationEvent` has a kind, a column and a **count** only; `inject_anomalies` lists rows/kinds/columns for streams only | JSON Lines: a `run` record (seed, batch, corruptions, rows in/out per table) then one `change` record per change: `table, kind, scope, row, key, column, before, after, seed, batch` (+ `source_row`, `days`, `pii`, `rate`). Byte-identical for a seed | `test_the_log_lists_exactly_the_changed_cells` (the cells that differ between input and output are exactly the logged ones, with their values), `test_chaos_is_deterministic_to_the_byte` |
| Applied to a generated table or batch | only through `shape pack` chaos or the Python API | `shape chaos DOMAIN\|SCHEMA [--input DIR] -o DIR --corrupt ...` (generates, or reads `--input`) | below |

Runnable example (run in this session on retail; 814 changes, 9 files written):

```
shape chaos retail --scale small --seed 7 -o corrupted/ --corrupt duplicates=0.02@order \
  --corrupt orphan_keys=0.01@order.customer_id --corrupt date_shift=0.03@order.order_date:days=14 \
  --corrupt negative_amounts=0.02@order.order_total --corrupt case_whitespace=0.05@order.status \
  --corrupt pii_fill=0.05@customer.email --corrupt type_change=1@order.shipping_address_id \
  --corrupt null_creep=0.02@order.promotion_id:step=0.01
```

Design notes. The new corruptions are **beside** the six categories, not new sub-mutations of them:
the categories' scheduler and draw order are what `chaos_1to1/verify.py` compares to the baseline, and
adding a sub-mutation would change those draws. The module reuses the categories' helpers and the
orphan writer. The log is built by the corruption itself (not by diffing) and the test proves it equals
the diff. Rows are never reordered or removed, so a log position is an output position. Corruptions of
different kinds do not disturb each other's random streams (`test_adding_a_corruption_...`).
Doc: `docs/CHAOS.md` ("Targeted corruptions and the ground-truth log").

Not done, on purpose: the issue's closing note (the profiler seeing a column that is only partly SSNs,
and `diff` reporting a pattern change, issues #2 and #3) belongs to those issues; no test here uses
`shape profile` or `shape diff` on a chaos output.

## Issue #15: a date-partitioned landing layout with a format per table

| Requirement | Before | Now | Evidence |
|---|---|---|---|
| Path template `{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}` | `files.py` sinks: `<dir>/<table>.<ext>` only; `shape pack` landing roots; no template | `shape.io.landing` (tokens `{table} {ext} {date} {yyyymmdd} {yyyy} {mm} {dd}`; relative only, no `..`); sink options `path_template`, `batch_date` on csv/tsv/jsonl/parquet/ipc; `--path-template` | `test_template_of_the_issue_renders_the_documented_path`, `test_file_sinks_take_path_template_and_batch_date` |
| A format per table | one `--format` per run | `--table-format TABLE=FORMAT` (repeatable); a misspelt table is an error; `shape.generation.landing.write_landing` | `test_a_format_per_table`, `test_generate_lands_one_file_per_table_in_its_format` |
| A batch date; one batch per run; a backfill writes a range | none | `--batch-date`; never read from the clock (a date token without it is an error); range via `continue --end-date` | `test_a_dated_template_without_a_date_is_an_error`, `test_a_backfill_writes_one_file_per_day` |
| Works from the commands | n/a | `shape generate`, `shape continue` (daily and change mode) and `shape chaos` share `cli/landing.py` | e2e tests |
| Cloud targets (ADLS Gen2, OneLake) | not in `main` | **not built** (P6-13, above) | `docs/LANDING.md` |

Runnable (retail, small): `shape generate retail --scale small --seed 7 --format parquet
--table-format customer=csv --batch-date 2026-08-04 -o land/` wrote
`land/customer/ingest_date=2026-08-04/customer_20260804.csv`,
`land/order/ingest_date=2026-08-04/order_20260804.parquet` and the other tables as Parquet.
Landing generates tables whole (not streamed). `generate --format summary` with a landing option is an
error ("give --format"), because the default format writes nothing. Doc: new `docs/LANDING.md`.

## Issue #16: documented incremental daily batches

| Requirement | Before | Now | Evidence |
|---|---|---|---|
| Batch / "as of" concept, new rows only | `shape continue` (P6-05) is a **change** mode: needs `--input` files, clones rows, tags INSERT/UPDATE/DELETE; `--as-of` is a timestamp | `shape continue --daily-rows TABLE=N --start-date D --batch-date D [--end-date D]`, `shape.generation.batches.BatchGenerator` | e2e tests |
| Stable keys across batches | sequence keys only continue above the max of existing files; string keys unsupported (`primary key has no integer column`) | row addressing: batch `i` is rows `i*N..(i+1)*N-1` of a table whose count is the running total, so `sequence` and `pattern` (`C{seq:9}`) keys never change | `test_keys_are_stable_across_batches` |
| FK references parents from earlier batches, full integrity | change-mode inserts reference parents that exist after the delta | orders draw customers from all customers after that day; checked on files | `test_a_backfill_references_earlier_batches_with_full_integrity` (6 days, 180 customers, every FK a real customer, rows pointing to earlier days > 0) |
| Any single day regenerable alone; same seed and date, same bytes | not possible without the previous day's files | depends only on schema, seed, N and the distance from `--start-date`; one-day run bytes equal that day's bytes in a 5-day run | `test_one_day_alone_is_the_same_bytes_as_that_day_in_the_run`, `test_a_batch_is_regenerable_alone_and_byte_identical` |
| Avoid generating the whole parent each day | issue notes 0.07 s is fine, large parents not | `generate_chunk` fast path generates only the day's rows (equal to the slice of the full table, `test_the_fast_path_equals_the_whole_table_slice`); schemas with a post-pass (retail: computed totals) generate the running total and slice; a non-sequence parent's key pool is still generated per day (documented) | `docs/INCREMENTAL.md` "Limits of daily batches" |
| Rows dated the day | n/a | `--date-column TABLE.COL` | `test_the_date_column_is_the_batch_date` |
| A worked example in `docs/GENERATION_ENGINE.md` | none | section "Daily batches" with a pointer to the example in `docs/INCREMENTAL.md#daily-batches` (command, Python, guarantees, limits) | docs |

Runnable: `shape continue retail --daily-rows customer=300 --daily-rows order=4000 --start-date
2026-08-01 --batch-date 2026-08-01 --end-date 2026-08-31 --date-column order.order_date -o landing/`
wrote 31 days, 62 files (0.55 s for one day of retail).

## Checks run in this session

| check | result |
|---|---|
| `ruff check` and `ruff format --check` on `src tests plugins benchmarks/vs_refengine` | clean (723 files) |
| `mypy` (strict, all of shape) | no issues, 313 files |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | no output |
| `lint-imports` | 1 contract kept, 0 broken |
| `python scripts/check_user_facing.py` | clean |
| `bandit -q -r src -ll` | no findings (only the existing `nosec` notices) |
| new tests (`tests/iss_gaps`, `tests/chaos/test_groundtruth.py`) | 150 passed together with the existing chaos tests |
| `incremental_1to1/verify.py` (baseline venv Python) | PASS, 0 failures, 56.7 s |
| `pack_1to1/verify.py` (baseline venv Python) | 5 inputs, T-21 64/64, 0 problems, PASS, 23.2 s |
| `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, `SHAPE_KERNEL=rust` | 4607 passed, 46 deselected (359 s) |

| `pytest` (same selection), `SHAPE_KERNEL=python` | 4607 passed, 46 deselected (692 s) |
| `chaos_1to1/verify.py` (Shape venv Python, as its header says) | PASS: 20,083 / 20,083 outputs identical cell for cell |
| START (median of 10 `shape --version`) | 45.8 ms (gate 300 ms) |

Notes on the run: both suites ran on the tree after the last code commit (the docs and status commits
after it change no code). The first launch of the chaos harness used the baseline's Python by mistake
and stopped at an import error; it was re-run with the Shape venv's Python, as the harness's header
says. Setup: Shape venv `-e plugins/shape-domains` then `-e .[dev,advanced]`; baseline venv built by
`benchmarks/vs_refengine/setup_refengine.sh` (pinned commit, `$REFENGINE_ROOT` untouched).

## Open points for the lead

- Token names differ between this layout and the file-drop simulator of P6-04a/b (see above).
- Cloud targets for the landing layout belong to P6-13's Fabric sinks.
- `shape chaos --input` reads a flat directory (the files `shape generate` writes); to corrupt a
  daily batch use `--path-template '{table}.{ext}'` on `continue`, as `test_the_daily_pipeline_...` does.
- Change mode (`continue --input`) still cannot extend a table with a string primary key; daily
  batches can.
