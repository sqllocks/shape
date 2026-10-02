# STREAM-EMIT: `shape stream` against the baseline's stream command

The workload of plan section 3.4 (gate: at least 10x, T-19):

```bash
"$SPINDLE_VENV/bin/spindle" stream retail --table order --scale medium --no-realtime --sink file -o F --seed 42
"$SHAPE_VENV/bin/shape"     stream retail --table order --scale medium --no-realtime --sink file -o F --seed 1042
```

Both write one JSON object per event for the 500,000 rows of the `order` table in event-time
order (`--max-events N` keeps the first N).

| file | purpose |
|---|---|
| `stream_common.py` | the workload, the fixed seed set (T-21), `FIELD_MAP` (the baseline's event field names and the Shape names they stand for, D-13) and `ALLOWED` (named baseline defects) |
| `verify.py` | equivalence verifier (run it with the baseline venv's Python); exit 0 pass, 1 fail, 2 inputs missing |
| `bench.py` | timing: fresh processes, `--warmup` discarded runs, median of `--runs`, tools alternating; also the two command lines end to end |
| `baseline_worker.py`, `shape_worker.py` | one timed run in the tool's own venv (imports before the timed region) |

```bash
source scripts/env.sh
"$SPINDLE_PY" benchmarks/vs_spindle/stream_1to1/verify.py --scale medium --negative-control
"$SHAPE_VENV/bin/python" benchmarks/vs_spindle/stream_1to1/bench.py --scale medium --runs 5
python benchmarks/vs_spindle/run.py --only stream --full        # verify, bench, verify the timed output
```

## What the verifier compares

* The baseline's field names, mapped through `FIELD_MAP`, equal Shape's field names in the same
  order, in every event of both files. The map is the only place the two names meet; the event
  time is `_spindle_event_time` there and `_shape_event_time` in Shape.
* The event multiset per field under T-21 (b)-(e), against the baseline's own seed-to-seed spread
  (seeds 43-46): null rate, KS for numeric and datetime fields, TVD and vocabulary overlap for
  categorical fields. Values are compared as parsed values (the baseline writes a timestamp as
  `2022-01-16 21:22:16`, Shape as ISO-8601 `2022-01-16T21:22:16`).
* Event count, `seq` uniqueness and range, the event-time field equal to the first datetime
  column, events in non-decreasing event-time order, one JSON type class per field.

## Allow-list

| id | baseline defect (fixed in Shape) | probe |
|---|---|---|
| ST-1 | `--anomaly-fraction` is accepted and ignored: the output is the same with and without it | both tools at `--anomaly-fraction 0.2`: the baseline output is byte-identical to its plain output; Shape's differs in about 20% of events, identically on a second run |

Anything not on the list that differs fails the verifier; a probe that no longer shows its
defect fails it too.
