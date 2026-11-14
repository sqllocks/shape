# Databases

Choose a local or server writer and distinguish it from a profiling source.

Status: available.

`shape-databases` reads and writes PostgreSQL, MySQL, Oracle, Snowflake and Databricks,
and writes local DuckDB files. DuckDB reads come from `shape-integrations`. Optional drivers
load only when a connection opens. [New sources](../NEW_SOURCES.md) describes table and
catalog capture, credentials and sampling. Snowflake and Databricks live read validation
remains pending; their existing receipts cover writes.

- [DuckDB](duckdb.md): native local containers and the sixth starter tutorial.
- [PostgreSQL](postgresql.md): COPY writes and direct table/catalog profiling.
- [MySQL](mysql.md): batched INSERT writes and direct table/catalog profiling.
- [Oracle](../NEW_SOURCES.md#database-reads): thin-mode reads and array-DML writes.
- [Snowflake](snowflake.md): staged COPY writes and Arrow/catalog profiling.
- [Databricks](databricks.md): bound INSERT writes and Arrow/catalog profiling.

Cloud and server command transcripts need owner execution with an appropriate test account.
The pages document code behavior without claiming platform validation.

## Related

[What leaves my machine](../WHAT_LEAVES.md) · [Generate a domain locally](../tutorials/06-domain-duckdb.md)
