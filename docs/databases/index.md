# Databases

Choose a local or server writer and distinguish it from a profiling source.

Status: available.

`shape-databases` writes generated Arrow batches to DuckDB, PostgreSQL, MySQL, Snowflake and
Databricks. Optional driver imports happen when a connection opens. `shape-integrations`
supplies database reads for PostgreSQL, MySQL and DuckDB. Snowflake and Databricks are write
targets; Shape does not profile from them yet.

- [DuckDB](duckdb.md): a local file, covered by the sixth starter tutorial.
- [PostgreSQL](postgresql.md): COPY writes and integrations reads.
- [MySQL](mysql.md): bound batched INSERT writes and integrations reads.
- [Snowflake](snowflake.md): staged Parquet and COPY INTO writes.
- [Databricks](databricks.md): bound INSERT writes to SQL warehouses.

Cloud and server command transcripts need owner execution with an appropriate test account.
The pages document code behavior without claiming platform validation.

## Related

[What leaves my machine](../WHAT_LEAVES.md) · [Generate a domain locally](../tutorials/06-domain-duckdb.md)
