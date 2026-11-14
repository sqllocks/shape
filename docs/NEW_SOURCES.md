# New database and stream sources

Status: experimental.

These sources complement [file sources](SOURCES.md). Database table URIs work with
`shape profile`; omit `table` for catalog profiling where supported. Streaming URIs use
`shape stream-profile`. Source options and writer options are different: source URIs refuse
writer-only nested options before connecting. Install the matching optional driver extra;
core does not import database or broker clients until they are needed.

## Database reads

| Source | Driver extra | Read behavior |
|---|---|---|
| PostgreSQL | `sqllocks-shape-databases[postgres]` | Bounded cursor batches; native scalar array element types; declared catalog keys and row-count provenance |
| MySQL | `sqllocks-shape-databases[mysql]` | Server-side cursor batches; declared types and keys; bounded catalog sampling |
| Oracle 19c+ | `sqllocks-shape-databases[oracle]` | Thin mode; exact decimal/temporal reads; bounded cursor fallback for LOB/RAW/INTERVAL columns |
| Snowflake | `sqllocks-shape-databases[snowflake]` | Arrow reads; bounded samples; declared catalog keys and explicit population counts; warehouse provenance |
| Databricks SQL warehouse | `sqllocks-shape-databases[databricks]` | Arrow reads; recursive declared ARRAY/STRUCT/MAP types; bounded samples and declared catalog keys |

**Snowflake and Databricks live read validation remains pending Owner accounts**, as recorded
in the [changelog](../CHANGELOG.md). Existing write receipts validate writes only.
Offline and emulator coverage is not a substitute for live read validation.

