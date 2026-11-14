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
