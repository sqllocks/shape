# sqllocks-shape-sqlserver

Shape plugin for SQL Server, Azure SQL and Fabric SQL (warehouse and SQL database).

- **`shape profile-db`**: profile a database schema into a `.shape` profile. The catalog views
  give exact primary keys, foreign keys, types and row counts; a sample of rows per table
  (1000 by default) gives the distributions.
- **`mssql://` source**: read one table as Arrow record batches (`shape.sources`).
- **Writing** to a live SQL Server (the `sqlserver` sink, `mssql://` URIs, bulk insert) is in
  `shape-fabric`, which builds on this plugin: see its README.
- **Shared helpers** for other SQL Server code (such as `shape-fabric`): `shape_sqlserver.sql`
  (identifier quoting, connection strings and their redaction, catalog queries, type maps) and
  `shape_sqlserver.auth` (SQL logins and Microsoft Entra tokens).

```bash
pip install 'sqllocks-shape[sqlserver]'            # also: sqllocks-shape-sqlserver[entra]
shape profile-db --server myserver.database.windows.net --database shop --auth cli \
    --schema dbo -o shop.shape
```

You also need the Microsoft ODBC Driver 18 for SQL Server (and unixODBC on Linux).
The full guide is `docs/plugins/sqlserver.md` in the repository.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md`.
