# Fingerprint

Status: experimental.

Once generated data leaves Shape it carries almost nothing about where it came from. A fingerprint
is a small JSON document stored inside the file (a Parquet footer) or the table (a Delta table
property) that says the data is synthetic, names the table, the run and the profile that produced
it, and carries a digest of the table's content so a changed value is found. It can be signed with
Ed25519 (extra `[sign]`).

A fingerprint is evidence of origin and integrity. It is not a watermark: any tool that rewrites the
file can drop it (see [what keeps it](#what-keeps-it)), and an unsigned one can be written by anyone.

## Format

`shape-fingerprint`, version 1. Stored as the Parquet footer key-value metadata key
`shape.fingerprint` and as the Delta table property `shape.fingerprint`:

```json
{
  "format": "shape-fingerprint",
  "version": 1,
  "synthetic": true,
  "table": "order",
  "table_id": "sha256:...",
  "dataset_id": "sha256:...",
  "profile_content_id": "...",
  "reproducibility": {"schema_version": 1, "profile_version": 1, "seed": 7, "scale": "small",
                      "shape_version": "0.9.0", "kernel": "rust", "platform": "linux-x86_64"},
  "key_id": "56475aa75463474c",
  "signature": "base64..."
}
```

| field | meaning |
|---|---|
| `format`, `version` | `shape-fingerprint` and an integer. A reader refuses a version newer than it knows (exit 2) and ignores fields it does not know. |
| `synthetic` | always `true` |
| `table` | the table's name in the run |
| `table_id` | `shape.repro.dataset_id` of this table alone ([REPRODUCIBILITY.md](REPRODUCIBILITY.md)): it changes with any value, type or name, and not with row or column order |
| `dataset_id` | the id of the whole run, from the run manifest; `null` when the sink wrote it without a run (`fingerprint=True` on a sink alone) |
| `profile_content_id` | the `shape_content_id` of the profile the run was generated from (`--profile`), or `null` |
| `reproducibility` | the run's reproducibility tuple, from the manifest |
| `key_id`, `signature` | `null` when unsigned. Otherwise the key's short id and an Ed25519 signature, base64, over the canonical form (`shape.artifact.canonical`) of the document with `signature` set to `null`, under its own domain tag so it is valid for nothing else |

The reproducibility tuple must hold no floating-point numbers (the canonical form rejects them).

## Commands

<!-- example: 1 -->

Syntax reference. Replace the named arguments with your inputs.

```bash
shape fingerprint embed FILE|DELTA_DIR --run MANIFEST.json [--profile P.shape] [--key PRIVATE_KEY]
shape fingerprint show FILE|DELTA_DIR
shape fingerprint verify FILE|DELTA_DIR [--public-key KEY]
shape generate SCHEMA -f parquet|delta -o DIR --fingerprint
```


`embed` takes `table`, `dataset_id` and the reproducibility tuple from the run manifest
(`shape pack run` writes `<run_id>_manifest.json`): the table is the manifest table whose
`file_paths` name the file, or whose name is the file's stem (the Delta directory's name). It
recomputes `table_id` from the data. A Parquet file is rewritten atomically (row groups and
compression are kept; a failed write leaves the original); a Delta table gets one new commit.
Keys are the same as for `shape sign` (a file, `-`, `env://VAR`, `file://PATH`, `kv://...`; an
encrypted key asks for its passphrase, see [SIGNING.md](SIGNING.md)).

`verify` recomputes `table_id` from the data and compares it with the fingerprint, and with
`--public-key` checks the signature. Without a key a signed fingerprint is reported as "signature
not checked"; with a key an unsigned one fails.

| exit | meaning |
|---|---|
| 0 | valid |
| 1 | the digest does not match the data, or the signature is invalid, by another key, or missing when a key was given |
| 2 | no fingerprint, a newer `version`, or bad input (missing or unreadable file, not Parquet or Delta, a manifest without `dataset_id`, a table the manifest does not know, signing without the `[sign]` extra) |

`--fingerprint` on `shape generate` (Parquet and Delta only; not with `--to`, `--scale-mode` or the
landing options) generates the tables whole, computes the run's `dataset_id`, and writes each table
with its fingerprint. It is unsigned: sign afterwards with `embed --key`. The Parquet and Delta
sinks take the same option (`fingerprint=True`, or a dict with `dataset_id`, `reproducibility` and
`profile_content_id`); it does not combine with rolling Parquet files, Delta micro-batch commits or
Delta `mode="append"` (the digest covers the whole table), and writes local Delta tables only.

## How it is stored in Delta

`deltalake` refuses to set a table property it does not know, so the property is added by a
`metaData` action written to the Delta log directly: the table's current metadata with one more
configuration entry, in a new commit (`SET TBLPROPERTIES`) created exclusively, so a concurrent
writer is never overwritten. The table's data, schema and protocol are unchanged, and `deltalake`
and DuckDB read the table as before. Local tables only.

## What keeps it

A tool that reads a fingerprinted file and writes a new one keeps the fingerprint only if it
copies the file's key-value metadata. Each row below is a test (`tests/w5_10/
test_fingerprint_preservation.py`) that rewrites a fingerprinted file or table with the tool and
checks whether `shape.fingerprint` is still there. The table is generated by the test run: the test
fails when it differs from this page, and `SHAPE_UPDATE_DOCS=1` rewrites it. "survives" means the
entry is present; whether it still verifies depends on whether the tool changed the data
(`verify` recomputes the digest). "not run" means the tool or its extension was not available
where the table was generated.

<!-- preservation-table:begin (generated by tests/w5_10/test_fingerprint_preservation.py) -->
| Tool | Operation | Input | `shape.fingerprint` |
|---|---|---|---|
| pyarrow | `pq.read_table` then `pq.write_table` | Parquet file | survives |
| pyarrow | `pq.read_table(columns=[...])` then `pq.write_table` | Parquet file | survives |
| pyarrow | `ParquetWriter` with a schema built from the fields | Parquet file | lost |
| pyarrow | `pyarrow.dataset.write_dataset` | Parquet file | survives |
| pandas | `to_pandas` then `Table.from_pandas` and `pq.write_table` | Parquet file | lost |
| DuckDB | `COPY (SELECT * FROM read_parquet(...)) TO ...` | Parquet file | lost |
| DuckDB | the same `COPY` with `KV_METADATA` | Parquet file | survives |
| DuckDB | `parquet_kv_metadata(...)` (read only) | Parquet file | survives |
| deltalake | `write_deltalake` of the Parquet file's rows | Parquet file | lost |
| deltalake | `write_deltalake(mode="append")` | Delta table | survives |
| deltalake | `write_deltalake(mode="overwrite")` | Delta table | survives |
| deltalake | `optimize.compact()` | Delta table | survives |
| deltalake | `to_pyarrow_table` then `write_deltalake` to a new table | Delta table | lost |
| file copy | `shutil.copytree` of the table directory | Delta table | survives |
| DuckDB | `COPY (SELECT * FROM delta_scan(...)) TO ...` (needs the `delta` extension) | Delta table | lost |
<!-- preservation-table:end -->

Only local tools are tested; Fabric-side preservation is not.
