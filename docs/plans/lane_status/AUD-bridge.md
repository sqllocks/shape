# AUD-bridge — audit and fix: bridge, API, errors, types

Branch `lane/AUD-bridge` (from `origin/build/main-plan` at `5c91ea5`). Area: `src/shape/bridge/**`,
`src/shape/api.py`, `src/shape/errors.py`, `src/shape/types.py`, `tests/bridge/**`, `tests/types/**`,
`docs/BRIDGE.md`, `docs/bridge/**`, `docs/API_STABILITY.md`.

Status: **phase 1 (hunt) done; phase 2 (file) and 3 (fix) in progress.**

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
