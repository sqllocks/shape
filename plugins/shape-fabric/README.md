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

## Writers (Python API)

Batch writers take Arrow `RecordBatch` iterators, one table per `write_table` call (or a mapping to
`write_tables`), and a `credential` (any object with `get_token(scope)`, or a function
`scope -> token`; `None` means the writer's default). A failure raises `WriteError`; its `.result`
lists the tables completed before it. Nothing is reported written unless the destination accepted it.

| writer | destination | notes |
|---|---|---|
| `LakehouseWriter(folder)` | Parquet/CSV/JSONL files in a local folder or OneLake (`abfss://`, `onelake://<ws>/<lakehouse>/Files/..`) | streamed; a file is complete or absent; landing-zone, manifest and done-flag helpers |
| `SqlDatabaseWriter(connection_string)` | Fabric SQL database, Azure SQL, SQL Server | parameterised `INSERT`s, `batch_size` rows per trip, one transaction per table |
| `WarehouseWriter(connection_string, staging_path)` | Fabric Warehouse | Parquet staged in OneLake, `COPY INTO`, rows loaded must equal rows staged, staging always removed |
| `EventhouseWriter(uri)` | KQL table | the emitter's Kusto transport (retries, sign-in), a table's own columns |
| `EventstreamWriter(uri)` | Eventstream | the emitter's transport, flat events with the `_shape_table`/`_shape_seq` key |

`write_mode` (SQL, Warehouse, Eventhouse): `create` (default: an existing table is an error), `append`,
`truncate`, `replace` (drops the old table). Names are quoted and checked, values are parameters; the
one literal that cannot be a parameter (the `COPY INTO` location) is validated against a strict
character set. `shape_fabric.sinks` has `Sink`-protocol adapters; `shape_fabric.onelake` builds
OneLake paths.

Profile a lakehouse straight from OneLake: `shape profile onelake://<workspace>/<lakehouse>/Tables/<table>`
(Delta) or `.../Files/<path>`; authentication and `adlfs` handling are core's `abfss://` source.

## Tests

Contract tests replay recorded interactions (`tests/fixtures/*.json`, made by `python -m
shape_fabric.scenarios record <dir>`; secrets are scrubbed, and a test fails if a tape holds one).
Emulator tests: `test_sql_emulator.py` (SQL Server), `test_kusto_emulator.py`. Live tests
(`test_live_writers.py`, nightly `fabric-live` job) need the `FABRIC_*` secrets listed in the file.

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
