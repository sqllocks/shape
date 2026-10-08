# The `shape` command line

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

## `--json` and `--dry-run` on every command

`--json` prints exactly one JSON document on standard output (`format: "shape-result"`, `version: 1`,
`command`, `exit_code`, and the keys of what the command prints); the text goes to standard error.
Where `--json` takes a file (`profile`, `diff`, `check`, `design`) it keeps that meaning, and
`--json -` writes the document to standard output. `--dry-run` is on every command that writes: it
prints what would be written or sent (`format: "shape-dry-run"` with `--json`), writes nothing and
exits 0, or 2 for invalid input. `--junit FILE` and `--sarif FILE` on `diff`, `check`, `verify`,
`fidelity` and `profile validate --safe` write CI reports. See `docs/CI.md`.

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

## `shape vault`

`shape vault keygen|inspect|verify|rekey` manage the value vault, an encrypted file of the values a
safe capture withheld (`docs/VAULT.md`). `shape profile --vault OUT.shapevault --vault-policy
POLICY.json --kek REF` writes one with the profile; `shape generate --from X.shape --vault VAULT
--kek REF [--verify PUBKEY]` and `shape plan X.shape --vault VAULT --kek REF` use it. A key is
always a reference (`env://NAME`, `file://PATH`, a path), never a literal value.

| Command | Does |
|---|---|
| `shape vault keygen -o KEK.key` | writes a key-encryption key: 32 random bytes, base64, mode 0600; never overwrites |
| `shape vault inspect VAULT [--json]` | prints the header; needs no key |
| `shape vault verify VAULT --shape X.shape [--kek REF] [--verify PUBKEY] [--json]` | checks hash, ids, signature and, with a key, every column |
| `shape vault rekey X.shape VAULT --kek OLD --new-kek NEW --out-shape X2.shape --out-vault V2.shapevault [--key SIGNING_KEY] [--dry-run] [--json]` | re-encrypts under a new key; never in place |

