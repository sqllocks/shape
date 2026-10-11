# Snowflake

Understand the shipped database adapter before you select a target.

Status: available.

## What the code does

Writes generated tables by staging local Parquet, uploading it with PUT to the table stage and running COPY INTO. The sink checks loaded row counts and removes staged and local files.

## Connection and data handling

The URI selects user, account, database and schema, with optional warehouse and role. A password in the URI is refused. Credentials use the configured key-pair or password option. Identifiers are quoted and validated. DDL commits at once; failed loads do not restore tables already replaced or truncated.

The databases plugin imports its optional driver only when opening a connection. A missing
driver fails the command instead of silently selecting another sink. Create mode refuses an
existing table. Append, truncate and replace have different effects; review the target before
you select a destructive mode. A write error can report committed rows, so a retry needs an
idempotency decision.

Evidence: `plugins/shape-databases/src/shape_databases/snowflake.py` and shared adapter helpers.
The integrations source definitions are in `plugins/shape-integrations/src/shape_integrations/`.
Snowflake and Databricks are write targets; Shape does not profile from them yet.

## Write receipt

Owner-run on 2026-10-11 in a clean Windows 11 VM, Python 3.12.10, using Shape 0.9.1 installed from PyPI and snowflake-connector-python 4.8.0. The Owner reports approximately 6:50 PM Eastern; the VM clock was incorrect. The captured run used key-pair sign-in and replace mode.

Fill in the target identifiers below. The key is referenced by its local file URI, as used in the receipt. This PowerShell example sets the recorded `SHAPE_CONFIRM_REMOTE=1` environment value. Target identifiers are substituted in both the command and output.

```powershell
$env:SHAPE_CONFIRM_REMOTE = "1"
shape generate retail --scale small --seed 42 --to "snowflake://<USER>@<ACCOUNT>/<DATABASE>/<SCHEMA>?warehouse=<WAREHOUSE>&role=<ROLE>" --write-mode replace --yes --sink-config "snowflake.private_key=file://C:/Users/shape/.snowflake/rsa_key.p8"
```

Owner-reported output, exit 0:

```text
shape 0.9.1
Wrote 21,750 rows in 9 tables to snowflake://<USER>@<ACCOUNT>/<DATABASE>/<SCHEMA>?warehouse=<WAREHOUSE>&role=<ROLE> (19.51s)
```

The elapsed time records this single run. The [Owner-run receipt](https://github.com/sqllocks/shape/blob/main/docs/plans/evidence/owner-notes/snowflake-write.md) records the install command, test-account configuration and all nine persisted table counts verified in Snowsight.

The receipt records one test-account run. Authentication, permissions and server limits
remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
