# `shape bridge`: Shape as a JSON protocol

`shape bridge` serves Shape's commands over standard input and output as a versioned JSON
request/response protocol. Editors, notebooks, wrappers and agents use it to run Shape without
parsing command-line text.

```console
$ echo '{"api_version": "1.0", "id": "r1", "command": "dry_run", "args": {"domain": "retail"}}' | shape bridge
{"api_version": "1.0", "command": "dry_run", "id": "r1", "ok": true, "result": {...}, "warnings": []}
```

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
| `api_version` | `"MAJOR.MINOR"`. Optional (assumed current, with a warning) but you should send it. |
| `id` | Any string or integer of at most 128 characters. Echoed in the response; use it to match answers. |
| `command` | One of the commands below. |
| `args` | The command's arguments (an object; `null` means none). Unknown names are refused. A `null` value is the same as leaving the argument out. |
| `options` | `include_raw_values`, `async`, `max_inline_bytes` (below). |

A success:

```json
{"api_version": "1.0", "id": "r1", "command": "generate", "ok": true,
 "result": {...}, "warnings": [{"code": "result_in_file", "message": "..."}]}
```

A failure:

```json
{"api_version": "1.0", "id": "r1", "command": "generate", "ok": false,
 "error": {"code": "input.unknown_domain", "group": "input",
           "message": "no domain named 'nope' (installed: retail); ...",
           "hint": "run the `list` command"},
 "warnings": []}
```

Every request gets exactly one response, even a request that is not JSON (`id` is `null` then, and
`command` too when it could not be read). The bridge never exits on a bad request.

`warnings` are non-fatal: something the caller should know (for example `api_version_assumed`,
`newer_minor_version`, `artifact_not_verified`, `profile_file_holds_values`, `result_in_file`).
A warning's `code` is stable.

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
[`docs/bridge/schema/index.json`](bridge/schema/index.json).

## Versions and the stability promise

`api_version` is `MAJOR.MINOR`; this bridge speaks **1.0**.

- **Minor versions only add:** a command, an optional argument, an option, a result field, an error
  code, a warning code. A client written for 1.0 keeps working on 1.7, so **a client ignores result
  fields it does not know**.
- **Within a major version nothing is renamed, removed, retyped or tightened:** no command, argument,
  result field, error code or the meaning of any of them.
- **A major version may break.** A bridge serves one major version. A request for another is refused
  with `usage.unsupported_version`, and the message names the supported range
  (`... this bridge serves 1.0 to 1.0`). The response always carries the bridge's own
  `api_version`, so a client can read it from the refusal.
- A request with a newer *minor* than the bridge's is served, with the warning
  `newer_minor_version`: what the newer minor added is not available.
- Persisted files declare their own `format` and integer `version` (the job file below). A file
  written by a newer Shape is refused with `input.unsupported_format_version`, never misread.

The promise is enforced: the published schemas are generated from the code, a test fails when the
committed files differ (so a change is a visible, deliberate edit), and a compatibility test per
command runs the published vectors against a live bridge: a result may gain fields, never lose one.

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
| `profile` | profile a file, folder, glob or Delta table into a `.shape` artifact (job-capable) | `source`\*, `output`, `name`, `dataset`, `version`, `as_of`, `fail_on_empty` |
| `diff` | compare two profiles | `before`\*, `after`\*, `policy`, `thresholds`, `column_thresholds`, `ignore_columns`, `only_columns` |
| `check` | check a profile against a contract | `profile`\*, `contract`\* |
| `verify` | run the validation gates over data files (job-capable) | `path`\*, `format`, `schema`, `config`, `statistical`, `strict` |
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
value always gives the same file. Parts that can spill: `preview`'s table `data`, `profile`'s
`summary`, `diff`'s `changes`, `check`'s `violations`, `verify`'s `gates`. Each time one does, the
response has the warning `result_in_file`. `generate` and `scale_generate` never return data: they
write files and return their paths. `profile` returns the path and `content_id` of the `.shape`
artifact it wrote (default location: `DIR/bridge/profiles/`, named by content id).

## Safe by default

`profile`, `diff`, `check` and `verify` read real data. A result never includes the raw values of a
**classified column** unless the request sets `"options": {"include_raw_values": true}`.

A column is classified by the same rule the safe profile uses: a detected personal-data pattern
(email, SSN, credit card, phone, IP address, IBAN, postal code), or nearly every value distinct
(the free-text and identifier backstop). For a classified column:

- `profile`'s `summary` has `min` and `max` set to `null` and `"redacted": true`;
- a `diff` change has `baseline` and `current` set to `null` and `"redacted": true`;
- a `check` violation has `observed` set to `null` and `"redacted": true`.

An entry about several columns (the joint analysis: a broken dependency `a -> b`, an association
`a ~ b`, a placeholder surge, a reference pair) is withheld when any of its columns is classified,
and then its `message` and `detail`, which quote values, are `null` too.

What stays is the column's shape (type, null rate, cardinality, pattern), which is not a value.
`verify` messages come from the gate schema and counts, never from data values: the actual
minimum or maximum a range gate reports is withheld (`(actual max withheld)`, and the gate has
`"redacted": true`) unless the request sets `include_raw_values`.

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
| `verify` | `shape verify` (`shape.quality.VerifyRunner`) |

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
