# Emitting events: `shape emit`

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


`shape emit` turns a domain (or a generation schema file) into a stream of events: the same rows
`shape generate` writes (same schema, seed and scale), one JSON object per line, in a
deterministic order. The runtime is `shape.streaming.emit`; sinks for Kafka, Event Hubs and
Fabric come from emitter plugins (`shape.emitters`).

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

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

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

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
* `--arrivals constant|poisson` (default `constant`: events evenly spaced, as always; `poisson`
  needs `--realtime`): `poisson` draws exponential gaps with mean `1/rate`, so arrivals are random
  the way independent clients are. The draw for event position *p* is a function of the seed and
  *p* alone, so the schedule is identical on every run, and a run resumed at offset *k* sees the
  gaps the uninterrupted run had from *k* on. It follows `--burst` (and the ramps and curves
  below): in a burst of MULT the mean gap is `1/(rate x MULT)`. `--max-rate` still caps it. Over
  100,000 scheduled events the realised mean rate is within 1% of `--rate` and the gaps' coefficient
  of variation is within 0.02 of 1 (tested with a fixed seed). The events themselves do not change,
  only when they are sent.
* `--ramp START:DURATION:FROM:TO` (repeatable, needs `--realtime`): from START seconds for DURATION
  seconds the rate multiplier moves linearly from FROM to TO. Before the first ramp the multiplier
  is 1; **after a ramp it holds that ramp's TO** until the next ramp begins, so two ramps with a gap
  between them make a plateau (`--ramp 0:60:0.1:1` is a one-minute warm-up; `--ramp 60:30:1:5 --ramp
  120:30:5:1` goes up, holds 5 and comes down). Ramps may not overlap each other (exit 2, `ramps may
  not overlap`; touching is fine), may overlap bursts (the multipliers multiply), and a ramp that ends
  at 0 with no later ramp is refused (the rest of the events would never be due).
* `--daily-curve NAME|FILE`: a 24-hour multiplier. Built-in `flat` (1 all day) and `business-hours`
  (0.15 overnight, rising linearly from 07:00 to 1.0 at 09:00, 1.0 until 17:00, falling to 0.3 at
  19:00 and to 0.15 at 22:00), or a JSON file
  `{"format": "shape-rate-curve", "version": 1, "points": [["00:00", 0.2], ["09:00", 1.0], ["18:00", 0.4]]}`
  (times `HH:MM` or `HH:MM:SS`, multipliers of 0 or more, at least one above 0; unknown keys and a
  newer `version` are refused). The curve is interpolated linearly and **wraps at midnight**: the
  last point continues to the first of the next day (a single point is a constant). It follows the
  **wall clock** with `--realtime` (the multiplier at the run's start is the one for the local time
  of day when the run begins) and the **event time** with `--speed` (a replay runs `multiplier`
  times faster at an event's UTC time of day, so busy hours deliver more events per second of wall
  time; every multiplier must then be above 0). Multipliers multiply `--rate`; a burst multiplies on
  top. The schedule stays absolute-time: the rate is a function of the seconds since the start, so
  the events delivered over any window equal the integral of the rate over it, within one event
  (tested over windows spanning days, with ramps, bursts and a curve together).
* `--speed 60x` (a virtual clock, instead of `--realtime`): pace by the events' **event time**, 60
  times the clock rate, so a day of events replays in 24 minutes. An event stamped `t`
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
  adds to the file and writes the events after its checkpoint again). The key names only events
  that were delivered: a run stopped by `--max-events`, `--duration` or an interrupt does not list
  the faults of the events it had generated but not sent. The choice of events depends on the seed and the
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
| `kafka://host:9092/topic` | `sqllocks-shape-kafka` | one message per event, **message key = `<table>/<seq>`**, header `shape-table`; `acks=all`, idempotent producer; `--event-format avro\|protobuf\|json-schema` through a schema registry |
| `eventhubs://namespace/hub` | `sqllocks-shape-eventhubs` | one message per event, property `shape_key` = `<table>/<seq>`; one table per service batch, partition key = table |
| `eventstream://name[/entity]` | `sqllocks-shape-fabric[eventhubs]` | a Fabric Eventstream custom endpoint (Event Hubs protocol; connection string in `FABRIC_EVENTSTREAM_CONNECTION_STRING`-style option or `SHAPE_EVENTSTREAM_CONNECTION_STRING`) |
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

