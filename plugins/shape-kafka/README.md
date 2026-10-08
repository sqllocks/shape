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

Tests: contract tests run on every PR against in-memory fakes
(`shape_kafka.testing.FakeBroker`, `FakeProducer`; the emitter contract is
`shape.streaming.emit.contract`); `pytest -m emulator plugins/shape-kafka/tests` runs the
end-to-end tests against a real broker (`docker compose -f ci/emulators/docker-compose.yml up -d
--wait kafka`), nightly in CI.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.

Live tests (`pytest -m live plugins/shape-kafka/tests`) need `KAFKA_SERVERS`, `KAFKA_TOPIC` and,
optionally, `KAFKA_CONFIG` (JSON); the nightly job runs them only where those secrets exist.
