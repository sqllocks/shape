# `shape bridge`: Shape as a JSON protocol

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


`shape bridge` serves Shape's commands over standard input and output as a versioned JSON
request/response protocol. Editors, notebooks, wrappers and agents use it to run Shape without
parsing command-line text.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

- `shape bridge` reads **one request per line** and writes **one response per line** until end of
  input (a long-lived session).
- `shape bridge --once` reads all of standard input as **one request** (it may span lines), answers
  once and exits: 0 for a success, 1 for an error response.
- `--jobs-dir DIR` says where job state is kept (below). Without it: `$SHAPE_JOBS_DIR`, else
  `~/.shape/jobs`.
- Standard output carries only responses. Anything a command prints goes to standard error.
- Requests are read as UTF-8 whatever the locale (JSON text is UTF-8); a line that is not UTF-8
  is answered `usage.invalid_json`. Responses are ASCII (other characters are `\u` escapes).
- Every command calls the code the matching `shape` command calls. There is no second
  implementation, so a result and the CLI's agree.

The schemas of every request and result are published in
[`docs/bridge/schema/`](bridge/schema/index.json), with test vectors in
[`docs/bridge/vectors/`](bridge/vectors). `shape bridge schema --out DIR` writes the schemas and
`shape bridge schema --check DIR` verifies a directory against them.

## The envelope

A request:

```json
{"api_version": "1.0", "id": "r1", "command": "generate",
 "args": {"domain": "retail", "scale": "small"}, "options": {"async": false}}
```

| field | meaning |
|---|---|
| `api_version` | `"MAJOR.MINOR"`. Optional (assumed current, 1.1, with a warning) but you should send it. A request that declares `"1.0"` is answered as the 1.0 bridge answers it (below). |
| `id` | Any string or integer of at most 128 characters. Echoed in the response; use it to match answers. |
| `command` | One of the commands below. |
| `args` | The command's arguments (an object; `null` means none). Unknown names are refused. A `null` value is the same as leaving the argument out. |
| `options` | `include_raw_values`, `async`, `max_inline_bytes` (below). |

A success:

```json
{"api_version": "1.1", "id": "r1", "command": "generate", "ok": true,
 "result": {...}, "warnings": [{"code": "result_in_file", "message": "..."}]}
```

A failure:

```json
{"api_version": "1.1", "id": "r1", "command": "generate", "ok": false,
 "error": {"code": "input.unknown_domain", "group": "input",
           "message": "no domain named 'nope' (installed: retail); ...",
           "hint": "run the `list` command"},
 "warnings": []}
```

Every request gets exactly one response, even a request that is not JSON (`id` is `null` then, and
`command` too when it could not be read). The bridge never exits on a bad request.

`warnings` are non-fatal: something the caller should know (for example `api_version_assumed`,
`newer_minor_version`, `artifact_not_verified`, `profile_file_holds_values`, `result_in_file`,
`project_source_not_selected`, `real_input_corrupted`). A warning's `code` is stable; the full list, with a sentence for each,
is `warning_codes` in [`docs/bridge/schema/index.json`](bridge/schema/index.json).

### Error codes

`code` is `group.name`. The groups:

| group | meaning |
|---|---|
| `usage` | the request is malformed: bad JSON, unknown command or argument, wrong type, unsupported version |
| `input` | what the request names is wrong: an unknown domain, a missing or invalid file, an unknown job |
| `policy` | a security, trust or capability rule refuses it: a signature that does not verify, something not installed |
| `privacy` | the operation would return raw values and was not asked to |
| `io` | a read, write or sink failed |
| `auth` | a credential is missing or was rejected |
| `internal` | a bug in Shape. The message names the exception; the traceback goes to standard error, never to the client |

The full list, with a sentence for each, is `error_codes` in
[`docs/bridge/schema/index.json`](bridge/schema/index.json). 1.1 adds `input.unknown_proposal`,
`input.unknown_source` and `input.unknown_format`; 1.2 adds `input.contract_conflict` and
`policy.unverified_input`.

## Versions and the stability promise

`api_version` is `MAJOR.MINOR`; this bridge speaks **1.2** and serves **1.0 to 1.2**.

- **Minor versions only add:** a command, an optional argument, an option, a result field, an error
  code, a warning code. A client written for 1.0 keeps working on 1.7, so **a client ignores result
  fields it does not know**.
- **Within a major version nothing is renamed, removed, retyped or tightened:** no command, argument,
  result field, error code or the meaning of any of them.
- **A major version may break.** A bridge serves one major version. A request for another is refused
  with `usage.unsupported_version`, and the message names the supported range
  (`... this bridge serves 1.0 to 1.2`). The response always carries the bridge's own
  `api_version`, so a client can read it from the refusal.
- A request with a newer *minor* than the bridge's is served, with the warning
  `newer_minor_version`: what the newer minor added is not available.
- A request with **no `api_version`** is served as 1.2, with the warning `api_version_assumed`.
- Persisted files declare their own `format` and integer `version` (the job file below, the vector
  files). A file written by a newer Shape is refused with `input.unsupported_format_version`, never
  misread. The job file `shape-bridge-job` and the vector files `shape-bridge-vectors` are still
  version 1: a job file written by 1.0 or 1.1 is read unchanged by the 1.2 bridge.

**The 1.0 promise.** A request that declares `"api_version": "1.0"` is answered exactly as the 1.0
bridge answers it:

- a command added in 1.1 or 1.2 is `usage.unknown_command`, and an argument added later is
  `usage.unknown_argument`; the hint names the version that added it and lists only what 1.0 had;
- no result field or warning of 1.1 or 1.2 is added to the result of a 1.0 command (`verify`'s gates have no
  `details` for a 1.0 request, for example);
- the one field that differs is the response's own `api_version`, which is the bridge's version
  (`"1.2"`), as it always was.

Every command and every argument records the version that added it: `since` (`"1.0"`, `"1.1"` or `"1.2"`) in
`index.json` (`commands.NAME.since`, and `commands.NAME.args` for each argument) and `x-since` in the
request and result schemas.

The promise is enforced: the published schemas are generated from the code, a test fails when the
committed files differ (so a change is a visible, deliberate edit), and a compatibility test per
command runs the published vectors against a live bridge: a result may gain fields, never lose one.
A vector file with `"needs": "no-advanced"` (the report-card vectors) was recorded without the
optional scikit-learn (extra `advanced`): replay it with scikit-learn absent or hidden, since with it
the fidelity section also runs the adversarial test and its answer differs.
The 1.0 contract is **frozen** in [`docs/bridge/schema/1.0/`](bridge/schema/1.0/index.json) and
[`docs/bridge/vectors/1.0/`](bridge/vectors/1.0): `tests/bridge/test_compat_1_0.py` replays every 1.0
vector against the 1.2 bridge (each response equals the recorded one in every field except
`api_version`) and checks that the 1.2 schemas keep every 1.0 command, argument (name, type,
required-ness, enum values, default), result property, error code and warning code.

## Commands

Names and argument names are those of the original 17-command bridge, so existing clients keep
working; Shape's own commands are added.

| command | what it does | arguments |
|---|---|---|
| `list` | the installed domains, with their modes and profiles | none |
| `describe` | tables, columns, relationships, rules and scale presets of a domain or schema | `domain`\*, `mode`, `scale`, `profile` |
| `dry_run` | plan a run (rows per table, order, problems); generates nothing | `domain`\*, `scale`, `mode`, `profile` |
| `validate` | validate a generation schema file (or a contract) | `schema_path`\* |
| `profile_info` | the weights and ratios a domain generates with | `domain`\*, `mode`, `profile` |
| `generate` | generate a domain or schema; optionally write the files (job-capable) | `domain`\*, `scale`, `seed`, `format`, `output_dir`, `mode`, `profile`, `identifiers` (1.2: `reserved` or `realistic`, the run switch of the identifier values; `realistic` is said once on the bridge's standard error) |
| `preview` | a small sample as JSON rows | `domain`\*, `rows`, `seed`, `tables`, `mode`, `profile` |
| `scale_generate` | generate at scale into sinks: `local_single`, `local_mp`, `fabric_spark` (job-capable, cancellable) | `domain`\*, `scale`, `seed`, `scale_mode`, `sinks`, `sink_config`, `chunk_size`, `max_workers`, `mode`, `profile` |
| `stream` | start a background stream (always a job, cancellable) | `domain`\*, `scale`, `seed`, `sinks`, `sink_config`, `interval_seconds`, `chunk_size`, `max_chunks`, `mode`, `profile` |
| `stream_status`, `stream_stop` | a stream's state; stop it | `stream_id`\* |
| `scale_status`, `scale_cancel` | a scale job's state; cancel it | `job_id`\*, `token` |
| `profile` | profile a file, folder, glob or Delta table into a `.shape` artifact (job-capable) | `source`\*, `output`, `name`, `dataset`, `version`, `as_of`, `fail_on_empty`, `project` (1.1) |
| `diff` | compare two profiles | `before`\*, `after`\*, `policy`, `thresholds`, `column_thresholds`, `ignore_columns`, `only_columns`, `project` (1.1), `source` (1.1) |
| `check` | check a profile against a contract | `profile`\*, `contract`\*, `project` (1.1), `source` (1.1) |
| `verify` | run the validation gates over data files (job-capable) | `path`\*, `format`, `schema`, `config`, `statistical`, `strict`, `project` (1.1), `source` (1.1) |
| `proposals_propose` | find proposals for a profile and merge them into a decision file (1.1, job-capable; `rule` in `kinds` is 1.2) | `profile`\*, `decisions`\*, `data`, `kinds`, `min_confidence`, `auto_accept` |
| `proposals_list` | list a decision file's proposals and the decisions on them (1.1) | `decisions`\*, `status`, `kind`, `min_confidence` |
| `proposals_decide` | accept, reject or defer a proposal (1.1) | `decisions`\*, `proposal`\*, `verb`\*, `actor`, `note` |
| `project_validate` | check a `shape.yml` and report every problem with its key path (1.1) | `text` or `path` |
| `project_show` | show a project file's sources and gate modes (1.1) | `path`\* |
| `design` | design a 3NF, star or snowflake schema: lint report, tables, DDL (1.1) | `input`\*, `mode`, `dialect`, `schema_name`, `drop` |
| `design_from_data` | build a design input from a data file (1.1) | `source`\*, `name` |
| `format_schema` | list the formats Shape reads, or give the JSON Schema of one (1.1) | `name` |
| `profile_show` | show a stored `.shape` profile without profiling again (1.1) | `path`\* |
| `contract_validate` | validate a contract of the format `shape check` reads (1.1) | `path` or `text` |
| `safe_scan` | scan a share-safe profile for leaks (1.1) | `path` or `text` |
| `proposals_contract` | write the accepted rule proposals as a contract (1.2) | `decisions`\*, `output`\*, `merge` |
| `report_card` | one report card for a synthetic dataset: fidelity, utility, privacy (1.2, job-capable) | `real`\*, `synthetic`\*, `config`, `tiers`, `holdout`, `manifest`, `require`, `output` |
| `report_card_read` | a stored report card, checked (1.2) | `path`\* |
| `rules_mutate` | mutation-test a contract against planted faults (1.2, job-capable, cancellable) | `data`\*, `contract`\*, `plan`, `seed`, `rate`, `min_score` |
| `rules_backtest` | replay a contract over a registry's history (1.2, job-capable) | `registry`\*, `name`\*, `contract`\*, `since`, `until`, `window`, `incidents`, `compare` |
| `bisect` | the first committed version of a name that changed (1.2, job-capable) | `registry`\*, `name`\*, `good`\*, `bad`\*, `column`, `kind`, `contract`, `project`, `source`, `verify_all`, `coarse` |
| `bisect_layers` | the layer of a pipeline where a change appears (1.2, job-capable) | `layers`\*, `good_date`\*, `bad_date`\*, `column`, `map`, `project` |
| `timelapse` | one column across the versions of a name (1.2, job-capable) | `registry`\*, `name`\*, `column`\*, `table`, `since`, `until`, `window` |
| `registry_diff` | drift between two versions in a registry, raw or share-safe (1.2) | `root`\*, `name`\*, `ref1`\*, `ref2`\*, `policy`, `thresholds` |
| `chaos` | corrupt tables on purpose, with a ground-truth log (1.2, job-capable, cancellable) | `output_dir`\*, `corrupt`\*, `input`, `domain`, `mode`, `scale`, `seed`, `format`, `batch`, `start_date`, `ground_truth`, `allow_real_input` |
| `suite_list` | the built-in suites and the starter scenarios (1.2) | none |
| `suite_run` | run a suite of scenarios against their answer keys (1.2, job-capable, cancellable) | `suite`\*, `scale`, `seed`, `output_dir` |
| `job_status`, `job_cancel`, `job_list` | any job's state, cancel, list | `job_id`\*, `token`; `status`, `limit` |
| `demo_list`, `demo_run`, `demo_status`, `demo_cleanup` | the demo scenarios (see below) | see the schema |

\* required. `domain` is an installed domain (see `list`) or the path of a generation schema file.

`describe` lists a table's columns in the order the schema declares them. A generated table (what
`generate` writes and `preview` shows) lists its columns in generation order: keys first, then
the columns that depend on others.

Per-command argument, result and example detail is in the published schemas
(`commands/NAME.request.schema.json`, `commands/NAME.result.schema.json`) and the vectors
(`vectors/NAME.json`); every argument carries a description.

**The demo commands.** `demo_list`, `demo_run`, `demo_status` and `demo_cleanup` call the same
functions as `shape demo`: the result of each is what the matching `shape demo` operation returns.
`demo_run` takes the settings `shape demo run` takes (`scenario`, `mode`, `rows`, `domain`,
`input_file`, `connection`, `output_formats`, `dry_run`, `seed`, `scale_mode`) and returns
`success`, `session_id`, `scenario`, `mode`, `fidelity_score` (`null` when no column was compared),
`error` and `artifact_count`; a failed run is a result with `success: false`, and a setting that
cannot be used is `input.invalid_value`. `demo_status` returns the session's `manifest` (and, for a
Spark run, its live state under `fabric`, which needs `token` or `$SHAPE_FABRIC_TOKEN`; the token
is never stored). `demo_cleanup` returns `removed` as a list of `{target, names}` entries, with
`failed` and `skipped` (what was left, and why). An unknown session is `input.invalid_value`. The
sessions are kept in `$SHAPE_HOME` (default `~/.shape`). A run's progress is printed to standard
error; standard output carries only the reply.

`generate`, `preview` and `scale_generate` return synthetic data. `profile`, `diff`, `check` and
`verify` read real data: see *Safe by default*.

## What's new in 1.1

Bridge 1.1 adds commands, arguments and schema annotations. It changes nothing a 1.0 client sees
(see *The 1.0 promise*).

- **Proposals and decision files**: `proposals_propose`, `proposals_list`, `proposals_decide`.
- **The project file**: `project_validate`, `project_show`, and the arguments `project` (and
  `source`) on `profile`, `diff`, `check` and `verify`.
