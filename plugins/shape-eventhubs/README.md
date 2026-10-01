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

Tests: contract tests run on every PR against an in-memory hub
(`shape_eventhubs.testing.FakeHub`); `pytest -m emulator plugins/shape-eventhubs/tests` runs the
end-to-end tests against the Event Hubs emulator (`docker compose -f ci/emulators/docker-compose.yml
up -d --wait azurite eventhubs`; amd64 only), nightly in CI. Against live Azure resources, the
`live` marker is reserved for the owner's `EVENTHUBS_*` secrets (plan section 9, O-03).

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository. The Event Hubs emitter
arrives with the streaming-during-generation work.
