# AUD-bridge — audit and fix: bridge, API, errors, types

Branch `lane/AUD-bridge` (from `origin/build/main-plan` at `5c91ea5`). Area: `src/shape/bridge/**`,
`src/shape/api.py`, `src/shape/errors.py`, `src/shape/types.py`, `tests/bridge/**`, `tests/types/**`,
`docs/BRIDGE.md`, `docs/bridge/**`, `docs/API_STABILITY.md`.

Status: **done: 11 defects fixed (8 filed by this lane, 2 filed by AUD-api, plus the #546 group); 1 left for the lead (#541), 1 left open (finding 14), 1 filed outside the area (#543).**

## Phase 1: baseline

- `pytest tests/bridge tests/types -m "not emulator and not live" --cov=shape.bridge --cov=shape.api
  --cov=shape.errors --cov=shape.types --cov-report=term-missing`: 409 passed; coverage 93% in all
  (bridge 92–100% per module, `api.py` 28%, `types.py` 71%: both are exercised by other suites).
- The environment needs `pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains`
  (as CI does); without the domains plugin `test_catalog.py` fails at the first test.

## Findings

Severity: critical / high / medium / low. "Repro" commands run from a scratch directory, with
`b = shape.bridge.core.Bridge(Path("jobs"))` and `call(cmd, options, **args)` sending one request.

1. [#533] **high — privacy: `diff` and `check` return raw values of classified columns through joint
   entries.** `src/shape/bridge/handlers/flow.py:84` (`redact_entries`) redacts an entry only when
   its `column` is a classified name, and only `baseline`/`current`/`observed`. A joint-analysis
   entry's `column` is a label (`"email -> region"`, `"a ~ b"`, `"(a, b) in ref"`), and its raw
   values sit in `message`, `detail` (`violations[].determinant_value`, `dependent_values`,
   `placeholders`, `examples`, `value`) and in a `check` fd violation's `observed.violations`.
   Repro: 3000 rows, `email` with 40 distinct values (pattern `email`) that determines `region`;
   in the second file 10 emails map to two regions. `profile` both, then `diff` → change
   `dependency_broken`, column `email -> region`, message `worst: email='person0@secret.com' ...`;
   `check` with `{"fd": [{"determinant": "email", "dependent": "region", "min_confidence": 0.99}]}`
   → `observed.violations[].determinant_value` holds the e-mails. Expected (docs/BRIDGE.md "Safe by
   default"): a classified column's raw values are never returned without
   `options.include_raw_values`. Actual: returned.
2. [#535] **high — privacy: `verify` returns raw extremes in range-gate messages.**
   `src/shape/bridge/handlers/flow.py:220` passes `g.errors` through; `range_constraint` writes
   `(actual max: 1037913.0)`. Repro: `vd/people.csv` with a salary column, config
   `{"format": "shape-verify-config", "version": 1, "ranges": {"people.salary": {"max": 100000}}}`,
   `verify path=vd config=vc.json` → `"people.salary: 1 values above maximum 100000 (actual max:
   1037913.0)"`. Expected (docs/BRIDGE.md): "`verify` messages come from the gate schema and counts,
   never from data values". Actual: a data value.
3. [#537] **high — data corruption: concurrent `profile` jobs overwrite each other's artifact.**
   `src/shape/bridge/handlers/flow.py:136` saves to `profiles/.new-<pid>.shape`, one name per
   process; async jobs run on threads of one process. Repro: six `profile` requests with
   `options.async` and no `output`, then `close()` → artifacts named by one content id hold another
   job's bytes, or a corrupt zip (`ArtifactError: invalid manifest`, `Bad CRC-32`). Expected: each
   job writes its own file, named by its own content id. Actual: shared scratch file.
4. [#539] **medium — the stdio bridge decodes requests with the locale's encoding, not UTF-8.**
   `src/shape/bridge/server.py:27` reads `sys.stdin` as configured by the locale. Repro:
   `printf '{"command":"validate","args":{"schema_path":"caf\xc3\xa9.json"}}\n' | LC_ALL=C
   PYTHONUTF8=0 shape bridge` → `file not found: caf\udcc3\udca9.json`; on Windows a pipe decodes
   with the ANSI code page (`cafÃ©.json`). Expected: JSON is UTF-8 (RFC 8259) and a request is read
   as UTF-8 everywhere; bytes that are not UTF-8 are `usage.invalid_json`. Actual: mangled paths.
5. [#541] **medium — `demo_*` answer `policy.capability_unavailable` although `shape demo` (P6-12) ships
   in this build.** `src/shape/bridge/handlers/demo.py:34`; `shape.demo.api` exists and says "which
   the JSON bridge calls too"; docs/BRIDGE.md says "until it does". **Pinned by existing tests**
   (`tests/bridge/test_demo.py::test_a_valid_demo_request_waits_for_shape_demo`,
   `test_the_demo_commands_are_marked_pending_...`) and the published schema index (`"pending":
   "P6-12"`): changing it changes an existing test's expectation, so **not fixed; for the lead**.
6. [#542] **medium — a Fabric job status outside the bridge's statuses leaves a job neither active nor
   final.** `src/shape/bridge/handlers/scale.py:250` (`_poll`) records whatever
   `shape.scale.jobs.Jobs.status` returns. Fabric's `Deduped` status is not in
   `shape.scale.jobs.STATUS_MAP` (outside this area: filed as #543, not fixed), so it becomes `"deduped"`. The bridge job's
   `status` is then outside the published `JOB` enum, `_poll` never asks Fabric again (not in
   `ACTIVE`) and `job_cancel` marks it cancelled locally without cancelling the Fabric run.
   Expected: a status the bridge does not know keeps the job active (and is reported).
7. [#544] **low — `job_status` serves another job's record, and a malformed `worker` gives
   `internal.error`.** `src/shape/bridge/jobs.py:118` (`check_record`) does not check that the
   record's `job_id` is the file's, nor `worker`'s type. Repro: copy `job-A.json` to
   `job-bbbbbbbbbbbb.json` → `job_status job-bbbbbbbbbbbb` answers job A (and updates A's file);
   a record with `"worker": "x"` makes `job_list` fail with `AttributeError`. (The other parts of
   one-bad-file-breaks-`job_list` are #289, fixed on lane AUD-security2.)
8. [#545] **low — a large `interval_seconds` makes a stream fail with `internal.error`.**
   `src/shape/bridge/handlers/scale.py:195`: `Event.wait(1e300)` raises `OverflowError: timestamp
   out of range for platform time_t`. Expected: the stream waits (or the value is refused as
   `usage.invalid_argument`). Actual: job `failed`, `internal.error`.
9. [#546] **low — `jsonable` turns NumPy scalars into strings.** `src/shape/bridge/handlers/common.py:66`:
   `np.int64(5)` → `"5"`, `np.bool_(True)` → `"True"`, an array → `"[1 2]"`. No handler returns one
   today (checked by instrumenting list, describe, dry_run, profile_info, generate, profile, diff,
   check and verify), but any future one changes a number's JSON type silently.
10. [#546] **low — result paths are relative when the jobs directory is.** `Bridge(Path("jobs"))` or
    `shape bridge --jobs-dir ./jobs` → spilled `path` `jobs/bridge/results/<id>.json` and
    `profile`'s `path`: a client in another directory cannot open them. docs/BRIDGE.md shows an
    absolute path.
11. [#546] **low — the request size limit counts characters and the trailing newline.**
    `src/shape/bridge/protocol.py:151`, `server.py:53`: a request of exactly `MAX_REQUEST_BYTES`
    bytes on one line is refused (`usage.request_too_large`); a request of 8.4 MB of `é`
    (4.2 M characters) passes the check. Expected: "at most 8388608 bytes".
12. [#546] **low — `Bridge.handle` can raise.** `src/shape/bridge/core.py:61` converts the result with
    `jsonable` outside the `try`; an exception there (a self-referencing result: `RecursionError`)
    escapes, against "whatever goes wrong becomes an error response".
13. [#546] **low — finished job threads are never forgotten.** `src/shape/bridge/jobs.py:248`: `_live`
    keeps every job's thread and event for the life of the process (a long session grows without
    bound, and `drain` sets events of jobs that ended long ago).
14. [not filed: left open] **low — `stream_stop` answers `stopped` for a stream that had already failed or was
    interrupted.** `src/shape/bridge/handlers/scale.py:217`. The result's `status` enum is
    `stopped | stop_timeout`; saying more needs a new value (a minor-version addition). Left open,
    see below.
15. [#251] **medium — public API: `shape.generate` ignores `n`, `relationships`, `mode` and `scale`
    depending on the input.** Already filed as #251 by AUD-api ("filed only").
16. [#260] **low — public API: `LogicalType` accepts any kind and raises `ValueError`, not
    `ShapeTypeError`.** Already filed as #260 by AUD-api.

Already filed and fixed on another lane (not repeated here): #279 (job files keep credentials
inside URIs and error messages) and #289 (jobs dir mode, one bad job file, trailing-newline ids),
both on `lane/AUD-security2`, which also changes `bridge/jobs.py`, `bridge/errors.py` and
`bridge/protocol.py`.

Checked and found sound: request parsing (ids, versions, unknown fields, `null` args, nested
depth), argument checking (types, enums, minimum, `1e999`), path handling of job ids (no
traversal), atomic `0600` job and spill writes, error mapping by type only, `internal.error`
keeping tracebacks out of responses, stdout purity, profile summary redaction, the published
schemas check, every domain and mode through `describe`/`dry_run`/`profile_info`/`preview`.

## Phase 3: fixes

Each defect: a regression test committed first (its failing output in the commit message), then
the fix. All pushed to `lane/AUD-bridge`.

| finding | issue | test commit | fix commit | what changed |
|---|---|---|---|---|
| 1 | #533 | `04a24050` | `6d67d9c2` | `flow.entry_columns` reads every column a joint entry names (label and `detail`); such an entry is withheld when any is classified, `message` and `detail` included |
| 2 | #535 | `bef2c04e` | `7cee8ab3` | `verify` replaces `(actual max: X)` with `(actual max withheld)` and marks the gate `redacted` unless `include_raw_values` |
| 3 | #537 | `323ac222` | `0483928d` | `profile` saves through a `mkstemp` scratch file per call |
| 4 | #539 | `e80907f2` | `4a57981c` | `serve` reads standard input's bytes as UTF-8 (`surrogateescape`); a request with a non-UTF-8 byte is `usage.invalid_json` |
| 6 | #542 | `c26228a6` | `082e24f0` | `_poll` keeps a job active on a status it has no name for, reported as `progress.fabric_status` |
| 7 | #544 | `5630d488` | `61cd5035` | `jobs.check_fields`: the record's `job_id` is its file's, and `command`, `status`, `created_at`, `progress`, `worker`, `external` have their types |
| 8 | #545 | `c34b99b9` | `3e6beee1` | the stream waits in steps of at most an hour |
| 9–13 | #546 | `d05fe886`, `b0430c61` | `9293c130` | size limit in UTF-8 bytes without the line end; jobs dir made absolute; NumPy scalars and arrays via `tolist`; `jsonable` inside `handle`'s `try`; finished threads dropped from `_live` |
| 15 | #251 | `d5cdaf1c` | `6541fef6` | `shape.generate` raises `TypeError` naming an argument its form cannot use |
| 16 | #260 | `e9bec1bf` | `1c13f720` | `LogicalType` checks its kind and bit width; invalid types raise `ShapeTypeError`, now also a `ValueError` |

Improvement (no defect): `e546aab9` tests for paths no test reached (scale-job error codes,
`writing`, `pid_alive`, `jsonable` of dates/decimals/bytes, a profile on a schema file, a spill that
fails half-way). Bridge coverage 92% → 97%; `types.py` 71% → 100%.

`docs/BRIDGE.md` describes each changed behaviour (joint redaction, verify, UTF-8, unknown Fabric
status). `CHANGELOG.md` has the Fixed entries (`3d3de6f9`).

## Left open, and why

- **#541 (finding 5), for the lead.** Wiring `demo_*` to `shape.demo.api` changes the expectation
  of two existing tests (`tests/bridge/test_demo.py::test_a_valid_demo_request_waits_for_shape_demo`,
  `::test_the_demo_commands_are_marked_pending_in_the_command_table_and_nothing_else_is`) and the
  published schema index (`"pending": "P6-12"`). The lane rules forbid changing an existing test's
  expectation, so it is recorded, not done.
- **Finding 14 (`stream_stop` on a failed stream answers `stopped`).** Saying more needs a new
  `status` value in the `stream_stop` result (a 1.x minor addition to a published schema); left for
  the lead to decide, not filed as a defect.
- **#543 (`scale`: `STATUS_MAP` lacks `Deduped`).** Outside this area: filed only. The bridge side
  (#542) no longer depends on it.

## Merging with other lanes

- `lane/AUD-security2` (fixes #277, #279, #289 in `bridge/jobs.py`, `errors.py`, `protocol.py`):
  `git merge-tree` shows no conflict with this branch.
- `lane/AUD-api` rewrote the docstrings of `src/shape/api.py` and `src/shape/types.py`: both files
  conflict, in docstrings only. Resolution: keep AUD-api's docstrings, and change their two
  "not used/validated (issue #251/#260)" sentences to what the code now does: `generate` raises
  `TypeError` for `n`/`relationships` with a domain or schema, `mode` with anything but a domain and
  `scale`/`mode` with an evidence document (a profile refuses `relationships` and `mode`);
  `LogicalType` raises `ShapeTypeError` (a `ValueError`) for an unknown kind, a bit width the kind
  does not have, and an incomplete decimal, list or map.

## Equivalence verifiers

No output that a verifier compares changed. `benchmarks/vs_spindle/bridge_1to1` compares `list`,
`describe`, `dry_run`, `profile_info`, `generate`, `preview` and the scale commands; none of their
results changed (the `jsonable` change only touches NumPy objects, which none returns, and paths
are not compared). `tests/bridge/test_parity_compare.py` passes. The harness itself needs
`$SPINDLE_ROOT`, which this container does not have.
