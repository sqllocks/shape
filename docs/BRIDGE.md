# `shape bridge`: Shape as a JSON protocol

`shape bridge` serves Shape's commands over standard input and output as a versioned JSON
request/response protocol. Editors, notebooks, wrappers and agents use it to run Shape without
parsing command-line text.

```console
$ echo '{"api_version": "1.0", "id": "r1", "command": "dry_run", "args": {"domain": "retail"}}' | shape bridge
{"api_version": "1.1", "command": "dry_run", "id": "r1", "ok": true, "result": {...}, "warnings": []}
```

- `shape bridge` reads **one request per line** and writes **one response per line** until end of
  input (a long-lived session).
- `shape bridge --once` reads all of standard input as **one request** (it may span lines), answers
  once and exits: 0 for a success, 1 for an error response.
- `--jobs-dir DIR` says where job state is kept (below). Without it: `$SHAPE_JOBS_DIR`, else
  `~/.shape/jobs`.
- Standard output carries only responses. Anything a command prints goes to standard error.
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
`project_source_not_selected`). A warning's `code` is stable; the full list, with a sentence for each,
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
`input.unknown_source` and `input.unknown_format`.

## Versions and the stability promise

`api_version` is `MAJOR.MINOR`; this bridge speaks **1.1** and serves **1.0 to 1.1**.

- **Minor versions only add:** a command, an optional argument, an option, a result field, an error
  code, a warning code. A client written for 1.0 keeps working on 1.7, so **a client ignores result
  fields it does not know**.
- **Within a major version nothing is renamed, removed, retyped or tightened:** no command, argument,
  result field, error code or the meaning of any of them.
- **A major version may break.** A bridge serves one major version. A request for another is refused
  with `usage.unsupported_version`, and the message names the supported range
  (`... this bridge serves 1.0 to 1.1`). The response always carries the bridge's own
  `api_version`, so a client can read it from the refusal.
- A request with a newer *minor* than the bridge's is served, with the warning
  `newer_minor_version`: what the newer minor added is not available.
- A request with **no `api_version`** is served as 1.1, with the warning `api_version_assumed`.
- Persisted files declare their own `format` and integer `version` (the job file below, the vector
  files). A file written by a newer Shape is refused with `input.unsupported_format_version`, never
  misread. The job file `shape-bridge-job` and the vector files `shape-bridge-vectors` are still
  version 1: a 1.1 job file is read by the 1.1 bridge, and a job file written by 1.0 is read
  unchanged.

**The 1.0 promise.** A request that declares `"api_version": "1.0"` is answered exactly as the 1.0
bridge answers it:

- a command added in 1.1 is `usage.unknown_command`, and an argument added in 1.1 is
  `usage.unknown_argument`; the hint names `api_version 1.1` and lists only what 1.0 had;
- no result field or warning of 1.1 is added to the result of a 1.0 command (`verify`'s gates have no
  `details` for a 1.0 request, for example);
- the one field that differs is the response's own `api_version`, which is the bridge's version
  (`"1.1"`), as it always was.

Every command and every argument records the version that added it: `since` (`"1.0"` or `"1.1"`) in
`index.json` (`commands.NAME.since`, and `commands.NAME.args` for each argument) and `x-since` in the
request and result schemas.

