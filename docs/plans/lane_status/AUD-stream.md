# AUD-stream: audit of the streaming engine and sinks

Lane: audit-and-fix, area **streaming engine and sinks** (`src/shape/streaming/` and the tests that
exercise it). Branch `lane/AUD-stream`, started from int/INT-15 (`f99563e`).

## Phase 1: baseline

* `pytest -m "not emulator and not live" tests/streaming tests/test_streaming_async.py
  --cov=shape.streaming`: 373 passed, 1 skipped (`shape_databases` not installed), 92% line coverage.
  Least covered: `emit/tables.py` 58% (the threaded writer), `monitor.py` 67%, `vectorized.py` 74%,
  `evidence.py` 75%, `core.py` 75%, `aggregate_windows.py` 77%, `platinum.py` 78%.

## Findings

Severity, place, reproduction, expected, actual. Issue numbers and fix commits are in the table
below the list.

1. **high** — `streaming/cli.py:306-312` (`shape stream-profile`). Ctrl-C with `--checkpoint`, then
   the same command again: the windows file keeps the *partial* profile of each window that was open
   at the interrupt, and the complete one is never written (its key is already in the file).
   Repro: 50 events one a minute, `--window tumbling --size 10m --batch-size 15 --checkpoint-every 1`,
   interrupt after the second batch, rerun. Expected: the window starting 00:10 has 10 rows, as in an
   uninterrupted run (STREAMING_SEMANTICS §6: "after any number of reconnects or restarts, the windows
   equal those of an uninterrupted run"). Actual: 5 rows.
2. **high** — `streaming/messages.py:83-100`. An event time that is a number out of the timestamp
   range (`{"_shape_event_time": 1e30}`) raises `OverflowError` from `decode_messages`, which ends the
   whole read (file source, Kafka, Event Hubs). Expected: the value is not a valid time, so the broker
   timestamp (or null) is used, as for any other invalid time. Actual: the stream dies on one message.
3. **high** — `streaming/windows.py:387-392`, `streaming/aggregate_windows.py:57-61`. `TumblingWindow`
   and `AggregateTumblingWindow` compute the window start with float division, so a sub-second size
   puts events in the wrong window: with `size=100ms` an event at `00:00:01.300` lands in
   `[00:00:01.200, 00:00:01.300)`, which does not contain it. Expected: `start <= t < end`.
4. **medium** — `streaming/emit/anomaly.py:357-366`. `ValueAnomalyMutator` rewrites every value of a
   numeric column through float64, not only the targeted rows: nulls become `0` (integers) or `NaN`
   (floats), integers above 2^53 lose precision (`2**60+1` -> `2**60`), and an outlier clipped to the
   int64 maximum wraps to `-2**63`. Expected: one value per row changes ("one value per row becomes an
   outlier or empty, or null").
5. **medium** — `streaming/keyed.py:432-437`. `KeyedSketches`: a key past its TTL but not yet swept
   (sweeps run every `ttl/16`) is reported absent (`key in s` is False), yet when it returns its new
   events are added to the old sketch. Repro: ttl 16, `a@0`, `b@15.5`, `c@16.2`, `a@16.3` gives
   `count 2, first_time 0`. Expected: `count 1`, a new sketch (the key was dropped).
6. **medium** — `streaming/dedupe.py:358-367`. `Deduplicator`: a first batch that is empty or all null
   (Arrow type `null`) fixes the key kind to `hash`, and every later integer batch raises
   `TypeError: this state holds hash keys, not int keys`. Expected: a batch with no keys does not
   decide the kind.
7. **medium** — `streaming/cli.py:145-150`. The `--windows` file of `shape stream-profile` is read on
   restart with `json.loads` per line; a half-written last line (a crash mid-write) makes every restart
   fail with "not valid JSON". Expected: the torn line is cut (as `shape emit`'s file sink does) and
   the window is written again.
8. **medium** — `streaming/emit/sinks.py:164-166`. `FileSink.send` leaves whatever part of the batch
   reached the file when the write fails (`ENOSPC` is an `OSError`); the runtime retries the batch and
   appends it after the fragment, so the fragment and the first retried event form one corrupt line.
   Expected: a failed send leaves the file as it was.
9. **medium** — `streaming/vectorized.py:263-283`. `deduplicate_ids([1, "1"], set())` gives
   `[True, False]`: `np.asarray` turns the mixed list into strings, and `seen` receives `"1"` for the
   integer 1. Expected `[True, True]` (a set treats them as different ids).
10. **low** — `streaming/emit/faults.py:200-227`. `FaultSink`: when sending a due duplicate copy fails,
    the runtime retries the whole `send`, so the batch already delivered is sent again (an unlisted
    duplicate), `_sent` counts it twice (later copies come late), and the copies still in the due list
    of that call are lost. Expected: a retry re-sends only what failed.
11. **low** — `streaming/consumer.py:123-124`, `streaming/keyed.py:568-569`. A checkpoint's
    `counters` object is applied with `setattr` for any key it holds, so a damaged or crafted file can
    overwrite `source`, `profiler`, `store` (or any attribute) with an integer. Expected: only the known
    counters are read, anything else is a `CheckpointError`/`ValueError`.
12. **low** — `streaming/file_source.py:445-452`. A CSV whose column changes type after the first
    block (1 MiB) aborts `shape stream-profile` with Arrow's "CSV conversion error to int64: invalid
    value 'abc'", which does not say what to do. Expected: the value is read (the decoder already
    counts rows that do not fit as rejected) or the error names the fix.
13. **low** — `streaming/platinum.py:285-318`. The batch helpers treat a missing value differently from
    the per-value classes: `geo_grid_batch` puts a NaN latitude into cell `(0, ...)` (a bogus cell;
    `GeoGridEvidence.update` raises on it), `relational_batch` counts a NaN key as a checked orphan
    (`RelationalEvidence.update` skips a missing key).
14. **for the lead (not fixed: an existing test pins it)** — `streaming/emit/formats.py:279-280`. The
    CloudEvents `time` attribute must be RFC 3339 (with an offset); for a table whose event-time column
    has no zone it is written as `2024-01-02T03:04:05.678901`, and for a date column as `2024-01-02`.
    `tests/streaming/emit/test_formats.py:93` asserts the zone-less form, so the brief's rule applies:
    recorded, not changed.
15. **observation (not a defect)** — `SlidingProfiler` closes a window by merging its panes, so a
    window of 10 minutes every second costs about 600 pane merges per window (measured: about 50 s per
    1,000 one-second events). This is the documented pane design (STREAMING_SEMANTICS §2); a faster
    scheme (two-stack aggregation) would be a design change.

Second pass, after merging origin/int/INT-18 (per-partition watermarks, issue #41):

16. **high** — `streaming/runtime.py` (`WindowedProfiler.watermark`, `register_partitions`). Once a
    partition is registered the watermark is the partition watermark, which is `None` until the next
    batch. A profiler restored from a snapshot written before partitions (no `partitions` key; the
    restore accepts it) therefore classifies the first batch after a resume against no watermark:
    its rows for windows already closed are neither late nor emitted (they land in panes of closed
    windows and are pruned). Repro: tumbling 10m, minutes 0-24, snapshot without `partitions`,
    restore, `register_partitions(["0"])`, batch of minutes 1, 2, 30. Expected 2 late events.
    Actual 0, and the rows are in no window.
17. **low** — `streaming/cli.py` (`run`). `--max-partition-skew` was set on the fresh profiler,
    which `StreamConsumer` replaces with the one restored from `--checkpoint`, so a resumed run
    kept the first run's skew without saying so.

Third pass, after merging origin/int/INT-18 again (W2-09: dead letters, drift plans, dry run,
progress line, ramps, curves and Poisson arrivals in `emit/`):

18. **low, for the lead (not fixed: an existing test pins it)** — `streaming/emit/rate.py`
    (`DaySchedule._absolute`). The last event of a day that an empty day follows is due when the
    empty day ends, not at the end of its own day: `DaySchedule([0, 5, 0, 10], 10).due_time(5)` is
    30, while `expected_by(20)` already counts 5 events. `tests/streaming/emit/test_drift_stream.py::
    test_the_day_schedule_checks_its_arguments` asserts this (`DaySchedule([10, 0, 10], 4).due_time(10)
    == 8`). The fix would be `bisect_left` in place of `bisect_right`; the regression test was
    committed (b67d89e) and reverted (bebc424) when the pinning test turned up.
19. **for the lead (outside the area)** — three W2-09 tests fail on int/INT-18 itself, because of the
    W1-14 CLI changes: `shape emit --json` now wraps the events in a `shape-result` document, and
    the remote-target confirmation gate runs before the sink URI and `--event-format` are checked
    (`--dead-letter nope://x` and `--event-format avro --to abfss://...` are told to pass `--yes`
    instead of getting the usage error). Tests: `test_arrivals.py::test_constant_is_the_default_and_poisson_runs_end_to_end`,
    `test_dead_letter.py::test_dead_letter_needs_a_different_destination_and_max_needs_dead_letter`,
    `test_event_format.py::test_a_table_sink_refuses_a_registry_format`.
20. **for the lead (outside the area)** — `tests/test_removed_modules.py::test_removed_module_is_not_importable[history]`
    fails on int/INT-18: W3-03 added `src/shape/history/`, a module plan section 8.1 lists as removed.

Probed and found sound in the third pass: `RateSchedule.due_time` against `expected_by` over 300
random mixes of bursts, ramps and curves (one with a zero-multiplier stretch), 9,000 points, no
mismatch and never decreasing; `DeadLetterSink` retries (a failed dead-letter write is retried
without resending the batch to the primary destination); `FanOutSink` rejections; `ProgressLine`.


## Issues and fixes

Each fix has a regression test in `tests/streaming/test_audit_regressions.py`, committed first
with its failing output in the commit message.

| finding | issue | severity | fix commit | status |
|---|---|---|---|---|
| 1 | #153 | high | 4604d9d | fixed: Ctrl-C windows written `"partial": true`, removed and rewritten complete on restart |
| 2 | #154 | high | 531ddee | fixed |
| 3 | #155 | high | b289a2e | fixed (`window_start`, whole microseconds) |
| 4 | #156 | medium | 99a2a8d | fixed |
| 5 | #157 | medium | 22ee04a | fixed |
| 6 | #158 | medium | 984a63c | fixed |
| 7 | #159 | medium | 0c673d1 | fixed |
| 8 | #160 | medium | b9f022f | fixed (unbuffered write, truncate on failure) |
| 9 | #161 | medium | f368cb3 | fixed |
| 10 | #162 | low | d36f582 | fixed |
| 11 | #163 | low | 7192345 | fixed |
| 12 | #164 | low | 8478e49 | fixed (text re-read after the first-block type stops fitting) |
| 13 | #165 | low | 410c488 | fixed |
| 14 | #166 | low | — | **open, for the lead**: `tests/streaming/emit/test_formats.py:93` pins the zone-less CloudEvents `time`; changing it needs a decision (append `Z` for zone-less timestamps, midnight `Z` for dates?) |
| 15 | — | — | — | observation only (pane design) |
| 16 | #753 | high | dacbec8 | fixed (`register_partitions` keeps the watermark the profiler already has) |
| 17 | #754 | low | a1fb750 | fixed (the run's `--max-partition-skew` applies on resume; documented) |
| 18 | #761 | low | — | **open, for the lead**: an existing test pins it (see the finding) |
| 19 | #762 | medium | — | **open, for the lead**: outside the area (CLI, `io/targets.py`), and the tests may not be changed |
| 20 | #756 | medium | — | **open, for the lead**: outside the area |

Improvements (behaviour-preserving): tests for `TableEventSink`'s threaded writer
(`tests/streaming/emit/test_threaded_table_writer.py`, `emit/tables.py` 58% -> 97%; 5a8a131),
a dead statement removed from `FullEvidenceEngine.process` (c1c7a17).

## Notes for the lead

* `benchmarks/vs_refengine/stream_1to1/verify.py` could not be run here: `$REFENGINE_ROOT` is not
  checked out in this container. The only change to emitted bytes is #156 (the anomaly mutator),
  and only for rows that hit the defect (a null or an integer above 2**53 in a column the row's
  anomaly did not target, or an outlier past the int64 limit); the probe compares runs with and
  without `--anomaly-fraction` and two runs with it, which stay deterministic. Please run it at
  integration.
* #153 adds an optional key, `"partial": true`, to the lines of `--windows` that Ctrl-C wrote; an
  uninterrupted run's file is unchanged. Documented in `docs/plugins/streaming.md`.
* `src/shape/streaming/windows.py`, `aggregate_windows.py`, `keyed.py`, `vectorized.py` (and other
  files of the package) are stored with CRLF line endings; the fixes keep them.
* No `.github/workflows` change is needed.

## Commands and results

### Second session (this update)

Branch head: origin/int/INT-18 merged twice with merge commits (74f1b57, then 6494a10 = INT-18 at
004762f). The venv follows plan section 1.1 with `.[dev,streaming,advanced]` and `plugins/shape-domains`,
plus `tests/demo/fabric/requirements.txt` without `fabric-user-data-functions`, which needs a pyarrow
older than the 25.0.1 pin. `pydantic` was added so that mypy can check
`src/shape/importers/pydantic_models.py`. It is not declared in `pyproject.toml`, so a mypy run
without it reports one `import-not-found` error (outside this lane; for the lead).

* `python scripts/check_user_facing.py`: clean, exit 0.
* `ruff check src tests plugins benchmarks/vs_refengine`: all checks passed.
* `ruff format --check src tests plugins benchmarks/vs_refengine`: 1713 files already formatted.
* `mypy`: no issues in 635 source files (with pydantic installed).
* Streaming tests with coverage (`tests/streaming tests/test_streaming_async.py
  --cov=shape.streaming`), after the second merge: 623 passed, 1 skipped, 3 failed, 93% line
  coverage. The 3 failures are #762 and fail the same way on INT-18 itself.
* Full `pytest -m "not emulator and not live"`, run in two halves (`tests/` entries 1-40 and 41-74)
  with `--ignore=tests/demo/fabric/test_udf.py` (`fabric` package not installable here, see above)
  and `--deselect tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows`
  (more than an hour under the python kernel, see the first session):
  * `SHAPE_KERNEL=rust`: 7690 + 6326 passed, 24 skipped, 139 failed, 11 errors.
  * `SHAPE_KERNEL=python`: 7690 + 6326 passed, 24 skipped, 139 failed, 11 errors. These are the
    same 150 tests as under rust.
  * All 150 fail the same way on the merged INT-18 commit 004762f, checked in a worktree with the
    same kernel build. Of those 150, 3 are in this area (#762); the rest are outside it, and #756
    is one of them. The rest come from INT-18 itself: the `shape-result` `--json` envelope against
    older expectations, `multivariate_outlier_rate_change` unknown to the drift engine,
    `shape.history`'s `--coarse` needing sketch state, the `shape.behaviors` scaffold group, and
    missing optional packages (`shape_dbt`, `fabric`, `sqllocks_refengine`, the shape-fabric
    plugin). None come from this lane's changes.
* Not run: `benchmarks/vs_refengine/stream_1to1/verify.py`, because `$REFENGINE_ROOT` is not checked
  out. This session changed no emitted bytes: #753 changes window classification after a resume,
  and #754 changes a CLI option on resume.

### Final merge

origin/int/INT-18 was merged a third time, with a merge commit and no conflicts. It renames
`shape.history` to `shape.versions`, which resolves #756, and updates the two CLI tests of #762.
Run in a clean worktree of the final head (`tests/streaming tests/test_streaming_async.py
tests/test_removed_modules.py`): **654 passed, 1 skipped** under `SHAPE_KERNEL=rust` and under
`SHAPE_KERNEL=python`. #756 and #762 can be closed once the lead confirms. The full suite and
ruff/mypy were not re-run after this last merge: those commands were blocked by the session's
permission policy (the results above are for 6494a10). The lead should re-run `make check` at
integration.

### First session
Run on the merged branch (origin/build/main-plan merged at 5618be3), venv per plan §1.1 with
`.[dev,streaming,advanced]`, `plugins/shape-domains` and `tests/demo/fabric/requirements.txt`.

* `python scripts/check_user_facing.py`: clean, exit 0.
* `ruff check src tests plugins benchmarks/vs_refengine`: all checks passed.
* `ruff format --check src tests plugins benchmarks/vs_refengine`: 1089 files already formatted.
* `mypy`: no issues in 436 source files.
* Streaming tests (`tests/streaming tests/test_streaming_async.py`), coverage baseline before the
  fixes: 373 passed, 1 skipped, 92%; each fix's regression test and its module's tests pass after it.
* `SHAPE_KERNEL=rust pytest -m "not emulator and not live"`: 7105 passed, 2 skipped, **6 failed**.
* `SHAPE_KERNEL=python pytest -m "not emulator and not live"`, in two halves (one run of the whole
  suite exceeded the session's 2-hour limit for a background command): 4310 + 2795 passed, 2
  skipped, **5 failed**, 1 deselected.

None of the failures are in this lane's area, and none come from its changes:

* 5 fail the same way on base `origin/build/main-plan` (checked in a worktree of it):
  `tests/demo_cmd/test_notebook_and_outputs.py::test_the_semantic_model_is_a_bim_of_the_learned_schema`
  and `::test_all_writes_the_page_and_the_model` (they need the `shape-fabric` plugin, not
  installed here), `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date`,
  `tests/kernel/test_hashing.py::test_rust_equals_reference_on_a_million_values[float16]` and
  `::test_one_and_one_point_zero_hash_equal` (installing the Fabric test requirements brought
  pyarrow down to 19.0.1; CI runs those requirements in a separate job).
* `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`
  (rust run only) depends on test order (issue #77); it passes on its own (41 passed).
* Deselected in the python run: `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows`
  (profile engine, outside this area, untouched by the lane). Under the python kernel it ran more
  than an hour without finishing in two full-suite runs. It passes under the rust kernel.
* Not run: `benchmarks/vs_refengine/stream_1to1/verify.py`, because the pinned checkout is absent (see the notes above).
