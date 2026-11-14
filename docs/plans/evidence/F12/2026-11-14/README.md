# F12 qualification attempt — 2026-11-14

No default Python notebook memory or library-compatibility measurement is available yet.
No end-to-end Eventstream count qualification has been performed in this attempt.

- `notebook-api.jsonl`: a new notebook definition using Python notebook metadata was
  accepted, its scheduled job completed, and the notebook was deleted. This records
  job status only: no runtime output was retrieved and no Python resource size was verified.
- `capture-api.jsonl`: creation of a new scratch Lakehouse returned `InvalidInput`;
  no Lakehouse was created. A second notebook attempted to expose runtime details in a
  diagnostic exception. The job failed with a generic Spark-session error that contained
  no runtime details. That notebook was deleted. This is not a runtime measurement.
- `semantic-model-test.txt`: the existing live semantic-model test ran against the
  available model using the configured service principal. It failed before profiling,
  because the local environment could not determine the .NET root. No model profile
  was produced. The transcript replaces credential values and UUIDs with redactions;
  timing values are preserved. It does not establish model read permission.
- `local-harness-smoke.txt`: a small **local** harness smoke run, not Fabric evidence.
- `owner_memory.py`: prepared Shape generation and bounded profiling workload.
  The two row counts follow the public plan's 5M/50M sizes. The ten-column workload
  is explicitly different from the D3 benchmark fixture; it cannot close the D3 RSS
  comparison acceptance criterion. Generation writes Parquet in chunks; profiling runs
  in a fresh process per size. Peak RSS includes child imports and profiling, excludes
  parent generation, and retains the raw operating-system units. No RSS ratio is inferred.
- `f12-owner-qualification.ipynb`: unexecuted Owner handoff. The semantic-model cell
  invokes the unchanged existing live test body using the notebook identity, bypassing
  the service-principal pytest fixture; this must be reported distinctly from pytest.
  It prints profile dimensions rather than potentially sensitive column values.

All Fabric items created by this attempt were deleted; existing items were only listed.
There are no successful live qualifications to close. Owner instructions are in
`OWNER_STEPS.md`. The notebook is prepared for review, not a record of execution.

## Handoff revision after Owner checks

The notebook now captures pytest `Failed` outcomes as JSON and hides all three
connection-detail prompts. Destination setup selects the already-created table.
Count polling waits for both the Eventstream batches and direct-writer rows and
retains a mismatch if visibility does not arrive within the polling limit.
`handoff-revision-validation.txt` records simulated local failure/visibility checks;
its counts and times are mock inputs/outputs, not service observations.
`handoff-source-audit.txt` records the inspected Shape functions: semantic profiling
uses metadata reads and DAX queries, with `readonly=True` for TOM roles; the direct
writer uses the streaming-ingest endpoint, and its count helper documents delayed
visibility. This code audit does not establish successful live execution. The
notebook install and both requested dataset sizes remain unverified in Fabric.
The wheel artifacts have not been rebuilt or modified by this handoff revision.

## Agent-first revision

The current handoff replaces mandatory resource uploads with agent-side rendering of
the three unchanged wheels into notebook-definition code and hash-verified local
installation. `f12_agent.py` supplies guarded setup, explicit Jupyter submission,
filtered job status/exit records and ledger-based cleanup. `REST_SETUP.md` contains
the exact payloads, mode choice and Learn references. Current Learn documents
source-connection retrieval (including the SAS connection string) and destination
resume; these are no longer presumed portal-only. Only an actual service result can
establish that those APIs work for this tenant/identity. The earlier receipts remain
historical attempts, not tests of the revised automation.

`automation-validation.txt` records local mocks/static checks only. They validate
mode-specific payloads, four JSON paths, wheel hashes/rendering, a mocked pip launch,
no-send guards, private-output filtering and foreign-item cleanup refusal. They do
not establish Fabric installation, runtime configuration or event delivery. The
archive manifest keeps the original wheel `source_commit` and wheel digests and
adds digests for every other archive member, excluding the manifest itself.