The promise is enforced: the published schemas are generated from the code, a test fails when the
committed files differ (so a change is a visible, deliberate edit), and a compatibility test per
command runs the published vectors against a live bridge: a result may gain fields, never lose one.
The 1.0 contract is **frozen** in [`docs/bridge/schema/1.0/`](bridge/schema/1.0/index.json) and
[`docs/bridge/vectors/1.0/`](bridge/vectors/1.0): `tests/bridge/test_compat_1_0.py` replays every 1.0
vector against the 1.1 bridge (each response equals the recorded one in every field except
`api_version`) and checks that the 1.1 schemas keep every 1.0 command, argument (name, type,
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
| `generate` | generate a domain or schema; optionally write the files (job-capable) | `domain`\*, `scale`, `seed`, `format`, `output_dir`, `mode`, `profile` |
| `preview` | a small sample as JSON rows | `domain`\*, `rows`, `seed`, `tables`, `mode`, `profile` |
| `scale_generate` | generate at scale into sinks: `local_single`, `local_mp`, `fabric_spark` (job-capable, cancellable) | `domain`\*, `scale`, `seed`, `scale_mode`, `sinks`, `sink_config`, `chunk_size`, `max_workers`, `mode`, `profile` |
| `stream` | start a background stream (always a job, cancellable) | `domain`\*, `scale`, `seed`, `sinks`, `sink_config`, `interval_seconds`, `chunk_size`, `max_chunks`, `mode`, `profile` |
| `stream_status`, `stream_stop` | a stream's state; stop it | `stream_id`\* |
| `scale_status`, `scale_cancel` | a scale job's state; cancel it | `job_id`\*, `token` |
| `profile` | profile a file, folder, glob or Delta table into a `.shape` artifact (job-capable) | `source`\*, `output`, `name`, `dataset`, `version`, `as_of`, `fail_on_empty`, `project` (1.1) |
| `diff` | compare two profiles | `before`\*, `after`\*, `policy`, `thresholds`, `column_thresholds`, `ignore_columns`, `only_columns`, `project` (1.1), `source` (1.1) |
| `check` | check a profile against a contract | `profile`\*, `contract`\*, `project` (1.1), `source` (1.1) |
| `verify` | run the validation gates over data files (job-capable) | `path`\*, `format`, `schema`, `config`, `statistical`, `strict`, `project` (1.1), `source` (1.1) |
| `proposals_propose` | find proposals for a profile and merge them into a decision file (1.1, job-capable) | `profile`\*, `decisions`\*, `data`, `kinds`, `min_confidence`, `auto_accept` |
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
| `job_status`, `job_cancel`, `job_list` | any job's state, cancel, list | `job_id`\*, `token`; `status`, `limit` |
| `demo_list`, `demo_run`, `demo_status`, `demo_cleanup` | the demo scenarios (see below) | see the schema |

\* required. `domain` is an installed domain (see `list`) or the path of a generation schema file.

`describe` lists a table's columns in the order the schema declares them. A generated table (what
`generate` writes and `preview` shows) lists its columns in generation order: keys first, then
the columns that depend on others.

Per-command argument, result and example detail is in the published schemas
(`commands/NAME.request.schema.json`, `commands/NAME.result.schema.json`) and the vectors
(`vectors/NAME.json`); every argument carries a description.

