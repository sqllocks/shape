# The `shape` command line

Status: experimental.

This page documents commands currently listed as stable in [CLI_STABILITY.md](CLI_STABILITY.md).
The page itself has not been promoted. See [experimental commands](CLI_EXPERIMENTAL.md)
for the remaining command documentation.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" CLI
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for CLI
    ```


Run it as `shape ...`, or as `python -m shape ...` when the `shape` script is not on `PATH` (an
environment that is not activated, a CI step, a notebook). Both start the same program.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | a check failed, drift was found, a signature or leak scan failed, or constraints did not hold after `--sql-constraints disable` loaded the rows (the error names each one) |
| 2 | bad input: a missing or unreadable file, the wrong kind of file, a bad argument |
| 3 and above | a command's own verdict (a certificate below its threshold, a failed contract, an incompatible change); each command's `--help` says which |
| 141 | standard output was closed before the command finished (`shape ... \| head`); nothing is printed |

The classes, and what is stable about each command, are promised in
[CLI_STABILITY.md](CLI_STABILITY.md).

Every command's codes are listed in `docs/EXIT_CODES.md` (generated from `shape.cli.exitcodes`) and
at the end of its `--help`.

## JSON output and dry runs

Core commands support `--json`; plugin commands support it only when their own
`--help` lists the option. The core wrapper prints one JSON document on standard output
(`format: "shape-result"`, `version: 1`, `command`, `exit_code`, and the command's result
keys); text goes to standard error. Where `--json` takes a file (`profile`, `diff`,
`check`), it keeps that meaning, and `--json -` writes to standard output.

Core writing commands expose `--dry-run` where their parser supports it. It previews
writes or sends without performing them, and exits 0, or 2 for invalid input. Plugin
commands have their own dry-run behavior; consult their `--help`. These flags are not
universal promises for plugin commands. `--junit FILE` and `--sarif FILE` on `diff`,
`check`, `verify` and `profile validate --safe` write CI reports. See [CI.md](CI.md).

## Errors

An expected error is one line on stderr and exit code 2:

[Run this example](#local-example-0).


The same wording is used for the same problem everywhere (`shape: error: file not found: PATH`
for a missing file). A bug in Shape is not hidden: it keeps its Python traceback. To see the
traceback of an expected error as well, put `--debug` before the command, or set `SHAPE_DEBUG=1`:

[Run this example](#local-example-1).


## What each command expects

- `shape profile SRC -o OUT.shape` reads CSV, Parquet, JSONL, a folder or glob of them, or a Delta
  table, and writes a **profile**. The profile is the **safe capture** by default (`--capture
  safe`): a sensitive column keeps statistics and formats only, and a category is kept only if
  every released category has at least `k` rows (`--k N`, default 5; `--column-k COLUMN=N`;
  `--classify COLUMN=LEVEL`, where `CONFIDENTIAL` or higher makes a column sensitive). The `--json`
  summary and the `--html` report are redacted the same way. `--capture full` keeps real values,
  says so in the artifact and prints `shape: warning: --capture full keeps real values in OUT; do
  not commit or share it` once; `--k`, `--column-k` and `--classify` are errors with it (exit 2).
  `shape profile validate --safe OUT.shape` exits 0 for the default and 1 for a full capture.
  `shape profile registry save` takes the same options. See `docs/PRIVACY_MODEL.md`. A table with 0 rows prints `shape: warning: ... has 0 rows`
  (the profile is still written); `--fail-on-empty` exits 2 instead and writes nothing. A Delta
  table with deletion vectors or column mapping is read with DuckDB (extra `delta-fallback`) and
  says so on stderr; see the README. `--time-column COL` names the date or timestamp column that
  each numeric column's seasonality is measured against (default: the table's only one; with
  `--univariate`; `docs/PROFILING_NOTES.md`).
- `shape profile SRC -o OUT.shape --sketches --capture full` also keeps the mergeable sketch state
  in the file (it holds real values: `--sketches` without `--capture full` exits 2);
  `shape profile merge A.shape B.shape ... -o OUT.shape [--name N] [--exact-only] [--capture
  safe|full]` combines profiles
  of partitions or days into the profile of their union, without reading the data again. Exit 2
  when an input lacks the sketch state the statistics need (and `--exact-only` is not given), or
  the profiles do not share their columns. See `docs/PROFILE_MERGE.md`.
- `shape profile` can profile a sample: `--sample N|P%` (N rows or P% of them; nothing is sampled
  without it), `--sample-method random|systematic|head` (default `random`) and `--sample-seed S`
  (default 42). The profile records the sample and its adequacy, and `shape profile` prints
  `shape: note: profiled a random sample of N of M rows (seed S)` to stderr (one line per table
  with `--dataset`); an invalid value (0 rows, a percentage outside 0 to 100, an unknown method)
  exits 2. `--decisions DECISIONS.json` reads the accepted `type` decisions of a decision file as
  `--types` (a `--types` file wins for a column both name). See
  [PROFILING_NOTES.md](PROFILING_NOTES.md#sampling).
- `shape check`, `shape diff`, `shape plan`, `shape generate --from` read profiles, safe captures
  included. `shape diff` lists a comparison a safe capture makes impossible under
  `not_evaluable` and does not count it as drift. `shape check` exits 0 when every rule passes, 1
  when a rule is violated, and 2 when a rule needs a value a safe capture left out (`shape: error:
  not evaluable: COLUMN was captured safe (statistics and formats only); re-profile with --capture
  full`) and nothing is violated. `shape generate --from` and `shape plan` generate such a column
  from its pattern and length distribution and mark it `approximate`.
  `shape check PROFILE CONTRACT [--json OUT] [--strict] [--enforce-learned]`: a rule may carry a
  `strength`: `hard` (the default) fails, `soft` is a warning, `learned` is a warning unless
  `--enforce-learned`, and `--strict` makes every broken rule fail; an unknown rule or strength
  is a malformed contract (exit 2; `docs/CONTRACTS.md`, "Rule strength").
  `shape check --data DATA` also checks the contract's `timeseries` and `reconcile` rules against
  data (`docs/VERIFY.md`).
  `shape diff` prints its result on standard output as one JSON line. On a terminal, a list of
  more than 20 values (a `new_categorical_values` change of a column with thousands of values) is
  cut to its first 20, with the number left out under the change's `values_omitted`, and stderr
  says how many were left out; a pipe or a redirect gets the complete result. `--json FILE`
  writes the complete result to FILE instead and prints nothing on standard output; `--json -`
  prints the complete `shape-result` document. Notes on how the two profiles
  were read (`notes`, a sampled profile against a full one) are also printed on stderr.
  `shape diff` also compares two captures (`shape capture ... -o X.json`): it prints the changes,
  or writes them with `--json OUT` instead, and exits 1 under `--fail-on-drift` when there is any. The
  thresholds, `--ignore`, `--only` and `--policy` are the profile engine's and are refused there.
  `shape diff` adds `notes` to its result when the two profiles were sampled differently, and
  compares `row_count_change` on the sources' row counts (`population_rows`) when both profiles
  state them.
- `shape inspect ARTIFACT.shape` prints what an artifact holds, a profile or a model (for a
  profile, its sampling record per table: how many rows it saw, or `not recorded` for a profile
  written by an older Shape). `shape show` is an alias of `shape inspect`.
  A numeric column of a profile made with `shape profile --univariate` carries the univariate
  depth fields (best family by BIC and the fitted candidates, zero share and zero inflation,
  heaping, Benford conformity, tail index, a Gaussian mixture and seasonality against the time
  column), so `shape show` prints them; `shape profile --json`
  and `--html` show them too (`docs/PROFILING_NOTES.md`). They are off by default: they add work
  for every numeric column. With `shape profile --multivariate`, the table's `joint` entry carries the multivariate depth entries
  (two-column determinants, multivariate outliers, PCA, cohorts and the mixed-type copula;
  `docs/JOINT.md`), which `shape show` prints and the `--html` report lists under the table.
- `shape generate --from PROFILE.shape --mixed-copula` links the numeric and categorical columns
  by the profile's mixed-type Gaussian copula (`joint.copula`): each column keeps exactly its
  generated values, only their order across rows changes. Without the flag the output is what it
  was. `shape plan PROFILE.shape --mixed-copula` lists the columns the copula orders (the flag is
  also `mixed_copula=True` of `shape.generate`). See [JOINT.md](JOINT.md).
- `shape compatibility BEFORE AFTER` compares two Shape **models** (made by `shape capture` or
  written as model JSON), not profiles. Compare two profiles with `shape diff`. To check a feed
  for schema changes, capture it each day and compare with the baseline; a renamed or dropped
  column is reported as `removed` and a changed type as `type_changed` (exit 5):

[Run this example](#local-example-3).

## Cloud and Fabric options

Three options reach cloud destinations and sources. Their help text, as `shape --help` prints it:

| Option | Where | Help text |
|---|---|---|
| `--source-config KEY=REF` | before or after any command; repeatable | Azure readers: `token`, `account_key`, `sas_token` or `connection_string` from `env://`, `file://` or `kv://` |
| `--lakehouse-schema NAME` | `shape generate` (with `--scale-mode fabric_spark`)  | fabric_spark: schema-enabled Lakehouse schema (for example `dbo`); omitted for classic Lakehouses |
| `--table-prefix P` | `shape generate` | prefix of the physical destination tables (Delta, database and Fabric sinks); with `--scale-mode` it needs `fabric_spark` |