- **Schema design**: `design` and `design_from_data`.
- **Format schemas**: `format_schema`.
- **Stored artifacts**: `profile_show`, `contract_validate` and `safe_scan`.
- **Comparison gates in `verify`**: the argument `source` (the memorization and utility gates) and
  `details` on every gate.
- **Annotations for clients**: `x-path`, `x-name-or-path`, `x-since` in the request schemas and
  `since` and `effects` in `index.json` (*Annotations for clients*, below).
- New error codes `input.unknown_proposal`, `input.unknown_source` and `input.unknown_format`, and
  the warning code `project_source_not_selected`. Every warning code is now listed in
  `index.json` (`warning_codes`).

The paragraphs below give each new command's request and result, an example request and its
response (taken from the published vectors, with `/work` for the scratch folder), its error codes
and its warnings. A path argument is a file the bridge reads or writes as given; the schemas say
which (`x-path`).

## Proposals and decision files

Some facts about a dataset cannot be settled by a profile alone: whether a column is a foreign key,
whether it holds personal data, what it means. The three commands run what `shape proposals
propose|list|decide` run (`shape.proposals`) on the same decision file (`docs/PROPOSALS.md`). **A
person decides:** `proposals_propose` accepts nothing unless `auto_accept` is given, and a rejected
proposal is never proposed again.

### `proposals_propose`

Finds the proposals for a profile (`profile`, a `.shape` file) and merges them into the decision file
`decisions` (created when it does not exist). `data` is the profiled data for value evidence: one
directory with a `NAME.csv` or `NAME.parquet` per table, or `NAME=PATH` pairs (not both). `kinds`
(`relationship`, `pii`, `semantic`; default all), `min_confidence` (0 to 1, default 0.5) and
`auto_accept` (0 to 1: accept undecided proposals at or above it, as the actor `auto-accept`; off
unless given, and it never overrides a decision). Job-capable: a bad request is refused before a job
is made.

Result: `decisions` (the path), `proposal_ids` (the ids the run found, most confident first),
`added`, `unchanged` (counts), `skipped` (ids of rejected proposals not proposed again), and, as the
CLI prints them, `proposals`, `updated`, `withdrawn`, `skipped_rejected` and `auto_accepted`.

Errors: `input.not_found` (profile, data or decision file), `input.invalid_schema` (a decision file
that is not valid, or a profile that is not a `.shape` profile), `input.unsupported_format_version`
(a decision file a newer Shape wrote), `io.write_failed`, `usage.invalid_argument` (a kind that does
not exist, a confidence outside 0 to 1, `data` mixing a directory and pairs). Warnings:
`artifact_not_verified` (an unsigned profile).

```json request
{"api_version": "1.1", "args": {"data": ["/work/shop"], "decisions": "/work/decisions.json", "profile": "/work/shop.shape"}, "command": "proposals_propose", "id": "shop"}
```

```json response
{"api_version": "1.2", "command": "proposals_propose", "id": "shop", "ok": true, "result": {"added": 5, "auto_accepted": [], "decisions": "/work/decisions.json", "proposal_ids": ["pii:customers.email", "relationship:orders.customer_id->customers.customer_id", "semantic:customers.email", "pii:customers.full_name", "semantic:customers.full_name"], "proposals": 5, "skipped": [], "skipped_rejected": [], "unchanged": 0, "updated": 0, "withdrawn": 0}, "warnings": [{"code": "artifact_not_verified", "message": "/work/shop.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}]}
```

```json request
{"api_version": "1.1", "args": {"decisions": "/work/decisions.json", "profile": "/work/none.shape"}, "command": "proposals_propose", "id": "missing-profile"}
```

```json response
{"api_version": "1.2", "command": "proposals_propose", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.shape"}, "id": "missing-profile", "ok": false, "warnings": []}
```


### `proposals_list`

Lists the proposals of the decision file `decisions` and the decision on each, most confident first.
Filters: `status` (`pending`, `accepted`, `rejected`, `deferred`), `kind`, `min_confidence`. Result:
`count` and `proposals`, each with `id`, `kind`, `subject`, `claim`, `confidence`, `evidence`,
`proposed_at` and `decision`: `status` (`pending` when nobody decided), `actor`, `note` and
`decided_at` (`null` while pending). `proposals` is spillable (*Large results*).

**Safe by default.** The evidence of a proposal can hold values of the profiled columns: the range
(`range.child`, `range.parent`) of the columns of a relationship. A decision file does not say which
columns are classified, so those values are withheld whatever the column: `null`, and the proposal
carries `"redacted": true`. Counts, fractions, names and the `within` verdict stay. Set
`options.include_raw_values` to get the values. (The decision file itself holds them: it is the
person's record.)

Errors: `input.not_found`, `input.invalid_schema`, `input.unsupported_format_version`,
`usage.invalid_argument` (a status, kind or confidence that does not exist). Warnings:
`result_in_file`.

```json request
{"api_version": "1.1", "args": {"decisions": "/work/decisions.json", "kind": "relationship"}, "command": "proposals_list", "id": "relationships-only"}
```

```json response
{"api_version": "1.2", "command": "proposals_list", "id": "relationships-only", "ok": true, "result": {"count": 1, "decisions": "/work/decisions.json", "proposals": [{"claim": {"child": "orders", "child_columns": ["customer_id"], "parent": "customers", "parent_columns": ["customer_id"], "type": "one_to_many"}, "confidence": 1.0, "decision": {"actor": null, "decided_at": "<any>", "note": null, "status": "pending"}, "evidence": {"cardinality": {"child_distinct": 50, "parent_distinct": 50, "parent_unique": true}, "containment": {"child_distinct": 50, "fraction": 1.0}, "name": {"rule": "same name as the parent key", "score": 1.0}, "range": {"child": null, "parent": null, "within": true}, "type": {"child": "integer", "compatible": true, "parent": "integer"}}, "id": "relationship:orders.customer_id->customers.customer_id", "kind": "relationship", "proposed_at": "<any>", "redacted": true, "subject": "orders.customer_id->customers.customer_id"}]}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"decisions": "/work/none.json"}, "command": "proposals_list", "id": "missing-file"}
```

```json response
{"api_version": "1.2", "command": "proposals_list", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.json"}, "id": "missing-file", "ok": false, "warnings": []}
```


### `proposals_decide`

Records that the proposal `proposal` of the decision file `decisions` is accepted, rejected or
deferred (`verb`: `accept`, `reject`, `defer`), by `actor` (default `$SHAPE_ACTOR`, then the login
name, as the CLI) with a `note`. A second decision replaces the first. Result: the updated proposal,
as `proposals_list` returns it (with the same protection of values).

Errors: `input.unknown_proposal` (no proposal has the id), `input.not_found`,
`input.invalid_schema`, `input.unsupported_format_version`, `input.invalid_value` (an empty actor),
`io.write_failed`, `usage.invalid_argument` (a verb that does not exist).

```json request
{"api_version": "1.1", "args": {"actor": "ana", "decisions": "/work/decisions.json", "note": "orders belong to customers", "proposal": "relationship:orders.customer_id->customers.customer_id", "verb": "accept"}, "command": "proposals_decide", "id": "accept"}
```

```json response
{"api_version": "1.2", "command": "proposals_decide", "id": "accept", "ok": true, "result": {"claim": {"child": "orders", "child_columns": ["customer_id"], "parent": "customers", "parent_columns": ["customer_id"], "type": "one_to_many"}, "confidence": 1.0, "decision": {"actor": "ana", "decided_at": "<any>", "note": "orders belong to customers", "status": "accepted"}, "evidence": {"cardinality": {"child_distinct": 50, "parent_distinct": 50, "parent_unique": true}, "containment": {"child_distinct": 50, "fraction": 1.0}, "name": {"rule": "same name as the parent key", "score": 1.0}, "range": {"child": null, "parent": null, "within": true}, "type": {"child": "integer", "compatible": true, "parent": "integer"}}, "id": "relationship:orders.customer_id->customers.customer_id", "kind": "relationship", "proposed_at": "<any>", "redacted": true, "subject": "orders.customer_id->customers.customer_id"}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"actor": "ana", "decisions": "/work/decisions.json", "proposal": "pii:nothing.here", "verb": "accept"}, "command": "proposals_decide", "id": "unknown-proposal"}
```

```json response
{"api_version": "1.2", "command": "proposals_decide", "error": {"code": "input.unknown_proposal", "group": "input", "hint": "run `proposals_list` for the ids", "message": "no proposal 'pii:nothing.here' in /work/decisions.json"}, "id": "unknown-proposal", "ok": false, "warnings": []}
```


## The project file

`shape.yml` names the sources, baselines, thresholds and gate modes of a setup (`docs/PROJECT.md`).
Reading one needs the optional PyYAML (extra `yaml`): without it the commands answer
`policy.capability_unavailable` and name the extra. **The bridge never looks for a `shape.yml` on
its own:** a request that names no project behaves as before, even when a `shape.yml` is in the
working folder.

### `project_validate`

Checks a project file given as `text` or as `path` (exactly one) with the rules of `shape project
validate`; it writes nothing. Result: `valid` and `problems`, each with `path` (the key path, or
`document`), `message` and `line` (an integer for a YAML syntax error or a duplicate key, else
`null`).

Errors: `input.not_found`, `input.unsupported_format_version` (a file of a newer version is not
"invalid": it is refused as such), `usage.invalid_argument` (neither or both of `text` and `path`),
`policy.capability_unavailable` (PyYAML is missing).

```json request
{"api_version": "1.1", "args": {"text": "format: shape-project\nversion: 1\nsources:\n  validate: {path: ''}\n"}, "command": "project_validate", "id": "problems"}
```

```json response
{"api_version": "1.2", "command": "project_validate", "id": "problems", "ok": true, "result": {"problems": [{"line": null, "message": "source name 'validate' is a `shape profile` subcommand", "path": "sources.validate"}, {"line": null, "message": "must not be empty", "path": "sources.validate.path"}], "valid": false}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"text": "format: shape-project\nversion: 2\nname: vectors\nsources:\n  orders:\n    path: a.csv\n    contract: contract.json\n    ignore: [status]\n    columns:\n      amount: {owner: finance@example.com}\ngates:\n  range_constraint: {mode: observe}\n"}, "command": "project_validate", "id": "newer-version"}
```

```json response
{"api_version": "1.2", "command": "project_validate", "error": {"code": "input.unsupported_format_version", "group": "input", "hint": "upgrade Shape to read it", "message": "shape.yml: version 2 is newer than this Shape understands (it reads up to version 1): upgrade Shape, or lower the file's version if it uses nothing newer"}, "id": "newer-version", "ok": false, "warnings": []}
```


### `project_show`

Shows the project file `path`. Result: `file`, `format`, `version`, `name`, `sources` (name to
`path`, resolved against the file's folder, `dataset`, `contract` and `baseline`) and `gates` (name
to `mode`, `observe` or `enforce`).

Errors: `input.not_found`, `input.invalid_schema` (with the problems), `input.unsupported_format_version`,
`policy.capability_unavailable`.

```json request
{"api_version": "1.1", "args": {"path": "/work/shape.yml"}, "command": "project_show", "id": "sources"}
```

```json response
{"api_version": "1.2", "command": "project_show", "id": "sources", "ok": true, "result": {"file": "/work/shape.yml", "format": "shape-project", "gates": {"range_constraint": "observe"}, "name": "vectors", "sources": {"orders": {"baseline": null, "contract": "/work/contract.json", "dataset": false, "path": "/work/a.csv"}}, "version": 1}, "warnings": []}
```


### `project` and `source` on `profile`, `diff`, `check` and `verify`

`project` is the path of a `shape.yml`. Its settings then apply as for `--project FILE --source NAME`:

- `diff` and `check` also take `source`, the name of a source of the project: the source's drift
  thresholds, ignore lists, owners and annotations apply, a flag of the request (for example
  `ignore_columns`) replaces the project's, and the result gains the `project` block of
  `shape diff|check --json` (`file`, `format`, `version`, `source`) with `owner` and `annotations`
  on the changes and violations of annotated columns. Without `source`, a project with one source
  selects it; with several, the source named like the profile does, else none applies and the
  response has the warning `project_source_not_selected`. `before`, `after` and `contract` are still
  given: the bridge does not look up a baseline.
- `profile`'s `source` and `verify`'s `path` may name a source of the project instead of a path (a
  path that exists wins, as on the command line): the source's path, `dataset` and name apply.
- `verify` reports each gate's `mode` (`observe` or `enforce`; a gate the project does not list is
  enforced), `enforced_passed` (every enforced gate passed) and the `project` block, as `shape
  verify -o report.json` does. `passed` stays the 1.0 result (every gate passed): a failing gate that
  the project only observes leaves `passed` false and `enforced_passed` true, and the exit code of
  `shape verify` is 0.

