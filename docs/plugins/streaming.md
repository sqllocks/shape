# Stream sources and `shape stream-profile` (`shape-kafka`, `shape-eventhubs`, files)

Shape profiles streams it consumes. A **stream source** is a `shape.stream_sources` plugin that
turns a Kafka topic or an Event Hubs hub into Arrow micro-batches with an offset after each one;
`shape stream-profile` feeds those batches to the stream runtime
(`docs/specs/STREAMING_SEMANTICS.md`), which profiles them in bounded mode (the sketches of
plan T-14: memory does not grow with the number of events), with windows, checkpoints, reconnects
and deduplication on offset.

## Install

```bash
pip install 'sqllocks-shape[kafka]'        # sqllocks-shape-kafka and confluent-kafka
pip install 'sqllocks-shape[eventhubs]'    # sqllocks-shape-eventhubs and azure-eventhub
pip install 'sqllocks-shape-eventhubs[entra]'   # Microsoft Entra sign-in (azure-identity)
```

Neither client library is imported until a read starts, so `shape plugins doctor` is clean
without a broker. `shape plugins list` shows `shape.stream_sources:kafka` and
`shape.stream_sources:eventhubs`.

## Profile a stream

```bash
# everything the topic holds now, as one profile
shape stream-profile kafka://broker:9092/orders -o orders.json

# 5-minute tumbling windows, written as they close, resumable
shape stream-profile eventhubs://contoso.servicebus.windows.net/telemetry \
    --window tumbling --size 5m --allowed-lateness 30s \
    --windows telemetry.jsonl --checkpoint telemetry.ckpt --follow
```

Exit codes: `0` done, `2` the input or the connection is wrong (nothing partial is hidden: the
message says what).

