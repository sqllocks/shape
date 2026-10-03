# Fabric commands

The `sqllocks-shape-fabric` plugin adds `shape fabric`, with five commands, each also a top-level
command of its own, and `shape profile-model`:

| `shape fabric …` | top-level | what it does |
|---|---|---|
| `publish DOMAIN -t TARGET` | `shape publish` | generate a domain and publish it to a Lakehouse, Warehouse, SQL Database or Eventhouse |
| `notebook DOMAIN` | `shape notebook` | make a ready-to-run Fabric notebook for a domain |
| `deploy-notebook DOMAIN --workspace W` | `shape deploy-notebook` | make that notebook in a Fabric workspace |
| `setup --workspace W` | `shape setup-fabric` | make a Fabric Environment (and a Lakehouse) for Shape |
| `export-model DOMAIN` | `shape export-model` | export a domain as a Power BI semantic model (`.bim`) |

Exit codes: `0` done; `1` the service or destination failed (a sign-in, a write, an HTTP error);
`2` the command line or input is wrong (an unknown domain, a missing option). Every message is
redacted: a connection string, key or token never reaches the terminal.

Sign-in is `--auth cli|msi|spn|sql|device-code|fabric` with the references described in
[fabric-auth.md](fabric-auth.md). With no `--auth`, the Azure CLI's login (`cli`) is used. `sql` is a
database login: it applies to `publish -t sql-database|warehouse` only.

## `publish`

```
shape publish retail -t lakehouse --base-path onelake://MyWorkspace/MyLakehouse/Files
shape publish retail -t sql-database --connection-string env://SHAPE_SQL_CONNECTION
shape publish retail -t warehouse --connection-string env://WH --staging-path onelake://W/L/Files
shape publish retail -t eventhouse --connection-string https://<query-uri-host> --database mydb
```

`DOMAIN` is a domain or a generation schema file. `-s/--scale`, `--seed`, `-m/--mode 3nf|star` as in
`shape generate`. `--dry-run` generates and prints the table summary and publishes nothing.

* **Lakehouse**: `--base-path` is a local folder, an `abfss://` or an `onelake://` path (env
  `SHAPE_LAKEHOUSE_PATH`). Each table is written to
  `landing/DOMAIN/TABLE/dt=latest/part-0001.EXT` (`--format parquet|csv|jsonl`; `delta` writes Delta
  tables to a local folder and is refused for OneLake), and the run manifest to
  `landing/DOMAIN/manifest/_control/run_manifest.json`. The manifest holds the run id
  (`YYYYMMDD_HHMMSS_DOMAIN_SCALE_sSEED`), the domain, scale, seed and engine version, every table's
  rows, columns and file, `--workspace-id` and `--lakehouse-id` (env `SHAPE_WORKSPACE_ID`,
  `SHAPE_LAKEHOUSE_ID`) and a software bill of materials.
* **sql-database** / **warehouse**: `--connection-string` (env `SHAPE_SQL_CONNECTION`) is a
  reference or a connection string without a password; `--credential REF` names a reference that
  holds it. The Warehouse stages Parquet at `--staging-path` (or `--base-path`) and loads with `COPY
  INTO`. `--write-mode create|append|truncate|replace` (default `create`: an existing table is an
  error, nothing is dropped), `--batch-size`, `--schema-name`.
* **eventhouse**: `--connection-string` is the query URI (or a reference to it), `--database` the KQL
  database; one KQL table per table, `--write-mode` as above.

## `notebook` and `deploy-notebook`

```
shape notebook retail --target lakehouse -o retail.ipynb      # or print the JSON: no -o
shape deploy-notebook retail --workspace "Demo" --auth cli
```

The notebook installs Shape, generates the domain with `shape.generate`, checks foreign keys and
writes Parquet files to the default Lakehouse (`--target lakehouse`), CSV files (`csv`) or shows a
sample (`display`). `deploy-notebook` creates it as a Notebook item (`--notebook-name`, default
`Shape_DOMAIN_SCALE`); the workspace is a name or a GUID, and a name is looked up across every page
of the listing. A creation that Fabric accepts to finish later is followed to its end.

## `setup-fabric`

```
shape setup-fabric --workspace "Demo" --create-lakehouse
shape setup-fabric --snippet        # print the cell to paste into a notebook instead
```

Creates the Fabric Environment item (`--env-name`, default `shape-env`) and, with
`--create-lakehouse`, a Lakehouse (`--lakehouse-name`). An item that already exists is reused. The
command prints the libraries to add to the Environment; adding them is done in Fabric.

## `export-model`

```
shape export-model retail --source-type warehouse --source-name Sales -o retail.bim
```

Writes a Tabular Object Model document (compatibility level 1604): typed columns, relationships,
one Power Query partition per table for the source (`--source-type lakehouse|warehouse|sql_database`,
`--source-name`, `--schema-name` for the SQL sources) and DAX measures (`--include-measures`, the
default, or `--no-measures`): a row count per table, a total and an average per decimal or float
column, a total per other integer column. `-s/--scale` is accepted for the command line's sake and
does not change the model. `-m/--mode 3nf|star` picks the domain's schema. Names that reach M or
DAX are quoted. Open the file in Tabular Editor or deploy it through the XMLA endpoint.

For a Warehouse or SQL Database source the one `--source-name` is used as both the server and the
database name in `Sql.Database(...)`; edit the expression for a server whose name differs from the
database's. For a Lakehouse the expression keeps the placeholders `{workspace_id}` and
`{lakehouse_id}` for you to fill in.

## `profile-model`

```
shape profile-model Sales/Retail -o retail.shape [--tables Customer,Orders] [--max-rows 100000] [--json]
```

Profiles every table of a Power BI / Fabric semantic model as one dataset profile and records the
model's relationships, the way `shape profile-db` does for a SQL Server schema. It needs `sempy`
and runs inside a Fabric notebook (`pip install 'sqllocks-shape-fabric[semantic-link]'`); the
tables are read through the [`semantic-model://` source](cloud-sources.md#semantic-models-semantic-model).
`WORKSPACE/MODEL` are names or GUIDs (a `/` inside a name is `%2F`).

- `--tables T1,T2`: only these tables (default: every table, hidden ones included).
- `--max-rows N`: read at most `N` rows of each table (a DAX `TOPN`), so the statistics describe
  those rows.
- `--json`: print one JSON document (`written`, `shape_content_id`, `tables`, `relationships`,
  `max_rows`) instead of a sentence.

A table's column statistics equal those of the same rows profiled from a Parquet file. A hidden
column carries `hidden: true`. Each relationship of the model (`sempy.fabric.list_relationships`)
is stored in the profile's `relationships` with `evidence: "declared"`, `source: "semantic model"`,
`type` (`one_to_many`, `one_to_one` or `many_to_many`) and `active`:

- the many side is the child column, marked a foreign key (`is_foreign_key`, `fk_ref_table`,
  `fk_evidence: "declared"`); the one side is the table's primary key when the profile has none;
- an inactive relationship is recorded with `active: false` and is still a declared key;
- a many-to-many relationship is recorded and is not a foreign key: `shape generate --from` and
  `shape proposals` leave it out;
- a relationship that names a table you did not select, or a multiplicity Shape does not know
  (reported on stderr), is not recorded.

`shape proposals` and `shape generate --from` treat the declared relationships as declared keys.
The output is an ordinary `.shape` profile (format `shape`, version 1); its provenance names the
workspace, the model and `max_rows`. Exit codes: `0` done; `2` the input is wrong (a name that is
not in the model, a bad `--max-rows`, no `-o`, `sempy` missing).
