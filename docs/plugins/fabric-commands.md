# Fabric commands

The `sqllocks-shape-fabric` plugin adds `shape fabric`, with eight commands, each also a top-level
command of its own, and `shape profile-model`:

| `shape fabric …` | top-level | what it does |
|---|---|---|
| `publish DOMAIN -t TARGET` | `shape publish` | generate a domain and publish it to a Lakehouse, Warehouse, SQL Database or Eventhouse |
| `notebook DOMAIN` | `shape notebook` | make a ready-to-run Fabric notebook for a domain |
| `deploy-notebook DOMAIN --workspace W` | `shape deploy-notebook` | make that notebook in a Fabric workspace |
| `setup --workspace W` | `shape setup-fabric` | make a Fabric Environment (and a Lakehouse) for Shape |
| `export-model DOMAIN` | `shape export-model` | export a domain as a Power BI semantic model (`.bim`) |
| `known-answer DOMAIN -o DIR` | `shape known-answer` | generate a dataset, its model and the exact results of its DAX measures |
| `check-answers ANSWERS RESULTS` | `shape check-answers` | compare results exported from a DAX client with those answers |
| `publish-report PROFILE.shape... -o DIR` | `shape publish-report` | turn a profile history into drift tables and a semantic model ([DRIFT_REPORT.md](../DRIFT_REPORT.md)) |

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

## `known-answer`

```
shape known-answer retail --scale fabric_demo -o ka/
shape known-answer schema.json --seed 7 --measures measures.json --plant order_line.line_total=250000 -o ka/
```

Generates a dataset whose measure results are known exactly, so a model author can tell whether
the measures return the right numbers. `DOMAIN` is a domain or a generation schema file; `-s/--scale`
picks the preset (default `small`) and `--seed` the seed (default: the schema's). `-o DIR` is
required and gets:

| file | what it is |
|---|---|
| `data/TABLE.parquet` | one Parquet file per table. A `decimal` column is written as `decimal128` with its declared precision and scale (18 and 6 when it declares none), an `integer` column as `int64`, a `float` column as `float64`. |
| `model.bim` | the model of the schema, written by the same exporter as `export-model`, with the measures of the run. |
| `answers.json` | the expected value of every measure for every slice (below). |
| `queries.dax` | one `EVALUATE SUMMARIZECOLUMNS(...)` per slice, to run in any DAX client. |

Load the tables into a Lakehouse (or any store the model's partitions read), deploy `model.bim`,
run the queries, save the results and compare them with [`check-answers`](#check-answers).

**Measures and slices.** Without `--measures`, the measures are the exporter's (a row count per
table, a total and an average per decimal or float column, a total per other integer column) and
the slices are the first text column of each dimension (a table that is the parent of a
relationship). `--measures MEASURES.json` replaces both:

```json
{
  "format": "shape-dax-measures",
  "version": 1,
  "measures": [
    {"name": "Items", "table": "item", "aggregation": "count"},
    {"name": "Revenue", "table": "item", "aggregation": "sum", "column": "price"},
    {"name": "Revenue per Item", "table": "item", "aggregation": "ratio",
     "numerator": "Revenue", "denominator": "Items"}
  ],
  "slice_by": ["category.label", "category.region"]
}
```

`aggregation` is `count` (the rows of `table`; no `column`), `sum`, `avg`, `min` or `max` (of a
numeric `column` of `table`) or `ratio` (`DIVIDE` of two other measures of the file, which are not
ratios themselves). Names are unique in the file. `slice_by` lists `TABLE.COLUMN` attributes
(optional: without it only the grand total is computed). Every key is checked: an unknown one, a
newer `version` or a name that is not in the schema exits 2. Only these measures are computed; the
command does not read DAX.

**What is exact.** `answers.json` (format `shape-dax-answers`, version 1) holds, for the grand total
(`q01`) and every slice (`q02`, ...), one row per group with the value of each measure. Counts,
minimums, maximums and sums of integer and decimal columns are computed with `Decimal` arithmetic
over the written tables and compared exactly. Averages, ratios and sums of `float` columns are exact
fractions (`"fractions"` holds `numerator/denominator`) rounded half-even to `places` places (6);
`check-answers` compares those at the answer's places. A group whose measures are all blank (a
dimension value with no fact rows) is not in the answers, as `SUMMARIZECOLUMNS` does not return it,
and a null attribute value is the blank group (`null` in the key).

A measure is sliced by an attribute of its own table or of a table reached by following
relationships from the many side to the one side (an `order_line` measure by `customer.first_name`
through `order`). The relationships of the model filter in one direction, so a measure of a parent
table is not sliced by an attribute of its child, and a measure with two relationship paths to the
slice is ambiguous: those pairs are left out and listed under `skipped` with the reason, and
`known-answer` prints the number of skipped pairs on stderr. With the exporter's
default measures and slices most skipped pairs are one-direction ones: a measure of a parent table
(`product_category`, `product`) sliced by an attribute of a child or of an unrelated table
(`customer.first_name`). In
`queries.dax` each measure is referenced by its table (`'item'[Revenue]`) and returned as
`"item.Revenue"`, because two tables can hold a measure of one name (the exporter's defaults do:
`Total Unit Price` of `product` and of `order_line`; give them distinct names with `--measures`
before deploying one model).

**Planted totals.** `--plant TABLE.COLUMN=VALUE` (repeatable) moves the values of a `decimal` column
so that their total is exactly `VALUE`, deterministically and inside the generator's bounds: the
`min` and `max` of a distribution, `low` and `high` of a uniform one, the smallest and largest of a
choice's values, or the column's precision; a generator with no lower bound lets the values go down
to 0. The difference is shared out in proportion to each value's room to move (up when the total must
grow, down when it must shrink), in whole units of the column's last place, so no value leaves its
bounds and null values stay null. Nothing is random: the same data and the same plant always give the same values. The planted
totals are in `answers.json` under `plants`, and flow into every slice. A plant that cannot be met
exits 1 and names the column, the requested total and the reachable range (the number of non-null
values times the lower and the upper bound); so does a total with more decimal places than the
column has. A plant changes the final table only: a column computed from the planted one (an
`order_total` that sums `line_total`) is not recomputed.

