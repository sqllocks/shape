# Simulating landing files and event streams

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


`sqllocks-shape-simulation` (`pip install 'sqllocks-shape[simulation]'`) turns generated tables into
what an upstream system would produce over time: files landing in dated folders, versioned
(SCD type 2) snapshots and deltas, a stream of events, both at once, and business-process events
from a state machine. Each simulator is a class in its own module, importable from
`shape_simulation`, and a sub-command of `shape simulate`.

| Simulator | Module | Command | What it produces |
|---|---|---|---|
| `FileDropSimulator` | `shape_simulation.file_drop` | `shape simulate file-drop` | partitioned files over a date range, manifests, `_done` flags, late arrivals, duplicates, a backfill, restatements, several files per partition |
| `SCD2FileDropSimulator` | `shape_simulation.scd2_file_drops` | `shape simulate scd2` | an initial snapshot and daily deltas of inserts and update pairs with `valid_from` / `valid_to` / `is_current` |
| `StreamEmitter` | `shape_simulation.stream_emit` | `shape simulate stream` | the tables as enveloped events, out of order, with replays, on the emit runtime |
| `HybridSimulator` | `shape_simulation.hybrid` | `shape simulate hybrid` | a file drop and a stream of the same tables, joined by one run id |
| `WorkflowSimulator` | `shape_simulation.state_machine` | `shape simulate workflow` | transition events of entities moving through a workflow, and a summary per entity |

All of them take **Arrow tables** (a `dict[str, pa.Table]`, a generation result, or pandas frames)
and are **deterministic**: the random draws are made in a fixed order, so a seed reproduces a run.
Wall-clock fields (`created_utc`, the event `time`, `_restated_at`) and run ids are the only values
that change between runs.

```python
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape_simulation import FileDropConfig, FileDropSimulator

tables = Engine(load_domain("retail").schema, scale="small", seed=42).generate().tables
cfg = FileDropConfig(domain="retail", base_path="landing", date_range_start="2024-01-01",
                     date_range_end="2024-03-31", entities=["order", "return"])
result = FileDropSimulator(tables, cfg).run()
```

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`shape simulate` exits 0 when it is done and 2 for input it cannot use (an unknown table, a bad
date, a sink it does not know).

## File drop

For each slot of the date range (a day, an hour or a quarter of an hour: `cadence`), the rows whose
time falls in the slot are written to `<base_path>/<domain>/<entity>/<partition>/`, named by
`file_naming` (`{domain}_{entity}_{dt}_{seq}.{ext}`), in each of `formats` (`parquet`, `csv`,
`jsonl`, written by the `shape.sinks` plugins). A `_manifest.json` (entity, domain, slot, cadence,
files, `file_count`, `created_utc`, `correlation_id`) and a `_done` flag are written per partition.

* **The time column** is the first timestamp column; otherwise the first column named like a date
  (`date`, `timestamp`, `created`, `updated`) that holds dates or ISO-8601 text. A number is never a
  date. A table without one is dealt out round robin. Rows outside the range are not written.
* **Late arrivals** (`lateness_probability`, up to `max_days_late` days): rows move to the partition
  of a later day, into one `..._00900` file per partition, whichever slots they came from, so no row
  is lost. The on-time partition's manifest does not list them (they arrived after it).
* **Duplicates** repeat rows of a slot. **Backfill** re-drops one of the first `max_days_back`
  partitions (`..._00990`). **Restatements** re-drop partitions (`..._00980`) with every number
  except `*_id` columns and the first column moved by up to `restatement_max_correction_pct`, marked
  by `_restatement` and `_restated_at`. A backfill or restatement of a slot holds the rows the slot was given, also for a
  table dealt out round robin.
* **Multi-file** (`multi_file_enabled`) splits a partition into `multi_file_chunks` files of nearly
  equal size; the manifest lists each file's SHA-256 and size.
* A setting that cannot work is refused when the configuration is made: an unknown cadence or format,
  `max_days_late` below 1; an end date before the start date is refused when the run starts.

## SCD2 drop

The snapshot is `<entity>/initial/<entity>_initial.<fmt>`; day `n` of `num_delta_days` is
`<entity>/delta/dt=<day>/<entity>_delta.<fmt>`. The table's own columns come first, then
`valid_from`, `valid_to` and `is_current` (names are settings), then `_delta_type` in deltas, in
the same order in every file.