**`verify`'s `source` is the real data**, not a source name (below), as in `shape verify --source
DATA`; a project source for `verify` is selected as the command line selects it, by the single source
of the project or by `path`.

Errors: `input.not_found`, `input.invalid_schema` (the message names every problem),
`input.unsupported_format_version`, `input.unknown_source` (the project has no source with this name),
`usage.invalid_argument` (`source` without `project`), `policy.capability_unavailable`. Warnings:
`project_source_not_selected`.

```json request
{"api_version": "1.1", "args": {"after": "/work/b.shape", "before": "/work/a.shape", "project": "/work/shape.yml"}, "command": "diff", "id": "project-policy"}
```

```json response
{"api_version": "1.2", "command": "diff", "id": "project-policy", "ok": true, "result": {"change_count": 2, "changes": [{"baseline": 100.625, "column": "amount", "current": 150.625, "kind": "mean_shift", "owner": "finance@example.com", "score": 0.7235, "severity": "medium"}, {"baseline": {"p05": 69.85, "p25": 85.75, "p50": 99.5, "p75": 114.25, "p95": 125.8}, "column": "amount", "current": {"p05": 119.85, "p25": 135.75, "p50": 149.5, "p75": 164.25, "p95": 175.8}, "kind": "distribution_shift", "owner": "finance@example.com", "score": 0.8616, "severity": "medium"}], "drifted": true, "project": {"file": "/work/shape.yml", "format": "shape-project", "source": "orders", "version": 1}}, "warnings": [{"code": "artifact_not_verified", "message": "/work/a.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}, {"code": "artifact_not_verified", "message": "/work/b.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}]}
```

```json request
{"api_version": "1.1", "args": {"after": "/work/b.shape", "before": "/work/a.shape", "project": "/work/shape.yml", "source": "ghost"}, "command": "diff", "id": "unknown-source"}
```

```json response
{"api_version": "1.2", "command": "diff", "error": {"code": "input.unknown_source", "group": "input", "hint": "run `project_show` for the sources", "message": "no source 'ghost' in /work/shape.yml (sources: orders)"}, "id": "unknown-source", "ok": false, "warnings": [{"code": "artifact_not_verified", "message": "/work/a.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}, {"code": "artifact_not_verified", "message": "/work/b.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}]}
```


## Schema design

`design` and `design_from_data` run what `shape design` and `shape design --from-data` run
(`shape.design`, `docs/DESIGN.md`). Both are read-only: nothing is written; for the same input the
`ddl` is byte for byte the file `shape design -o` writes and `tables` is the document `--json`
writes.

### `design`

Reads the design input `input` (JSON, format `shape-design`), lints it and derives the schema for
`mode` (`3nf`, `star`, `snowflake`; default `3nf`) in `dialect` (`tsql`, `tsql-fabric-warehouse`,
`postgres`, `mysql`; default `tsql`), optionally qualified with `schema_name` and with `drop` (a
`DROP TABLE` before each `CREATE`). Result: `lint` (the `--lint` report: `code`, `severity`, `path`,
`message`), `tables` (the `shape-design-result` document), `ddl` (spillable) and `passed` (no
finding of severity `error`). A design with errors has no DDL: `tables` and `ddl` are `null`.

Errors: `input.not_found`, `input.invalid_schema` (the message names what is wrong),
`input.unsupported_format_version`, `usage.invalid_argument` (a mode or dialect that does not exist).
Warnings: `result_in_file`.

```json request
{"api_version": "1.1", "args": {"dialect": "postgres", "input": "/work/tiny.design.json", "schema_name": "dw"}, "command": "design", "id": "postgres-3nf"}
```

```json response
{"api_version": "1.2", "command": "design", "id": "postgres-3nf", "ok": true, "result": {"ddl": "-- Schema design 'tiny' (3nf)\n\n-- relation: city\nCREATE TABLE \"dw\".\"city\" (\n    \"city_id\"                      BIGINT               NOT NULL,\n    \"city\"                         VARCHAR(255)         NULL,\n    CONSTRAINT \"PK_city\" PRIMARY KEY (\"city_id\")\n);\n\n-- relation: city_city\nCREATE TABLE \"dw\".\"city_city\" (\n    \"city\"                         VARCHAR(255)         NOT NULL,\n    \"country\"                      VARCHAR(255)         NULL,\n    CONSTRAINT \"PK_city_city\" PRIMARY KEY (\"city\")\n);\n\n-- foreign keys\nALTER TABLE \"dw\".\"city\" ADD CONSTRAINT \"FK_city_city\" FOREIGN KEY (\"city\") REFERENCES \"dw\".\"city_city\" (\"city\");\n", "lint": [], "passed": true, "tables": {"format": "shape-design-result", "mode": "3nf", "name": "tiny", "notes": [], "tables": [{"columns": [{"name": "city_id", "nullable": false, "type": "integer"}, {"name": "city", "nullable": true, "type": "string"}], "foreign_keys": [{"columns": ["city"], "ref_columns": ["city"], "ref_table": "city_city"}], "kind": "relation", "name": "city", "primary_key": ["city_id"], "source_entity": "City"}, {"columns": [{"name": "city", "nullable": false, "type": "string"}, {"name": "country", "nullable": true, "type": "string"}], "foreign_keys": [], "kind": "relation", "name": "city_city", "primary_key": ["city"], "source_entity": "City"}], "version": 1}}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"input": "/work/failing.design.json", "mode": "star"}, "command": "design", "id": "lint-errors"}
```

```json response
{"api_version": "1.2", "command": "design", "id": "lint-errors", "ok": true, "result": {"ddl": null, "lint": [{"code": "D001", "message": "fact 'sales' has no declared grain", "path": "facts.sales", "severity": "error"}], "passed": false, "tables": null}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"input": "/work/none.design.json"}, "command": "design", "id": "missing-file"}
```

```json response
{"api_version": "1.2", "command": "design", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.design.json"}, "id": "missing-file", "ok": false, "warnings": []}
```


### `design_from_data`

Builds a design input from the data file `source` (CSV, Parquet or JSONL): types, nullability, keys
and exact functional dependencies. `name` names the design and its entity (default: the file's
name). Result: the design input document `--from-data` writes (`format`, `version`, `name`,
`entities`, ...).

Errors: `input.not_found`, `input.invalid_schema` (a file that cannot be read as data).

```json request
{"api_version": "1.1", "args": {"name": "orders", "source": "/work/a.csv"}, "command": "design_from_data", "id": "a-csv"}
```

```json response
{"api_version": "1.2", "command": "design_from_data", "id": "a-csv", "ok": true, "result": {"entities": [{"attributes": [{"name": "id", "nullable": false, "type": "integer"}, {"max_length": 18, "name": "email", "nullable": false, "type": "string"}, {"max_length": 4, "name": "status", "nullable": false, "type": "string"}, {"name": "amount", "nullable": false, "type": "integer"}], "dependencies": [], "history": {"attributes": {}, "default": 1}, "keys": [["id"], ["email"]], "name": "orders"}], "facts": [], "format": "shape-design", "hierarchies": [], "name": "orders", "version": 1}, "warnings": []}
```


## Format schemas

### `format_schema`

Without `name`, the result is `names`: the formats available. With a `name`, the result is `name`,
`format`, `version` and `schema`, the JSON Schema Shape's readers validate that input with. `format`
is the document's own `format` value, `null` for a document that identifies itself by its version
only. The names come from one table, `shape.bridge.handlers.formats.FORMATS`, which maps every
schema file shipped in `shape/schemas`: `design-input`, `generation-schema`, `decisions`, `project`,
`model` (the Shape model v2), `model-v1`, `model-v1-ga` and `profile-engine`. A test fails when a
shipped schema file is not in the table.

Error: `input.unknown_format` (the message lists the names).

```json request
{"api_version": "1.1", "args": {}, "command": "format_schema", "id": "names"}
```

```json response
{"api_version": "1.2", "command": "format_schema", "id": "names", "ok": true, "result": {"names": ["decisions", "design-input", "generation-schema", "model", "model-v1", "model-v1-ga", "profile-engine", "project"]}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"name": "nope"}, "command": "format_schema", "id": "unknown-format"}
```

```json response
{"api_version": "1.2", "command": "format_schema", "error": {"code": "input.unknown_format", "group": "input", "hint": "call format_schema without a name for the list", "message": "no published format named 'nope' (formats: decisions, design-input, generation-schema, model, model-v1, model-v1-ga, profile-engine, project)"}, "id": "unknown-format", "ok": false, "warnings": []}
```


## Stored profiles, contracts and the leak scanner

### `profile_show`

Reads the `.shape` profile `path`, which is already on disk, without profiling the data again. Result:
`content_id`, `name`, `tables` (rows and columns per table), `summary` and `signed`, with the same
schema, the same spilling and the same protection of classified columns as `profile`'s result
(*Safe by default*); `provenance` when the file has it.

Errors: `input.not_found`, `input.invalid_schema` (not a `.shape` profile, or damaged),
`input.unsupported_format_version` (a profile of a newer format). Warnings: `artifact_not_verified`
(the file is not signed, or its signature was not verified), `result_in_file`.

```json request
{"api_version": "1.1", "args": {"path": "/work/a.shape"}, "command": "profile_show", "id": "a-profile"}
```

```json response
{"api_version": "1.2", "command": "profile_show", "id": "a-profile", "ok": true, "result": {"content_id": "<any>", "name": "a", "signed": false, "summary": "<any>", "tables": {"a": {"columns": 4, "rows": 40}}}, "warnings": [{"code": "artifact_not_verified", "message": "/work/a.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}]}
```

```json request
{"api_version": "1.1", "args": {"path": "/work/none.shape"}, "command": "profile_show", "id": "missing-file"}
```

```json response
{"api_version": "1.2", "command": "profile_show", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.shape"}, "id": "missing-file", "ok": false, "warnings": []}
```


### `contract_validate`

Validates a contract of the format `shape check` reads (`row_count`, `columns`, `required_columns`,
`fd`, ...), given as `path` or `text` (exactly one), with the code `shape check` validates it with.
Result: `valid`, `errors` (each with `location`, such as `columns.id`, and `message`) and `warnings`.
Every problem is listed, not only the first. Text that is not JSON is `valid: false`. The 1.0
`validate` command checks another kind of contract (`name` and `fields`) and is not changed.

Errors: `input.not_found`, `usage.invalid_argument` (neither or both of `path` and `text`).

```json request
{"api_version": "1.1", "args": {"path": "/work/contract_invalid.json"}, "command": "contract_validate", "id": "bad-rule"}
```

```json response
{"api_version": "1.2", "command": "contract_validate", "id": "bad-rule", "ok": true, "result": {"errors": [{"location": "columns.id", "message": "unknown rules for column 'id': ['colour']"}], "kind": "check-contract", "valid": false, "warnings": []}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"path": "/work/none.json"}, "command": "contract_validate", "id": "missing-file"}
```

```json response
{"api_version": "1.2", "command": "contract_validate", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.json"}, "id": "missing-file", "ok": false, "warnings": []}
```


### `safe_scan`

Runs the leak scanner of `shape.privacy.safe_validator` on a share-safe profile given as `path` (a
JSON file, or a `.shape` file) or as `text`, as `shape profile validate --safe` does. Result: `clean`
and `findings`, each with `rule`, `pointer` (where, as a JSON path) and `message`. **A finding's
message never holds the value it found** (it says that a value matches the `email` pattern, not which
value), and a pointer that is itself a personal-data value is shown as `<redacted>`, unless the
request sets `options.include_raw_values`.

Errors: `input.not_found`, `usage.invalid_argument` (neither or both of `path` and `text`). A file
that is not JSON is a finding (`malformed`), as on the command line. Warnings:
`artifact_not_verified` (a `.shape` file that is not signed).

```json request
{"api_version": "1.1", "args": {"path": "/work/leaky.json"}, "command": "safe_scan", "id": "leaks"}
```

```json response
{"api_version": "1.2", "command": "safe_scan", "id": "leaks", "ok": true, "result": {"clean": false, "findings": [{"message": "a numeric minimum and maximum pair: a raw minimum and maximum can identify a record", "pointer": "$.tables.people.columns.amount", "rule": "extreme-pair"}, {"message": "a value matches the email pattern", "pointer": "$.tables.people.columns.mail.example", "rule": "pii-regex"}, {"message": "a value matches the email pattern", "pointer": "$.tables.people.columns.mail.example", "rule": "pii-regex"}]}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"path": "/work/none.json"}, "command": "safe_scan", "id": "missing-file"}
```

```json response
{"api_version": "1.2", "command": "safe_scan", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.json"}, "id": "missing-file", "ok": false, "warnings": []}
```


## Comparison gates in `verify`

`verify` gains `source`: the real data the generated data was made from, a file or directory read
with the same `format` rules as `path`. It runs the memorization gate and, when the verify
configuration has a `utility` section, the utility gate, with the same pass results and exit
semantics as `shape verify --source`. A configuration that asks for either gate without a `source`
answers `input.invalid_value` with the CLI's message (`... give --source`); a `source` with no data
files is `input.invalid_value` too.

In 1.1 each gate in `gates` also carries `details`: the gate's counts, rates, distances and scores
(for example the memorization gate's `exact_match_rate` and nearest-neighbour distances, the utility
gate's `real_score`, `synthetic_score` and `retention`), **never a data value**: the extremes the
range gate saw (`actual_min`, `actual_max`) are left out. The result schema publishes `details` as an
object per gate. A `verify` served as 1.0 has no `details`.

```json request
{"api_version": "1.1", "args": {"config": "/work/memo.json", "path": "/work/people_copy", "source": "/work/people_real"}, "command": "verify", "id": "memorization-fails"}
```

```json response
{"api_version": "1.2", "command": "verify", "id": "memorization-fails", "ok": true, "result": {"gates": [{"details": {"fail_at": "CONFIDENTIAL", "min_nn_distance": null, "tables": {"people": {"columns": ["email", "name"], "exact_match_rate": 1.0, "nn_distance": {"columns": ["age"], "median": 0.0, "min": 0.0, "p05": 0.0, "rows_checked": 20}, "reproduced_row_indices": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19], "reproduced_rows": 20, "restricted": true, "rows": 20, "source_rows": 40}}}, "errors": ["people: 20 of 20 generated rows reproduce a source row on the CONFIDENTIAL+ columns [email, name] (rows 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, ...)"], "name": "memorization", "passed": false, "warnings": []}], "passed": false, "row_counts": {"people": 20}, "statistical": false}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"config": "/work/memo.json", "path": "/work/people_new"}, "command": "verify", "id": "needs-the-source"}
```

```json response
{"api_version": "1.2", "command": "verify", "error": {"code": "input.invalid_value", "group": "input", "hint": null, "message": "the verify configuration asks for the memorization or utility gate, which compare with the source data: give --source"}, "id": "needs-the-source", "ok": false, "warnings": []}
```


## What's new in 1.2

Bridge 1.2 adds the analysis commands of the open engine, so an editor, a notebook or an agent no
longer scrapes terminal text, and drift with severity between two share-safe versions in a registry.
It changes nothing a 1.0 or 1.1 client sees (see *The 1.0 promise* and *The 1.1 promise*).

- **Report card**: `report_card` and `report_card_read`.
- **Rule testing and suggestion**: `rules_mutate`, `rules_backtest`, the kind `rule` of
  `proposals_propose` (and the statuses and kinds `stale` and `rule` of `proposals_list`), and
  `proposals_contract`.
- **History**: `bisect`, `bisect_layers` and `timelapse`.
- **Registry drift**: `registry_diff`, and `shape registry ROOT diff` for two safe forms
  (`docs/REGISTRY.md`).
- **Chaos and suites**: `chaos`, with the input check of `shape chaos`, and `suite_list` and
  `suite_run`, the named scenario suites of the starter library.
- New error codes `input.contract_conflict` and `policy.unverified_input`, the warning code
  `real_input_corrupted`, and `format_schema` names for the new formats (`mutation-plan`,
  `mutation-report`, `incidents`, `backtest-report`; `decisions` is version 2 for a 1.2 request).
- Every new command carries `effects` and its path arguments `x-path`; `chaos`, `suite_run`,
  `proposals_contract` and `report_card` (with `output`) list `writes_files`.

**The 1.1 promise.** A request that declares `"api_version": "1.1"` (or `"1.0"`) is answered exactly
as that version answers it: a 1.2 command is `usage.unknown_command` (the hint names `api_version
1.2`), the 1.2 values of an enumeration (`rule` in `kinds`, `stale` in `status`) are refused with the
message 1.1 gave, `proposals_list` and `proposals_propose` read decision files of version 1 only,
`format_schema` lists the names 1.1 listed, and no 1.2 result field or warning is added. The 1.1
contract is **frozen** in [`docs/bridge/schema/1.1/`](bridge/schema/1.1/index.json) and
[`docs/bridge/vectors/1.1/`](bridge/vectors/1.1); `tests/bridge/test_compat_1_1.py` replays every 1.1
vector against the 1.2 bridge and holds the 1.2 schemas to the 1.1 ones, next to the 1.0 replay.
The job file `shape-bridge-job` stays at version 1, and a job written by the 1.1 bridge is read by
the 1.2 bridge.

Each new command below has its request, result, an example request and response (taken from the
published vectors, with `/work` for the scratch folder), its error codes and its warnings.

## Report card

### `report_card`

Runs what `shape report-card` runs (`shape.quality.report_card`): one card for a synthetic dataset,
with its fidelity scores and tiers, the utility gate, the memorization gate and, with `holdout`, the
membership-inference test. `real`, `synthetic` and `holdout` are each a data file or a folder with one
file per table; `config` is a verify configuration file (its `utility` section drives the utility
gate); `tiers` the fidelity tiers to run, of `1` and `2` (default both); `manifest` the run manifest
of the generation (adds its reproducibility tuple and dataset id); `require` the sections that must
have run (`fidelity`, `utility`, `privacy`: one that was not run fails the card); `output` also writes
the card to a file as JSON. Job-capable.

Result: the `shape-report-card` document, as `shape report-card --json` prints it: `format`,
`version`, `inputs`, `sections` (`fidelity`, `utility`, `privacy`, each with its tests and what was
not run), `overall` (`pass` or `fail`) and `overall_reasons`. A card whose `overall` is `fail` is a
result, not an error. The result is spillable as a whole (*Large results*): a card above
`max_inline_bytes` is a file reference. **The card holds no value from the real data**: its
sections are scores, rates, counts and distances. A test searches the response and the written
card for every value of the real fixture.

Errors: `input.not_found` (a data, holdout, manifest or configuration path), `input.invalid_value`
(no data file in a folder, no table in common, a tier that is not 1 or 2, a missing scikit-learn when
`require` names `utility`), `input.invalid_schema` (a configuration that is not valid),
`io.write_failed` (`output`), `usage.invalid_argument`. Warnings: `result_in_file`.

```json request
{"api_version": "1.2", "args": {"output": "/work/card_written.json", "real": "/work/report_real", "synthetic": "/work/report_synthetic", "tiers": [1]}, "command": "report_card", "id": "written-too"}
```

```json response
{"api_version": "1.2", "command": "report_card", "id": "written-too", "ok": true, "result": {"format": "shape-report-card", "inputs": {"holdout": null, "manifest": null, "real": {"dataset_id": "sha256:ed4a40e81d1bf97fe55bbfd1f90a760202a89de3af22461c9dfe4cb300673a65", "tables": {"customers": 120}}, "synthetic": {"dataset_id": "sha256:8f05650b7387206b60a4b0ebb4203176f533b1994b953334a76aa057cacef64f", "tables": {"customers": 120}}}, "overall": "fail", "overall_reasons": ["fidelity: failed (overall_score, table_score (customers))"], "require": [], "sections": {"fidelity": {"gates": [{"name": "overall_score", "relation": ">=", "status": "fail", "threshold": 85.0, "value": 65.2940258744698}, {"name": "table_score", "relation": ">=", "status": "fail", "table": "customers", "threshold": 70.0, "value": 65.2940258744698}, {"name": "coverage", "status": "pass"}, {"name": "tier1_adversarial_auc", "reason": "adversarial test skipped: scikit-learn is not installed (pip install 'sqllocks-shape[advanced]')", "status": "not_run", "table": "customers"}], "metrics": {"extra_tables": [], "issues": [], "missing_tables": [], "overall_score": 65.2940258744698, "tables": {"customers": {"columns": {"amount": {"cardinality_ratio": 1.0, "chi2_pvalue": null, "chi2_statistic": null, "dtype_match": true, "ks_statistic": 0.10833333333333335, "mean_delta": 0.00690209511738474, "null_rate_delta": 0.0, "present": true, "score": 95.85788447438982, "std_ratio": 1.16781056322461, "value_overlap": null}, "customer_id": {"cardinality_ratio": 1.0, "chi2_pvalue": null, "chi2_statistic": null, "dtype_match": true, "ks_statistic": 1.0, "mean_delta": 22794.095739341778, "null_rate_delta": 0.0, "present": true, "score": 44.89795918367348, "std_ratio": 0.14285714285714288, "value_overlap": null}, "email": {"cardinality_ratio": 1.0, "chi2_pvalue": 0.0, "chi2_statistic": 1199999999880.0, "dtype_match": true, "ks_statistic": null, "mean_delta": null, "null_rate_delta": 0.0, "present": true, "score": 42.857142857142854, "std_ratio": null, "value_overlap": 0.0}, "full_name": {"cardinality_ratio": 1.0, "chi2_pvalue": 0.0, "chi2_statistic": 1199999999880.0, "dtype_match": true, "ks_statistic": null, "mean_delta": null, "null_rate_delta": 0.0, "present": true, "score": 42.857142857142854, "std_ratio": null, "value_overlap": 0.0}, "tier": {"cardinality_ratio": 1.0, "chi2_pvalue": 0.8394570207692074, "chi2_statistic": 0.35, "dtype_match": true, "ks_statistic": null, "mean_delta": null, "null_rate_delta": 0.0, "present": true, "score": 100.0, "std_ratio": null, "value_overlap": 1.0}}, "extra_columns": [], "issues": [], "missing_columns": [], "present": true, "row_count_real": 120, "row_count_synth": 120, "score": 65.2940258744698}}, "thresholds": {"min_column": null, "min_overall": 85.0, "min_table": 70.0}, "tier1": {"customers": {"adversarial": null, "adversarial_reason": "adversarial test skipped: scikit-learn is not installed (pip install 'sqllocks-shape[advanced]')", "conditional_profiles": 1, "mixture_fit_columns": [], "periodic_columns": ["amount"], "temporal_columns": []}}}, "notes": [], "status": "fail"}, "privacy": {"gates": [{"name": "memorization", "status": "pass", "value": 0.0}, {"name": "membership_inference", "reason": "no holdout was given (--holdout): membership inference needs real rows that were not given to the generator", "status": "not_run"}], "metrics": {"membership_inference": {"reason": "no holdout was given (--holdout): membership inference needs real rows that were not given to the generator", "status": "not_run"}, "memorization": {"fail_at": "CONFIDENTIAL", "min_nn_distance": null, "tables": {"customers": {"columns": ["customer_id", "email", "full_name", "tier"], "exact_match_rate": 0.0, "nn_distance": {"columns": ["amount"], "median": 0.008302127409569371, "min": 0.0002591041064102795, "p05": 0.0007514019085912538, "rows_checked": 120}, "reproduced_row_indices": [], "reproduced_rows": 0, "restricted": false, "rows": 120, "source_rows": 120}}}}, "notes": ["customers: no column is classified CONFIDENTIAL or above, so reproduced rows are reported but cannot fail the gate (set \"classifications\" in the verify configuration)"], "status": "pass"}, "utility": {"gates": [{"name": "utility_retention", "reason": "no verify configuration was given (--config), so there is no `utility` section", "status": "not_run"}], "metrics": {}, "notes": [], "reason": "no verify configuration was given (--config), so there is no `utility` section", "status": "not_run"}}, "shape_version": "<any>", "version": 1}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"real": "/work/none", "synthetic": "/work/report_synthetic"}, "command": "report_card", "id": "missing-real-data"}
```

```json response
{"api_version": "1.2", "command": "report_card", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "Path not found: /work/none"}, "id": "missing-real-data", "ok": false, "warnings": []}
```

### `report_card_read`

Reads a stored card (`path`) after checking its `format` and `version`, and returns the document as
`report_card` does (spillable). Errors: `input.not_found`, `input.invalid_schema` (not JSON, not a
`shape-report-card`, no `version`), `input.unsupported_format_version` (a card a newer Shape wrote).

```json request
{"api_version": "1.2", "args": {"path": "/work/card.json"}, "command": "report_card_read", "id": "stored-card"}
```

```json response
{"api_version": "1.2", "command": "report_card_read", "id": "stored-card", "ok": true, "result": {"format": "shape-report-card", "inputs": {"holdout": null, "manifest": null, "real": {"dataset_id": "sha256:ed4a40e81d1bf97fe55bbfd1f90a760202a89de3af22461c9dfe4cb300673a65", "tables": {"customers": 120}}, "synthetic": {"dataset_id": "sha256:8f05650b7387206b60a4b0ebb4203176f533b1994b953334a76aa057cacef64f", "tables": {"customers": 120}}}, "overall": "fail", "overall_reasons": ["fidelity: failed (overall_score, table_score (customers))"], "require": [], "sections": {"fidelity": {"gates": [{"name": "overall_score", "relation": ">=", "status": "fail", "threshold": 85.0, "value": 65.2940258744698}, {"name": "table_score", "relation": ">=", "status": "fail", "table": "customers", "threshold": 70.0, "value": 65.2940258744698}, {"name": "coverage", "status": "pass"}, {"name": "tier1_adversarial_auc", "reason": "adversarial test skipped: scikit-learn is not installed (pip install 'sqllocks-shape[advanced]')", "status": "not_run", "table": "customers"}, {"name": "tier2_pass_rate", "relation": ">=", "status": "pass", "table": "customers", "threshold": 1.0, "value": 1.0}], "metrics": {"extra_tables": [], "issues": [], "missing_tables": [], "overall_score": 65.2940258744698, "tables": {"customers": {"columns": {"amount": {"cardinality_ratio": 1.0, "chi2_pvalue": null, "chi2_statistic": null, "dtype_match": true, "ks_statistic": 0.10833333333333335, "mean_delta": 0.00690209511738474, "null_rate_delta": 0.0, "present": true, "score": 95.85788447438982, "std_ratio": 1.16781056322461, "value_overlap": null}, "customer_id": {"cardinality_ratio": 1.0, "chi2_pvalue": null, "chi2_statistic": null, "dtype_match": true, "ks_statistic": 1.0, "mean_delta": 22794.095739341778, "null_rate_delta": 0.0, "present": true, "score": 44.89795918367348, "std_ratio": 0.14285714285714288, "value_overlap": null}, "email": {"cardinality_ratio": 1.0, "chi2_pvalue": 0.0, "chi2_statistic": 1199999999880.0, "dtype_match": true, "ks_statistic": null, "mean_delta": null, "null_rate_delta": 0.0, "present": true, "score": 42.857142857142854, "std_ratio": null, "value_overlap": 0.0}, "full_name": {"cardinality_ratio": 1.0, "chi2_pvalue": 0.0, "chi2_statistic": 1199999999880.0, "dtype_match": true, "ks_statistic": null, "mean_delta": null, "null_rate_delta": 0.0, "present": true, "score": 42.857142857142854, "std_ratio": null, "value_overlap": 0.0}, "tier": {"cardinality_ratio": 1.0, "chi2_pvalue": 0.8394570207692074, "chi2_statistic": 0.35, "dtype_match": true, "ks_statistic": null, "mean_delta": null, "null_rate_delta": 0.0, "present": true, "score": 100.0, "std_ratio": null, "value_overlap": 1.0}}, "extra_columns": [], "issues": [], "missing_columns": [], "present": true, "row_count_real": 120, "row_count_synth": 120, "score": 65.2940258744698}}, "thresholds": {"min_column": null, "min_overall": 85.0, "min_table": 70.0}, "tier1": {"customers": {"adversarial": null, "adversarial_reason": "adversarial test skipped: scikit-learn is not installed (pip install 'sqllocks-shape[advanced]')", "conditional_profiles": 1, "mixture_fit_columns": [], "periodic_columns": ["amount"], "temporal_columns": []}}, "tier2": {"customers": {"anomaly_rate": null, "cardinality": {"amount": {"column": "amount", "deviation": 0.0, "passed": true, "ratio": 1.0, "real_cardinality": 120, "synth_cardinality": 120}, "customer_id": {"column": "customer_id", "deviation": 0.0, "passed": true, "ratio": 1.0, "real_cardinality": 120, "synth_cardinality": 120}, "email": {"column": "email", "deviation": 0.0, "passed": true, "ratio": 1.0, "real_cardinality": 120, "synth_cardinality": 120}, "full_name": {"column": "full_name", "deviation": 0.0, "passed": true, "ratio": 1.0, "real_cardinality": 120, "synth_cardinality": 120}, "tier": {"column": "tier", "deviation": 0.0, "passed": true, "ratio": 1.0, "real_cardinality": 3, "synth_cardinality": 3}}, "format_preservation": {"email": {"column": "email", "delta": 0.0, "detected_format": "email", "passed": true, "real_format_rate": 1.0, "synth_format_rate": 1.0}}, "passing_rate": 1.0, "string_similarity": {"customer_id": {"column": "customer_id", "cosine_similarity": 0.014885889194962794, "ngram_n": 3, "score": 1.49}, "email": {"column": "email", "cosine_similarity": 0.35661379234708135, "ngram_n": 3, "score": 35.66}, "full_name": {"column": "full_name", "cosine_similarity": 0.0008334831433326548, "ngram_n": 3, "score": 0.08}, "tier": {"column": "tier", "cosine_similarity": 0.9417932051409833, "ngram_n": 3, "score": 94.18}}}}}, "notes": [], "status": "fail"}, "privacy": {"gates": [{"name": "memorization", "status": "pass", "value": 0.0}, {"name": "membership_inference", "reason": "no holdout was given (--holdout): membership inference needs real rows that were not given to the generator", "status": "not_run"}], "metrics": {"membership_inference": {"reason": "no holdout was given (--holdout): membership inference needs real rows that were not given to the generator", "status": "not_run"}, "memorization": {"fail_at": "CONFIDENTIAL", "min_nn_distance": null, "tables": {"customers": {"columns": ["customer_id", "email", "full_name", "tier"], "exact_match_rate": 0.0, "nn_distance": {"columns": ["amount"], "median": 0.008302127409569371, "min": 0.0002591041064102795, "p05": 0.0007514019085912538, "rows_checked": 120}, "reproduced_row_indices": [], "reproduced_rows": 0, "restricted": false, "rows": 120, "source_rows": 120}}}}, "notes": ["customers: no column is classified CONFIDENTIAL or above, so reproduced rows are reported but cannot fail the gate (set \"classifications\" in the verify configuration)"], "status": "pass"}, "utility": {"gates": [{"name": "utility_retention", "reason": "no verify configuration was given (--config), so there is no `utility` section", "status": "not_run"}], "metrics": {}, "notes": [], "reason": "no verify configuration was given (--config), so there is no `utility` section", "status": "not_run"}}, "shape_version": "<any>", "version": 1}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"path": "/work/card_newer.json"}, "command": "report_card_read", "id": "newer-card"}
```

```json response
{"api_version": "1.2", "command": "report_card_read", "error": {"code": "input.unsupported_format_version", "group": "input", "hint": "upgrade Shape to read it", "message": "/work/card_newer.json is a report card of version 2, which is newer than the version 1 this Shape reads"}, "id": "newer-card", "ok": false, "warnings": []}
```

## Rule testing and suggestion

`rules_mutate` and `rules_backtest` call `shape rules mutate` and `shape rules backtest`
(`shape.rules`, `docs/RULES_TESTING.md`) and return the reports `--json` prints. Neither reports a
value of the data: a mutant is named by its corruption, table and column, and a backtest entry by its
date and the rules that failed.

### `rules_mutate`

Plants the corruptions of `shape chaos` in `data` one at a time, checks each mutant against
`contract`, and reports which rules killed which mutants. `plan` is a `shape-mutation-plan` file
(default: every applicable corruption of every table and column); `seed` (default 0; the same seed
gives the same report); `rate` the share of rows each mutant changes (0 to 1, default 0.05);
`min_score` (0 to 1) adds `min_score` (`required` and `met`) to the report, as the gate of
`shape rules mutate --min-score`; the report is otherwise unchanged. Job-capable and **cancellable
between mutants**: a cancelled job is `cancelled` and its `result` counts what ran
(`mutants_run`, `killed`, `survived`, `not_applicable`); `progress` has `mutants_run` and
`mutants_total`.

Result: the `shape-mutation-report`: `score` (overall, `by_kind`, `by_table`), `mutants`, `rules` and
`rules_killed_none`. Spillable as a whole.

Errors: `input.not_found` (data, contract or plan), `input.invalid_value` (a contract or a plan that
is not valid, data with nothing to corrupt), `input.unsupported_format_version` (a plan a newer Shape
wrote), `usage.invalid_argument` (a `rate` or `min_score` outside 0 to 1).

```json request
{"api_version": "1.2", "args": {"contract": "/work/contract_customers.json", "data": "/work/mutate_data", "min_score": 0.5, "seed": 3}, "command": "rules_mutate", "id": "customers"}
```

```json response
{"api_version": "1.2", "command": "rules_mutate", "id": "customers", "ok": true, "result": {"baseline_failed_rules": [], "diff": false, "format": "shape-mutation-report", "min_score": {"met": true, "required": 0.5}, "mutants": [{"cells_changed": 6, "column": null, "id": "duplicates.customers", "killed": true, "killed_by": ["customers.customer_id.unique"], "kind": "duplicates", "rate": 0.05, "seed": 3, "status": "killed", "table": "customers"}, {"cells_changed": 6, "column": "amount", "id": "negative_amounts.customers.amount", "killed": true, "killed_by": ["customers.amount.min"], "kind": "negative_amounts", "rate": 0.05, "seed": 3, "status": "killed", "table": "customers"}, {"cells_changed": 6, "column": "tier", "id": "case_whitespace.customers.tier", "killed": true, "killed_by": ["customers.tier.allowed_values"], "kind": "case_whitespace", "rate": 0.05, "seed": 3, "status": "killed", "table": "customers"}, {"cells_changed": 6, "column": "full_name", "id": "pii_fill.customers.full_name", "killed": false, "killed_by": [], "kind": "pii_fill", "rate": 0.05, "seed": 3, "status": "survived", "table": "customers"}, {"cells_changed": 6, "column": "email", "id": "pii_fill.customers.email", "killed": false, "killed_by": [], "kind": "pii_fill", "rate": 0.05, "seed": 3, "status": "survived", "table": "customers"}, {"cells_changed": 6, "column": "tier", "id": "pii_fill.customers.tier", "killed": true, "killed_by": ["customers.tier.allowed_values"], "kind": "pii_fill", "rate": 0.05, "seed": 3, "status": "killed", "table": "customers"}, {"cells_changed": 120, "column": "amount", "id": "type_change.customers.amount", "killed": true, "killed_by": ["customers.amount.min"], "kind": "type_change", "rate": 0.05, "seed": 3, "status": "killed", "table": "customers"}, {"cells_changed": 6, "column": "full_name", "id": "null_creep.customers.full_name", "killed": false, "killed_by": [], "kind": "null_creep", "rate": 0.05, "seed": 3, "status": "survived", "table": "customers"}, {"cells_changed": 6, "column": "email", "id": "null_creep.customers.email", "killed": true, "killed_by": ["customers.email.nullable"], "kind": "null_creep", "rate": 0.05, "seed": 3, "status": "killed", "table": "customers"}, {"cells_changed": 6, "column": "amount", "id": "null_creep.customers.amount", "killed": false, "killed_by": [], "kind": "null_creep", "rate": 0.05, "seed": 3, "status": "survived", "table": "customers"}, {"cells_changed": 6, "column": "tier", "id": "null_creep.customers.tier", "killed": false, "killed_by": [], "kind": "null_creep", "rate": 0.05, "seed": 3, "status": "survived", "table": "customers"}], "rate": 0.05, "rules": {"customers.amount.min": {"killed": ["negative_amounts.customers.amount", "type_change.customers.amount"]}, "customers.customer_id.dtype": {"killed": []}, "customers.customer_id.unique": {"killed": ["duplicates.customers"]}, "customers.email.nullable": {"killed": ["null_creep.customers.email"]}, "customers.row_count.min": {"killed": []}, "customers.tier.allowed_values": {"killed": ["case_whitespace.customers.tier", "pii_fill.customers.tier"]}}, "rules_killed_none": ["customers.row_count.min", "customers.customer_id.dtype"], "score": {"by_kind": {"case_whitespace": {"applicable": 1, "killed": 1, "score": 1.0}, "duplicates": {"applicable": 1, "killed": 1, "score": 1.0}, "negative_amounts": {"applicable": 1, "killed": 1, "score": 1.0}, "null_creep": {"applicable": 4, "killed": 1, "score": 0.25}, "pii_fill": {"applicable": 3, "killed": 1, "score": 0.333333}, "type_change": {"applicable": 1, "killed": 1, "score": 1.0}}, "by_table": {"customers": {"applicable": 11, "killed": 6, "score": 0.545455}}, "overall": {"applicable": 11, "killed": 6, "score": 0.545455}}, "seed": 3, "version": 1}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"contract": "/work/none.json", "data": "/work/mutate_data"}, "command": "rules_mutate", "id": "missing-contract"}
```

```json response
{"api_version": "1.2", "command": "rules_mutate", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "contract not found: /work/none.json"}, "id": "missing-contract", "ok": false, "warnings": []}
```

### `rules_backtest`

Replays `contract` over every committed version of `name` in the registry `registry`, oldest first
by business date. `since` and `until` (dates, inclusive) limit the versions; `window` is `day`
(default), `week` or `month` (the versions of a window are merged first: profiles need sketches);
`incidents` is a `shape-incidents` file that scores the replay (caught, missed, alarms outside
incidents); `compare` an older contract to run beside it. A rule the stored form cannot evaluate (a
share-safe version withholds the extremes) is `not_measured`, never a pass. Job-capable.

Result: the `shape-backtest-report`: `summary`, `entries` (per version or window: `status`,
`failed_rules`, `not_measured_rules`), `rules`, and, when asked, `incidents`,
`alarms_outside_incidents` and `compare`. Spillable as a whole.

Errors: `input.not_found` (registry, contract, incidents or compare), `input.invalid_value` (a folder
that is not a registry, a name with no versions, a contract that is not valid, a date that is not a
date, `until` before `since`), `input.unsupported_format_version` (an incidents file a newer Shape
wrote), `usage.invalid_argument` (`window`).

```json request
{"api_version": "1.2", "args": {"contract": "/work/contract_feed.json", "name": "orders", "registry": "/work/feed_safe"}, "command": "rules_backtest", "id": "safe-history"}
```

```json response
{"api_version": "1.2", "command": "rules_backtest", "id": "safe-history", "ok": true, "result": {"entries": [{"failed_rules": [], "first_date": "2026-03-01", "from": "2026-03-01", "id": "2026-03-01@828f07c958fc", "last_date": "2026-03-01", "not_measured_rules": ["orders.status.allowed_values", "orders.amount.max"], "rules": {"failed": 0, "not_measured": 2, "passed": 2}, "status": "not_measured", "to": "2026-03-01", "versions": ["828f07c958fc361fb5c346b8b284ad934723bdd9ddec5d135b132d5e4a1dc206"], "window": "day"}, {"failed_rules": [], "first_date": "2026-03-02", "from": "2026-03-02", "id": "2026-03-02@07346306bd7f", "last_date": "2026-03-02", "not_measured_rules": ["orders.status.allowed_values", "orders.amount.max"], "rules": {"failed": 0, "not_measured": 2, "passed": 2}, "status": "not_measured", "to": "2026-03-02", "versions": ["07346306bd7f160ba38d8297b4ca53fc7165f806f4a6ab0e87a3cc1dccc9d671"], "window": "day"}, {"failed_rules": [], "first_date": "2026-03-03", "from": "2026-03-03", "id": "2026-03-03@b5ad07c8668e", "last_date": "2026-03-03", "not_measured_rules": ["orders.status.allowed_values", "orders.amount.max"], "rules": {"failed": 0, "not_measured": 2, "passed": 2}, "status": "not_measured", "to": "2026-03-03", "versions": ["b5ad07c8668e46bf1bef17989f8098a03b5de9696814b73ca4e7e956e29b3c3a"], "window": "day"}, {"failed_rules": ["orders.note.max_null_rate"], "first_date": "2026-03-04", "from": "2026-03-04", "id": "2026-03-04@f1335cd0f6d8", "last_date": "2026-03-04", "not_measured_rules": ["orders.status.allowed_values", "orders.amount.max"], "rules": {"failed": 1, "not_measured": 2, "passed": 1}, "status": "fail", "to": "2026-03-04", "versions": ["f1335cd0f6d8c147172ef1d91e3c933561b7e2831c404c449324e02f12a39870"], "window": "day"}, {"failed_rules": ["orders.note.max_null_rate"], "first_date": "2026-03-05", "from": "2026-03-05", "id": "2026-03-05@0a356ce29932", "last_date": "2026-03-05", "not_measured_rules": ["orders.status.allowed_values", "orders.amount.max"], "rules": {"failed": 1, "not_measured": 2, "passed": 1}, "status": "fail", "to": "2026-03-05", "versions": ["0a356ce2993297a3755fc78bb3a279d97faea7ba29d5684306acfd5d679ea766"], "window": "day"}, {"failed_rules": ["orders.note.max_null_rate"], "first_date": "2026-03-06", "from": "2026-03-06", "id": "2026-03-06@13adcc218bb0", "last_date": "2026-03-06", "not_measured_rules": ["orders.status.allowed_values", "orders.amount.max"], "rules": {"failed": 1, "not_measured": 2, "passed": 1}, "status": "fail", "to": "2026-03-06", "versions": ["13adcc218bb05558bc4fabed8ffa2f40f3f19cf1fc0ff5d622e12cf5f73bb312"], "window": "day"}], "format": "shape-backtest-report", "name": "orders", "rules": {"orders.amount.max": {"failed": 0, "not_measured": 6}, "orders.note.max_null_rate": {"failed": 3, "not_measured": 0}, "orders.status.allowed_values": {"failed": 0, "not_measured": 6}}, "since": null, "summary": "<any>", "until": null, "version": 1, "window": "day"}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"contract": "/work/contract_feed.json", "name": "ghost", "registry": "/work/feed_safe"}, "command": "rules_backtest", "id": "unknown-name"}
```

```json response
{"api_version": "1.2", "command": "rules_backtest", "error": {"code": "input.invalid_value", "group": "input", "hint": null, "message": "nothing is recorded for 'ghost' (names in this registry: orders)"}, "id": "unknown-name", "ok": false, "warnings": []}
```

### `proposals_propose`, `proposals_list` and `proposals_decide` with rules (1.2)

`rule` is a kind of `proposals_propose` (`kinds: ["rule"]`): it proposes contract rules from the
profile, each a proposal `rule:TABLE.COLUMN.RULE` whose `claim` is the exact contract fragment. It is
proposed only when asked for. A decision file with rule proposals is version 2; a request served as
1.1 reads version 1 only. The result of a run that asked for `rule` adds `stale`: the ids of accepted
rules the run no longer supports (they keep their claim, and `proposals_list` shows them with the
status `stale`). `proposals_list` accepts `rule` as `kind` and `stale` as `status`. Only this
enumeration grew: the arguments are those of 1.1.

A rule's `claim` is returned as it is: the engine keeps the rules of a personal-data column
value-free, and a claim is what the contract will say. Its `evidence` is withheld as for any
proposal (*Safe by default*).

### `proposals_contract`

Writes the accepted, non-stale rule proposals of the decision file `decisions` as a contract that
`check` reads, to `output` (what `shape proposals contract` writes, byte for byte). `merge` is an
existing contract file to add the rules to (left unchanged). Result: `written` (the path) and
`rules` (how many accepted rules). Not a job.

Errors: `input.contract_conflict` (an accepted rule disagrees with a rule of `merge`: the message
names both, for example `rule:orders.row_count conflicts with orders.row_count.min in /work/base.json`;
nothing is written), `input.invalid_value` (no accepted rule), `input.not_found`,
`input.invalid_schema` (a decision file or merge file that is not valid),
`input.unsupported_format_version`, `io.write_failed`.

```json request
{"api_version": "1.2", "args": {"decisions": "/work/rules.json", "output": "/work/contract_rules.json"}, "command": "proposals_contract", "id": "accepted-rules"}
```

```json response
{"api_version": "1.2", "command": "proposals_contract", "id": "accepted-rules", "ok": true, "result": {"rules": 3, "written": "/work/contract_rules.json"}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"decisions": "/work/rules.json", "merge": "/work/contract_conflicting.json", "output": "/work/contract_conflict.json"}, "command": "proposals_contract", "id": "conflict"}
```

```json response
{"api_version": "1.2", "command": "proposals_contract", "error": {"code": "input.contract_conflict", "group": "input", "hint": null, "message": "rule:orders.row_count conflicts with orders.row_count.min in /work/contract_conflicting.json"}, "id": "conflict", "ok": false, "warnings": []}
```

## History

`bisect`, `bisect_layers` and `timelapse` call `shape bisect`, `shape bisect layers` and `shape
timelapse` (`shape.versions`, `docs/HISTORY.md`) and return the `to_dict()` of each result. All three
are job-capable. The bridge never looks for a `shape.yml` on its own: `project` names it.

**Safe by default.** A registry version that is a full profile holds real values, so the values of a
classified column (*Safe by default*) are withheld unless the request sets
`options.include_raw_values`: a change's `before` and `after` are `null` with `"redacted": true`, and
a timelapse frame's `top_values` have `value` set to `null` and the frame is `"redacted": true`.
A version stored in its share-safe form holds only what its safe form holds, and nothing more is
withheld.

### `bisect`

A binary search over the versions of `name` in `registry`, between the refs `good` and `bad` (a tag
or a content id), for the first version that tests bad. The test is `shape diff` against the good
version, under the thresholds and ignore lists of the `project` source (`source` selects one; the
only source, or the one named `name`, applies otherwise); `column` and `kind` restrict which changes
count; `contract` makes "the contract fails" the test; `verify_all` tests every version and reports
any that flips back to good; `coarse` (`week` or `month`) bisects over merged windows first.

Result: `found`, `first_bad` and `last_good` (`ref`, `content_id`, `business_date`), `changes`,
`candidates`, `evaluated`, `evaluations`, `cost`, `flips` and `warnings`.

Errors: `input.not_found` (registry, contract or project), `input.invalid_value` (a name or ref that
is not in the registry, a `good` that tests bad, a `bad` that tests good, a version that cannot be
tested: a share-safe one cannot be diffed or checked, `coarse` with `contract` or `verify_all`),
`input.unknown_source`, `usage.invalid_argument` (`source` without `project`, `coarse`).

```json request
{"api_version": "1.2", "args": {"bad": "last", "good": "first", "name": "orders", "registry": "/work/feed_raw"}, "command": "bisect", "id": "first-bad-version"}
```

```json response
{"api_version": "1.2", "command": "bisect", "id": "first-bad-version", "ok": true, "result": {"bad": {"business_date": "2026-03-06", "content_id": "<any>", "ref": "last"}, "candidates": 5, "changes": [{"after": null, "before": null, "column": "amount", "kind": "mean_shift", "redacted": true, "score": 0.9448, "severity": "medium"}, {"after": null, "before": null, "column": "amount", "kind": "spread_change", "redacted": true, "score": 0.9054, "severity": "medium"}, {"after": null, "before": null, "column": "amount", "kind": "distribution_shift", "redacted": true, "score": 0.9179, "severity": "medium"}, {"after": null, "before": null, "column": "amount", "kind": "range_change", "redacted": true, "score": 0.9711, "severity": "low"}, {"after": 0.375, "before": 0.01, "column": "note", "kind": "null_rate_change", "score": 0.365, "severity": "medium"}], "changes_vs_good": [{"after": null, "before": null, "column": "amount", "kind": "mean_shift", "redacted": true, "score": 0.9491, "severity": "medium"}, {"after": null, "before": null, "column": "amount", "kind": "spread_change", "redacted": true, "score": 0.9124, "severity": "medium"}, {"after": null, "before": null, "column": "amount", "kind": "distribution_shift", "redacted": true, "score": 0.9184, "severity": "medium"}, {"after": null, "before": null, "column": "amount", "kind": "range_change", "redacted": true, "score": 0.9732, "severity": "low"}, {"after": 0.375, "before": 0.005, "column": "note", "kind": "null_rate_change", "score": 0.37, "severity": "medium"}], "cost": {"full_profile_tests": 3, "versions_read": 4, "window_tests": 0}, "evaluated": 3, "evaluations": [{"bad": true, "business_date": "2026-03-06", "content_id": "<any>"}, {"bad": false, "business_date": "2026-03-03", "content_id": "<any>"}, {"bad": true, "business_date": "2026-03-04", "content_id": "<any>"}], "first_bad": {"business_date": "2026-03-04", "content_id": "<any>", "ref": "039e00ed66d4464ea5f80b5bcf83812f12991ecebf98f02f7411bdf594c01c43"}, "flips": [], "format": "shape-bisect", "found": true, "good": {"business_date": "2026-03-01", "content_id": "<any>", "ref": "first"}, "last_good": {"business_date": "2026-03-03", "content_id": "<any>", "ref": "ed863798d227fb0eb5a2c57d4518f8976bf5a238ec76f2e18512368e8f0ca66e"}, "max_evaluations": 5, "mode": "bisect", "name": "orders", "test": {"change_kind": null, "column": null, "contract": null, "kind": "diff", "source": null}, "version": 1, "warnings": []}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"bad": "first", "good": "last", "name": "orders", "registry": "/work/feed_raw"}, "command": "bisect", "id": "good-tests-bad"}
```

```json response
{"api_version": "1.2", "command": "bisect", "error": {"code": "input.invalid_value", "group": "input", "hint": null, "message": "--good (last, 2026-03-06) is not older than --bad (first, 2026-03-01): versions are ordered by business_date, else by commit time"}, "id": "good-tests-bad", "ok": false, "warnings": []}
```

### `bisect_layers`

Finds the layer of a pipeline where a change first appears. `layers` are sources of the project file
`project` in pipeline order (each source's baseline names its registry and name); the version at
`bad_date` is diffed against the version at `good_date` (the newest on or before each) for every
layer. `column` names the column in canonical terms and `map` (`{"LAYER.COL": "COL"}`) maps a layer's
renamed column to it. `project` is required: the layers are its sources.

Result: `found`, `first_layer`, `persists`, `disappears` and `layers` (each with `status` of
`origin`, `persists`, `disappears` or `unchanged`, `changes` and `columns`). No layer showing the
change is a result (`found` false).

Errors: `input.invalid_value` (no `project`, no layer, a layer that is not a source, a source with no
baseline, dates that are not dates or not in order, a malformed `map`), `input.not_found`,
`input.invalid_schema` (a project file that is not valid), `policy.capability_unavailable` (PyYAML
is not installed).

```json request
{"api_version": "1.2", "args": {"bad_date": "2026-03-03", "good_date": "2026-03-01", "layers": ["raw", "clean"], "project": "/work/layers/shape.yml"}, "command": "bisect_layers", "id": "second-layer"}
```

```json response
{"api_version": "1.2", "command": "bisect_layers", "id": "second-layer", "ok": true, "result": {"bad_date": "2026-03-03", "column": null, "disappears": [], "first_layer": "clean", "format": "shape-bisect-layers", "found": true, "good_date": "2026-03-01", "layers": [{"bad": {"business_date": "2026-03-03", "content_id": "<any>", "ref": "86d972bbbd4b7eabef1e87e4a2e1a7aa9096bb7f12eef4ff4ee7c4def45c89e6"}, "changed": false, "changes": [], "columns": [], "good": {"business_date": "2026-03-01", "content_id": "<any>", "ref": "f96e3ed913d136efc72d72f3c62fa8c7f23d29521fc02513b09d1f4b5772ca12"}, "name": "layer_raw", "registry": "/work/layers/reg", "source": "raw", "status": "unchanged"}, {"bad": {"business_date": "2026-03-03", "content_id": "<any>", "ref": "1583a94945d3e9edc4db4689b554d7bcb16e313709c4de4e3494c2712a846a95"}, "changed": true, "changes": [{"after": null, "before": null, "column": "total", "kind": "mean_shift", "redacted": true, "score": 0.7648, "severity": "medium"}, {"after": null, "before": null, "column": "total", "kind": "distribution_shift", "redacted": true, "score": 0.866, "severity": "medium"}, {"after": null, "before": null, "column": "total", "kind": "range_change", "redacted": true, "score": 0.8336, "severity": "low"}, {"after": null, "before": null, "column": "total", "kind": "distribution_change", "redacted": true, "score": 0.2, "severity": "low"}], "columns": ["total"], "good": {"business_date": "2026-03-01", "content_id": "<any>", "ref": "4a35577bf2b7685cd0f592fb0113a3e62cdfd0ffd01ae7c2ddbc7efe0a436c76"}, "name": "layer_clean", "registry": "/work/layers/reg", "source": "clean", "status": "origin"}], "persists": [], "project": "/work/layers/shape.yml", "version": 1, "warnings": []}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"bad_date": "2026-03-03", "good_date": "2026-03-01", "layers": ["raw", "clean"]}, "command": "bisect_layers", "id": "needs-a-project"}
```

```json response
{"api_version": "1.2", "command": "bisect_layers", "error": {"code": "input.invalid_value", "group": "input", "hint": "give project (the bridge never looks for a shape.yml on its own)", "message": "bisect_layers needs a project file: its sources are the layers"}, "id": "needs-a-project", "ok": false, "warnings": []}
```

### `timelapse`

One column's statistics across the committed versions of `name` in the registry `registry`: a frame per version (or per merged
`window`: `day`, `week` or `month`) with rows, null rate, distinct estimate, quantiles, mean,
standard deviation and top values, read from the stored profiles; frames where `shape diff` reports a
change are change points. `table` picks the table of a dataset profile; `since` and `until` limit
the versions (dates, inclusive).

Result: `column`, `table`, `window`, `change_points` and `frames`, which is **spillable**
(*Large results*). A frame from a share-safe version has `form: "safe"` and only what the safe form
holds.

Errors: `input.not_found`, `input.invalid_value` (a name, column or table that is not there, a date
that is not a date, `since` after `until`), `usage.invalid_argument` (`window`).

```json request
{"api_version": "1.2", "args": {"column": "note", "name": "orders", "registry": "/work/feed_safe"}, "command": "timelapse", "id": "note-over-six-days"}
```

```json response
{"api_version": "1.2", "command": "timelapse", "id": "note-over-six-days", "ok": true, "result": {"change_points": [], "column": "note", "format": "shape-timelapse", "frames": [{"cardinality": 3, "change_point": false, "changes": [], "content_ids": ["828f07c958fc361fb5c346b8b284ad934723bdd9ddec5d135b132d5e4a1dc206"], "date": "2026-03-01", "end": "2026-03-01", "form": "safe", "gap": false, "mean": null, "null_rate": 0.005, "quantiles": null, "row_count": 200, "std": null, "top_values": [{"share": 0.361809, "value": "c"}, {"share": 0.321608, "value": "a"}, {"share": 0.316583, "value": "b"}], "versions": 1}, {"cardinality": 3, "change_point": null, "changes": [], "content_ids": ["07346306bd7f160ba38d8297b4ca53fc7165f806f4a6ab0e87a3cc1dccc9d671"], "date": "2026-03-02", "end": "2026-03-02", "form": "safe", "gap": false, "mean": null, "null_rate": 0.01, "quantiles": null, "row_count": 200, "std": null, "top_values": [{"share": 0.363636, "value": "a"}, {"share": 0.343434, "value": "c"}, {"share": 0.292929, "value": "b"}], "versions": 1}, {"cardinality": 3, "change_point": null, "changes": [], "content_ids": ["b5ad07c8668e46bf1bef17989f8098a03b5de9696814b73ca4e7e956e29b3c3a"], "date": "2026-03-03", "end": "2026-03-03", "form": "safe", "gap": false, "mean": null, "null_rate": 0.01, "quantiles": null, "row_count": 200, "std": null, "top_values": [{"share": 0.363636, "value": "a"}, {"share": 0.348485, "value": "c"}, {"share": 0.287879, "value": "b"}], "versions": 1}, {"cardinality": 3, "change_point": null, "changes": [], "content_ids": ["f1335cd0f6d8c147172ef1d91e3c933561b7e2831c404c449324e02f12a39870"], "date": "2026-03-04", "end": "2026-03-04", "form": "safe", "gap": false, "mean": null, "null_rate": 0.375, "quantiles": null, "row_count": 200, "std": null, "top_values": [{"share": 0.352, "value": "b"}, {"share": 0.352, "value": "c"}, {"share": 0.296, "value": "a"}], "versions": 1}, {"cardinality": 3, "change_point": null, "changes": [], "content_ids": ["0a356ce2993297a3755fc78bb3a279d97faea7ba29d5684306acfd5d679ea766"], "date": "2026-03-05", "end": "2026-03-05", "form": "safe", "gap": false, "mean": null, "null_rate": 0.45, "quantiles": null, "row_count": 200, "std": null, "top_values": [{"share": 0.4, "value": "c"}, {"share": 0.327273, "value": "a"}, {"share": 0.272727, "value": "b"}], "versions": 1}, {"cardinality": 3, "change_point": null, "changes": [], "content_ids": ["13adcc218bb05558bc4fabed8ffa2f40f3f19cf1fc0ff5d622e12cf5f73bb312"], "date": "2026-03-06", "end": "2026-03-06", "form": "safe", "gap": false, "mean": null, "null_rate": 0.49, "quantiles": null, "row_count": 200, "std": null, "top_values": [{"share": 0.343137, "value": "b"}, {"share": 0.333333, "value": "a"}, {"share": 0.323529, "value": "c"}], "versions": 1}], "name": "orders", "notes": ["6 frame(s) come from share-safe profiles: they show what the safe form holds, and 5 frame(s) next to them are not compared, so no change point is decided for them"], "since": null, "source": null, "table": "orders", "until": null, "version": 1, "window": null}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"column": "ghost", "name": "orders", "registry": "/work/feed_safe"}, "command": "timelapse", "id": "unknown-column"}
```

```json response
{"api_version": "1.2", "command": "timelapse", "error": {"code": "input.invalid_value", "group": "input", "hint": null, "message": "column 'ghost' is in none of the 6 versions of 'orders'"}, "id": "unknown-column", "ok": false, "warnings": []}
```

## Registry drift

### `registry_diff`

Drift between two versions (`ref1`, `ref2`: `latest`, a tag, or a content id) of `name` in the
registry `root`: what `shape registry ROOT diff NAME REF1 REF2` gives (`docs/REGISTRY.md`), in the
shape of `diff`'s result. `policy` is a drift policy file and `thresholds` thresholds that override
the defaults, as for `diff`.

- **Two share-safe versions**: `form` is `safe`; every metric both forms hold is compared with the
  rules of `shape diff`, and `not_measured` lists the metrics a safe form withholds (`table`,
  `column`, `metric`, `reason`), for example the `range` of a numeric column. No raw value is ever
  returned.
- **Two raw versions**: `form` is `raw`; the changes are `diff`'s, with the values of classified
  columns withheld as `diff` withholds them (`"redacted": true`), and `not_measured` is empty.
- **The same version twice**: `same` is true, `drifted` false, no changes.
- A raw and a safe version, or two other documents, are refused (`input.invalid_value`): use `diff`
  on two profile files.

Result: `name`, `from` and `to` (content ids), `same`, `form`, `drifted`, `change_count`, `changes`
(spillable) and `not_measured`.

Errors: `input.not_found` (the registry or `policy`), `input.invalid_value` (a folder that is not a
registry, a name or ref that is not recorded, thresholds that are not valid, two versions of different
forms), `usage.invalid_argument`. Warnings: `result_in_file`.

```json request
{"api_version": "1.2", "args": {"name": "orders", "ref1": "first", "ref2": "last", "root": "/work/feed_safe"}, "command": "registry_diff", "id": "safe-forms"}
```

```json response
{"api_version": "1.2", "command": "registry_diff", "id": "safe-forms", "ok": true, "result": {"change_count": 4, "changes": [{"baseline": 4801.646100000001, "column": "amount", "current": 45517.9272, "kind": "mean_shift", "score": 0.9481, "severity": "medium"}, {"baseline": 2227.58931394113, "column": "amount", "current": 24943.214233028044, "kind": "spread_change", "score": 0.9107, "severity": "medium"}, {"baseline": {"p05": 1482.612, "p25": 2858.2925, "p50": 4743.145, "p75": 6562.87, "p95": 8446.795}, "column": "amount", "current": {"p05": 5935.1435, "p25": 25039.6475, "p50": 46780.765, "p75": 65981.9525, "p95": 85054.6855}, "kind": "distribution_shift", "score": 0.9179, "severity": "medium"}, {"baseline": 0.005, "column": "note", "current": 0.49, "kind": "null_rate_change", "score": 0.485, "severity": "medium"}], "drifted": true, "form": "safe", "from": "828f07c958fc361fb5c346b8b284ad934723bdd9ddec5d135b132d5e4a1dc206", "name": "orders", "not_measured": [{"column": "amount", "metric": "range", "reason": "a safe form keeps bounds, not the minimum and maximum", "table": null}, {"column": "amount", "metric": "outlier_rate", "reason": "a safe form does not hold the outlier rate", "table": null}], "same": false, "to": "13adcc218bb05558bc4fabed8ffa2f40f3f19cf1fc0ff5d622e12cf5f73bb312"}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"name": "ghost", "ref1": "first", "ref2": "last", "root": "/work/feed_safe"}, "command": "registry_diff", "id": "unknown-name"}
```

```json response
{"api_version": "1.2", "command": "registry_diff", "error": {"code": "input.invalid_value", "group": "input", "hint": null, "message": "ghost@first is not recorded in the registry"}, "id": "unknown-name", "ok": false, "warnings": []}
```

## Chaos

### `chaos`

Corrupts tables on purpose and writes the ground-truth log: what `shape chaos` does
(`shape.chaos`, `docs/CHAOS.md`). `corrupt` is an array of corruptions, each as `--corrupt` takes it
(`KIND[=RATE][@TABLE[.COLUMN]][:OPT=V,...]`, for example `duplicates=0.02`). The tables come from
`input` (a folder, one file per table) or, with `domain` (an installed domain or a generation
schema file, as the command line's target) and no `input`, are generated at `scale` and `mode`;
`domain` also names the keys and foreign keys of an `input`. `seed` (default: the schema's, else
42), `format` (`csv`, `parquet` or `jsonl`), `batch` and `start_date` (as `--batch` and
`--start-date`; `start_date` derives the batch from a batch date, which the bridge does not take, so
a request with it alone is refused as the command line refuses it) and `ground_truth` (the log
file; default `output_dir/_chaos_ground_truth.jsonl`). Job-capable and cancellable: a cancel is
noticed before the files are written, so a cancelled job writes nothing.

**The input check of W1-17, unchanged.** Chaos corrupts only data that Shape wrote. An `input` folder
with a table file not marked as Shape-generated (listed with a matching sha256 in its
`_shape_provenance.json`, or a Parquet file with the `shape_synthetic` marker) answers
`policy.unverified_input` **before anything is written** (not even `output_dir`).
`allow_real_input` runs it anyway, with the warning `real_input_corrupted`, and the log records
`input_provenance: unverified`. Files written by the command line's `shape generate` and by `chaos`
itself are marked; files written by the bridge's `generate` are not.

**Local only.** An `output_dir`, `ground_truth` or `input` that is a URL (`s3://`, `abfss://`,
`https://`) answers `policy.not_permitted`.

