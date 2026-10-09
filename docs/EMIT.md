# Emitting events: `shape emit`

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" EMIT
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for EMIT
    ```


`shape emit` turns a domain (or a generation schema file) into a stream of events: the same rows
`shape generate` writes (same schema, seed and scale), one JSON object per line, in a
deterministic order. The runtime is `shape.streaming.emit`; sinks for Kafka, Event Hubs and
Fabric come from emitter plugins (`shape.emitters`).

[Run this example](#local-example-0).


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

[Run this example](#local-example-1).


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

<!-- example: 2 -->

**Needs a Kafka account. Not run in CI.**

```
pip install 'sqllocks-shape-kafka[avro]'        # or [protobuf]; json-schema needs no extra
shape emit retail --table order_line --sink kafka://broker:9092/orders \
    --event-format avro \
    --sink-config kafka.schema_registry_url=https://registry.example:8081 \
    --sink-config kafka.schema_registry_username=svc \
    --sink-config kafka.schema_registry_password=env://REGISTRY_PASSWORD
```

<!-- owner: Kafka maintainer — supply the transcript for docs/EMIT.md example 2. -->


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

<!-- example: 3 -->

**Needs a Kafka account. Not run in CI.**

```
shape emit retail --drift-plan plan.json --rows customer=500 --rows order=2000 \
    --sink kafka://broker:9092/orders --realtime --day-seconds 60 --answer-key key.jsonl
