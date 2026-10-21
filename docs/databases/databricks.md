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
Databricks also profiles SQL warehouse tables and Unity Catalog schemas.

## Write receipt

Owner-run on 2026-10-11 in a clean Windows 11 VM, Python 3.12.10, using Shape 0.9.1 installed from PyPI. The target was Databricks Free Edition’s serverless SQL warehouse. The personal access token was in `DATABRICKS_TOKEN`, outside the URI; keep it in your environment when running the command.

Fill in the workspace, warehouse, catalog and schema identifiers below. Target identifiers are substituted in both the command and output.

```powershell
shape generate retail --scale small --seed 42 --to "databricks://<WORKSPACE_HOST>/sql/1.0/warehouses/<WAREHOUSE_ID>?catalog=<CATALOG>&schema=<SCHEMA>" --write-mode create --yes
```

Owner-reported output, exit 0:

```text
Sunday, October 11, 2026 6:55:25 PM
shape 0.9.1
Wrote 21,750 rows in 9 tables to databricks://<WORKSPACE_HOST>/sql/1.0/warehouses/<WAREHOUSE_ID>?catalog=<CATALOG>&schema=<SCHEMA> (108.30s)
```

The elapsed time records this single run. The [Owner-run receipt](../evidence/databricks-write.md) records the install command, warehouse configuration and all nine persisted table counts verified with `COUNT(*)` in the SQL Editor.

The receipt records one test-account run. Authentication, permissions and server limits
remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Profile a table or schema

Use `databricks://HOST/sql/1.0/warehouses/ID?catalog=C&schema=S&table=T` with
`shape profile` or the bridge profile handler. Omit `table` to profile a whole schema.
This source supports SQL warehouses. Authentication uses the sink's token or OAuth
machine-to-machine options; credentials are refused in URIs before connecting.

The connector's `fetchall_arrow` supplies Arrow results. Bounded distributions use
`TABLESAMPLE (... PERCENT)` and a limit. Catalog population counts come from `DESCRIBE
TABLE EXTENDED` statistics. Missing row statistics fail with an instruction to ask the
table owner to collect them; Shape does not modify statistics. Primary and foreign keys
come from Unity Catalog `information_schema.table_constraints`, `key_column_usage` and
`referential_constraints`. Key relationships carry `evidence: "declared"`.

TINYINT, SMALLINT, INT and BIGINT retain int8, int16, int32 and int64 widths.
DECIMAL(p,s) uses decimal128(p,s). TIMESTAMP uses UTC; TIMESTAMP_NTZ has no timezone.
ARRAY, STRUCT and MAP retain recursive native Arrow types from `full_data_type` catalog
declarations. This parses declared child types and does not profile child fields. Catalog row counts,
`sampled_rows` and `sampling` distinguish the population and sampled distributions;
zero requested sample rows reads the catalog only.

Offline fake catalogs and Arrow result sets cover these reads. Live account validation
is pending Owner settings.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