`--source-config` takes only `env://`, `file://` or `kv://` references, never a literal secret, and
each key at most once. `--lakehouse-schema` must name a schema that already exists. See
[Fabric platform notes](FABRIC_PLATFORM.md) and [scaling](SCALE.md).

## Non-local targets

Commands that write to a non-local target (`generate --to`, `generate --scale-mode --sink`)
need `--yes`, or `SHAPE_CONFIRM_REMOTE=1` (exactly `1`), or `y` at the prompt on a terminal;
otherwise they exit 2. Paths, `file://`, `duckdb://`, `console` and
`localhost`/`127.0.0.1`/`::1` are local, and `--dry-run` needs no confirmation.
See [SINKS.md](SINKS.md). `shape generate -o` and `shape pack run` write
`_shape_provenance.json` beside the tables (`format: shape-provenance`, `version: 1`).

## `shape doctor`

[Run this example](#local-example-4).


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

<!-- example: 5 -->

This connectivity example uses the workflow's disposable Kafka broker. Fabric and Event Hubs checks require their own accounts; the option table below describes those checks.

<!-- owner-example: kafka-doctor -->

```bash {.runnable-owner}
shape doctor --broker kafka://localhost:9092/orders
```

??? info "Output (exit 0)"

    ```text {.expected}
    Shape 0.9.1
      python    3.12.15  (Linux-6.17.0-1022-azure-x86_64-with-glibc2.39)
      kernel    rust  (compiled)
    
    Required
      OK      numpy         2.5.3
      OK      pyarrow       25.0.1
    
    Optional
      OK      cryptography  50.0.2
      OK      yaml          6.0.3
      OK      pandas        3.0.6
      OK      scipy         1.18.1
      OK      deltalake     1.6.6
      OK      openpyxl      3.1.5
      OK      sklearn       1.9.1
      OK      tzdata        2026.3
    
    Checks
      PASS  broker.dns    localhost resolves to ::1, 127.0.0.1
      PASS  broker.tcp    connected to localhost:9092 (::1)
      PASS  broker.auth   the broker answered a metadata request
    
    Result: OK
    ```

Evidence: [run 38273696299, ubuntu-latest, `7e0aa725e228`](evidence/transcripts/docs-transcripts-38273696299-7e0aa725e228-ubuntu-latest-kafka-doctor.json).

Receipts: the [Owner-run Fabric, Kafka and Event Hubs checks](evidence/doctor-additional-environments.md) and the [replayed local Delta check](evidence/local-delta-doctor.md) cover all four command shapes above. The service checks ran on Windows on 2026-10-11; the local Delta check ran on Linux on 2026-10-12 UTC. Each returned exit 0; the Delta check reports `PASS delta.limits` on a readable table.


| Option | Checks |
|---|---|
| `--fabric` | `fabric.onelake`: DNS, TCP and TLS to the OneLake endpoint; `fabric.auth`: sign-in with the `--auth` mode (default `cli`); `fabric.item`: the workspace and item exist and you may read them (404 means not found, 403 means no permission) |
| `--broker` (repeatable) | `broker.dns`, `broker.tcp`, `broker.tls` (Event Hubs, port 5671) and `broker.auth` (Event Hubs: a token from `--auth`; Kafka: a metadata request through `confluent-kafka` when installed, otherwise a warning). `--no-auth-check` skips the sign-in |
| `--delta-table` | `delta.limits`: deletion vectors and column mapping, which delta-rs cannot read in a Python notebook; the fix names Spark or a table rewrite |

**IPv6.** When a broker name resolves to an IPv6 address that does not answer while an IPv4
address does, `broker.tcp` is a warning that names the address (a client that tries IPv6 first
stalls); when nothing answers it is a failure that says IPv6 may be the cause.

**Sign-in.** `--auth`, `--tenant-id`, `--client-id`, `--client-secret`, `--sql-user` and
`--sql-password` use credential references (see
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

## Editor support

The VS Code extension in `editors/vscode/` completes and validates `shape.yml`, offers snippets,
and shows a `.shape` file as the text `shape cat FILE` prints (read-only), with **Shape: Compare
with Git HEAD** for a diff. It needs `shape` 0.9.0 or newer (the `shape.path` setting, else `PATH`).
See `editors/vscode/README.md`.

## Change classes

`shape diff` classes every change as breaking, additive or cosmetic and prints a closing line
`bump: major (2 breaking, 1 additive, 4 cosmetic)` on stderr; the `--json` result and the line on
stdout carry `class`, `class_reason` and `semver` (`docs/DRIFT.md`, "Change classes").
`--fail-on breaking|additive|cosmetic` exits 1 when an unplanned change of that class or a stricter
one is reported (it may be combined with `--fail-on-drift`; the run fails when either fails), and
`--version-from X.Y.Z` adds `semver.next_version`. A class other than those three, or a version that
is not `X.Y.Z`, exits 2. A drift policy that names an unknown kind or class exits 2 too.

## Registries

`shape registry` (content-addressed artifacts) and `shape profile registry` (named profiles) are
different stores. See `docs/REGISTRY.md` and `docs/PROFILE_REGISTRY.md`.

`shape registry ROOT prune --before DATE [--name NAME ...] [--keep-last N] [--dry-run] [--json]`
removes the log entries committed before DATE and the objects nothing points at any more; every
version a ref or tag points at, and the newest N entries of each name, are kept. `--json` prints the
`shape-result` document with the report (`format` `shape-registry-prune`, `version` 1) as its
`payload`. Exit 0 pruned or dry run, 2 bad input (a bad
date, an unknown name, a bad `--keep-last`, a held `prune.lock`). See
[REGISTRY.md](REGISTRY.md#pruning).

## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape registry reg checkout orders nope
```

??? info "Output (exit 2)"

    ```text {.expected}
    shape: error: orders@nope is not recorded in the registry
    ```

This command exits nonzero. Read the diagnostic; this transcript shows a refusal or failed check, not a passing gate.

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape --debug registry reg checkout orders nope
SHAPE_DEBUG=1 shape check missing.shape contract.json
```

??? info "Output (exit 1)"

    ```text {.expected}
    Traceback (most recent call last):
      File "<checkout>/.venv/bin/shape", line 6, in <module>
        sys.exit(main())
                 ^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2281, in main
        code = _main(argv)
               ^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2318, in _main
        return errors.guarded(lambda: _logged(opts, argv))
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/errors.py", line 149, in guarded
        return fn()
               ^^^^
      File "<checkout>/src/shape/cli/main.py", line 2318, in <lambda>
        return errors.guarded(lambda: _logged(opts, argv))
                                      ^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2338, in _logged
        return _dispatch(argv)
               ^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2440, in _dispatch
        return _dispatch_command(argv)
               ^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2507, in _dispatch_command
        return machine.run(machine.command_path(used, a), a, lambda: _route(a))
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/machine.py", line 403, in run
        return fn()
               ^^^^
      File "<checkout>/src/shape/cli/main.py", line 2507, in <lambda>
        return machine.run(machine.command_path(used, a), a, lambda: _route(a))
                                                                     ^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2813, in _route
        return run_registry(a)
               ^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/registry.py", line 359, in run
        return _checkout(r, a)
               ^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/registry.py", line 284, in _checkout
        data = r.checkout(a.name, a.ref)
               ^^^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/registry/local.py", line 297, in checkout
        h = self.resolve(name, ref)
            ^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/registry/local.py", line 294, in resolve
        raise RegistryError(f"{name}@{ref} is not recorded in the registry")
    shape.registry.local.RegistryError: orders@nope is not recorded in the registry
    Traceback (most recent call last):
      File "<checkout>/.venv/bin/shape", line 6, in <module>
        sys.exit(main())
                 ^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2281, in main
        code = _main(argv)
               ^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2318, in _main
        return errors.guarded(lambda: _logged(opts, argv))
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/errors.py", line 149, in guarded
        return fn()
               ^^^^
      File "<checkout>/src/shape/cli/main.py", line 2318, in <lambda>
        return errors.guarded(lambda: _logged(opts, argv))
                                      ^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2338, in _logged
        return _dispatch(argv)
               ^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2440, in _dispatch
        return _dispatch_command(argv)
               ^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2507, in _dispatch_command
        return machine.run(machine.command_path(used, a), a, lambda: _route(a))
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/machine.py", line 403, in run
        return fn()
               ^^^^
      File "<checkout>/src/shape/cli/main.py", line 2507, in <lambda>
        return machine.run(machine.command_path(used, a), a, lambda: _route(a))
                                                                     ^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 2700, in _route
        return _run(_cmd_check, a)
               ^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/cli/main.py", line 93, in _run
        return fn(a)
               ^^^^^
      File "<checkout>/src/shape/cli/main.py", line 670, in _cmd_check
        profile = shape.load(a.shape)
                  ^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/profile/reference/profile.py", line 920, in load
        manifest, parts = read_artifact(str(path))
                          ^^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/artifact/io.py", line 168, in read_artifact
        result = _read_artifact(path, *args, **kwargs)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      File "<checkout>/src/shape/artifact/io.py", line 209, in _read_artifact
        with zipfile.ZipFile(path) as z:
             ^^^^^^^^^^^^^^^^^^^^^
      File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/zipfile/__init__.py", line 1353, in __init__
        self.fp = io.open(file, filemode)
                  ^^^^^^^^^^^^^^^^^^^^^^^
    FileNotFoundError: [Errno 2] No such file or directory: 'missing.shape'
    ```

This command exits nonzero. Read the diagnostic; this transcript shows a refusal or failed check, not a passing gate.

<a id="local-example-3"></a>

### Example 4

<!-- example: 3 -->

```bash {.runnable-reference}
shape capture orders.parquet -o orders-base.shape
  shape capture orders-today.parquet -o orders-today.shape
  shape compatibility orders-base.shape orders-today.shape --mode backward
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "7cf1c15de54d60a4a786a42ae7c675fca759ed3a8b350fc214d07d0c5e959b78", "written": "orders-base.shape"}
    {"shape_content_id": "3db012279effb591c5c20f9d053c296849287d97ee137090352b73fc28f0a667", "written": "orders-today.shape"}
    shape: note: orders-base.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: orders-today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"compatible": true, "issues": [], "mode": "backward"}
    ```

<a id="local-example-4"></a>

### Example 5

<!-- example: 4 -->

```bash {.runnable-reference}
shape doctor
```

??? info "Output (exit 0)"

    ```text {.expected}
    Shape 0.9.2
      python    3.12.14  (Linux-6.18.44-x86_64-with-glibc2.41)
      kernel    python  (pure Python; set SHAPE_KERNEL=rust)

    Required
      OK      numpy         2.5.3
      OK      pyarrow       25.0.1

    Optional
      OK      cryptography  50.0.2
      OK      yaml          6.0.3
      OK      pandas        3.0.6
      OK      scipy         1.18.1
      OK      deltalake     1.6.6
      OK      openpyxl      3.1.5
      OK      sklearn       1.9.1
      OK      tzdata        2026.3

    Result: OK
    ```

## Capture and generation formats

- `shape capture SRC` reads everything `shape profile` reads (CSV, Parquet, JSONL, a folder or
  glob, a Delta table with `--version N` or `--as-of TIMESTAMP`, an `abfss://` source) and writes a
  **model** (JSON, or a model `.shape` with `-o OUT.shape`), which
  `shape compatibility` and `shape plan` read. `--dataset` captures a folder of one file per table
  as a model with one table each. The model has the same content whatever the file format; the
  one difference is what the format itself carries: a CSV holds text and numbers only, so a date
  column is text there and a timestamp in Parquet (both are captured as text values). It is not a
  second way to profile; use `shape profile` for that.
- `--identifiers reserved|realistic` on `shape generate` (also with `--from` and `--scale-mode`)
  and `shape pack run` is the run switch of the identifier
  values (e-mail addresses, URIs, phone numbers, social security numbers): `reserved` values cannot
  belong to a real person, `realistic` ones can, so never use them for data that leaves a test
  system. Without the flag, a `pack run` spec's `scenario.identifiers` applies, then the schema's
  top-level `"identifiers"`, then `reserved`; a column's own `domains` or `range` key wins over
  all of them. A run with realistic identifiers prints one line on standard error
  (`shape: realistic identifiers are on: ...`), and `pack run` records the mode in its manifest
  (`reproducibility.identifiers`, which `pack replay` uses). Any other value exits 2. See
  [GENERATION_STRATEGIES.md](GENERATION_STRATEGIES.md).
- A generation schema file is JSON, or YAML when
  it is named `.yaml` or `.yml` (needs PyYAML). Every command that takes one (`generate`,
  `describe`, `presets`, `validate`) reads both.

`shape profile --validate
  COLUMN=KIND` stores how many values of a column are valid IBANs, ISO codes or US ZIPs. See
  [REFERENCE_PACKS.md](REFERENCE_PACKS.md).

`shape generate
  --fingerprint` writes a signed statement inside a Parquet file or Delta table that the data is
  synthetic and which run made it, at generation time (exit 0 valid, 1 digest or signature mismatch,
  2 none, a newer version or bad input). See [FINGERPRINT.md](FINGERPRINT.md).

`shape plugins new NAME --group GROUP [-o DIR] [--author TEXT]` creates a plugin package
from a template. See [plugin authoring](plugins/authoring.md).
