# Emitting events: `shape emit`

`shape emit` turns a domain (or a generation schema file) into a stream of events: the same rows
`shape generate` writes (same schema, seed and scale), one JSON object per line, in a
deterministic order. The runtime is `shape.streaming.emit`; sinks for Kafka, Event Hubs and
Fabric come from emitter plugins (`shape.emitters`).

```bash
shape emit retail --scale small --max-events 5                       # events on standard output
shape emit retail --realtime --rate 10000 --duration 60 --sink file -o events.jsonl
shape emit retail --realtime --rate 500 --burst 30:10:4 --out-of-order 0.05 \
      --anomaly-fraction 0.01 --sink file -o events.jsonl
```

## `shape stream`: one table in event-time order

`shape stream` is `shape emit` for **one table, earliest event first**. It is the same command
underneath (`shape.cli.emit`, one runtime): the same options, sinks, envelopes, delivery guarantees,
checkpoint and live fidelity; `shape stream` differs only in the shape of the stream.

| | `shape emit` | `shape stream` |
|---|---|---|
| tables | every table of the schema, one after another in dependency order (`--table`, repeatable, selects) | exactly one: `--table NAME` is required |
| order | row order within a table | **event-time order**: the table is generated whole and stably sorted by `_shape_event_time` (a null time first; equal times in row order); a table with no date or timestamp column stays in row order |
| `--max-events N` | the first N events of the sequence (the first tables' rows) | the **N earliest events** |
| `--out-of-order` | window positions in row order | window positions in the time-ordered sequence |
| memory | bounded by a block (except a table a post-pass changes) | the whole table, as events |
| `--rate` default | 100 | 10 |
| short flags | none | `-t` (`--table`), `-s` (`--scale`), `-m` (`--mode`) |
| delivery batch (not paced) | 1,000 events | 32,768 events |

```bash
shape stream retail --table order --scale medium --no-realtime --sink file -o orders.jsonl
shape stream retail -t order -s small --max-events 1000          # the 1,000 earliest orders
shape stream retail -t order --realtime --rate 500 --burst 30:10:4 --sink file -o orders.jsonl
```

`_shape_seq` is still the row's position in the table (so the idempotency key is the same as in
`shape emit`), which makes the sequence of `_shape_seq` values of a time-ordered stream a permutation
of the table's rows. A checkpoint of a `shape stream` run is not accepted by `shape emit` or the
other way round (the order is part of the stream's identity).

The events are written by an encoder that builds the JSON text of a batch column by column with
Arrow kernels (on several threads for a batch of 16,384 events or more); its output is
byte-identical to the row-by-row encoder it replaces (`tests/streaming/emit/test_stream.py`).

## Events

A flat event (the default) is the row's columns plus:

| field | meaning |
|---|---|
| `_shape_table` | the table the row belongs to |
| `_shape_seq` | the row's position in its table, from 0 |
| `_shape_event_time` | the table's first date or timestamp column, when it has one |

**The idempotency key is `(_shape_table, _shape_seq)`.** A replay produces the same key for the
same row. `--envelope cloudevents` wraps each flat event as a CloudEvents 1.0 structured JSON event
(`id` is `<table>/<seq>`, `type` is `shape.<table>.row`, the flat event is the `data`).

JSON values: dates and timestamps are ISO-8601 strings, decimals are strings, binary is base64,
non-finite floats are `null`.

Tables are streamed one after another in dependency order; `--table NAME` (repeatable) selects.
A table that a post-pass changes (computed columns, rule repair, correlated columns) is generated
whole once, so its rows equal `shape generate`'s; the others are read chunk by chunk.

## Rate

* `--no-realtime` (the default): as fast as the sink takes events.
* `--realtime --rate N`: N events per second (default 100). Each batch is sent at its absolute due
  time, so the long-run rate equals N whatever the sleep overshoot. A sink that is too slow makes
  the run fall behind its schedule; no event is dropped, and the report shows the worst lag.
  Housekeeping stays off the pacing path: a realtime run freezes the objects the process already
  holds (so a garbage collection cannot stall the pacing for the cost of a large host heap) and
  writes its checkpoints on a writer thread (so a slow `fsync` cannot delay a batch). The writer
  only persists offsets that were delivered and flushed; the final checkpoint is synchronous.
* `--burst START:DURATION:MULT` (repeatable, needs `--realtime`): from START seconds for DURATION
  seconds the rate is MULT times `--rate`. Bursts may not overlap.
* `--speed 60x` (a virtual clock, instead of `--realtime`): pace by the events' **event time**, 60
  times faster than the clock, so a day of events replays in 24 minutes. An event stamped `t`
  seconds after the first is due `t / 60` seconds after the start; an event that is earlier than
  one already sent (`--out-of-order`) goes at once. It needs events with a date or timestamp
  column, so it is meant for `shape stream`, which delivers one table in time order. The report
  gives `virtual_span`, the seconds of event time replayed.
* `--max-rate N`: a hard cap, never more than N events per second from the start of the run,
  whatever `--rate`, `--burst`, `--speed` or the sink would otherwise do.
* `--max-events N`: stop when N events have been delivered *in all* (a position in the stream, so a
  resumed run stops at the same place). `--duration S`: stop after S seconds of this run.

## Out of order and anomalies

* `--out-of-order F`: each event, with probability F, is delivered late: it moves 1 to
  `--ooo-window` (default 1000) places later, inside its window. Its event time is unchanged.
* `--anomaly-fraction F`: each event, with probability F, has its values mutated through the
  `shape.chaos` mutator protocol (`--anomaly-mutator NAME`, repeatable; the default is
  `value-anomaly`, which turns one non-key value of the row into an outlier or a null). The same
  rows are chosen on every run. A mutator used here must keep the row count and schema, because
  `_shape_seq` is the row position.

Both are row-addressed draws from the seed, so the stream is the same on every run and after a
restart.

## Duplicates, poison messages and the answer key

Delivery systems repeat and corrupt messages; a pipeline has to survive both, and a detector has
to be scored against what was really injected.

* `--duplicate-fraction F` (`--duplicate-window N`, default 1000): each event, with probability F,
  is delivered a second time 1 to N events later. A consumer that keeps the first event of each
  `(_shape_table, _shape_seq)` key still sees the original stream.
* `--poison-fraction F`: each event, with probability F, is delivered cut off, so it is not valid
  JSON (its key is intact). It needs a sink that sends the JSON text: a file, standard output,
  Kafka or Event Hubs; a table sink stores typed values and refuses it.
* `--answer-key FILE`: every injected fault as JSON lines, one record per event: `kind` (`late`,
  `anomaly`, `duplicate`, `poison`), `table`, `seq`, `key` (`table/seq`) and details (`moved_up_to`
  for a late event, `mutators` for an anomaly, `delivered_after_events` for a duplicate).
  `shape.streaming.emit.read_answer_key(path)` returns each `(kind, table, seq)` once (a resumed run
  writes the events after its checkpoint again). The choice of events depends on the seed and the
  event's key only, so the answer key is the same on every run.

`--synthetic-header` (on by default; `--no-synthetic-header` turns it off) marks every message of
a transport that has headers as synthetic: Kafka header `shape-synthetic: true`, Event Hubs and
Eventstream property `shape_synthetic`; the Parquet files of a table sink carry the file metadata
`shape_synthetic`. The event body is not changed (D-12).

## Delivery, backpressure and checkpoints

Delivery is **at-least-once**. A batch is delivered when the sink's `send` returns, and only then
can the checkpoint move past it. A bounded queue sits between the generator and the sink, so a
slow sink blocks the generator (`--queue-batches`); transient `OSError`s are retried
(`--retries`).

A checkpoint (`--checkpoint FILE`; for `--sink file` the default is `<output>.checkpoint`) is an
atomic JSON document with the offset, a fingerprint of everything that decides the sequence, and
whether the run completed. It is written every `--checkpoint-every` events and every
`--checkpoint-seconds`, and always on shutdown: the end of the data, a limit, SIGINT or SIGTERM,
or an error. After a crash (even `kill -9`), run the same command again:

* it resumes at the checkpoint's offset, so events after the checkpoint may be sent a second time;
* a file sink continues the file (a half-written last line is cut first);
* **a consumer that keeps the first event of each key sees exactly the uninterrupted stream**
  (`shape.streaming.emit.read_events(path, dedupe=True)`);
* a checkpoint of a different stream (other schema, seed, scale or option) is refused;
  `--fresh` starts over, and a finished run is not repeated.

A run with no checkpoint (the console sink, or no `--checkpoint`) starts from the beginning.

## Several destinations, and tables as destinations

`--to URI` is repeatable, and `--sink` counts as one: `shape emit retail --to kafka://... --to
abfss://...` sends every batch to every destination. A destination that fails is retried alone, so
the others are not sent the batch again. The URI of a **sink** (`abfss://`, `delta+abfss://`,
`mssql://`, `postgresql://`, `mysql://`, `snowflake://`, `databricks://`, `synapse://`,
`duckdb://`; see `docs/SINKS.md`) lands the stream in files, Delta
tables or database tables **that a reader can query while the stream runs**:

* files roll to a new numbered file every `--roll-rows N` rows or `--roll-seconds S` seconds, and at
  every checkpoint; each file is written under a temporary name and renamed when complete, so a
  reader never sees a partial file; `--format` (`parquet` default, `csv`, `tsv`, `jsonl`),
  `--path-template` and `--batch-date` set the layout (`{table}/ingest_date={date}/...`);
* Delta commits at every checkpoint (and every `--commit-rows`);
* databases commit every batch (or every `--commit-rows`);
* `--write-mode upsert` (`mssql://`, `duckdb://`) merges each batch on the schema's primary key,
  so a row with the same key is written once and a rerun after a failure leaves no duplicates;
  `--sql-constraints disable` and `--auth kerberos` apply to `mssql://`, and an identity column of
  the schema keeps its generated values (`docs/SINKS.md`).

A table gets the event's columns less `_shape_table`, so `_shape_seq` and `_shape_event_time` are
columns, and delivery stays at-least-once: a resumed run appends (it never replaces files), and a
consumer removes repeats on `_shape_seq`. Set `--checkpoint-every` to how often readers should see
new rows. Secrets are never command-line values: see `docs/SINKS.md`.

## Emitters

`--sink` takes `console` (the default), `file` (with `-o`) or the URI of an emitter, a
`shape.emitters` plugin. `shape emit` hands the emitter each batch and counts it delivered when
`emit` returns, so an emitter returns only after its destination acknowledged the batch.

| URI | where it comes from | what it does |
|---|---|---|
| `console://` | core | JSON lines on standard output |
| `file:///path.jsonl` | core | JSON lines in one file (appended to after a resume) |
| `jsonl:///dir` | core | JSON lines, one `<table>.jsonl` per table |
| `kafka://host:9092/topic` | `sqllocks-shape-kafka` | one message per event, **message key = `<table>/<seq>`**, header `shape-table`; `acks=all`, idempotent producer |
| `eventhubs://namespace/hub` | `sqllocks-shape-eventhubs` | one message per event, property `shape_key` = `<table>/<seq>`; one table per service batch, partition key = table |
| `eventstream://name[/entity]` | `sqllocks-shape-fabric` | a Fabric Eventstream custom endpoint (Event Hubs protocol; connection string in `FABRIC_EVENTSTREAM_CONNECTION_STRING`-style option or `SHAPE_EVENTSTREAM_CONNECTION_STRING`) |
| `eventhouse://query-host/database[/table]` | `sqllocks-shape-fabric` | a KQL database by streaming ingestion; each Shape table becomes a KQL table (created from the schema) unless `table` is given |

What every emitter owes the runtime (and what `shape.streaming.emit.contract` tests for each):

* **Idempotency key.** Every message carries `<table>/<seq>` where the transport has a place for
  it (Kafka key, Event Hubs property, CloudEvents `id`, the `_shape_table`/`_shape_seq` columns of
  a KQL table), the same for the same row on every run.
* **At-least-once.** `emit` returns after acknowledgement. A transient failure raises
  `OSError`/`ConnectionError`/`TimeoutError` and the runtime retries the batch (`--retries`), so
  a destination may see a message twice and never zero times. Kafka, Event Hubs and Eventstream
  do not deduplicate; **Eventhouse does not either**: read it with
  `shape_fabric.eventhouse.dedupe_query(table)` (`summarize take_any(*) by _shape_table,
  _shape_seq`).
* **Backpressure.** A full producer queue (Kafka), a throttled service (Event Hubs
  `server-busy`, Eventhouse 429/503) is waited for inside `emit`; the bounded queue then blocks the
  generator. Nothing is dropped. A service that stays busy past `busy_retries` is a retryable
  failure.
* **Checkpoint.** The checkpoint moves only past events whose `emit` returned. After a crash the
  run resumes from the checkpoint; the events after it are sent again.

Options of an emitter (a Kafka `config`, a connection string, a token) are keyword options of its
`emit`; see each plugin's README. Sign-in material belongs in environment variables or an options
file, never in the URI.

## Live fidelity

`shape emit --live-target TARGET` scores the events against a target *while they are delivered*,
and raises an alert when the score drifts. The score is the one `shape fidelity` gives
(`docs/FIDELITY.md`): at any moment the live score is `shape fidelity` of the target against the
events delivered so far, computed without keeping the events.

```bash
shape emit retail --scale medium --sink file -o events.jsonl \
      --live-target retail --live-alerts alerts.jsonl --live-report live.json
shape emit retail --scale medium --anomaly-fraction 0.05 --live-target retail --live-fail --sink file -o e.jsonl
```

What it is made of: a tee (`shape.streaming.emit.live.TeeSink`) sits in front of the sink. An event
reaches the tee only after the sink's `send` returned, so a batch the runtime retries is counted
once. A worker thread feeds each table's events to the stream profiler (`shape.streaming.runtime`,
bounded mode: the same profiler as `shape stream-profile`) and to the score accumulators, and
compares them with the target. Nothing the live side does can fail or slow a delivery beyond the
overhead below: a failure inside it switches it off and raises a `live-error` alert.

