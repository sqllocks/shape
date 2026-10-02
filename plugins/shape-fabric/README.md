# sqllocks-shape-fabric

Shape plugin: Microsoft Fabric. Today it holds the two event emitters of `shape emit`
(`shape.emitters`); Fabric, Synapse and Azure Data Factory pipeline integration is added by its own
work packages.

```
pip install sqllocks-shape-fabric          # brings sqllocks-shape-eventhubs
```

## `eventstream://`: a Fabric Eventstream

Add a **custom endpoint** source to an Eventstream, copy its connection string (it ends in
`EntityPath=...`) and:

```
export SHAPE_EVENTSTREAM_CONNECTION_STRING='Endpoint=sb://...;EntityPath=es_...'
shape emit retail --realtime --rate 2000 --sink eventstream://my-eventstream
```

The custom endpoint speaks the Event Hubs protocol, so this is the Event Hubs emitter (idempotency
key `<table>/<seq>` in the property `shape_key`, at-least-once, throttling waited for) with the
Eventstream's connection string. Sign-in with Microsoft Entra is not available for a custom
endpoint.

## `eventhouse://`: a Fabric Eventhouse (KQL database)

```
export SHAPE_EVENTHOUSE_TOKEN=$(az account get-access-token --resource https://<query-uri-host> --query accessToken -o tsv)
shape emit retail --sink eventhouse://<query-uri-host>/<database>
```

Events are sent by Kusto **streaming ingestion**, a JSON-lines request per batch (under the 4 MB
limit). Each Shape table goes to the KQL table of the same name, created from its schema
(`.create-merge table`, plus a JSON ingestion mapping); put a table name last in the URI to send
everything to one table. The idempotency key is the pair of columns `_shape_table`,
`_shape_seq`. Streaming ingestion does not deduplicate, so a batch the runtime repeats after a
failure is in the table twice: read it with

```python
from shape_fabric.eventhouse import dedupe_query

dedupe_query("order_line")  # ['order_line'] | summarize take_any(*) by _shape_table, _shape_seq
```

Sign-in: `token` option, `SHAPE_EVENTHOUSE_TOKEN`, or Microsoft Entra through `azure-identity`
(`pip install 'sqllocks-shape-fabric[entra]'`). A 429/503 is waited for (`Retry-After` honoured);
400 (a malformed event, or streaming ingestion not enabled) and 401/403 stop the run.

## Tests

Contract tests run on every PR against in-memory fakes (`shape_fabric.testing`; the emitter
contract is `shape.streaming.emit.contract`). `pytest -m emulator plugins/shape-fabric/tests` runs
the end-to-end tests against the Event Hubs emulator (as an Eventstream stand-in) and the Kusto
emulator (`docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs && docker
compose -f ci/emulators/docker-compose.yml up -d kusto`), nightly in CI. `pytest -m live
plugins/shape-fabric/tests` needs the owner's `FABRIC_*` secrets (`FABRIC_EVENTSTREAM_CONNECTION_STRING`;
`FABRIC_EVENTHOUSE_URI`, `FABRIC_EVENTHOUSE_TOKEN`); the nightly job runs each only where its secret
exists.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.
