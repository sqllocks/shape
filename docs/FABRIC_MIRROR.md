# Fabric open mirroring landing zone (`fabric-mirror` sink)

Status: experimental.


The `fabric-mirror` sink writes tables in the format a Microsoft Fabric **open mirrored database**
reads from its landing zone, so generated data, or the changes `shape continue` writes, can be
replicated into a mirrored database.

## Use

```python
from shape.builtins.sinks import FabricMirrorSink

sink = FabricMirrorSink()
# initial load: every row is an insert (marker 0)
sink.write("landing/", "customer", batches, key_columns=["customer_id"])
# a later change file: per-row markers from a _shape_delta_type column
sink.write("landing/", "customer", delta_batches, key_columns=["customer_id"])
# OneLake
sink.write(
    "abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<db>.MirroredDatabase/Files/LandingZone",
    "customer", batches, key_columns=["customer_id"],
)
```

`shape.sinks` name: `fabric-mirror`. One call writes one file for one table.

| Option | Meaning |
|---|---|
| `key_columns` | the table's unique key, declared in `_metadata.json`. Required for any marker other than insert |
| `format` | `parquet` (default) or `csv` |
| `row_marker` | `insert` (default), `update`, `delete`, `upsert`, or 0, 1, 2, 4 |
| `schema_name` | writes into `<schema_name>.schema/<table>/` |
| `schema` | the Arrow schema, for a call with no batches (writes `_metadata.json` only) |
| `filesystem` | an fsspec filesystem for `abfss://` (default: adlfs, with the `[azure]` extra) |

`abfss://` authentication is the Azure source's: `token`, `credential`, `account_key`,
`sas_token`, `connection_string`, `account_name`, then Fabric notebook credentials, then
`DefaultAzureCredential` (`shape.builtins.sources._azure_auth`). The `--auth` modes and credential
references of [fabric-auth](plugins/fabric-auth.md) belong to the Fabric plugin's writers and are
not applied by this sink: pass `credential` (an azure-identity credential) or `token` for a
specific identity.

A `_shape_delta_type` column (`INSERT`, `UPDATE`, `DELETE`, `UPSERT`) sets the marker of each row
and is not written; `_shape_delta_timestamp` stays as a data column. A column named
`__rowMarker__` in the data is validated, moved last and used as is. Updated and deleted rows are
written with every column (the service only needs the key columns of a delete).

## Format rules and where they come from

Verified on 2026-10-03 against Microsoft Learn,
[Open Mirroring Landing Zone Requirements and Formats](https://learn.microsoft.com/en-us/fabric/mirroring/open-mirroring-landing-zone-format)
(`ms.date` 2025-09-09, page updated 2026-02-03):

| Rule | Source text | Where implemented |
|---|---|---|
| Parquet or delimited text (CSV); uncompressed or Snappy, GZIP, ZSTD | "Parquet or delimited text formats" | `format`; Parquet is Snappy |
| `__rowMarker__` is the last column | "The `__rowMarker__` column needs to be the final column in the list" | `_with_markers` |
| 0 insert, 1 update, 2 delete, 4 upsert | the `__rowMarker__` table | `MARKERS` |
| Updated rows carry the full row | "Updated rows must contain the full row data, with all columns" | rows are written whole |
| Row order is the order changes apply | "All the logs in the file should be in natural order" | batches are written in order |
| 20-digit names, monotonically increasing and continuous; the service deletes processed files but leaves the last | "File name is 20 digits, like `00000000000000000001.parquet`" | `_next_sequence` |
| `_metadata.json` with `keyColumns`; key columns cannot change once added; without them updates and deletes are impossible | "Metadata file in the landing zone" | `FabricMirrorSink.write` |
| Delimited text: header row, `_metadata.json` with `FileExtension`, `FirstRowAsHeader: true`, column types | "Delimited text requirements" | `format="csv"` |
| Schema folders `<name>.schema/<table>` | "Schema" | `schema_name` |

**Not stated on Microsoft Learn.** Two rules in the work package are not in the pages read (the
page above, the open mirroring FAQ and tutorial, all checked on 2026-10-03), so they are this
sink's own conservative practice, not documented service behaviour:

* **Immutability.** The sink never appends to or rewrites a published file, and raises
  `FileExistsError` instead of overwriting one that appeared meanwhile. (Learn says the service
  itself deletes processed files, so a publisher must not rely on old files staying.)
* **Publish by rename.** The sink writes `_<20 digits>.<ext>` and renames it to the final name,
  so a file with a valid data name only ever appears whole. The underscore name does not match
  the 20-digit pattern, which is why a reader that follows the documented naming ignores it; that
  is an inference, not a documented guarantee. A failed write removes the underscore file.

Other choices the page leaves open: a table written with no `key_columns` (insert only) gets no
`_metadata.json`, since the page says the file only matters for updates and deletes; `keyColumns`
is written in the lower camel case of the page's main example (its delimited-text example spells
it `KeyColumns`); a CSV `_metadata.json` declares `FileFormat` `CSV`, `FileExtension` `csv`,
`FirstRowAsHeader` `true` and a `SchemaDefinition` from the Arrow schema (types the page does not
list, such as decimals, are an error in CSV: use Parquet); `__rowMarker__` is a non-null 32-bit
integer. Tables are written to one folder format: a `.csv` file in a Parquet table folder is an
error.

## Not covered

* Concurrent publishers to one table: sequence numbers come from a listing, and only the
  rename guard (`FileExistsError`) protects against a race.
* `_partnerEvents.json`, non-sequential file detection (`fileDetectionStrategy`) and
  `isUpsertDefaultRowMarker`.
* Tests against a real OneLake landing zone run only when `SHAPE_FABRIC_MIRROR_LANDING_ZONE` is
  set (marker `live`); the recorded-interaction tests in `tests/io/test_fabric_mirror.py` run
  everywhere.