**The demo commands.** `demo_list`, `demo_run`, `demo_status` and `demo_cleanup` are specified,
their schemas are published and their requests are checked now, but the demo scenarios are run by
`shape demo`, which this build does not include yet. A valid request answers
`policy.capability_unavailable` until it does.

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
{"api_version": "1.1", "command": "proposals_propose", "id": "shop", "ok": true, "result": {"added": 5, "auto_accepted": [], "decisions": "/work/decisions.json", "proposal_ids": ["pii:customers.email", "relationship:orders.customer_id->customers.customer_id", "semantic:customers.email", "pii:customers.full_name", "semantic:customers.full_name"], "proposals": 5, "skipped": [], "skipped_rejected": [], "unchanged": 0, "updated": 0, "withdrawn": 0}, "warnings": [{"code": "artifact_not_verified", "message": "/work/shop.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}]}
```

```json request
{"api_version": "1.1", "args": {"decisions": "/work/decisions.json", "profile": "/work/none.shape"}, "command": "proposals_propose", "id": "missing-profile"}
```

```json response
{"api_version": "1.1", "command": "proposals_propose", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.shape"}, "id": "missing-profile", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "proposals_list", "id": "relationships-only", "ok": true, "result": {"count": 1, "decisions": "/work/decisions.json", "proposals": [{"claim": {"child": "orders", "child_columns": ["customer_id"], "parent": "customers", "parent_columns": ["customer_id"], "type": "one_to_many"}, "confidence": 1.0, "decision": {"actor": null, "decided_at": "<any>", "note": null, "status": "pending"}, "evidence": {"cardinality": {"child_distinct": 50, "parent_distinct": 50, "parent_unique": true}, "containment": {"child_distinct": 50, "fraction": 1.0}, "name": {"rule": "same name as the parent key", "score": 1.0}, "range": {"child": null, "parent": null, "within": true}, "type": {"child": "integer", "compatible": true, "parent": "integer"}}, "id": "relationship:orders.customer_id->customers.customer_id", "kind": "relationship", "proposed_at": "<any>", "redacted": true, "subject": "orders.customer_id->customers.customer_id"}]}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"decisions": "/work/none.json"}, "command": "proposals_list", "id": "missing-file"}
```

```json response
{"api_version": "1.1", "command": "proposals_list", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.json"}, "id": "missing-file", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "proposals_decide", "id": "accept", "ok": true, "result": {"claim": {"child": "orders", "child_columns": ["customer_id"], "parent": "customers", "parent_columns": ["customer_id"], "type": "one_to_many"}, "confidence": 1.0, "decision": {"actor": "ana", "decided_at": "<any>", "note": "orders belong to customers", "status": "accepted"}, "evidence": {"cardinality": {"child_distinct": 50, "parent_distinct": 50, "parent_unique": true}, "containment": {"child_distinct": 50, "fraction": 1.0}, "name": {"rule": "same name as the parent key", "score": 1.0}, "range": {"child": null, "parent": null, "within": true}, "type": {"child": "integer", "compatible": true, "parent": "integer"}}, "id": "relationship:orders.customer_id->customers.customer_id", "kind": "relationship", "proposed_at": "<any>", "redacted": true, "subject": "orders.customer_id->customers.customer_id"}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"actor": "ana", "decisions": "/work/decisions.json", "proposal": "pii:nothing.here", "verb": "accept"}, "command": "proposals_decide", "id": "unknown-proposal"}
```

```json response
{"api_version": "1.1", "command": "proposals_decide", "error": {"code": "input.unknown_proposal", "group": "input", "hint": "run `proposals_list` for the ids", "message": "no proposal 'pii:nothing.here' in /work/decisions.json"}, "id": "unknown-proposal", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "project_validate", "id": "problems", "ok": true, "result": {"problems": [{"line": null, "message": "source name 'validate' is a `shape profile` subcommand", "path": "sources.validate"}, {"line": null, "message": "must not be empty", "path": "sources.validate.path"}], "valid": false}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"text": "format: shape-project\nversion: 2\nname: vectors\nsources:\n  orders:\n    path: a.csv\n    contract: contract.json\n    ignore: [status]\n    columns:\n      amount: {owner: finance@example.com}\ngates:\n  range_constraint: {mode: observe}\n"}, "command": "project_validate", "id": "newer-version"}
```

```json response
{"api_version": "1.1", "command": "project_validate", "error": {"code": "input.unsupported_format_version", "group": "input", "hint": "upgrade Shape to read it", "message": "shape.yml: version 2 is newer than this Shape understands (it reads up to version 1): upgrade Shape, or lower the file's version if it uses nothing newer"}, "id": "newer-version", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "project_show", "id": "sources", "ok": true, "result": {"file": "/work/shape.yml", "format": "shape-project", "gates": {"range_constraint": "observe"}, "name": "vectors", "sources": {"orders": {"baseline": null, "contract": "/work/contract.json", "dataset": false, "path": "/work/a.csv"}}, "version": 1}, "warnings": []}
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
- `verify` reports each gate's `mode` and `enforced_passed` (every enforced gate passed), and
  `passed` follows the exit code of `shape verify`: a failing gate that the project only observes
  does not fail the run. The result has the `project` block.

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
{"api_version": "1.1", "command": "diff", "id": "project-policy", "ok": true, "result": {"change_count": 2, "changes": [{"baseline": 100.625, "column": "amount", "current": 150.625, "kind": "mean_shift", "owner": "finance@example.com", "score": 0.7235, "severity": "medium"}, {"baseline": {"p05": 69.85, "p25": 85.75, "p50": 99.5, "p75": 114.25, "p95": 125.8}, "column": "amount", "current": {"p05": 119.85, "p25": 135.75, "p50": 149.5, "p75": 164.25, "p95": 175.8}, "kind": "distribution_shift", "owner": "finance@example.com", "score": 0.8616, "severity": "medium"}], "drifted": true, "project": {"file": "/work/shape.yml", "format": "shape-project", "source": "orders", "version": 1}}, "warnings": [{"code": "artifact_not_verified", "message": "/work/a.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}, {"code": "artifact_not_verified", "message": "/work/b.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}]}
```

```json request
{"api_version": "1.1", "args": {"after": "/work/b.shape", "before": "/work/a.shape", "project": "/work/shape.yml", "source": "ghost"}, "command": "diff", "id": "unknown-source"}
```

```json response
{"api_version": "1.1", "command": "diff", "error": {"code": "input.unknown_source", "group": "input", "hint": "run `project_show` for the sources", "message": "no source 'ghost' in /work/shape.yml (sources: orders)"}, "id": "unknown-source", "ok": false, "warnings": [{"code": "artifact_not_verified", "message": "/work/a.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}, {"code": "artifact_not_verified", "message": "/work/b.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}]}
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
{"api_version": "1.1", "command": "design", "id": "postgres-3nf", "ok": true, "result": {"ddl": "-- Schema design 'tiny' (3nf)\n\n-- relation: city\nCREATE TABLE \"dw\".\"city\" (\n    \"city_id\"                      BIGINT               NOT NULL,\n    \"city\"                         VARCHAR(255)         NULL,\n    CONSTRAINT \"PK_city\" PRIMARY KEY (\"city_id\")\n);\n\n-- relation: city_city\nCREATE TABLE \"dw\".\"city_city\" (\n    \"city\"                         VARCHAR(255)         NOT NULL,\n    \"country\"                      VARCHAR(255)         NULL,\n    CONSTRAINT \"PK_city_city\" PRIMARY KEY (\"city\")\n);\n\n-- foreign keys\nALTER TABLE \"dw\".\"city\" ADD CONSTRAINT \"FK_city_city\" FOREIGN KEY (\"city\") REFERENCES \"dw\".\"city_city\" (\"city\");\n", "lint": [], "passed": true, "tables": {"format": "shape-design-result", "mode": "3nf", "name": "tiny", "notes": [], "tables": [{"columns": [{"name": "city_id", "nullable": false, "type": "integer"}, {"name": "city", "nullable": true, "type": "string"}], "foreign_keys": [{"columns": ["city"], "ref_columns": ["city"], "ref_table": "city_city"}], "kind": "relation", "name": "city", "primary_key": ["city_id"], "source_entity": "City"}, {"columns": [{"name": "city", "nullable": false, "type": "string"}, {"name": "country", "nullable": true, "type": "string"}], "foreign_keys": [], "kind": "relation", "name": "city_city", "primary_key": ["city"], "source_entity": "City"}], "version": 1}}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"input": "/work/failing.design.json", "mode": "star"}, "command": "design", "id": "lint-errors"}
```

```json response
{"api_version": "1.1", "command": "design", "id": "lint-errors", "ok": true, "result": {"ddl": null, "lint": [{"code": "D001", "message": "fact 'sales' has no declared grain", "path": "facts.sales", "severity": "error"}], "passed": false, "tables": null}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"input": "/work/none.design.json"}, "command": "design", "id": "missing-file"}
```

```json response
{"api_version": "1.1", "command": "design", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.design.json"}, "id": "missing-file", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "design_from_data", "id": "a-csv", "ok": true, "result": {"entities": [{"attributes": [{"name": "id", "nullable": false, "type": "integer"}, {"max_length": 18, "name": "email", "nullable": false, "type": "string"}, {"max_length": 4, "name": "status", "nullable": false, "type": "string"}, {"name": "amount", "nullable": false, "type": "integer"}], "dependencies": [], "history": {"attributes": {}, "default": 1}, "keys": [["id"], ["email"]], "name": "orders"}], "facts": [], "format": "shape-design", "hierarchies": [], "name": "orders", "version": 1}, "warnings": []}
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
{"api_version": "1.1", "command": "format_schema", "id": "names", "ok": true, "result": {"names": ["decisions", "design-input", "generation-schema", "model", "model-v1", "model-v1-ga", "profile-engine", "project"]}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"name": "nope"}, "command": "format_schema", "id": "unknown-format"}
```

```json response
{"api_version": "1.1", "command": "format_schema", "error": {"code": "input.unknown_format", "group": "input", "hint": "call format_schema without a name for the list", "message": "no published format named 'nope' (formats: decisions, design-input, generation-schema, model, model-v1, model-v1-ga, profile-engine, project)"}, "id": "unknown-format", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "profile_show", "id": "a-profile", "ok": true, "result": {"content_id": "<any>", "name": "a", "signed": false, "summary": "<any>", "tables": {"a": {"columns": 4, "rows": 40}}}, "warnings": [{"code": "artifact_not_verified", "message": "/work/a.shape is not signed: its origin is not verified (check it with --verify PUBKEY)"}]}
```

```json request
{"api_version": "1.1", "args": {"path": "/work/none.shape"}, "command": "profile_show", "id": "missing-file"}
```

```json response
{"api_version": "1.1", "command": "profile_show", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.shape"}, "id": "missing-file", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "contract_validate", "id": "bad-rule", "ok": true, "result": {"errors": [{"location": "columns.id", "message": "unknown rules for column 'id': ['colour']"}], "kind": "check-contract", "valid": false, "warnings": []}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"path": "/work/none.json"}, "command": "contract_validate", "id": "missing-file"}
```

```json response
{"api_version": "1.1", "command": "contract_validate", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.json"}, "id": "missing-file", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "safe_scan", "id": "leaks", "ok": true, "result": {"clean": false, "findings": [{"message": "a numeric minimum and maximum pair: a raw minimum and maximum can identify a record", "pointer": "$.tables.people.columns.amount", "rule": "extreme-pair"}, {"message": "a value matches the email pattern", "pointer": "$.tables.people.columns.mail.example", "rule": "pii-regex"}, {"message": "a value matches the email pattern", "pointer": "$.tables.people.columns.mail.example", "rule": "pii-regex"}]}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"path": "/work/none.json"}, "command": "safe_scan", "id": "missing-file"}
```

```json response
{"api_version": "1.1", "command": "safe_scan", "error": {"code": "input.not_found", "group": "input", "hint": null, "message": "file not found: /work/none.json"}, "id": "missing-file", "ok": false, "warnings": []}
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
{"api_version": "1.1", "command": "verify", "id": "memorization-fails", "ok": true, "result": {"gates": [{"details": {"fail_at": "CONFIDENTIAL", "min_nn_distance": null, "tables": {"people": {"columns": ["email", "name"], "exact_match_rate": 1.0, "nn_distance": {"columns": ["age"], "median": 0.0, "min": 0.0, "p05": 0.0, "rows_checked": 20}, "reproduced_row_indices": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19], "reproduced_rows": 20, "restricted": true, "rows": 20, "source_rows": 40}}}, "errors": ["people: 20 of 20 generated rows reproduce a source row on the CONFIDENTIAL+ columns [email, name] (rows 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, ...)"], "name": "memorization", "passed": false, "warnings": []}], "passed": false, "row_counts": {"people": 20}, "statistical": false}, "warnings": []}
```

```json request
{"api_version": "1.1", "args": {"config": "/work/memo.json", "path": "/work/people_new"}, "command": "verify", "id": "needs-the-source"}
```

```json response
{"api_version": "1.1", "command": "verify", "error": {"code": "input.invalid_value", "group": "input", "hint": null, "message": "the verify configuration asks for the memorization or utility gate, which compare with the source data: give --source"}, "id": "needs-the-source", "ok": false, "warnings": []}
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
  scale run and a stream notice a cancel request; for any other running job it answers
  `input.job_state` (`"cancellable": false` in the job says which). A cancelled stream keeps its
  counts.
- `job_list` lists jobs oldest first (`status` filter, `limit` the most recent N).
- `scale_status` and `scale_cancel` are `job_status` and `job_cancel` for scale jobs.
  `stream_status` and `stream_stop` are the stream views. They take the stream's id, which is the
  job id.
- A `fabric_spark` run is submitted to Fabric and recorded as a job; its state is read from Fabric
  when it is asked for (`job_status`, `scale_status`), so those calls need the Fabric token: the
  argument `token`, else `$SHAPE_FABRIC_TOKEN`. A token is never written to a file.

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
`proposals_list`'s `proposals` and `design`'s `ddl`. Each time one does, the response has the
warning `result_in_file`. `generate` and `scale_generate` never return data: they
write files and return their paths. `profile` returns the path and `content_id` of the `.shape`
artifact it wrote (default location: `DIR/bridge/profiles/`, named by content id).

## Safe by default

`profile`, `profile_show`, `diff`, `check`, `verify`, `proposals_list`, `proposals_decide` and
`safe_scan` read real data or what was made from it. A result never includes the raw values of a
**classified column** unless the request sets `"options": {"include_raw_values": true}`.

A column is classified by the same rule the safe profile uses: a detected personal-data pattern
(email, SSN, credit card, phone, IP address, IBAN, postal code), or nearly every value distinct
(the free-text and identifier backstop). For a classified column:

- `profile`'s and `profile_show`'s `summary` have `min` and `max` set to `null` and
  `"redacted": true`;
- a `diff` change has `baseline` and `current` set to `null` and `"redacted": true`;
- a `check` violation has `observed` set to `null` and `"redacted": true`;
- the evidence of a proposal (`proposals_list`, `proposals_decide`) has its `range` endpoints set to
  `null` and the proposal has `"redacted": true`: a decision file does not say which columns are
  classified, so no value of the evidence is returned by default;
- a `safe_scan` finding's `message` never holds the value it found, and a pointer that is a
  personal-data value is `<redacted>`.

What stays is the column's shape (type, null rate, cardinality, pattern), which is not a value.
`verify` messages come from the gate schema and counts, never from data values, and the `details`
of its gates (1.1) hold counts, rates, distances and scores only.

Two things the default does not do: it does not change the `.shape` file `profile` writes, which is
the full profile and holds real values (the warning `profile_file_holds_values` says so; share a
safe profile, `shape profile safe`, not that file), and it does not touch `generate`, `preview` or
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

```console
$ shape bridge --jobs-dir ./jobs <<'EOF'
{"api_version": "1.0", "id": 1, "command": "list"}
{"api_version": "1.0", "id": 2, "command": "preview", "args": {"domain": "retail", "rows": 2, "tables": ["store"]}}
{"api_version": "1.0", "id": 3, "command": "generate", "args": {"domain": "retail", "scale": "small", "format": "parquet", "output_dir": "./out"}, "options": {"async": true}}
EOF
```

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
