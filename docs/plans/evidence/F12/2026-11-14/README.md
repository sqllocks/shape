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
  invokes the unchanged existing live test body using the notebook identity, capturing
  and reusing its profile result for the summary, and bypassing
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

## Caller-token identity, exit retrieval and size checks

`Agent(access_token=...)`, `Agent(token_provider=...)` and the caller-supplied
`FABRIC_ACCESS_TOKEN` environment variable now support a human-signed-in agent.
Precedence is explicit access token, callable provider, environment access token,
then the original service-principal credential path. An invalid/expired supplied
user token fails; it never silently changes to the service principal. A callable
returns a current token before each API request. Tokens are only held in memory and
sent in Authorization headers; they are never printed or saved to the state/ledger.
Use a delegated Fabric token for the intended human identity, with sufficient
workspace and API permissions. The helper does not prove a token is a user token
or switch identities on its own; supplying the correct token is the caller's duty.
Do not acquire tokens by printing them, pasting them into chat or shell history, or
having an agent control the Owner's signed-in browser.

Setup, create, resume, run submission, monitoring and cleanup use that same selected
caller identity. Keep the caller/provider on the same identity across the lifecycle.
When a human token submits the Jupyter job, the job is submitted as that user;
cell 5 uses sempy's current notebook identity and cell 7 uses the same notebook
identity for Fabric REST, Key Vault and Kusto access. Neither cell invokes the
service-principal fixture or receives the caller token as a notebook parameter.
Under the retained service-principal path, those external operations are submitted
as the service principal and the notebook uses the identity Fabric supplies to that
job. Opening and running the notebook interactively uses the signed-in notebook
user. The original local service-principal test receipt remains a separate failed
attempt; no user-authenticated live run has been performed by this revision.

`status()` converts the recorded job Location to the notebook-specific
`/notebooks/{notebookId}/jobs/execute/instances/{jobInstanceId}?beta=true` form, checks
origin/workspace/owned-notebook references, and reports exit-value availability.
It accepts top-level `exitValue` and `properties.exitValue` without logging raw
responses; only allowlisted measurement fields are emitted. If the beta call is
unavailable, or the exit value is missing, empty or unusable, the Owner must copy
the safe JSON records from the notebook cells (before/after install, resource
limits, generation/profile/child exits, semantic summary and readiness/event counts),
plus the observed runtime/resources and cleanup confirmation. Never copy parameter
values, debug output, variable inspectors or connection details.
[Learn documents the beta exit-value endpoint](https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-public-api).

[Learn limits notebook content and snapshots to 32 MB](https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-limitation).
The reviewed [Create Item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/create-item),
[Create Notebook](https://learn.microsoft.com/en-us/rest/api/fabric/notebook/items/create-notebook)
and [notebook definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition)
references specify no numeric general HTTP-body limit. Therefore the helper does
not claim that 32 MB is a published wire/request-body limit. It enforces a
conservative 32,000,000-byte content budget and a separate local 32,000,000-byte
encoded-request budget. Both are checked before notebook creation. The preflight
receipt gives the exact serialized notebook, InlineBase64 and full JSON request
sizes; `render()` and `run()` stop if either budget is exceeded. Passing the check
is not proof that a service/proxy will accept the request.

Fallback: import the supplied unrendered notebook (without embedded wheel bytes)
as a new f12 notebook and upload the same three hash-verified wheels to builtin
using the Owner UI or the documented VS Code resource publisher. Retain Python
3.12, default 2 vCore/16 GB and no Environment. The existing install fallback reads
those exact builtin files. Do not raise the size budgets or replace/rebuild wheels
to conceal a service rejection. See OWNER_STEPS.md for the Owner-only steps.
