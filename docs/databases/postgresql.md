# PostgreSQL

Understand the shipped database adapter before you select a target.

Status: available.

## What the code does

Writes batches with psycopg 3 COPY FROM STDIN. By default the table and rows are one transaction. With commit_rows, prior commits remain after a later failure.

## Connection and data handling

The sink accepts the `postgresql` and `postgres` URI schemes. Non-loopback hosts default to TLS verify-full; explicit URI options can override that. Credentials can come from an option or environment reference. The integrations plugin reads a selected table through its PostgreSQL source.

The databases plugin imports its optional driver only when opening a connection. A missing
driver fails the command instead of silently selecting another sink. Create mode refuses an
existing table. Append, truncate and replace have different effects; review the target before
you select a destructive mode. A write error can report committed rows, so a retry needs an
idempotency decision.

Evidence: `plugins/shape-databases/src/shape_databases/postgres.py` and shared adapter helpers.
The integrations source definitions are in `plugins/shape-integrations/src/shape_integrations/`.
Snowflake and Databricks are write targets; Shape does not profile from them yet.

## Next step

<!-- owner: database maintainer — execute a PostgreSQL read/write transcript against a disposable server before publishing commands. -->

These pages describe implementation behavior. They make no account or service-validation
claim. Authentication, permissions and server limits remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
