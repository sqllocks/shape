# MySQL

Understand the shipped database adapter before you select a target.

Status: available.

## What the code does

Writes bound batched multi-row INSERT statements through PyMySQL. MySQL DDL commits implicitly; row rollback does not restore a replaced or truncated table.

## Connection and data handling

The `mysql` URI selects the database. Non-loopback hosts default to certificate and hostname verification; explicit TLS options override it. NaN and infinity are written as NULL. The integrations plugin reads a selected table through its MySQL source.

The databases plugin imports its optional driver only when opening a connection. A missing
driver fails the command instead of silently selecting another sink. Create mode refuses an
existing table. Append, truncate and replace have different effects; review the target before
you select a destructive mode. A write error can report committed rows, so a retry needs an
idempotency decision.

Evidence: `plugins/shape-databases/src/shape_databases/mysql.py` and shared adapter helpers.
The integrations source definitions are in `plugins/shape-integrations/src/shape_integrations/`.
Snowflake and Databricks are write targets; Shape does not profile from them yet.

## Next step

[Owner: database maintainer — execute a MySQL read/write transcript against a disposable server before publishing commands.]

These pages describe implementation behavior. They make no account or service-validation
claim. Authentication, permissions and server limits remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
