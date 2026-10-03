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

## `shape migrate`

`shape migrate SRC DST` (also installed as `shape-migrate`) writes a migrated copy of a persisted
file, offline: a `.shape` artifact, or a safe profile, model, run manifest, contract or any other
JSON kind in [the state and compatibility policy](specs/STATE_AND_COMPATIBILITY.md).

```bash
shape migrate old.shape new.shape --dry-run   # print the plan, write nothing
shape migrate old.shape new.shape             # new file + new.shape.receipt.json; old.shape kept
shape migrate old.shape new.shape --verify old.pub --sign-key release.key   # a signed source
```

It never rewrites in place or overwrites a file, records `migrated_from` and `source_content_id`,
refuses a downgrade (`--to N` below the file's version), reads its result back and checks the
content id before publishing it, and migrates a file that is already current to nothing. `--kind
KIND` names a JSON file that is not recognised by itself. Exit codes: 0 migrated, no-op or dry run;
1 the source failed `--verify`; 2 refused or bad input. Strict reading of every format (`SHAPE_STRICT_FORMATS=1`) is described in
the policy.

## What each command expects

- `shape profile SRC -o OUT.shape` reads CSV, Parquet, JSONL, a folder or glob of them, or a Delta
  table, and writes a **profile**. A table with 0 rows prints `shape: warning: ... has 0 rows`
  (the profile is still written); `--fail-on-empty` exits 2 instead and writes nothing. A Delta
  table with deletion vectors or column mapping is read with DuckDB (extra `delta-fallback`) and
  says so on stderr; see the README.
- `shape profile SRC -o OUT.shape --sketches` also keeps the mergeable sketch state in the file;
  `shape profile merge A.shape B.shape ... -o OUT.shape [--name N] [--exact-only]` combines profiles
  of partitions or days into the profile of their union, without reading the data again. Exit 2
  when an input lacks the sketch state the statistics need (and `--exact-only` is not given), or
  the profiles do not share their columns. See `docs/PROFILE_MERGE.md`.
- `shape check`, `shape diff`, `shape plan`, `shape generate --from` read profiles. `shape check
  --data DATA` also checks the contract's `timeseries` and `reconcile` rules against data
  (`docs/VERIFY.md`).
- `shape explain DIFF.json` explains a `shape diff --json` or `shape drift` result in plain
  English, deterministically (`docs/EXPLAIN.md`).
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
- `shape contract emit CONTRACT.json --to ddl|jsonschema|pandera|gx` writes a contract as database
  DDL, a JSON Schema, a pandera schema or a Great Expectations suite, listing what the target cannot
  say (`--strict` fails on it). See [CONTRACT_EMIT.md](CONTRACT_EMIT.md).
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
  CSV against it instead. A `.shape` file (a profile or a model) is not data and is refused with a one-line message; profile the synthetic data and
  run `shape diff`.
- `shape proposals propose|list|decide|contract` keeps the answers to what a profile cannot settle
  alone (foreign keys, personal data, meaning) in a decision file; `shape generate --from` and
  `shape plan` take it with `--decisions`. `propose --kinds rule` (one or several profiles) proposes
  contract rules with evidence and a confidence, `list --status stale` shows accepted rules whose
  evidence no longer holds after a re-profile, and `contract -d DECISIONS.json -o CONTRACT.json
  [--merge EXISTING.json]` writes the accepted rules as a contract `shape check` reads (exit 2 on a
  conflict with a rule in the file merged into). See [PROPOSALS.md](PROPOSALS.md#rules).

- `shape fingerprint embed|show|verify` writes, reads and checks a signed statement inside a Parquet
  file or Delta table that the data is synthetic and which run made it; `shape generate
  --fingerprint` writes it at generation time (exit 0 valid, 1 digest or signature mismatch, 2 none,
  a newer version or bad input). See [FINGERPRINT.md](FINGERPRINT.md).
- `shape share-bundle create|verify` checks generated data against its source (memorization gate and
  a top-values check) and writes a zip with a signed attestation, or, for `verify`, recomputes its
  dataset id and checks it (`create`: 1 a check failed, nothing written; `verify`: 1 tampered or
  failing, 2 malformed). Evidence that the checks passed, not a privacy guarantee. See
  [SHARE_BUNDLE.md](SHARE_BUNDLE.md).
- `shape skew-rehearsal PROFILE.shape --schema SCHEMA --scale S -o DIR` generates at scale with the
  profile's key skew and reports, per column, the profile's top share against the generated one
  (exit 1 outside the tolerance, 2 no frequency data for a requested column). See
  [SCALE.md](SCALE.md#skew-rehearsal).

- `shape bridge` serves Shape's commands as a versioned JSON protocol on standard input and output
  (one request and one response per line; `--once` for a single request; `--jobs-dir DIR` for the
  job files). `shape bridge schema --out DIR` writes its JSON Schemas and `--check DIR` verifies a
  directory against them. See [`BRIDGE.md`](BRIDGE.md).

- `shape resolve run FILE` finds duplicate entities in a CSV, Parquet or JSONL file and writes
  golden records; `shape resolve synth FILE -o OUT` plants seeded duplicates and writes the true
  clusters. See [RESOLVE.md](RESOLVE.md). Bad options or an unreadable file exit 2.

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

### Connectivity checks

Three options add checks that touch the network or a cloud sign-in. Each is off unless you ask for
it, so plain `shape doctor` stays offline. Every check is one line with a status, `PASS`, `WARN` or
`FAIL`, and a `Next:` step for a warning or failure. A warning never changes the exit code; a
failure exits 1. A malformed target is an input error (exit 2).

```
shape doctor --fabric onelake://WORKSPACE/ITEM [--auth cli|msi|spn|device-code|fabric ...]
shape doctor --broker kafka://host:9092[,host2:9092]/topic
shape doctor --broker eventhubs://NAMESPACE/HUB [--auth ...]
shape doctor --delta-table PATH
```

| Option | Checks |
|---|---|
| `--fabric` | `fabric.onelake`: DNS, TCP and TLS to the OneLake endpoint; `fabric.auth`: sign-in with the `--auth` mode (default `cli`); `fabric.item`: the workspace and item exist and you may read them (404 means not found, 403 means no permission) |
| `--broker` (repeatable) | `broker.dns`, `broker.tcp`, `broker.tls` (Event Hubs, port 5671) and `broker.auth` (Event Hubs: a token from `--auth`; Kafka: a metadata request through `confluent-kafka` when installed, otherwise a warning). `--no-auth-check` skips the sign-in |
| `--delta-table` | `delta.limits`: deletion vectors and column mapping, which delta-rs cannot read in a Python notebook; the fix names Spark or a table rewrite |

**IPv6.** When a broker name resolves to an IPv6 address that does not answer while an IPv4
address does, `broker.tcp` is a warning that names the address (a client that tries IPv6 first
stalls); when nothing answers it is a failure that says IPv6 may be the cause.

**Sign-in.** `--auth`, `--tenant-id`, `--client-id`, `--client-secret`, `--sql-user` and
`--sql-password` work as in `shape emit` (see the credential references in
[SECURITY_SPECIFICATION.md](SECURITY_SPECIFICATION.md)); a secret is always a reference such as
`env://NAME`. A token is used for one request and never printed. `--auth` needs the `shape-fabric`
plugin; without it the sign-in line fails and says so. `--timeout SECONDS` bounds each step
(default 5).

**`--json`** prints every check as an entry of `checks`, each with `id`, `status`, `message` and
`next`, next to the earlier keys; `ok` is false when any check failed.

Live variants of these checks are marked `live` in the test suite and need network access.

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

## The project file

`shape init` scaffolds a project and `shape project validate` checks its `shape.yml`; `profile`,
`diff`, `check` and `verify` read it when it is present, and their flags override it
(`--project FILE`, `--no-project`, `--source NAME`, `diff --baseline-date`). See
`docs/PROJECT.md`.

## `shape dictionary`

`shape dictionary PROFILE.shape [--project FILE | --no-project] [--source NAME] [--format md|html|json] [--examples] -o OUT`
writes a data dictionary (one entry per table and column) from a profile and `shape.yml`. Example
and top values need `--examples` and are never written for a column classified `CONFIDENTIAL` or
higher. Exit 0 done, 2 a missing or unreadable profile, an invalid `shape.yml` or a source that is
not in the project. See `docs/DICTIONARY.md`.

## Editor support

The VS Code extension in `editors/vscode/` completes and validates `shape.yml`, offers snippets,
and shows a `.shape` file as the text `shape cat FILE` prints (read-only), with **Shape: Compare
with Git HEAD** for a diff. It needs `shape` 0.9.0 or newer (the `shape.path` setting, else `PATH`).
See `editors/vscode/README.md`.

## Registries

`shape registry` (content-addressed artifacts) and `shape profile registry` (named profiles) are
different stores. See `docs/REGISTRY.md` and `docs/PROFILE_REGISTRY.md`.
