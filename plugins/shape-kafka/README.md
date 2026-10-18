# sqllocks-shape-kafka

Shape plugin: a Kafka stream source (`shape.stream_sources`, scheme `kafka://`).

```
pip install sqllocks-shape-kafka          # or: pip install 'sqllocks-shape[kafka]'
shape stream-profile kafka://broker:9092/orders -o orders.json
```

On Alpine/musllinux, `confluent-kafka` has no ARM64 or x86_64 wheels and builds from
source. Installing this plugin, `sqllocks-shape[kafka]` or `sqllocks-shape[all]` needs
librdkafka **2.16 or newer**, development headers and C build tools. Alpine's
`librdkafka-dev` is currently older. Build the library first using the
[Alpine installation steps](../../docs/INSTALL.md#alpine-and-musllinux), which match
`scripts/release_smoke_alpine.sh`, or
[Confluent's build instructions](https://github.com/confluentinc/librdkafka#building).

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
maps are supported by the binary formats, as described below; JSON Schema still refuses them. Protobuf field numbers follow the
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

## Local binary schemas and nested events

Avro and Protobuf work on both Kafka and Event Hubs. Set `event_format=avro`
and `schema_file=events.avsc`, or `event_format=protobuf`, `schema_file=events.proto`
and `message=package.Message`. These local-schema modes send **plain binary bytes,
with no registry wire-format prefix**. Protobuf uses the built-in proto3 parser;
no `protoc` executable is needed. A schema mismatch names the field and stops
before any event from that batch is sent. Local schemas describe the data fields;
Shape's `_shape_*` fields are included only when declared in the schema.
Local schema matching allows same-sign integer widening and unsigned-to-signed
widening only when the destination is wider. Narrowing, signed-to-unsigned
conversion, and uint64-to-Avro-long are refused at preflight. Registry-derived
Avro retains its per-value uint64 overflow check. UUID extension fields map to
wire strings; Avro logical UUIDs read as strings, including in nested fields.

For CLI writes, use `--event-format avro` with
`--sink-config kafka.schema_file=events.avsc`, or the same key under `eventhubs`.
Protobuf also needs `--sink-config kafka.message=package.Message` (or
`eventhubs.message`). Binary writes require the `flat` envelope. Install the plugin's
`[avro]` or `[protobuf]` extra for the selected format.

The source API accepts `format="avro", schema_file="events.avsc"` or
`format="protobuf", schema_file="events.proto", message="package.Message"` on
`KafkaStreamSource.read(...)` and `EventHubsStreamSource.read(...)`.
Kafka Avro reads can instead use `schema_registry_url="https://registry"`, with
`schema_registry_username` and a separately supplied `schema_registry_password`
for basic authentication. Never put credentials in a URI. Each message's writer
schema id is resolved and cached for the read. A reader `schema=pyarrow.schema(...)`
pins the returned batch schema. Malformed messages increment `undecodable`;
rows that do not fit the reader schema increment `rejected`. Their offsets advance.
`on_error="raise"` stops at the first such message. Without a reader schema, binary batches follow the writer schemas; compatible
numeric changes are promoted. Distinct writer ids form separate batches so
incompatible writer types retain their values. JSON keeps its existing first-batch schema.
For Protobuf integer event timestamps encoded in microseconds, pass
`event_time_unit="us"`; Avro logical timestamps are read directly.

| Arrow | Avro | Protobuf |
|---|---|---|
| `struct` | record | message |
| `list` | array | `repeated` field |
| `map` | map with string keys | `map` field |

Nested records, lists and maps retain these Arrow types on read and write.
Protobuf event schemas declare their types in one file; recursive messages,
`oneof`, `Any` and extensions are unsupported. Avro unions allow `null` plus
one value type. Protobuf repeated/map containers and scalars without presence
cannot represent null; such values are refused rather than changed to empty
collections or scalar defaults. JSON Schema on Event Hubs is unsupported.

For Azure Schema Registry, install `sqllocks-shape-eventhubs[schema-registry,entra]`.
Writes use `event_format=avro`, `schema_file`,
`schema_registry_url=<namespace.servicebus.windows.net>` and
`schema_registry_group=<group>`. The CLI keys have the `eventhubs.` prefix.
Reads use `format="avro", schema_registry_url="namespace.servicebus.windows.net"`.
The SDK uses Microsoft Entra credentials (or an explicit API `credential`) and
caches schemas by the `avro/binary+<schema-id>` content type; Azure messages use
that content type with the Avro binary body, rather than the Kafka wire prefix.
The account needs permission on the registry group. Azure live validation requires
Owner-provided account access and is pending; offline tests use an encoder fake.
