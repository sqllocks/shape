# Cloud sources: OneLake, ADLS Gen2 and Delta

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
2. **Inside Fabric:** `notebookutils.credentials.getToken("storage")`. Synapse's `mssparkutils`
   is used the same way.
3. **`DefaultAzureCredential`:** managed identity, a service principal from the environment
   (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`), or the Azure CLI.

Delta tables take a bearer token (delta-rs has no credential object), so a connection string
cannot open one; a key or SAS token can. `storage_options` overrides what authentication
derived.

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
