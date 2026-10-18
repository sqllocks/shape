# DuckDB

Understand the shipped database adapter before you select a target.

Status: available.

## What the code does

Writes Arrow batches with INSERT SELECT into a local database file. The databases sink supports create, append, truncate, replace and upsert; upsert requires keys.

## Connection and data handling

`duckdb:///rel.duckdb` writes a relative file. `duckdb:////absolute/path.duckdb` writes an absolute file. The integrations source uses `duckdb://rel.duckdb?table=T` for a relative read, and opens the file read-only. The source rejects a missing table, unknown options and in-memory targets.

The databases plugin imports its optional driver only when opening a connection. A missing
driver fails the command instead of silently selecting another sink. Create mode refuses an
existing table. Append, truncate and replace have different effects; review the target before
you select a destructive mode. A write error can report committed rows, so a retry needs an
idempotency decision.

Evidence: `plugins/shape-databases/src/shape_databases/duckdb_sink.py` and shared adapter helpers.
The integrations source definitions are in `plugins/shape-integrations/src/shape_integrations/`.
Snowflake and Databricks are write targets; Shape does not profile from them yet.

## Next step

Use [the tested domain tutorial](../tutorials/06-domain-duckdb.md) for real commands and output. It needs both databases and integrations plugins with DuckDB.

These pages describe implementation behavior. They make no account or service-validation
claim. Authentication, permissions and server limits remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