**The target** (`--live-target`) is reference data: a file or a directory of one file per table
(Parquet, CSV or JSONL, read as `shape fidelity` reads them), or a domain or schema file, from which
the reference is generated (`--live-target-seed`, default the stream's seed plus 1;
`--live-target-scale`, default the stream's). Only the tables the stream emits are compared. A
`.shape` profile is not a target: the score needs the reference's distributions (the
Kolmogorov-Smirnov statistic, value overlap), which a profile does not hold.

**Exact and bounded.** Per column the live side keeps running moments (mean, spread, null rate:
exact), the values for the Kolmogorov-Smirnov statistic (all of them up to `--live-sample`, 100,000
per numeric column; then a uniform reservoir sample), the distinct values (exact up to
`--live-key-cap`, 250,000; then the profiler's HyperLogLog, about 0.8% error) and the value counts of
categorical columns (exact up to `--live-key-cap` distinct values; then the profiler's top-500
table, which can move the overlap and chi-squared points of a column with more categories than
that). Columns that used a bound are listed under `approximate` in the report. Text whose every
value is a number, or 95% of whose values are ISO dates, is scored as numbers or dates, as the
comparator does. A live run that resumed from a checkpoint scores only the events *it* delivered.

### Alerts

Alerts go to standard error as one line each, to the `--live-alerts FILE` as JSON lines (appended),
and are in the report. An alert is raised when its condition becomes true and a `recovered` alert
when it stops being true; a condition that stays true is not repeated.

```json
{"format":"shape-live-alert-v1","kind":"score-low","level":"error","table":"order_line","column":null,
 "score":58.54,"threshold":70.0,"events":14400,"time":"2026-10-02T10:18:22+00:00",
 "message":"table order_line: live score 58.54 < 70 after 14,400 events"}
```

| kind | level | raised when |
|---|---|---|
| `score-low` | error | a table's live score is below `--live-min-table-score` (70), or the overall score is below `--live-min-score` (85); the overall is judged once every table is |
| `score-drop` | warning | a table's (or the overall) score is `--live-drop` points (5) or more below the best it had reached |
| `column-low` | warning | a column's score is below `--live-min-column-score` (off unless given) |
| `live-error` | error | the live side failed and was switched off (the stream goes on) |
| `recovered` | info | an earlier alert's condition no longer holds (`table` and `column` name it) |

The defaults are the `shape fidelity` pass marks. The alerts judge a table only once
`--live-min-events` (1,000) of its events were seen (or all of a smaller table) and
`--live-min-progress` (0.5) of the target table's rows: a table that is half emitted scores lower
than the finished one (distinct counts grow with the rows), and that is not drift. The score in the
report is never gated. Scores are computed every `--live-interval` events (50,000) and at least
`--live-interval-seconds` (2) apart, and once more at the end.

Exit codes: `shape emit` exits **0** when the run completed, whatever alerts were raised; with
`--live-fail` it exits **1** when the run ends with a live pass mark missed (the final overall or
a table below its mark, over the tables that were emitted) or an error alert other than `score-low`
still active (`live-error`); **2** is bad input (an unknown target, `--live-profile` with
`--no-live-profile`, a threshold out of range), as everywhere else in `shape emit`.

Outputs: `--live-report FILE` (`.json`: the live summary with the alerts and the score trajectory
and the fidelity report; `.md` or `.html`: the fidelity report), `--live-profile FILE` (the stream
profiler's bounded profile of each table, JSON). `--json` adds a `live` object to the run report.

### Overhead

The tee costs CPU on the thread that feeds it and on the stream profiler. `--no-live-profile` leaves
the profiler out. The measured events per second with and without the tee are in
`docs/plans/evidence/P5-03/live_fidelity.json` (`benchmarks/live_fidelity/run.py`, section
`overhead`); the numbers are summarised in `docs/plans/lane_status/P5-03.md`.

## Limits

* The first block of a table with post-passes waits for the whole schema to generate.
* Event order across tables is fixed (table by table); there is no interleaving.
* Rate accuracy depends on the sink keeping up; `max_lag` in the report says whether it did.
* Live fidelity compares what *this run* delivered with the target, and the target must be data (see above).
