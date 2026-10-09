# Signing in to Fabric and Azure (`--auth`) and credential references

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


`shape generate --scale-mode` (the SQL Database, Warehouse, Lakehouse and KQL sinks, and `fabric_spark`),
`shape generate --to synapse://...` (a Synapse dedicated SQL pool),
`shape emit` / `shape stream` (`eventhouse://`, `eventstream://`), `shape profile` (`onelake://`, `abfss://`)
and `shape jobs` take the same sign-in options. The sign-in itself is the `sqllocks-shape-fabric` plugin's
(`shape_fabric.auth`); core only reads the options and has no cloud dependency.

## `--auth`

| mode | signs in as | needs |
|---|---|---|
| `cli` (default) | the Azure CLI session (`az login`) | `azure-identity` |
| `msi` | a managed identity (the Fabric notebook identity first, when inside a notebook) | `azure-identity`; `--client-id` for a user-assigned identity |
| `spn` | a service principal | `--tenant-id`, `--client-id`, `--client-secret REF` |
| `sql` | a SQL login | `--sql-user`, `--sql-password REF`, and a connection string |
| `device-code` | you, in a browser (the address and code go to stderr) | `azure-identity`; `--tenant-id` optional |
| `fabric` | the Fabric notebook identity only (no fallback) | running in a Fabric notebook |
| `kerberos` | a Windows (domain) account, with a keytab on Linux and macOS | `--keytab REF`, `--principal NAME@REALM`; `kinit` (MIT Kerberos) on `PATH`; `mssql://` targets only |

`azure-identity` comes with `pip install 'sqllocks-shape-fabric[entra]'`. A SQL destination is given as
`--connection-string` (the server and database; Entra modes send a token, `sql` adds the login in memory)
or `--sink-config sql_database.connection_string=...`. For `fabric_spark`, `--auth` gives the Fabric API
and OneLake tokens; `SHAPE_FABRIC_TOKEN` / `SHAPE_FABRIC_STORAGE_TOKEN` still win when set.

## `--auth kerberos`: a SQL Server that takes Windows authentication only

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

For `shape generate --to mssql://` and `shape emit` / `shape stream --to mssql://` (not
`warehouse://`, not `--scale-mode` jobs). On Linux and macOS Shape runs
`kinit -k -t KEYTAB -c CACHE PRINCIPAL` into a **private credential cache** and connects with
`Trusted_Connection=yes` (ODBC Driver 18); `--connection-string` may still give the server and
database (without a login). `--keytab` is a credential reference:

* `file://PATH`: the keytab file, which must be mode 600 (a keytab other users can read is refused
  before `kinit` runs);
* `kv://VAULT/NAME`: an Azure Key Vault secret that holds the keytab **base64-encoded**; Shape decodes
  it into a mode-600 temporary file that is removed as soon as `kinit` returns.

The cache is a file in a new mode-700 directory. `KRB5CCNAME` is set only while a connection is
being opened and put back afterwards; the cache is removed when the command ends, also after an
error. On Windows there is no keytab: the signed-in account is used (`Trusted_Connection=yes`), and
`--keytab` is an error.

| error (all exit 2) | when |
|---|---|
| `--auth kerberos needs the MIT Kerberos client (kinit) on PATH` | `kinit` is not installed |
| `kinit failed for PRINCIPAL with keytab REF: MESSAGE` | `kinit` failed; the message is `kinit`'s, with the keytab reference and the principal and never the keytab bytes |
| `on Windows, --auth kerberos uses the signed-in account; leave out --keytab` | `--keytab` on Windows |
| `--auth kerberos needs --keytab and --principal` | one of them is missing (Linux, macOS) |
| `--keytab and --principal belong to --auth kerberos` | used with another mode |

A live Kerberos realm is not part of the automated tests (a fake `kinit` covers the flow); a run
against a real domain is an owner action.

## Credential references

Every secret option takes a **reference**, never the secret:

| reference | secret |
|---|---|
| `env://NAME` | the environment variable `NAME` |
| `file://PATH` | the text of a file (one trailing newline removed) |
| `kv://VAULT/SECRET[/VERSION]` | an Azure Key Vault secret (`https://VAULT.vault.azure.net`), read with the ambient Entra identity (environment, managed identity, Azure CLI); provided by the Fabric plugin |

One resolver (`shape.security.credrefs`) serves these and the signing-key sources (`--key env://...`).
`kv://` is a plugin function (`shape_fabric.keyvault`, plain HTTPS: no Key Vault SDK); without the plugin it
fails with a message saying so, and a host can register its own with `register_resolver("kv", fn)`.

* **`file://` refuses a secret file that other users can access** (POSIX: any group or other permission bit,
  for example mode 0644): the command fails with `chmod 600 PATH` in the message, **before the file is read**.
  Refusing, not warning: a warning is read after the secret has been exposed. On Windows the operating
  system's ACLs apply and Shape does not check them. A key that is not secret (a public key) is read with
  `private=False` and may be world-readable.
* **A secret is never a command-line value.** `--client-secret` and `--sql-password` accept references only,
  and a `--connection-string` or `--sink-config ....connection_string=` that holds `PWD=`, `Password=`,
  `AccountKey=` or `SharedAccessKey=` is refused, because command lines show in the process list and in shell
  history. Put the whole string in a variable and pass `env://NAME`. The refusal does not repeat the value.
* **The SQL password reaches the ODBC driver and nothing else.** The login is added to an in-memory
  connection string (values in `{...}`, `}` doubled, so `;` and `=` in a password cannot add attributes); no
  process is started with it.
* **A job record keeps references, never secrets**, so `shape jobs resume` and `shape jobs status` sign in
  again by themselves; tokens are never stored.
* **Errors, logs and reports are redacted.** Passwords, keys, tokens and bearer values are hidden in every
  message that can show a connection string (a driver often echoes it), in job errors and in log lines
  (`shape.security.redact`). A sign-in library's failure for a service principal is re-raised without
  the secret and without the original exception chained. Recorded test fixtures are scrubbed and a test fails
  if one holds a secret.
