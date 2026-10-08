# sqllocks-shape-kafka

Shape plugin: a Kafka stream source (`shape.stream_sources`, scheme `kafka://`).

```
pip install sqllocks-shape-kafka          # or: pip install 'sqllocks-shape[kafka]'
shape stream-profile kafka://broker:9092/orders -o orders.json
```

`kafka://host1:9092,host2:9092/TOPIC` reads one topic as Arrow micro-batches. Message bodies are
JSON objects; their fields become columns, and `_shape_event_time` carries the event time (the
body's `_shape_event_time`, or the message timestamp). Shape keeps its own position: the consumer
is *assigned* every partition at explicit offsets, nothing is committed to the broker, and a
`StreamOffset` is `{partition: next offset}`, so a checkpoint resumes exactly where it stopped.

Options, offsets, delivery guarantees and the checkpoint format are described in
`docs/plugins/streaming.md`. Extra `confluent-kafka` settings (SASL, TLS) go in through the
`config` source option, preferably from a file (`--options-file`).

## Emitting events: `kafka://`

```
shape emit retail --realtime --rate 5000 --sink kafka://broker:9092/retail-events
```

`shape.emitters` `kafka`: one message per event, body = the event JSON (flat, or `--envelope
cloudevents`), **message key = the idempotency key `<table>/<seq>`**, header `shape-table`. The
producer uses `acks=all` and `enable.idempotence=true` (overridable through the `config` option, for
SASL/TLS and the like). `emit` returns after the broker acknowledged every message, so the
runtime's checkpoint never passes an undelivered event; a failed delivery is retried by the runtime
(a message may arrive twice: keep the first of each key). A full local queue is waited for.
See `docs/EMIT.md`.

### Registry formats: Avro, Protobuf, JSON Schema

```
pip install 'sqllocks-shape-kafka[avro]'      # fastavro; or [protobuf]; json-schema needs nothing
shape emit retail --sink kafka://broker:9092/orders --event-format avro \
    --sink-config kafka.schema_registry_url=https://registry:8081
```

`--event-format json|avro|protobuf|json-schema` (default `json`, today's bytes). The emitter
derives the schema from the table's Arrow schema, registers it with a Confluent-compatible registry
(`kafka.schema_registry_url`; `kafka.schema_registry_username` and
`kafka.schema_registry_password=env://NAME`, a credential reference; `kafka.subject_strategy` =
`topic` (default, `<topic>-value`), `record` or `topic_record`) and writes the Confluent wire format:
byte 0, the 4-byte big-endian schema id, the payload (Protobuf: a zero byte for the message index
`[0]` first). The record (message) name is the table name in the namespace/package `shape.events`;
the table and its columns must be valid identifiers for Avro and Protobuf. The key stays
`<table>/<seq>`. The encoders are imported on first use; a missing one stops the run (exit 2) with the
`pip install` command above.

Type mapping (a nullable Arrow field is a nullable field: an Avro union with `null`, a Protobuf
`optional`, a JSON `null`; `_shape_table` and `_shape_seq` are never null; a float is always nullable
because NaN and infinities are sent as `null`, as in the flat JSON event):

| Arrow | Avro | Protobuf | JSON Schema |
|---|---|---|---|
| bool | `boolean` | `bool` | `boolean` |
| int8, int16, int32 | `int` | `int32` | `integer` |
| uint8, uint16 | `int` | `uint32` | `integer` |
| int64, uint32 | `long` | `int64`, `uint32` | `integer` |
| uint64 | `long` (a value above 2^63-1 cannot be encoded) | `uint64` | `integer` |
| float32, float64 | `float`, `double` | `float`, `double` | `number` |
| string | `string` | `string` | `string` |
| uuid (the Arrow extension type) | `string`, logical type `uuid` | `string` | `string`, format `uuid` |
| binary | `bytes` | `bytes` | `string`, `contentEncoding: base64` |
| decimal(p, s) | `bytes`, logical type `decimal(p, s)` | `string` (canonical text) | `string` (canonical text) |
| date | `int`, logical type `date` | `int32` (days since 1970-01-01) | `string`, format `date` |
| time | `time-millis` (s, ms) or `time-micros` | `int64` (microseconds) | `string` |
| timestamp, no time zone | `local-timestamp-millis` (s, ms) or `-micros` (us, ns) | `int64` (wall-clock microseconds since the epoch) | `string`, "timestamp without time zone" |
| timestamp with time zone | `timestamp-millis` (s, ms) or `-micros` (us, ns) | `int64` (UTC microseconds since the epoch) | `string`, format `date-time` |

A nanosecond value is cut to microseconds. A dictionary column is its value type. Lists, structs and
maps are not mapped (the table is refused with exit 2; use `json`). Protobuf field numbers follow the
column order. Tests decode every message back from the registry's copy of the schema and compare it
with the flat event (`shape_kafka.testing.FakeRegistry`, `decode_messages`).

Tests: contract tests run on every PR against in-memory fakes
(`shape_kafka.testing.FakeBroker`, `FakeProducer`; the emitter contract is
`shape.streaming.emit.contract`); `pytest -m emulator plugins/shape-kafka/tests` runs the
end-to-end tests against a real broker (`docker compose -f ci/emulators/docker-compose.yml up -d
--wait kafka`), nightly in CI.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.

Live tests (`pytest -m live plugins/shape-kafka/tests`) need `KAFKA_SERVERS`, `KAFKA_TOPIC` and,
optionally, `KAFKA_CONFIG` (JSON); the nightly job runs them only where those secrets exist.

### Message metadata (W9-08)

Both broker emitters accept `key="column"` or `key="a|b"` (values joined with
`|`; null components become empty strings), and `headers=["name=value",
"name=@column"]`. A mapping of header names to static values or `@column`
references is also accepted. Unknown columns fail before sending the batch.
The default key remains `<table>/<seq>`. A column key moves that replay key to
`shape-key`; Event Hubs retains its established `shape_key` property name for
the selected key. User headers cannot replace reserved `shape-*` or `shape_*`
metadata. Synthetic and dead-letter headers retain their established names.

Kafka `partition=0` selects one partition; `partition="by_key"` uses the broker's
key partitioner. Event Hubs `partition_key` accepts `table` (default), `none`,
or a column. `timestamp="broker"` leaves timestamp assignment to the transport.
`timestamp="event_time"` takes `_shape_event_time`: Kafka receives producer
milliseconds, and Event Hubs receives AMQP creation time in milliseconds. Event
Hubs enqueue time is always assigned by the service. Null event times leave the
transport timestamp unset; submillisecond precision is truncated.

Sources accept `with_key`, `with_headers`, `with_timestamp`. They add binary
`_shape_key`, map<string,binary> `_shape_headers`, and UTC-microsecond
`_shape_timestamp`. Event Hubs also includes `_shape_properties` and
`_shape_partition_key` when key/header metadata is requested. Its message
timestamp uses creation time when present, otherwise service enqueue time.
`nested=False` writes header/property maps as JSON text with hex-encoded binary
values (nulls remain null). Missing metadata stays null or an empty map.
Message metadata columns are excluded from drift by default; explicitly select
one with `only_columns` to compare it.

CLI: `shape emit ... --key customer_id --header trace=@trace_id --partition 0
--timestamp event_time` (Kafka); use `--partition-key customer_id` for Event
Hubs. `shape stream-profile URI --with-key --with-headers --with-timestamp`
reads metadata. The existing `--option nested=false` selects JSON-text maps.
