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

## Next step

[Owner: platform maintainer — execute a Snowflake write transcript with a test account before publishing commands.]

These pages describe implementation behavior. They make no account or service-validation
claim. Authentication, permissions and server limits remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
