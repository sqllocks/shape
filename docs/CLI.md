# The `shape` command line

Run it as `shape ...`, or as `python -m shape ...` when the `shape` script is not on `PATH` (an
environment that is not activated, a CI step, a notebook). Both start the same program.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | a check failed, drift was found, a signature or leak scan failed |
| 2 | bad input: a missing or unreadable file, the wrong kind of file, a bad argument |
| 3 and above | a command's own verdict (a certificate below its threshold, a failed contract, an incompatible change); each command's `--help` says which |
| 141 | standard output was closed before the command finished (`shape ... \| head`); nothing is printed |

## Errors

An expected error is one line on stderr and exit code 2:

```
$ shape registry reg checkout orders nope
shape: error: orders@nope is not recorded in the registry
```

The same wording is used for the same problem everywhere (`shape: error: file not found: PATH`
for a missing file). A bug in Shape is not hidden: it keeps its Python traceback. To see the
traceback of an expected error as well, put `--debug` before the command, or set `SHAPE_DEBUG=1`:

```bash
shape --debug registry reg checkout orders nope
SHAPE_DEBUG=1 shape check missing.shape contract.json
```

## What each command expects

- `shape profile SRC -o OUT.shape` reads CSV, Parquet, JSONL, a folder or glob of them, or a Delta
  table, and writes a **profile**. A table with 0 rows prints `shape: warning: ... has 0 rows`
  (the profile is still written); `--fail-on-empty` exits 2 instead and writes nothing. A Delta
  table with deletion vectors or column mapping is read with DuckDB (extra `delta-fallback`) and
  says so on stderr; see the README.
- `shape check`, `shape diff`, `shape plan`, `shape generate --from` read profiles.
  `shape diff` also compares two captures (`shape capture ... -o X.json`): it prints the changes,
  writes them with `--json OUT`, and exits 1 under `--fail-on-drift` when there is any. The
  thresholds, `--ignore`, `--only` and `--policy` are the profile engine's and are refused there.
- `shape inspect ARTIFACT.shape` prints what an artifact holds, a profile or a model. `shape show`
  is an alias of `shape inspect`.
- `shape capture SRC` reads everything `shape profile` reads (CSV, Parquet, JSONL, a folder or
  glob, a Delta table with `--version N` or `--as-of TIMESTAMP`, an `abfss://` source) and writes a
  **model** (JSON, or a model `.shape` with `-o OUT.shape`), which `shape query`,
  `shape compatibility` and `shape plan` read. `--dataset` captures a folder of one file per table
  as a model with one table each. The model has the same content whatever the file format; the
  one difference is what the format itself carries: a CSV holds text and numbers only, so a date
  column is text there and a timestamp in Parquet (both are captured as text values). It is not a
  second way to profile; use `shape profile` for that.
- `shape design INPUT.json` reads a **design input** and writes DDL for a 3NF, star or snowflake
  schema, after linting it; `shape design DATA.csv --from-data` builds a design input from data.
  See [DESIGN.md](DESIGN.md).
- `shape compatibility BEFORE AFTER` compares two Shape **models** (made by `shape capture` or
  written as model JSON), not profiles. Compare two profiles with `shape diff`. To check a feed
  for schema changes, capture it each day and compare with the baseline; a renamed or dropped
  column is reported as `removed` and a changed type as `type_changed` (exit 5):

  ```bash
  shape capture orders.parquet -o orders-base.shape
  shape capture orders-today.parquet -o orders-today.shape
  shape compatibility orders-base.shape orders-today.shape --mode backward
  ```
- `shape fidelity REFERENCE SYNTHETIC` compares data with data: CSV, Parquet, JSONL or a folder
  of one file per table. Given a captured evidence document (`REFERENCE.json`) it certifies the
  CSV against it instead. A profile is not a fidelity reference; profile the synthetic data and
  run `shape diff`.
- A generation schema file (what `shape from-ddl` and `shape learn` write) is JSON, or YAML when
  it is named `.yaml` or `.yml` (needs PyYAML). Every command that takes one (`generate`,
  `describe`, `presets`, `emit`, `stream`, `continue`, `time-travel`, `chaos`, `generate-drift`,
  `validate`) reads both.
- `shape proposals propose|list|decide` keeps the answers to what a profile cannot settle alone
  (foreign keys, personal data, meaning) in a decision file; `shape generate --from` and
  `shape plan` take it with `--decisions`. See [PROPOSALS.md](PROPOSALS.md).

- `shape bridge` serves Shape's commands as a versioned JSON protocol on standard input and output
  (one request and one response per line; `--once` for a single request; `--jobs-dir DIR` for the
  job files). `shape bridge schema --out DIR` writes its JSON Schemas and `--check DIR` verifies a
  directory against them. See [`BRIDGE.md`](BRIDGE.md).

## `shape doctor`

```
$ shape doctor
Shape 0.9.0
  python    3.12.10  (Linux-6.8.0-x86_64-with-glibc2.39)
  kernel    rust  (compiled)

Required
  OK      numpy         2.4.6
  OK      pyarrow       25.0.1

Optional
  OK      cryptography  50.0.2
  missing yaml          needed for YAML generation schemas and scenario packs
  ...
Result: OK
```

It shows the Shape version, the Python, which kernel is in use (`rust` is the compiled kernel;
`python` is the pure-Python fallback, selected with `SHAPE_KERNEL`), and each package with what it
is needed for. Exit code 0 unless a required package is missing (then 1); a missing optional
package never fails it. `shape doctor --json` prints the same facts as one JSON object. For
plugins, run `shape plugins doctor`.

## Content ids across source formats

`shape_content_id` is the hash of the whole profile, including the typed minimum and maximum of
each column. The same rows read from CSV and from Parquet can therefore have different ids, for
example when a date column's minimum is a string tag in one and a date in the other, although
`shape diff` finds no drift between them. Compare profiles with `shape diff`, not by id, when the
sources differ in format.

## `shape demo`

`shape demo init|list|run|preflight|cleanup|status|notebook|report` runs scenarios for talks, clients and
workshops and cleans up after them; see `docs/DEMO.md`. Exit 0 done, 1 a run failed, a cleanup could not
remove something or a preflight check failed, 2 bad input.

## Registries

`shape registry` (content-addressed artifacts) and `shape profile registry` (named profiles) are
different stores. See `docs/REGISTRY.md` and `docs/PROFILE_REGISTRY.md`.
