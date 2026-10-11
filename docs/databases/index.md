# Databases

See [what Shape reads and writes](https://docs.shapedata.ai/reference/capabilities/) for the current list.

Choose a local or server writer and distinguish it from a profiling source.

Status: available.

`shape-databases` writes generated Arrow batches to DuckDB, PostgreSQL, MySQL, Snowflake and
Databricks. Optional driver imports happen when a connection opens. Shape writes to
PostgreSQL and MySQL through `shape-databases`; neither has a direct profiling source
in 0.9.1. Export their data to Parquet or CSV and profile the file.

The shipped database and platform sources are `mssql`, `duckdb`, `onelake` and
`semantic-model`. DuckDB reads come from `shape-integrations`. Snowflake and Databricks
are write targets; export their data to Parquet or CSV before profiling.

Reading PostgreSQL and MySQL tables directly is planned (W9-01, issue #2).

- [DuckDB](duckdb.md): a local file, covered by the sixth starter tutorial.
- [PostgreSQL](postgresql.md): COPY writes; profile a CSV or Parquet export.
- [MySQL](mysql.md): bound batched INSERT writes; profile a CSV or Parquet export.
- [Snowflake](snowflake.md): staged Parquet and COPY INTO writes.
- [Databricks](databricks.md): bound INSERT writes to SQL warehouses.

Cloud and server command transcripts need owner execution with an appropriate test account.
The pages document code behavior without claiming platform validation.

## Related

[What leaves my machine](../WHAT_LEAVES.md) · [Generate a domain locally](../tutorials/06-domain-duckdb.md)
