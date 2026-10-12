# Cloud sources: OneLake, ADLS Gen2 and Delta

Status: experimental.

Two built-in sources (`shape.sources`, see [builtins.md](builtins.md)) read cloud storage. Install
their packages with `pip install 'sqllocks-shape[azure]'` (`adlfs`, `azure-identity`,
`deltalake`). Nothing from those packages is imported until a read needs it.

| URI | Source | Reads |
|---|---|---|
| `abfss://<container>@<account>.dfs.core.windows.net/<path>` | `abfss` | ADLS Gen2 |
| `abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse/Files/<path>` | `abfss` | OneLake files |
| `abfss://<container>/<path>` with `account_name` or a connection string | `abfss` | the short form (Azurite uses it) |
| `delta+abfss://<container>@<host>/<path>` | `delta` | a Delta table in the same places |
| a local directory that holds `_delta_log` | `delta` | a local Delta table |

`<path>` is one file, a glob, or a directory of files of one kind (CSV, Parquet, JSONL or Arrow
IPC; `.gz`, `.bz2`, `.zst`, `.lz4` accepted). Directories are read in name order and skip names
starting with `.` or `_`. The first file's schema wins; later files are cast to it, and a file
that lacks one of its columns is an error.

`shape profile <uri>` and `shape.profile(<uri>)` use these sources through the plugin registry:
any installed `shape.sources` plugin that says `can_open(uri)` can serve a URL.

## Authentication

First match wins:

1. **Explicit:** the `credential` option (any object with `get_token`), `token` (a bearer token
   string), or `account_key`, `sas_token`, `connection_string`.
2. **Storage environment:** `AZURE_STORAGE_ACCOUNT_KEY`, then `AZURE_STORAGE_SAS_TOKEN`, then
   `AZURE_STORAGE_CONNECTION_STRING`. The first non-empty variable wins, as for sinks.
   An explicit credential, token or storage key takes precedence over all these variables.
3. **Inside Fabric:** `notebookutils.credentials.getToken("storage")`. Synapse's `mssparkutils`
   is used the same way.
