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

Improvements (behaviour-preserving): tests for `TableEventSink`'s threaded writer
(`tests/streaming/emit/test_threaded_table_writer.py`, `emit/tables.py` 58% -> 97%; 5a8a131),
a dead statement removed from `FullEvidenceEngine.process` (c1c7a17).

## Notes for the lead

* `benchmarks/vs_spindle/stream_1to1/verify.py` could not be run here: `$SPINDLE_ROOT` is not
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