Exit codes: 0 valid; 1 a wrong key, a hash, id, signature or authentication mismatch; 2 a malformed
vault, a newer `version`, an unusable key, policy or path, or other bad input. `shape git-setup`
also adds `*.shapevault` to `.gitignore`.

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
- `shape types PROFILE.shape [--contract CONTRACT.json] [--min-confidence C] [--json]` lists the
  columns whose declared, inferred or contract types disagree: a declared `string` that holds
  integers or ISO dates, a `float` that holds whole numbers, an `integer` the identifier rule
  calls a suspect, an inferred type that fewer than `C` (default 0.99) of the values fit, and a
  contract `dtype` that differs from the profile's. Each line names the table, the column, both
  types, the confidence and the option that would change it (`--types`, `--string-columns`). Exit
  0 with no findings, 1 with findings, 2 for bad input; `--json` prints the findings as a list
  (the same as `shape.types_report(profile, contract, min_confidence)`). See
  [PROFILING_NOTES.md](PROFILING_NOTES.md#type-inference).
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
- `shape explain DIFF.json` explains a `shape diff --json` or `shape drift` result in plain
  English, deterministically (`docs/EXPLAIN.md`).
- `shape rules mutate DATA CONTRACT.json` plants the corruptions of `shape chaos` one at a time and
  reports which rules of the contract catch them (`--plan`, `--seed`, `--rate`, `--diff`,
  `--min-score S`: exit 1 below S, `-o REPORT.json`, `--json`). `shape rules backtest REGISTRY NAME
  CONTRACT.json` replays a contract over every committed version of a registry name (`--since`,
  `--until`, `--window day|week|month`, `--incidents FILE`, `--fail-on-miss`: exit 1 on a missed
  incident, `--compare OLD_CONTRACT.json`, `-o`, `--json`). Exit 2 for unusable input. See
  `docs/RULES_TESTING.md`.
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
  See [DESIGN.md](DESIGN.md). `--tmdl DIR` also writes the star or snowflake design as a TMDL
  semantic model.
- `shape import-schema FILE -o OUT.gen.json` imports a JSON Schema, OpenAPI, Avro, Protobuf,
  Pydantic or TMDL schema as a generation spec, with `--from`, `--report`, `--strict` and
  `--allow-import` (Pydantic runs the module it imports). Exit 1 means `--strict` found an element
  that was not imported; 2 is a malformed or ambiguous input. See [IMPORTERS.md](IMPORTERS.md).
- `shape contract emit CONTRACT.json --to ddl|jsonschema|pandera|gx` writes a contract as database
  DDL, a JSON Schema, a pandera schema or a Great Expectations suite, listing what the target cannot
  say (`--strict` fails on it). See [CONTRACT_EMIT.md](CONTRACT_EMIT.md).
- `shape pin SPEC [-o OUT] [--json]` writes into a generation spec the current generator version of
  every strategy and distribution it uses (its `generators` map), so the same spec and seed give the
  same dataset id in every 1.x release; a pin that is there is kept. `shape pin SPEC --check`
  writes nothing: exit 1 and the names the spec does not pin, 0 when all are pinned, 2 for a spec
  that is not valid or pins a version this Shape does not have. See
  [GENERATION_STABILITY.md](GENERATION_STABILITY.md).
- `--identifiers reserved|realistic` on `shape generate` (also with `--from` and `--scale-mode`),
  `shape composite`, `shape demo run` and `shape pack run` is the run switch of the identifier
  values (e-mail addresses, URIs, phone numbers, social security numbers): `reserved` values cannot
  belong to a real person, `realistic` ones can, so never use them for data that leaves a test
  system. Without the flag, a `pack run` spec's `scenario.identifiers` applies, then the schema's
  top-level `"identifiers"`, then `reserved`; a column's own `domains` or `range` key wins over
  all of them. A run with realistic identifiers prints one line on standard error
  (`shape: realistic identifiers are on: ...`), and `pack run` records the mode in its manifest
  (`reproducibility.identifiers`, which `pack replay` uses). Any other value exits 2. See
  [GENERATION_STRATEGIES.md](GENERATION_STRATEGIES.md).
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
- `shape report-card REAL SYNTHETIC` runs the fidelity scores and tiers, the utility gate, the
  memorization gate and (with `--holdout`) a membership-inference test, and writes one card as
  JSON, Markdown or HTML (`-o`, by extension). Exit 0 when every section that ran passed, 1 when
  one failed or a `--require`d one did not run, 2 for unusable input. See
  [REPORT_CARD.md](REPORT_CARD.md).
- A generation schema file (what `shape from-ddl` and `shape learn` write) is JSON, or YAML when
  it is named `.yaml` or `.yml` (needs PyYAML). Every command that takes one (`generate`,
  `describe`, `presets`, `emit`, `stream`, `continue`, `time-travel`, `chaos`, `generate-drift`,
  `validate`) reads both.
- `shape proposals propose|list|decide` keeps the answers to what a profile cannot settle alone
  (foreign keys, personal data, meaning) in a decision file; `shape generate --from` and
  `shape plan` take it with `--decisions`. See [PROPOSALS.md](PROPOSALS.md).

- `shape reference list|show` lists the reference packs Shape can find (ZIP to city, ISO codes,
  IBAN lengths; your own in `SHAPE_REFERENCE_PATH`) and shows one with its manifest and first
  rows; a pack whose file does not match its checksum is exit 2. `shape profile --validate
  COLUMN=KIND` stores how many values of a column are valid IBANs, ISO codes or US ZIPs. See
  [REFERENCE_PACKS.md](REFERENCE_PACKS.md).
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

- `shape scorecard DATA` scores data quality by dimension ([SCORECARD.md](SCORECARD.md)); with
  `--slice-by COLUMN` it scores every slice and exits 1 when `--max-slice-gap` is exceeded.
  `shape skew TRAIN SERVING` compares serving data with training data (data or profiles) and
  exits 0 when nothing is flagged, 1 when a feature is flagged, 2 for unusable input
  ([FAIRNESS_AND_SKEW.md](FAIRNESS_AND_SKEW.md)).

- `shape bridge` serves Shape's commands as a versioned JSON protocol on standard input and output
  (one request and one response per line; `--once` for a single request; `--jobs-dir DIR` for the
  job files). `shape bridge schema --out DIR` writes its JSON Schemas and `--check DIR` verifies a
  directory against them. See [`BRIDGE.md`](BRIDGE.md).

- `shape resolve run FILE` finds duplicate entities in a CSV, Parquet or JSONL file and writes
  golden records; `shape resolve synth FILE -o OUT` plants seeded duplicates and writes the true
  clusters. See [RESOLVE.md](RESOLVE.md). Bad options or an unreadable file exit 2.

## Non-local targets and chaos input

- Commands that write to a non-local target (`generate --to`, `emit`, `stream`,
  `generate --scale-mode --sink`) need `--yes`, or `SHAPE_CONFIRM_REMOTE=1` (exactly `1`), or `y` at
  the prompt on a terminal; otherwise they exit 2 with
  `shape: error: refusing to write to non-local target URI without confirmation; pass --yes or set
  SHAPE_CONFIRM_REMOTE=1`. Paths, `file://`, `duckdb://`, `console` and `localhost`/`127.0.0.1`/`::1` are local,
  and `--dry-run` needs no confirmation. See `docs/SINKS.md`.
- `shape chaos --input DIR` corrupts only tables listed in `DIR/_shape_provenance.json` (matching
  sha256) or Parquet files with the `shape_synthetic` marker; `--allow-real-input` overrides, and
  `-o` may not be the input folder. See `docs/CHAOS.md`.
- `shape generate -o`, `continue`, `time-travel`, `pack run` and `chaos` write
  `_shape_provenance.json` beside the tables (`format: shape-provenance`, `version: 1`).

## Failure modes, detective, library, canary and game day

- `shape failure-modes list [--json]` and `shape failure-modes show ID` print the failure mode
  catalog (`docs/FAILURE_MODES.md`). `shape suite run failure-modes` runs the scenario of every
  entry and checks that every check the entry names fires; it exits 1 when one does not.
- `shape detective list`, `start NAME -o DIR`, `hint NAME N` and `check NAME --answer ANSWER.json`
  play a data detective pack (`docs/DETECTIVE.md`). `check` exits 0 when every planted finding is
  named and none is wrong, 1 otherwise, 2 for a malformed answer or an unknown pack.
- `shape library list`, `show NAME` and `get NAME -o X.shape` print and copy the safe profiles of
  public datasets (`docs/DATASET_LIBRARY.md`); `shape generate --from dataset:NAME` generates from
  one with no network.
- `shape canary make (library:NAME | ID) [--rows N] [--seed N] [--marker COLUMN=VALUE] [--format
  csv|parquet|jsonl] -o DIR [--dry-run]` writes a marked batch with planted failures;
  `shape canary check canary.json --result RESULT.json...` exits 0 when every expected detection is
  present, 1 when one is missing (a blind spot), 2 for malformed input (`docs/CANARIES.md`).
- `shape gameday run PLAN.json -o DIR [--seed N] [--dry-run]` plants failures in copies of your
  local data and runs the checks you list; it exits 0 when every expectation was detected, 1 when
  one was missed, 2 for a malformed plan (`docs/GAMEDAY.md`).

## `shape suite`, `shape seed` and the starter library

- `shape pack list --library` lists the starter scenarios and suites; `shape pack run library:NAME`
  runs one and compares it with its answer key. `shape suite run NAME|FILE [--scale small]
  [--seed N] [-o DIR] [--json]` runs a suite (`smoke`, `schema-evolution` or a `shape-suite` file):
  exit 0 when every scenario met its key, 1 when one did not (the scenario, the expectation and
  the observation are printed), 2 for a malformed suite or an unknown scenario. See
  `docs/SCENARIO_LIBRARY.md`.
- `shape seed SPEC|DOMAIN --target URI [--scale S] [--seed N] [--mode create|truncate|append]
  [--dry-run] [--json]` writes the generated tables into a database (`mssql`, `postgres`, `mysql`)
  or, with `sql://DIR`, into one ordered INSERT script per table, parents first. Exit 0 when
  seeded or planned, 1 when `--mode create` found an existing table or `--mode append` a primary key
  already in its table (nothing written), 2 for bad input or a failed write. See `docs/TESTING_WITH_SHAPE.md`, which also describes the pytest
  plugin.

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

## Change classes

`shape diff` classes every change as breaking, additive or cosmetic and prints a closing line
`bump: major (2 breaking, 1 additive, 4 cosmetic)` on stderr; the `--json` result and the line on
stdout carry `class`, `class_reason` and `semver` (`docs/DRIFT.md`, "Change classes").
`--fail-on breaking|additive|cosmetic` exits 1 when an unplanned change of that class or a stricter
one is reported (it may be combined with `--fail-on-drift`; the run fails when either fails), and
`--version-from X.Y.Z` adds `semver.next_version`. A class other than those three, or a version that
is not `X.Y.Z`, exits 2. A drift policy that names an unknown kind or class exits 2 too.

## Planned changes

`shape changes validate|list|add|ack` manages `shape-changes.yml`, the reviewable list of planned
changes that `diff`, `check` and `verify` read (`--changes FILE`, `--no-changes`, `--on DATE`). Exit
0 done, 1 `validate` found problems, 2 bad input. See `docs/PLANNED_CHANGES.md`.

## Parity and consumer contracts

`shape parity A B` checks that environment B has the same shape as environment A (tables, columns,
types, keys, relationships, null rates, distributions, table sizes); exit 0 parity, 1 a check
failed, 2 unusable input. See `docs/PARITY.md`. `shape contracts validate FILE` checks a consumer
contract file (exit 0 valid, 2 not, with every problem and its key path), and
`shape contracts check-consumers PROFILE.shape` runs the consumer contracts of a source in the
producer's CI (exit 0 every consumer passes, 1 one is broken, 2 unusable input; `--baseline`
marks the violations a change introduced). See `docs/CONSUMER_CONTRACTS.md`.

