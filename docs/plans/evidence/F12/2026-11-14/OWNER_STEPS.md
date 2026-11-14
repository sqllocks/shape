# F12 agent-first handoff

No new live run is claimed by this revision. Extract the zip in a private working
folder. Keep the supplied wheels and manifest together. Never enable HTTP/debug
logging, print API bodies, dump notebook variables, or paste connection details to
chat. Only items created by this run with the `f12-` prefix may be deleted. Existing
workspace items are never reused. Failures and timeouts stay recorded as failures.

## Agent path (no resource upload or UI activation required by the documented APIs)

Use the existing `FABRIC_TENANT_ID`, `FABRIC_CLIENT_ID`, and
`FABRIC_CLIENT_SECRET` environment variables, without printing them. In the repo,
run tools using `uv run --locked`; outside the repo the helper uses only the Python
standard library:

```sh
python f12_agent.py setup
python f12_agent.py run
python f12_agent.py status
```

Read each safe JSON receipt. Do not continue after a failure. `setup` creates only
**f12-eventhouse**, **f12-kql**, and **f12-eventstream**, including the table/schema,
then checks and if needed resumes ingestion. It refuses to reuse existing f12 items.
`run` creates **f12-owner-qualification** and submits a **Jupyter** job with 2 cores,
Python 3.12 notebook metadata, no Environment and no Lakehouse attachment. Do not
substitute the old generic RunNotebook request: the earlier probe used that path
and did not establish a Python session. Wait for the service's Retry-After before
calling `status` again. Check the observed runtime/resources; requested settings and
the documented default do not replace an observed measurement.

The Python session defaults to 2 vCore / 16 GB. The core wheel is specifically
**sqllocks_shape-0.9.4-py3-none-any.whl**; the other two are
**sqllocks_shape_fabric-0.9.4-py3-none-any.whl** and
**sqllocks_shape_eventhubs-0.9.4-py3-none-any.whl**. All original hashes are unchanged.