PostgreSQL/MySQL table reads require `table=T`. Schema capture uses `--sample-rows`
(default 1000) for bounded distributions, `0` for catalogs only, and `--tables` to select
which tables to profile. Catalog population counts remain separate from sample distributions.
Credentials follow the sinks' TLS defaults and password/credential/environment precedence;
use `SHAPE_POSTGRES_PASSWORD`/`PGPASSWORD` or `SHAPE_MYSQL_PASSWORD`/`MYSQL_PWD`.
Never put passwords in URIs. See [database adapter details](../plugins/shape-databases/README.md#postgresql-and-mysql-sources).

**Needs a PostgreSQL account. Not run in CI.** Configure the password in the environment.

```bash
shape profile 'postgresql://reader@host/db?schema=public&table=orders' -o orders.shape
shape profile 'postgresql://reader@host/db?schema=public' --tables orders --sample-rows 1000 -o catalog.shape
```

**Needs a MySQL account. Not run in CI.** Configure the password in the environment.

```bash
shape profile 'mysql://reader@host/db?table=orders' -o orders.shape
```

**Needs an Oracle account. Not run in CI.** Configure `ORACLE_PASSWORD` separately.

```bash
shape profile 'oracle://reader@host:1521/service?schema=SALES&table=Orders' -o orders.shape
shape profile 'oracle://reader@host:1521/service?schema=SALES' --tables Orders --sample-rows 1000 -o catalog.shape
```

Oracle identifiers preserve case and must fit 128 UTF-8 bytes. Unconstrained NUMBER maps to
`decimal128(38,10)` with a warning; values that cannot fit fail instead of rounding. DATE
becomes a zone-less microsecond timestamp; zoned timestamps normalize to UTC. Catalog
population counts use statistics; missing statistics fail unless `exact_counts=true` is
explicitly selected. The [Oracle adapter guide](../plugins/shape-databases/README.md#oracle)
also describes array-DML writes and empty-string refusal: nullable conversion must be explicit.

**Needs a Snowflake account. Not run in CI. Live read validation pending.**
Use the password or key-pair environment/reference settings from the adapter guide.

```bash
shape profile 'snowflake://READER@ACCOUNT/DATABASE/SCHEMA?table=ORDERS&warehouse=WH' -o orders.shape
```

**Needs a Databricks account. Not run in CI. Live read validation pending.**
Configure the token or OAuth credentials outside the URI.

```bash
shape profile 'databricks://host/sql/1.0/warehouses/ID?catalog=main&schema=default&table=orders' -o orders.shape
```

See [Snowflake](databases/snowflake.md) and [Databricks](databases/databricks.md) for
catalog counting, sampling and authentication limits. The experimental
[BigQuery source](../plugins/shape-databases/README.md#bigquery-bigquery) also ships Parquet
load and Storage Read paths, reference-only credentials, bounded catalog sampling and a
dry-run query cost guard; real account validation is still required.

## Fabric Eventhouse

The Fabric plugin adds `eventhouse://HOST/DATABASE?table=T` through the KQL query endpoint.
Omit `table` to profile its database tables. Schema discovery preserves declared KQL types;
`dynamic` is JSON text, decimal uses `decimal256(57,28)`, datetime is UTC microseconds and
timespan is nanosecond duration. Reads page through results and refuse truncated/partial
query responses rather than silently accepting incomplete data. Authentication uses an
explicit token/credential, `SHAPE_EVENTHOUSE_TOKEN`, or Microsoft Entra credentials.

**Needs a Fabric Eventhouse account. Not run in CI.** Configure sign-in separately.

```bash
shape profile 'eventhouse://cluster.kusto.fabric.microsoft.com/Database?table=Orders' -o orders.shape
```

See [Fabric platform inventory](FABRIC_PLATFORM.md) for supported platform surfaces.

## Kafka and Event Hubs: Avro and Protobuf

Both plugins read and write plain Avro/Protobuf with local schemas and preserve nested Arrow
fields. Binary reads use `format`, `schema_file` and Protobuf `message` options. For Protobuf,
`schema_file` is UTF-8 `.proto` source; the local parser builds descriptors without a compiler
or network access. `message` selects the message. For Avro it is the JSON writer schema.

**Needs a Kafka broker with matching Avro messages. Not run in CI.** Install the Kafka
plugin's `avro` extra and provide your local writer schema.

```bash
shape stream-profile kafka://broker:9092/orders -o orders.json \
  --option format=avro --option schema_file=orders.avsc
```

**Needs an Event Hubs account with matching Protobuf messages. Not run in CI.** Install the
Event Hubs plugin's `protobuf` extra. Supply a connection string through
`SHAPE_EVENTHUBS_CONNECTION_STRING` or configure Microsoft Entra credentials.

```bash
shape stream-profile eventhubs://namespace.servicebus.windows.net/orders -o orders.json \
  --option format=protobuf --option schema_file=orders.proto --option message=demo.Order
```

Emitters use `event_format` with `schema_file` and Protobuf `message`. Kafka Avro can resolve
registry-framed writer schema IDs with a per-read cache using `schema_registry_url`.
Event Hubs supports Azure Schema Registry Avro through its optional `schema-registry` extra.
Malformed payloads increment `undecodable`; incompatible rows increment `rejected`. The default
skip behavior counts failures; use source `on_error=raise` when decoding must stop immediately.
These counters describe data actually read, not a promise that every broker event was valid.
[Streaming](plugins/streaming.md) covers checkpoints, reader schemas and bounded/follow modes;
[emitting events](EMIT.md) covers writer configuration.

## Try the shared binary codecs locally

The broker plugins use the same local-schema codec. This no-account exercise writes an
Avro schema and a self-contained `.proto` file, then encodes and decodes a synthetic row
with a nested list. Use the [locked example environment](contributing/EXAMPLES.md), with
`fastavro` and `protobuf` installed, in an empty directory. This validates the payload
format locally; it does not test a broker connection. Protobuf imports and `oneof` fields
are refused by this codec; declare message types together in one file.

<!-- example: 0 -->

```bash {.runnable-reference}
python - <<'PYDATA'
import json
from pathlib import Path
from shape.streaming.binary import BinaryCodec

avro = {"type": "record", "name": "Order", "fields": [
    {"name": "id", "type": "long"},
    {"name": "items", "type": {"type": "array", "items": "string"}}
]}
Path("orders.avsc").write_text(json.dumps(avro))
Path("orders.proto").write_text('syntax = "proto3"; package demo; message Order { int64 id = 1; repeated string items = 2; }')
row = {"id": 1, "items": ["synthetic-a", "synthetic-b"]}
for fmt, path, message in [("avro", "orders.avsc", None), ("protobuf", "orders.proto", "demo.Order")]:
    codec = BinaryCodec.from_file(fmt, path, message=message)
    decoded = codec.decode(codec.encode(row))
    assert decoded == row
    print(fmt + ": " + json.dumps(decoded, sort_keys=True))
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    avro: {"id": 1, "items": ["synthetic-a", "synthetic-b"]}
    protobuf: {"id": 1, "items": ["synthetic-a", "synthetic-b"]}
    ```

## Related

[New feature workflows](NEW_FEATURES.md) · [Database adapters](databases/index.md) ·
[SQL and Delta fidelity](TYPE_FIDELITY.md)