## The pull request check, the badge, notifications and plugin templates

- `shape ci comment RESULT.json... [-o FILE] [--max-findings 50] [--title TEXT]` renders the Markdown
  comment of a pull request from `shape-result` documents; `shape ci post-comment --body-file FILE
  --repo OWNER/REPO --pr N` creates or updates the one Shape comment (the token comes from
  `GITHUB_TOKEN`). Exit 0, 2 for a missing file or a document that is not a `shape-result`; for
  `post-comment` also 1 for an HTTP error other than 403 and 404 (those print a notice and exit 0).
  See `docs/PR_BOT.md`, the action `uses: sqllocks/shape@<tag>`.
- `shape badge RESULT.json... -o badge.svg [--label shape]` writes the status badge: passing, drift,
  failing or unknown. See `docs/CI.md`.
- `shape notify test [--project DIR]` sends a test notification to every target of `notifications:` in
  `shape.yml`; `--notify REF` on `diff`, `check`, `verify` and `fidelity` adds a target for one run.
  Exit 1 when a delivery failed, 2 without a project or notifications. See `docs/NOTIFICATIONS.md`.
- `shape plugins new NAME --group GROUP [-o DIR] [--author TEXT]` creates a plugin package from a
  template. See `docs/plugins/authoring.md`.

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

## History: `shape bisect` and `shape timelapse`

```
shape bisect REGISTRY NAME --good REF --bad REF [--column COL] [--kind KIND] [--contract FILE]
             [--verify-all] [--coarse week|month] [--json] [--source NAME] [threshold flags]
shape bisect layers --layers SOURCE[,SOURCE...] --good-date D1 --bad-date D2 [--column COL]
             [--map LAYER.COL=COL]... [--project shape.yml] [--json]
shape timelapse REGISTRY NAME --column COL [--table T] [--since DATE] [--until DATE]
             [--window day|week|month] [-o OUT.json|OUT.html] [--format json|text]
```

`bisect` finds the first committed version of `NAME` that changed (exit 0; exit 2 when `--good`
tests bad, `--bad` tests good, or a version cannot be tested, for example a share-safe profile).
`bisect layers` finds the layer of a pipeline where a change appears (exit 0 a layer shows it, 1
none does, 2 unusable input). `timelapse` follows one column across the versions (`-o OUT.html` is
one offline page). See `docs/HISTORY.md`.