| Option | Meaning |
|---|---|
| `URI` | `kafka://host1:9092,host2:9092/TOPIC`, `eventhubs://NAMESPACE/HUB[?consumer_group=NAME]`, or, with no broker, a file, folder, glob or `-` (see [Files and standard input](#files-and-standard-input)). |
| `-o OUT.json` | The `global` window: one profile of everything read, the profile engine's document with `mode: "bounded"`. |
| `--window` | `global` (default), `tumbling`, `sliding` or `session`. Windowed runs write `--windows`. |
| `--windows OUT.jsonl` | One closed window per line (`kind`, `start`, `end`, `rows`, `profile`), written as it closes. A window is identified by `(kind, start, end)`; a restarted run reads the file and does not write a window it already has. |
| `--size`, `--slide`, `--gap` | Durations: `500ms`, `30s`, `5m`, `1h`, `1d`, or a number of seconds. One rule everywhere (one parser, `shape.streaming.runtime.parse_duration`): a string with a unit, where a bare numeric *string* means seconds. In the Python API a duration is a `timedelta` or such a string; a bare integer or float is refused (`docs/specs/STREAMING_SEMANTICS.md` section 2). |
| `--allowed-lateness` | How far behind the watermark a row may arrive and still count (default `0s`; a duration as above). The watermark is kept per partition, so partitions read at different speeds lose nothing by default; rows that are still late are counted, and a line on stderr gives the count and the `--allowed-lateness` that would have kept them. |
| `--max-partition-skew` | How far (event time) a partition may trail the newest event before windows stop waiting for it (a duration, default `10m`). Bounds the number of open windows. A resumed run uses the value given to it, not the one in the checkpoint. |
| `--partition-idle-timeout` | With `--follow`: seconds without a delivery before a partition stops holding windows open (default `30`; `0` is off). A plain number of seconds, not a duration string. |
| `--event-time FIELD` | The payload field holding the event time (default `_shape_event_time`); `--event-time-unit s|ms|us` for numbers. An event without one uses the broker's timestamp. |
| `--start earliest\|latest` | Where to begin when there is no checkpoint (brokers only; a file is read from its start). |
| `--order file\|event-time` | Files only: replay in file order (default), or sorted by event time. |
| `--follow` | Keep reading as events arrive. Without it the run stops at the end the stream had when it began. `--max-events N` and `--idle-timeout SECONDS` stop a followed read; Ctrl-C finishes the profile from what was read and keeps the checkpoint resumable. The windows that Ctrl-C closed early are written with `"partial": true`; a restart removes them and writes them complete. |
| `--checkpoint FILE`, `--checkpoint-every N` | Commit the offsets and profile state every N batches and at the end, and resume from the file when it exists. |
| `--batch-size N` | Events per micro-batch (default 65,536). The first batch fixes the schema. |
| `--option KEY=VALUE`, `--options-file FILE.json` | Source options (JSON values allowed). Put secrets in the file, not on the command line. |

The schema is that of the first batch (or of the checkpoint). An event whose value cannot take
its column's type is *rejected*, not coerced; a message that is not a JSON object is
*undecodable*. Both are counted in the summary the command prints, and neither stops the run.

## Files and standard input

No broker is needed to try, test or replay a stream. The same command, with the same windows,
lateness, event time, `--max-events` and checkpoints, reads:

```bash
shape stream retail -t order --max-events 5000 --sink file -o orders.jsonl   # a stream to replay
shape stream-profile orders.jsonl --window tumbling --size 30d --windows orders.windows.jsonl
shape stream-profile file:///data/landed/2026-06-02/ --window tumbling --size 5m --windows day.jsonl
shape stream-profile 'landed/*.parquet' --event-time ts --window sliding --size 1h --slide 15m \
    --windows history.jsonl
cat events.jsonl | shape stream-profile - -o events.json
```

* **Sources:** a path or `file://` URI, a folder (its `.jsonl`, `.ndjson`, `.json`, `.csv`,
  `.parquet` files in name order; names starting with `.` or `_` are skipped), a glob (matches in
  name order), or `-` for standard input. All files must be of one format.
* **Formats:** JSON lines (what `shape emit` and `shape stream` write, flat or as CloudEvents,
  whose `data` is read), CSV and Parquet (one event per row). Standard input is JSON lines unless
  `--option format=csv|parquet` says otherwise; `--option format=...` also names a file whose
  suffix does not.
* **Decoding** is the broker sources': a column of a CSV or Parquet file is read as a JSON event's
  would be (nested values as text, a timestamp as ISO text), so a timestamp column is a timestamp
  only as the event time (`--event-time ts`, the field name in the file). There is no broker
  timestamp: a row without a valid event time has none and is counted (`null_event_time`).
* **Order:** by default file by file, line by line, at full speed, so rows that are out of order
  in the file are out of order in the stream and count as late once their window has closed.
  `--order event-time` sorts every row by event time first (rows without one last, ties in file
  order), so a file written out of order replays in time order and nothing is late; it holds the
  rows in memory.
* **Checkpoints:** the offset is the number of rows consumed. A resumed run reads the same files
  in the same order (or the same piped input) and skips what its checkpoint covers.
* **Not for files:** `--follow` (a file is read to its end) and `--start latest` are refused.

## Messages

A message body is one JSON object. Its top-level fields become columns (a nested object or array
is kept as its JSON text), and the batch carries `_shape_event_time` (timestamp, microseconds,
UTC): the body's `_shape_event_time` (ISO-8601, or a number in `--event-time-unit`) when it holds
a valid time, else the broker's timestamp, else null (the runtime counts such rows and skips
them). Rows from Shape's own emitters carry `_shape_table` and `_shape_seq`; they are profiled
like any other column.

## Offsets and delivery

Shape keeps its own position and commits nothing to the broker:

* **Kafka:** the consumer is assigned every partition at explicit offsets (no consumer group
  rebalancing); an offset is `{partition: next offset}`.
* **Event Hubs:** each partition is read from an explicit sequence number (nothing is
  checkpointed in Azure Blob Storage); an offset is `{partition id: next sequence number}`.

A **bounded** read (the default) takes the partitions one after the other, in order, so every run
produces the same batches and the same profile. **Following** reads all partitions together in
arrival order; partitions are not ordered against each other in event time, so the watermark is
kept per partition (`docs/specs/STREAMING_SEMANTICS.md` section 3) and nothing is lost to
interleaving; `--allowed-lateness` is for events that really arrive late. Delivery is at least once and the profile state is exact:
`docs/specs/STREAMING_SEMANTICS.md` section 6.

A checkpoint belongs to one URI and one profiler configuration; another is refused. A checkpoint
of a *finished* run is final: running the same command again reads nothing and says so.

The checkpoint and the state inside it (the window snapshot, the deduplicator's and the keyed
sketches' snapshots, the emit checkpoint, live alerts and reports) follow the policy of
`docs/specs/STATE_AND_COMPATIBILITY.md`: each declares `format` (the first release's string, such as
`shape-stream-checkpoint-v1`), an integer `version` (now 1), `shape_version` and `min_shape_version`.
A file without a version is version 1. A file of a newer version is refused with the release that
reads it; a checkpoint whose profile state cannot be read says so, names the file and suggests
removing it or using another `--checkpoint`.

## Source options

Passed as `--option KEY=VALUE` or in `--options-file`; the first five are also set by flags.

| Option | Meaning |
|---|---|
| `start_at` | `earliest` (default) or `latest`, for partitions without a stored position. |
| `stop_at_end` | `true` (default): stop at the end the stream had when the read began; messages written during the read are left for the next run. |
| `idle_timeout`, `max_messages` | Stop after this many seconds without a message, or after this many messages (counted for each read: a reconnect starts the count again). |
| `batch_size` | Messages per micro-batch, at most. A batch never mixes partitions. |
| `schema` | The column types to read into (an Arrow schema; library use). |
| `on_error` | `skip` (default) counts undecodable messages; `raise` fails on the first. |
| `event_time_field`, `event_time_unit`, `with_offsets` | See *Messages*; `with_offsets` adds `_shape_partition` and `_shape_offset` for row-level deduplication. |
| `config` (Kafka) | A JSON object of extra `confluent-kafka` settings, e.g. `{"security.protocol": "SASL_SSL", "sasl.mechanisms": "PLAIN", ...}`. Shape sets `bootstrap.servers` and keeps `enable.auto.commit` off. |
| `connection_string`, `credential` (Event Hubs) | A connection string (or set `SHAPE_EVENTHUBS_CONNECTION_STRING`); the hub name comes from the URI. Without one, `azure-identity`'s `DefaultAzureCredential` signs in. |

## Writing another stream source

Implement the `StreamSource` Protocol (`docs/plugins/api-v1.md`): `name`, `schemes` and
`read(uri, start=None, **options)`, yielding `(StreamOffset, RecordBatch)`. Offsets must be
JSON-serialisable and reading from the offset after batch *k* must give exactly the batches after
*k*; `shape.plugins.kit.check_stream_source` tests that. Use
`shape.streaming.messages.decode_messages` to turn message bodies into batches, and keep offsets
as `{partition: next offset}` so the consumer can deduplicate replays. A source for tests can
follow `shape_kafka.testing.FakeBroker`.

To read a source outside the consumer with reconnects, wrap its `read` in
`shape.connectors.qualification.reconnecting_batches(connect, max_attempts=5)`: after a failure it
reconnects from the offset after the last batch it yielded and gives up after `max_attempts`
failures in a row. It waits before each reconnect: `backoff` seconds (keyword-only, default 0.5),
doubled for each further failure in a row and capped at 30 s; a delivered batch resets it.
`backoff=0` reconnects at once, and `sleep=` replaces the wait function in tests.

## Tests

* Every PR: contract tests with an in-memory broker and hub, and the kit
  (`plugins/shape-kafka/tests`, `plugins/shape-eventhubs/tests`, `tests/streaming/test_cli.py`).
* Nightly: `pytest -m emulator plugins/shape-kafka/tests plugins/shape-eventhubs/tests` against
  the containers of `ci/emulators/docker-compose.yml` (plan T-26). Locally:
  `docker compose -f ci/emulators/docker-compose.yml up -d --wait kafka azurite eventhubs`.
