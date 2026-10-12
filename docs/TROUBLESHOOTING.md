# Troubleshooting by symptom

Use the symptom and exit code to find the next check.

Status: available.

## My command exits nonzero

For profile, check and diff, exit 0 means the operation passes. A check or gated diff uses 1
for a failed requirement or reported drift. Exit 2 means bad input, a missing dependency or
unavailable required evidence. Model commands have their own codes; use [Exit codes](EXIT_CODES.md)
for the command you run. Read stderr as well as stdout. The tutorials show both streams together.

## I need a diagnosis

`shape doctor` reports environment and optional dependency checks. Cloud options can probe
endpoints or authentication; a default local check is different from a cloud check.
Check the installed core version and the plugin entry points before changing your source URI.

### Owner-run connectivity receipts

On 2026-10-11, the Owner ran Shape 0.9.1 from PyPI with the rust kernel on a clean Windows 11 VM, Python 3.12.10:

```text
shape doctor --fabric onelake://WORKSPACE/ITEM --auth cli
shape doctor --broker kafka://HOST:9092/TOPIC
shape doctor --broker eventhubs://NAMESPACE.servicebus.windows.net/HUB
```

The Fabric run passed DNS, TCP, TLS, sign-in and workspace/item checks. Kafka passed DNS, TCP and its metadata authentication request. Event Hubs passed DNS, TCP, TLS and sign-in. Each returned `Result: OK`, exit 0. See the [Owner-run doctor receipts](evidence/doctor-additional-environments.md) for the selected checks and environment details.

## Generation writes no files

A profile generation command without a format produces a summary and writes nothing.
Choose a format and output directory. [The generation tutorial](tutorials/05-generate-profile.md)
uses the Python API and explicitly writes the result. The [CLI reference](https://docs.shapedata.ai/reference/cli/)
shows the current writer options.

## Delta reports a reader feature error

Shape detects deletion vectors and column mapping and selects the optional DuckDB reader.
Install the `[azure,delta-fallback]` extras before running this example: `[azure]`
provides the cloud-source dependencies and `[delta-fallback]` provides the optional
DuckDB reader. A local Delta table is passed as its directory path; `delta://` is
not a supported local source URI. The DuckDB extension may download
on first use; that is a network call. The workflow installs the real DuckDB extension and profiles the committed deletion-vector fixture. A blocked download fails this example; it is not evidence of a working fallback.

<!-- owner-example: delta-fallback -->

```bash {.runnable-owner}
python -m pip --disable-pip-version-check install --quiet 'sqllocks-shape[azure,delta-fallback]'
python - <<'PYTHON'
import duckdb
con = duckdb.connect()
con.execute("INSTALL delta")
con.execute("LOAD delta")
print("DuckDB delta extension installed and loaded")
con.close()
PYTHON
shape profile "$SHAPE_DOCS_REPO/tests/fixtures/delta_fallback/dv" -o deletion-vectors.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    DuckDB delta extension installed and loaded
    shape: note: dv: the Delta table uses reader feature(s) deletionVectors that deltalake cannot read; reading it with DuckDB delta_scan instead
    {"provenance": {"as_of": null, "fallback_reason": "dv: the Delta table uses reader feature(s) deletionVectors that deltalake cannot read; reading it with DuckDB delta_scan instead", "format": "delta", "reader": "duckdb", "reader_features": ["deletionVectors"], "timestamp": "2026-10-03T03:48:46.451000+00:00", "version": 2}, "shape_content_id": "138321eea84d2b15e1bcdc49b2f67c96b6bf78c93d75b5c59d205274aa58549a", "written": "deletion-vectors.json"}
    ```

Evidence: [run 38273696299, ubuntu-latest, `7e0aa725e228`](evidence/transcripts/docs-transcripts-38273696299-7e0aa725e228-ubuntu-latest-delta-fallback.json).
For version or as-of reads, confirm that the historical files remain available.

### Local fallback receipt

A [fresh local installation and fallback receipt](evidence/delta-extension-fallback.md) on 2026-10-12 UTC records `INSTALL delta` and `LOAD delta`, then the real commands:

```bash
shape profile tests/fixtures/delta_fallback/dv -o dv.shape
shape cat dv.shape
```

With Shape 0.9.1, the reader reported `DuckDB delta_scan` for `deletionVectors`; the artifact reported `row_count: 90`. Installation, profile and cat each exited 0. The receipt retains the unsigned-origin warning. This uses a committed local fixture and published dependency software, without a live data account.

## Safe capture is refused

A full capture or recorded unsafe marker fails safe validation. The scan also looks for
retained values and personal-data patterns. Do not suppress the refusal simply to commit a file.
Review the classification and capture policy. Multi-table dataset profiles have a known 0.9.1
validator failure; see [Known limitations](KNOWN_LIMITATIONS.md). Single-table validation is
shown in [Your first profile](tutorials/01-first-profile.md).

## A folder is rejected or has the wrong tables

Without dataset mode, a folder is one table whose files are partitions. CLI profiling requires
matching columns, in any order. Dataset mode treats files as named tables. Do not mix unrelated
files into one partition folder. A `tables` contract requires a dataset profile; a single table
cannot satisfy that input shape.

## My model query rejects a .shape file

The extension does not identify the payload kind. Queries operate on models, not profiles.
Use the saved profile's report or inspection interface; read [Models](MODELS.md).

## Related

[FAQ](FAQ.md) · [Known limitations](KNOWN_LIMITATIONS.md) · [Exit codes](EXIT_CODES.md)
