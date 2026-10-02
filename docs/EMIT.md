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

## Limits

* The first block of a table with post-passes waits for the whole schema to generate.
* Event order across tables is fixed (table by table); there is no interleaving.
* Rate accuracy depends on the sink keeping up; `max_lag` in the report says whether it did.
