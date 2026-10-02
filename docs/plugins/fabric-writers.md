# Fabric writers and the `onelake://` source (`sqllocks-shape-fabric`)

Writers for Lakehouse files, SQL databases, Warehouses (`COPY INTO`), Eventhouses and Eventstreams,
and a source that reads lakehouse tables and files by `onelake://` URI. The API and the write modes
are described in the plugin's README (`plugins/shape-fabric/README.md`).

Safety rules the writers follow: the default `write_mode` is `create`, which never touches an existing
table; identifiers are validated and quoted; values are driver parameters; the `COPY INTO` location is
checked against a strict character set; errors never show passwords or tokens; a failed write rolls
back its table, and staged files and partial files are removed.

The `onelake://<workspace>/<lakehouse>/Tables/<table>` form reads a Delta table and
`.../Files/<path>` reads files, through the core `abfss://` source (see [cloud-sources.md](cloud-sources.md)).