4. **`DefaultAzureCredential`:** managed identity, a service principal from the environment
   (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`), or the Azure CLI.

Delta tables take a bearer token (delta-rs has no credential object), so a connection string
cannot open one; a key or SAS token can. `storage_options` overrides what authentication
derived.

Every CLI command that reads Azure storage accepts `--source-config KEY=REF`, before or after
the command. `KEY` is `account_key`, `sas_token`, `connection_string` or `token`; `REF` must be
an `env://`, `file://` or `kv://` reference. Literal secrets are refused. For example, append
`--source-config account_key=env://AZURE_STORAGE_ACCOUNT_KEY` to `shape profile <uri>`.
`file://` requires a private secret file; `kv://` needs the Fabric plugin or a registered
resolver. An explicit `--auth` credential takes precedence over a storage key, as on writes.
The resolved values last only for the command and are never stored in its profile.

Permanent authentication failures, including a tenant mismatch (`AADSTS50020`), stop without
repeating the sign-in. Sign in to the target tenant with an account or service principal that
belongs to it. Network timeouts, throttling and service outages retain the client's retries.
A missing Delta table reports `no Delta table at <path>`; permission and damaged-log errors
remain separate failures.

## Lakehouse table layouts

Classic Lakehouses use `onelake://<workspace>/<lakehouse>/Tables/<table>`. Schema-enabled
Lakehouses use `.../Tables/<schema>/<table>`, normally `dbo`. Both forms resolve to the same
core Delta reader; include the actual schema in the read URI.

For Delta writes, target `delta+abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse/Tables`.
For a schema-enabled Lakehouse, add `--sink-config delta.schema_enabled=true`; the schema
defaults to `dbo`, or use `--schema-name sales` for an existing schema. For a classic Lakehouse,
omit that option. `--table-prefix shape_` names the physical tables `shape_<table>` in either
layout. A target already ending in `Tables/dbo` can also be used directly, without
`schema_enabled`; the sink appends the table name once. See the
[0.9.1 limitations and workarounds](../KNOWN_LIMITATIONS.md#fabric-in-091).

## What is tested

- Every PR: contract tests with the Azure SDK replaced by recorded interactions
  (`tests/builtins/test_cloud_sources.py`): the authentication order, how `adlfs` is built for
  ADLS and OneLake, every file kind, directories, globs, schema conformance, and local Delta
  tables written with `deltalake`.
- Nightly: `tests/builtins/test_cloud_sources_azurite.py` (marker `emulator`) against Azurite
  from `ci/emulators/docker-compose.yml`, through the blob endpoint.
- Live OneLake and ADLS Gen2 reads (marker `live`) run only where the owner's secrets exist
  (plan O-02, O-03). Until they have run, the OneLake host mapping is verified against the
  recorded call, not against a real tenant.

## Semantic models: `semantic-model://`

The `sqllocks-shape-fabric` plugin adds a source (`shape.sources: semantic-model`) that reads one
table of a Power BI or Fabric semantic model through `sempy` (semantic link). It runs inside a
Fabric notebook, where `sempy` signs in as the notebook's user.

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

<!-- example: 0 -->

**Needs a Fabric account. Not run in CI.**

```
pip install 'sqllocks-shape-fabric[semantic-link]'
shape profile semantic-model://Sales/Retail/Customer -o customer.shape
```

<!-- owner: Fabric maintainer — supply the transcript for docs/plugins/cloud-sources.md example 0. -->


`semantic-model://<workspace>/<model>/<table>`: workspace and model are names or GUIDs, and a `/`
inside any part is written `%2F`. `shape profile-model` profiles every table of a model with its
relationships (see [fabric-commands.md](fabric-commands.md)).

| Option | Meaning |
|---|---|
| `columns` | a subset of the table's columns, in the order given |
| `batch_rows` | at most this many rows per batch (default 65,536) |
| `max_rows` | read at most this many rows, as a DAX `TOPN` through `evaluate_dax`; `0` reads none |
| `mode` | sempy's read mode (`xmla` or `onelake`), where this `sempy` has one; not with `max_rows` |

`schema(uri)` is built from the model's column metadata (`sempy.fabric.list_columns`) and reads no
rows. Hidden columns are included. Measures are not columns and are not read.

### Type map

The data type of a column (`Data Type` of `list_columns`) maps to Arrow:

| Semantic model type | Arrow type |
|---|---|
| `Int64` | `int64` |
| `Double` | `double` |
| `Decimal` | `decimal128(19, 4)` |
| `Currency` | `decimal128(19, 4)` |
| `String` | `string` |
| `Boolean` | `bool` |
| `DateTime` | `timestamp[us]` |
| `Binary` | `binary` |

`Decimal` is Power BI's fixed decimal number (four decimal places). Any other type is read as a
string and reported once on stderr:
`shape: warning: semantic-model column Customer[Code] has type Variant; read as string`.

### Where it runs, and errors

`sempy` is imported only when a `semantic-model://` URI is opened. Outside a Fabric notebook, or
without the package, the source stops with one line and exit code 2:
`shape: error: semantic-model:// needs sempy (pip install 'sqllocks-shape-fabric[semantic-link]') and runs inside a Fabric notebook`.
An unknown name is reported as given, for example
`shape: error: semantic model Retail in workspace Sales has no table Custmer`. Names that reach
DAX (`max_rows`) are quoted, so a table or column name cannot end the expression.

Tests use a fake `sempy` (`plugins/shape-fabric/tests/fake_sempy.py`); a `live`-marked test
(`test_live_semantic_model.py`) reads a real model where `FABRIC_WORKSPACE_ID` and
`FABRIC_SEMANTIC_MODEL` are set.
