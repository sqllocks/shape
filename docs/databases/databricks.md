# Databricks

Understand the shipped database adapter before you select a target.

Status: available.

## What the code does

Writes bound multi-row INSERT statements to Delta tables through a SQL warehouse. Each statement commits; there is no transaction around the whole write.

## Connection and data handling

The URI selects host, HTTP path, catalog and schema. Tokens in the URI are refused. Credentials use a configured token or OAuth client pair. Table/schema names must meet the sink's lower-case rules; columns follow its Delta identifier restrictions. A failed append can leave rows already committed.

The databases plugin imports its optional driver only when opening a connection. A missing
driver fails the command instead of silently selecting another sink. Create mode refuses an
existing table. Append, truncate and replace have different effects; review the target before
you select a destructive mode. A write error can report committed rows, so a retry needs an
idempotency decision.

Evidence: `plugins/shape-databases/src/shape_databases/databricks.py` and shared adapter helpers.
The integrations source definitions are in `plugins/shape-integrations/src/shape_integrations/`.
Snowflake and Databricks are write targets; Shape does not profile from them yet.

## Next step

[Owner: platform maintainer — execute a Databricks write transcript with a test account before publishing commands.]

These pages describe implementation behavior. They make no account or service-validation
claim. Authentication, permissions and server limits remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