Result, as `shape chaos --json` prints it: `output`, `ground_truth` (the log's path), `seed`,
`batch`, `files`, `changes` (rows in the log) and `applied`. The log holds the cells that were
changed (before and after); it is written, never returned.

Errors: `policy.unverified_input`, `policy.not_permitted`, `input.not_found` (`input` folder),
`input.invalid_value` (neither `domain` nor `input`, no corruption, a corruption that is not valid,
`output_dir` that is the input folder or inside it, a scale that does not exist, `start_date`),
`input.unknown_domain`, `io.write_failed`, `usage.invalid_argument`. Warnings:
`real_input_corrupted`.

```json request
{"api_version": "1.2", "args": {"corrupt": ["duplicates=0.05"], "domain": "/work/schema.json", "output_dir": "/work/chaos_out", "seed": 5}, "command": "chaos", "id": "generated-tables"}
```

```json response
{"api_version": "1.2", "command": "chaos", "id": "generated-tables", "ok": true, "result": {"applied": [{"column": null, "kind": "duplicates", "rows": 2, "table": "customer"}, {"column": null, "kind": "duplicates", "rows": 60, "table": "order"}, {"column": null, "kind": "duplicates", "rows": 155, "table": "order_line"}], "batch": 0, "changes": 217, "files": ["/work/chaos_out/customer.csv", "/work/chaos_out/order.csv", "/work/chaos_out/order_line.csv"], "ground_truth": "/work/chaos_out/_chaos_ground_truth.jsonl", "output": "/work/chaos_out", "seed": 5}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"corrupt": ["duplicates=0.5"], "input": "/work/unmarked", "output_dir": "/work/chaos_refused"}, "command": "chaos", "id": "unmarked-input-refused"}
```

```json response
{"api_version": "1.2", "command": "chaos", "error": {"code": "policy.unverified_input", "group": "policy", "hint": "chaos only corrupts data marked as Shape-generated; allow_real_input runs it anyway", "message": "chaos input /work/unmarked/orders.csv is not marked as Shape-generated data; chaos only corrupts synthetic data (pass --allow-real-input to override)"}, "id": "unmarked-input-refused", "ok": false, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"allow_real_input": true, "corrupt": ["duplicates=0.5"], "input": "/work/unmarked", "output_dir": "/work/chaos_real"}, "command": "chaos", "id": "real-input-allowed"}
```

```json response
{"api_version": "1.2", "command": "chaos", "id": "real-input-allowed", "ok": true, "result": {"applied": [{"column": null, "kind": "duplicates", "rows": 2, "table": "orders"}], "batch": 0, "changes": 2, "files": ["/work/chaos_real/orders.csv"], "ground_truth": "/work/chaos_real/_chaos_ground_truth.jsonl", "output": "/work/chaos_real", "seed": 42}, "warnings": [{"code": "real_input_corrupted", "message": "allow_real_input: corrupting data that is not marked as Shape-generated; the ground-truth log records input_provenance: unverified"}]}
```

```json request
{"api_version": "1.2", "args": {"corrupt": ["duplicates=0.5"], "input": "/work/unmarked", "output_dir": "s3://bucket/out"}, "command": "chaos", "id": "not-local"}
```

```json response
{"api_version": "1.2", "command": "chaos", "error": {"code": "policy.not_permitted", "group": "policy", "hint": "give a folder on this machine", "message": "output_dir s3://bucket/out is not a local path: chaos writes local files only"}, "id": "not-local", "ok": false, "warnings": []}
```

## Suites

### `suite_list`

Lists what `shape pack list --library --json` lists (`shape.scenario.library`, `docs/SCENARIO_LIBRARY.md`):
`suites` (the names of the built-in suites, sorted) and `scenarios` (the starter library: `id`,
`domain` and `description` of each). It takes no arguments and touches no file.

Errors: `usage.unknown_argument`.

```json request
{"api_version": "1.2", "args": {}, "command": "suite_list", "id": "library"}
```

```json response
{"api_version": "1.2", "command": "suite_list", "id": "library", "ok": true, "result": {"scenarios": [{"description": "Retail generated as is: every validation gate passes. The control the other scenarios are read against.", "domain": "retail", "id": "clean_baseline"}, {"description": "About 8% of customer last names are null although the column is not nullable: the null check must fail and nothing else may.", "domain": "retail", "id": "nulls_injected"}, {"description": "Return rows take over the primary key of another return, so keys repeat: the uniqueness gate must fail; the references into the table stay intact.", "domain": "retail", "id": "duplicate_rows"}, {"description": "About 3% of orders point at a customer that does not exist: the referential integrity gate must fail.", "domain": "retail", "id": "orphaned_foreign_keys"}, {"description": "About 10% of orders carry an order date 45 days before the rest of the batch, as when a late feed lands behind newer data. No gate is meant to catch it: the scenario checks that the late rows are planted and that the batch is otherwise sound.", "domain": "retail", "id": "late_arriving_data"}, {"description": "A column appears on a schedule: orders gain a channel on day 5. The diff of day 0 and day 10 must report it as added, and nothing else structural.", "domain": "retail", "id": "schema_add_column"}, {"description": "A column is renamed on a schedule: order status becomes order_status on day 5. The diff sees one column dropped and one added; the answer key records the pair as one rename.", "domain": "retail", "id": "schema_rename_column"}, {"description": "A column disappears on a schedule: stores lose their state on day 5. The diff of day 0 and day 10 must report it as removed.", "domain": "retail", "id": "schema_drop_column"}, {"description": "A column changes type on a schedule: the customer active flag turns from the text true or false into an integer on day 5. The diff must report a type change.", "domain": "retail", "id": "schema_retype_column"}, {"description": "All four schema changes on one schedule: a column is added on day 5, one renamed on day 10, one dropped on day 15 and one retyped on day 20. Four diffs, each against day 0, must report exactly the changes that have happened by then.", "domain": "retail", "id": "schema_evolution_schedule"}, {"description": "About 60% of customer last names are null although the column is not nullable: far beyond a stray missing value, as when a source column stops being filled. The null check must fail and nothing else may.", "domain": "retail", "id": "null_flood"}, {"description": "From the middle of the batch on, order totals are 100 times larger, as when a feed switches from dollars to cents. No gate is meant to catch it: the scenario checks that the changed rows are planted.", "domain": "retail", "id": "unit_change"}, {"description": "About 40% of customer emails are cut to five characters, as when a column is loaded into a narrower one. No gate is meant to catch it.", "domain": "retail", "id": "truncated_strings"}, {"description": "About 20% of customer first names are the text N/A, as when a form fills a required field with a stand-in. No gate is meant to catch it.", "domain": "retail", "id": "placeholder_values"}, {"description": "About 30% of customer emails end in the characters that UTF-8 text turns into when it is read as Latin-1. No gate is meant to catch it.", "domain": "retail", "id": "encoding_corruption"}, {"description": "The order table carries ten times as many rows as it should, as when a retry loop re-sends a batch. The batch has repeated keys, so the uniqueness gate fails.", "domain": "retail", "id": "volume_spike"}, {"description": "The order table arrives with no rows, as when an upstream job succeeds without writing. The row count gate fails, and so do the references into the table.", "domain": "retail", "id": "empty_load"}, {"description": "The order table arrives with a single row, as when a job is cut short. The references into the table fail.", "domain": "retail", "id": "partial_load"}, {"description": "Every order date is swapped with another order's, so the dates no longer follow the order in which the rows arrived. Each value is still there, so no check that looks at the column alone can see it.", "domain": "retail", "id": "out_of_order_events"}, {"description": "Every order date moves eight hours later, as when a writer starts to emit UTC instead of local time. No gate is meant to catch it.", "domain": "retail", "id": "timezone_offset"}, {"description": "About 30% of order dates are moved onto the hours around the daylight-saving changes of 2024 and 2025. No gate is meant to catch it.", "domain": "retail", "id": "dst_boundary"}, {"description": "Customer addresses keep their cities and states, but the cities are swapped between rows, so a city no longer implies its state. Each column alone looks as before.", "domain": "retail", "id": "concept_drift"}, {"description": "Orders that were mostly completed become mostly cancelled from day 5: the mix of order statuses moves, and no value is new.", "domain": "retail", "id": "class_imbalance_shift"}, {"description": "A status that no order had before, lost, appears on day 5 for 15% of the orders.", "domain": "retail", "id": "new_category_values"}, {"description": "The share of customers without a last name climbs to 40% over ten days, starting on day 5.", "domain": "retail", "id": "null_rate_creep"}, {"description": "Product unit prices are 1.8 times larger from day 5, as when prices are raised or a unit changes. The shape of the distribution is the same.", "domain": "retail", "id": "numeric_shift"}, {"description": "About 20% of orders carry an order date 200 days before the rest of the batch, as when a backfill lands in the live table. No gate is meant to catch it: the scenario checks that the late rows are planted and that the batch is otherwise sound.", "domain": "retail", "id": "late_backfill"}, {"description": "Customer emails end in the characters that UTF-8 text turns into when it is read as Latin-1, and half of the customer last names are cut to three characters. The two problems are in different columns. No gate is meant to catch them.", "domain": "retail", "id": "detective_text_trouble"}, {"description": "Four schema changes at once, one in each of four tables: order status is renamed, store state is dropped, the customer active flag turns into an integer and products gain a channel. The schema conformance gate fails.", "domain": "retail", "id": "detective_renovations"}, {"description": "Order dates arrive 200 days late for 20% of the rows and every date is eight hours off; return ids repeat; address cities are swapped between rows. The uniqueness gate fails.", "domain": "retail", "id": "detective_clocks_and_keys"}], "suites": ["failure-modes", "schema-evolution", "smoke"]}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"suite": "smoke"}, "command": "suite_list", "id": "takes-no-arguments"}
```

```json response
{"api_version": "1.2", "command": "suite_list", "error": {"code": "usage.unknown_argument", "group": "usage", "hint": "it takes: no arguments", "message": "suite_list does not take argument(s): suite"}, "id": "takes-no-arguments", "ok": false, "warnings": []}
```

### `suite_run`

Runs a suite of library scenarios and compares each outcome with its answer key: what `shape suite
run --json` does (`shape.scenario.library.run_suite`). `suite` is a built-in name (see `suite_list`)
or the path of a suite file (format `shape-suite`, version 1; a file of a newer version answers
`input.unsupported_format_version`). `scale` is a scale preset of the scenarios' domain or `tiny`
(default: `small`); `seed` replaces each scenario's own seed; `output_dir` writes each scenario's
tables under `output_dir/<scenario>/` (default: nothing is written). The suite is written locally
only: an `output_dir` that is a URL (`s3://`, `abfss://`, `https://`) answers
`policy.not_permitted`. Job-capable and cancellable between scenarios: a cancelled job is `cancelled`
and its result holds the counts so far (`suite`, `scenarios_run`, `scenarios_total`, `met`,
`not_met`); no later scenario starts.

All names are checked before anything runs, so a suite that names an unknown scenario runs nothing
and writes nothing.

Result, the document `shape suite run --json` prints plus `passed`: `suite`, `scale`, `met` and
`passed` (every scenario met its answer key; the command line's exit code 0, and 1 when false) and
`scenarios`, one per scenario in suite order: `scenario`, `met`, `mismatches` (each with the
`expected` entry of the answer key and what was `observed`; empty when met) and `outcome` (`domain`,
`scale`, `seed`, `gates` and `gate_messages`, `defects`, `drift`, `files`, `elapsed_seconds`). A
scenario that missed its key is a result with `passed: false`, not an error.

Errors: `input.not_found` (no such suite name or file), `input.invalid_value` (a malformed suite
file, an unknown scenario, a scale that does not exist), `input.unsupported_format_version`,
`policy.not_permitted`, `io.write_failed`, `usage.missing_argument`, `usage.invalid_argument`.

```json request
{"api_version": "1.2", "args": {"scale": "tiny", "seed": 5, "suite": "/work/pair.suite.json"}, "command": "suite_run", "id": "pair-met"}
```

```json response
{"api_version": "1.2", "command": "suite_run", "id": "pair-met", "ok": true, "result": {"met": true, "passed": true, "scale": "tiny", "scenarios": [{"met": true, "mismatches": [], "outcome": {"defects": {}, "domain": "retail", "drift": [], "elapsed_seconds": "<any>", "files": [], "gate_messages": {}, "gates": {"null_check": true, "referential_integrity": true, "row_count": true, "schema_conformance": true, "uniqueness": true}, "scale": "tiny", "scenario": "clean_baseline", "seed": 5}, "scenario": "clean_baseline"}, {"met": true, "mismatches": [], "outcome": {"defects": {"inject_nulls": 8}, "domain": "retail", "drift": [], "elapsed_seconds": "<any>", "files": [], "gate_messages": {"null_check": "customer.last_name has nulls"}, "gates": {"null_check": false, "referential_integrity": true, "row_count": true, "schema_conformance": true, "uniqueness": true}, "scale": "tiny", "scenario": "nulls_injected", "seed": 5}, "scenario": "nulls_injected"}], "suite": "pair.suite"}, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"suite": "/work/typo.suite.json"}, "command": "suite_run", "id": "unknown-scenario"}
```

```json response
{"api_version": "1.2", "command": "suite_run", "error": {"code": "input.invalid_value", "group": "input", "hint": null, "message": "suite typo.suite names unknown scenarios: no_such_scenario; the library has: class_imbalance_shift, clean_baseline, concept_drift, detective_clocks_and_keys, detective_renovations, detective_text_trouble, dst_boundary, duplicate_rows, empty_load, encoding_corruption, late_arriving_data, late_backfill, new_category_values, null_flood, null_rate_creep, nulls_injected, numeric_shift, orphaned_foreign_keys, out_of_order_events, partial_load, placeholder_values, schema_add_column, schema_drop_column, schema_evolution_schedule, schema_rename_column, schema_retype_column, timezone_offset, truncated_strings, unit_change, volume_spike"}, "id": "unknown-scenario", "ok": false, "warnings": []}
```

```json request
{"api_version": "1.2", "args": {"output_dir": "s3://bucket/out", "suite": "smoke"}, "command": "suite_run", "id": "not-local"}
```

```json response
{"api_version": "1.2", "command": "suite_run", "error": {"code": "policy.not_permitted", "group": "policy", "hint": "give a folder on this machine", "message": "output_dir s3://bucket/out is not a local path: chaos writes local files only"}, "id": "not-local", "ok": false, "warnings": []}
```


## Annotations for clients

A client that asks a person before it touches files needs to know which arguments are paths and what
a command does. The request schemas and `index.json` say so:

| annotation | where | meaning |
|---|---|---|
| `x-path` | an argument in a request schema | `"read"` or `"write"`: the argument is a filesystem path (an array of paths for `data`) the command reads or writes. A file that is read and updated (a decision file) is `"write"`. |
| `x-name-or-path` | `domain` | `true`: an installed domain name, tried first, else the path of a generation schema file. |
| `x-since` | an argument, a request and a result schema | the version that added it. |
| `effects` | a command in `index.json` | a list from `reads_files`, `writes_files`, `cancels` (stops a running job or stream) and `network` (a `fabric_spark` run or a remote sink). |

`sink_config` (`scale_generate`, `stream`) holds paths inside an object: no argument-level annotation
can say which, so those commands list `writes_files` and `network` in their `effects`. `effects` says
what a command can do, not what a given request does (`generate` writes files only when it is given a
`format` and `output_dir`). Job files and spilled results in the jobs directory are the bridge's own
and are not listed. A test checks that every argument that is a path is annotated.


## Jobs

A command that can take a while returns **a job** instead of a result when the request sets
`"options": {"async": true}` (the commands marked job-capable above); `stream` always does.

```json
{"api_version": "1.0", "command": "generate", "args": {"domain": "retail", "scale": "large"},
 "options": {"async": true}}
```
```json
{"ok": true, "result": {"job_id": "job-0a1b2c3d4e5f", "command": "generate", "status": "running",
  "progress": {}, "result": null, "error": null, "cancellable": false, ...}}
```

- `job_status` returns the job: `status` (`running`, `submitted`, `succeeded`, `failed`,
  `cancelled`, `interrupted`), `progress` (for a scale run `rows_done`, `rows_total`, `chunks`), the
  `result` of a success (what the synchronous command would have returned) and the `error` of a
  failure (the same object an error response carries).
- `job_cancel` stops a job and answers `cancelled` (false when the job had already ended). Only a
  scale run, a stream, `rules_mutate` (between mutants), `suite_run` (between scenarios) and `chaos`
  (before its files are written) notice a cancel request; for any other running job it answers
  `input.job_state` (`"cancellable": false` in the job says which). A cancelled stream keeps its
  counts, and a cancelled `rules_mutate` or `suite_run` job its partial counts (a cancelled `chaos` job
  writes nothing).
- `job_list` lists jobs oldest first (`status` filter, `limit` the most recent N).
- `scale_status` and `scale_cancel` are `job_status` and `job_cancel` for scale jobs.
  `stream_status` and `stream_stop` are the stream views. They take the stream's id, which is the
  job id.
- A `fabric_spark` run is submitted to Fabric and recorded as a job; its state is read from Fabric
  when it is asked for (`job_status`, `scale_status`), so those calls need the Fabric token: the
  argument `token`, else `$SHAPE_FABRIC_TOKEN`. A token is never written to a file. A Fabric status
  the bridge has no name for leaves the job `submitted` (still asked, still cancellable) and is
  reported as `progress.fabric_status`.

**A job survives a restart of the bridge.** Job state lives in one JSON file per job under
`DIR/bridge/` (`DIR` from `--jobs-dir`, `$SHAPE_JOBS_DIR`, or `~/.shape/jobs`):

```json
{"format": "shape-bridge-job", "version": 1, "job_id": "job-0a1b2c3d4e5f", "command": "generate",
 "status": "succeeded", "request": {"args": {...}, "options": {...}}, "progress": {...},
 "result": {...}, "error": null, "worker": {"pid": 4242}, "external": null, ...}
```

A new bridge process reads the jobs an old one wrote. A job that was running when its process died
is reported as `interrupted` (error `input.job_interrupted`, with how far it got); nothing claims it
still runs. At end of input the bridge stops its streams and scale runs (recorded as `cancelled`)
and waits for its other jobs to finish, so a normal exit leaves no job `interrupted`. Files are
written atomically with mode `0600`, secrets in the request (any key named like a token, password,
key or connection string) are replaced by `***`, and the file's `version` is checked on read.

A `stream` job writes one chunk to its sinks every `interval_seconds`: the schema at `scale`, each
table at most `chunk_size` rows, with seed `seed XOR chunk_index`, until `max_chunks` or until it
is stopped. A Parquet sink writes each chunk under `chunk-NNNNNN/`; the `memory` sink only counts.

## Large results

A result part that could be large is returned inline up to `options.max_inline_bytes` (default
262144, at least 1024) and above that written to a file and returned as a reference:

```json
{"spilled": true, "content_id": "<sha256 hex>", "path": "/.../bridge/results/<content_id>.json", "bytes": 1048576}
```

The file holds the value as JSON, named by its content id (the SHA-256 of its bytes); the same
value always gives the same file. Parts that can spill: `preview`'s table `data`, `profile`'s and
`profile_show`'s `summary`, `diff`'s `changes`, `check`'s `violations`, `verify`'s `gates`,
`proposals_list`'s `proposals`, `design`'s `ddl`, the whole result of `report_card`,
`report_card_read`, `rules_mutate` and `rules_backtest`, the `changes` of `registry_diff` and the
`frames` of `timelapse`. Each time one does, the response has the
warning `result_in_file`. `generate` and `scale_generate` never return data: they
write files and return their paths. `profile` returns the path and `content_id` of the `.shape`
artifact it wrote (default location: `DIR/bridge/profiles/`, named by content id).

## Safe by default

`profile`, `profile_show`, `diff`, `check`, `verify`, `proposals_list`, `proposals_decide`,
`safe_scan`, `bisect`, `bisect_layers`, `timelapse` and `registry_diff` read real data or what was made from it. A result never includes the raw values of a
**classified column** unless the request sets `"options": {"include_raw_values": true}`.

A column is classified by the same rule the safe profile uses: a detected personal-data pattern
(email, SSN, credit card, phone, IP address, IBAN, postal code), or nearly every value distinct
(the free-text and identifier backstop). For a classified column:

- `profile`'s and `profile_show`'s `summary` have `min` and `max` set to `null` and
  `"redacted": true`;
- a `diff` change has `baseline` and `current` set to `null` and `"redacted": true`;
- a `check` violation has `observed` set to `null` and `"redacted": true`;
- a `diff` change or `check` violation also has its `message` and `detail` set to `null` (they quote
  values), and an entry about several columns (a dependency `"email -> region"`, an association
  `"a ~ b"`, a key `"(a, b) in ref"`) is withheld this way when any of its columns is classified;
- a `verify` gate message about a classified column leaves out the actual minimum or maximum the
  range gate would quote (`(actual max: ...)`);
- the evidence of a proposal (`proposals_list`, `proposals_decide`) has its `range` endpoints set to
  `null` and the proposal has `"redacted": true`: a decision file does not say which columns are
  classified, so no value of the evidence is returned by default;
- a `bisect`, `bisect_layers` or `registry_diff` change about a classified column has `before` and
  `after` (`baseline` and `current` for `registry_diff`) set to `null` and `"redacted": true`, and a
  `timelapse` frame of one has the `value` of each of its `top_values` set to `null`;
  `report_card` and `rules_mutate` return scores and counts, and no value of the data;
- a `safe_scan` finding's `message` never holds the value it found, and a pointer that is a
  personal-data value is `<redacted>`.

An entry about several columns (the joint analysis: a broken dependency `a -> b`, an association
`a ~ b`, a placeholder surge, a reference pair) is withheld when any of its columns is classified,
and then its `message` and `detail`, which quote values, are `null` too.

What stays is the column's shape (type, null rate, cardinality, pattern), which is not a value.
`verify` messages come from the gate schema and counts. The range gate's actual minimum or maximum
is withheld for a classified column (safe by default). For a column that is not classified the
message still quotes it (`(actual max: ...)`): that is the frozen, documented 1.1 return value, and
the frozen 1.1 vector keeps it. The `details` of its gates (1.1) hold counts, rates, distances and
scores only.

Two things the default does not do: it does not change the `.shape` file `profile` writes, which is
still the full profile (`capture="full"`) and holds real values (the warning
`profile_file_holds_values` says so; the safe default of `shape profile` does not apply to the
bridge until its protocol says so), and it does not touch `generate`, `preview` or
`stream`, which return generated data.

## What each command runs

There is no second implementation: a command calls what the matching command line calls.

| bridge command | code it calls |
|---|---|
| `list`, `describe`, `dry_run` | `shape list`, `shape describe`, `shape generate --dry-run` (`shape.cli.generation`, `shape.generation.engine`) |
| `generate`, `preview` | `shape generate`'s engine and file writers (`shape.generation.engine`, `shape.generation.output`) |
| `validate` | `shape validate` (`shape.cli.validate`, `GenSchema.validate`) |
| `scale_generate`, `stream` | `shape generate --scale-mode` (`shape.scale`: router, sinks, Fabric Spark router) |
| `profile`, `diff`, `check` | `shape profile`, `shape diff`, `shape check` (`shape.profile`, `shape.diff`, `shape.check`) |
| `verify` | `shape verify` (`shape.quality.VerifyRunner`; with `source`, `shape verify --source`: the memorization and utility gates of `shape.quality`) |
| `project` and `source` on `profile`, `diff`, `check`, `verify` | `shape ... --project FILE --source NAME` (`shape.project`, `shape.cli.project`) |
| `proposals_propose`, `proposals_list`, `proposals_decide` | `shape proposals propose`, `list`, `decide` (`shape.proposals`) |
| `project_validate`, `project_show` | `shape project validate` (`shape.project`: `parse_project`, `load_project`) |
| `design`, `design_from_data` | `shape design`, `shape design --from-data` (`shape.design`: `load_design`, `lint`, `derive`, `emit_ddl`, `design_from_rows`) |
| `format_schema` | the schemas the readers validate with, shipped in `shape/schemas` (`shape.design.design_input_schema`, `shape.generation.schema.json_schema`, `shape.project.schema`, `shape.spec.model.model_schema`, ...) |
| `profile_show` | `shape.load` (`shape.profile.reference`): what `diff` and `check` read |
| `contract_validate` | the contract validation of `shape check` (`shape.contracts.v1`) |
| `proposals_contract` | `shape proposals contract` (`shape.proposals`: `DecisionFile.to_contract`, `dump_contract`) |
| `report_card`, `report_card_read` | `shape report-card` (`shape.quality.reportcard`: `report_card`, `load_report_card`) |
| `rules_mutate`, `rules_backtest` | `shape rules mutate`, `shape rules backtest` (`shape.rules`: `mutation_test`, `backtest`) |
| `bisect`, `bisect_layers`, `timelapse` | `shape bisect`, `shape bisect layers`, `shape timelapse` (`shape.versions`: `bisect`, `bisect_layers`, `timelapse`) |
| `registry_diff` | `shape registry ROOT diff` (`shape.registry.drift`: `diff_safe`; `shape.diff` for two raw versions) |
| `suite_list`, `suite_run` | `shape pack list --library`, `shape suite run` (`shape.scenario.library`: `list_scenarios`, `list_suites`, `load_suite`, `run_scenario`) |
| `chaos` | `shape chaos` (`shape.chaos`: `corrupt_tables`, `verify_chaos_input`, `write_ground_truth`) |
| `safe_scan` | `shape profile validate --safe` (`shape.privacy.safe_validator.SafeProfileValidator`) |

## Security

The bridge speaks over standard input and output and does what its caller asks with the
permissions of its process: it reads and writes the paths a request names, as given. Run it with
the permissions you would give the program that drives it. It opens no network connection of its
own, except to Fabric for a `fabric_spark` run you ask for. A credential is read from the request
or the environment when a command needs it and is never written to a job file, a result or a
log. Job files and spilled results are created with mode `0600` in a `0700` directory.

## Examples

A long session, one request per line:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

The core workflow, driven from Python:

```python
import json, subprocess

def call(proc, command, **args):
    proc.stdin.write(json.dumps({"api_version": "1.0", "command": command, "args": args}) + "\n")
    proc.stdin.flush()
    response = json.loads(proc.stdout.readline())
    if not response["ok"]:
        raise RuntimeError(f'{response["error"]["code"]}: {response["error"]["message"]}')
    return response["result"]

proc = subprocess.Popen(["shape", "bridge"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
before = call(proc, "profile", source="last_month.csv", output="before.shape")
after = call(proc, "profile", source="this_month.csv", output="after.shape")
drift = call(proc, "diff", before="before.shape", after="after.shape")
print(drift["drifted"], drift["change_count"])
```

Bridge 1.1: a person decides what Shape proposes, and the answer is kept in a decision file:

```python
ids = call(proc, "proposals_propose", profile="shop.shape", decisions="decisions.json",
           data=["data/"])["proposal_ids"]
for row in call(proc, "proposals_list", decisions="decisions.json", status="pending",
                min_confidence=0.9)["proposals"]:
    print(row["id"], row["confidence"])           # evidence values are withheld by default
call(proc, "proposals_decide", decisions="decisions.json", proposal=ids[0], verb="accept",
     actor="ana", note="orders belong to customers")
```

(`call` is the helper above, with `"api_version": "1.1"` in the request.)

Bridge 1.2: find where in a history a column changed, and what a safe history cannot tell:

```python
card = call(proc, "report_card", real="data/real", synthetic="data/synthetic")
print(card["overall"], card["overall_reasons"])    # a failed card is a result, not an error

found = call(proc, "bisect", registry="reg", name="orders", good="last-week", bad="latest")
print(found["first_bad"]["business_date"], [c["kind"] for c in found["changes"]])

drift = call(proc, "registry_diff", root="reg", name="orders", ref1="last-week", ref2="latest")
print(drift["drifted"], [(n["column"], n["metric"]) for n in drift["not_measured"]])
```

(`call` is the helper above, with `"api_version": "1.2"` in the request.)