```

<!-- owner: Kafka maintainer — supply the transcript for docs/EMIT.md example 3. -->


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

[Run this example](#local-example-4).


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

[Run this example](#local-example-6).


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
<!-- owner: performance maintainer — publish only committed, machine-labelled overhead measurements. -->

## Limits

* The first block of a table with post-passes waits for the whole schema to generate.
* Event order across tables is fixed (table by table); there is no interleaving.
* Rate accuracy depends on the sink keeping up; `max_lag` in the report says whether it did.
* Live fidelity compares what *this run* delivered with the target, and the target must be data (see above).


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape emit retail --scale small --max-events 5                       # events on standard output
shape emit retail --realtime --rate 10000 --duration 60 --sink file -o events.jsonl --fresh
shape emit retail --realtime --rate 500 --burst 30:10:4 --out-of-order 0.05 \
      --anomaly-fraction 0.01 --sink file -o events.jsonl --fresh
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"customer_id":1,"first_name":"Flira","last_name":"Serrano","email":"flira.serrano389@example.org","gender":"M","loyalty_tier":"Platinum","signup_date":"2025-08-06T12:11:04.000352000","is_active":"true","_shape_table":"customer","_shape_seq":0,"_shape_event_time":"2025-08-06T12:11:04.000352000"}
    {"customer_id":2,"first_name":"Neella","last_name":"Hawkins","email":"neella.hawkins873@example.net","gender":"M","loyalty_tier":"Silver","signup_date":"2024-03-20T20:37:24.271604000","is_active":"true","_shape_table":"customer","_shape_seq":1,"_shape_event_time":"2024-03-20T20:37:24.271604000"}
    {"customer_id":3,"first_name":"Stella","last_name":"Landman","email":"stella.landman817@example.net","gender":"F","loyalty_tier":"Platinum","signup_date":"2022-06-23T18:57:53.535709000","is_active":"true","_shape_table":"customer","_shape_seq":2,"_shape_event_time":"2022-06-23T18:57:53.535709000"}
    {"customer_id":4,"first_name":"Panor","last_name":"Mosburg","email":"panor.mosburg215@example.org","gender":"F","loyalty_tier":"Basic","signup_date":"2022-08-07T11:14:20.229154000","is_active":"false","_shape_table":"customer","_shape_seq":3,"_shape_event_time":"2022-08-07T11:14:20.229154000"}
    {"customer_id":5,"first_name":"Leonard","last_name":"Allen","email":"leonard.allen379@example.org","gender":"M","loyalty_tier":"Basic","signup_date":"2023-12-06T14:01:26.714262000","is_active":"true","_shape_table":"customer","_shape_seq":4,"_shape_event_time":"2023-12-06T14:01:26.714262000"}
    shape emit: 5 events delivered, offset 5 of 21,750, max-events, 1,853 events/s
    shape emit: 21,750 events delivered, offset 21,750 of 21,750, complete, 10,016 events/s
    shape emit: 21,750 events delivered, offset 21,750 of 21,750, complete, 652 events/s
    ```

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape stream retail --table order --scale medium --no-realtime --sink file -o orders.jsonl --fresh
shape stream retail -t order -s small --max-events 1000          # the 1,000 earliest orders
shape stream retail -t order --realtime --rate 500 --burst 30:10:4 --sink file -o orders.jsonl --fresh
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape stream: 500,000 events delivered, offset 500,000 of 500,000, complete, 822,491 events/s
    {"order_id":1273,"customer_id":178,"store_id":5,"shipping_address_id":614,"promotion_id":153,"order_date":"2022-01-10T15:29:42.000000","status":"completed","order_total":34.4,"_shape_table":"order","_shape_seq":1272,"_shape_event_time":"2022-01-10T15:29:42.000000"}
    {"order_id":1600,"customer_id":241,"store_id":1,"shipping_address_id":295,"promotion_id":null,"order_date":"2022-01-31T16:55:24.959846","status":"returned","order_total":32.36,"_shape_table":"order","_shape_seq":1599,"_shape_event_time":"2022-01-31T16:55:24.959846"}
    {"order_id":3092,"customer_id":238,"store_id":1,"shipping_address_id":42,"promotion_id":123,"order_date":"2022-02-12T05:55:41.663880","status":"completed","order_total":34.3,"_shape_table":"order","_shape_seq":3091,"_shape_event_time":"2022-02-12T05:55:41.663880"}
    {"order_id":4611,"customer_id":713,"store_id":1,"shipping_address_id":1290,"promotion_id":null,"order_date":"2022-02-13T11:46:57.447772","status":"returned","order_total":64.04,"_shape_table":"order","_shape_seq":4610,"_shape_event_time":"2022-02-13T11:46:57.447772"}
    {"order_id":3812,"customer_id":99,"store_id":1,"shipping_address_id":null,"promotion_id":155,"order_date":"2022-02-17T18:08:51.840985","status":"returned","order_total":295.91,"_shape_table":"order","_shape_seq":3811,"_shape_event_time":"2022-02-17T18:08:51.840985"}
    {"order_id":4014,"customer_id":700,"store_id":1,"shipping_address_id":1388,"promotion_id":null,"order_date":"2022-03-01T05:08:00.256629","status":"completed","order_total":209.08,"_shape_table":"order","_shape_seq":4013,"_shape_event_time":"2022-03-01T05:08:00.256629"}
    {"order_id":4622,"customer_id":213,"store_id":14,"shipping_address_id":519,"promotion_id":null,"order_date":"2022-03-04T17:01:37.672710","status":"cancelled","order_total":27.39,"_shape_table":"order","_shape_seq":4621,"_shape_event_time":"2022-03-04T17:01:37.672710"}
    {"order_id":907,"customer_id":828,"store_id":23,"shipping_address_id":null,"promotion_id":30,"order_date":"2022-03-16T05:47:52.000000","status":"completed","order_total":16.68,"_shape_table":"order","_shape_seq":906,"_shape_event_time":"2022-03-16T05:47:52.000000"}
    {"order_id":4188,"customer_id":447,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-03-21T17:04:05.571605","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4187,"_shape_event_time":"2022-03-21T17:04:05.571605"}
    {"order_id":2673,"customer_id":178,"store_id":11,"shipping_address_id":614,"promotion_id":null,"order_date":"2022-03-24T14:26:07.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2672,"_shape_event_time":"2022-03-24T14:26:07.000000"}
    {"order_id":1337,"customer_id":135,"store_id":2,"shipping_address_id":870,"promotion_id":null,"order_date":"2022-04-02T09:32:46.000000","status":"completed","order_total":66.85,"_shape_table":"order","_shape_seq":1336,"_shape_event_time":"2022-04-02T09:32:46.000000"}
    {"order_id":1811,"customer_id":118,"store_id":2,"shipping_address_id":498,"promotion_id":null,"order_date":"2022-04-04T04:35:10.000000","status":"shipped","order_total":19.11,"_shape_table":"order","_shape_seq":1810,"_shape_event_time":"2022-04-04T04:35:10.000000"}
    {"order_id":3124,"customer_id":959,"store_id":18,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-04-05T09:58:19.222687","status":"completed","order_total":508.29,"_shape_table":"order","_shape_seq":3123,"_shape_event_time":"2022-04-05T09:58:19.222687"}
    {"order_id":4419,"customer_id":146,"store_id":15,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-04-12T12:06:50.823044","status":"completed","order_total":18.53,"_shape_table":"order","_shape_seq":4418,"_shape_event_time":"2022-04-12T12:06:50.823044"}
    {"order_id":3882,"customer_id":99,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-04-15T19:28:35.000000","status":"shipped","order_total":22.15,"_shape_table":"order","_shape_seq":3881,"_shape_event_time":"2022-04-15T19:28:35.000000"}
    {"order_id":1233,"customer_id":871,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-04-24T04:58:24.000000","status":"completed","order_total":79.58,"_shape_table":"order","_shape_seq":1232,"_shape_event_time":"2022-04-24T04:58:24.000000"}
    {"order_id":1119,"customer_id":447,"store_id":5,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-04-25T16:36:13.000000","status":"completed","order_total":39.18,"_shape_table":"order","_shape_seq":1118,"_shape_event_time":"2022-04-25T16:36:13.000000"}
    {"order_id":4209,"customer_id":252,"store_id":1,"shipping_address_id":633,"promotion_id":null,"order_date":"2022-04-27T11:26:19.911331","status":"completed","order_total":26.58,"_shape_table":"order","_shape_seq":4208,"_shape_event_time":"2022-04-27T11:26:19.911331"}
    {"order_id":1347,"customer_id":56,"store_id":6,"shipping_address_id":832,"promotion_id":null,"order_date":"2022-04-28T16:13:04.000000","status":"returned","order_total":4.43,"_shape_table":"order","_shape_seq":1346,"_shape_event_time":"2022-04-28T16:13:04.000000"}
    {"order_id":4703,"customer_id":739,"store_id":1,"shipping_address_id":1221,"promotion_id":null,"order_date":"2022-05-03T02:43:54.000000","status":"completed","order_total":49.86,"_shape_table":"order","_shape_seq":4702,"_shape_event_time":"2022-05-03T02:43:54.000000"}
    {"order_id":4609,"customer_id":468,"store_id":1,"shipping_address_id":1068,"promotion_id":null,"order_date":"2022-05-04T06:47:48.000000","status":"completed","order_total":17.67,"_shape_table":"order","_shape_seq":4608,"_shape_event_time":"2022-05-04T06:47:48.000000"}
    {"order_id":1472,"customer_id":494,"store_id":1,"shipping_address_id":938,"promotion_id":null,"order_date":"2022-05-08T18:23:56.059299","status":"completed","order_total":35.59,"_shape_table":"order","_shape_seq":1471,"_shape_event_time":"2022-05-08T18:23:56.059299"}
    {"order_id":3817,"customer_id":468,"store_id":80,"shipping_address_id":1068,"promotion_id":null,"order_date":"2022-05-10T02:47:00.000000","status":"completed","order_total":20.19,"_shape_table":"order","_shape_seq":3816,"_shape_event_time":"2022-05-10T02:47:00.000000"}
    {"order_id":4186,"customer_id":551,"store_id":3,"shipping_address_id":null,"promotion_id":144,"order_date":"2022-05-10T22:55:33.893725","status":"completed","order_total":30.49,"_shape_table":"order","_shape_seq":4185,"_shape_event_time":"2022-05-10T22:55:33.893725"}
    {"order_id":4910,"customer_id":92,"store_id":4,"shipping_address_id":933,"promotion_id":null,"order_date":"2022-05-14T13:27:08.135034","status":"completed","order_total":55.89,"_shape_table":"order","_shape_seq":4909,"_shape_event_time":"2022-05-14T13:27:08.135034"}
    {"order_id":3711,"customer_id":324,"store_id":71,"shipping_address_id":null,"promotion_id":193,"order_date":"2022-05-15T13:08:55.000000","status":"completed","order_total":10.63,"_shape_table":"order","_shape_seq":3710,"_shape_event_time":"2022-05-15T13:08:55.000000"}
    {"order_id":1403,"customer_id":575,"store_id":3,"shipping_address_id":496,"promotion_id":null,"order_date":"2022-05-17T10:24:33.000000","status":"completed","order_total":511.03,"_shape_table":"order","_shape_seq":1402,"_shape_event_time":"2022-05-17T10:24:33.000000"}
    {"order_id":1926,"customer_id":460,"store_id":1,"shipping_address_id":1342,"promotion_id":null,"order_date":"2022-05-19T23:07:53.055946","status":"completed","order_total":50.35,"_shape_table":"order","_shape_seq":1925,"_shape_event_time":"2022-05-19T23:07:53.055946"}
    {"order_id":1908,"customer_id":25,"store_id":12,"shipping_address_id":450,"promotion_id":191,"order_date":"2022-05-22T00:08:19.338828","status":"completed","order_total":239.68,"_shape_table":"order","_shape_seq":1907,"_shape_event_time":"2022-05-22T00:08:19.338828"}
    {"order_id":3744,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2022-05-22T00:08:19.338828","status":"completed","order_total":35.18,"_shape_table":"order","_shape_seq":3743,"_shape_event_time":"2022-05-22T00:08:19.338828"}
    {"order_id":4640,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2022-05-22T00:08:19.338828","status":"completed","order_total":182.91,"_shape_table":"order","_shape_seq":4639,"_shape_event_time":"2022-05-22T00:08:19.338828"}
    {"order_id":1876,"customer_id":273,"store_id":2,"shipping_address_id":283,"promotion_id":90,"order_date":"2022-05-22T23:24:01.731167","status":"completed","order_total":761.48,"_shape_table":"order","_shape_seq":1875,"_shape_event_time":"2022-05-22T23:24:01.731167"}
    {"order_id":4845,"customer_id":447,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-05-23T10:33:37.000000","status":"completed","order_total":22.15,"_shape_table":"order","_shape_seq":4844,"_shape_event_time":"2022-05-23T10:33:37.000000"}
    {"order_id":1480,"customer_id":866,"store_id":1,"shipping_address_id":1317,"promotion_id":null,"order_date":"2022-05-23T22:34:35.000000","status":"completed","order_total":37.06,"_shape_table":"order","_shape_seq":1479,"_shape_event_time":"2022-05-23T22:34:35.000000"}
    {"order_id":2845,"customer_id":988,"store_id":1,"shipping_address_id":380,"promotion_id":191,"order_date":"2022-05-26T13:29:45.933198","status":"completed","order_total":3.54,"_shape_table":"order","_shape_seq":2844,"_shape_event_time":"2022-05-26T13:29:45.933198"}
    {"order_id":2143,"customer_id":81,"store_id":3,"shipping_address_id":null,"promotion_id":67,"order_date":"2022-05-29T05:28:38.908194","status":"completed","order_total":7.53,"_shape_table":"order","_shape_seq":2142,"_shape_event_time":"2022-05-29T05:28:38.908194"}
    {"order_id":4893,"customer_id":81,"store_id":11,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-05-29T05:28:38.908194","status":"shipped","order_total":0.0,"_shape_table":"order","_shape_seq":4892,"_shape_event_time":"2022-05-29T05:28:38.908194"}
    {"order_id":1231,"customer_id":521,"store_id":34,"shipping_address_id":514,"promotion_id":null,"order_date":"2022-05-30T19:03:27.630776","status":"completed","order_total":54.58,"_shape_table":"order","_shape_seq":1230,"_shape_event_time":"2022-05-30T19:03:27.630776"}
    {"order_id":3913,"customer_id":521,"store_id":2,"shipping_address_id":1035,"promotion_id":null,"order_date":"2022-05-30T19:03:27.630776","status":"completed","order_total":18.96,"_shape_table":"order","_shape_seq":3912,"_shape_event_time":"2022-05-30T19:03:27.630776"}
    {"order_id":4558,"customer_id":521,"store_id":64,"shipping_address_id":37,"promotion_id":null,"order_date":"2022-05-30T19:03:27.630776","status":"completed","order_total":27.97,"_shape_table":"order","_shape_seq":4557,"_shape_event_time":"2022-05-30T19:03:27.630776"}
    {"order_id":3428,"customer_id":361,"store_id":58,"shipping_address_id":null,"promotion_id":52,"order_date":"2022-06-03T00:34:28.252684","status":"completed","order_total":3.54,"_shape_table":"order","_shape_seq":3427,"_shape_event_time":"2022-06-03T00:34:28.252684"}
    {"order_id":2495,"customer_id":56,"store_id":1,"shipping_address_id":832,"promotion_id":18,"order_date":"2022-06-04T09:56:06.000000","status":"completed","order_total":25.26,"_shape_table":"order","_shape_seq":2494,"_shape_event_time":"2022-06-04T09:56:06.000000"}
    {"order_id":802,"customer_id":837,"store_id":1,"shipping_address_id":1008,"promotion_id":null,"order_date":"2022-06-06T04:32:07.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":801,"_shape_event_time":"2022-06-06T04:32:07.000000"}
    {"order_id":3419,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2022-06-08T21:33:25.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3418,"_shape_event_time":"2022-06-08T21:33:25.000000"}
    {"order_id":4610,"customer_id":340,"store_id":1,"shipping_address_id":343,"promotion_id":200,"order_date":"2022-06-09T09:58:11.000000","status":"completed","order_total":43.44,"_shape_table":"order","_shape_seq":4609,"_shape_event_time":"2022-06-09T09:58:11.000000"}
    {"order_id":4259,"customer_id":866,"store_id":20,"shipping_address_id":1317,"promotion_id":null,"order_date":"2022-06-09T20:24:09.000000","status":"processing","order_total":255.31,"_shape_table":"order","_shape_seq":4258,"_shape_event_time":"2022-06-09T20:24:09.000000"}
    {"order_id":2914,"customer_id":199,"store_id":3,"shipping_address_id":null,"promotion_id":190,"order_date":"2022-06-10T18:41:59.771683","status":"completed","order_total":86.59,"_shape_table":"order","_shape_seq":2913,"_shape_event_time":"2022-06-10T18:41:59.771683"}
    {"order_id":2557,"customer_id":782,"store_id":17,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-06-11T09:05:48.377799","status":"shipped","order_total":61.14,"_shape_table":"order","_shape_seq":2556,"_shape_event_time":"2022-06-11T09:05:48.377799"}
    {"order_id":4192,"customer_id":77,"store_id":1,"shipping_address_id":1176,"promotion_id":199,"order_date":"2022-06-14T10:29:02.034338","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4191,"_shape_event_time":"2022-06-14T10:29:02.034338"}
    {"order_id":2507,"customer_id":623,"store_id":1,"shipping_address_id":482,"promotion_id":19,"order_date":"2022-06-20T19:07:59.000000","status":"completed","order_total":30.33,"_shape_table":"order","_shape_seq":2506,"_shape_event_time":"2022-06-20T19:07:59.000000"}
    {"order_id":810,"customer_id":3,"store_id":1,"shipping_address_id":1441,"promotion_id":null,"order_date":"2022-06-24T06:21:29.000000","status":"completed","order_total":226.13,"_shape_table":"order","_shape_seq":809,"_shape_event_time":"2022-06-24T06:21:29.000000"}
    {"order_id":37,"customer_id":3,"store_id":4,"shipping_address_id":1441,"promotion_id":null,"order_date":"2022-06-24T18:57:53.535709","status":"shipped","order_total":100.36,"_shape_table":"order","_shape_seq":36,"_shape_event_time":"2022-06-24T18:57:53.535709"}
    {"order_id":137,"customer_id":3,"store_id":2,"shipping_address_id":1441,"promotion_id":137,"order_date":"2022-06-24T18:57:53.535709","status":"completed","order_total":225.5,"_shape_table":"order","_shape_seq":136,"_shape_event_time":"2022-06-24T18:57:53.535709"}
    {"order_id":188,"customer_id":3,"store_id":5,"shipping_address_id":1441,"promotion_id":null,"order_date":"2022-06-24T18:57:53.535709","status":"processing","order_total":13.29,"_shape_table":"order","_shape_seq":187,"_shape_event_time":"2022-06-24T18:57:53.535709"}
    {"order_id":354,"customer_id":3,"store_id":2,"shipping_address_id":746,"promotion_id":null,"order_date":"2022-06-24T18:57:53.535709","status":"completed","order_total":244.16,"_shape_table":"order","_shape_seq":353,"_shape_event_time":"2022-06-24T18:57:53.535709"}
    {"order_id":414,"customer_id":3,"store_id":1,"shipping_address_id":746,"promotion_id":4,"order_date":"2022-06-24T18:57:53.535709","status":"completed","order_total":98.44,"_shape_table":"order","_shape_seq":413,"_shape_event_time":"2022-06-24T18:57:53.535709"}
    {"order_id":1825,"customer_id":713,"store_id":19,"shipping_address_id":1290,"promotion_id":97,"order_date":"2022-06-24T23:19:12.000000","status":"completed","order_total":18.83,"_shape_table":"order","_shape_seq":1824,"_shape_event_time":"2022-06-24T23:19:12.000000"}
    {"order_id":4127,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2022-06-26T09:08:06.000000","status":"processing","order_total":8.86,"_shape_table":"order","_shape_seq":4126,"_shape_event_time":"2022-06-26T09:08:06.000000"}
    {"order_id":4497,"customer_id":56,"store_id":9,"shipping_address_id":1216,"promotion_id":null,"order_date":"2022-06-28T23:18:00.000000","status":"returned","order_total":44.4,"_shape_table":"order","_shape_seq":4496,"_shape_event_time":"2022-06-28T23:18:00.000000"}
    {"order_id":2776,"customer_id":84,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-07-05T20:01:13.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2775,"_shape_event_time":"2022-07-05T20:01:13.000000"}
    {"order_id":528,"customer_id":3,"store_id":1,"shipping_address_id":746,"promotion_id":189,"order_date":"2022-07-06T15:42:27.000000","status":"completed","order_total":144.81,"_shape_table":"order","_shape_seq":527,"_shape_event_time":"2022-07-06T15:42:27.000000"}
    {"order_id":2664,"customer_id":454,"store_id":54,"shipping_address_id":700,"promotion_id":null,"order_date":"2022-07-06T18:18:43.256507","status":"completed","order_total":10.25,"_shape_table":"order","_shape_seq":2663,"_shape_event_time":"2022-07-06T18:18:43.256507"}
    {"order_id":4676,"customer_id":318,"store_id":1,"shipping_address_id":1289,"promotion_id":null,"order_date":"2022-07-10T08:41:12.000000","status":"processing","order_total":15.21,"_shape_table":"order","_shape_seq":4675,"_shape_event_time":"2022-07-10T08:41:12.000000"}
    {"order_id":4994,"customer_id":988,"store_id":3,"shipping_address_id":380,"promotion_id":null,"order_date":"2022-07-12T16:03:34.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":4993,"_shape_event_time":"2022-07-12T16:03:34.000000"}
    {"order_id":2168,"customer_id":866,"store_id":1,"shipping_address_id":1317,"promotion_id":null,"order_date":"2022-07-12T20:06:58.000000","status":"completed","order_total":43.68,"_shape_table":"order","_shape_seq":2167,"_shape_event_time":"2022-07-12T20:06:58.000000"}
    {"order_id":1031,"customer_id":736,"store_id":3,"shipping_address_id":529,"promotion_id":null,"order_date":"2022-07-13T18:19:21.101153","status":"completed","order_total":79.54,"_shape_table":"order","_shape_seq":1030,"_shape_event_time":"2022-07-13T18:19:21.101153"}
    {"order_id":3242,"customer_id":575,"store_id":1,"shipping_address_id":496,"promotion_id":null,"order_date":"2022-07-15T06:36:48.000000","status":"completed","order_total":54.51,"_shape_table":"order","_shape_seq":3241,"_shape_event_time":"2022-07-15T06:36:48.000000"}
    {"order_id":2274,"customer_id":334,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-07-15T21:33:45.690951","status":"returned","order_total":187.26,"_shape_table":"order","_shape_seq":2273,"_shape_event_time":"2022-07-15T21:33:45.690951"}
    {"order_id":943,"customer_id":324,"store_id":2,"shipping_address_id":null,"promotion_id":196,"order_date":"2022-07-17T05:26:12.000000","status":"processing","order_total":37.34,"_shape_table":"order","_shape_seq":942,"_shape_event_time":"2022-07-17T05:26:12.000000"}
    {"order_id":2468,"customer_id":675,"store_id":5,"shipping_address_id":988,"promotion_id":null,"order_date":"2022-07-17T13:18:58.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2467,"_shape_event_time":"2022-07-17T13:18:58.000000"}
    {"order_id":1699,"customer_id":420,"store_id":1,"shipping_address_id":638,"promotion_id":null,"order_date":"2022-07-18T18:56:23.481790","status":"shipped","order_total":23.5,"_shape_table":"order","_shape_seq":1698,"_shape_event_time":"2022-07-18T18:56:23.481790"}
    {"order_id":4246,"customer_id":89,"store_id":11,"shipping_address_id":1037,"promotion_id":126,"order_date":"2022-07-22T10:59:28.599644","status":"completed","order_total":201.13,"_shape_table":"order","_shape_seq":4245,"_shape_event_time":"2022-07-22T10:59:28.599644"}
    {"order_id":1212,"customer_id":376,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-07-22T11:15:58.836254","status":"completed","order_total":657.58,"_shape_table":"order","_shape_seq":1211,"_shape_event_time":"2022-07-22T11:15:58.836254"}
    {"order_id":3710,"customer_id":376,"store_id":9,"shipping_address_id":null,"promotion_id":129,"order_date":"2022-07-22T11:15:58.836254","status":"completed","order_total":122.44,"_shape_table":"order","_shape_seq":3709,"_shape_event_time":"2022-07-22T11:15:58.836254"}
    {"order_id":505,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-07-24T03:04:57.161115","status":"completed","order_total":45.98,"_shape_table":"order","_shape_seq":504,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":1408,"customer_id":20,"store_id":3,"shipping_address_id":102,"promotion_id":10,"order_date":"2022-07-24T03:04:57.161115","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1407,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":1443,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-07-24T03:04:57.161115","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":1442,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":1797,"customer_id":20,"store_id":13,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-07-24T03:04:57.161115","status":"returned","order_total":7.62,"_shape_table":"order","_shape_seq":1796,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":1980,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-07-24T03:04:57.161115","status":"completed","order_total":13.29,"_shape_table":"order","_shape_seq":1979,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":2088,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-07-24T03:04:57.161115","status":"completed","order_total":33.76,"_shape_table":"order","_shape_seq":2087,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":2901,"customer_id":20,"store_id":3,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-07-24T03:04:57.161115","status":"completed","order_total":49.83,"_shape_table":"order","_shape_seq":2900,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":3336,"customer_id":20,"store_id":2,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-07-24T03:04:57.161115","status":"returned","order_total":84.07,"_shape_table":"order","_shape_seq":3335,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":3348,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-07-24T03:04:57.161115","status":"shipped","order_total":93.09,"_shape_table":"order","_shape_seq":3347,"_shape_event_time":"2022-07-24T03:04:57.161115"}
    {"order_id":426,"customer_id":3,"store_id":12,"shipping_address_id":1441,"promotion_id":null,"order_date":"2022-07-25T17:36:23.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":425,"_shape_event_time":"2022-07-25T17:36:23.000000"}
    {"order_id":2578,"customer_id":286,"store_id":9,"shipping_address_id":31,"promotion_id":null,"order_date":"2022-07-28T08:32:50.302691","status":"completed","order_total":36.83,"_shape_table":"order","_shape_seq":2577,"_shape_event_time":"2022-07-28T08:32:50.302691"}
    {"order_id":66,"customer_id":6,"store_id":1,"shipping_address_id":823,"promotion_id":139,"order_date":"2022-07-28T13:30:07.855576","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":65,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":180,"customer_id":6,"store_id":7,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-07-28T13:30:07.855576","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":179,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":205,"customer_id":6,"store_id":3,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-07-28T13:30:07.855576","status":"returned","order_total":155.39,"_shape_table":"order","_shape_seq":204,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":513,"customer_id":6,"store_id":12,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-07-28T13:30:07.855576","status":"completed","order_total":216.24,"_shape_table":"order","_shape_seq":512,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":545,"customer_id":6,"store_id":18,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-07-28T13:30:07.855576","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":544,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":601,"customer_id":6,"store_id":62,"shipping_address_id":823,"promotion_id":116,"order_date":"2022-07-28T13:30:07.855576","status":"completed","order_total":12.48,"_shape_table":"order","_shape_seq":600,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":677,"customer_id":6,"store_id":1,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-07-28T13:30:07.855576","status":"completed","order_total":62.56,"_shape_table":"order","_shape_seq":676,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":705,"customer_id":6,"store_id":20,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-07-28T13:30:07.855576","status":"completed","order_total":23.5,"_shape_table":"order","_shape_seq":704,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":721,"customer_id":6,"store_id":57,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-07-28T13:30:07.855576","status":"completed","order_total":55.59,"_shape_table":"order","_shape_seq":720,"_shape_event_time":"2022-07-28T13:30:07.855576"}
    {"order_id":131,"customer_id":13,"store_id":6,"shipping_address_id":559,"promotion_id":null,"order_date":"2022-07-28T15:14:00.855434","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":130,"_shape_event_time":"2022-07-28T15:14:00.855434"}
    {"order_id":861,"customer_id":13,"store_id":2,"shipping_address_id":559,"promotion_id":87,"order_date":"2022-07-28T15:14:00.855434","status":"completed","order_total":216.27,"_shape_table":"order","_shape_seq":860,"_shape_event_time":"2022-07-28T15:14:00.855434"}
    {"order_id":1965,"customer_id":13,"store_id":37,"shipping_address_id":1241,"promotion_id":null,"order_date":"2022-07-28T15:14:00.855434","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":1964,"_shape_event_time":"2022-07-28T15:14:00.855434"}
    {"order_id":2077,"customer_id":13,"store_id":1,"shipping_address_id":559,"promotion_id":null,"order_date":"2022-07-28T15:14:00.855434","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2076,"_shape_event_time":"2022-07-28T15:14:00.855434"}
    {"order_id":2310,"customer_id":13,"store_id":1,"shipping_address_id":559,"promotion_id":175,"order_date":"2022-07-28T15:14:00.855434","status":"shipped","order_total":57.11,"_shape_table":"order","_shape_seq":2309,"_shape_event_time":"2022-07-28T15:14:00.855434"}
    {"order_id":863,"customer_id":238,"store_id":10,"shipping_address_id":348,"promotion_id":null,"order_date":"2022-07-28T15:51:52.000000","status":"completed","order_total":125.35,"_shape_table":"order","_shape_seq":862,"_shape_event_time":"2022-07-28T15:51:52.000000"}
    {"order_id":3033,"customer_id":354,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-07-29T15:21:32.611777","status":"completed","order_total":77.83,"_shape_table":"order","_shape_seq":3032,"_shape_event_time":"2022-07-29T15:21:32.611777"}
    {"order_id":3761,"customer_id":354,"store_id":61,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-07-29T15:21:32.611777","status":"cancelled","order_total":56.86,"_shape_table":"order","_shape_seq":3760,"_shape_event_time":"2022-07-29T15:21:32.611777"}
    {"order_id":4768,"customer_id":61,"store_id":1,"shipping_address_id":257,"promotion_id":135,"order_date":"2022-07-31T07:50:15.000000","status":"completed","order_total":1186.15,"_shape_table":"order","_shape_seq":4767,"_shape_event_time":"2022-07-31T07:50:15.000000"}
    {"order_id":2378,"customer_id":824,"store_id":10,"shipping_address_id":962,"promotion_id":null,"order_date":"2022-08-01T08:52:36.131968","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":2377,"_shape_event_time":"2022-08-01T08:52:36.131968"}
    {"order_id":4521,"customer_id":114,"store_id":71,"shipping_address_id":906,"promotion_id":102,"order_date":"2022-08-01T15:01:27.000000","status":"completed","order_total":44.22,"_shape_table":"order","_shape_seq":4520,"_shape_event_time":"2022-08-01T15:01:27.000000"}
    {"order_id":2296,"customer_id":177,"store_id":5,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-08-03T15:51:35.000000","status":"completed","order_total":96.8,"_shape_table":"order","_shape_seq":2295,"_shape_event_time":"2022-08-03T15:51:35.000000"}
    {"order_id":1059,"customer_id":61,"store_id":6,"shipping_address_id":1477,"promotion_id":80,"order_date":"2022-08-04T20:07:32.000000","status":"completed","order_total":88.46,"_shape_table":"order","_shape_seq":1058,"_shape_event_time":"2022-08-04T20:07:32.000000"}
    {"order_id":539,"customer_id":531,"store_id":9,"shipping_address_id":null,"promotion_id":149,"order_date":"2022-08-05T04:15:54.000000","status":"completed","order_total":32.02,"_shape_table":"order","_shape_seq":538,"_shape_event_time":"2022-08-05T04:15:54.000000"}
    {"order_id":2162,"customer_id":886,"store_id":2,"shipping_address_id":367,"promotion_id":null,"order_date":"2022-08-05T12:04:07.785796","status":"completed","order_total":1062.96,"_shape_table":"order","_shape_seq":2161,"_shape_event_time":"2022-08-05T12:04:07.785796"}
    {"order_id":3306,"customer_id":240,"store_id":9,"shipping_address_id":65,"promotion_id":null,"order_date":"2022-08-05T15:51:00.931369","status":"completed","order_total":30.96,"_shape_table":"order","_shape_seq":3305,"_shape_event_time":"2022-08-05T15:51:00.931369"}
    {"order_id":4800,"customer_id":81,"store_id":1,"shipping_address_id":null,"promotion_id":40,"order_date":"2022-08-05T19:05:17.000000","status":"completed","order_total":12.03,"_shape_table":"order","_shape_seq":4799,"_shape_event_time":"2022-08-05T19:05:17.000000"}
    {"order_id":39,"customer_id":9,"store_id":1,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"returned","order_total":109.09,"_shape_table":"order","_shape_seq":38,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":69,"customer_id":9,"store_id":134,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"cancelled","order_total":59.5,"_shape_table":"order","_shape_seq":68,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":115,"customer_id":9,"store_id":46,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":114,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":149,"customer_id":9,"store_id":1,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"completed","order_total":10.25,"_shape_table":"order","_shape_seq":148,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":235,"customer_id":9,"store_id":6,"shipping_address_id":1070,"promotion_id":80,"order_date":"2022-08-06T10:02:25.058379","status":"completed","order_total":21.32,"_shape_table":"order","_shape_seq":234,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":322,"customer_id":9,"store_id":4,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"completed","order_total":70.76,"_shape_table":"order","_shape_seq":321,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":332,"customer_id":9,"store_id":44,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"completed","order_total":11.43,"_shape_table":"order","_shape_seq":331,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":434,"customer_id":9,"store_id":17,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"completed","order_total":38.19,"_shape_table":"order","_shape_seq":433,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":477,"customer_id":9,"store_id":1,"shipping_address_id":1070,"promotion_id":97,"order_date":"2022-08-06T10:02:25.058379","status":"completed","order_total":79.05,"_shape_table":"order","_shape_seq":476,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":670,"customer_id":9,"store_id":13,"shipping_address_id":1070,"promotion_id":169,"order_date":"2022-08-06T10:02:25.058379","status":"shipped","order_total":17.62,"_shape_table":"order","_shape_seq":669,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":998,"customer_id":9,"store_id":1,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"shipped","order_total":13.29,"_shape_table":"order","_shape_seq":997,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":1141,"customer_id":9,"store_id":10,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-06T10:02:25.058379","status":"shipped","order_total":19.11,"_shape_table":"order","_shape_seq":1140,"_shape_event_time":"2022-08-06T10:02:25.058379"}
    {"order_id":1693,"customer_id":492,"store_id":28,"shipping_address_id":1450,"promotion_id":null,"order_date":"2022-08-06T13:44:17.000000","status":"completed","order_total":27.91,"_shape_table":"order","_shape_seq":1692,"_shape_event_time":"2022-08-06T13:44:17.000000"}
    {"order_id":3758,"customer_id":881,"store_id":1,"shipping_address_id":364,"promotion_id":null,"order_date":"2022-08-07T00:56:26.000000","status":"returned","order_total":246.13,"_shape_table":"order","_shape_seq":3757,"_shape_event_time":"2022-08-07T00:56:26.000000"}
    {"order_id":1504,"customer_id":281,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-08-07T22:17:07.756465","status":"completed","order_total":152.19,"_shape_table":"order","_shape_seq":1503,"_shape_event_time":"2022-08-07T22:17:07.756465"}
    {"order_id":2740,"customer_id":560,"store_id":1,"shipping_address_id":363,"promotion_id":null,"order_date":"2022-08-08T03:13:16.000000","status":"completed","order_total":25.96,"_shape_table":"order","_shape_seq":2739,"_shape_event_time":"2022-08-08T03:13:16.000000"}
    {"order_id":304,"customer_id":3,"store_id":3,"shipping_address_id":1441,"promotion_id":106,"order_date":"2022-08-08T09:12:38.000000","status":"completed","order_total":16.84,"_shape_table":"order","_shape_seq":303,"_shape_event_time":"2022-08-08T09:12:38.000000"}
    {"order_id":44,"customer_id":4,"store_id":1,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-08-08T11:14:20.229154","status":"completed","order_total":55.89,"_shape_table":"order","_shape_seq":43,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":160,"customer_id":4,"store_id":2,"shipping_address_id":1091,"promotion_id":74,"order_date":"2022-08-08T11:14:20.229154","status":"completed","order_total":48.57,"_shape_table":"order","_shape_seq":159,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":189,"customer_id":4,"store_id":1,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-08-08T11:14:20.229154","status":"completed","order_total":256.04,"_shape_table":"order","_shape_seq":188,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":366,"customer_id":4,"store_id":5,"shipping_address_id":684,"promotion_id":100,"order_date":"2022-08-08T11:14:20.229154","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":365,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":418,"customer_id":4,"store_id":2,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-08-08T11:14:20.229154","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":417,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":419,"customer_id":4,"store_id":2,"shipping_address_id":684,"promotion_id":12,"order_date":"2022-08-08T11:14:20.229154","status":"completed","order_total":35.14,"_shape_table":"order","_shape_seq":418,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":523,"customer_id":4,"store_id":27,"shipping_address_id":684,"promotion_id":null,"order_date":"2022-08-08T11:14:20.229154","status":"returned","order_total":45.22,"_shape_table":"order","_shape_seq":522,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":631,"customer_id":4,"store_id":24,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-08-08T11:14:20.229154","status":"completed","order_total":27.93,"_shape_table":"order","_shape_seq":630,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":742,"customer_id":4,"store_id":1,"shipping_address_id":684,"promotion_id":null,"order_date":"2022-08-08T11:14:20.229154","status":"completed","order_total":103.15,"_shape_table":"order","_shape_seq":741,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":753,"customer_id":4,"store_id":7,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-08-08T11:14:20.229154","status":"returned","order_total":27.39,"_shape_table":"order","_shape_seq":752,"_shape_event_time":"2022-08-08T11:14:20.229154"}
    {"order_id":1864,"customer_id":297,"store_id":5,"shipping_address_id":995,"promotion_id":null,"order_date":"2022-08-08T11:14:44.000000","status":"completed","order_total":78.56,"_shape_table":"order","_shape_seq":1863,"_shape_event_time":"2022-08-08T11:14:44.000000"}
    {"order_id":1663,"customer_id":498,"store_id":11,"shipping_address_id":1128,"promotion_id":null,"order_date":"2022-08-08T11:24:46.958307","status":"completed","order_total":248.64,"_shape_table":"order","_shape_seq":1662,"_shape_event_time":"2022-08-08T11:24:46.958307"}
    {"order_id":682,"customer_id":3,"store_id":1,"shipping_address_id":1441,"promotion_id":null,"order_date":"2022-08-09T03:14:52.000000","status":"completed","order_total":13.29,"_shape_table":"order","_shape_seq":681,"_shape_event_time":"2022-08-09T03:14:52.000000"}
    {"order_id":2535,"customer_id":146,"store_id":2,"shipping_address_id":null,"promotion_id":15,"order_date":"2022-08-09T09:41:36.000000","status":"completed","order_total":24.94,"_shape_table":"order","_shape_seq":2534,"_shape_event_time":"2022-08-09T09:41:36.000000"}
    {"order_id":2241,"customer_id":890,"store_id":1,"shipping_address_id":1020,"promotion_id":198,"order_date":"2022-08-09T19:31:26.000000","status":"returned","order_total":42.37,"_shape_table":"order","_shape_seq":2240,"_shape_event_time":"2022-08-09T19:31:26.000000"}
    {"order_id":333,"customer_id":9,"store_id":77,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-08-14T04:52:46.000000","status":"completed","order_total":699.61,"_shape_table":"order","_shape_seq":332,"_shape_event_time":"2022-08-14T04:52:46.000000"}
    {"order_id":2987,"customer_id":569,"store_id":1,"shipping_address_id":1486,"promotion_id":9,"order_date":"2022-08-15T05:46:18.000000","status":"completed","order_total":61.35,"_shape_table":"order","_shape_seq":2986,"_shape_event_time":"2022-08-15T05:46:18.000000"}
    {"order_id":1143,"customer_id":20,"store_id":2,"shipping_address_id":102,"promotion_id":43,"order_date":"2022-08-15T10:36:14.000000","status":"completed","order_total":113.71,"_shape_table":"order","_shape_seq":1142,"_shape_event_time":"2022-08-15T10:36:14.000000"}
    {"order_id":2729,"customer_id":99,"store_id":2,"shipping_address_id":null,"promotion_id":56,"order_date":"2022-08-15T23:29:47.000000","status":"returned","order_total":113.24,"_shape_table":"order","_shape_seq":2728,"_shape_event_time":"2022-08-15T23:29:47.000000"}
    {"order_id":4895,"customer_id":922,"store_id":6,"shipping_address_id":null,"promotion_id":17,"order_date":"2022-08-16T02:01:08.000000","status":"cancelled","order_total":105.14,"_shape_table":"order","_shape_seq":4894,"_shape_event_time":"2022-08-16T02:01:08.000000"}
    {"order_id":1346,"customer_id":9,"store_id":3,"shipping_address_id":1070,"promotion_id":106,"order_date":"2022-08-25T18:34:10.000000","status":"completed","order_total":292.62,"_shape_table":"order","_shape_seq":1345,"_shape_event_time":"2022-08-25T18:34:10.000000"}
    {"order_id":2737,"customer_id":48,"store_id":13,"shipping_address_id":1348,"promotion_id":null,"order_date":"2022-08-27T00:16:29.813826","status":"completed","order_total":687.71,"_shape_table":"order","_shape_seq":2736,"_shape_event_time":"2022-08-27T00:16:29.813826"}
    {"order_id":1462,"customer_id":13,"store_id":1,"shipping_address_id":1241,"promotion_id":null,"order_date":"2022-09-01T09:35:34.000000","status":"completed","order_total":24.31,"_shape_table":"order","_shape_seq":1461,"_shape_event_time":"2022-09-01T09:35:34.000000"}
    {"order_id":716,"customer_id":6,"store_id":2,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-09-02T09:19:29.000000","status":"returned","order_total":573.6,"_shape_table":"order","_shape_seq":715,"_shape_event_time":"2022-09-02T09:19:29.000000"}
    {"order_id":2916,"customer_id":48,"store_id":1,"shipping_address_id":1348,"promotion_id":null,"order_date":"2022-09-04T13:07:31.000000","status":"completed","order_total":441.34,"_shape_table":"order","_shape_seq":2915,"_shape_event_time":"2022-09-04T13:07:31.000000"}
    {"order_id":396,"customer_id":9,"store_id":1,"shipping_address_id":1070,"promotion_id":144,"order_date":"2022-09-05T19:33:51.000000","status":"completed","order_total":7.97,"_shape_table":"order","_shape_seq":395,"_shape_event_time":"2022-09-05T19:33:51.000000"}
    {"order_id":1532,"customer_id":13,"store_id":62,"shipping_address_id":1241,"promotion_id":22,"order_date":"2022-09-06T17:44:41.000000","status":"completed","order_total":36.18,"_shape_table":"order","_shape_seq":1531,"_shape_event_time":"2022-09-06T17:44:41.000000"}
    {"order_id":2157,"customer_id":851,"store_id":28,"shipping_address_id":804,"promotion_id":null,"order_date":"2022-09-07T03:26:34.185345","status":"returned","order_total":67.44,"_shape_table":"order","_shape_seq":2156,"_shape_event_time":"2022-09-07T03:26:34.185345"}
    {"order_id":4239,"customer_id":851,"store_id":56,"shipping_address_id":1105,"promotion_id":null,"order_date":"2022-09-07T03:26:34.185345","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4238,"_shape_event_time":"2022-09-07T03:26:34.185345"}
    {"order_id":247,"customer_id":73,"store_id":7,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-07T17:45:27.131331","status":"completed","order_total":23.54,"_shape_table":"order","_shape_seq":246,"_shape_event_time":"2022-09-07T17:45:27.131331"}
    {"order_id":2595,"customer_id":73,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-07T17:45:27.131331","status":"completed","order_total":451.59,"_shape_table":"order","_shape_seq":2594,"_shape_event_time":"2022-09-07T17:45:27.131331"}
    {"order_id":1915,"customer_id":13,"store_id":6,"shipping_address_id":1241,"promotion_id":null,"order_date":"2022-09-09T03:56:46.000000","status":"completed","order_total":76.0,"_shape_table":"order","_shape_seq":1914,"_shape_event_time":"2022-09-09T03:56:46.000000"}
    {"order_id":4069,"customer_id":334,"store_id":91,"shipping_address_id":null,"promotion_id":120,"order_date":"2022-09-10T10:30:22.000000","status":"cancelled","order_total":36.02,"_shape_table":"order","_shape_seq":4068,"_shape_event_time":"2022-09-10T10:30:22.000000"}
    {"order_id":1208,"customer_id":16,"store_id":59,"shipping_address_id":8,"promotion_id":57,"order_date":"2022-09-10T21:03:00.199998","status":"shipped","order_total":70.97,"_shape_table":"order","_shape_seq":1207,"_shape_event_time":"2022-09-10T21:03:00.199998"}
    {"order_id":1266,"customer_id":16,"store_id":2,"shipping_address_id":900,"promotion_id":null,"order_date":"2022-09-10T21:03:00.199998","status":"completed","order_total":13.29,"_shape_table":"order","_shape_seq":1265,"_shape_event_time":"2022-09-10T21:03:00.199998"}
    {"order_id":1428,"customer_id":16,"store_id":11,"shipping_address_id":900,"promotion_id":170,"order_date":"2022-09-10T21:03:00.199998","status":"completed","order_total":77.65,"_shape_table":"order","_shape_seq":1427,"_shape_event_time":"2022-09-10T21:03:00.199998"}
    {"order_id":1645,"customer_id":16,"store_id":1,"shipping_address_id":900,"promotion_id":null,"order_date":"2022-09-10T21:03:00.199998","status":"completed","order_total":474.41,"_shape_table":"order","_shape_seq":1644,"_shape_event_time":"2022-09-10T21:03:00.199998"}
    {"order_id":2499,"customer_id":16,"store_id":3,"shipping_address_id":900,"promotion_id":null,"order_date":"2022-09-10T21:03:00.199998","status":"returned","order_total":143.94,"_shape_table":"order","_shape_seq":2498,"_shape_event_time":"2022-09-10T21:03:00.199998"}
    {"order_id":3382,"customer_id":16,"store_id":7,"shipping_address_id":8,"promotion_id":null,"order_date":"2022-09-10T21:03:00.199998","status":"completed","order_total":50.89,"_shape_table":"order","_shape_seq":3381,"_shape_event_time":"2022-09-10T21:03:00.199998"}
    {"order_id":4937,"customer_id":739,"store_id":8,"shipping_address_id":1221,"promotion_id":null,"order_date":"2022-09-10T21:31:17.000000","status":"shipped","order_total":55.69,"_shape_table":"order","_shape_seq":4936,"_shape_event_time":"2022-09-10T21:31:17.000000"}
    {"order_id":4104,"customer_id":222,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-12T04:38:53.540970","status":"completed","order_total":72.24,"_shape_table":"order","_shape_seq":4103,"_shape_event_time":"2022-09-12T04:38:53.540970"}
    {"order_id":4389,"customer_id":222,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-12T04:38:53.540970","status":"completed","order_total":52.38,"_shape_table":"order","_shape_seq":4388,"_shape_event_time":"2022-09-12T04:38:53.540970"}
    {"order_id":4805,"customer_id":824,"store_id":3,"shipping_address_id":962,"promotion_id":null,"order_date":"2022-09-13T08:41:22.000000","status":"completed","order_total":76.47,"_shape_table":"order","_shape_seq":4804,"_shape_event_time":"2022-09-13T08:41:22.000000"}
    {"order_id":3760,"customer_id":919,"store_id":1,"shipping_address_id":1461,"promotion_id":146,"order_date":"2022-09-13T20:42:14.375150","status":"completed","order_total":420.6,"_shape_table":"order","_shape_seq":3759,"_shape_event_time":"2022-09-13T20:42:14.375150"}
    {"order_id":379,"customer_id":6,"store_id":1,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-09-15T10:09:40.000000","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":378,"_shape_event_time":"2022-09-15T10:09:40.000000"}
    {"order_id":4574,"customer_id":654,"store_id":5,"shipping_address_id":499,"promotion_id":null,"order_date":"2022-09-15T22:50:40.000000","status":"completed","order_total":18.53,"_shape_table":"order","_shape_seq":4573,"_shape_event_time":"2022-09-15T22:50:40.000000"}
    {"order_id":2384,"customer_id":417,"store_id":114,"shipping_address_id":955,"promotion_id":null,"order_date":"2022-09-16T16:50:17.000000","status":"completed","order_total":239.78,"_shape_table":"order","_shape_seq":2383,"_shape_event_time":"2022-09-16T16:50:17.000000"}
    {"order_id":3035,"customer_id":161,"store_id":4,"shipping_address_id":null,"promotion_id":31,"order_date":"2022-09-17T12:46:33.211628","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3034,"_shape_event_time":"2022-09-17T12:46:33.211628"}
    {"order_id":4098,"customer_id":161,"store_id":7,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-17T12:46:33.211628","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":4097,"_shape_event_time":"2022-09-17T12:46:33.211628"}
    {"order_id":4375,"customer_id":161,"store_id":4,"shipping_address_id":null,"promotion_id":104,"order_date":"2022-09-17T12:46:33.211628","status":"completed","order_total":263.59,"_shape_table":"order","_shape_seq":4374,"_shape_event_time":"2022-09-17T12:46:33.211628"}
    {"order_id":686,"customer_id":4,"store_id":5,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-09-18T22:10:43.000000","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":685,"_shape_event_time":"2022-09-18T22:10:43.000000"}
    {"order_id":4914,"customer_id":803,"store_id":32,"shipping_address_id":783,"promotion_id":null,"order_date":"2022-09-19T16:13:53.527333","status":"cancelled","order_total":136.18,"_shape_table":"order","_shape_seq":4913,"_shape_event_time":"2022-09-19T16:13:53.527333"}
    {"order_id":404,"customer_id":3,"store_id":1,"shipping_address_id":1441,"promotion_id":null,"order_date":"2022-09-19T20:25:44.000000","status":"completed","order_total":150.53,"_shape_table":"order","_shape_seq":403,"_shape_event_time":"2022-09-19T20:25:44.000000"}
    {"order_id":1289,"customer_id":129,"store_id":5,"shipping_address_id":70,"promotion_id":null,"order_date":"2022-09-21T03:11:05.474352","status":"completed","order_total":126.71,"_shape_table":"order","_shape_seq":1288,"_shape_event_time":"2022-09-21T03:11:05.474352"}
    {"order_id":2155,"customer_id":129,"store_id":22,"shipping_address_id":70,"promotion_id":null,"order_date":"2022-09-21T03:11:05.474352","status":"returned","order_total":117.05,"_shape_table":"order","_shape_seq":2154,"_shape_event_time":"2022-09-21T03:11:05.474352"}
    {"order_id":3736,"customer_id":706,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-21T12:07:01.000000","status":"completed","order_total":6.02,"_shape_table":"order","_shape_seq":3735,"_shape_event_time":"2022-09-21T12:07:01.000000"}
    {"order_id":4513,"customer_id":551,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-21T14:15:46.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4512,"_shape_event_time":"2022-09-21T14:15:46.000000"}
    {"order_id":1225,"customer_id":34,"store_id":5,"shipping_address_id":702,"promotion_id":null,"order_date":"2022-09-22T13:58:45.848858","status":"completed","order_total":30.75,"_shape_table":"order","_shape_seq":1224,"_shape_event_time":"2022-09-22T13:58:45.848858"}
    {"order_id":2420,"customer_id":34,"store_id":10,"shipping_address_id":409,"promotion_id":null,"order_date":"2022-09-22T13:58:45.848858","status":"completed","order_total":77.32,"_shape_table":"order","_shape_seq":2419,"_shape_event_time":"2022-09-22T13:58:45.848858"}
    {"order_id":3323,"customer_id":34,"store_id":4,"shipping_address_id":409,"promotion_id":null,"order_date":"2022-09-22T13:58:45.848858","status":"completed","order_total":39.92,"_shape_table":"order","_shape_seq":3322,"_shape_event_time":"2022-09-22T13:58:45.848858"}
    {"order_id":4220,"customer_id":34,"store_id":2,"shipping_address_id":346,"promotion_id":null,"order_date":"2022-09-22T13:58:45.848858","status":"cancelled","order_total":8.86,"_shape_table":"order","_shape_seq":4219,"_shape_event_time":"2022-09-22T13:58:45.848858"}
    {"order_id":2289,"customer_id":550,"store_id":1,"shipping_address_id":491,"promotion_id":null,"order_date":"2022-09-22T21:09:27.000000","status":"completed","order_total":53.3,"_shape_table":"order","_shape_seq":2288,"_shape_event_time":"2022-09-22T21:09:27.000000"}
    {"order_id":2462,"customer_id":883,"store_id":1,"shipping_address_id":1,"promotion_id":null,"order_date":"2022-09-25T01:28:30.907767","status":"completed","order_total":33.21,"_shape_table":"order","_shape_seq":2461,"_shape_event_time":"2022-09-25T01:28:30.907767"}
    {"order_id":412,"customer_id":4,"store_id":10,"shipping_address_id":1091,"promotion_id":194,"order_date":"2022-09-26T06:48:19.000000","status":"completed","order_total":110.42,"_shape_table":"order","_shape_seq":411,"_shape_event_time":"2022-09-26T06:48:19.000000"}
    {"order_id":739,"customer_id":16,"store_id":22,"shipping_address_id":900,"promotion_id":5,"order_date":"2022-09-27T02:23:53.000000","status":"shipped","order_total":87.6,"_shape_table":"order","_shape_seq":738,"_shape_event_time":"2022-09-27T02:23:53.000000"}
    {"order_id":704,"customer_id":38,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-27T13:53:08.941397","status":"completed","order_total":31.01,"_shape_table":"order","_shape_seq":703,"_shape_event_time":"2022-09-27T13:53:08.941397"}
    {"order_id":921,"customer_id":38,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-27T13:53:08.941397","status":"completed","order_total":13.29,"_shape_table":"order","_shape_seq":920,"_shape_event_time":"2022-09-27T13:53:08.941397"}
    {"order_id":1578,"customer_id":38,"store_id":58,"shipping_address_id":null,"promotion_id":106,"order_date":"2022-09-27T13:53:08.941397","status":"completed","order_total":89.77,"_shape_table":"order","_shape_seq":1577,"_shape_event_time":"2022-09-27T13:53:08.941397"}
    {"order_id":1890,"customer_id":38,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-27T13:53:08.941397","status":"returned","order_total":298.51,"_shape_table":"order","_shape_seq":1889,"_shape_event_time":"2022-09-27T13:53:08.941397"}
    {"order_id":3326,"customer_id":38,"store_id":55,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-27T13:53:08.941397","status":"completed","order_total":67.74,"_shape_table":"order","_shape_seq":3325,"_shape_event_time":"2022-09-27T13:53:08.941397"}
    {"order_id":4300,"customer_id":38,"store_id":34,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-09-27T13:53:08.941397","status":"processing","order_total":157.54,"_shape_table":"order","_shape_seq":4299,"_shape_event_time":"2022-09-27T13:53:08.941397"}
    {"order_id":4740,"customer_id":38,"store_id":14,"shipping_address_id":null,"promotion_id":57,"order_date":"2022-09-27T13:53:08.941397","status":"completed","order_total":55.34,"_shape_table":"order","_shape_seq":4739,"_shape_event_time":"2022-09-27T13:53:08.941397"}
    {"order_id":4013,"customer_id":127,"store_id":1,"shipping_address_id":877,"promotion_id":null,"order_date":"2022-09-27T19:17:19.000000","status":"completed","order_total":109.53,"_shape_table":"order","_shape_seq":4012,"_shape_event_time":"2022-09-27T19:17:19.000000"}
    {"order_id":1410,"customer_id":48,"store_id":33,"shipping_address_id":1348,"promotion_id":68,"order_date":"2022-09-27T21:23:05.000000","status":"completed","order_total":481.27,"_shape_table":"order","_shape_seq":1409,"_shape_event_time":"2022-09-27T21:23:05.000000"}
    {"order_id":549,"customer_id":6,"store_id":23,"shipping_address_id":823,"promotion_id":63,"order_date":"2022-09-28T10:21:28.000000","status":"returned","order_total":389.23,"_shape_table":"order","_shape_seq":548,"_shape_event_time":"2022-09-28T10:21:28.000000"}
    {"order_id":145,"customer_id":138,"store_id":55,"shipping_address_id":932,"promotion_id":null,"order_date":"2022-09-28T16:35:24.000000","status":"completed","order_total":27.39,"_shape_table":"order","_shape_seq":144,"_shape_event_time":"2022-09-28T16:35:24.000000"}
    {"order_id":3102,"customer_id":699,"store_id":62,"shipping_address_id":351,"promotion_id":102,"order_date":"2022-09-29T11:17:33.000000","status":"completed","order_total":2.22,"_shape_table":"order","_shape_seq":3101,"_shape_event_time":"2022-09-29T11:17:33.000000"}
    {"order_id":1904,"customer_id":424,"store_id":1,"shipping_address_id":337,"promotion_id":null,"order_date":"2022-09-30T00:21:21.501607","status":"shipped","order_total":30.72,"_shape_table":"order","_shape_seq":1903,"_shape_event_time":"2022-09-30T00:21:21.501607"}
    {"order_id":2166,"customer_id":424,"store_id":10,"shipping_address_id":551,"promotion_id":null,"order_date":"2022-09-30T00:21:21.501607","status":"completed","order_total":104.85,"_shape_table":"order","_shape_seq":2165,"_shape_event_time":"2022-09-30T00:21:21.501607"}
    {"order_id":2511,"customer_id":92,"store_id":3,"shipping_address_id":933,"promotion_id":null,"order_date":"2022-09-30T18:53:48.000000","status":"completed","order_total":158.88,"_shape_table":"order","_shape_seq":2510,"_shape_event_time":"2022-09-30T18:53:48.000000"}
    {"order_id":1767,"customer_id":13,"store_id":8,"shipping_address_id":559,"promotion_id":null,"order_date":"2022-10-01T02:30:15.000000","status":"completed","order_total":87.91,"_shape_table":"order","_shape_seq":1766,"_shape_event_time":"2022-10-01T02:30:15.000000"}
    {"order_id":569,"customer_id":6,"store_id":1,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-10-01T08:49:01.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":568,"_shape_event_time":"2022-10-01T08:49:01.000000"}
    {"order_id":2962,"customer_id":144,"store_id":4,"shipping_address_id":1313,"promotion_id":null,"order_date":"2022-10-01T19:13:30.115090","status":"completed","order_total":475.1,"_shape_table":"order","_shape_seq":2961,"_shape_event_time":"2022-10-01T19:13:30.115090"}
    {"order_id":4960,"customer_id":601,"store_id":5,"shipping_address_id":178,"promotion_id":null,"order_date":"2022-10-02T04:07:05.417921","status":"completed","order_total":468.73,"_shape_table":"order","_shape_seq":4959,"_shape_event_time":"2022-10-02T04:07:05.417921"}
    {"order_id":2567,"customer_id":214,"store_id":138,"shipping_address_id":1259,"promotion_id":null,"order_date":"2022-10-02T04:08:54.938471","status":"completed","order_total":32.93,"_shape_table":"order","_shape_seq":2566,"_shape_event_time":"2022-10-02T04:08:54.938471"}
    {"order_id":4175,"customer_id":214,"store_id":12,"shipping_address_id":1259,"promotion_id":null,"order_date":"2022-10-02T04:08:54.938471","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4174,"_shape_event_time":"2022-10-02T04:08:54.938471"}
    {"order_id":1065,"customer_id":886,"store_id":5,"shipping_address_id":367,"promotion_id":null,"order_date":"2022-10-02T18:12:55.000000","status":"completed","order_total":63.96,"_shape_table":"order","_shape_seq":1064,"_shape_event_time":"2022-10-02T18:12:55.000000"}
    {"order_id":4240,"customer_id":326,"store_id":15,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-10-03T11:39:26.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4239,"_shape_event_time":"2022-10-03T11:39:26.000000"}
    {"order_id":3950,"customer_id":168,"store_id":2,"shipping_address_id":1460,"promotion_id":null,"order_date":"2022-10-05T00:06:53.756822","status":"completed","order_total":20.5,"_shape_table":"order","_shape_seq":3949,"_shape_event_time":"2022-10-05T00:06:53.756822"}
    {"order_id":671,"customer_id":4,"store_id":1,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-10-06T07:35:09.000000","status":"shipped","order_total":0.0,"_shape_table":"order","_shape_seq":670,"_shape_event_time":"2022-10-06T07:35:09.000000"}
    {"order_id":2335,"customer_id":81,"store_id":30,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-10-07T02:57:27.000000","status":"returned","order_total":41.22,"_shape_table":"order","_shape_seq":2334,"_shape_event_time":"2022-10-07T02:57:27.000000"}
    {"order_id":3679,"customer_id":25,"store_id":34,"shipping_address_id":450,"promotion_id":null,"order_date":"2022-10-08T01:53:10.000000","status":"completed","order_total":199.84,"_shape_table":"order","_shape_seq":3678,"_shape_event_time":"2022-10-08T01:53:10.000000"}
    {"order_id":2193,"customer_id":941,"store_id":1,"shipping_address_id":1257,"promotion_id":5,"order_date":"2022-10-09T03:51:08.196283","status":"completed","order_total":18.44,"_shape_table":"order","_shape_seq":2192,"_shape_event_time":"2022-10-09T03:51:08.196283"}
    {"order_id":2018,"customer_id":436,"store_id":1,"shipping_address_id":328,"promotion_id":null,"order_date":"2022-10-09T04:05:40.599146","status":"completed","order_total":1543.05,"_shape_table":"order","_shape_seq":2017,"_shape_event_time":"2022-10-09T04:05:40.599146"}
    {"order_id":3333,"customer_id":713,"store_id":1,"shipping_address_id":350,"promotion_id":null,"order_date":"2022-10-10T10:15:18.000000","status":"completed","order_total":64.54,"_shape_table":"order","_shape_seq":3332,"_shape_event_time":"2022-10-10T10:15:18.000000"}
    {"order_id":4036,"customer_id":960,"store_id":33,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-10-10T10:50:47.000000","status":"completed","order_total":91.16,"_shape_table":"order","_shape_seq":4035,"_shape_event_time":"2022-10-10T10:50:47.000000"}
    {"order_id":4721,"customer_id":158,"store_id":17,"shipping_address_id":1268,"promotion_id":56,"order_date":"2022-10-11T10:46:16.725229","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4720,"_shape_event_time":"2022-10-11T10:46:16.725229"}
    {"order_id":2285,"customer_id":250,"store_id":1,"shipping_address_id":602,"promotion_id":100,"order_date":"2022-10-12T01:27:55.000000","status":"completed","order_total":68.16,"_shape_table":"order","_shape_seq":2284,"_shape_event_time":"2022-10-12T01:27:55.000000"}
    {"order_id":2954,"customer_id":196,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-10-12T19:14:45.000000","status":"completed","order_total":58.92,"_shape_table":"order","_shape_seq":2953,"_shape_event_time":"2022-10-12T19:14:45.000000"}
    {"order_id":1871,"customer_id":13,"store_id":1,"shipping_address_id":1241,"promotion_id":null,"order_date":"2022-10-13T14:57:55.000000","status":"completed","order_total":557.39,"_shape_table":"order","_shape_seq":1870,"_shape_event_time":"2022-10-13T14:57:55.000000"}
    {"order_id":359,"customer_id":4,"store_id":12,"shipping_address_id":684,"promotion_id":null,"order_date":"2022-10-15T00:30:47.000000","status":"shipped","order_total":345.85,"_shape_table":"order","_shape_seq":358,"_shape_event_time":"2022-10-15T00:30:47.000000"}
    {"order_id":660,"customer_id":9,"store_id":2,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-10-15T00:41:35.000000","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":659,"_shape_event_time":"2022-10-15T00:41:35.000000"}
    {"order_id":2141,"customer_id":37,"store_id":4,"shipping_address_id":1484,"promotion_id":null,"order_date":"2022-10-15T06:43:21.450956","status":"cancelled","order_total":439.73,"_shape_table":"order","_shape_seq":2140,"_shape_event_time":"2022-10-15T06:43:21.450956"}
    {"order_id":3556,"customer_id":37,"store_id":12,"shipping_address_id":1402,"promotion_id":null,"order_date":"2022-10-15T06:43:21.450956","status":"completed","order_total":64.99,"_shape_table":"order","_shape_seq":3555,"_shape_event_time":"2022-10-15T06:43:21.450956"}
    {"order_id":3670,"customer_id":37,"store_id":1,"shipping_address_id":735,"promotion_id":null,"order_date":"2022-10-15T06:43:21.450956","status":"completed","order_total":62.35,"_shape_table":"order","_shape_seq":3669,"_shape_event_time":"2022-10-15T06:43:21.450956"}
    {"order_id":3754,"customer_id":37,"store_id":1,"shipping_address_id":735,"promotion_id":null,"order_date":"2022-10-15T06:43:21.450956","status":"completed","order_total":20.5,"_shape_table":"order","_shape_seq":3753,"_shape_event_time":"2022-10-15T06:43:21.450956"}
    {"order_id":114,"customer_id":20,"store_id":111,"shipping_address_id":102,"promotion_id":90,"order_date":"2022-10-16T02:21:48.000000","status":"completed","order_total":461.86,"_shape_table":"order","_shape_seq":113,"_shape_event_time":"2022-10-16T02:21:48.000000"}
    {"order_id":3241,"customer_id":623,"store_id":1,"shipping_address_id":482,"promotion_id":null,"order_date":"2022-10-17T19:51:56.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3240,"_shape_event_time":"2022-10-17T19:51:56.000000"}
    {"order_id":2634,"customer_id":34,"store_id":2,"shipping_address_id":584,"promotion_id":null,"order_date":"2022-10-18T06:22:32.000000","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":2633,"_shape_event_time":"2022-10-18T06:22:32.000000"}
    {"order_id":4701,"customer_id":790,"store_id":25,"shipping_address_id":897,"promotion_id":null,"order_date":"2022-10-18T16:10:26.000000","status":"completed","order_total":269.4,"_shape_table":"order","_shape_seq":4700,"_shape_event_time":"2022-10-18T16:10:26.000000"}
    {"order_id":53,"customer_id":15,"store_id":1,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":93.76,"_shape_table":"order","_shape_seq":52,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":760,"customer_id":15,"store_id":12,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":30.52,"_shape_table":"order","_shape_seq":759,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":1676,"customer_id":15,"store_id":2,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":28.5,"_shape_table":"order","_shape_seq":1675,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":2101,"customer_id":15,"store_id":3,"shipping_address_id":695,"promotion_id":3,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":10.63,"_shape_table":"order","_shape_seq":2100,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":2470,"customer_id":15,"store_id":13,"shipping_address_id":695,"promotion_id":80,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":52.2,"_shape_table":"order","_shape_seq":2469,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":2725,"customer_id":15,"store_id":41,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"returned","order_total":432.48,"_shape_table":"order","_shape_seq":2724,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":2898,"customer_id":15,"store_id":57,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":147.98,"_shape_table":"order","_shape_seq":2897,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":2918,"customer_id":15,"store_id":98,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":2917,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":2988,"customer_id":15,"store_id":8,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"returned","order_total":53.6,"_shape_table":"order","_shape_seq":2987,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":3008,"customer_id":15,"store_id":1,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":48.47,"_shape_table":"order","_shape_seq":3007,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":3308,"customer_id":15,"store_id":3,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-18T19:19:47.100432","status":"completed","order_total":18.53,"_shape_table":"order","_shape_seq":3307,"_shape_event_time":"2022-10-18T19:19:47.100432"}
    {"order_id":3113,"customer_id":963,"store_id":1,"shipping_address_id":905,"promotion_id":null,"order_date":"2022-10-22T22:07:42.000000","status":"completed","order_total":278.7,"_shape_table":"order","_shape_seq":3112,"_shape_event_time":"2022-10-22T22:07:42.000000"}
    {"order_id":2699,"customer_id":355,"store_id":147,"shipping_address_id":1271,"promotion_id":null,"order_date":"2022-10-23T07:23:12.277794","status":"completed","order_total":253.57,"_shape_table":"order","_shape_seq":2698,"_shape_event_time":"2022-10-23T07:23:12.277794"}
    {"order_id":4593,"customer_id":355,"store_id":3,"shipping_address_id":1271,"promotion_id":null,"order_date":"2022-10-23T07:23:12.277794","status":"completed","order_total":8.24,"_shape_table":"order","_shape_seq":4592,"_shape_event_time":"2022-10-23T07:23:12.277794"}
    {"order_id":2004,"customer_id":185,"store_id":61,"shipping_address_id":1227,"promotion_id":124,"order_date":"2022-10-23T22:33:04.000000","status":"completed","order_total":29.3,"_shape_table":"order","_shape_seq":2003,"_shape_event_time":"2022-10-23T22:33:04.000000"}
    {"order_id":2060,"customer_id":37,"store_id":9,"shipping_address_id":1484,"promotion_id":55,"order_date":"2022-10-26T04:03:55.000000","status":"completed","order_total":18.8,"_shape_table":"order","_shape_seq":2059,"_shape_event_time":"2022-10-26T04:03:55.000000"}
    {"order_id":3180,"customer_id":685,"store_id":1,"shipping_address_id":1390,"promotion_id":null,"order_date":"2022-10-26T11:47:28.000000","status":"completed","order_total":13.29,"_shape_table":"order","_shape_seq":3179,"_shape_event_time":"2022-10-26T11:47:28.000000"}
    {"order_id":673,"customer_id":4,"store_id":1,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-10-27T03:24:08.000000","status":"completed","order_total":68.97,"_shape_table":"order","_shape_seq":672,"_shape_event_time":"2022-10-27T03:24:08.000000"}
    {"order_id":2671,"customer_id":215,"store_id":2,"shipping_address_id":1332,"promotion_id":null,"order_date":"2022-10-28T14:26:20.000000","status":"completed","order_total":216.24,"_shape_table":"order","_shape_seq":2670,"_shape_event_time":"2022-10-28T14:26:20.000000"}
    {"order_id":2703,"customer_id":16,"store_id":4,"shipping_address_id":900,"promotion_id":54,"order_date":"2022-10-28T15:11:53.000000","status":"completed","order_total":13.67,"_shape_table":"order","_shape_seq":2702,"_shape_event_time":"2022-10-28T15:11:53.000000"}
    {"order_id":2625,"customer_id":15,"store_id":1,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-29T01:25:02.000000","status":"completed","order_total":61.3,"_shape_table":"order","_shape_seq":2624,"_shape_event_time":"2022-10-29T01:25:02.000000"}
    {"order_id":2816,"customer_id":746,"store_id":4,"shipping_address_id":1247,"promotion_id":null,"order_date":"2022-10-29T07:50:25.000000","status":"cancelled","order_total":47.89,"_shape_table":"order","_shape_seq":2815,"_shape_event_time":"2022-10-29T07:50:25.000000"}
    {"order_id":954,"customer_id":15,"store_id":1,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-10-29T09:33:32.000000","status":"completed","order_total":238.33,"_shape_table":"order","_shape_seq":953,"_shape_event_time":"2022-10-29T09:33:32.000000"}
    {"order_id":3840,"customer_id":828,"store_id":1,"shipping_address_id":null,"promotion_id":13,"order_date":"2022-10-31T09:37:56.000000","status":"completed","order_total":27.85,"_shape_table":"order","_shape_seq":3839,"_shape_event_time":"2022-10-31T09:37:56.000000"}
    {"order_id":1250,"customer_id":746,"store_id":5,"shipping_address_id":53,"promotion_id":null,"order_date":"2022-10-31T17:36:49.000000","status":"completed","order_total":8.9,"_shape_table":"order","_shape_seq":1249,"_shape_event_time":"2022-10-31T17:36:49.000000"}
    {"order_id":3277,"customer_id":406,"store_id":1,"shipping_address_id":121,"promotion_id":null,"order_date":"2022-11-01T07:45:29.000000","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":3276,"_shape_event_time":"2022-11-01T07:45:29.000000"}
    {"order_id":1123,"customer_id":61,"store_id":3,"shipping_address_id":257,"promotion_id":null,"order_date":"2022-11-02T02:47:49.000000","status":"completed","order_total":418.85,"_shape_table":"order","_shape_seq":1122,"_shape_event_time":"2022-11-02T02:47:49.000000"}
    {"order_id":2594,"customer_id":291,"store_id":2,"shipping_address_id":183,"promotion_id":null,"order_date":"2022-11-02T18:04:32.000000","status":"cancelled","order_total":52.18,"_shape_table":"order","_shape_seq":2593,"_shape_event_time":"2022-11-02T18:04:32.000000"}
    {"order_id":4125,"customer_id":232,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-11-03T09:43:52.000000","status":"completed","order_total":12.67,"_shape_table":"order","_shape_seq":4124,"_shape_event_time":"2022-11-03T09:43:52.000000"}
    {"order_id":4361,"customer_id":837,"store_id":2,"shipping_address_id":886,"promotion_id":null,"order_date":"2022-11-03T11:21:08.000000","status":"completed","order_total":71.28,"_shape_table":"order","_shape_seq":4360,"_shape_event_time":"2022-11-03T11:21:08.000000"}
    {"order_id":772,"customer_id":3,"store_id":1,"shipping_address_id":1441,"promotion_id":65,"order_date":"2022-11-03T13:00:00.000000","status":"completed","order_total":53.99,"_shape_table":"order","_shape_seq":771,"_shape_event_time":"2022-11-03T13:00:00.000000"}
    {"order_id":4393,"customer_id":392,"store_id":13,"shipping_address_id":185,"promotion_id":null,"order_date":"2022-11-03T16:04:30.971708","status":"returned","order_total":8.86,"_shape_table":"order","_shape_seq":4392,"_shape_event_time":"2022-11-03T16:04:30.971708"}
    {"order_id":2522,"customer_id":726,"store_id":32,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-11-06T10:59:35.751152","status":"completed","order_total":17.1,"_shape_table":"order","_shape_seq":2521,"_shape_event_time":"2022-11-06T10:59:35.751152"}
    {"order_id":3222,"customer_id":726,"store_id":2,"shipping_address_id":null,"promotion_id":11,"order_date":"2022-11-06T10:59:35.751152","status":"completed","order_total":12.48,"_shape_table":"order","_shape_seq":3221,"_shape_event_time":"2022-11-06T10:59:35.751152"}
    {"order_id":3623,"customer_id":37,"store_id":3,"shipping_address_id":735,"promotion_id":55,"order_date":"2022-11-08T10:11:11.000000","status":"completed","order_total":7.09,"_shape_table":"order","_shape_seq":3622,"_shape_event_time":"2022-11-08T10:11:11.000000"}
    {"order_id":428,"customer_id":45,"store_id":4,"shipping_address_id":1087,"promotion_id":50,"order_date":"2022-11-08T21:26:04.383354","status":"cancelled","order_total":48.23,"_shape_table":"order","_shape_seq":427,"_shape_event_time":"2022-11-08T21:26:04.383354"}
    {"order_id":1063,"customer_id":45,"store_id":2,"shipping_address_id":1087,"promotion_id":98,"order_date":"2022-11-08T21:26:04.383354","status":"completed","order_total":11.3,"_shape_table":"order","_shape_seq":1062,"_shape_event_time":"2022-11-08T21:26:04.383354"}
    {"order_id":1434,"customer_id":45,"store_id":1,"shipping_address_id":1087,"promotion_id":null,"order_date":"2022-11-08T21:26:04.383354","status":"completed","order_total":113.84,"_shape_table":"order","_shape_seq":1433,"_shape_event_time":"2022-11-08T21:26:04.383354"}
    {"order_id":1970,"customer_id":45,"store_id":26,"shipping_address_id":1087,"promotion_id":null,"order_date":"2022-11-08T21:26:04.383354","status":"completed","order_total":273.64,"_shape_table":"order","_shape_seq":1969,"_shape_event_time":"2022-11-08T21:26:04.383354"}
    {"order_id":2164,"customer_id":45,"store_id":34,"shipping_address_id":1087,"promotion_id":null,"order_date":"2022-11-08T21:26:04.383354","status":"completed","order_total":10.25,"_shape_table":"order","_shape_seq":2163,"_shape_event_time":"2022-11-08T21:26:04.383354"}
    {"order_id":3367,"customer_id":45,"store_id":7,"shipping_address_id":1087,"promotion_id":null,"order_date":"2022-11-08T21:26:04.383354","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":3366,"_shape_event_time":"2022-11-08T21:26:04.383354"}
    {"order_id":3433,"customer_id":45,"store_id":24,"shipping_address_id":1087,"promotion_id":null,"order_date":"2022-11-08T21:26:04.383354","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3432,"_shape_event_time":"2022-11-08T21:26:04.383354"}
    {"order_id":3478,"customer_id":45,"store_id":53,"shipping_address_id":1087,"promotion_id":153,"order_date":"2022-11-08T21:26:04.383354","status":"completed","order_total":43.63,"_shape_table":"order","_shape_seq":3477,"_shape_event_time":"2022-11-08T21:26:04.383354"}
    {"order_id":728,"customer_id":81,"store_id":2,"shipping_address_id":null,"promotion_id":32,"order_date":"2022-11-09T08:48:04.000000","status":"completed","order_total":43.06,"_shape_table":"order","_shape_seq":727,"_shape_event_time":"2022-11-09T08:48:04.000000"}
    {"order_id":10,"customer_id":4,"store_id":1,"shipping_address_id":684,"promotion_id":null,"order_date":"2022-11-09T11:01:57.000000","status":"completed","order_total":48.42,"_shape_table":"order","_shape_seq":9,"_shape_event_time":"2022-11-09T11:01:57.000000"}
    {"order_id":520,"customer_id":4,"store_id":7,"shipping_address_id":684,"promotion_id":null,"order_date":"2022-11-10T05:02:37.000000","status":"completed","order_total":26.65,"_shape_table":"order","_shape_seq":519,"_shape_event_time":"2022-11-10T05:02:37.000000"}
    {"order_id":737,"customer_id":6,"store_id":99,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-11-11T00:14:37.000000","status":"completed","order_total":36.25,"_shape_table":"order","_shape_seq":736,"_shape_event_time":"2022-11-11T00:14:37.000000"}
    {"order_id":1055,"customer_id":34,"store_id":1,"shipping_address_id":702,"promotion_id":null,"order_date":"2022-11-11T06:53:12.000000","status":"completed","order_total":38.75,"_shape_table":"order","_shape_seq":1054,"_shape_event_time":"2022-11-11T06:53:12.000000"}
    {"order_id":90,"customer_id":4,"store_id":1,"shipping_address_id":684,"promotion_id":null,"order_date":"2022-11-11T20:14:59.000000","status":"completed","order_total":24.93,"_shape_table":"order","_shape_seq":89,"_shape_event_time":"2022-11-11T20:14:59.000000"}
    {"order_id":2748,"customer_id":37,"store_id":1,"shipping_address_id":1484,"promotion_id":197,"order_date":"2022-11-13T08:55:26.000000","status":"returned","order_total":0.0,"_shape_table":"order","_shape_seq":2747,"_shape_event_time":"2022-11-13T08:55:26.000000"}
    {"order_id":1286,"customer_id":757,"store_id":1,"shipping_address_id":null,"promotion_id":200,"order_date":"2022-11-13T18:09:54.000000","status":"cancelled","order_total":14.17,"_shape_table":"order","_shape_seq":1285,"_shape_event_time":"2022-11-13T18:09:54.000000"}
    {"order_id":4887,"customer_id":305,"store_id":30,"shipping_address_id":533,"promotion_id":null,"order_date":"2022-11-14T04:51:21.466398","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":4886,"_shape_event_time":"2022-11-14T04:51:21.466398"}
    {"order_id":1457,"customer_id":38,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-11-15T16:31:51.000000","status":"completed","order_total":222.26,"_shape_table":"order","_shape_seq":1456,"_shape_event_time":"2022-11-15T16:31:51.000000"}
    {"order_id":2903,"customer_id":15,"store_id":44,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-11-16T05:15:07.000000","status":"completed","order_total":24.07,"_shape_table":"order","_shape_seq":2902,"_shape_event_time":"2022-11-16T05:15:07.000000"}
    {"order_id":2134,"customer_id":527,"store_id":1,"shipping_address_id":44,"promotion_id":151,"order_date":"2022-11-16T14:02:24.000000","status":"shipped","order_total":172.99,"_shape_table":"order","_shape_seq":2133,"_shape_event_time":"2022-11-16T14:02:24.000000"}
    {"order_id":1468,"customer_id":929,"store_id":1,"shipping_address_id":153,"promotion_id":91,"order_date":"2022-11-17T04:28:34.316231","status":"completed","order_total":3.99,"_shape_table":"order","_shape_seq":1467,"_shape_event_time":"2022-11-17T04:28:34.316231"}
    {"order_id":3794,"customer_id":929,"store_id":6,"shipping_address_id":153,"promotion_id":47,"order_date":"2022-11-17T04:28:34.316231","status":"completed","order_total":42.8,"_shape_table":"order","_shape_seq":3793,"_shape_event_time":"2022-11-17T04:28:34.316231"}
    {"order_id":2433,"customer_id":968,"store_id":10,"shipping_address_id":1452,"promotion_id":null,"order_date":"2022-11-17T05:09:53.358843","status":"completed","order_total":178.47,"_shape_table":"order","_shape_seq":2432,"_shape_event_time":"2022-11-17T05:09:53.358843"}
    {"order_id":150,"customer_id":38,"store_id":120,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-11-17T05:29:28.000000","status":"cancelled","order_total":77.05,"_shape_table":"order","_shape_seq":149,"_shape_event_time":"2022-11-17T05:29:28.000000"}
    {"order_id":1561,"customer_id":20,"store_id":138,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-11-17T15:23:13.000000","status":"completed","order_total":61.75,"_shape_table":"order","_shape_seq":1560,"_shape_event_time":"2022-11-17T15:23:13.000000"}
    {"order_id":3987,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":121,"order_date":"2022-11-18T14:25:55.000000","status":"completed","order_total":35.79,"_shape_table":"order","_shape_seq":3986,"_shape_event_time":"2022-11-18T14:25:55.000000"}
    {"order_id":139,"customer_id":6,"store_id":19,"shipping_address_id":823,"promotion_id":122,"order_date":"2022-11-18T20:44:14.000000","status":"shipped","order_total":17.26,"_shape_table":"order","_shape_seq":138,"_shape_event_time":"2022-11-18T20:44:14.000000"}
    {"order_id":299,"customer_id":41,"store_id":1,"shipping_address_id":1154,"promotion_id":null,"order_date":"2022-11-19T18:04:12.664324","status":"completed","order_total":70.54,"_shape_table":"order","_shape_seq":298,"_shape_event_time":"2022-11-19T18:04:12.664324"}
    {"order_id":693,"customer_id":41,"store_id":1,"shipping_address_id":1154,"promotion_id":99,"order_date":"2022-11-19T18:04:12.664324","status":"completed","order_total":89.83,"_shape_table":"order","_shape_seq":692,"_shape_event_time":"2022-11-19T18:04:12.664324"}
    {"order_id":2769,"customer_id":41,"store_id":3,"shipping_address_id":1154,"promotion_id":null,"order_date":"2022-11-19T18:04:12.664324","status":"completed","order_total":128.24,"_shape_table":"order","_shape_seq":2768,"_shape_event_time":"2022-11-19T18:04:12.664324"}
    {"order_id":3022,"customer_id":41,"store_id":64,"shipping_address_id":1154,"promotion_id":51,"order_date":"2022-11-19T18:04:12.664324","status":"completed","order_total":114.43,"_shape_table":"order","_shape_seq":3021,"_shape_event_time":"2022-11-19T18:04:12.664324"}
    {"order_id":3053,"customer_id":41,"store_id":18,"shipping_address_id":1154,"promotion_id":null,"order_date":"2022-11-19T18:04:12.664324","status":"completed","order_total":49.28,"_shape_table":"order","_shape_seq":3052,"_shape_event_time":"2022-11-19T18:04:12.664324"}
    {"order_id":4969,"customer_id":41,"store_id":125,"shipping_address_id":1154,"promotion_id":null,"order_date":"2022-11-19T18:04:12.664324","status":"completed","order_total":1090.06,"_shape_table":"order","_shape_seq":4968,"_shape_event_time":"2022-11-19T18:04:12.664324"}
    {"order_id":1192,"customer_id":841,"store_id":16,"shipping_address_id":401,"promotion_id":null,"order_date":"2022-11-19T23:57:27.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1191,"_shape_event_time":"2022-11-19T23:57:27.000000"}
    {"order_id":3139,"customer_id":214,"store_id":2,"shipping_address_id":1259,"promotion_id":null,"order_date":"2022-11-21T19:25:35.000000","status":"cancelled","order_total":14.68,"_shape_table":"order","_shape_seq":3138,"_shape_event_time":"2022-11-21T19:25:35.000000"}
    {"order_id":2757,"customer_id":34,"store_id":2,"shipping_address_id":537,"promotion_id":null,"order_date":"2022-11-22T05:42:35.000000","status":"completed","order_total":95.16,"_shape_table":"order","_shape_seq":2756,"_shape_event_time":"2022-11-22T05:42:35.000000"}
    {"order_id":77,"customer_id":20,"store_id":53,"shipping_address_id":102,"promotion_id":null,"order_date":"2022-11-22T15:44:19.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":76,"_shape_event_time":"2022-11-22T15:44:19.000000"}
    {"order_id":3238,"customer_id":302,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-11-24T22:59:18.000000","status":"completed","order_total":101.43,"_shape_table":"order","_shape_seq":3237,"_shape_event_time":"2022-11-24T22:59:18.000000"}
    {"order_id":1185,"customer_id":543,"store_id":2,"shipping_address_id":593,"promotion_id":103,"order_date":"2022-11-25T01:08:40.986657","status":"cancelled","order_total":31.29,"_shape_table":"order","_shape_seq":1184,"_shape_event_time":"2022-11-25T01:08:40.986657"}
    {"order_id":3948,"customer_id":543,"store_id":2,"shipping_address_id":593,"promotion_id":3,"order_date":"2022-11-25T01:08:40.986657","status":"completed","order_total":14.14,"_shape_table":"order","_shape_seq":3947,"_shape_event_time":"2022-11-25T01:08:40.986657"}
    {"order_id":1194,"customer_id":536,"store_id":149,"shipping_address_id":314,"promotion_id":141,"order_date":"2022-11-26T09:28:16.274928","status":"completed","order_total":20.01,"_shape_table":"order","_shape_seq":1193,"_shape_event_time":"2022-11-26T09:28:16.274928"}
    {"order_id":2175,"customer_id":536,"store_id":6,"shipping_address_id":1110,"promotion_id":null,"order_date":"2022-11-26T09:28:16.274928","status":"completed","order_total":32.25,"_shape_table":"order","_shape_seq":2174,"_shape_event_time":"2022-11-26T09:28:16.274928"}
    {"order_id":4326,"customer_id":79,"store_id":1,"shipping_address_id":null,"promotion_id":154,"order_date":"2022-11-27T02:50:52.870956","status":"completed","order_total":14.26,"_shape_table":"order","_shape_seq":4325,"_shape_event_time":"2022-11-27T02:50:52.870956"}
    {"order_id":3436,"customer_id":37,"store_id":1,"shipping_address_id":1484,"promotion_id":null,"order_date":"2022-11-28T06:17:10.000000","status":"completed","order_total":350.16,"_shape_table":"order","_shape_seq":3435,"_shape_event_time":"2022-11-28T06:17:10.000000"}
    {"order_id":1488,"customer_id":436,"store_id":103,"shipping_address_id":328,"promotion_id":null,"order_date":"2022-11-28T22:09:34.000000","status":"completed","order_total":83.42,"_shape_table":"order","_shape_seq":1487,"_shape_event_time":"2022-11-28T22:09:34.000000"}
    {"order_id":825,"customer_id":959,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-11-29T08:45:34.000000","status":"returned","order_total":204.66,"_shape_table":"order","_shape_seq":824,"_shape_event_time":"2022-11-29T08:45:34.000000"}
    {"order_id":4305,"customer_id":612,"store_id":1,"shipping_address_id":48,"promotion_id":null,"order_date":"2022-12-01T04:33:35.000000","status":"completed","order_total":311.08,"_shape_table":"order","_shape_seq":4304,"_shape_event_time":"2022-12-01T04:33:35.000000"}
    {"order_id":3589,"customer_id":117,"store_id":4,"shipping_address_id":123,"promotion_id":152,"order_date":"2022-12-01T05:46:28.000000","status":"completed","order_total":21.32,"_shape_table":"order","_shape_seq":3588,"_shape_event_time":"2022-12-01T05:46:28.000000"}
    {"order_id":4351,"customer_id":728,"store_id":11,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-01T06:19:10.000000","status":"completed","order_total":23.5,"_shape_table":"order","_shape_seq":4350,"_shape_event_time":"2022-12-01T06:19:10.000000"}
    {"order_id":4277,"customer_id":117,"store_id":1,"shipping_address_id":123,"promotion_id":71,"order_date":"2022-12-01T10:20:43.685158","status":"completed","order_total":23.8,"_shape_table":"order","_shape_seq":4276,"_shape_event_time":"2022-12-01T10:20:43.685158"}
    {"order_id":2073,"customer_id":13,"store_id":1,"shipping_address_id":1241,"promotion_id":null,"order_date":"2022-12-02T04:51:20.000000","status":"completed","order_total":57.02,"_shape_table":"order","_shape_seq":2072,"_shape_event_time":"2022-12-02T04:51:20.000000"}
    {"order_id":562,"customer_id":3,"store_id":1,"shipping_address_id":746,"promotion_id":null,"order_date":"2022-12-02T19:05:55.000000","status":"completed","order_total":229.53,"_shape_table":"order","_shape_seq":561,"_shape_event_time":"2022-12-02T19:05:55.000000"}
    {"order_id":1099,"customer_id":156,"store_id":1,"shipping_address_id":964,"promotion_id":null,"order_date":"2022-12-03T04:42:53.899214","status":"completed","order_total":33.74,"_shape_table":"order","_shape_seq":1098,"_shape_event_time":"2022-12-03T04:42:53.899214"}
    {"order_id":2911,"customer_id":156,"store_id":4,"shipping_address_id":964,"promotion_id":null,"order_date":"2022-12-03T04:42:53.899214","status":"completed","order_total":68.8,"_shape_table":"order","_shape_seq":2910,"_shape_event_time":"2022-12-03T04:42:53.899214"}
    {"order_id":1515,"customer_id":962,"store_id":5,"shipping_address_id":234,"promotion_id":null,"order_date":"2022-12-04T12:06:33.488734","status":"shipped","order_total":4.43,"_shape_table":"order","_shape_seq":1514,"_shape_event_time":"2022-12-04T12:06:33.488734"}
    {"order_id":3528,"customer_id":962,"store_id":1,"shipping_address_id":1389,"promotion_id":71,"order_date":"2022-12-04T12:06:33.488734","status":"processing","order_total":46.38,"_shape_table":"order","_shape_seq":3527,"_shape_event_time":"2022-12-04T12:06:33.488734"}
    {"order_id":3873,"customer_id":744,"store_id":18,"shipping_address_id":734,"promotion_id":null,"order_date":"2022-12-04T23:46:59.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":3872,"_shape_event_time":"2022-12-04T23:46:59.000000"}
    {"order_id":1975,"customer_id":144,"store_id":1,"shipping_address_id":1313,"promotion_id":99,"order_date":"2022-12-06T07:30:49.000000","status":"returned","order_total":3.54,"_shape_table":"order","_shape_seq":1974,"_shape_event_time":"2022-12-06T07:30:49.000000"}
    {"order_id":3096,"customer_id":610,"store_id":35,"shipping_address_id":270,"promotion_id":null,"order_date":"2022-12-06T08:24:30.684369","status":"completed","order_total":225.1,"_shape_table":"order","_shape_seq":3095,"_shape_event_time":"2022-12-06T08:24:30.684369"}
    {"order_id":4008,"customer_id":610,"store_id":5,"shipping_address_id":270,"promotion_id":16,"order_date":"2022-12-06T08:24:30.684369","status":"completed","order_total":3.32,"_shape_table":"order","_shape_seq":4007,"_shape_event_time":"2022-12-06T08:24:30.684369"}
    {"order_id":1371,"customer_id":26,"store_id":6,"shipping_address_id":66,"promotion_id":null,"order_date":"2022-12-06T16:12:41.992235","status":"shipped","order_total":0.0,"_shape_table":"order","_shape_seq":1370,"_shape_event_time":"2022-12-06T16:12:41.992235"}
    {"order_id":3953,"customer_id":26,"store_id":128,"shipping_address_id":39,"promotion_id":null,"order_date":"2022-12-06T16:12:41.992235","status":"shipped","order_total":374.1,"_shape_table":"order","_shape_seq":3952,"_shape_event_time":"2022-12-06T16:12:41.992235"}
    {"order_id":4810,"customer_id":26,"store_id":30,"shipping_address_id":66,"promotion_id":36,"order_date":"2022-12-06T16:12:41.992235","status":"completed","order_total":180.08,"_shape_table":"order","_shape_seq":4809,"_shape_event_time":"2022-12-06T16:12:41.992235"}
    {"order_id":2610,"customer_id":118,"store_id":81,"shipping_address_id":498,"promotion_id":151,"order_date":"2022-12-07T10:28:04.000000","status":"completed","order_total":21.85,"_shape_table":"order","_shape_seq":2609,"_shape_event_time":"2022-12-07T10:28:04.000000"}
    {"order_id":3462,"customer_id":612,"store_id":74,"shipping_address_id":48,"promotion_id":null,"order_date":"2022-12-07T15:33:54.000000","status":"shipped","order_total":92.48,"_shape_table":"order","_shape_seq":3461,"_shape_event_time":"2022-12-07T15:33:54.000000"}
    {"order_id":4648,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2022-12-07T18:13:49.000000","status":"completed","order_total":217.59,"_shape_table":"order","_shape_seq":4647,"_shape_event_time":"2022-12-07T18:13:49.000000"}
    {"order_id":3935,"customer_id":831,"store_id":12,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-07T21:42:17.894861","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":3934,"_shape_event_time":"2022-12-07T21:42:17.894861"}
    {"order_id":2556,"customer_id":118,"store_id":1,"shipping_address_id":498,"promotion_id":null,"order_date":"2022-12-08T13:33:37.000000","status":"completed","order_total":478.13,"_shape_table":"order","_shape_seq":2555,"_shape_event_time":"2022-12-08T13:33:37.000000"}
    {"order_id":3678,"customer_id":263,"store_id":1,"shipping_address_id":794,"promotion_id":null,"order_date":"2022-12-09T10:11:42.980860","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3677,"_shape_event_time":"2022-12-09T10:11:42.980860"}
    {"order_id":2104,"customer_id":117,"store_id":2,"shipping_address_id":530,"promotion_id":null,"order_date":"2022-12-10T08:01:46.000000","status":"returned","order_total":478.88,"_shape_table":"order","_shape_seq":2103,"_shape_event_time":"2022-12-10T08:01:46.000000"}
    {"order_id":1247,"customer_id":630,"store_id":1,"shipping_address_id":508,"promotion_id":null,"order_date":"2022-12-10T14:00:00.810960","status":"shipped","order_total":0.0,"_shape_table":"order","_shape_seq":1246,"_shape_event_time":"2022-12-10T14:00:00.810960"}
    {"order_id":2003,"customer_id":630,"store_id":128,"shipping_address_id":508,"promotion_id":null,"order_date":"2022-12-10T14:00:00.810960","status":"completed","order_total":47.89,"_shape_table":"order","_shape_seq":2002,"_shape_event_time":"2022-12-10T14:00:00.810960"}
    {"order_id":1715,"customer_id":48,"store_id":7,"shipping_address_id":1348,"promotion_id":null,"order_date":"2022-12-12T06:04:16.000000","status":"completed","order_total":79.42,"_shape_table":"order","_shape_seq":1714,"_shape_event_time":"2022-12-12T06:04:16.000000"}
    {"order_id":1429,"customer_id":862,"store_id":1,"shipping_address_id":1449,"promotion_id":132,"order_date":"2022-12-12T18:01:36.845586","status":"completed","order_total":22.8,"_shape_table":"order","_shape_seq":1428,"_shape_event_time":"2022-12-12T18:01:36.845586"}
    {"order_id":2939,"customer_id":862,"store_id":1,"shipping_address_id":917,"promotion_id":null,"order_date":"2022-12-12T18:01:36.845586","status":"completed","order_total":93.31,"_shape_table":"order","_shape_seq":2938,"_shape_event_time":"2022-12-12T18:01:36.845586"}
    {"order_id":4864,"customer_id":862,"store_id":11,"shipping_address_id":917,"promotion_id":null,"order_date":"2022-12-12T18:01:36.845586","status":"completed","order_total":23.5,"_shape_table":"order","_shape_seq":4863,"_shape_event_time":"2022-12-12T18:01:36.845586"}
    {"order_id":3633,"customer_id":200,"store_id":7,"shipping_address_id":1038,"promotion_id":null,"order_date":"2022-12-14T10:25:38.000000","status":"shipped","order_total":29.76,"_shape_table":"order","_shape_seq":3632,"_shape_event_time":"2022-12-14T10:25:38.000000"}
    {"order_id":1608,"customer_id":885,"store_id":3,"shipping_address_id":892,"promotion_id":55,"order_date":"2022-12-14T14:38:49.000000","status":"completed","order_total":172.99,"_shape_table":"order","_shape_seq":1607,"_shape_event_time":"2022-12-14T14:38:49.000000"}
    {"order_id":13,"customer_id":4,"store_id":19,"shipping_address_id":1091,"promotion_id":null,"order_date":"2022-12-14T22:00:24.000000","status":"completed","order_total":15.21,"_shape_table":"order","_shape_seq":12,"_shape_event_time":"2022-12-14T22:00:24.000000"}
    {"order_id":752,"customer_id":943,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-15T02:19:56.081103","status":"completed","order_total":24.93,"_shape_table":"order","_shape_seq":751,"_shape_event_time":"2022-12-15T02:19:56.081103"}
    {"order_id":3409,"customer_id":943,"store_id":8,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-15T02:19:56.081103","status":"returned","order_total":76.02,"_shape_table":"order","_shape_seq":3408,"_shape_event_time":"2022-12-15T02:19:56.081103"}
    {"order_id":4809,"customer_id":943,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-15T02:19:56.081103","status":"returned","order_total":0.0,"_shape_table":"order","_shape_seq":4808,"_shape_event_time":"2022-12-15T02:19:56.081103"}
    {"order_id":665,"customer_id":13,"store_id":1,"shipping_address_id":1241,"promotion_id":null,"order_date":"2022-12-16T03:14:58.000000","status":"completed","order_total":1118.84,"_shape_table":"order","_shape_seq":664,"_shape_event_time":"2022-12-16T03:14:58.000000"}
    {"order_id":4982,"customer_id":80,"store_id":1,"shipping_address_id":250,"promotion_id":159,"order_date":"2022-12-16T19:21:02.240100","status":"returned","order_total":5.12,"_shape_table":"order","_shape_seq":4981,"_shape_event_time":"2022-12-16T19:21:02.240100"}
    {"order_id":3287,"customer_id":967,"store_id":3,"shipping_address_id":651,"promotion_id":136,"order_date":"2022-12-17T20:36:13.367526","status":"returned","order_total":0.0,"_shape_table":"order","_shape_seq":3286,"_shape_event_time":"2022-12-17T20:36:13.367526"}
    {"order_id":1171,"customer_id":16,"store_id":23,"shipping_address_id":8,"promotion_id":null,"order_date":"2022-12-18T05:19:43.000000","status":"completed","order_total":195.89,"_shape_table":"order","_shape_seq":1170,"_shape_event_time":"2022-12-18T05:19:43.000000"}
    {"order_id":4561,"customer_id":798,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-20T02:37:00.913867","status":"completed","order_total":126.09,"_shape_table":"order","_shape_seq":4560,"_shape_event_time":"2022-12-20T02:37:00.913867"}
    {"order_id":4045,"customer_id":667,"store_id":1,"shipping_address_id":null,"promotion_id":116,"order_date":"2022-12-20T06:11:07.524845","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4044,"_shape_event_time":"2022-12-20T06:11:07.524845"}
    {"order_id":4916,"customer_id":881,"store_id":2,"shipping_address_id":364,"promotion_id":null,"order_date":"2022-12-20T10:36:25.000000","status":"processing","order_total":41.53,"_shape_table":"order","_shape_seq":4915,"_shape_event_time":"2022-12-20T10:36:25.000000"}
    {"order_id":2439,"customer_id":677,"store_id":2,"shipping_address_id":1102,"promotion_id":null,"order_date":"2022-12-20T23:39:13.418918","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":2438,"_shape_event_time":"2022-12-20T23:39:13.418918"}
    {"order_id":2485,"customer_id":677,"store_id":4,"shipping_address_id":1102,"promotion_id":null,"order_date":"2022-12-20T23:39:13.418918","status":"completed","order_total":90.51,"_shape_table":"order","_shape_seq":2484,"_shape_event_time":"2022-12-20T23:39:13.418918"}
    {"order_id":3417,"customer_id":677,"store_id":11,"shipping_address_id":1102,"promotion_id":177,"order_date":"2022-12-20T23:39:13.418918","status":"returned","order_total":40.87,"_shape_table":"order","_shape_seq":3416,"_shape_event_time":"2022-12-20T23:39:13.418918"}
    {"order_id":3951,"customer_id":677,"store_id":47,"shipping_address_id":1102,"promotion_id":null,"order_date":"2022-12-20T23:39:13.418918","status":"completed","order_total":54.3,"_shape_table":"order","_shape_seq":3950,"_shape_event_time":"2022-12-20T23:39:13.418918"}
    {"order_id":727,"customer_id":4,"store_id":1,"shipping_address_id":1091,"promotion_id":148,"order_date":"2022-12-20T23:57:35.000000","status":"completed","order_total":141.22,"_shape_table":"order","_shape_seq":726,"_shape_event_time":"2022-12-20T23:57:35.000000"}
    {"order_id":2062,"customer_id":734,"store_id":82,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-21T04:05:02.896850","status":"completed","order_total":28.53,"_shape_table":"order","_shape_seq":2061,"_shape_event_time":"2022-12-21T04:05:02.896850"}
    {"order_id":3055,"customer_id":734,"store_id":52,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-21T04:05:02.896850","status":"completed","order_total":121.59,"_shape_table":"order","_shape_seq":3054,"_shape_event_time":"2022-12-21T04:05:02.896850"}
    {"order_id":1471,"customer_id":615,"store_id":1,"shipping_address_id":399,"promotion_id":90,"order_date":"2022-12-21T11:09:09.095410","status":"completed","order_total":15.75,"_shape_table":"order","_shape_seq":1470,"_shape_event_time":"2022-12-21T11:09:09.095410"}
    {"order_id":1917,"customer_id":615,"store_id":1,"shipping_address_id":399,"promotion_id":null,"order_date":"2022-12-21T11:09:09.095410","status":"completed","order_total":27.39,"_shape_table":"order","_shape_seq":1916,"_shape_event_time":"2022-12-21T11:09:09.095410"}
    {"order_id":2229,"customer_id":757,"store_id":31,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-21T17:36:00.000000","status":"completed","order_total":27.93,"_shape_table":"order","_shape_seq":2228,"_shape_event_time":"2022-12-21T17:36:00.000000"}
    {"order_id":4194,"customer_id":697,"store_id":1,"shipping_address_id":1098,"promotion_id":46,"order_date":"2022-12-21T22:22:50.000000","status":"shipped","order_total":57.73,"_shape_table":"order","_shape_seq":4193,"_shape_event_time":"2022-12-21T22:22:50.000000"}
    {"order_id":2501,"customer_id":20,"store_id":2,"shipping_address_id":102,"promotion_id":190,"order_date":"2022-12-22T15:02:11.000000","status":"completed","order_total":393.22,"_shape_table":"order","_shape_seq":2500,"_shape_event_time":"2022-12-22T15:02:11.000000"}
    {"order_id":3916,"customer_id":968,"store_id":1,"shipping_address_id":600,"promotion_id":null,"order_date":"2022-12-23T13:08:20.000000","status":"returned","order_total":648.72,"_shape_table":"order","_shape_seq":3915,"_shape_event_time":"2022-12-23T13:08:20.000000"}
    {"order_id":4569,"customer_id":422,"store_id":14,"shipping_address_id":1400,"promotion_id":null,"order_date":"2022-12-23T19:54:07.649074","status":"completed","order_total":20.5,"_shape_table":"order","_shape_seq":4568,"_shape_event_time":"2022-12-23T19:54:07.649074"}
    {"order_id":4338,"customer_id":262,"store_id":20,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-24T09:37:08.000000","status":"returned","order_total":45.8,"_shape_table":"order","_shape_seq":4337,"_shape_event_time":"2022-12-24T09:37:08.000000"}
    {"order_id":3356,"customer_id":960,"store_id":1,"shipping_address_id":null,"promotion_id":98,"order_date":"2022-12-26T01:57:02.000000","status":"completed","order_total":207.2,"_shape_table":"order","_shape_seq":3355,"_shape_event_time":"2022-12-26T01:57:02.000000"}
    {"order_id":4011,"customer_id":25,"store_id":2,"shipping_address_id":450,"promotion_id":null,"order_date":"2022-12-26T14:21:04.000000","status":"returned","order_total":203.98,"_shape_table":"order","_shape_seq":4010,"_shape_event_time":"2022-12-26T14:21:04.000000"}
    {"order_id":4000,"customer_id":660,"store_id":68,"shipping_address_id":994,"promotion_id":169,"order_date":"2022-12-27T17:17:04.000000","status":"completed","order_total":90.41,"_shape_table":"order","_shape_seq":3999,"_shape_event_time":"2022-12-27T17:17:04.000000"}
    {"order_id":3776,"customer_id":38,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-28T10:18:51.000000","status":"completed","order_total":172.61,"_shape_table":"order","_shape_seq":3775,"_shape_event_time":"2022-12-28T10:18:51.000000"}
    {"order_id":1786,"customer_id":922,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-28T13:24:55.000000","status":"completed","order_total":118.23,"_shape_table":"order","_shape_seq":1785,"_shape_event_time":"2022-12-28T13:24:55.000000"}
    {"order_id":4656,"customer_id":962,"store_id":2,"shipping_address_id":1389,"promotion_id":null,"order_date":"2022-12-28T22:55:25.000000","status":"completed","order_total":15.49,"_shape_table":"order","_shape_seq":4655,"_shape_event_time":"2022-12-28T22:55:25.000000"}
    {"order_id":975,"customer_id":326,"store_id":5,"shipping_address_id":null,"promotion_id":null,"order_date":"2022-12-29T19:30:01.000000","status":"completed","order_total":193.52,"_shape_table":"order","_shape_seq":974,"_shape_event_time":"2022-12-29T19:30:01.000000"}
    {"order_id":1356,"customer_id":9,"store_id":2,"shipping_address_id":1070,"promotion_id":null,"order_date":"2022-12-29T23:49:04.000000","status":"completed","order_total":105.08,"_shape_table":"order","_shape_seq":1355,"_shape_event_time":"2022-12-29T23:49:04.000000"}
    {"order_id":32,"customer_id":6,"store_id":1,"shipping_address_id":823,"promotion_id":null,"order_date":"2022-12-30T00:16:05.000000","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":31,"_shape_event_time":"2022-12-30T00:16:05.000000"}
    {"order_id":3844,"customer_id":250,"store_id":1,"shipping_address_id":654,"promotion_id":null,"order_date":"2022-12-30T01:33:08.000000","status":"completed","order_total":121.43,"_shape_table":"order","_shape_seq":3843,"_shape_event_time":"2022-12-30T01:33:08.000000"}
    {"order_id":368,"customer_id":15,"store_id":4,"shipping_address_id":695,"promotion_id":null,"order_date":"2022-12-31T13:54:19.000000","status":"completed","order_total":48.47,"_shape_table":"order","_shape_seq":367,"_shape_event_time":"2022-12-31T13:54:19.000000"}
    {"order_id":351,"customer_id":25,"store_id":3,"shipping_address_id":450,"promotion_id":98,"order_date":"2023-01-01T00:18:53.000000","status":"completed","order_total":211.31,"_shape_table":"order","_shape_seq":350,"_shape_event_time":"2023-01-01T00:18:53.000000"}
    {"order_id":1534,"customer_id":120,"store_id":2,"shipping_address_id":204,"promotion_id":null,"order_date":"2023-01-03T13:21:10.000000","status":"completed","order_total":74.12,"_shape_table":"order","_shape_seq":1533,"_shape_event_time":"2023-01-03T13:21:10.000000"}
    {"order_id":1669,"customer_id":558,"store_id":2,"shipping_address_id":829,"promotion_id":null,"order_date":"2023-01-04T04:06:47.427603","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":1668,"_shape_event_time":"2023-01-04T04:06:47.427603"}
    {"order_id":3976,"customer_id":575,"store_id":1,"shipping_address_id":496,"promotion_id":null,"order_date":"2023-01-04T05:59:40.000000","status":"completed","order_total":70.5,"_shape_table":"order","_shape_seq":3975,"_shape_event_time":"2023-01-04T05:59:40.000000"}
    {"order_id":3657,"customer_id":26,"store_id":2,"shipping_address_id":66,"promotion_id":null,"order_date":"2023-01-05T03:00:58.000000","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":3656,"_shape_event_time":"2023-01-05T03:00:58.000000"}
    {"order_id":3144,"customer_id":41,"store_id":5,"shipping_address_id":1154,"promotion_id":null,"order_date":"2023-01-05T16:27:42.000000","status":"completed","order_total":7.03,"_shape_table":"order","_shape_seq":3143,"_shape_event_time":"2023-01-05T16:27:42.000000"}
    {"order_id":4694,"customer_id":443,"store_id":41,"shipping_address_id":90,"promotion_id":null,"order_date":"2023-01-06T03:19:54.497442","status":"completed","order_total":55.69,"_shape_table":"order","_shape_seq":4693,"_shape_event_time":"2023-01-06T03:19:54.497442"}
    {"order_id":4892,"customer_id":41,"store_id":4,"shipping_address_id":1154,"promotion_id":null,"order_date":"2023-01-08T13:23:43.000000","status":"returned","order_total":44.04,"_shape_table":"order","_shape_seq":4891,"_shape_event_time":"2023-01-08T13:23:43.000000"}
    {"order_id":4435,"customer_id":419,"store_id":5,"shipping_address_id":464,"promotion_id":49,"order_date":"2023-01-08T20:35:08.000000","status":"shipped","order_total":19.97,"_shape_table":"order","_shape_seq":4434,"_shape_event_time":"2023-01-08T20:35:08.000000"}
    {"order_id":395,"customer_id":48,"store_id":1,"shipping_address_id":1348,"promotion_id":null,"order_date":"2023-01-09T00:06:32.000000","status":"completed","order_total":290.24,"_shape_table":"order","_shape_seq":394,"_shape_event_time":"2023-01-09T00:06:32.000000"}
    {"order_id":3824,"customer_id":941,"store_id":132,"shipping_address_id":1257,"promotion_id":133,"order_date":"2023-01-09T02:23:32.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3823,"_shape_event_time":"2023-01-09T02:23:32.000000"}
    {"order_id":112,"customer_id":4,"store_id":1,"shipping_address_id":1091,"promotion_id":null,"order_date":"2023-01-09T13:12:29.000000","status":"returned","order_total":1581.75,"_shape_table":"order","_shape_seq":111,"_shape_event_time":"2023-01-09T13:12:29.000000"}
    {"order_id":97,"customer_id":4,"store_id":5,"shipping_address_id":1091,"promotion_id":null,"order_date":"2023-01-09T15:59:56.000000","status":"completed","order_total":239.52,"_shape_table":"order","_shape_seq":96,"_shape_event_time":"2023-01-09T15:59:56.000000"}
    {"order_id":2039,"customer_id":854,"store_id":3,"shipping_address_id":1455,"promotion_id":null,"order_date":"2023-01-10T06:44:24.734942","status":"completed","order_total":463.23,"_shape_table":"order","_shape_seq":2038,"_shape_event_time":"2023-01-10T06:44:24.734942"}
    {"order_id":2194,"customer_id":854,"store_id":29,"shipping_address_id":1455,"promotion_id":null,"order_date":"2023-01-10T06:44:24.734942","status":"completed","order_total":53.97,"_shape_table":"order","_shape_seq":2193,"_shape_event_time":"2023-01-10T06:44:24.734942"}
    {"order_id":4993,"customer_id":854,"store_id":1,"shipping_address_id":1455,"promotion_id":null,"order_date":"2023-01-10T06:44:24.734942","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4992,"_shape_event_time":"2023-01-10T06:44:24.734942"}
    {"order_id":1006,"customer_id":926,"store_id":31,"shipping_address_id":706,"promotion_id":null,"order_date":"2023-01-11T07:54:54.459716","status":"completed","order_total":51.25,"_shape_table":"order","_shape_seq":1005,"_shape_event_time":"2023-01-11T07:54:54.459716"}
    {"order_id":249,"customer_id":6,"store_id":1,"shipping_address_id":823,"promotion_id":null,"order_date":"2023-01-11T11:48:34.000000","status":"completed","order_total":22.15,"_shape_table":"order","_shape_seq":248,"_shape_event_time":"2023-01-11T11:48:34.000000"}
    {"order_id":4953,"customer_id":80,"store_id":127,"shipping_address_id":250,"promotion_id":22,"order_date":"2023-01-13T14:57:53.000000","status":"returned","order_total":102.07,"_shape_table":"order","_shape_seq":4952,"_shape_event_time":"2023-01-13T14:57:53.000000"}
    {"order_id":1057,"customer_id":235,"store_id":63,"shipping_address_id":1231,"promotion_id":null,"order_date":"2023-01-13T19:51:41.921163","status":"completed","order_total":31.12,"_shape_table":"order","_shape_seq":1056,"_shape_event_time":"2023-01-13T19:51:41.921163"}
    {"order_id":3174,"customer_id":235,"store_id":3,"shipping_address_id":755,"promotion_id":null,"order_date":"2023-01-13T19:51:41.921163","status":"completed","order_total":243.39,"_shape_table":"order","_shape_seq":3173,"_shape_event_time":"2023-01-13T19:51:41.921163"}
    {"order_id":2034,"customer_id":16,"store_id":24,"shipping_address_id":900,"promotion_id":null,"order_date":"2023-01-15T15:23:12.000000","status":"shipped","order_total":151.49,"_shape_table":"order","_shape_seq":2033,"_shape_event_time":"2023-01-15T15:23:12.000000"}
    {"order_id":1355,"customer_id":25,"store_id":4,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-01-17T03:08:53.000000","status":"completed","order_total":22.15,"_shape_table":"order","_shape_seq":1354,"_shape_event_time":"2023-01-17T03:08:53.000000"}
    {"order_id":540,"customer_id":37,"store_id":40,"shipping_address_id":735,"promotion_id":null,"order_date":"2023-01-18T10:17:28.000000","status":"completed","order_total":51.81,"_shape_table":"order","_shape_seq":539,"_shape_event_time":"2023-01-18T10:17:28.000000"}
    {"order_id":57,"customer_id":28,"store_id":3,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":58.73,"_shape_table":"order","_shape_seq":56,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":771,"customer_id":28,"store_id":1,"shipping_address_id":427,"promotion_id":122,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":54.62,"_shape_table":"order","_shape_seq":770,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":1081,"customer_id":28,"store_id":19,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":18.49,"_shape_table":"order","_shape_seq":1080,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":2056,"customer_id":28,"store_id":1,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":126.3,"_shape_table":"order","_shape_seq":2055,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":2575,"customer_id":28,"store_id":1,"shipping_address_id":427,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"returned","order_total":53.21,"_shape_table":"order","_shape_seq":2574,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":2923,"customer_id":28,"store_id":3,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":91.63,"_shape_table":"order","_shape_seq":2922,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":3049,"customer_id":28,"store_id":3,"shipping_address_id":427,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":3048,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":3136,"customer_id":28,"store_id":5,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3135,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":3852,"customer_id":28,"store_id":2,"shipping_address_id":427,"promotion_id":154,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":61.76,"_shape_table":"order","_shape_seq":3851,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":4115,"customer_id":28,"store_id":3,"shipping_address_id":427,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":23.5,"_shape_table":"order","_shape_seq":4114,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":4162,"customer_id":28,"store_id":6,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-01-18T17:48:59.891020","status":"completed","order_total":29.36,"_shape_table":"order","_shape_seq":4161,"_shape_event_time":"2023-01-18T17:48:59.891020"}
    {"order_id":4625,"customer_id":970,"store_id":1,"shipping_address_id":771,"promotion_id":141,"order_date":"2023-01-20T16:20:50.000000","status":"completed","order_total":228.94,"_shape_table":"order","_shape_seq":4624,"_shape_event_time":"2023-01-20T16:20:50.000000"}
    {"order_id":2632,"customer_id":106,"store_id":1,"shipping_address_id":125,"promotion_id":null,"order_date":"2023-01-21T11:01:57.274723","status":"completed","order_total":10.25,"_shape_table":"order","_shape_seq":2631,"_shape_event_time":"2023-01-21T11:01:57.274723"}
    {"order_id":2809,"customer_id":191,"store_id":13,"shipping_address_id":390,"promotion_id":null,"order_date":"2023-01-23T10:01:10.337102","status":"completed","order_total":1094.49,"_shape_table":"order","_shape_seq":2808,"_shape_event_time":"2023-01-23T10:01:10.337102"}
    {"order_id":4131,"customer_id":191,"store_id":59,"shipping_address_id":193,"promotion_id":null,"order_date":"2023-01-23T10:01:10.337102","status":"returned","order_total":55.69,"_shape_table":"order","_shape_seq":4130,"_shape_event_time":"2023-01-23T10:01:10.337102"}
    {"order_id":890,"customer_id":853,"store_id":1,"shipping_address_id":106,"promotion_id":null,"order_date":"2023-01-23T12:19:02.000000","status":"completed","order_total":115.77,"_shape_table":"order","_shape_seq":889,"_shape_event_time":"2023-01-23T12:19:02.000000"}
    {"order_id":1071,"customer_id":13,"store_id":1,"shipping_address_id":559,"promotion_id":32,"order_date":"2023-01-24T23:45:52.000000","status":"completed","order_total":90.81,"_shape_table":"order","_shape_seq":1070,"_shape_event_time":"2023-01-24T23:45:52.000000"}
    {"order_id":4828,"customer_id":37,"store_id":3,"shipping_address_id":1402,"promotion_id":null,"order_date":"2023-01-25T18:17:49.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4827,"_shape_event_time":"2023-01-25T18:17:49.000000"}
    {"order_id":2998,"customer_id":885,"store_id":28,"shipping_address_id":892,"promotion_id":146,"order_date":"2023-01-26T00:32:15.000000","status":"processing","order_total":29.21,"_shape_table":"order","_shape_seq":2997,"_shape_event_time":"2023-01-26T00:32:15.000000"}
    {"order_id":1411,"customer_id":990,"store_id":2,"shipping_address_id":null,"promotion_id":143,"order_date":"2023-01-26T17:23:07.088004","status":"completed","order_total":7.97,"_shape_table":"order","_shape_seq":1410,"_shape_event_time":"2023-01-26T17:23:07.088004"}
    {"order_id":596,"customer_id":17,"store_id":23,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"completed","order_total":124.65,"_shape_table":"order","_shape_seq":595,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":646,"customer_id":17,"store_id":42,"shipping_address_id":981,"promotion_id":178,"order_date":"2023-01-28T03:19:03.327059","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":645,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":898,"customer_id":17,"store_id":2,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"shipped","order_total":96.08,"_shape_table":"order","_shape_seq":897,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":1014,"customer_id":17,"store_id":1,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"returned","order_total":35.15,"_shape_table":"order","_shape_seq":1013,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":1455,"customer_id":17,"store_id":23,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"completed","order_total":88.3,"_shape_table":"order","_shape_seq":1454,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":1487,"customer_id":17,"store_id":1,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"processing","order_total":0.0,"_shape_table":"order","_shape_seq":1486,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":1614,"customer_id":17,"store_id":93,"shipping_address_id":981,"promotion_id":117,"order_date":"2023-01-28T03:19:03.327059","status":"completed","order_total":32.12,"_shape_table":"order","_shape_seq":1613,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":1946,"customer_id":17,"store_id":44,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"completed","order_total":101.96,"_shape_table":"order","_shape_seq":1945,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":2195,"customer_id":17,"store_id":1,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"shipped","order_total":94.79,"_shape_table":"order","_shape_seq":2194,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":2198,"customer_id":17,"store_id":3,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"completed","order_total":25.46,"_shape_table":"order","_shape_seq":2197,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":2413,"customer_id":17,"store_id":106,"shipping_address_id":981,"promotion_id":79,"order_date":"2023-01-28T03:19:03.327059","status":"shipped","order_total":376.25,"_shape_table":"order","_shape_seq":2412,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":2421,"customer_id":17,"store_id":4,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-01-28T03:19:03.327059","status":"completed","order_total":47.4,"_shape_table":"order","_shape_seq":2420,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":2533,"customer_id":17,"store_id":74,"shipping_address_id":981,"promotion_id":50,"order_date":"2023-01-28T03:19:03.327059","status":"completed","order_total":21.9,"_shape_table":"order","_shape_seq":2532,"_shape_event_time":"2023-01-28T03:19:03.327059"}
    {"order_id":2719,"customer_id":966,"store_id":5,"shipping_address_id":470,"promotion_id":null,"order_date":"2023-01-28T21:40:07.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2718,"_shape_event_time":"2023-01-28T21:40:07.000000"}
    {"order_id":3209,"customer_id":149,"store_id":1,"shipping_address_id":86,"promotion_id":185,"order_date":"2023-01-28T23:06:31.719329","status":"completed","order_total":7.08,"_shape_table":"order","_shape_seq":3208,"_shape_event_time":"2023-01-28T23:06:31.719329"}
    {"order_id":1009,"customer_id":15,"store_id":1,"shipping_address_id":695,"promotion_id":null,"order_date":"2023-01-31T09:30:33.000000","status":"completed","order_total":21.23,"_shape_table":"order","_shape_seq":1008,"_shape_event_time":"2023-01-31T09:30:33.000000"}
    {"order_id":918,"customer_id":26,"store_id":1,"shipping_address_id":39,"promotion_id":18,"order_date":"2023-01-31T09:50:23.000000","status":"completed","order_total":231.37,"_shape_table":"order","_shape_seq":917,"_shape_event_time":"2023-01-31T09:50:23.000000"}
    {"order_id":2209,"customer_id":230,"store_id":2,"shipping_address_id":1474,"promotion_id":null,"order_date":"2023-01-31T16:20:34.919314","status":"completed","order_total":41.29,"_shape_table":"order","_shape_seq":2208,"_shape_event_time":"2023-01-31T16:20:34.919314"}
    {"order_id":2012,"customer_id":348,"store_id":29,"shipping_address_id":809,"promotion_id":91,"order_date":"2023-01-31T21:07:39.834250","status":"returned","order_total":58.75,"_shape_table":"order","_shape_seq":2011,"_shape_event_time":"2023-01-31T21:07:39.834250"}
    {"order_id":2830,"customer_id":16,"store_id":1,"shipping_address_id":900,"promotion_id":null,"order_date":"2023-02-03T11:19:47.000000","status":"completed","order_total":684.78,"_shape_table":"order","_shape_seq":2829,"_shape_event_time":"2023-02-03T11:19:47.000000"}
    {"order_id":2961,"customer_id":400,"store_id":17,"shipping_address_id":null,"promotion_id":98,"order_date":"2023-02-03T21:07:07.000000","status":"cancelled","order_total":12.93,"_shape_table":"order","_shape_seq":2960,"_shape_event_time":"2023-02-03T21:07:07.000000"}
    {"order_id":889,"customer_id":497,"store_id":13,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-02-05T21:12:53.305641","status":"completed","order_total":144.71,"_shape_table":"order","_shape_seq":888,"_shape_event_time":"2023-02-05T21:12:53.305641"}
    {"order_id":2061,"customer_id":832,"store_id":38,"shipping_address_id":439,"promotion_id":null,"order_date":"2023-02-06T10:48:42.880272","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":2060,"_shape_event_time":"2023-02-06T10:48:42.880272"}
    {"order_id":3552,"customer_id":500,"store_id":2,"shipping_address_id":1197,"promotion_id":null,"order_date":"2023-02-06T17:05:56.000000","status":"completed","order_total":74.93,"_shape_table":"order","_shape_seq":3551,"_shape_event_time":"2023-02-06T17:05:56.000000"}
    {"order_id":2572,"customer_id":26,"store_id":6,"shipping_address_id":39,"promotion_id":null,"order_date":"2023-02-07T05:20:23.000000","status":"returned","order_total":0.0,"_shape_table":"order","_shape_seq":2571,"_shape_event_time":"2023-02-07T05:20:23.000000"}
    {"order_id":624,"customer_id":530,"store_id":2,"shipping_address_id":null,"promotion_id":107,"order_date":"2023-02-08T08:42:35.000000","status":"completed","order_total":37.6,"_shape_table":"order","_shape_seq":623,"_shape_event_time":"2023-02-08T08:42:35.000000"}
    {"order_id":4923,"customer_id":739,"store_id":3,"shipping_address_id":1221,"promotion_id":24,"order_date":"2023-02-08T11:48:28.000000","status":"cancelled","order_total":63.77,"_shape_table":"order","_shape_seq":4922,"_shape_event_time":"2023-02-08T11:48:28.000000"}
    {"order_id":696,"customer_id":92,"store_id":11,"shipping_address_id":933,"promotion_id":188,"order_date":"2023-02-08T16:31:17.000000","status":"completed","order_total":32.6,"_shape_table":"order","_shape_seq":695,"_shape_event_time":"2023-02-08T16:31:17.000000"}
    {"order_id":3266,"customer_id":71,"store_id":31,"shipping_address_id":911,"promotion_id":166,"order_date":"2023-02-08T21:54:29.380342","status":"processing","order_total":237.24,"_shape_table":"order","_shape_seq":3265,"_shape_event_time":"2023-02-08T21:54:29.380342"}
    {"order_id":3393,"customer_id":71,"store_id":3,"shipping_address_id":911,"promotion_id":null,"order_date":"2023-02-08T21:54:29.380342","status":"completed","order_total":288.6,"_shape_table":"order","_shape_seq":3392,"_shape_event_time":"2023-02-08T21:54:29.380342"}
    {"order_id":3448,"customer_id":71,"store_id":2,"shipping_address_id":911,"promotion_id":164,"order_date":"2023-02-08T21:54:29.380342","status":"completed","order_total":19.93,"_shape_table":"order","_shape_seq":3447,"_shape_event_time":"2023-02-08T21:54:29.380342"}
    {"order_id":4234,"customer_id":71,"store_id":1,"shipping_address_id":911,"promotion_id":null,"order_date":"2023-02-08T21:54:29.380342","status":"shipped","order_total":35.44,"_shape_table":"order","_shape_seq":4233,"_shape_event_time":"2023-02-08T21:54:29.380342"}
    {"order_id":4140,"customer_id":820,"store_id":70,"shipping_address_id":273,"promotion_id":null,"order_date":"2023-02-11T10:06:49.175051","status":"completed","order_total":47.0,"_shape_table":"order","_shape_seq":4139,"_shape_event_time":"2023-02-11T10:06:49.175051"}
    {"order_id":1295,"customer_id":744,"store_id":1,"shipping_address_id":1117,"promotion_id":162,"order_date":"2023-02-12T18:23:23.000000","status":"cancelled","order_total":19.82,"_shape_table":"order","_shape_seq":1294,"_shape_event_time":"2023-02-12T18:23:23.000000"}
    {"order_id":2677,"customer_id":26,"store_id":15,"shipping_address_id":39,"promotion_id":55,"order_date":"2023-02-12T20:54:47.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2676,"_shape_event_time":"2023-02-12T20:54:47.000000"}
    {"order_id":4149,"customer_id":305,"store_id":33,"shipping_address_id":533,"promotion_id":25,"order_date":"2023-02-12T21:50:13.000000","status":"completed","order_total":58.44,"_shape_table":"order","_shape_seq":4148,"_shape_event_time":"2023-02-12T21:50:13.000000"}
    {"order_id":2905,"customer_id":587,"store_id":8,"shipping_address_id":595,"promotion_id":null,"order_date":"2023-02-13T07:02:07.000000","status":"shipped","order_total":52.66,"_shape_table":"order","_shape_seq":2904,"_shape_event_time":"2023-02-13T07:02:07.000000"}
    {"order_id":3925,"customer_id":775,"store_id":1,"shipping_address_id":126,"promotion_id":null,"order_date":"2023-02-13T16:13:51.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3924,"_shape_event_time":"2023-02-13T16:13:51.000000"}
    {"order_id":2525,"customer_id":847,"store_id":2,"shipping_address_id":656,"promotion_id":null,"order_date":"2023-02-14T00:21:50.777001","status":"returned","order_total":13.29,"_shape_table":"order","_shape_seq":2524,"_shape_event_time":"2023-02-14T00:21:50.777001"}
    {"order_id":4786,"customer_id":574,"store_id":5,"shipping_address_id":186,"promotion_id":null,"order_date":"2023-02-14T15:50:39.834535","status":"completed","order_total":119.51,"_shape_table":"order","_shape_seq":4785,"_shape_event_time":"2023-02-14T15:50:39.834535"}
    {"order_id":3750,"customer_id":84,"store_id":4,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-02-15T05:17:06.000000","status":"returned","order_total":41.79,"_shape_table":"order","_shape_seq":3749,"_shape_event_time":"2023-02-15T05:17:06.000000"}
    {"order_id":3876,"customer_id":962,"store_id":40,"shipping_address_id":827,"promotion_id":98,"order_date":"2023-02-16T10:08:31.000000","status":"completed","order_total":7.54,"_shape_table":"order","_shape_seq":3875,"_shape_event_time":"2023-02-16T10:08:31.000000"}
    {"order_id":1547,"customer_id":795,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-02-17T11:51:38.162647","status":"completed","order_total":228.16,"_shape_table":"order","_shape_seq":1546,"_shape_event_time":"2023-02-17T11:51:38.162647"}
    {"order_id":3450,"customer_id":548,"store_id":20,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-02-18T08:59:07.000000","status":"completed","order_total":13.4,"_shape_table":"order","_shape_seq":3449,"_shape_event_time":"2023-02-18T08:59:07.000000"}
    {"order_id":206,"customer_id":27,"store_id":8,"shipping_address_id":67,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"returned","order_total":0.0,"_shape_table":"order","_shape_seq":205,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":745,"customer_id":27,"store_id":1,"shipping_address_id":785,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":432.48,"_shape_table":"order","_shape_seq":744,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":1509,"customer_id":27,"store_id":1,"shipping_address_id":785,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":1508,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":1581,"customer_id":27,"store_id":4,"shipping_address_id":67,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":121.93,"_shape_table":"order","_shape_seq":1580,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":1724,"customer_id":27,"store_id":2,"shipping_address_id":67,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"returned","order_total":13.29,"_shape_table":"order","_shape_seq":1723,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":1863,"customer_id":27,"store_id":10,"shipping_address_id":67,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":136.24,"_shape_table":"order","_shape_seq":1862,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":2124,"customer_id":27,"store_id":1,"shipping_address_id":675,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":215.16,"_shape_table":"order","_shape_seq":2123,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":2904,"customer_id":27,"store_id":21,"shipping_address_id":67,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":129.45,"_shape_table":"order","_shape_seq":2903,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":2907,"customer_id":27,"store_id":3,"shipping_address_id":67,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":28.5,"_shape_table":"order","_shape_seq":2906,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":4097,"customer_id":27,"store_id":10,"shipping_address_id":675,"promotion_id":132,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4096,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":4720,"customer_id":27,"store_id":4,"shipping_address_id":675,"promotion_id":null,"order_date":"2023-02-18T17:13:15.907302","status":"completed","order_total":985.6,"_shape_table":"order","_shape_seq":4719,"_shape_event_time":"2023-02-18T17:13:15.907302"}
    {"order_id":3226,"customer_id":195,"store_id":57,"shipping_address_id":1219,"promotion_id":197,"order_date":"2023-02-18T22:24:29.824672","status":"completed","order_total":35.3,"_shape_table":"order","_shape_seq":3225,"_shape_event_time":"2023-02-18T22:24:29.824672"}
    {"order_id":1412,"customer_id":9,"store_id":127,"shipping_address_id":1070,"promotion_id":null,"order_date":"2023-02-19T02:37:23.000000","status":"completed","order_total":47.58,"_shape_table":"order","_shape_seq":1411,"_shape_event_time":"2023-02-19T02:37:23.000000"}
    {"order_id":2944,"customer_id":374,"store_id":1,"shipping_address_id":9,"promotion_id":null,"order_date":"2023-02-19T09:57:20.000000","status":"shipped","order_total":333.02,"_shape_table":"order","_shape_seq":2943,"_shape_event_time":"2023-02-19T09:57:20.000000"}
    {"order_id":3616,"customer_id":841,"store_id":5,"shipping_address_id":401,"promotion_id":null,"order_date":"2023-02-20T09:49:06.000000","status":"cancelled","order_total":17.72,"_shape_table":"order","_shape_seq":3615,"_shape_event_time":"2023-02-20T09:49:06.000000"}
    {"order_id":4381,"customer_id":946,"store_id":137,"shipping_address_id":370,"promotion_id":null,"order_date":"2023-02-20T14:36:40.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":4380,"_shape_event_time":"2023-02-20T14:36:40.000000"}
    {"order_id":3322,"customer_id":28,"store_id":66,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-02-21T11:33:14.000000","status":"completed","order_total":51.48,"_shape_table":"order","_shape_seq":3321,"_shape_event_time":"2023-02-21T11:33:14.000000"}
    {"order_id":264,"customer_id":17,"store_id":1,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-02-22T00:49:39.000000","status":"completed","order_total":864.96,"_shape_table":"order","_shape_seq":263,"_shape_event_time":"2023-02-22T00:49:39.000000"}
    {"order_id":669,"customer_id":6,"store_id":17,"shipping_address_id":823,"promotion_id":null,"order_date":"2023-02-22T16:13:01.000000","status":"completed","order_total":459.73,"_shape_table":"order","_shape_seq":668,"_shape_event_time":"2023-02-22T16:13:01.000000"}
    {"order_id":793,"customer_id":6,"store_id":5,"shipping_address_id":823,"promotion_id":null,"order_date":"2023-02-22T17:09:02.000000","status":"completed","order_total":41.37,"_shape_table":"order","_shape_seq":792,"_shape_event_time":"2023-02-22T17:09:02.000000"}
    {"order_id":2312,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-02-23T20:19:43.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2311,"_shape_event_time":"2023-02-23T20:19:43.000000"}
    {"order_id":875,"customer_id":498,"store_id":1,"shipping_address_id":1223,"promotion_id":105,"order_date":"2023-02-24T12:34:19.000000","status":"completed","order_total":38.44,"_shape_table":"order","_shape_seq":874,"_shape_event_time":"2023-02-24T12:34:19.000000"}
    {"order_id":1189,"customer_id":476,"store_id":1,"shipping_address_id":1244,"promotion_id":null,"order_date":"2023-02-25T15:56:12.223384","status":"completed","order_total":86.3,"_shape_table":"order","_shape_seq":1188,"_shape_event_time":"2023-02-25T15:56:12.223384"}
    {"order_id":1294,"customer_id":476,"store_id":1,"shipping_address_id":570,"promotion_id":83,"order_date":"2023-02-25T15:56:12.223384","status":"completed","order_total":202.6,"_shape_table":"order","_shape_seq":1293,"_shape_event_time":"2023-02-25T15:56:12.223384"}
    {"order_id":3086,"customer_id":966,"store_id":18,"shipping_address_id":470,"promotion_id":null,"order_date":"2023-02-25T16:02:45.000000","status":"completed","order_total":89.31,"_shape_table":"order","_shape_seq":3085,"_shape_event_time":"2023-02-25T16:02:45.000000"}
    {"order_id":2742,"customer_id":235,"store_id":16,"shipping_address_id":755,"promotion_id":null,"order_date":"2023-02-26T22:36:19.000000","status":"completed","order_total":34.35,"_shape_table":"order","_shape_seq":2741,"_shape_event_time":"2023-02-26T22:36:19.000000"}
    {"order_id":3304,"customer_id":28,"store_id":3,"shipping_address_id":1464,"promotion_id":73,"order_date":"2023-02-27T02:14:57.000000","status":"completed","order_total":411.87,"_shape_table":"order","_shape_seq":3303,"_shape_event_time":"2023-02-27T02:14:57.000000"}
    {"order_id":1340,"customer_id":927,"store_id":1,"shipping_address_id":41,"promotion_id":null,"order_date":"2023-02-28T18:35:40.562817","status":"cancelled","order_total":75.53,"_shape_table":"order","_shape_seq":1339,"_shape_event_time":"2023-02-28T18:35:40.562817"}
    {"order_id":2047,"customer_id":927,"store_id":1,"shipping_address_id":41,"promotion_id":40,"order_date":"2023-02-28T18:35:40.562817","status":"completed","order_total":19.76,"_shape_table":"order","_shape_seq":2046,"_shape_event_time":"2023-02-28T18:35:40.562817"}
    {"order_id":4201,"customer_id":927,"store_id":2,"shipping_address_id":41,"promotion_id":null,"order_date":"2023-02-28T18:35:40.562817","status":"completed","order_total":405.39,"_shape_table":"order","_shape_seq":4200,"_shape_event_time":"2023-02-28T18:35:40.562817"}
    {"order_id":4139,"customer_id":257,"store_id":2,"shipping_address_id":672,"promotion_id":null,"order_date":"2023-03-01T02:02:22.000000","status":"completed","order_total":24.93,"_shape_table":"order","_shape_seq":4138,"_shape_event_time":"2023-03-01T02:02:22.000000"}
    {"order_id":1263,"customer_id":512,"store_id":3,"shipping_address_id":1295,"promotion_id":null,"order_date":"2023-03-01T13:43:58.050744","status":"completed","order_total":122.02,"_shape_table":"order","_shape_seq":1262,"_shape_event_time":"2023-03-01T13:43:58.050744"}
    {"order_id":2334,"customer_id":760,"store_id":6,"shipping_address_id":1358,"promotion_id":null,"order_date":"2023-03-01T15:56:28.441292","status":"completed","order_total":17.72,"_shape_table":"order","_shape_seq":2333,"_shape_event_time":"2023-03-01T15:56:28.441292"}
    {"order_id":832,"customer_id":15,"store_id":88,"shipping_address_id":695,"promotion_id":53,"order_date":"2023-03-02T07:59:19.000000","status":"shipped","order_total":714.72,"_shape_table":"order","_shape_seq":831,"_shape_event_time":"2023-03-02T07:59:19.000000"}
    {"order_id":3826,"customer_id":209,"store_id":5,"shipping_address_id":665,"promotion_id":null,"order_date":"2023-03-02T16:02:25.092566","status":"completed","order_total":19.11,"_shape_table":"order","_shape_seq":3825,"_shape_event_time":"2023-03-02T16:02:25.092566"}
    {"order_id":362,"customer_id":25,"store_id":20,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-03-02T22:19:42.000000","status":"completed","order_total":488.17,"_shape_table":"order","_shape_seq":361,"_shape_event_time":"2023-03-02T22:19:42.000000"}
    {"order_id":726,"customer_id":3,"store_id":5,"shipping_address_id":746,"promotion_id":null,"order_date":"2023-03-03T14:47:34.000000","status":"shipped","order_total":58.4,"_shape_table":"order","_shape_seq":725,"_shape_event_time":"2023-03-03T14:47:34.000000"}
    {"order_id":3809,"customer_id":661,"store_id":1,"shipping_address_id":1413,"promotion_id":null,"order_date":"2023-03-03T19:51:10.000000","status":"completed","order_total":107.44,"_shape_table":"order","_shape_seq":3808,"_shape_event_time":"2023-03-03T19:51:10.000000"}
    {"order_id":986,"customer_id":146,"store_id":4,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-04T05:13:26.000000","status":"completed","order_total":59.53,"_shape_table":"order","_shape_seq":985,"_shape_event_time":"2023-03-04T05:13:26.000000"}
    {"order_id":4355,"customer_id":25,"store_id":46,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-03-05T02:38:45.000000","status":"returned","order_total":79.44,"_shape_table":"order","_shape_seq":4354,"_shape_event_time":"2023-03-05T02:38:45.000000"}
    {"order_id":2418,"customer_id":155,"store_id":4,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-05T08:56:17.517839","status":"completed","order_total":15.21,"_shape_table":"order","_shape_seq":2417,"_shape_event_time":"2023-03-05T08:56:17.517839"}
    {"order_id":2447,"customer_id":155,"store_id":4,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-05T08:56:17.517839","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2446,"_shape_event_time":"2023-03-05T08:56:17.517839"}
    {"order_id":2695,"customer_id":37,"store_id":37,"shipping_address_id":1484,"promotion_id":null,"order_date":"2023-03-06T01:45:24.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":2694,"_shape_event_time":"2023-03-06T01:45:24.000000"}
    {"order_id":4547,"customer_id":166,"store_id":15,"shipping_address_id":1152,"promotion_id":null,"order_date":"2023-03-06T02:31:47.901792","status":"completed","order_total":38.14,"_shape_table":"order","_shape_seq":4546,"_shape_event_time":"2023-03-06T02:31:47.901792"}
    {"order_id":1596,"customer_id":426,"store_id":123,"shipping_address_id":1138,"promotion_id":null,"order_date":"2023-03-06T07:36:50.281696","status":"completed","order_total":186.42,"_shape_table":"order","_shape_seq":1595,"_shape_event_time":"2023-03-06T07:36:50.281696"}
    {"order_id":1565,"customer_id":37,"store_id":1,"shipping_address_id":1484,"promotion_id":null,"order_date":"2023-03-06T14:35:20.000000","status":"completed","order_total":67.24,"_shape_table":"order","_shape_seq":1564,"_shape_event_time":"2023-03-06T14:35:20.000000"}
    {"order_id":1089,"customer_id":25,"store_id":2,"shipping_address_id":450,"promotion_id":67,"order_date":"2023-03-07T03:33:48.000000","status":"completed","order_total":103.64,"_shape_table":"order","_shape_seq":1088,"_shape_event_time":"2023-03-07T03:33:48.000000"}
    {"order_id":3267,"customer_id":17,"store_id":53,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-03-07T19:25:52.000000","status":"shipped","order_total":271.56,"_shape_table":"order","_shape_seq":3266,"_shape_event_time":"2023-03-07T19:25:52.000000"}
    {"order_id":172,"customer_id":25,"store_id":9,"shipping_address_id":450,"promotion_id":187,"order_date":"2023-03-07T20:58:57.000000","status":"completed","order_total":207.27,"_shape_table":"order","_shape_seq":171,"_shape_event_time":"2023-03-07T20:58:57.000000"}
    {"order_id":4995,"customer_id":554,"store_id":15,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-07T22:29:28.000000","status":"returned","order_total":50.35,"_shape_table":"order","_shape_seq":4994,"_shape_event_time":"2023-03-07T22:29:28.000000"}
    {"order_id":2216,"customer_id":831,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-08T03:19:49.000000","status":"completed","order_total":53.13,"_shape_table":"order","_shape_seq":2215,"_shape_event_time":"2023-03-08T03:19:49.000000"}
    {"order_id":779,"customer_id":13,"store_id":12,"shipping_address_id":559,"promotion_id":null,"order_date":"2023-03-08T10:37:29.000000","status":"completed","order_total":30.95,"_shape_table":"order","_shape_seq":778,"_shape_event_time":"2023-03-08T10:37:29.000000"}
    {"order_id":2074,"customer_id":13,"store_id":1,"shipping_address_id":1241,"promotion_id":42,"order_date":"2023-03-08T11:25:45.000000","status":"completed","order_total":73.09,"_shape_table":"order","_shape_seq":2073,"_shape_event_time":"2023-03-08T11:25:45.000000"}
    {"order_id":1652,"customer_id":269,"store_id":1,"shipping_address_id":716,"promotion_id":null,"order_date":"2023-03-09T03:43:12.016947","status":"completed","order_total":10.25,"_shape_table":"order","_shape_seq":1651,"_shape_event_time":"2023-03-09T03:43:12.016947"}
    {"order_id":4633,"customer_id":269,"store_id":139,"shipping_address_id":251,"promotion_id":null,"order_date":"2023-03-09T03:43:12.016947","status":"completed","order_total":665.19,"_shape_table":"order","_shape_seq":4632,"_shape_event_time":"2023-03-09T03:43:12.016947"}
    {"order_id":4645,"customer_id":269,"store_id":11,"shipping_address_id":716,"promotion_id":null,"order_date":"2023-03-09T03:43:12.016947","status":"completed","order_total":119.86,"_shape_table":"order","_shape_seq":4644,"_shape_event_time":"2023-03-09T03:43:12.016947"}
    {"order_id":2710,"customer_id":48,"store_id":2,"shipping_address_id":1348,"promotion_id":163,"order_date":"2023-03-09T04:26:03.000000","status":"returned","order_total":12.46,"_shape_table":"order","_shape_seq":2709,"_shape_event_time":"2023-03-09T04:26:03.000000"}
    {"order_id":484,"customer_id":34,"store_id":1,"shipping_address_id":702,"promotion_id":null,"order_date":"2023-03-09T12:40:16.000000","status":"returned","order_total":107.95,"_shape_table":"order","_shape_seq":483,"_shape_event_time":"2023-03-09T12:40:16.000000"}
    {"order_id":963,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2023-03-09T18:40:27.000000","status":"returned","order_total":175.64,"_shape_table":"order","_shape_seq":962,"_shape_event_time":"2023-03-09T18:40:27.000000"}
    {"order_id":2369,"customer_id":361,"store_id":2,"shipping_address_id":null,"promotion_id":104,"order_date":"2023-03-09T19:23:49.000000","status":"completed","order_total":19.92,"_shape_table":"order","_shape_seq":2368,"_shape_event_time":"2023-03-09T19:23:49.000000"}
    {"order_id":3911,"customer_id":780,"store_id":26,"shipping_address_id":1064,"promotion_id":null,"order_date":"2023-03-10T19:05:09.291646","status":"shipped","order_total":4.43,"_shape_table":"order","_shape_seq":3910,"_shape_event_time":"2023-03-10T19:05:09.291646"}
    {"order_id":2730,"customer_id":757,"store_id":3,"shipping_address_id":null,"promotion_id":65,"order_date":"2023-03-11T22:42:10.000000","status":"returned","order_total":96.34,"_shape_table":"order","_shape_seq":2729,"_shape_event_time":"2023-03-11T22:42:10.000000"}
    {"order_id":4047,"customer_id":56,"store_id":25,"shipping_address_id":1216,"promotion_id":null,"order_date":"2023-03-11T23:00:19.000000","status":"cancelled","order_total":445.77,"_shape_table":"order","_shape_seq":4046,"_shape_event_time":"2023-03-11T23:00:19.000000"}
    {"order_id":4170,"customer_id":273,"store_id":96,"shipping_address_id":283,"promotion_id":41,"order_date":"2023-03-12T23:20:06.000000","status":"completed","order_total":63.62,"_shape_table":"order","_shape_seq":4169,"_shape_event_time":"2023-03-12T23:20:06.000000"}
    {"order_id":2850,"customer_id":400,"store_id":2,"shipping_address_id":null,"promotion_id":37,"order_date":"2023-03-13T00:54:38.000000","status":"completed","order_total":46.47,"_shape_table":"order","_shape_seq":2849,"_shape_event_time":"2023-03-13T00:54:38.000000"}
    {"order_id":3089,"customer_id":971,"store_id":1,"shipping_address_id":790,"promotion_id":null,"order_date":"2023-03-14T11:33:05.000000","status":"completed","order_total":237.3,"_shape_table":"order","_shape_seq":3088,"_shape_event_time":"2023-03-14T11:33:05.000000"}
    {"order_id":962,"customer_id":76,"store_id":4,"shipping_address_id":1356,"promotion_id":null,"order_date":"2023-03-14T17:50:23.322952","status":"returned","order_total":62.26,"_shape_table":"order","_shape_seq":961,"_shape_event_time":"2023-03-14T17:50:23.322952"}
    {"order_id":2455,"customer_id":76,"store_id":2,"shipping_address_id":1356,"promotion_id":null,"order_date":"2023-03-14T17:50:23.322952","status":"returned","order_total":229.47,"_shape_table":"order","_shape_seq":2454,"_shape_event_time":"2023-03-14T17:50:23.322952"}
    {"order_id":3536,"customer_id":76,"store_id":24,"shipping_address_id":821,"promotion_id":151,"order_date":"2023-03-14T17:50:23.322952","status":"completed","order_total":6.1,"_shape_table":"order","_shape_seq":3535,"_shape_event_time":"2023-03-14T17:50:23.322952"}
    {"order_id":4819,"customer_id":76,"store_id":126,"shipping_address_id":821,"promotion_id":64,"order_date":"2023-03-14T17:50:23.322952","status":"completed","order_total":89.71,"_shape_table":"order","_shape_seq":4818,"_shape_event_time":"2023-03-14T17:50:23.322952"}
    {"order_id":4958,"customer_id":892,"store_id":8,"shipping_address_id":1168,"promotion_id":null,"order_date":"2023-03-16T07:12:37.712026","status":"shipped","order_total":48.47,"_shape_table":"order","_shape_seq":4957,"_shape_event_time":"2023-03-16T07:12:37.712026"}
    {"order_id":1885,"customer_id":15,"store_id":1,"shipping_address_id":695,"promotion_id":null,"order_date":"2023-03-16T08:24:46.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1884,"_shape_event_time":"2023-03-16T08:24:46.000000"}
    {"order_id":4396,"customer_id":27,"store_id":26,"shipping_address_id":785,"promotion_id":null,"order_date":"2023-03-16T08:31:43.000000","status":"returned","order_total":4.43,"_shape_table":"order","_shape_seq":4395,"_shape_event_time":"2023-03-16T08:31:43.000000"}
    {"order_id":2997,"customer_id":77,"store_id":5,"shipping_address_id":1253,"promotion_id":73,"order_date":"2023-03-17T03:32:20.000000","status":"completed","order_total":9.63,"_shape_table":"order","_shape_seq":2996,"_shape_event_time":"2023-03-17T03:32:20.000000"}
    {"order_id":2884,"customer_id":760,"store_id":4,"shipping_address_id":1358,"promotion_id":null,"order_date":"2023-03-17T09:05:47.000000","status":"completed","order_total":44.57,"_shape_table":"order","_shape_seq":2883,"_shape_event_time":"2023-03-17T09:05:47.000000"}
    {"order_id":3938,"customer_id":27,"store_id":2,"shipping_address_id":785,"promotion_id":null,"order_date":"2023-03-17T15:54:00.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3937,"_shape_event_time":"2023-03-17T15:54:00.000000"}
    {"order_id":1964,"customer_id":13,"store_id":14,"shipping_address_id":1241,"promotion_id":null,"order_date":"2023-03-17T17:10:47.000000","status":"completed","order_total":18.53,"_shape_table":"order","_shape_seq":1963,"_shape_event_time":"2023-03-17T17:10:47.000000"}
    {"order_id":2900,"customer_id":604,"store_id":7,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-21T06:50:56.000000","status":"completed","order_total":49.87,"_shape_table":"order","_shape_seq":2899,"_shape_event_time":"2023-03-21T06:50:56.000000"}
    {"order_id":2933,"customer_id":34,"store_id":1,"shipping_address_id":702,"promotion_id":79,"order_date":"2023-03-21T20:00:50.000000","status":"shipped","order_total":134.94,"_shape_table":"order","_shape_seq":2932,"_shape_event_time":"2023-03-21T20:00:50.000000"}
    {"order_id":3555,"customer_id":41,"store_id":58,"shipping_address_id":1154,"promotion_id":null,"order_date":"2023-03-21T21:09:45.000000","status":"shipped","order_total":22.15,"_shape_table":"order","_shape_seq":3554,"_shape_event_time":"2023-03-21T21:09:45.000000"}
    {"order_id":3387,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2023-03-22T04:53:58.000000","status":"completed","order_total":181.76,"_shape_table":"order","_shape_seq":3386,"_shape_event_time":"2023-03-22T04:53:58.000000"}
    {"order_id":288,"customer_id":4,"store_id":5,"shipping_address_id":1091,"promotion_id":null,"order_date":"2023-03-22T08:29:00.000000","status":"completed","order_total":35.18,"_shape_table":"order","_shape_seq":287,"_shape_event_time":"2023-03-22T08:29:00.000000"}
    {"order_id":2119,"customer_id":392,"store_id":1,"shipping_address_id":185,"promotion_id":null,"order_date":"2023-03-22T22:11:56.000000","status":"completed","order_total":45.77,"_shape_table":"order","_shape_seq":2118,"_shape_event_time":"2023-03-22T22:11:56.000000"}
    {"order_id":3235,"customer_id":334,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-24T01:19:33.000000","status":"completed","order_total":126.43,"_shape_table":"order","_shape_seq":3234,"_shape_event_time":"2023-03-24T01:19:33.000000"}
    {"order_id":1585,"customer_id":431,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-25T01:18:39.553625","status":"completed","order_total":60.29,"_shape_table":"order","_shape_seq":1584,"_shape_event_time":"2023-03-25T01:18:39.553625"}
    {"order_id":4092,"customer_id":431,"store_id":104,"shipping_address_id":null,"promotion_id":132,"order_date":"2023-03-25T01:18:39.553625","status":"completed","order_total":36.08,"_shape_table":"order","_shape_seq":4091,"_shape_event_time":"2023-03-25T01:18:39.553625"}
    {"order_id":4262,"customer_id":476,"store_id":1,"shipping_address_id":570,"promotion_id":null,"order_date":"2023-03-25T03:44:31.000000","status":"completed","order_total":27.39,"_shape_table":"order","_shape_seq":4261,"_shape_event_time":"2023-03-25T03:44:31.000000"}
    {"order_id":556,"customer_id":307,"store_id":26,"shipping_address_id":1431,"promotion_id":null,"order_date":"2023-03-25T14:26:46.313812","status":"completed","order_total":227.46,"_shape_table":"order","_shape_seq":555,"_shape_event_time":"2023-03-25T14:26:46.313812"}
    {"order_id":3631,"customer_id":307,"store_id":21,"shipping_address_id":1431,"promotion_id":null,"order_date":"2023-03-25T14:26:46.313812","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3630,"_shape_event_time":"2023-03-25T14:26:46.313812"}
    {"order_id":4987,"customer_id":25,"store_id":2,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-03-26T03:38:15.000000","status":"returned","order_total":176.56,"_shape_table":"order","_shape_seq":4986,"_shape_event_time":"2023-03-26T03:38:15.000000"}
    {"order_id":84,"customer_id":60,"store_id":1,"shipping_address_id":737,"promotion_id":155,"order_date":"2023-03-26T15:11:50.883631","status":"completed","order_total":7.42,"_shape_table":"order","_shape_seq":83,"_shape_event_time":"2023-03-26T15:11:50.883631"}
    {"order_id":1824,"customer_id":60,"store_id":1,"shipping_address_id":1331,"promotion_id":null,"order_date":"2023-03-26T15:11:50.883631","status":"shipped","order_total":8.86,"_shape_table":"order","_shape_seq":1823,"_shape_event_time":"2023-03-26T15:11:50.883631"}
    {"order_id":2597,"customer_id":60,"store_id":35,"shipping_address_id":737,"promotion_id":null,"order_date":"2023-03-26T15:11:50.883631","status":"completed","order_total":55.53,"_shape_table":"order","_shape_seq":2596,"_shape_event_time":"2023-03-26T15:11:50.883631"}
    {"order_id":3224,"customer_id":60,"store_id":1,"shipping_address_id":1331,"promotion_id":null,"order_date":"2023-03-26T15:11:50.883631","status":"completed","order_total":65.38,"_shape_table":"order","_shape_seq":3223,"_shape_event_time":"2023-03-26T15:11:50.883631"}
    {"order_id":674,"customer_id":145,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-26T18:56:17.042855","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":673,"_shape_event_time":"2023-03-26T18:56:17.042855"}
    {"order_id":1664,"customer_id":932,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-03-27T01:57:43.813701","status":"shipped","order_total":480.72,"_shape_table":"order","_shape_seq":1663,"_shape_event_time":"2023-03-27T01:57:43.813701"}
    {"order_id":4745,"customer_id":205,"store_id":8,"shipping_address_id":1051,"promotion_id":null,"order_date":"2023-03-28T03:37:29.000000","status":"completed","order_total":68.57,"_shape_table":"order","_shape_seq":4744,"_shape_event_time":"2023-03-28T03:37:29.000000"}
    {"order_id":308,"customer_id":4,"store_id":10,"shipping_address_id":684,"promotion_id":67,"order_date":"2023-03-29T07:38:50.000000","status":"shipped","order_total":23.29,"_shape_table":"order","_shape_seq":307,"_shape_event_time":"2023-03-29T07:38:50.000000"}
    {"order_id":2116,"customer_id":163,"store_id":1,"shipping_address_id":583,"promotion_id":null,"order_date":"2023-03-31T20:34:50.422126","status":"completed","order_total":22.34,"_shape_table":"order","_shape_seq":2115,"_shape_event_time":"2023-03-31T20:34:50.422126"}
    {"order_id":2182,"customer_id":163,"store_id":98,"shipping_address_id":839,"promotion_id":187,"order_date":"2023-03-31T20:34:50.422126","status":"cancelled","order_total":58.01,"_shape_table":"order","_shape_seq":2181,"_shape_event_time":"2023-03-31T20:34:50.422126"}
    {"order_id":3221,"customer_id":163,"store_id":1,"shipping_address_id":583,"promotion_id":null,"order_date":"2023-03-31T20:34:50.422126","status":"completed","order_total":46.62,"_shape_table":"order","_shape_seq":3220,"_shape_event_time":"2023-03-31T20:34:50.422126"}
    {"order_id":4907,"customer_id":418,"store_id":1,"shipping_address_id":740,"promotion_id":31,"order_date":"2023-03-31T20:38:04.000000","status":"completed","order_total":3.77,"_shape_table":"order","_shape_seq":4906,"_shape_event_time":"2023-03-31T20:38:04.000000"}
    {"order_id":1257,"customer_id":300,"store_id":1,"shipping_address_id":480,"promotion_id":null,"order_date":"2023-04-01T09:20:14.739917","status":"completed","order_total":96.01,"_shape_table":"order","_shape_seq":1256,"_shape_event_time":"2023-04-01T09:20:14.739917"}
    {"order_id":3258,"customer_id":300,"store_id":18,"shipping_address_id":874,"promotion_id":127,"order_date":"2023-04-01T09:20:14.739917","status":"shipped","order_total":33.19,"_shape_table":"order","_shape_seq":3257,"_shape_event_time":"2023-04-01T09:20:14.739917"}
    {"order_id":2129,"customer_id":73,"store_id":4,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-04-02T07:28:47.000000","status":"cancelled","order_total":0.0,"_shape_table":"order","_shape_seq":2128,"_shape_event_time":"2023-04-02T07:28:47.000000"}
    {"order_id":1215,"customer_id":163,"store_id":1,"shipping_address_id":396,"promotion_id":53,"order_date":"2023-04-02T13:11:31.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1214,"_shape_event_time":"2023-04-02T13:11:31.000000"}
    {"order_id":1037,"customer_id":111,"store_id":2,"shipping_address_id":34,"promotion_id":null,"order_date":"2023-04-03T02:36:28.000000","status":"completed","order_total":196.53,"_shape_table":"order","_shape_seq":1036,"_shape_event_time":"2023-04-03T02:36:28.000000"}
    {"order_id":2341,"customer_id":149,"store_id":5,"shipping_address_id":86,"promotion_id":null,"order_date":"2023-04-04T01:33:00.000000","status":"completed","order_total":26.58,"_shape_table":"order","_shape_seq":2340,"_shape_event_time":"2023-04-04T01:33:00.000000"}
    {"order_id":4626,"customer_id":774,"store_id":1,"shipping_address_id":null,"promotion_id":89,"order_date":"2023-04-04T03:00:54.064761","status":"completed","order_total":16.56,"_shape_table":"order","_shape_seq":4625,"_shape_event_time":"2023-04-04T03:00:54.064761"}
    {"order_id":2679,"customer_id":484,"store_id":44,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-04-04T03:03:52.515380","status":"shipped","order_total":112.81,"_shape_table":"order","_shape_seq":2678,"_shape_event_time":"2023-04-04T03:03:52.515380"}
    {"order_id":3636,"customer_id":484,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-04-04T03:03:52.515380","status":"completed","order_total":115.77,"_shape_table":"order","_shape_seq":3635,"_shape_event_time":"2023-04-04T03:03:52.515380"}
    {"order_id":3898,"customer_id":513,"store_id":5,"shipping_address_id":1286,"promotion_id":null,"order_date":"2023-04-04T07:59:32.274238","status":"completed","order_total":13.29,"_shape_table":"order","_shape_seq":3897,"_shape_event_time":"2023-04-04T07:59:32.274238"}
    {"order_id":4655,"customer_id":513,"store_id":1,"shipping_address_id":1310,"promotion_id":null,"order_date":"2023-04-04T07:59:32.274238","status":"cancelled","order_total":32.36,"_shape_table":"order","_shape_seq":4654,"_shape_event_time":"2023-04-04T07:59:32.274238"}
    {"order_id":363,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2023-04-05T20:05:09.000000","status":"completed","order_total":56.87,"_shape_table":"order","_shape_seq":362,"_shape_event_time":"2023-04-05T20:05:09.000000"}
    {"order_id":24,"customer_id":6,"store_id":64,"shipping_address_id":823,"promotion_id":null,"order_date":"2023-04-07T03:49:58.000000","status":"returned","order_total":4.43,"_shape_table":"order","_shape_seq":23,"_shape_event_time":"2023-04-07T03:49:58.000000"}
    {"order_id":3295,"customer_id":623,"store_id":1,"shipping_address_id":482,"promotion_id":4,"order_date":"2023-04-07T09:04:21.000000","status":"completed","order_total":403.15,"_shape_table":"order","_shape_seq":3294,"_shape_event_time":"2023-04-07T09:04:21.000000"}
    {"order_id":26,"customer_id":4,"store_id":2,"shipping_address_id":684,"promotion_id":null,"order_date":"2023-04-07T09:50:45.000000","status":"completed","order_total":50.35,"_shape_table":"order","_shape_seq":25,"_shape_event_time":"2023-04-07T09:50:45.000000"}
    {"order_id":3030,"customer_id":80,"store_id":41,"shipping_address_id":250,"promotion_id":null,"order_date":"2023-04-09T07:10:03.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":3029,"_shape_event_time":"2023-04-09T07:10:03.000000"}
    {"order_id":3227,"customer_id":998,"store_id":1,"shipping_address_id":835,"promotion_id":null,"order_date":"2023-04-09T10:10:18.045631","status":"completed","order_total":441.34,"_shape_table":"order","_shape_seq":3226,"_shape_event_time":"2023-04-09T10:10:18.045631"}
    {"order_id":2562,"customer_id":124,"store_id":6,"shipping_address_id":1333,"promotion_id":null,"order_date":"2023-04-09T22:41:38.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2561,"_shape_event_time":"2023-04-09T22:41:38.000000"}
    {"order_id":1612,"customer_id":34,"store_id":2,"shipping_address_id":702,"promotion_id":null,"order_date":"2023-04-10T14:13:18.000000","status":"completed","order_total":35.44,"_shape_table":"order","_shape_seq":1611,"_shape_event_time":"2023-04-10T14:13:18.000000"}
    {"order_id":1657,"customer_id":711,"store_id":1,"shipping_address_id":478,"promotion_id":100,"order_date":"2023-04-10T15:38:36.000000","status":"completed","order_total":17.12,"_shape_table":"order","_shape_seq":1656,"_shape_event_time":"2023-04-10T15:38:36.000000"}
    {"order_id":4242,"customer_id":649,"store_id":11,"shipping_address_id":683,"promotion_id":null,"order_date":"2023-04-10T19:25:24.594364","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4241,"_shape_event_time":"2023-04-10T19:25:24.594364"}
    {"order_id":559,"customer_id":3,"store_id":2,"shipping_address_id":746,"promotion_id":148,"order_date":"2023-04-10T20:31:34.000000","status":"completed","order_total":41.16,"_shape_table":"order","_shape_seq":558,"_shape_event_time":"2023-04-10T20:31:34.000000"}
    {"order_id":3210,"customer_id":367,"store_id":3,"shipping_address_id":540,"promotion_id":null,"order_date":"2023-04-11T14:00:16.803211","status":"completed","order_total":41.22,"_shape_table":"order","_shape_seq":3209,"_shape_event_time":"2023-04-11T14:00:16.803211"}
    {"order_id":4986,"customer_id":196,"store_id":14,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-04-11T22:09:27.000000","status":"completed","order_total":22.15,"_shape_table":"order","_shape_seq":4985,"_shape_event_time":"2023-04-11T22:09:27.000000"}
    {"order_id":4508,"customer_id":124,"store_id":2,"shipping_address_id":1333,"promotion_id":null,"order_date":"2023-04-12T20:25:59.000000","status":"completed","order_total":22.96,"_shape_table":"order","_shape_seq":4507,"_shape_event_time":"2023-04-12T20:25:59.000000"}
    {"order_id":3676,"customer_id":904,"store_id":2,"shipping_address_id":null,"promotion_id":187,"order_date":"2023-04-13T03:30:26.190020","status":"completed","order_total":55.51,"_shape_table":"order","_shape_seq":3675,"_shape_event_time":"2023-04-13T03:30:26.190020"}
    {"order_id":312,"customer_id":4,"store_id":5,"shipping_address_id":1091,"promotion_id":187,"order_date":"2023-04-13T03:53:20.000000","status":"completed","order_total":28.88,"_shape_table":"order","_shape_seq":311,"_shape_event_time":"2023-04-13T03:53:20.000000"}
    {"order_id":532,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":109,"order_date":"2023-04-14T08:38:55.000000","status":"completed","order_total":367.61,"_shape_table":"order","_shape_seq":531,"_shape_event_time":"2023-04-14T08:38:55.000000"}
    {"order_id":1020,"customer_id":896,"store_id":1,"shipping_address_id":220,"promotion_id":157,"order_date":"2023-04-14T09:04:05.763609","status":"completed","order_total":53.58,"_shape_table":"order","_shape_seq":1019,"_shape_event_time":"2023-04-14T09:04:05.763609"}
    {"order_id":1603,"customer_id":896,"store_id":1,"shipping_address_id":220,"promotion_id":null,"order_date":"2023-04-14T09:04:05.763609","status":"completed","order_total":17.72,"_shape_table":"order","_shape_seq":1602,"_shape_event_time":"2023-04-14T09:04:05.763609"}
    {"order_id":4650,"customer_id":896,"store_id":51,"shipping_address_id":403,"promotion_id":null,"order_date":"2023-04-14T09:04:05.763609","status":"completed","order_total":47.0,"_shape_table":"order","_shape_seq":4649,"_shape_event_time":"2023-04-14T09:04:05.763609"}
    {"order_id":3908,"customer_id":410,"store_id":61,"shipping_address_id":1184,"promotion_id":null,"order_date":"2023-04-17T18:02:06.268409","status":"completed","order_total":33.94,"_shape_table":"order","_shape_seq":3907,"_shape_event_time":"2023-04-17T18:02:06.268409"}
    {"order_id":4811,"customer_id":410,"store_id":30,"shipping_address_id":1184,"promotion_id":88,"order_date":"2023-04-17T18:02:06.268409","status":"completed","order_total":48.94,"_shape_table":"order","_shape_seq":4810,"_shape_event_time":"2023-04-17T18:02:06.268409"}
    {"order_id":1882,"customer_id":435,"store_id":7,"shipping_address_id":227,"promotion_id":null,"order_date":"2023-04-18T14:33:40.342594","status":"processing","order_total":35.49,"_shape_table":"order","_shape_seq":1881,"_shape_event_time":"2023-04-18T14:33:40.342594"}
    {"order_id":4659,"customer_id":435,"store_id":4,"shipping_address_id":227,"promotion_id":null,"order_date":"2023-04-18T14:33:40.342594","status":"completed","order_total":108.75,"_shape_table":"order","_shape_seq":4658,"_shape_event_time":"2023-04-18T14:33:40.342594"}
    {"order_id":3316,"customer_id":881,"store_id":3,"shipping_address_id":364,"promotion_id":null,"order_date":"2023-04-19T11:36:43.000000","status":"shipped","order_total":28.39,"_shape_table":"order","_shape_seq":3315,"_shape_event_time":"2023-04-19T11:36:43.000000"}
    {"order_id":2785,"customer_id":257,"store_id":1,"shipping_address_id":672,"promotion_id":null,"order_date":"2023-04-20T12:21:18.000000","status":"completed","order_total":22.15,"_shape_table":"order","_shape_seq":2784,"_shape_event_time":"2023-04-20T12:21:18.000000"}
    {"order_id":1379,"customer_id":220,"store_id":5,"shipping_address_id":799,"promotion_id":144,"order_date":"2023-04-21T01:28:07.000000","status":"completed","order_total":11.96,"_shape_table":"order","_shape_seq":1378,"_shape_event_time":"2023-04-21T01:28:07.000000"}
    {"order_id":870,"customer_id":27,"store_id":35,"shipping_address_id":785,"promotion_id":28,"order_date":"2023-04-21T01:47:21.000000","status":"completed","order_total":3.54,"_shape_table":"order","_shape_seq":869,"_shape_event_time":"2023-04-21T01:47:21.000000"}
    {"order_id":2001,"customer_id":527,"store_id":13,"shipping_address_id":44,"promotion_id":12,"order_date":"2023-04-21T08:29:29.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2000,"_shape_event_time":"2023-04-21T08:29:29.000000"}
    {"order_id":3440,"customer_id":92,"store_id":3,"shipping_address_id":933,"promotion_id":null,"order_date":"2023-04-21T08:44:42.000000","status":"completed","order_total":140.63,"_shape_table":"order","_shape_seq":3439,"_shape_event_time":"2023-04-21T08:44:42.000000"}
    {"order_id":95,"customer_id":33,"store_id":6,"shipping_address_id":632,"promotion_id":96,"order_date":"2023-04-23T02:39:41.643263","status":"completed","order_total":321.99,"_shape_table":"order","_shape_seq":94,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":122,"customer_id":33,"store_id":13,"shipping_address_id":632,"promotion_id":108,"order_date":"2023-04-23T02:39:41.643263","status":"completed","order_total":39.47,"_shape_table":"order","_shape_seq":121,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":904,"customer_id":33,"store_id":1,"shipping_address_id":632,"promotion_id":null,"order_date":"2023-04-23T02:39:41.643263","status":"shipped","order_total":81.91,"_shape_table":"order","_shape_seq":903,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":1110,"customer_id":33,"store_id":2,"shipping_address_id":301,"promotion_id":null,"order_date":"2023-04-23T02:39:41.643263","status":"shipped","order_total":66.59,"_shape_table":"order","_shape_seq":1109,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":1983,"customer_id":33,"store_id":1,"shipping_address_id":632,"promotion_id":null,"order_date":"2023-04-23T02:39:41.643263","status":"completed","order_total":220.67,"_shape_table":"order","_shape_seq":1982,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":3708,"customer_id":33,"store_id":1,"shipping_address_id":237,"promotion_id":null,"order_date":"2023-04-23T02:39:41.643263","status":"cancelled","order_total":487.7,"_shape_table":"order","_shape_seq":3707,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":4438,"customer_id":33,"store_id":3,"shipping_address_id":237,"promotion_id":null,"order_date":"2023-04-23T02:39:41.643263","status":"completed","order_total":132.66,"_shape_table":"order","_shape_seq":4437,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":4456,"customer_id":33,"store_id":1,"shipping_address_id":237,"promotion_id":null,"order_date":"2023-04-23T02:39:41.643263","status":"returned","order_total":4.43,"_shape_table":"order","_shape_seq":4455,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":4883,"customer_id":33,"store_id":1,"shipping_address_id":632,"promotion_id":null,"order_date":"2023-04-23T02:39:41.643263","status":"completed","order_total":244.17,"_shape_table":"order","_shape_seq":4882,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":4941,"customer_id":33,"store_id":18,"shipping_address_id":237,"promotion_id":46,"order_date":"2023-04-23T02:39:41.643263","status":"completed","order_total":358.95,"_shape_table":"order","_shape_seq":4940,"_shape_event_time":"2023-04-23T02:39:41.643263"}
    {"order_id":349,"customer_id":6,"store_id":5,"shipping_address_id":823,"promotion_id":null,"order_date":"2023-04-23T16:23:30.000000","status":"completed","order_total":263.32,"_shape_table":"order","_shape_seq":348,"_shape_event_time":"2023-04-23T16:23:30.000000"}
    {"order_id":1635,"customer_id":289,"store_id":4,"shipping_address_id":1468,"promotion_id":null,"order_date":"2023-04-24T22:25:19.370152","status":"completed","order_total":221.13,"_shape_table":"order","_shape_seq":1634,"_shape_event_time":"2023-04-24T22:25:19.370152"}
    {"order_id":3389,"customer_id":289,"store_id":4,"shipping_address_id":1468,"promotion_id":null,"order_date":"2023-04-24T22:25:19.370152","status":"completed","order_total":70.63,"_shape_table":"order","_shape_seq":3388,"_shape_event_time":"2023-04-24T22:25:19.370152"}
    {"order_id":4218,"customer_id":289,"store_id":1,"shipping_address_id":1468,"promotion_id":null,"order_date":"2023-04-24T22:25:19.370152","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":4217,"_shape_event_time":"2023-04-24T22:25:19.370152"}
    {"order_id":2021,"customer_id":547,"store_id":2,"shipping_address_id":1065,"promotion_id":null,"order_date":"2023-04-25T08:26:15.000000","status":"completed","order_total":84.88,"_shape_table":"order","_shape_seq":2020,"_shape_event_time":"2023-04-25T08:26:15.000000"}
    {"order_id":3596,"customer_id":312,"store_id":13,"shipping_address_id":850,"promotion_id":45,"order_date":"2023-04-25T09:20:25.285389","status":"completed","order_total":342.62,"_shape_table":"order","_shape_seq":3595,"_shape_event_time":"2023-04-25T09:20:25.285389"}
    {"order_id":2204,"customer_id":267,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-04-25T21:56:41.405318","status":"completed","order_total":45.44,"_shape_table":"order","_shape_seq":2203,"_shape_event_time":"2023-04-25T21:56:41.405318"}
    {"order_id":2970,"customer_id":27,"store_id":1,"shipping_address_id":675,"promotion_id":null,"order_date":"2023-04-27T23:30:58.000000","status":"completed","order_total":22.15,"_shape_table":"order","_shape_seq":2969,"_shape_event_time":"2023-04-27T23:30:58.000000"}
    {"order_id":326,"customer_id":4,"store_id":14,"shipping_address_id":1091,"promotion_id":146,"order_date":"2023-04-30T10:28:18.000000","status":"completed","order_total":69.1,"_shape_table":"order","_shape_seq":325,"_shape_event_time":"2023-04-30T10:28:18.000000"}
    {"order_id":179,"customer_id":16,"store_id":62,"shipping_address_id":900,"promotion_id":null,"order_date":"2023-04-30T14:32:08.000000","status":"completed","order_total":113.73,"_shape_table":"order","_shape_seq":178,"_shape_event_time":"2023-04-30T14:32:08.000000"}
    {"order_id":480,"customer_id":16,"store_id":6,"shipping_address_id":8,"promotion_id":null,"order_date":"2023-05-01T11:34:03.000000","status":"completed","order_total":116.81,"_shape_table":"order","_shape_seq":479,"_shape_event_time":"2023-05-01T11:34:03.000000"}
    {"order_id":3632,"customer_id":745,"store_id":2,"shipping_address_id":null,"promotion_id":159,"order_date":"2023-05-01T21:55:41.000000","status":"completed","order_total":13.07,"_shape_table":"order","_shape_seq":3631,"_shape_event_time":"2023-05-01T21:55:41.000000"}
    {"order_id":730,"customer_id":6,"store_id":1,"shipping_address_id":823,"promotion_id":null,"order_date":"2023-05-03T15:54:39.000000","status":"completed","order_total":134.6,"_shape_table":"order","_shape_seq":729,"_shape_event_time":"2023-05-03T15:54:39.000000"}
    {"order_id":3692,"customer_id":243,"store_id":4,"shipping_address_id":1085,"promotion_id":null,"order_date":"2023-05-03T20:07:49.974616","status":"returned","order_total":138.16,"_shape_table":"order","_shape_seq":3691,"_shape_event_time":"2023-05-03T20:07:49.974616"}
    {"order_id":214,"customer_id":36,"store_id":4,"shipping_address_id":157,"promotion_id":null,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":55.14,"_shape_table":"order","_shape_seq":213,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":786,"customer_id":36,"store_id":4,"shipping_address_id":448,"promotion_id":null,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":43.15,"_shape_table":"order","_shape_seq":785,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":1035,"customer_id":36,"store_id":2,"shipping_address_id":157,"promotion_id":null,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":1034,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":1390,"customer_id":36,"store_id":16,"shipping_address_id":157,"promotion_id":null,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":14.68,"_shape_table":"order","_shape_seq":1389,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":2573,"customer_id":36,"store_id":1,"shipping_address_id":448,"promotion_id":null,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":15.68,"_shape_table":"order","_shape_seq":2572,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":2616,"customer_id":36,"store_id":1,"shipping_address_id":448,"promotion_id":48,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":52.73,"_shape_table":"order","_shape_seq":2615,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":2827,"customer_id":36,"store_id":6,"shipping_address_id":448,"promotion_id":null,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":56.97,"_shape_table":"order","_shape_seq":2826,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":3146,"customer_id":36,"store_id":2,"shipping_address_id":157,"promotion_id":37,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":19.8,"_shape_table":"order","_shape_seq":3145,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":3534,"customer_id":36,"store_id":1,"shipping_address_id":448,"promotion_id":null,"order_date":"2023-05-04T13:11:20.928841","status":"completed","order_total":36.25,"_shape_table":"order","_shape_seq":3533,"_shape_event_time":"2023-05-04T13:11:20.928841"}
    {"order_id":1051,"customer_id":250,"store_id":15,"shipping_address_id":602,"promotion_id":null,"order_date":"2023-05-05T10:34:13.000000","status":"completed","order_total":17.72,"_shape_table":"order","_shape_seq":1050,"_shape_event_time":"2023-05-05T10:34:13.000000"}
    {"order_id":852,"customer_id":329,"store_id":15,"shipping_address_id":789,"promotion_id":null,"order_date":"2023-05-05T12:00:43.417698","status":"shipped","order_total":244.17,"_shape_table":"order","_shape_seq":851,"_shape_event_time":"2023-05-05T12:00:43.417698"}
    {"order_id":3734,"customer_id":329,"store_id":1,"shipping_address_id":1096,"promotion_id":null,"order_date":"2023-05-05T12:00:43.417698","status":"completed","order_total":81.69,"_shape_table":"order","_shape_seq":3733,"_shape_event_time":"2023-05-05T12:00:43.417698"}
    {"order_id":348,"customer_id":25,"store_id":22,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-05-05T13:33:59.000000","status":"completed","order_total":17.1,"_shape_table":"order","_shape_seq":347,"_shape_event_time":"2023-05-05T13:33:59.000000"}
    {"order_id":1136,"customer_id":585,"store_id":1,"shipping_address_id":388,"promotion_id":null,"order_date":"2023-05-05T22:40:28.662569","status":"completed","order_total":69.5,"_shape_table":"order","_shape_seq":1135,"_shape_event_time":"2023-05-05T22:40:28.662569"}
    {"order_id":3717,"customer_id":222,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-05-06T03:00:04.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":3716,"_shape_event_time":"2023-05-06T03:00:04.000000"}
    {"order_id":4407,"customer_id":884,"store_id":6,"shipping_address_id":564,"promotion_id":null,"order_date":"2023-05-06T11:15:26.279286","status":"completed","order_total":65.03,"_shape_table":"order","_shape_seq":4406,"_shape_event_time":"2023-05-06T11:15:26.279286"}
    {"order_id":4489,"customer_id":884,"store_id":1,"shipping_address_id":564,"promotion_id":null,"order_date":"2023-05-06T11:15:26.279286","status":"returned","order_total":41.0,"_shape_table":"order","_shape_seq":4488,"_shape_event_time":"2023-05-06T11:15:26.279286"}
    {"order_id":4222,"customer_id":41,"store_id":23,"shipping_address_id":1154,"promotion_id":92,"order_date":"2023-05-06T18:17:47.000000","status":"completed","order_total":37.87,"_shape_table":"order","_shape_seq":4221,"_shape_event_time":"2023-05-06T18:17:47.000000"}
    {"order_id":643,"customer_id":863,"store_id":9,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-05-07T03:39:14.000000","status":"shipped","order_total":60.42,"_shape_table":"order","_shape_seq":642,"_shape_event_time":"2023-05-07T03:39:14.000000"}
    {"order_id":218,"customer_id":17,"store_id":26,"shipping_address_id":981,"promotion_id":null,"order_date":"2023-05-07T04:07:25.000000","status":"completed","order_total":43.84,"_shape_table":"order","_shape_seq":217,"_shape_event_time":"2023-05-07T04:07:25.000000"}
    {"order_id":2450,"customer_id":945,"store_id":24,"shipping_address_id":1093,"promotion_id":null,"order_date":"2023-05-07T13:42:03.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":2449,"_shape_event_time":"2023-05-07T13:42:03.000000"}
    {"order_id":38,"customer_id":41,"store_id":4,"shipping_address_id":1154,"promotion_id":null,"order_date":"2023-05-08T08:16:12.000000","status":"shipped","order_total":436.91,"_shape_table":"order","_shape_seq":37,"_shape_event_time":"2023-05-08T08:16:12.000000"}
    {"order_id":1033,"customer_id":9,"store_id":2,"shipping_address_id":1070,"promotion_id":null,"order_date":"2023-05-08T20:46:18.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1032,"_shape_event_time":"2023-05-08T20:46:18.000000"}
    {"order_id":1323,"customer_id":470,"store_id":7,"shipping_address_id":1203,"promotion_id":132,"order_date":"2023-05-09T04:25:04.050513","status":"completed","order_total":8.2,"_shape_table":"order","_shape_seq":1322,"_shape_event_time":"2023-05-09T04:25:04.050513"}
    {"order_id":874,"customer_id":104,"store_id":139,"shipping_address_id":992,"promotion_id":null,"order_date":"2023-05-09T08:53:20.743813","status":"completed","order_total":145.92,"_shape_table":"order","_shape_seq":873,"_shape_event_time":"2023-05-09T08:53:20.743813"}
    {"order_id":4577,"customer_id":104,"store_id":1,"shipping_address_id":1482,"promotion_id":167,"order_date":"2023-05-09T08:53:20.743813","status":"completed","order_total":16.6,"_shape_table":"order","_shape_seq":4576,"_shape_event_time":"2023-05-09T08:53:20.743813"}
    {"order_id":630,"customer_id":77,"store_id":2,"shipping_address_id":1253,"promotion_id":111,"order_date":"2023-05-10T00:23:23.000000","status":"completed","order_total":57.83,"_shape_table":"order","_shape_seq":629,"_shape_event_time":"2023-05-10T00:23:23.000000"}
    {"order_id":4079,"customer_id":604,"store_id":3,"shipping_address_id":null,"promotion_id":196,"order_date":"2023-05-10T03:11:01.000000","status":"completed","order_total":24.66,"_shape_table":"order","_shape_seq":4078,"_shape_event_time":"2023-05-10T03:11:01.000000"}
    {"order_id":1961,"customer_id":831,"store_id":52,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-05-10T12:53:14.000000","status":"completed","order_total":281.77,"_shape_table":"order","_shape_seq":1960,"_shape_event_time":"2023-05-10T12:53:14.000000"}
    {"order_id":3402,"customer_id":374,"store_id":6,"shipping_address_id":9,"promotion_id":153,"order_date":"2023-05-12T10:25:22.000000","status":"shipped","order_total":37.1,"_shape_table":"order","_shape_seq":3401,"_shape_event_time":"2023-05-12T10:25:22.000000"}
    {"order_id":2170,"customer_id":45,"store_id":4,"shipping_address_id":1087,"promotion_id":null,"order_date":"2023-05-12T12:46:19.000000","status":"completed","order_total":61.57,"_shape_table":"order","_shape_seq":2169,"_shape_event_time":"2023-05-12T12:46:19.000000"}
    {"order_id":2094,"customer_id":352,"store_id":9,"shipping_address_id":468,"promotion_id":null,"order_date":"2023-05-13T03:27:09.302589","status":"completed","order_total":80.82,"_shape_table":"order","_shape_seq":2093,"_shape_event_time":"2023-05-13T03:27:09.302589"}
    {"order_id":1810,"customer_id":274,"store_id":1,"shipping_address_id":577,"promotion_id":null,"order_date":"2023-05-13T04:20:19.981198","status":"completed","order_total":97.82,"_shape_table":"order","_shape_seq":1809,"_shape_event_time":"2023-05-13T04:20:19.981198"}
    {"order_id":4636,"customer_id":977,"store_id":1,"shipping_address_id":798,"promotion_id":null,"order_date":"2023-05-13T04:55:46.661438","status":"returned","order_total":142.29,"_shape_table":"order","_shape_seq":4635,"_shape_event_time":"2023-05-13T04:55:46.661438"}
    {"order_id":2237,"customer_id":239,"store_id":149,"shipping_address_id":1120,"promotion_id":null,"order_date":"2023-05-13T12:15:41.000000","status":"completed","order_total":13.29,"_shape_table":"order","_shape_seq":2236,"_shape_event_time":"2023-05-13T12:15:41.000000"}
    {"order_id":2772,"customer_id":784,"store_id":3,"shipping_address_id":526,"promotion_id":51,"order_date":"2023-05-13T13:09:17.835522","status":"processing","order_total":54.18,"_shape_table":"order","_shape_seq":2771,"_shape_event_time":"2023-05-13T13:09:17.835522"}
    {"order_id":3901,"customer_id":784,"store_id":5,"shipping_address_id":526,"promotion_id":null,"order_date":"2023-05-13T13:09:17.835522","status":"completed","order_total":13.29,"_shape_table":"order","_shape_seq":3900,"_shape_event_time":"2023-05-13T13:09:17.835522"}
    {"order_id":4821,"customer_id":784,"store_id":6,"shipping_address_id":526,"promotion_id":29,"order_date":"2023-05-13T13:09:17.835522","status":"completed","order_total":197.16,"_shape_table":"order","_shape_seq":4820,"_shape_event_time":"2023-05-13T13:09:17.835522"}
    {"order_id":4735,"customer_id":26,"store_id":147,"shipping_address_id":66,"promotion_id":null,"order_date":"2023-05-13T17:11:31.000000","status":"completed","order_total":39.28,"_shape_table":"order","_shape_seq":4734,"_shape_event_time":"2023-05-13T17:11:31.000000"}
    {"order_id":2588,"customer_id":92,"store_id":1,"shipping_address_id":933,"promotion_id":null,"order_date":"2023-05-13T17:21:29.000000","status":"completed","order_total":51.21,"_shape_table":"order","_shape_seq":2587,"_shape_event_time":"2023-05-13T17:21:29.000000"}
    {"order_id":1139,"customer_id":236,"store_id":3,"shipping_address_id":592,"promotion_id":null,"order_date":"2023-05-13T22:01:50.151631","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1138,"_shape_event_time":"2023-05-13T22:01:50.151631"}
    {"order_id":1672,"customer_id":236,"store_id":21,"shipping_address_id":592,"promotion_id":null,"order_date":"2023-05-13T22:01:50.151631","status":"completed","order_total":271.83,"_shape_table":"order","_shape_seq":1671,"_shape_event_time":"2023-05-13T22:01:50.151631"}
    {"order_id":3401,"customer_id":236,"store_id":4,"shipping_address_id":592,"promotion_id":157,"order_date":"2023-05-13T22:01:50.151631","status":"completed","order_total":35.33,"_shape_table":"order","_shape_seq":3400,"_shape_event_time":"2023-05-13T22:01:50.151631"}
    {"order_id":4255,"customer_id":527,"store_id":1,"shipping_address_id":44,"promotion_id":144,"order_date":"2023-05-14T15:31:14.000000","status":"completed","order_total":23.93,"_shape_table":"order","_shape_seq":4254,"_shape_event_time":"2023-05-14T15:31:14.000000"}
    {"order_id":3131,"customer_id":968,"store_id":1,"shipping_address_id":600,"promotion_id":null,"order_date":"2023-05-15T06:27:39.000000","status":"completed","order_total":262.18,"_shape_table":"order","_shape_seq":3130,"_shape_event_time":"2023-05-15T06:27:39.000000"}
    {"order_id":974,"customer_id":926,"store_id":20,"shipping_address_id":10,"promotion_id":null,"order_date":"2023-05-17T00:36:15.000000","status":"completed","order_total":44.2,"_shape_table":"order","_shape_seq":973,"_shape_event_time":"2023-05-17T00:36:15.000000"}
    {"order_id":4422,"customer_id":38,"store_id":1,"shipping_address_id":null,"promotion_id":21,"order_date":"2023-05-17T07:34:08.000000","status":"completed","order_total":16.33,"_shape_table":"order","_shape_seq":4421,"_shape_event_time":"2023-05-17T07:34:08.000000"}
    {"order_id":4284,"customer_id":34,"store_id":2,"shipping_address_id":409,"promotion_id":null,"order_date":"2023-05-17T13:57:29.000000","status":"completed","order_total":202.56,"_shape_table":"order","_shape_seq":4283,"_shape_event_time":"2023-05-17T13:57:29.000000"}
    {"order_id":2330,"customer_id":27,"store_id":1,"shipping_address_id":67,"promotion_id":null,"order_date":"2023-05-18T00:03:29.000000","status":"completed","order_total":45.44,"_shape_table":"order","_shape_seq":2329,"_shape_event_time":"2023-05-18T00:03:29.000000"}
    {"order_id":595,"customer_id":3,"store_id":1,"shipping_address_id":1441,"promotion_id":72,"order_date":"2023-05-18T01:01:30.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":594,"_shape_event_time":"2023-05-18T01:01:30.000000"}
    {"order_id":2326,"customer_id":763,"store_id":19,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-05-19T01:03:08.880179","status":"completed","order_total":79.71,"_shape_table":"order","_shape_seq":2325,"_shape_event_time":"2023-05-19T01:03:08.880179"}
    {"order_id":2364,"customer_id":763,"store_id":76,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-05-19T01:03:08.880179","status":"completed","order_total":131.09,"_shape_table":"order","_shape_seq":2363,"_shape_event_time":"2023-05-19T01:03:08.880179"}
    {"order_id":2770,"customer_id":763,"store_id":12,"shipping_address_id":null,"promotion_id":168,"order_date":"2023-05-19T01:03:08.880179","status":"returned","order_total":160.16,"_shape_table":"order","_shape_seq":2769,"_shape_event_time":"2023-05-19T01:03:08.880179"}
    {"order_id":2092,"customer_id":565,"store_id":3,"shipping_address_id":null,"promotion_id":39,"order_date":"2023-05-19T21:19:20.143037","status":"completed","order_total":64.34,"_shape_table":"order","_shape_seq":2091,"_shape_event_time":"2023-05-19T21:19:20.143037"}
    {"order_id":4244,"customer_id":594,"store_id":7,"shipping_address_id":1463,"promotion_id":null,"order_date":"2023-05-20T00:59:54.827009","status":"completed","order_total":27.97,"_shape_table":"order","_shape_seq":4243,"_shape_event_time":"2023-05-20T00:59:54.827009"}
    {"order_id":4973,"customer_id":837,"store_id":47,"shipping_address_id":395,"promotion_id":165,"order_date":"2023-05-20T09:05:22.000000","status":"completed","order_total":376.75,"_shape_table":"order","_shape_seq":4972,"_shape_event_time":"2023-05-20T09:05:22.000000"}
    {"order_id":4621,"customer_id":161,"store_id":2,"shipping_address_id":null,"promotion_id":104,"order_date":"2023-05-20T11:17:44.000000","status":"completed","order_total":7.34,"_shape_table":"order","_shape_seq":4620,"_shape_event_time":"2023-05-20T11:17:44.000000"}
    {"order_id":1383,"customer_id":9,"store_id":1,"shipping_address_id":1070,"promotion_id":null,"order_date":"2023-05-23T01:56:03.000000","status":"completed","order_total":22.15,"_shape_table":"order","_shape_seq":1382,"_shape_event_time":"2023-05-23T01:56:03.000000"}
    {"order_id":3206,"customer_id":881,"store_id":2,"shipping_address_id":364,"promotion_id":null,"order_date":"2023-05-24T16:51:12.000000","status":"completed","order_total":78.01,"_shape_table":"order","_shape_seq":3205,"_shape_event_time":"2023-05-24T16:51:12.000000"}
    {"order_id":3984,"customer_id":266,"store_id":1,"shipping_address_id":1048,"promotion_id":null,"order_date":"2023-05-25T07:25:16.879194","status":"completed","order_total":220.67,"_shape_table":"order","_shape_seq":3983,"_shape_event_time":"2023-05-25T07:25:16.879194"}
    {"order_id":2417,"customer_id":386,"store_id":1,"shipping_address_id":1053,"promotion_id":null,"order_date":"2023-05-25T10:27:31.000000","status":"completed","order_total":24.89,"_shape_table":"order","_shape_seq":2416,"_shape_event_time":"2023-05-25T10:27:31.000000"}
    {"order_id":4490,"customer_id":693,"store_id":2,"shipping_address_id":724,"promotion_id":26,"order_date":"2023-05-25T13:39:17.926090","status":"shipped","order_total":14.68,"_shape_table":"order","_shape_seq":4489,"_shape_event_time":"2023-05-25T13:39:17.926090"}
    {"order_id":2536,"customer_id":474,"store_id":2,"shipping_address_id":875,"promotion_id":129,"order_date":"2023-05-26T03:56:17.415580","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2535,"_shape_event_time":"2023-05-26T03:56:17.415580"}
    {"order_id":4299,"customer_id":467,"store_id":2,"shipping_address_id":1448,"promotion_id":null,"order_date":"2023-05-27T14:44:50.465172","status":"completed","order_total":64.17,"_shape_table":"order","_shape_seq":4298,"_shape_event_time":"2023-05-27T14:44:50.465172"}
    {"order_id":1874,"customer_id":13,"store_id":28,"shipping_address_id":1241,"promotion_id":null,"order_date":"2023-05-27T17:33:44.000000","status":"completed","order_total":70.8,"_shape_table":"order","_shape_seq":1873,"_shape_event_time":"2023-05-27T17:33:44.000000"}
    {"order_id":3797,"customer_id":28,"store_id":126,"shipping_address_id":812,"promotion_id":null,"order_date":"2023-05-27T22:55:04.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3796,"_shape_event_time":"2023-05-27T22:55:04.000000"}
    {"order_id":3574,"customer_id":467,"store_id":2,"shipping_address_id":1448,"promotion_id":null,"order_date":"2023-05-29T06:15:38.000000","status":"completed","order_total":295.39,"_shape_table":"order","_shape_seq":3573,"_shape_event_time":"2023-05-29T06:15:38.000000"}
    {"order_id":3571,"customer_id":17,"store_id":34,"shipping_address_id":981,"promotion_id":132,"order_date":"2023-05-29T08:51:55.000000","status":"completed","order_total":21.91,"_shape_table":"order","_shape_seq":3570,"_shape_event_time":"2023-05-29T08:51:55.000000"}
    {"order_id":2579,"customer_id":828,"store_id":4,"shipping_address_id":null,"promotion_id":77,"order_date":"2023-05-29T09:23:39.000000","status":"returned","order_total":23.92,"_shape_table":"order","_shape_seq":2578,"_shape_event_time":"2023-05-29T09:23:39.000000"}
    {"order_id":350,"customer_id":286,"store_id":1,"shipping_address_id":49,"promotion_id":null,"order_date":"2023-05-29T20:34:27.000000","status":"completed","order_total":63.35,"_shape_table":"order","_shape_seq":349,"_shape_event_time":"2023-05-29T20:34:27.000000"}
    {"order_id":2126,"customer_id":880,"store_id":64,"shipping_address_id":679,"promotion_id":null,"order_date":"2023-05-30T00:53:41.696628","status":"completed","order_total":238.05,"_shape_table":"order","_shape_seq":2125,"_shape_event_time":"2023-05-30T00:53:41.696628"}
    {"order_id":2230,"customer_id":284,"store_id":1,"shipping_address_id":1281,"promotion_id":92,"order_date":"2023-05-30T15:44:21.623872","status":"shipped","order_total":0.0,"_shape_table":"order","_shape_seq":2229,"_shape_event_time":"2023-05-30T15:44:21.623872"}
    {"order_id":3480,"customer_id":284,"store_id":94,"shipping_address_id":1281,"promotion_id":69,"order_date":"2023-05-30T15:44:21.623872","status":"completed","order_total":7.69,"_shape_table":"order","_shape_seq":3479,"_shape_event_time":"2023-05-30T15:44:21.623872"}
    {"order_id":4632,"customer_id":284,"store_id":4,"shipping_address_id":1281,"promotion_id":null,"order_date":"2023-05-30T15:44:21.623872","status":"completed","order_total":39.87,"_shape_table":"order","_shape_seq":4631,"_shape_event_time":"2023-05-30T15:44:21.623872"}
    {"order_id":3171,"customer_id":28,"store_id":1,"shipping_address_id":812,"promotion_id":null,"order_date":"2023-05-30T23:36:22.000000","status":"completed","order_total":139.26,"_shape_table":"order","_shape_seq":3170,"_shape_event_time":"2023-05-30T23:36:22.000000"}
    {"order_id":1003,"customer_id":15,"store_id":1,"shipping_address_id":695,"promotion_id":28,"order_date":"2023-06-02T01:09:19.000000","status":"shipped","order_total":128.54,"_shape_table":"order","_shape_seq":1002,"_shape_event_time":"2023-06-02T01:09:19.000000"}
    {"order_id":2150,"customer_id":28,"store_id":1,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-06-02T07:33:40.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":2149,"_shape_event_time":"2023-06-02T07:33:40.000000"}
    {"order_id":1619,"customer_id":999,"store_id":5,"shipping_address_id":523,"promotion_id":22,"order_date":"2023-06-04T15:24:59.000000","status":"completed","order_total":3.99,"_shape_table":"order","_shape_seq":1618,"_shape_event_time":"2023-06-04T15:24:59.000000"}
    {"order_id":702,"customer_id":49,"store_id":1,"shipping_address_id":1369,"promotion_id":null,"order_date":"2023-06-05T04:48:26.637095","status":"completed","order_total":896.83,"_shape_table":"order","_shape_seq":701,"_shape_event_time":"2023-06-05T04:48:26.637095"}
    {"order_id":2412,"customer_id":49,"store_id":19,"shipping_address_id":1369,"promotion_id":null,"order_date":"2023-06-05T04:48:26.637095","status":"completed","order_total":37.64,"_shape_table":"order","_shape_seq":2411,"_shape_event_time":"2023-06-05T04:48:26.637095"}
    {"order_id":3395,"customer_id":49,"store_id":5,"shipping_address_id":1369,"promotion_id":null,"order_date":"2023-06-05T04:48:26.637095","status":"cancelled","order_total":14.68,"_shape_table":"order","_shape_seq":3394,"_shape_event_time":"2023-06-05T04:48:26.637095"}
    {"order_id":3985,"customer_id":49,"store_id":11,"shipping_address_id":1369,"promotion_id":186,"order_date":"2023-06-05T04:48:26.637095","status":"completed","order_total":34.4,"_shape_table":"order","_shape_seq":3984,"_shape_event_time":"2023-06-05T04:48:26.637095"}
    {"order_id":4457,"customer_id":49,"store_id":13,"shipping_address_id":1369,"promotion_id":null,"order_date":"2023-06-05T04:48:26.637095","status":"completed","order_total":127.87,"_shape_table":"order","_shape_seq":4456,"_shape_event_time":"2023-06-05T04:48:26.637095"}
    {"order_id":4918,"customer_id":267,"store_id":30,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-05T05:22:59.000000","status":"returned","order_total":106.83,"_shape_table":"order","_shape_seq":4917,"_shape_event_time":"2023-06-05T05:22:59.000000"}
    {"order_id":3833,"customer_id":464,"store_id":1,"shipping_address_id":null,"promotion_id":63,"order_date":"2023-06-05T13:58:47.000000","status":"completed","order_total":115.5,"_shape_table":"order","_shape_seq":3832,"_shape_event_time":"2023-06-05T13:58:47.000000"}
    {"order_id":1195,"customer_id":227,"store_id":17,"shipping_address_id":341,"promotion_id":null,"order_date":"2023-06-05T19:27:53.915288","status":"shipped","order_total":15.53,"_shape_table":"order","_shape_seq":1194,"_shape_event_time":"2023-06-05T19:27:53.915288"}
    {"order_id":2050,"customer_id":227,"store_id":1,"shipping_address_id":996,"promotion_id":null,"order_date":"2023-06-05T19:27:53.915288","status":"completed","order_total":41.49,"_shape_table":"order","_shape_seq":2049,"_shape_event_time":"2023-06-05T19:27:53.915288"}
    {"order_id":1158,"customer_id":982,"store_id":44,"shipping_address_id":1280,"promotion_id":null,"order_date":"2023-06-06T14:48:14.415320","status":"cancelled","order_total":67.75,"_shape_table":"order","_shape_seq":1157,"_shape_event_time":"2023-06-06T14:48:14.415320"}
    {"order_id":2805,"customer_id":982,"store_id":1,"shipping_address_id":1260,"promotion_id":null,"order_date":"2023-06-06T14:48:14.415320","status":"completed","order_total":648.72,"_shape_table":"order","_shape_seq":2804,"_shape_event_time":"2023-06-06T14:48:14.415320"}
    {"order_id":403,"customer_id":3,"store_id":1,"shipping_address_id":746,"promotion_id":118,"order_date":"2023-06-07T17:16:01.000000","status":"completed","order_total":42.59,"_shape_table":"order","_shape_seq":402,"_shape_event_time":"2023-06-07T17:16:01.000000"}
    {"order_id":830,"customer_id":9,"store_id":1,"shipping_address_id":1070,"promotion_id":null,"order_date":"2023-06-08T07:29:53.000000","status":"cancelled","order_total":0.0,"_shape_table":"order","_shape_seq":829,"_shape_event_time":"2023-06-08T07:29:53.000000"}
    {"order_id":1169,"customer_id":73,"store_id":61,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-08T09:23:45.000000","status":"completed","order_total":20.5,"_shape_table":"order","_shape_seq":1168,"_shape_event_time":"2023-06-08T09:23:45.000000"}
    {"order_id":759,"customer_id":101,"store_id":20,"shipping_address_id":134,"promotion_id":null,"order_date":"2023-06-08T12:59:45.045542","status":"shipped","order_total":110.85,"_shape_table":"order","_shape_seq":758,"_shape_event_time":"2023-06-08T12:59:45.045542"}
    {"order_id":1714,"customer_id":101,"store_id":10,"shipping_address_id":604,"promotion_id":93,"order_date":"2023-06-08T12:59:45.045542","status":"completed","order_total":12.72,"_shape_table":"order","_shape_seq":1713,"_shape_event_time":"2023-06-08T12:59:45.045542"}
    {"order_id":3622,"customer_id":101,"store_id":21,"shipping_address_id":604,"promotion_id":null,"order_date":"2023-06-08T12:59:45.045542","status":"processing","order_total":39.61,"_shape_table":"order","_shape_seq":3621,"_shape_event_time":"2023-06-08T12:59:45.045542"}
    {"order_id":1243,"customer_id":36,"store_id":3,"shipping_address_id":448,"promotion_id":17,"order_date":"2023-06-08T20:55:39.000000","status":"completed","order_total":15.06,"_shape_table":"order","_shape_seq":1242,"_shape_event_time":"2023-06-08T20:55:39.000000"}
    {"order_id":4031,"customer_id":196,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-09T04:50:03.000000","status":"completed","order_total":17.72,"_shape_table":"order","_shape_seq":4030,"_shape_event_time":"2023-06-09T04:50:03.000000"}
    {"order_id":1226,"customer_id":28,"store_id":5,"shipping_address_id":1464,"promotion_id":185,"order_date":"2023-06-10T02:09:50.000000","status":"shipped","order_total":63.22,"_shape_table":"order","_shape_seq":1225,"_shape_event_time":"2023-06-10T02:09:50.000000"}
    {"order_id":2838,"customer_id":507,"store_id":2,"shipping_address_id":1089,"promotion_id":null,"order_date":"2023-06-10T11:38:05.247451","status":"completed","order_total":26.58,"_shape_table":"order","_shape_seq":2837,"_shape_event_time":"2023-06-10T11:38:05.247451"}
    {"order_id":2922,"customer_id":774,"store_id":15,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-11T02:38:52.000000","status":"completed","order_total":17.87,"_shape_table":"order","_shape_seq":2921,"_shape_event_time":"2023-06-11T02:38:52.000000"}
    {"order_id":4844,"customer_id":959,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-12T02:14:22.000000","status":"shipped","order_total":76.65,"_shape_table":"order","_shape_seq":4843,"_shape_event_time":"2023-06-12T02:14:22.000000"}
    {"order_id":201,"customer_id":3,"store_id":1,"shipping_address_id":1441,"promotion_id":null,"order_date":"2023-06-12T22:45:18.000000","status":"returned","order_total":84.37,"_shape_table":"order","_shape_seq":200,"_shape_event_time":"2023-06-12T22:45:18.000000"}
    {"order_id":2565,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-06-13T06:41:02.000000","status":"processing","order_total":22.96,"_shape_table":"order","_shape_seq":2564,"_shape_event_time":"2023-06-13T06:41:02.000000"}
    {"order_id":3620,"customer_id":326,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-13T11:16:18.000000","status":"completed","order_total":418.39,"_shape_table":"order","_shape_seq":3619,"_shape_event_time":"2023-06-13T11:16:18.000000"}
    {"order_id":4823,"customer_id":833,"store_id":64,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-15T22:40:02.000000","status":"completed","order_total":38.22,"_shape_table":"order","_shape_seq":4822,"_shape_event_time":"2023-06-15T22:40:02.000000"}
    {"order_id":1629,"customer_id":900,"store_id":48,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-17T09:31:07.403693","status":"completed","order_total":89.87,"_shape_table":"order","_shape_seq":1628,"_shape_event_time":"2023-06-17T09:31:07.403693"}
    {"order_id":1966,"customer_id":900,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-17T09:31:07.403693","status":"shipped","order_total":46.35,"_shape_table":"order","_shape_seq":1965,"_shape_event_time":"2023-06-17T09:31:07.403693"}
    {"order_id":3476,"customer_id":900,"store_id":1,"shipping_address_id":null,"promotion_id":160,"order_date":"2023-06-17T09:31:07.403693","status":"completed","order_total":103.97,"_shape_table":"order","_shape_seq":3475,"_shape_event_time":"2023-06-17T09:31:07.403693"}
    {"order_id":1892,"customer_id":25,"store_id":129,"shipping_address_id":450,"promotion_id":192,"order_date":"2023-06-19T03:10:41.000000","status":"completed","order_total":55.11,"_shape_table":"order","_shape_seq":1891,"_shape_event_time":"2023-06-19T03:10:41.000000"}
    {"order_id":2256,"customer_id":788,"store_id":2,"shipping_address_id":148,"promotion_id":null,"order_date":"2023-06-19T21:35:32.615425","status":"returned","order_total":39.61,"_shape_table":"order","_shape_seq":2255,"_shape_event_time":"2023-06-19T21:35:32.615425"}
    {"order_id":2172,"customer_id":37,"store_id":6,"shipping_address_id":1402,"promotion_id":null,"order_date":"2023-06-20T00:26:46.000000","status":"completed","order_total":83.87,"_shape_table":"order","_shape_seq":2171,"_shape_event_time":"2023-06-20T00:26:46.000000"}
    {"order_id":4727,"customer_id":883,"store_id":2,"shipping_address_id":1,"promotion_id":12,"order_date":"2023-06-21T00:42:24.000000","status":"completed","order_total":2.22,"_shape_table":"order","_shape_seq":4726,"_shape_event_time":"2023-06-21T00:42:24.000000"}
    {"order_id":1528,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-06-21T14:40:03.000000","status":"returned","order_total":109.54,"_shape_table":"order","_shape_seq":1527,"_shape_event_time":"2023-06-21T14:40:03.000000"}
    {"order_id":913,"customer_id":899,"store_id":102,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-21T20:08:44.674230","status":"completed","order_total":254.79,"_shape_table":"order","_shape_seq":912,"_shape_event_time":"2023-06-21T20:08:44.674230"}
    {"order_id":1046,"customer_id":798,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-24T04:19:16.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":1045,"_shape_event_time":"2023-06-24T04:19:16.000000"}
    {"order_id":1736,"customer_id":285,"store_id":10,"shipping_address_id":841,"promotion_id":null,"order_date":"2023-06-24T19:32:32.552806","status":"completed","order_total":432.48,"_shape_table":"order","_shape_seq":1735,"_shape_event_time":"2023-06-24T19:32:32.552806"}
    {"order_id":2658,"customer_id":285,"store_id":7,"shipping_address_id":841,"promotion_id":null,"order_date":"2023-06-24T19:32:32.552806","status":"completed","order_total":53.22,"_shape_table":"order","_shape_seq":2657,"_shape_event_time":"2023-06-24T19:32:32.552806"}
    {"order_id":2906,"customer_id":285,"store_id":4,"shipping_address_id":841,"promotion_id":null,"order_date":"2023-06-24T19:32:32.552806","status":"completed","order_total":106.09,"_shape_table":"order","_shape_seq":2905,"_shape_event_time":"2023-06-24T19:32:32.552806"}
    {"order_id":768,"customer_id":559,"store_id":1,"shipping_address_id":997,"promotion_id":24,"order_date":"2023-06-24T23:10:47.122987","status":"completed","order_total":8.87,"_shape_table":"order","_shape_seq":767,"_shape_event_time":"2023-06-24T23:10:47.122987"}
    {"order_id":2432,"customer_id":169,"store_id":4,"shipping_address_id":744,"promotion_id":null,"order_date":"2023-06-24T23:16:54.452221","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":2431,"_shape_event_time":"2023-06-24T23:16:54.452221"}
    {"order_id":2429,"customer_id":667,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-06-25T01:41:09.000000","status":"completed","order_total":22.96,"_shape_table":"order","_shape_seq":2428,"_shape_event_time":"2023-06-25T01:41:09.000000"}
    {"order_id":3272,"customer_id":27,"store_id":4,"shipping_address_id":785,"promotion_id":162,"order_date":"2023-06-26T05:51:40.000000","status":"completed","order_total":32.31,"_shape_table":"order","_shape_seq":3271,"_shape_event_time":"2023-06-26T05:51:40.000000"}
    {"order_id":3702,"customer_id":492,"store_id":1,"shipping_address_id":459,"promotion_id":null,"order_date":"2023-06-27T19:04:19.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3701,"_shape_event_time":"2023-06-27T19:04:19.000000"}
    {"order_id":4646,"customer_id":470,"store_id":1,"shipping_address_id":1203,"promotion_id":null,"order_date":"2023-06-27T19:13:08.000000","status":"completed","order_total":46.54,"_shape_table":"order","_shape_seq":4645,"_shape_event_time":"2023-06-27T19:13:08.000000"}
    {"order_id":3415,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":187,"order_date":"2023-06-28T03:59:32.000000","status":"completed","order_total":38.84,"_shape_table":"order","_shape_seq":3414,"_shape_event_time":"2023-06-28T03:59:32.000000"}
    {"order_id":1392,"customer_id":64,"store_id":80,"shipping_address_id":1494,"promotion_id":null,"order_date":"2023-06-29T02:51:22.076266","status":"completed","order_total":198.0,"_shape_table":"order","_shape_seq":1391,"_shape_event_time":"2023-06-29T02:51:22.076266"}
    {"order_id":1667,"customer_id":64,"store_id":13,"shipping_address_id":1198,"promotion_id":145,"order_date":"2023-06-29T02:51:22.076266","status":"returned","order_total":153.99,"_shape_table":"order","_shape_seq":1666,"_shape_event_time":"2023-06-29T02:51:22.076266"}
    {"order_id":2065,"customer_id":64,"store_id":2,"shipping_address_id":931,"promotion_id":103,"order_date":"2023-06-29T02:51:22.076266","status":"completed","order_total":109.04,"_shape_table":"order","_shape_seq":2064,"_shape_event_time":"2023-06-29T02:51:22.076266"}
    {"order_id":4388,"customer_id":37,"store_id":10,"shipping_address_id":1402,"promotion_id":null,"order_date":"2023-06-29T03:49:58.000000","status":"completed","order_total":61.72,"_shape_table":"order","_shape_seq":4387,"_shape_event_time":"2023-06-29T03:49:58.000000"}
    {"order_id":4981,"customer_id":64,"store_id":31,"shipping_address_id":1330,"promotion_id":null,"order_date":"2023-06-29T11:20:19.000000","status":"completed","order_total":74.43,"_shape_table":"order","_shape_seq":4980,"_shape_event_time":"2023-06-29T11:20:19.000000"}
    {"order_id":1783,"customer_id":565,"store_id":4,"shipping_address_id":null,"promotion_id":43,"order_date":"2023-06-29T17:03:46.000000","status":"completed","order_total":130.89,"_shape_table":"order","_shape_seq":1782,"_shape_event_time":"2023-06-29T17:03:46.000000"}
    {"order_id":1044,"customer_id":16,"store_id":1,"shipping_address_id":8,"promotion_id":76,"order_date":"2023-06-30T21:42:25.000000","status":"completed","order_total":22.37,"_shape_table":"order","_shape_seq":1043,"_shape_event_time":"2023-06-30T21:42:25.000000"}
    {"order_id":3036,"customer_id":561,"store_id":3,"shipping_address_id":1457,"promotion_id":null,"order_date":"2023-06-30T23:56:57.000000","status":"completed","order_total":69.15,"_shape_table":"order","_shape_seq":3035,"_shape_event_time":"2023-06-30T23:56:57.000000"}
    {"order_id":1576,"customer_id":81,"store_id":9,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-01T00:56:18.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":1575,"_shape_event_time":"2023-07-01T00:56:18.000000"}
    {"order_id":2475,"customer_id":26,"store_id":1,"shipping_address_id":66,"promotion_id":null,"order_date":"2023-07-01T13:14:29.000000","status":"completed","order_total":88.3,"_shape_table":"order","_shape_seq":2474,"_shape_event_time":"2023-07-01T13:14:29.000000"}
    {"order_id":296,"customer_id":9,"store_id":37,"shipping_address_id":1070,"promotion_id":145,"order_date":"2023-07-02T12:25:37.000000","status":"completed","order_total":72.52,"_shape_table":"order","_shape_seq":295,"_shape_event_time":"2023-07-02T12:25:37.000000"}
    {"order_id":2840,"customer_id":637,"store_id":3,"shipping_address_id":14,"promotion_id":null,"order_date":"2023-07-02T20:56:00.000000","status":"completed","order_total":31.82,"_shape_table":"order","_shape_seq":2839,"_shape_event_time":"2023-07-02T20:56:00.000000"}
    {"order_id":1209,"customer_id":281,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-03T15:03:02.000000","status":"completed","order_total":38.01,"_shape_table":"order","_shape_seq":1208,"_shape_event_time":"2023-07-03T15:03:02.000000"}
    {"order_id":1707,"customer_id":158,"store_id":7,"shipping_address_id":1268,"promotion_id":null,"order_date":"2023-07-04T05:38:53.000000","status":"completed","order_total":291.36,"_shape_table":"order","_shape_seq":1706,"_shape_event_time":"2023-07-04T05:38:53.000000"}
    {"order_id":220,"customer_id":9,"store_id":2,"shipping_address_id":1070,"promotion_id":null,"order_date":"2023-07-04T08:56:30.000000","status":"completed","order_total":18.53,"_shape_table":"order","_shape_seq":219,"_shape_event_time":"2023-07-04T08:56:30.000000"}
    {"order_id":1962,"customer_id":356,"store_id":14,"shipping_address_id":1383,"promotion_id":null,"order_date":"2023-07-05T08:53:03.697025","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":1961,"_shape_event_time":"2023-07-05T08:53:03.697025"}
    {"order_id":3028,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2023-07-05T10:34:14.000000","status":"completed","order_total":97.94,"_shape_table":"order","_shape_seq":3027,"_shape_event_time":"2023-07-05T10:34:14.000000"}
    {"order_id":2233,"customer_id":93,"store_id":1,"shipping_address_id":261,"promotion_id":null,"order_date":"2023-07-05T15:08:27.904897","status":"shipped","order_total":86.3,"_shape_table":"order","_shape_seq":2232,"_shape_event_time":"2023-07-05T15:08:27.904897"}
    {"order_id":4505,"customer_id":93,"store_id":8,"shipping_address_id":355,"promotion_id":null,"order_date":"2023-07-05T15:08:27.904897","status":"shipped","order_total":122.73,"_shape_table":"order","_shape_seq":4504,"_shape_event_time":"2023-07-05T15:08:27.904897"}
    {"order_id":2812,"customer_id":354,"store_id":1,"shipping_address_id":null,"promotion_id":179,"order_date":"2023-07-05T16:19:57.000000","status":"completed","order_total":8.2,"_shape_table":"order","_shape_seq":2811,"_shape_event_time":"2023-07-05T16:19:57.000000"}
    {"order_id":1982,"customer_id":122,"store_id":1,"shipping_address_id":811,"promotion_id":null,"order_date":"2023-07-06T16:57:54.919117","status":"completed","order_total":493.78,"_shape_table":"order","_shape_seq":1981,"_shape_event_time":"2023-07-06T16:57:54.919117"}
    {"order_id":2712,"customer_id":122,"store_id":15,"shipping_address_id":811,"promotion_id":null,"order_date":"2023-07-06T16:57:54.919117","status":"completed","order_total":65.69,"_shape_table":"order","_shape_seq":2711,"_shape_event_time":"2023-07-06T16:57:54.919117"}
    {"order_id":3939,"customer_id":122,"store_id":41,"shipping_address_id":811,"promotion_id":54,"order_date":"2023-07-06T16:57:54.919117","status":"completed","order_total":78.74,"_shape_table":"order","_shape_seq":3938,"_shape_event_time":"2023-07-06T16:57:54.919117"}
    {"order_id":3965,"customer_id":122,"store_id":43,"shipping_address_id":811,"promotion_id":null,"order_date":"2023-07-06T16:57:54.919117","status":"returned","order_total":137.12,"_shape_table":"order","_shape_seq":3964,"_shape_event_time":"2023-07-06T16:57:54.919117"}
    {"order_id":4510,"customer_id":122,"store_id":1,"shipping_address_id":400,"promotion_id":93,"order_date":"2023-07-06T16:57:54.919117","status":"completed","order_total":80.49,"_shape_table":"order","_shape_seq":4509,"_shape_event_time":"2023-07-06T16:57:54.919117"}
    {"order_id":4965,"customer_id":519,"store_id":2,"shipping_address_id":82,"promotion_id":null,"order_date":"2023-07-07T08:56:44.000000","status":"returned","order_total":106.98,"_shape_table":"order","_shape_seq":4964,"_shape_event_time":"2023-07-07T08:56:44.000000"}
    {"order_id":458,"customer_id":3,"store_id":1,"shipping_address_id":746,"promotion_id":null,"order_date":"2023-07-07T14:01:02.000000","status":"completed","order_total":54.3,"_shape_table":"order","_shape_seq":457,"_shape_event_time":"2023-07-07T14:01:02.000000"}
    {"order_id":4742,"customer_id":127,"store_id":1,"shipping_address_id":877,"promotion_id":199,"order_date":"2023-07-08T02:35:34.000000","status":"completed","order_total":3.32,"_shape_table":"order","_shape_seq":4741,"_shape_event_time":"2023-07-08T02:35:34.000000"}
    {"order_id":4155,"customer_id":338,"store_id":2,"shipping_address_id":null,"promotion_id":85,"order_date":"2023-07-09T00:48:35.185071","status":"completed","order_total":3.32,"_shape_table":"order","_shape_seq":4154,"_shape_event_time":"2023-07-09T00:48:35.185071"}
    {"order_id":4920,"customer_id":668,"store_id":3,"shipping_address_id":349,"promotion_id":35,"order_date":"2023-07-10T02:11:57.294282","status":"completed","order_total":130.62,"_shape_table":"order","_shape_seq":4919,"_shape_event_time":"2023-07-10T02:11:57.294282"}
    {"order_id":3646,"customer_id":890,"store_id":4,"shipping_address_id":879,"promotion_id":null,"order_date":"2023-07-10T18:10:10.000000","status":"completed","order_total":58.73,"_shape_table":"order","_shape_seq":3645,"_shape_event_time":"2023-07-10T18:10:10.000000"}
    {"order_id":2815,"customer_id":92,"store_id":1,"shipping_address_id":933,"promotion_id":null,"order_date":"2023-07-10T22:57:41.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2814,"_shape_event_time":"2023-07-10T22:57:41.000000"}
    {"order_id":469,"customer_id":9,"store_id":1,"shipping_address_id":1070,"promotion_id":null,"order_date":"2023-07-11T08:54:25.000000","status":"completed","order_total":17.67,"_shape_table":"order","_shape_seq":468,"_shape_event_time":"2023-07-11T08:54:25.000000"}
    {"order_id":827,"customer_id":3,"store_id":11,"shipping_address_id":746,"promotion_id":null,"order_date":"2023-07-11T22:31:09.000000","status":"shipped","order_total":211.41,"_shape_table":"order","_shape_seq":826,"_shape_event_time":"2023-07-11T22:31:09.000000"}
    {"order_id":4271,"customer_id":739,"store_id":31,"shipping_address_id":1221,"promotion_id":88,"order_date":"2023-07-12T21:22:38.000000","status":"completed","order_total":92.46,"_shape_table":"order","_shape_seq":4270,"_shape_event_time":"2023-07-12T21:22:38.000000"}
    {"order_id":3802,"customer_id":81,"store_id":2,"shipping_address_id":null,"promotion_id":2,"order_date":"2023-07-13T10:30:41.000000","status":"completed","order_total":77.37,"_shape_table":"order","_shape_seq":3801,"_shape_event_time":"2023-07-13T10:30:41.000000"}
    {"order_id":1030,"customer_id":561,"store_id":27,"shipping_address_id":1429,"promotion_id":31,"order_date":"2023-07-13T18:18:39.000000","status":"completed","order_total":7.54,"_shape_table":"order","_shape_seq":1029,"_shape_event_time":"2023-07-13T18:18:39.000000"}
    {"order_id":2082,"customer_id":28,"store_id":51,"shipping_address_id":427,"promotion_id":null,"order_date":"2023-07-14T10:28:48.000000","status":"completed","order_total":28.01,"_shape_table":"order","_shape_seq":2081,"_shape_event_time":"2023-07-14T10:28:48.000000"}
    {"order_id":3142,"customer_id":20,"store_id":1,"shipping_address_id":102,"promotion_id":null,"order_date":"2023-07-15T07:34:08.000000","status":"completed","order_total":76.92,"_shape_table":"order","_shape_seq":3141,"_shape_event_time":"2023-07-15T07:34:08.000000"}
    {"order_id":3369,"customer_id":870,"store_id":2,"shipping_address_id":884,"promotion_id":null,"order_date":"2023-07-15T12:45:43.948162","status":"completed","order_total":970.35,"_shape_table":"order","_shape_seq":3368,"_shape_event_time":"2023-07-15T12:45:43.948162"}
    {"order_id":1523,"customer_id":990,"store_id":1,"shipping_address_id":null,"promotion_id":71,"order_date":"2023-07-17T21:27:24.000000","status":"shipped","order_total":46.06,"_shape_table":"order","_shape_seq":1522,"_shape_event_time":"2023-07-17T21:27:24.000000"}
    {"order_id":3551,"customer_id":675,"store_id":131,"shipping_address_id":988,"promotion_id":null,"order_date":"2023-07-17T23:36:02.000000","status":"completed","order_total":27.97,"_shape_table":"order","_shape_seq":3550,"_shape_event_time":"2023-07-17T23:36:02.000000"}
    {"order_id":4804,"customer_id":757,"store_id":47,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-18T21:22:31.000000","status":"completed","order_total":150.33,"_shape_table":"order","_shape_seq":4803,"_shape_event_time":"2023-07-18T21:22:31.000000"}
    {"order_id":4091,"customer_id":158,"store_id":1,"shipping_address_id":1268,"promotion_id":null,"order_date":"2023-07-19T19:38:21.000000","status":"completed","order_total":169.56,"_shape_table":"order","_shape_seq":4090,"_shape_event_time":"2023-07-19T19:38:21.000000"}
    {"order_id":1722,"customer_id":27,"store_id":9,"shipping_address_id":675,"promotion_id":87,"order_date":"2023-07-20T06:11:17.000000","status":"cancelled","order_total":11.83,"_shape_table":"order","_shape_seq":1721,"_shape_event_time":"2023-07-20T06:11:17.000000"}
    {"order_id":4468,"customer_id":28,"store_id":9,"shipping_address_id":427,"promotion_id":null,"order_date":"2023-07-20T10:22:43.000000","status":"completed","order_total":41.26,"_shape_table":"order","_shape_seq":4467,"_shape_event_time":"2023-07-20T10:22:43.000000"}
    {"order_id":1435,"customer_id":890,"store_id":1,"shipping_address_id":879,"promotion_id":null,"order_date":"2023-07-20T22:03:31.000000","status":"returned","order_total":118.75,"_shape_table":"order","_shape_seq":1434,"_shape_event_time":"2023-07-20T22:03:31.000000"}
    {"order_id":1422,"customer_id":371,"store_id":22,"shipping_address_id":1123,"promotion_id":null,"order_date":"2023-07-22T08:10:42.000000","status":"completed","order_total":138.26,"_shape_table":"order","_shape_seq":1421,"_shape_event_time":"2023-07-22T08:10:42.000000"}
    {"order_id":2554,"customer_id":243,"store_id":10,"shipping_address_id":1085,"promotion_id":169,"order_date":"2023-07-22T10:05:20.000000","status":"completed","order_total":48.41,"_shape_table":"order","_shape_seq":2553,"_shape_event_time":"2023-07-22T10:05:20.000000"}
    {"order_id":3784,"customer_id":850,"store_id":2,"shipping_address_id":137,"promotion_id":null,"order_date":"2023-07-23T08:31:16.000000","status":"completed","order_total":414.26,"_shape_table":"order","_shape_seq":3783,"_shape_event_time":"2023-07-23T08:31:16.000000"}
    {"order_id":511,"customer_id":28,"store_id":14,"shipping_address_id":1464,"promotion_id":null,"order_date":"2023-07-23T14:45:47.000000","status":"returned","order_total":112.38,"_shape_table":"order","_shape_seq":510,"_shape_event_time":"2023-07-23T14:45:47.000000"}
    {"order_id":1748,"customer_id":881,"store_id":9,"shipping_address_id":364,"promotion_id":null,"order_date":"2023-07-24T08:32:38.000000","status":"completed","order_total":353.41,"_shape_table":"order","_shape_seq":1747,"_shape_event_time":"2023-07-24T08:32:38.000000"}
    {"order_id":1602,"customer_id":65,"store_id":69,"shipping_address_id":null,"promotion_id":91,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":45.39,"_shape_table":"order","_shape_seq":1601,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":1721,"customer_id":65,"store_id":19,"shipping_address_id":null,"promotion_id":28,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":21.59,"_shape_table":"order","_shape_seq":1720,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":1808,"customer_id":65,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":22.82,"_shape_table":"order","_shape_seq":1807,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":1887,"customer_id":65,"store_id":10,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":121.09,"_shape_table":"order","_shape_seq":1886,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":1994,"customer_id":65,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1993,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":2022,"customer_id":65,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":2021,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":2079,"customer_id":65,"store_id":44,"shipping_address_id":null,"promotion_id":115,"order_date":"2023-07-24T10:08:27.080177","status":"cancelled","order_total":41.39,"_shape_table":"order","_shape_seq":2078,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":2331,"customer_id":65,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":19.96,"_shape_table":"order","_shape_seq":2330,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":2602,"customer_id":65,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":35.18,"_shape_table":"order","_shape_seq":2601,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":2775,"customer_id":65,"store_id":10,"shipping_address_id":null,"promotion_id":181,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":81.76,"_shape_table":"order","_shape_seq":2774,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":3337,"customer_id":65,"store_id":4,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-24T10:08:27.080177","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":3336,"_shape_event_time":"2023-07-24T10:08:27.080177"}
    {"order_id":806,"customer_id":127,"store_id":1,"shipping_address_id":877,"promotion_id":122,"order_date":"2023-07-24T22:09:53.000000","status":"completed","order_total":80.1,"_shape_table":"order","_shape_seq":805,"_shape_event_time":"2023-07-24T22:09:53.000000"}
    {"order_id":3320,"customer_id":84,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-25T11:30:39.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3319,"_shape_event_time":"2023-07-25T11:30:39.000000"}
    {"order_id":1407,"customer_id":593,"store_id":5,"shipping_address_id":101,"promotion_id":null,"order_date":"2023-07-25T20:14:25.117585","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1406,"_shape_event_time":"2023-07-25T20:14:25.117585"}
    {"order_id":2605,"customer_id":593,"store_id":9,"shipping_address_id":863,"promotion_id":128,"order_date":"2023-07-25T20:14:25.117585","status":"shipped","order_total":43.25,"_shape_table":"order","_shape_seq":2604,"_shape_event_time":"2023-07-25T20:14:25.117585"}
    {"order_id":4030,"customer_id":803,"store_id":2,"shipping_address_id":783,"promotion_id":null,"order_date":"2023-07-26T01:56:55.000000","status":"completed","order_total":63.16,"_shape_table":"order","_shape_seq":4029,"_shape_event_time":"2023-07-26T01:56:55.000000"}
    {"order_id":503,"customer_id":15,"store_id":1,"shipping_address_id":695,"promotion_id":82,"order_date":"2023-07-26T11:04:25.000000","status":"completed","order_total":49.49,"_shape_table":"order","_shape_seq":502,"_shape_event_time":"2023-07-26T11:04:25.000000"}
    {"order_id":4868,"customer_id":62,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-26T17:37:00.895507","status":"shipped","order_total":277.7,"_shape_table":"order","_shape_seq":4867,"_shape_event_time":"2023-07-26T17:37:00.895507"}
    {"order_id":4774,"customer_id":859,"store_id":4,"shipping_address_id":549,"promotion_id":null,"order_date":"2023-07-27T07:45:20.000000","status":"completed","order_total":308.89,"_shape_table":"order","_shape_seq":4773,"_shape_event_time":"2023-07-27T07:45:20.000000"}
    {"order_id":3926,"customer_id":884,"store_id":3,"shipping_address_id":165,"promotion_id":188,"order_date":"2023-07-27T15:06:47.000000","status":"returned","order_total":331.0,"_shape_table":"order","_shape_seq":3925,"_shape_event_time":"2023-07-27T15:06:47.000000"}
    {"order_id":1041,"customer_id":291,"store_id":2,"shipping_address_id":183,"promotion_id":null,"order_date":"2023-07-27T19:10:54.000000","status":"completed","order_total":227.25,"_shape_table":"order","_shape_seq":1040,"_shape_event_time":"2023-07-27T19:10:54.000000"}
    {"order_id":4051,"customer_id":920,"store_id":3,"shipping_address_id":1357,"promotion_id":40,"order_date":"2023-07-28T07:18:03.000000","status":"processing","order_total":349.05,"_shape_table":"order","_shape_seq":4050,"_shape_event_time":"2023-07-28T07:18:03.000000"}
    {"order_id":267,"customer_id":29,"store_id":6,"shipping_address_id":1314,"promotion_id":190,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":266,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":335,"customer_id":29,"store_id":25,"shipping_address_id":1314,"promotion_id":171,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":334,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":818,"customer_id":29,"store_id":1,"shipping_address_id":1314,"promotion_id":57,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":239.69,"_shape_table":"order","_shape_seq":817,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":995,"customer_id":29,"store_id":3,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":61.99,"_shape_table":"order","_shape_seq":994,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":1361,"customer_id":29,"store_id":4,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":47.08,"_shape_table":"order","_shape_seq":1360,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":1681,"customer_id":29,"store_id":1,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"cancelled","order_total":82.99,"_shape_table":"order","_shape_seq":1680,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":1716,"customer_id":29,"store_id":1,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"processing","order_total":117.34,"_shape_table":"order","_shape_seq":1715,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":2007,"customer_id":29,"store_id":2,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":355.0,"_shape_table":"order","_shape_seq":2006,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":2325,"customer_id":29,"store_id":34,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":148.63,"_shape_table":"order","_shape_seq":2324,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":3080,"customer_id":29,"store_id":109,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":192.67,"_shape_table":"order","_shape_seq":3079,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":3185,"customer_id":29,"store_id":10,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3184,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":3251,"customer_id":29,"store_id":25,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"shipped","order_total":64.72,"_shape_table":"order","_shape_seq":3250,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":3357,"customer_id":29,"store_id":2,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3356,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":3638,"customer_id":29,"store_id":2,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":20.5,"_shape_table":"order","_shape_seq":3637,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":3822,"customer_id":29,"store_id":7,"shipping_address_id":1314,"promotion_id":null,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":22.96,"_shape_table":"order","_shape_seq":3821,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":4253,"customer_id":29,"store_id":108,"shipping_address_id":1314,"promotion_id":66,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":83.89,"_shape_table":"order","_shape_seq":4252,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":4576,"customer_id":29,"store_id":2,"shipping_address_id":1314,"promotion_id":134,"order_date":"2023-07-28T17:32:54.375880","status":"completed","order_total":8.42,"_shape_table":"order","_shape_seq":4575,"_shape_event_time":"2023-07-28T17:32:54.375880"}
    {"order_id":2977,"customer_id":762,"store_id":1,"shipping_address_id":1121,"promotion_id":6,"order_date":"2023-07-29T13:05:42.146344","status":"completed","order_total":53.96,"_shape_table":"order","_shape_seq":2976,"_shape_event_time":"2023-07-29T13:05:42.146344"}
    {"order_id":2589,"customer_id":726,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-07-31T05:55:52.000000","status":"completed","order_total":26.53,"_shape_table":"order","_shape_seq":2588,"_shape_event_time":"2023-07-31T05:55:52.000000"}
    {"order_id":3311,"customer_id":28,"store_id":1,"shipping_address_id":427,"promotion_id":43,"order_date":"2023-07-31T08:54:46.000000","status":"completed","order_total":15.95,"_shape_table":"order","_shape_seq":3310,"_shape_event_time":"2023-07-31T08:54:46.000000"}
    {"order_id":3509,"customer_id":45,"store_id":9,"shipping_address_id":1087,"promotion_id":170,"order_date":"2023-07-31T17:00:18.000000","status":"completed","order_total":11.74,"_shape_table":"order","_shape_seq":3508,"_shape_event_time":"2023-07-31T17:00:18.000000"}
    {"order_id":3058,"customer_id":517,"store_id":2,"shipping_address_id":1422,"promotion_id":null,"order_date":"2023-08-01T02:58:28.000000","status":"cancelled","order_total":0.0,"_shape_table":"order","_shape_seq":3057,"_shape_event_time":"2023-08-01T02:58:28.000000"}
    {"order_id":2290,"customer_id":572,"store_id":35,"shipping_address_id":1003,"promotion_id":null,"order_date":"2023-08-01T13:08:12.000000","status":"completed","order_total":55.48,"_shape_table":"order","_shape_seq":2289,"_shape_event_time":"2023-08-01T13:08:12.000000"}
    {"order_id":1599,"customer_id":13,"store_id":8,"shipping_address_id":559,"promotion_id":null,"order_date":"2023-08-02T02:15:16.000000","status":"completed","order_total":106.87,"_shape_table":"order","_shape_seq":1598,"_shape_event_time":"2023-08-02T02:15:16.000000"}
    {"order_id":3584,"customer_id":511,"store_id":3,"shipping_address_id":978,"promotion_id":null,"order_date":"2023-08-02T05:34:28.586253","status":"completed","order_total":164.45,"_shape_table":"order","_shape_seq":3583,"_shape_event_time":"2023-08-02T05:34:28.586253"}
    {"order_id":4374,"customer_id":361,"store_id":6,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-02T11:11:13.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":4373,"_shape_event_time":"2023-08-02T11:11:13.000000"}
    {"order_id":634,"customer_id":23,"store_id":3,"shipping_address_id":1073,"promotion_id":null,"order_date":"2023-08-03T23:13:44.272634","status":"completed","order_total":75.24,"_shape_table":"order","_shape_seq":633,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":1074,"customer_id":23,"store_id":2,"shipping_address_id":591,"promotion_id":null,"order_date":"2023-08-03T23:13:44.272634","status":"cancelled","order_total":216.24,"_shape_table":"order","_shape_seq":1073,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":1223,"customer_id":23,"store_id":30,"shipping_address_id":1073,"promotion_id":null,"order_date":"2023-08-03T23:13:44.272634","status":"completed","order_total":174.05,"_shape_table":"order","_shape_seq":1222,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":1925,"customer_id":23,"store_id":10,"shipping_address_id":591,"promotion_id":126,"order_date":"2023-08-03T23:13:44.272634","status":"returned","order_total":82.79,"_shape_table":"order","_shape_seq":1924,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":2680,"customer_id":23,"store_id":8,"shipping_address_id":591,"promotion_id":null,"order_date":"2023-08-03T23:13:44.272634","status":"completed","order_total":1172.45,"_shape_table":"order","_shape_seq":2679,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":2932,"customer_id":23,"store_id":24,"shipping_address_id":591,"promotion_id":183,"order_date":"2023-08-03T23:13:44.272634","status":"returned","order_total":16.7,"_shape_table":"order","_shape_seq":2931,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":2958,"customer_id":23,"store_id":3,"shipping_address_id":591,"promotion_id":null,"order_date":"2023-08-03T23:13:44.272634","status":"returned","order_total":104.44,"_shape_table":"order","_shape_seq":2957,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":2967,"customer_id":23,"store_id":2,"shipping_address_id":591,"promotion_id":51,"order_date":"2023-08-03T23:13:44.272634","status":"completed","order_total":9.92,"_shape_table":"order","_shape_seq":2966,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":3512,"customer_id":23,"store_id":3,"shipping_address_id":591,"promotion_id":null,"order_date":"2023-08-03T23:13:44.272634","status":"completed","order_total":23.4,"_shape_table":"order","_shape_seq":3511,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":3964,"customer_id":23,"store_id":2,"shipping_address_id":591,"promotion_id":null,"order_date":"2023-08-03T23:13:44.272634","status":"completed","order_total":20.31,"_shape_table":"order","_shape_seq":3963,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":3988,"customer_id":23,"store_id":5,"shipping_address_id":591,"promotion_id":111,"order_date":"2023-08-03T23:13:44.272634","status":"shipped","order_total":8.42,"_shape_table":"order","_shape_seq":3987,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":4221,"customer_id":23,"store_id":38,"shipping_address_id":1073,"promotion_id":null,"order_date":"2023-08-03T23:13:44.272634","status":"shipped","order_total":167.25,"_shape_table":"order","_shape_seq":4220,"_shape_event_time":"2023-08-03T23:13:44.272634"}
    {"order_id":3554,"customer_id":599,"store_id":4,"shipping_address_id":1303,"promotion_id":null,"order_date":"2023-08-04T03:43:29.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3553,"_shape_event_time":"2023-08-04T03:43:29.000000"}
    {"order_id":1777,"customer_id":54,"store_id":4,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-04T04:31:44.696500","status":"shipped","order_total":55.69,"_shape_table":"order","_shape_seq":1776,"_shape_event_time":"2023-08-04T04:31:44.696500"}
    {"order_id":2853,"customer_id":54,"store_id":1,"shipping_address_id":null,"promotion_id":77,"order_date":"2023-08-04T04:31:44.696500","status":"completed","order_total":24.65,"_shape_table":"order","_shape_seq":2852,"_shape_event_time":"2023-08-04T04:31:44.696500"}
    {"order_id":3487,"customer_id":54,"store_id":24,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-04T04:31:44.696500","status":"cancelled","order_total":0.0,"_shape_table":"order","_shape_seq":3486,"_shape_event_time":"2023-08-04T04:31:44.696500"}
    {"order_id":3765,"customer_id":54,"store_id":1,"shipping_address_id":null,"promotion_id":177,"order_date":"2023-08-04T04:31:44.696500","status":"shipped","order_total":21.84,"_shape_table":"order","_shape_seq":3764,"_shape_event_time":"2023-08-04T04:31:44.696500"}
    {"order_id":3818,"customer_id":54,"store_id":22,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-04T04:31:44.696500","status":"shipped","order_total":28.73,"_shape_table":"order","_shape_seq":3817,"_shape_event_time":"2023-08-04T04:31:44.696500"}
    {"order_id":3927,"customer_id":54,"store_id":2,"shipping_address_id":null,"promotion_id":200,"order_date":"2023-08-04T04:31:44.696500","status":"completed","order_total":29.64,"_shape_table":"order","_shape_seq":3926,"_shape_event_time":"2023-08-04T04:31:44.696500"}
    {"order_id":2934,"customer_id":522,"store_id":5,"shipping_address_id":198,"promotion_id":null,"order_date":"2023-08-04T20:15:41.122394","status":"shipped","order_total":71.1,"_shape_table":"order","_shape_seq":2933,"_shape_event_time":"2023-08-04T20:15:41.122394"}
    {"order_id":3912,"customer_id":522,"store_id":33,"shipping_address_id":198,"promotion_id":51,"order_date":"2023-08-04T20:15:41.122394","status":"returned","order_total":9.96,"_shape_table":"order","_shape_seq":3911,"_shape_event_time":"2023-08-04T20:15:41.122394"}
    {"order_id":570,"customer_id":50,"store_id":18,"shipping_address_id":null,"promotion_id":156,"order_date":"2023-08-05T04:00:46.694063","status":"completed","order_total":70.85,"_shape_table":"order","_shape_seq":569,"_shape_event_time":"2023-08-05T04:00:46.694063"}
    {"order_id":2824,"customer_id":50,"store_id":19,"shipping_address_id":null,"promotion_id":171,"order_date":"2023-08-05T04:00:46.694063","status":"processing","order_total":23.8,"_shape_table":"order","_shape_seq":2823,"_shape_event_time":"2023-08-05T04:00:46.694063"}
    {"order_id":2831,"customer_id":50,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-05T04:00:46.694063","status":"completed","order_total":8.86,"_shape_table":"order","_shape_seq":2830,"_shape_event_time":"2023-08-05T04:00:46.694063"}
    {"order_id":2938,"customer_id":50,"store_id":17,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-05T04:00:46.694063","status":"shipped","order_total":14.68,"_shape_table":"order","_shape_seq":2937,"_shape_event_time":"2023-08-05T04:00:46.694063"}
    {"order_id":3168,"customer_id":50,"store_id":14,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-05T04:00:46.694063","status":"completed","order_total":528.04,"_shape_table":"order","_shape_seq":3167,"_shape_event_time":"2023-08-05T04:00:46.694063"}
    {"order_id":4313,"customer_id":195,"store_id":11,"shipping_address_id":1219,"promotion_id":133,"order_date":"2023-08-07T06:57:16.000000","status":"completed","order_total":182.79,"_shape_table":"order","_shape_seq":4312,"_shape_event_time":"2023-08-07T06:57:16.000000"}
    {"order_id":1557,"customer_id":245,"store_id":2,"shipping_address_id":1026,"promotion_id":null,"order_date":"2023-08-07T10:23:45.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":1556,"_shape_event_time":"2023-08-07T10:23:45.000000"}
    {"order_id":1649,"customer_id":450,"store_id":1,"shipping_address_id":567,"promotion_id":null,"order_date":"2023-08-07T13:39:58.000000","status":"shipped","order_total":36.69,"_shape_table":"order","_shape_seq":1648,"_shape_event_time":"2023-08-07T13:39:58.000000"}
    {"order_id":2404,"customer_id":148,"store_id":2,"shipping_address_id":806,"promotion_id":null,"order_date":"2023-08-08T10:12:00.662527","status":"completed","order_total":103.99,"_shape_table":"order","_shape_seq":2403,"_shape_event_time":"2023-08-08T10:12:00.662527"}
    {"order_id":2443,"customer_id":148,"store_id":9,"shipping_address_id":901,"promotion_id":null,"order_date":"2023-08-08T10:12:00.662527","status":"completed","order_total":676.69,"_shape_table":"order","_shape_seq":2442,"_shape_event_time":"2023-08-08T10:12:00.662527"}
    {"order_id":723,"customer_id":15,"store_id":2,"shipping_address_id":695,"promotion_id":null,"order_date":"2023-08-09T01:51:33.000000","status":"shipped","order_total":48.59,"_shape_table":"order","_shape_seq":722,"_shape_event_time":"2023-08-09T01:51:33.000000"}
    {"order_id":1013,"customer_id":16,"store_id":3,"shipping_address_id":8,"promotion_id":null,"order_date":"2023-08-09T06:58:00.000000","status":"completed","order_total":216.24,"_shape_table":"order","_shape_seq":1012,"_shape_event_time":"2023-08-09T06:58:00.000000"}
    {"order_id":1159,"customer_id":53,"store_id":1,"shipping_address_id":1350,"promotion_id":28,"order_date":"2023-08-09T08:21:54.701186","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1158,"_shape_event_time":"2023-08-09T08:21:54.701186"}
    {"order_id":1985,"customer_id":53,"store_id":97,"shipping_address_id":1350,"promotion_id":58,"order_date":"2023-08-09T08:21:54.701186","status":"completed","order_total":11.55,"_shape_table":"order","_shape_seq":1984,"_shape_event_time":"2023-08-09T08:21:54.701186"}
    {"order_id":826,"customer_id":734,"store_id":1,"shipping_address_id":null,"promotion_id":189,"order_date":"2023-08-10T04:35:09.000000","status":"completed","order_total":53.82,"_shape_table":"order","_shape_seq":825,"_shape_event_time":"2023-08-10T04:35:09.000000"}
    {"order_id":14,"customer_id":29,"store_id":6,"shipping_address_id":1314,"promotion_id":68,"order_date":"2023-08-10T09:42:06.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":13,"_shape_event_time":"2023-08-10T09:42:06.000000"}
    {"order_id":1160,"customer_id":16,"store_id":52,"shipping_address_id":8,"promotion_id":76,"order_date":"2023-08-10T16:40:52.000000","status":"completed","order_total":64.49,"_shape_table":"order","_shape_seq":1159,"_shape_event_time":"2023-08-10T16:40:52.000000"}
    {"order_id":1103,"customer_id":27,"store_id":2,"shipping_address_id":675,"promotion_id":null,"order_date":"2023-08-11T10:25:58.000000","status":"completed","order_total":48.63,"_shape_table":"order","_shape_seq":1102,"_shape_event_time":"2023-08-11T10:25:58.000000"}
    {"order_id":3230,"customer_id":16,"store_id":2,"shipping_address_id":8,"promotion_id":null,"order_date":"2023-08-11T10:35:06.000000","status":"returned","order_total":216.24,"_shape_table":"order","_shape_seq":3229,"_shape_event_time":"2023-08-11T10:35:06.000000"}
    {"order_id":3960,"customer_id":45,"store_id":8,"shipping_address_id":1087,"promotion_id":65,"order_date":"2023-08-11T16:21:16.000000","status":"completed","order_total":7.6,"_shape_table":"order","_shape_seq":3959,"_shape_event_time":"2023-08-11T16:21:16.000000"}
    {"order_id":4307,"customer_id":26,"store_id":138,"shipping_address_id":66,"promotion_id":119,"order_date":"2023-08-12T13:16:10.000000","status":"completed","order_total":79.88,"_shape_table":"order","_shape_seq":4306,"_shape_event_time":"2023-08-12T13:16:10.000000"}
    {"order_id":4511,"customer_id":589,"store_id":1,"shipping_address_id":959,"promotion_id":165,"order_date":"2023-08-12T18:21:01.716844","status":"completed","order_total":37.44,"_shape_table":"order","_shape_seq":4510,"_shape_event_time":"2023-08-12T18:21:01.716844"}
    {"order_id":3034,"customer_id":41,"store_id":1,"shipping_address_id":1154,"promotion_id":null,"order_date":"2023-08-12T18:50:28.000000","status":"processing","order_total":193.59,"_shape_table":"order","_shape_seq":3033,"_shape_event_time":"2023-08-12T18:50:28.000000"}
    {"order_id":3123,"customer_id":37,"store_id":140,"shipping_address_id":735,"promotion_id":null,"order_date":"2023-08-12T22:27:55.000000","status":"completed","order_total":35.18,"_shape_table":"order","_shape_seq":3122,"_shape_event_time":"2023-08-12T22:27:55.000000"}
    {"order_id":3021,"customer_id":660,"store_id":3,"shipping_address_id":1185,"promotion_id":null,"order_date":"2023-08-13T14:09:28.000000","status":"cancelled","order_total":51.48,"_shape_table":"order","_shape_seq":3020,"_shape_event_time":"2023-08-13T14:09:28.000000"}
    {"order_id":2353,"customer_id":26,"store_id":75,"shipping_address_id":39,"promotion_id":null,"order_date":"2023-08-13T17:56:36.000000","status":"completed","order_total":49.91,"_shape_table":"order","_shape_seq":2352,"_shape_event_time":"2023-08-13T17:56:36.000000"}
    {"order_id":120,"customer_id":6,"store_id":3,"shipping_address_id":823,"promotion_id":null,"order_date":"2023-08-13T18:54:09.000000","status":"shipped","order_total":88.83,"_shape_table":"order","_shape_seq":119,"_shape_event_time":"2023-08-13T18:54:09.000000"}
    {"order_id":3374,"customer_id":352,"store_id":1,"shipping_address_id":1426,"promotion_id":null,"order_date":"2023-08-16T04:38:19.000000","status":"cancelled","order_total":4.43,"_shape_table":"order","_shape_seq":3373,"_shape_event_time":"2023-08-16T04:38:19.000000"}
    {"order_id":3082,"customer_id":53,"store_id":1,"shipping_address_id":1350,"promotion_id":183,"order_date":"2023-08-16T07:42:10.000000","status":"completed","order_total":62.02,"_shape_table":"order","_shape_seq":3081,"_shape_event_time":"2023-08-16T07:42:10.000000"}
    {"order_id":778,"customer_id":50,"store_id":6,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-17T02:52:46.000000","status":"completed","order_total":62.85,"_shape_table":"order","_shape_seq":777,"_shape_event_time":"2023-08-17T02:52:46.000000"}
    {"order_id":3549,"customer_id":27,"store_id":1,"shipping_address_id":675,"promotion_id":null,"order_date":"2023-08-18T00:35:22.000000","status":"completed","order_total":156.29,"_shape_table":"order","_shape_seq":3548,"_shape_event_time":"2023-08-18T00:35:22.000000"}
    {"order_id":3691,"customer_id":546,"store_id":4,"shipping_address_id":81,"promotion_id":null,"order_date":"2023-08-18T05:32:10.000000","status":"returned","order_total":0.0,"_shape_table":"order","_shape_seq":3690,"_shape_event_time":"2023-08-18T05:32:10.000000"}
    {"order_id":1754,"customer_id":757,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-18T07:31:56.000000","status":"completed","order_total":55.94,"_shape_table":"order","_shape_seq":1753,"_shape_event_time":"2023-08-18T07:31:56.000000"}
    {"order_id":2622,"customer_id":334,"store_id":94,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-18T18:24:47.000000","status":"completed","order_total":327.96,"_shape_table":"order","_shape_seq":2621,"_shape_event_time":"2023-08-18T18:24:47.000000"}
    {"order_id":4426,"customer_id":892,"store_id":2,"shipping_address_id":233,"promotion_id":null,"order_date":"2023-08-18T21:40:59.000000","status":"returned","order_total":43.32,"_shape_table":"order","_shape_seq":4425,"_shape_event_time":"2023-08-18T21:40:59.000000"}
    {"order_id":1365,"customer_id":696,"store_id":58,"shipping_address_id":730,"promotion_id":null,"order_date":"2023-08-19T00:32:26.638531","status":"returned","order_total":26.81,"_shape_table":"order","_shape_seq":1364,"_shape_event_time":"2023-08-19T00:32:26.638531"}
    {"order_id":1692,"customer_id":696,"store_id":2,"shipping_address_id":730,"promotion_id":null,"order_date":"2023-08-19T00:32:26.638531","status":"returned","order_total":93.79,"_shape_table":"order","_shape_seq":1691,"_shape_event_time":"2023-08-19T00:32:26.638531"}
    {"order_id":370,"customer_id":38,"store_id":5,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-19T11:44:34.000000","status":"completed","order_total":105.34,"_shape_table":"order","_shape_seq":369,"_shape_event_time":"2023-08-19T11:44:34.000000"}
    {"order_id":3505,"customer_id":285,"store_id":1,"shipping_address_id":841,"promotion_id":null,"order_date":"2023-08-19T14:07:23.000000","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3504,"_shape_event_time":"2023-08-19T14:07:23.000000"}
    {"order_id":4281,"customer_id":171,"store_id":1,"shipping_address_id":1272,"promotion_id":null,"order_date":"2023-08-20T15:24:16.761520","status":"completed","order_total":53.4,"_shape_table":"order","_shape_seq":4280,"_shape_event_time":"2023-08-20T15:24:16.761520"}
    {"order_id":4948,"customer_id":171,"store_id":5,"shipping_address_id":1272,"promotion_id":57,"order_date":"2023-08-20T15:24:16.761520","status":"completed","order_total":393.22,"_shape_table":"order","_shape_seq":4947,"_shape_event_time":"2023-08-20T15:24:16.761520"}
    {"order_id":773,"customer_id":6,"store_id":11,"shipping_address_id":823,"promotion_id":null,"order_date":"2023-08-21T19:40:37.000000","status":"returned","order_total":0.0,"_shape_table":"order","_shape_seq":772,"_shape_event_time":"2023-08-21T19:40:37.000000"}
    {"order_id":1104,"customer_id":96,"store_id":2,"shipping_address_id":1057,"promotion_id":null,"order_date":"2023-08-22T04:10:29.375943","status":"completed","order_total":19.96,"_shape_table":"order","_shape_seq":1103,"_shape_event_time":"2023-08-22T04:10:29.375943"}
    {"order_id":1889,"customer_id":96,"store_id":1,"shipping_address_id":1251,"promotion_id":null,"order_date":"2023-08-22T04:10:29.375943","status":"completed","order_total":22.82,"_shape_table":"order","_shape_seq":1888,"_shape_event_time":"2023-08-22T04:10:29.375943"}
    {"order_id":2655,"customer_id":96,"store_id":8,"shipping_address_id":1251,"promotion_id":null,"order_date":"2023-08-22T04:10:29.375943","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":2654,"_shape_event_time":"2023-08-22T04:10:29.375943"}
    {"order_id":4267,"customer_id":50,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-22T17:21:10.000000","status":"completed","order_total":136.11,"_shape_table":"order","_shape_seq":4266,"_shape_event_time":"2023-08-22T17:21:10.000000"}
    {"order_id":1178,"customer_id":16,"store_id":1,"shipping_address_id":8,"promotion_id":null,"order_date":"2023-08-22T20:28:20.000000","status":"returned","order_total":916.29,"_shape_table":"order","_shape_seq":1177,"_shape_event_time":"2023-08-22T20:28:20.000000"}
    {"order_id":3592,"customer_id":991,"store_id":50,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-23T01:28:52.182930","status":"completed","order_total":29.36,"_shape_table":"order","_shape_seq":3591,"_shape_event_time":"2023-08-23T01:28:52.182930"}
    {"order_id":3190,"customer_id":573,"store_id":5,"shipping_address_id":391,"promotion_id":null,"order_date":"2023-08-23T08:51:40.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":3189,"_shape_event_time":"2023-08-23T08:51:40.000000"}
    {"order_id":2641,"customer_id":577,"store_id":13,"shipping_address_id":1036,"promotion_id":null,"order_date":"2023-08-23T20:06:45.630947","status":"completed","order_total":220.67,"_shape_table":"order","_shape_seq":2640,"_shape_event_time":"2023-08-23T20:06:45.630947"}
    {"order_id":3932,"customer_id":577,"store_id":2,"shipping_address_id":1036,"promotion_id":null,"order_date":"2023-08-23T20:06:45.630947","status":"completed","order_total":26.53,"_shape_table":"order","_shape_seq":3931,"_shape_event_time":"2023-08-23T20:06:45.630947"}
    {"order_id":2704,"customer_id":848,"store_id":1,"shipping_address_id":1292,"promotion_id":null,"order_date":"2023-08-24T12:06:54.568327","status":"cancelled","order_total":48.47,"_shape_table":"order","_shape_seq":2703,"_shape_event_time":"2023-08-24T12:06:54.568327"}
    {"order_id":3723,"customer_id":848,"store_id":84,"shipping_address_id":1292,"promotion_id":null,"order_date":"2023-08-24T12:06:54.568327","status":"completed","order_total":17.72,"_shape_table":"order","_shape_seq":3722,"_shape_event_time":"2023-08-24T12:06:54.568327"}
    {"order_id":4945,"customer_id":50,"store_id":4,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-24T17:22:59.000000","status":"completed","order_total":89.48,"_shape_table":"order","_shape_seq":4944,"_shape_event_time":"2023-08-24T17:22:59.000000"}
    {"order_id":572,"customer_id":25,"store_id":1,"shipping_address_id":450,"promotion_id":null,"order_date":"2023-08-24T21:12:39.000000","status":"completed","order_total":117.08,"_shape_table":"order","_shape_seq":571,"_shape_event_time":"2023-08-24T21:12:39.000000"}
    {"order_id":3006,"customer_id":23,"store_id":26,"shipping_address_id":591,"promotion_id":null,"order_date":"2023-08-26T02:42:21.000000","status":"completed","order_total":970.36,"_shape_table":"order","_shape_seq":3005,"_shape_event_time":"2023-08-26T02:42:21.000000"}
    {"order_id":3705,"customer_id":601,"store_id":121,"shipping_address_id":140,"promotion_id":null,"order_date":"2023-08-26T16:23:55.000000","status":"completed","order_total":12.49,"_shape_table":"order","_shape_seq":3704,"_shape_event_time":"2023-08-26T16:23:55.000000"}
    {"order_id":2627,"customer_id":861,"store_id":9,"shipping_address_id":713,"promotion_id":null,"order_date":"2023-08-27T03:44:34.000000","status":"completed","order_total":4.43,"_shape_table":"order","_shape_seq":2626,"_shape_event_time":"2023-08-27T03:44:34.000000"}
    {"order_id":1978,"customer_id":23,"store_id":3,"shipping_address_id":1073,"promotion_id":null,"order_date":"2023-08-27T22:58:14.000000","status":"completed","order_total":90.73,"_shape_table":"order","_shape_seq":1977,"_shape_event_time":"2023-08-27T22:58:14.000000"}
    {"order_id":3741,"customer_id":941,"store_id":29,"shipping_address_id":1257,"promotion_id":152,"order_date":"2023-08-28T13:13:17.000000","status":"shipped","order_total":23.25,"_shape_table":"order","_shape_seq":3740,"_shape_event_time":"2023-08-28T13:13:17.000000"}
    {"order_id":756,"customer_id":6,"store_id":1,"shipping_address_id":823,"promotion_id":54,"order_date":"2023-08-28T16:08:33.000000","status":"processing","order_total":7.09,"_shape_table":"order","_shape_seq":755,"_shape_event_time":"2023-08-28T16:08:33.000000"}
    {"order_id":1814,"customer_id":754,"store_id":1,"shipping_address_id":1047,"promotion_id":null,"order_date":"2023-08-28T18:22:00.000000","status":"completed","order_total":33.76,"_shape_table":"order","_shape_seq":1813,"_shape_event_time":"2023-08-28T18:22:00.000000"}
    {"order_id":2995,"customer_id":292,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-08-28T20:50:47.000000","status":"completed","order_total":134.36,"_shape_table":"order","_shape_seq":2994,"_shape_event_time":"2023-08-28T20:50:47.000000"}
    {"order_id":859,"customer_id":111,"store_id":14,"shipping_address_id":34,"promotion_id":171,"order_date":"2023-08-31T03:42:08.000000","status":"completed","order_total":50.76,"_shape_table":"order","_shape_seq":858,"_shape_event_time":"2023-08-31T03:42:08.000000"}
    {"order_id":173,"customer_id":4,"store_id":4,"shipping_address_id":1091,"promotion_id":null,"order_date":"2023-08-31T09:23:06.000000","status":"completed","order_total":104.42,"_shape_table":"order","_shape_seq":172,"_shape_event_time":"2023-08-31T09:23:06.000000"}
    {"order_id":1639,"customer_id":458,"store_id":1,"shipping_address_id":null,"promotion_id":127,"order_date":"2023-09-01T17:56:42.994928","status":"completed","order_total":3.54,"_shape_table":"order","_shape_seq":1638,"_shape_event_time":"2023-09-01T17:56:42.994928"}
    {"order_id":519,"customer_id":16,"store_id":5,"shipping_address_id":900,"promotion_id":null,"order_date":"2023-09-01T23:58:02.000000","status":"completed","order_total":1087.18,"_shape_table":"order","_shape_seq":518,"_shape_event_time":"2023-09-01T23:58:02.000000"}
    {"order_id":9,"customer_id":10,"store_id":1,"shipping_address_id":null,"promotion_id":56,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":34.96,"_shape_table":"order","_shape_seq":8,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":237,"customer_id":10,"store_id":1,"shipping_address_id":null,"promotion_id":149,"order_date":"2023-09-02T18:42:49.671379","status":"returned","order_total":90.4,"_shape_table":"order","_shape_seq":236,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":268,"customer_id":10,"store_id":7,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":267,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":298,"customer_id":10,"store_id":132,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"shipped","order_total":35.54,"_shape_table":"order","_shape_seq":297,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":526,"customer_id":10,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":33.23,"_shape_table":"order","_shape_seq":525,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":586,"customer_id":10,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":45.3,"_shape_table":"order","_shape_seq":585,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":691,"customer_id":10,"store_id":6,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":41.0,"_shape_table":"order","_shape_seq":690,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":715,"customer_id":10,"store_id":30,"shipping_address_id":null,"promotion_id":2,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":103.23,"_shape_table":"order","_shape_seq":714,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":725,"customer_id":10,"store_id":56,"shipping_address_id":null,"promotion_id":47,"order_date":"2023-09-02T18:42:49.671379","status":"processing","order_total":17.42,"_shape_table":"order","_shape_seq":724,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1157,"customer_id":10,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":192.35,"_shape_table":"order","_shape_seq":1156,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1232,"customer_id":10,"store_id":24,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"shipped","order_total":58.33,"_shape_table":"order","_shape_seq":1231,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1276,"customer_id":10,"store_id":1,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":27.13,"_shape_table":"order","_shape_seq":1275,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1297,"customer_id":10,"store_id":3,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":203.83,"_shape_table":"order","_shape_seq":1296,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1341,"customer_id":10,"store_id":13,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":244.21,"_shape_table":"order","_shape_seq":1340,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1426,"customer_id":10,"store_id":5,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"returned","order_total":4.43,"_shape_table":"order","_shape_seq":1425,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1447,"customer_id":10,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":28.5,"_shape_table":"order","_shape_seq":1446,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1555,"customer_id":10,"store_id":33,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":326.9,"_shape_table":"order","_shape_seq":1554,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":1628,"customer_id":10,"store_id":2,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-02T18:42:49.671379","status":"completed","order_total":20.0,"_shape_table":"order","_shape_seq":1627,"_shape_event_time":"2023-09-02T18:42:49.671379"}
    {"order_id":2464,"customer_id":223,"store_id":8,"shipping_address_id":1192,"promotion_id":66,"order_date":"2023-09-02T21:55:33.601039","status":"completed","order_total":1092.63,"_shape_table":"order","_shape_seq":2463,"_shape_event_time":"2023-09-02T21:55:33.601039"}
    {"order_id":3703,"customer_id":726,"store_id":15,"shipping_address_id":null,"promotion_id":null,"order_date":"2023-09-04T04:44:51.000000","status":"completed","order_total":690.21,"_shape_table":"order","_shape_seq":3702,"_shape_event_time":"2023-09-04T04:44:51.000000"}
    {"order_id":4898,"customer_id":273,"store_id":1,"shipping_address_id":283,"promotion_id":190,"order_date":"2023-09-04T20:00:50.000000","status":"returned","order_total":63.79,"_shape_table":"order","_shape_seq":4897,"_shape_event_time":"2023-09-04T20:00:50.000000"}
    {"order_id":1918,"customer_id":366,"store_id":3,"shipping_address_id":1456,"promotion_id":null,"order_date":"2023-09-05T09:25:41.068145","status":"returned","order_total":25.78,"_shape_table":"order","_shape_seq":1917,"_shape_event_time":"2023-09-05T09:25:41.068145"}
    {"order_id":12,"customer_id":18,"store_id":1,"shipping_address_id":1258,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":10.25,"_shape_table":"order","_shape_seq":11,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":60,"customer_id":18,"store_id":35,"shipping_address_id":1496,"promotion_id":9,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":51.75,"_shape_table":"order","_shape_seq":59,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":399,"customer_id":18,"store_id":5,"shipping_address_id":189,"promotion_id":96,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":27.49,"_shape_table":"order","_shape_seq":398,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":653,"customer_id":18,"store_id":5,"shipping_address_id":1258,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":432.48,"_shape_table":"order","_shape_seq":652,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":656,"customer_id":18,"store_id":68,"shipping_address_id":1258,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":19.11,"_shape_table":"order","_shape_seq":655,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":687,"customer_id":18,"store_id":11,"shipping_address_id":1496,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":19.11,"_shape_table":"order","_shape_seq":686,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":1152,"customer_id":18,"store_id":8,"shipping_address_id":1496,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":182.42,"_shape_table":"order","_shape_seq":1151,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":1335,"customer_id":18,"store_id":1,"shipping_address_id":1258,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"returned","order_total":115.08,"_shape_table":"order","_shape_seq":1334,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":1720,"customer_id":18,"store_id":1,"shipping_address_id":1134,"promotion_id":48,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":1719,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":1862,"customer_id":18,"store_id":6,"shipping_address_id":1496,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":35.18,"_shape_table":"order","_shape_seq":1861,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":2881,"customer_id":18,"store_id":10,"shipping_address_id":1180,"promotion_id":111,"order_date":"2023-09-07T10:23:54.496081","status":"returned","order_total":44.13,"_shape_table":"order","_shape_seq":2880,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3170,"customer_id":18,"store_id":1,"shipping_address_id":1134,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"processing","order_total":8.86,"_shape_table":"order","_shape_seq":3169,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3346,"customer_id":18,"store_id":35,"shipping_address_id":1180,"promotion_id":7,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":35.22,"_shape_table":"order","_shape_seq":3345,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3498,"customer_id":18,"store_id":2,"shipping_address_id":135,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":44.44,"_shape_table":"order","_shape_seq":3497,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3542,"customer_id":18,"store_id":59,"shipping_address_id":1496,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"cancelled","order_total":84.5,"_shape_table":"order","_shape_seq":3541,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3634,"customer_id":18,"store_id":30,"shipping_address_id":135,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"shipped","order_total":102.77,"_shape_table":"order","_shape_seq":3633,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3701,"customer_id":18,"store_id":3,"shipping_address_id":1134,"promotion_id":193,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":0.0,"_shape_table":"order","_shape_seq":3700,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3759,"customer_id":18,"store_id":3,"shipping_address_id":1180,"promotion_id":175,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":203.78,"_shape_table":"order","_shape_seq":3758,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3862,"customer_id":18,"store_id":15,"shipping_address_id":189,"promotion_id":null,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":4.94,"_shape_table":"order","_shape_seq":3861,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":3867,"customer_id":18,"store_id":89,"shipping_address_id":135,"promotion_id":28,"order_date":"2023-09-07T10:23:54.496081","status":"completed","order_total":31.98,"_shape_table":"order","_shape_seq":3866,"_shape_event_time":"2023-09-07T10:23:54.496081"}
    {"order_id":2431,"customer_id":130,"store_id":1,"shipping_address_id":1373,"promotion_id":118,"order_date":"2023-09-07T18:21:49.135936","status":"completed","order_total":13.21,"_shape_table":"order","_shape_seq":2430,"_shape_event_time":"2023-09-07T18:21:49.135936"}
    {"order_id":4334,"customer_id":130,"store_id":1,"shipping_address_id":1373,"promotion_id":null,"order_date":"2023-09-07T18:21:49.135936","status":"completed","order_total":96.54,"_shape_table":"order","_shape_seq":4333,"_shape_event_time":"2023-09-07T18:21:49.135936"}
    shape stream: 1,000 events delivered, offset 1,000 of 5,000, max-events, 438,112 events/s
    shape stream: 5,000 events delivered, offset 5,000 of 5,000, complete, 500 events/s
    ```

<a id="local-example-4"></a>

### Example 5

<!-- example: 4 -->

```bash {.runnable-reference}
shape emit retail --table order_line --realtime --rate 200 --burst 30:10:4 \
    --sink kafka://broker:9092/orders --dead-letter file:///dlq.jsonl \
    --checkpoint ck.json --dry-run            # text
shape emit retail --scale small --max-events 5 --dry-run --json                # {"format": "shape-dry-run", ..., "plan": {"format": "shape-emit-plan", ...}}
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape emit (dry run): nothing was opened, written or sent

    target        retail (seed 42, scale default)
      table       order_line: 12,500 rows
    events        12,500 events (12,500 in the stream, row order)
    format        json (flat envelope)
    destinations
      sink        kafka://broker:9092/orders  [emitter via plugin kafka]
      dead-letter file:///dlq.jsonl  [emitter via plugin file]
    checkpoint    ck.json: fresh start
    pacing        realtime, 200 events/s
      duration    about 38.1 s
      peak        48,000 events/minute
    would create ck.json
    would send   kafka://broker:9092/orders
    {
      "format": "shape-dry-run",
      "version": 1,
      "command": "emit",
      "actions": [],
      "plan": {
        "format": "shape-emit-plan",
        "version": 1,
        "command": "emit",
        "target": {
          "name": "retail",
          "mode": null,
          "seed": 42,
          "scale": "small",
          "tables": [
            {
              "name": "customer",
              "rows": 1000
            },
            {
              "name": "address",
              "rows": 1500
            },
            {
              "name": "product_category",
              "rows": 50
            },
            {
              "name": "product",
              "rows": 500
            },
            {
              "name": "promotion",
              "rows": 200
            },
            {
              "name": "store",
              "rows": 150
            },
            {
              "name": "order",
              "rows": 5000
            },
            {
              "name": "order_line",
              "rows": 12500
            },
            {
              "name": "return",
              "rows": 850
            }
          ],
          "total_events": 21750
        },
        "event_order": "row",
        "limits": {
          "max_events": 5,
          "duration": null,
          "events": 5
        },
        "event_format": "json",
        "envelope": "flat",
        "destinations": [
          {
            "role": "to",
            "uri": "console",
            "kind": "console",
            "scheme": "console",
            "plugin": null,
            "event_format": "json"
          }
        ],
        "credentials": [],
        "checkpoint": {
          "path": null,
          "state": "fresh",
          "offset": 0
        },
        "pacing": {
          "mode": "unpaced",
          "rate": null,
          "arrivals": "constant",
          "bursts": 0,
          "ramps": 0,
          "curve": null,
          "max_rate": null,
          "speed": null,
          "day_seconds": null,
          "expected_seconds": null,
          "peak_events_per_minute": null
        },
        "drift_plan": null,
        "answer_key": null,
        "faults": {
          "out_of_order": 0.0,
          "anomaly_fraction": 0.0,
          "duplicate_fraction": 0.0,
          "poison_fraction": 0.0
        }
      }
    }
    ```

<a id="local-example-6"></a>

### Example 7

<!-- example: 6 -->

```bash {.runnable-reference}
shape emit retail --scale small --sink file -o events.jsonl \
      --live-target retail --live-alerts alerts.jsonl --live-report live.json
shape emit retail --scale small --anomaly-fraction 0.05 --live-target retail --live-fail --sink file -o e.jsonl
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: error: events.jsonl.checkpoint belongs to a different stream (another schema, seed, scale or option set); use --fresh to start over
    shape emit: ALERT [error] score-low: table address: live score 65.93 < 70 after 21,750 events
    shape emit: ALERT [error] score-low: table customer: live score 62.89 < 70 after 21,750 events
    shape emit: ALERT [error] score-low: overall: live score 76.34 < 85 after 21,750 events
    shape emit: 21,750 events delivered, offset 21,750 of 21,750, complete, 52,526 events/s
    shape emit: live fidelity 76.34 over 9 tables, 3 alerts, FAILED: overall score 76.34 < 85; table address: score 65.93 < 70; table customer: score 62.89 < 70
    ```

This command exits nonzero. Read the diagnostic; this transcript shows a refusal or failed check, not a passing gate.