The answers are computed in Python over every row, once per measure and slice, so the time grows
with rows times measures times slices: use a small scale (the default) for a known-answer dataset.

Exit codes: `0` written; `1` a plant cannot be met or a file could not be written; `2` the input
is wrong (an unknown domain, scale, table or column, a malformed measures file or plant, a
non-decimal plant column). The same inputs give byte-identical `answers.json`, `queries.dax` and
`model.bim`.

## `check-answers`

```
shape check-answers ka/answers.json results/
shape check-answers ka/answers.json results.json --places 4
```

`RESULTS` is a CSV or JSON export of the queries of `queries.dax`:

* a **folder** with one file per query, named by the query id (`q01.csv`, `q02.json`, ...); other
  files in it are not results;
* or **one file** that says which query each row belongs to: a `query` column (CSV or JSON rows), or
  a JSON object that maps a query id to its rows. A file with one query's rows needs no id when
  `answers.json` has one query only. A JSON file may also be the response of the Power BI
  `executeQueries` REST call.

Columns are found by name, however the client writes it: a measure as `item.Revenue` or
`[item.Revenue]`, a grouping column as `category[label]`, `'category'[label]` or `[label]`. Other
columns are ignored. Counts, minimums, maximums and sums of integer and decimal columns are compared
exactly (`300.0` is `300`; a difference in the seventh decimal place is a mismatch). Averages, ratios
and float sums are compared rounded half-even to the answer's places, or to `--places N` (0 to 30)
when it is given: both sides are rounded, the expected value from its exact fraction, so an export
that kept two places is checked with `--places 2`. `--places` never loosens a count or a sum.

Exit `0`: every value matches. Exit `1`: a mismatch; the measure, the slice and group, the expected
and the observed value are printed (at most 50, then a count). A group that is missing, an extra
group and a blank cell where a value is expected are mismatches. Exit `2`: a file is malformed (not
JSON, a header-less or empty CSV, a cell that is not a plain number such as `1,234`, a column that
is missing or repeated, a group that appears twice), a query has no result, or a result is for a
query the answers do not have.

### How-to: unit test a DAX measure with known answers

1. `shape known-answer MY_SCHEMA.json --measures measures.json --plant fact.amount=1000000 -o ka/`
   makes data whose total `amount` is exactly 1,000,000. Add a plant for each total a test of your
   own measure should hit.
2. Load `ka/data/*.parquet` into the store your model reads (a Lakehouse for the default
   `--source-type lakehouse` partitions) and deploy `ka/model.bim`, or add your measures to the
   model you already have.
3. Make the measures of the model and `measures.json` agree: for each measure you wrote, add the
   structured form (`aggregation`, `column`) to `measures.json` under the same table and name, and
   run `queries.dax`. A measure whose DAX does something other than its structured form (a filter, a
   time-intelligence shift) will not match: that is the test failing, which is the point.
4. Export each query's result from your DAX client as CSV or JSON (the response of the Power BI
   `executeQueries` REST call is read too), one file per query, named `q01`, `q02`, ...
5. `shape check-answers ka/answers.json results/`. In CI, exit 1 fails the build and the mismatches
   are in the log. Change one digit of an export to see it fail.

The answers do not depend on any engine: they are computed from the Parquet files that were
written, so a mismatch is the measure or the model, never the answer key.
