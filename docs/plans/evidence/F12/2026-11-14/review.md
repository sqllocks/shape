# Independent review — F12 evidence and Owner handoff

Reviewed `README.md`, `OWNER_STEPS.md`, the notebook, raw logs, `owner_memory.py`,
and `docs/plans/lane_status/F12.md` on 2026-11-14. This is a review of the attempted
evidence and prepared handoff; it does not qualify the Fabric workloads.

## Verdict

The prepared handoff is useful and its status is honestly described. No successful
live qualification is established. The numerical results in the attempt are traceable
to the included logs, and the 5M/50M counts and expected 100 row stream batches are
clearly workload inputs/assertions rather than observations. The handoff is ready for
the authorized Owner run; the workspace GUID noted during review has been replaced
with the workspace name.

## Findings

- **Resolved during review:** the semantic-model cell initially embedded a workspace
  GUID; it now sets `FABRIC_WORKSPACE_ID` to `Shape Test` (JSON line 133). This removes
  an unnecessary resource identifier from the handoff. The notebook prompts for the
  connection string via hidden input and prints neither it nor model column values.
- **Avoid duplicate model profiling:** the same cell calls the existing live test body
  (which invokes `profile_model`) and then invokes `profile_model` a second time to
  build its summary (JSON lines 142–144). This is read-only and does not invalidate the
  result, but doubles the profile request and may add time/throttling. Reuse the first
  result for the summary if practical.

## Evidence checks

- `semantic-model-test.txt` supports the stated failure: `list_workspaces` ran, then
  model listing failed with `Can not determine dotnet root` before profiling. It does
  not establish model read permission. Credential values and logged UUIDs are redacted;
  the local cache path remains but contains no apparent user name or credential.
- `notebook-api.jsonl` and `capture-api.jsonl` support the job-status statements,
  generic Spark-session failure, and deletion claims. They contain no runtime output
  or resource measurement. `local-harness-smoke.txt` is explicitly labeled local and
  its small run's values are present in that raw file.
- `owner_memory.py` uses the 5M/50M row counts as inputs and a separate ten-column
  synthetic dataset, not the D3 fixture. Its bounded-mode fresh-process RSS result is
  therefore useful as exploratory evidence only; it cannot close the plan's D3 RSS
  comparison. The prepared notebook has no outputs, so none of its workloads have run.
- The updated Owner steps create the destination table and map the four JSON fields
  before activating ingestion. The emitter/writer APIs add `_shape_table` and
  `_shape_seq`; the direct Eventhouse writer writes its separate `f12-direct` table.
  The queries retain raw and de-duplicated Eventstream counts and compare observed
  counts with expected values.
- The lane status accurately leaves the live work open, records the missing .NET
  runtime, and states that no existing Fabric item changed. O-07/O-10 and W2-05 remain
  open. The reviewed files do not claim successful qualification or name a private
  counterpart.

## Follow-up review — 2026-11-14

Reviewed the Owner handoff corrections and `handoff-source-audit.txt` plus
`handoff-revision-validation.txt`. The four requested corrections are present:

- pytest `Failed` is included in the semantic cell's handled exception tuple, so an
  assertion failure is emitted as failure JSON rather than escaping without summary;
- the connection string, Eventhouse host, and database prompts all use `getpass`;
- the Eventstream destination selects the already-created `f12-events` table, whose
  schema and JSON paths are specified before activation;
- the polling loop checks Eventstream unique counts and direct-writer visibility before
  success, and emits the final observed values plus a timeout/mismatch result.

The static audit supports read-only semantic-model behavior: metadata calls, DAX
evaluation for row reads, and TOM access with `readonly=True`. It also identifies
the direct writer's streaming-ingest call and delayed-visibility behavior. The
revision-validation output explicitly labels its simulated errors, delays, counts,
and times as local mock data. These are not Fabric execution evidence. No blocker
found for the prepared handoff or the authorized Owner run; no successful live
qualification is claimed. The earlier non-blocking duplicate `profile_model` call
remains and can result in a second read/profile request.

## Agent-first archive review — 2026-11-14

Reviewed `/workspace/f12-owner-handoff.zip`, including the extracted notebook,
helper, setup guide, Owner steps, README, manifest and local validation receipt.
The archive passed ZIP integrity checking. Its member sizes and SHA-256 values match
`handoff-build.json`, and all three included release wheels match their original
manifest hashes. The archive is now self-contained for the documented setup path;
the earlier missing-wheel concern is resolved.

The helper's inspected path has the expected safety gates: it refuses existing f12
items, persists only created item references in a mode-restricted ledger, validates
current item type/name before deletion, and refuses cleanup while the job is active.
Credential acquisition and Fabric calls keep tokens and raw responses in memory;
API bodies, SAS connection strings, host/database values and raw remote errors are
not printed or passed as job parameters. The notebook binds the SAS Endpoint and
EntityPath to source metadata from the new `f12-input`, or to explicit Owner-attested
metadata in the vault/hidden-input fallback. Unsupported hidden input becomes a
failure rather than echoed input.

Before any emit/write call, setup and the final cell require a Running source and
destination with no error, the expected ProcessedIngestion mode, new database/table,
and JSON serialization. Warning, Stopped, wrong mode, item mismatch, or endpoint
mismatch fails closed. Stream and direct counts are both polled; the safe exit-value
filter allows expected aggregate fields, dimensions, bounded error categories, and
numeric cgroup limits while dropping arbitrary strings and model table names.

`automation-validation.txt` reports only local mocks/static checks, including a
zero-send endpoint-mismatch case. These checks do not establish tenant API behavior,
Python installation/runtime, model access, activation, or event delivery. No live
Fabric request or mutation was made in this review. The archive contains a workspace
resource reference, but no principal ID, token, credential, SAS string, or private
counterpart name. No blocker found for the prepared handoff. The duplicate model
profile call noted above remains a non-blocking efficiency issue; no qualification
closure is supported.

## Caller identity, status and payload-size follow-up — 2026-11-14

Reviewed the refreshed helper, notebook, guidance and `caller-auth-validation.txt`,
then rechecked `/workspace/f12-owner-handoff.zip`. Caller-supplied access tokens and
token providers take precedence over the retained service-principal flow; a bad
caller token fails without changing identities. Provider tokens are requested for
each API call. The reviewed code uses them only in memory and in the Authorization
header; the state ledger contains item/job references, not tokens.

`status()` validates the saved Location's Fabric origin, workspace path and owned
notebook reference, then forms the notebook-specific beta execute-instance URL. It
handles top-level and nested `properties.exitValue`, reports missing/empty values,
and emits only allowlisted result fields. The single-profile wrapper in the notebook
captures the existing test's result, restores the original binding in `finally`,
and reuses that result for the summary.

The rendered payload preflight is backed by `automation-validation.txt`: serialized
content 4,297,696 bytes, InlineBase64 payload 5,730,264 bytes, and full JSON request
5,730,454 bytes. These are local serialization results, not a Fabric request or
service acceptance. The documented 32 MB content budget and separately labeled
32 MB local request budget are distinct. The archive's per-file hashes match its
manifest, and the three included wheel hashes still match the original source
manifest. `caller-auth-validation.txt` identifies its credential, exit-value,
oversize, and profile-capture checks as local mocks; it contains no real token or
live job result.

No further blocker found. The helper and notebook revisions remain prepared but
unexecuted against Fabric; no model profile, Python runtime/resource measurement,
or Eventstream count qualification is closed.