## Schema registry formats (`--event-format`)

`--event-format json|avro|protobuf|json-schema` (default `json`: the flat event's JSON, byte for
byte what it always was) chooses the message format of a `kafka://` target. For the other three
the Kafka emitter derives a schema from the table's Arrow schema, registers it with a
Confluent-compatible registry, and sends the Confluent wire format: byte `0`, the 4-byte
big-endian schema id, then the payload (Protobuf adds the message-index list `[0]`, one zero byte,
as Confluent's serializers do). The message key stays `<table>/<seq>` (D-12) and the headers are
unchanged. Only `kafka://` takes a registry format: any other target refuses it (exit 2).

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

| `--sink-config` key | meaning |
|---|---|
| `kafka.schema_registry_url` | the registry's `http(s)` URL (required; it may not carry credentials) |
| `kafka.subject_strategy` | `topic` (default; subject `<topic>-value`), `record` (the record's full name, `shape.events.<table>`) or `topic_record` (`<topic>-shape.events.<table>`) |
| `kafka.schema_registry_username`, `kafka.schema_registry_password` | basic authentication; the password is a credential reference (`env://NAME`, `file://PATH`, `kv://VAULT/NAME`), never a literal |

* One schema per table, registered on the table's first batch of the run; a second run registers
  it again and gets the same id. Tables of different shape on one topic need `record` or
  `topic_record`: under `topic` they share one subject, and the registry's compatibility rule
  decides (it usually refuses the second).
* A registry that refuses the schema stops the run with exit 2 and `schema registry refused the
  schema for subject <subject>: <registry message>`. A registry that cannot be reached, or answers
  5xx, is a retryable delivery failure (`--retries`); nothing is sent without a schema id.
* `--envelope cloudevents` with a non-JSON format is refused (exit 2). A missing encoder extra
  stops with exit 2 and the `pip install` command that provides it. An event with a value the
  format cannot hold (a `uint64` above 2**63-1 in Avro, a table or column name that is not a valid
  Avro or Protobuf identifier) stops the run naming the event, or goes to `--dead-letter` when that is set.
* `--poison-fraction` cuts the payload of a chosen message short, as it does for JSON.
* The type mapping (decimal, date, timestamp with and without zone, uuid, binary, nullable) is in
  the `sqllocks-shape-kafka` README. A nullable column is a nullable field; a float column is
  always nullable because a non-finite float is sent as `null` in every format.

## Dead letters (`--dead-letter`)

An event the destination refuses for good no longer has to stop the run. `--dead-letter URI` takes
any URI that `--sink` or `--to` takes (`file:///dlq.jsonl`, `kafka://host:9092/orders.dlq`, a
table target) and sends such an event there instead. An event goes to it when

* the destination rejects it with a **non-retryable per-message error**: an emitter delivers every
  other event of the batch, then raises `shape.streaming.emit.RejectedEvents` with the keys and
  reasons (`Rejection(key, reason, body=None)`; it is not an `OSError`, so the runtime never retries
  it). The Kafka emitter does this for a message that is too large or an invalid record
  (`MSG_SIZE_TOO_LARGE`, `INVALID_MSG`, ...); errors about the cluster or topic (authorisation,
  brokers down) and retryable errors keep stopping the run after `--retries`;
* it **cannot be encoded** in the chosen `--event-format` (a value that does not fit the format).

Without `--dead-letter`, a rejection stops the run with exit 2 and
`the destination rejected N events (first KEY: REASON); use --dead-letter URI ...`; every other
behaviour is unchanged. `--dead-letter` may not name the same URI as `--sink` or `--to`.

**The record** (`format: "shape-dead-letter"`, `version: 1`), one JSON object per line:

| field | meaning |
|---|---|
| `format`, `version` | `"shape-dead-letter"` and `1` (a newer version is refused by `read_dead_letters`) |
| `key`, `table`, `seq` | the event's D-12 key `<table>/<seq>` and its parts |
| `reason` | why: the destination's error, or `cannot encode as avro: ...` |
| `destination` | the URI that refused the event, password redacted |
| `attempts` | deliveries of its batch up to the rejection |
| `at` | UTC time, ISO 8601 with microseconds and `Z` |
| `body` | the message the destination refused, as text; or base64 with `body_encoding: "base64"` when it is not UTF-8 (`body_encoding` is absent for text). When the event could not be encoded, the body is its flat JSON event |

Where the destination is an emitter, the record also holds `_shape_table` and `_shape_seq` (the same
values as `table` and `seq`), so a Kafka dead-letter message is keyed by the original event's key,
and a transport with headers carries the header `shape-dead-letter-reason` (Kafka). A table target
stores the records in the table `dead_letter`.

**Checkpoint.** A batch counts as delivered only after the dead-letter destination acknowledged its
records, so the checkpoint never moves past a dead-lettered event before that. A failed dead-letter
write is retried (`--retries`) without sending the batch to the primary destination again.
Delivery is at-least-once: after a crash an event can be dead-lettered twice; the key removes the
repeat.

**`--max-dead-letter N`** stops the run, after the batch in hand, once more than N events were
dead-lettered; exit 1, `stopped_by: "dead-letter-limit"`, a checkpoint at the end of that batch.
The run report (`--json`) gains `dead_lettered`: a count by reason (at most 50 distinct reasons, the
rest counted as `other`). `--max-dead-letter` needs `--dead-letter`.

An emitter's contract (`shape.streaming.emit.contract`) gains `check_rejections`, run for a harness
that offers `inject_rejections(n)`: the other events are delivered first, the error is not retried,
and delivered plus dead-lettered events are the stream, each once.

## Drift plans in a stream (`--drift-plan`)

`shape emit` and `shape stream` take the plan of `shape generate-drift` (`docs/DRIFT.md`) and plant
its drift in the stream, day by day:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

* **Day d is generated from `plan.schema_at(schema, d)` with seed + d**, exactly as `shape
  generate-drift` does, so for the same `--rows TABLE=N` the event values of day d equal that day's
  `generate-drift` tables (tested value for value). The days stream in order, and within a day the
  tables stream in dependency order. `--rows TABLE=N` (repeatable) sets the rows per day of a table,
  as in `generate-drift`; it is accepted only with `--drift-plan`.
* **`_shape_seq` continues across days**: a table's day-1 events follow its day-0 events, so the key
  `<table>/<seq>` is unique for the whole stream. **No event field is added**: a day's events are
  its columns plus the usual `_shape_*` fields; a column the plan adds appears from its start day, a
  column it drops is gone. `shape stream` (one table, event-time order) sorts each day by its event
  time. Faults (`--out-of-order`, `--anomaly-fraction`, duplicates, poison) are drawn by the global
  sequence number, so they are the same on every run.
* **`--day-seconds S`** (needs `--realtime` and `--drift-plan`) gives each day S seconds of wall
  time: day d starts S x d seconds after the start and its events are spread evenly over the S
  seconds, so the plan plays in `days x S` seconds. The day sets the pace, so it cannot be combined
  with `--rate`, `--burst`, `--ramp`, `--daily-curve` or `--arrivals` (`--max-rate` still caps it).
* **`--answer-key`** also writes `kind: "drift"` records: when the stream reaches a day, one record
  per plan event in effect that day, with `event` (the plan's event id), `table`, `column`, `day`
  (ISO date), `day_number` (0 is the plan's start), `effect` (0 to 1: how far the event has taken
  hold, following the plan's ramp) and `seq` (the first sequence number of the event's table that
  day; `key` is `table/seq`). A resumed run writes its day's records again; `read_answer_key`
  returns each `(kind, table, seq, event)` once.
* **The plan's SHA-256 is part of the checkpoint fingerprint**, so a checkpoint made with another
  plan is refused (`--fresh` starts over). The digest is of the plan file's bytes (`sha256sum
  plan.json`), so even a whitespace change counts as another plan.

A crashed and resumed run with `--arrivals poisson`, a daily curve and `--drift-plan` delivers,
after deduplication on the key, exactly the events of the uninterrupted run (tested). `shape
generate --drift-plan` (the Excel workbook README) is unchanged.

## Dry run (`--dry-run`)

`--dry-run` resolves everything a run would use and prints it, then exits without starting:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

It prints: the target and its schema (name, seed, scale), the tables with their row counts and the
event total; each destination (`--sink`, every `--to`, `--dead-letter`) with its kind, scheme, the
plugin that provides it and its URI with any password redacted; the event format and envelope; the
credential references in `--sink-config` (the option and the reference, **never the value**); the
checkpoint (`fresh`, `resume` at offset N, `finished`, or `refused` with the reason); the rate
schedule (mode, expected duration and peak events per minute); the drift plan (its SHA-256 and each
day's date, events and the plan events in effect); the limits; and the answer-key file.

* **It opens no connection, writes no file and sends no event.** No checkpoint, no answer key, no
  `--output` file, no dead-letter file is created; a destination plugin is looked up and checked
  (its scheme, `--event-format` support and `--sink-config` keys) but its `emit`, `flush` and
  `close` are never called (tested with fakes that fail on connect and on write, and by listing the
  directory before and after). A checkpoint file is read, never written.
* **Exit 0 when the run would start; exit 2, with the same message the real run would give, when it
  would be refused**: a bad flag combination, an unknown table, sink or plugin, a missing
  `--output`, overlapping ramps, a checkpoint of another stream (the plan is printed with
  `state: "refused"`, then the error), a drift plan that does not apply to the schema, a literal
  secret, a missing environment variable behind an `env://` reference. What a dry run cannot see is
  what only a connection shows (a broker that is down, a registry that refuses the schema).
  `env://` and `file://` references are resolved (that touches only this machine) so a missing one
  is refused as it would be at run time; a `kv://VAULT/NAME` reference would be a lookup in a
  secret store, which is a connection, so only its form is checked.
* **Rate schedule.** `expected_seconds` is how long the run takes by its schedule (`--rate`,
  `--burst`, `--ramp`, `--daily-curve`, `--day-seconds`, and `--max-rate` as a floor; for
  `--arrivals poisson` the mean). It is `null` when nothing paces the run (as fast as the sink
  takes events) or when `--speed` paces it by event time. `peak_events_per_minute` is the highest
  rate of the schedule times 60, capped by `--max-rate`. A `--daily-curve` starts at the local time
  of day of the dry run (`curve_origin`, seconds after midnight), as a real run starts at its own.

The plan is a persisted format: `format: "shape-emit-plan"`, `version: 1`. A reader refuses a newer
version. With `--json` it is printed inside the dry-run document every writing command prints
(`format: "shape-dry-run"`, `version: 1`, `command`, and `actions`: the files the run would write
and the destinations it would send to; `docs/CI.md`), under `plan`; the text form ends with the
same actions as `would ...` lines.

## Progress (`--progress`)

`--progress` / `--no-progress` (default: on when standard error is a terminal, off otherwise)
writes one line to standard error, rewritten in place at most once a second, and a final line at
exit:

```
emitted 120,400 / 1,000,000 events  9,980/s  lag 0.4s  retries 2  dead-lettered 0  eta 1m28s
```

`emitted` is the position in the stream (a resumed run starts from its checkpoint offset) out of
the events the run will deliver (`--max-events` included); `/s` is the rate since the last update;
`lag` is how far behind its schedule the latest batch was sent (0 when the run is not paced);
`retries` and `dead-lettered` are the run's counts; `eta` is the remaining events at the current
rate, and the line has no total and no ETA when they are not known. On a terminal the line is
rewritten with a carriage return; on a pipe (`--progress` forced) each update is its own line.
**Nothing is added to standard output**: `--sink console` output is byte-identical with and without
the progress line, and `--json` is unchanged.

## Live fidelity

`shape emit --live-target TARGET` scores the events against a target *while they are delivered*,
and raises an alert when the score drifts. The score is the one `shape fidelity` gives
(`docs/FIDELITY.md`): at any moment the live score is `shape fidelity` of the target against the
events delivered so far, computed without keeping the events.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

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

The tee profiles events on the feeding thread. The no-live-profile option disables that work.
[Owner: performance maintainer — publish only committed, machine-labelled overhead measurements.]

## Limits

* The first block of a table with post-passes waits for the whole schema to generate.
* Event order across tables is fixed (table by table); there is no interleaving.
* Rate accuracy depends on the sink keeping up; `max_lag` in the report says whether it did.
* Live fidelity compares what *this run* delivered with the target, and the target must be data (see above).