**Chosen install path:** the helper verifies the three wheel hashes and injects their
public bytes into the notebook definition in memory. The first/install cells decode
them into `/tmp/f12-wheels`, verify hashes again and install those exact local paths
with the running Python interpreter's pip. Diagnostics are captured, never echoed.
This composes documented notebook-definition creation and Python-kernel local pip
installation; it does **not** claim a notebook-resources REST API, nor is this
composition yet live-qualified. It avoids OneLake staging and another Fabric item.
Learn documents [notebook API creation/execution](https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-public-api),
[Python notebook libraries, default resources and equivalent inline pip commands](https://learn.microsoft.com/en-us/fabric/data-engineering/using-python-experience-on-notebook),
and the [explicit Jupyter execution request](https://learn.microsoft.com/en-us/rest/api/fabric/notebook/background-jobs/run-on-demand-notebook).
No Environment is used. `python f12_agent.py render` optionally creates the same
self-contained notebook locally for an Owner import; it creates no Fabric item.

The notebook retains the before/after package versions, imports, 5M/50M-row memory
workload, and semantic-model test body. The workload is Shape-generated with ten
columns, not the D3 fixture; no claim is made that 50M rows fits in memory. The model
cell uses the job identity or notebook user, and its output labels the current
notebook identity rather than claiming a separate service-principal pytest run.

## Noninteractive final cell and secret handling

The job receives only `F12_NONINTERACTIVE=True` and the newly created Eventstream
and KQL database item references. It retrieves the connection string through the
source-connection API and the Query URI/database internally from the KQL database
API. It never returns those responses or strings to output. The destination must be
Running, have no error, and match the ProcessedIngestion JSON destination/table and
new database; a Warning or other non-Running state prevents **all** event sends.

For a Key Vault fallback, configure `F12_VAULT_URL` and `F12_SECRET_NAME` as references
in the parameters cell or job parameters. The existing vault secret must be a JSON
object with keys `connection_string`, `host`, `database`, `eventstream_id`,
`source_id`, `namespace`, and `event_hub_name`. The Owner must obtain these references
and endpoint metadata from this run's new **f12-input**, never an existing stream. Supply values to the vault
securely, never in notebook source, job parameter values, shell arguments or chat.
The final cell calls `notebookutils.credentials.getSecret`, checks the item/source
references, and binds the connection string's Endpoint/EntityPath to the expected
namespace/event-hub metadata. Source-connection API metadata takes precedence when
available; otherwise the vault bundle is an explicit Owner attestation of that
binding. The hidden host/database must also match the new database's API metadata. An unattended run fails
closed if its secret/API route is unavailable; it never waits for input. An
interactive run may instead use hidden prompts; they abort rather than fall back
to echoing input if hidden input is unsupported. The `f12-` database assert
is retained inside the failure handler. See [NotebookUtils credentials](https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-credentials).

**An API does return the connection string:** [Get Eventstream Source Connection](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-source-connection)
documents CustomEndpoint responses containing
`accessKeys.primaryConnectionString` and `eventHubName`, with service-principal
support. This supersedes the earlier portal-only assumption; availability in this
tenant has not been observed. Do not log the response. If it is unavailable but
readiness checks succeed, the Owner-only fallback is: open **f12-eventstream**,
select **f12-input > Details > Event Hub > SAS Key Authentication**, and copy the
primary connection string directly to the hidden prompt or vault secret. Copy the
namespace and Event Hub name from that **same f12-input Details pane** into the
additional hidden metadata prompts or vault fields. The cell rejects an Endpoint
or EntityPath mismatch before any send. Without the API, this association requires
the Owner to verify the selected f12 source; do not attest a different endpoint.
Nothing is handed through chat. See [portal custom-endpoint details](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-source-custom-app).

[Entra authentication is supported by the platform](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/custom-endpoint-entra-id-auth).
It does not currently replace SAS for these unchanged Shape APIs:
`eventstream.py:88–94` requires a connection string; `EventstreamWriter` uses that
emitter. The underlying Event Hubs emitter supports Entra credentials, but bypassing
the Eventstream emitter's guard would require an implementation change and separate
live qualification. No emitter/writer or wheel change was made. If tenant policy
disables SAS, report that result; this handoff does not silently switch protocols.

## Exact Eventstream setup and readiness

The calls, KQL schema/mapping, selected ingestion mode and activation endpoint are
spelled out in **REST_SETUP.md** and implemented by `f12_agent.py`. Activation is
API-capable via **Resume Eventstream Destination**, not inherently manual. If the API
cannot reach Running, stop without sending; do not treat successful creation or a
null error as evidence of ingestion. The Owner-only fallback is to inspect the new
f12 destination in the portal, complete **Activate ingestion**, and rerun the same
API readiness check. Warning must be resolved before sending; do not bypass it.

## Model-read claim and limits

The Shape path calls sempy listing and DAX-read functions. In this checkout,
`plugins/shape-fabric/src/shape_fabric/semantic_metadata.py:73` also passes
`readonly=True` to `connect_semantic_model` when reading roles; this is outside the
two files originally inspected. `profile_model` (declared at `semantic_profile.py:114`) calls that metadata path.
Only the Shape source was inspected; sempy's internal implementation was not audited.
This is a source-level observation, not proof from a completed tenant run.

## Results, manual fallbacks and cleanup

The notebook records only versions, dimensions, timings, counts, booleans and error
categories. Its final unattended cell returns those structured records through the
notebook exit value. `status` reports service status and filters an available exit value through a
field allowlist; it drops endpoint fields, arbitrary strings and table names. It
never echoes raw response bodies. If exit-value retrieval is unavailable, the remaining manual step is to open
only **f12-owner-qualification**, copy its safe JSON outputs, and record the observed
Python/resource/runtime label as text. Do not copy variable inspectors or debug logs.
Both installed metadata versions and loaded module versions are returned; a
cache/version mismatch skips the workloads rather than claiming compatibility.
Any install/restart requirement or unsupported parameter/job behavior is a failed
attempt; report the safe receipt and stop for the Owner rather than change runtimes.

The expected unique counts are 100 each for emitter and stream writer, plus a
100-row direct-writer control. These are expectations, never substitute observations.
Both stream and direct counts are polled; direct writes use streaming ingestion and
can take seconds to become visible. Retain raw duplicates and a final mismatch.
For an independent Owner query, use only the new f12 database:

```kusto
['f12-events'] | where marker == 'f12-live' | summarize raw_rows=count() by _shape_table
```
```kusto
['f12-events'] | where marker == 'f12-live' | summarize by _shape_table, _shape_seq | summarize unique_rows=count() by _shape_table
```
```kusto
['f12-direct'] | count
```

When the job is terminal, run:

```sh
python f12_agent.py cleanup
```

Cleanup uses the run's private ID ledger and rechecks each current item's prefix and
type. It refuses deletion while the job is active, and never deletes by prefix
alone. Service-created default f12 databases are tracked only if their parent is the
new Eventhouse. Keep the ledger private; it contains item references, no endpoint
secrets. If provisioning times out before returning an item ID, Owner inspection
and cleanup of only that run's new f12 item may be necessary. Never reuse or delete
any pre-existing workspace item.
If a saved queryset is needed for manual queries, name it **f12-queries** and delete
it manually afterwards. Return safe outputs, observed runtime/resources, activation
result and cleanup confirmation. Do not return any connection detail.
