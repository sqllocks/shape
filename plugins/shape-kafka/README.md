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

Tests: contract tests run on every PR against an in-memory broker
(`shape_kafka.testing.FakeBroker`); `pytest -m emulator plugins/shape-kafka/tests` runs the
end-to-end tests against a real broker (`docker compose -f ci/emulators/docker-compose.yml up -d
--wait kafka`), nightly in CI.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository. The Kafka emitter
arrives with the streaming-during-generation work.
