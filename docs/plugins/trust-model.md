# Plugin trust model

Plugins are **trusted, in-process Python code**. Installing a plugin is the same decision as
installing any other Python package: it runs with the permissions of the process that loads
it, it can read and write what that process can, and Shape does not sandbox, isolate or
restrict it. Shape never claims otherwise.

What Shape does, and does not, do:

- **Loading.** The host discovers plugins through Python entry points and loads a plugin only
  when it is used. A plugin that fails to import or build is contained: it is reported by
  `shape plugins doctor` and does not stop `shape profile` or any other command that does not
  use it. Containing a load failure is not a security boundary.
- **API check.** A plugin declares the plugin API version it targets; the host refuses an
  incompatible major version. This is a compatibility check, not a permission check.
- **Allow-list and signatures (opt-in).** You can tell the host which plugin code may load, and
  check that its files did not change after installation and that it was signed by a key you
  trust. See [the allow-list](#the-plugin-allow-list) below. With neither switched on, nothing
  changes.
- **No sandbox.** There is no subprocess runtime, no capability negotiation for plugins and
  no network, filesystem or subprocess allow-list. (The `.shape` format's "capabilities" in
  `shape.spec.capabilities` are about file-format features and are unrelated to plugins.)

What you should do:

- Install plugins only from sources you trust, pin their versions, and review them as you
  would any dependency.
- Where you need confinement, apply it to the whole process: containers, OS accounts, egress
  rules and secret scoping are deployment controls (see `docs/THREAT_MODEL.md`).
- Treat a plugin's output like any other input: Shape validates artifacts it reads
  (hashes, paths and sizes) whatever produced them, and signed artifacts can be verified with
  `shape verify`.

## The plugin allow-list

An organisation that runs Shape in a shared pipeline can control which plugin code can load at
all. The check runs in `PluginHost` **before a plugin is imported**; a plugin it refuses has the
status `blocked`, is never imported, and `get` raises `PluginLoadError` (the CLI prints
`shape: error: plugin sources:kafka (sqllocks-shape-kafka 0.9.0) is not on the plugin allow-list
PATH` and exits 2). A blocked plugin does not stop commands that do not use it. Shape's own
built-ins are always allowed.

### Switching it on

- `SHAPE_PLUGIN_ALLOWLIST=PATH` in the environment, or
- `plugins: {allowlist: PATH}` in `shape.yml` (an additive key; a path in `shape.yml` is relative
  to that file). The environment variable wins.

With neither, behaviour is unchanged. A file that cannot be read or fails its checks blocks every
non-built-in plugin (it never means "no list"), and the reason is shown by `shape plugins list`.

### The file

JSON or YAML, `format: "shape-plugin-allowlist"`, `version: 1` (a newer version is refused with a
message to upgrade). The JSON Schema is `src/shape/schemas/plugin-allowlist-v1.schema.json`.

```json
{
  "format": "shape-plugin-allowlist",
  "version": 1,
  "plugins": [
    {
      "distribution": "sqllocks-shape-kafka",
      "version": "==0.9.0",
      "record_sha256": "<sha256 of the installed RECORD file>",
      "names": ["stream_sources:kafka", "emitters:kafka"]
    }
  ],
  "require_signature": false,
  "trusted_keys": [{"key_id": "56475aa75463474c", "public_key": "acme.pub"}]
}
```

| Key | Meaning |
|---|---|
| `plugins[].distribution` | Required. The distribution name (`-`, `_` and `.` are equivalent). A plugin from a distribution that is not listed is blocked. Listed twice is an error. |
| `plugins[].version` | Optional PEP 440 specifier (`==0.9.0`, `>=0.9,<1`). The installed version must satisfy it. Evaluating it needs the `packaging` package; without it the plugin is blocked. |
| `plugins[].names` | Optional `group:name` list (`sources:kafka` or `shape.sources:kafka`). Only these plugins of the distribution may load. |
| `plugins[].record_sha256` | Optional. The sha256 of the distribution's `RECORD`. When given, `RECORD` must match it and every file `RECORD` gives a hash for must match that hash; the first changed or missing file is named in the reason. |
| `require_signature` | Default `false`. When `true`, every listed distribution must carry a valid signature (below). |
| `trusted_keys` | `key_id` and `public_key`, a path (relative to the allow-list file) of a public key file as `shape keygen` writes it. The key id must match the file. |

`shape plugins allowlist init [-o PATH] [--pin-hashes] [--json]` writes an allow-list for the
plugins installed now (default file `shape-plugin-allowlist.json`; `.yml` writes YAML). It imports
none of them and refuses to overwrite a file (exit 2). Review it before using it: it allows
everything that is installed.

### Signatures

With `require_signature: true` a distribution must contain `shape-plugin.sig` in its `.dist-info`
folder: an Ed25519 signature (algorithm and key id are in the file, `format:
"shape-plugin-signature"`, `version: 1`) over the distribution's canonical file list, made by a key
in `trusted_keys`. The file list is the sorted `path sha256` lines of the files `RECORD` hashes,
without `RECORD`, the signature file, the files an installer adds (`INSTALLER`, `REQUESTED`,
`direct_url.json`) and files outside the install folder (scripts). The check also requires every
file to still have the hash `RECORD` gives it. Signing and checking need the `[sign]` extra; if
`cryptography` is missing while `require_signature` is `true`, every non-built-in plugin is blocked
and the message says `pip install 'sqllocks-shape[sign]'`.

- `shape plugins sign WHEEL --key KEY [-o OUT]` adds the signature to a wheel and updates its
  `RECORD` (in place, or into `OUT`). It refuses a wheel whose files do not match its `RECORD` and
  a wheel with a `.data` folder. KEY and passphrase options are those of `shape sign`.
- `shape plugins verify DIST_OR_WHEEL [--key PUBLIC.pub] [--json]` exits 0 when the signature is
  valid and the files match, 1 when it is missing or invalid (the reason is printed), 2 on a usage
  error. Without `--key` it uses the `trusted_keys` of the active allow-list.

### Seeing the result

`shape plugins list` shows `allowed` or `blocked -- reason` for each plugin without importing
anything (`--json` adds `allowed` and `reason`). `shape plugins doctor` lists blocked plugins
separately; it exits 1 when a *listed* plugin fails its version, file or signature check, and 0
when a plugin is merely not listed (that is reported, not an error).

### What this checks, and what it does not

It checks **which code may load** (distribution, version, plugin name) and **whether its installed
files changed** since the hash or signature was made. It does not check **what loaded code does**:
a plugin that is allowed and signed is still trusted, in-process Python, and can reach everything
the process can (files, environment variables, network, subprocesses). There is no sandbox (D-09).
Limits to know about: files that are not in `RECORD`, and files `RECORD` lists without a hash
(such as `.pyc` files), are not covered; a signature proves who signed the file list, not that the
code is safe; a key you trust can sign anything.

## What a plugin can reach

This is what each first-party plugin under `plugins/` contacts, reads and writes, so that a
deployment can set egress rules and secret scoping (see `docs/THREAT_MODEL.md`). The list covers
Shape's own code in the plugin; third-party libraries behave as they document. No first-party
plugin starts a subprocess, and none keeps a token cache or key file of its own. The table is
checked against the source by `tests/plugins/test_trust_reach.py`.

| Plugin | Network endpoints | Environment variables read | Local files read or written |
|---|---|---|---|
| `shape-databases` | The PostgreSQL or MySQL host in the URI you give (`postgresql://`, `mysql://`), through psycopg and PyMySQL. No fixed host. With a `credential` option it asks that credential for a token for the audience `ossrh-postgresql.azure.com` (the token is the password). | `SHAPE_POSTGRES_PASSWORD`, `PGPASSWORD`, `SHAPE_MYSQL_PASSWORD`, `MYSQL_PWD` (secrets). Any variable you name in an `env://NAME` reference. | Writes none. Reads TLS files you name (`sslrootcert`, `sslcert`, `sslkey`, `ssl_ca`, `ssl_cert`, `ssl_key`), `file://` password files (refused if group- or world-accessible) and, through libpq, `~/.pgpass`. |
| `shape-domains` | None. | None. | Reads its packaged data (`data/retail/*.json`, `*.arrow`). Writes nothing. |
| `shape-eventhubs` | The Event Hubs namespace in the connection string or `eventhubs://` URI (AMQP over TLS, azure-eventhub). With no connection string, Microsoft Entra sign-in through `azure.identity` (its login endpoint, the managed-identity endpoint, or the `az` command). | `SHAPE_EVENTHUBS_CONNECTION_STRING` (secret). With Entra sign-in, azure-identity's own variables (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` and the like). | None. |
| `shape-fabric` | `api.fabric.microsoft.com` (workspace and item management, HTTPS). `onelake.dfs.fabric.microsoft.com` (OneLake files). `<vault>.vault.azure.net` (Key Vault secrets for `kv://` references). Token audiences `storage.azure.com` and `database.windows.net`. The Eventhouse query URI, SQL endpoint, Eventstream or Event Hubs endpoint you give; `eventhouse://...?tls=false` uses plain HTTP without a token. Sign-in through azure-identity (`--auth cli`, `msi`, `spn`, `device-code`) or the notebook identity, which contact Entra, the managed-identity endpoint or the `az` command. | `SHAPE_WORKSPACE_ID`, `SHAPE_LAKEHOUSE_ID`, `SHAPE_LAKEHOUSE_PATH`, `SHAPE_SQL_CONNECTION` (secret if it holds a password), `SHAPE_EVENTHOUSE_TOKEN` (secret), `SHAPE_EVENTSTREAM_CONNECTION_STRING` (secret), any variable you name in an `env://NAME` reference, and azure-identity's variables when it signs in. | Writes lakehouse files (`landing/<domain>/<entity>/...`, `run_manifest.json`) to the local folder or OneLake path you give, atomically for local paths; writes warehouse staging files in OneLake and deletes them afterwards; writes the `export-model` `.bim` file and `notebook -o` file you name. Reads the schema file you give, `file://` credential files and, from the `onelake://` source, remote files. The developer tool `python -m shape_fabric.scenarios` writes recording files you name. |
| `shape-kafka` | The Kafka brokers in the `kafka://host:9092,host2:9092/topic` URI (librdkafka). A `config` option can add security settings and name key or certificate files, which librdkafka reads. | None. | None. |
| `shape-simulation` | None of its own. A `--sink` URI is handed to the emitter plugin for that scheme (`kafka://`, `eventhubs://`, `eventstream://`, `eventhouse://`), so the endpoints of those plugins apply. | None. | Writes under the output folder you give (`-o`): partitioned data files, `_manifest.json`, `_done` flags, `events.jsonl`, `stats.json`. Reads the schema file you give and re-reads files it just wrote to hash them. |
| `shape-sqlserver` | The SQL Server or Fabric SQL host in the `mssql://` URI, `--server` or connection string (ODBC Driver 18, encrypted by default; token audience `database.windows.net`). Sign-in through azure-identity for `--auth cli`, `msi` and `spn`, or the notebook identity for `--auth fabric`. | `SHAPE_SQLSERVER_CONNECTION_STRING` (secret), `SHAPE_SQLSERVER_CLIENT_SECRET` (secret), and azure-identity's variables with `--auth msi`. | `shape profile-db` writes the profile (`-o OUT.shape`) and summary (`--json FILE`) you name. Reads none. |