* An **update** is a pair: the expired version (`valid_to` = the day, `is_current` false, with the
  `valid_from` it had) and the new current one (`valid_from` = the day). A tracked column
  (`scd2_columns`) changes: text gets a `_v<N>` suffix, a number is multiplied by a factor in
  [0.8, 1.2] (an integer stays an integer), a flag flips.
* An **insert** is a new entity shaped like the first entity's current row: text gets `_new<key>`,
  numbers are redrawn around the template's, the key is the next integer. It starts current.
* Applied over the snapshot (a later record of the same entity and `valid_from` replaces an earlier
  one), the versions of every entity chain without a gap and exactly one is current.
* The business key must be numeric (new entities get the next integer); a text key is refused before
  anything is written.
  So are a key that repeats or is null (each row is one entity's current version), tracking the key
  or a version column in `scd2_columns`, and a `daily_change_rate` or `daily_new_rate` outside
  [0, 1]. A rate above 0 changes at least one entity a day; a rate of 0 changes none.

## Stream emitter

The rows become events on the emit runtime (`docs/EMIT.md`): the runtime paces them (`realtime`,
`rate_per_sec`, `burst_windows`), delivers them to a sink with backpressure and retries, and the
sinks are the runtime's (`console`, `file`, any `shape.emitters` URI such as `kafka://`,
`eventhubs://`, `eventstream://`), or an `EventSink` you pass in. A sink type that is none of these
is an error, never a silent console.

An event is a CloudEvents 1.0 structured event:

```json
{"specversion":"1.0","id":"order/0","source":"shape","type":"shape.order","time":"2026-01-05T10:00:00.1Z",
 "datacontenttype":"application/json","topic":"order","schemaversion":"1.0","correlationid":"<run id>",
 "shapetable":"order","shapeseq":0,
 "data":{"order_id":1,"...":"...","_shape_table":"order","_shape_seq":0,"_shape_event_time":"2022-01-16T21:22:16"}}
```

`id` is `<table>/<row position>` (a replay repeats it), `time` is when the event was sent,
`correlationid` is one id for the run (`HybridSimulator` passes its run id), and `data` is the row
with the runtime's fields (D-12): `_shape_table`, `_shape_seq` and, when the table has a date or
timestamp column, `_shape_event_time`. `topics` names the topics (one per table, or one for all).

* `out_of_order_probability`: that share of the events swap places with their successor.
* `replay_enabled`: after an event, with `replay_probability`, the last `replay_burst_size` events
  (at most the window, `rate_per_sec * replay_window_minutes * 60`, at least 100) are sent again with
  `replay: true` and `replaytime`. The window starts empty at every `emit()`.
* `max_events` cuts the sequence after the reordering (replays are not counted); `jitter_ms` adds a
  random pause before each event of a realtime run.
* Resumable like any emit: the plan (`TablesEventPlan`) is a counted sequence that can start at any
  offset.

## Hybrid

The tables go to the file drop (`batch_tables`) and the stream (`stream_tables`); empty means all.
With `link_strategy="correlation_id"` the run id is the `_correlation_id` column of every row of
both sides, the `correlation_id` of every manifest and the `correlationid` of every event;
`natural_keys` stamps nothing. A table name that does not exist is an error. The two phases run in
turn, or in two threads (`concurrent`).

## Workflow

`get_preset_workflow` returns `(states, transitions)` of `order_fulfillment`, `support_ticket` or
`employee_onboarding`; you can build your own from `StateDefinition` and `TransitionRule`
(`probability` is a weight per source state; the dwell time is normal around `dwell_hours_mean`
with a floor). Each entity starts in an initial state and moves until it reaches a terminal state,
gets stuck or reaches `max_transitions_per_entity`. Anomalies (one roll per step: `stuck`, `skip` to
a state after the normal next one, `backward` to a visited non-terminal state) are flagged. The result is
`events` (`event_id`, `entity_id`, `from_state`, `to_state`, `transitioned_at`, `dwell_hours`,
`is_anomaly`, `anomaly_type`; sorted by entity and time), `entity_summary`, `state_distribution` and
`stats`. Event ids are UUIDs derived from the seed (from a stream of their own, so no other draw
moves): the same seed gives the same ids.

## Scenario packs

`shape pack run` (`docs/SCENARIO_PACKS.md`) writes a pack's landing once (one file per entity, one
JSON Lines file per topic): it does not simulate a clock. These simulators do. They share the pack
runner's writers (the `shape.sinks` plugins) and the emit runtime's sinks and encoders; nothing is
implemented twice. A pack's timing fields (lateness, duplicates, backfill) still describe the
landing for a consumer; to produce that behaviour, run the simulator with the same settings.
