# sqllocks-shape-eventhubs

Shape plugin: an Azure Event Hubs stream source (`shape.stream_sources`, scheme `eventhubs://`).

```
pip install sqllocks-shape-eventhubs      # or: pip install 'sqllocks-shape[eventhubs]'
export SHAPE_EVENTHUBS_CONNECTION_STRING='Endpoint=sb://...'
shape stream-profile eventhubs://contoso.servicebus.windows.net/telemetry -o telemetry.json
```

`eventhubs://NAMESPACE/HUB[?consumer_group=NAME]` reads one event hub as Arrow micro-batches.
Connect with a connection string (the `connection_string` option or
`SHAPE_EVENTHUBS_CONNECTION_STRING`; the hub name comes from the URI) or, with the `entra` extra
(`pip install 'sqllocks-shape-eventhubs[entra]'`), with Microsoft Entra sign-in. Event bodies are
JSON objects; their fields become columns, and `_shape_event_time` carries the event time (the
body's `_shape_event_time`, or the enqueued time). Shape keeps its own position: each partition
is read from an explicit sequence number, nothing is checkpointed in Azure Blob Storage, and a
`StreamOffset` is `{partition id: next sequence number}`.

Options, offsets, delivery guarantees and the checkpoint format are described in
`docs/plugins/streaming.md`.

## Emitting events: `eventhubs://`

```
export SHAPE_EVENTHUBS_CONNECTION_STRING='Endpoint=sb://...'
shape emit retail --realtime --rate 2000 --sink eventhubs://my-namespace/my-hub
```

`shape.emitters` `eventhubs`: one message per event (body = the event JSON; content type
`application/json`, or `application/cloudevents+json` with `--envelope cloudevents`), the
idempotency key `<table>/<seq>` as the property `shape_key` (plus `shape_table`, `shape_seq`).
Events are packed into service batches by size, one table per batch, with the table as the
partition key (`partition_key="none"` to let the service spread them). `emit` returns after the
service accepted every batch; a throttled service (`server-busy`) is waited for, any other send
failure is retried by the runtime. Event Hubs does not deduplicate: keep the first message of each
`shape_key`. See `docs/EMIT.md`.

Tests: contract tests run on every PR against an in-memory hub
(`shape_eventhubs.testing.FakeHub`, `FakeProducerHub`; the emitter contract is
`shape.streaming.emit.contract`); `pytest -m emulator plugins/shape-eventhubs/tests` runs the
end-to-end tests against the Event Hubs emulator (`docker compose -f ci/emulators/docker-compose.yml
up -d --wait azurite eventhubs`; amd64 only), nightly in CI. Against live Azure resources, the
`live` marker runs `plugins/shape-eventhubs/tests/test_live.py` with the owner's `EVENTHUBS_*`
secrets (`EVENTHUBS_CONNECTION_STRING`, `EVENTHUBS_HUB`, optionally `EVENTHUBS_GROUP`; plan section
9, O-03); the nightly job runs it only where they exist.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.

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
