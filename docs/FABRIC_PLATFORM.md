# Fabric platform inventory

Status: experimental.

Every Fabric and OneLake surface that Shape calls or targets, with its release stage. A test
(`tests/docs/test_fabric_platform.py`) reads the table below and fails when the code builds a
Fabric REST path or names an item type that is not listed, or is listed `preview` or `retired`,
and when a doc or plugin README names a Fabric Spark runtime that is not listed or whose end of
support is before its `Checked` date. Preview APIs can change or be withdrawn without notice, so
none is allowed in the code.

**Adding a Fabric call:** add its row first, with the Microsoft Learn page you read the status from
and today's date. If the page says preview, the call does not go in; open an issue instead.
`python scripts/fabric_platform.py` runs the same check by hand.

## The table

- `Kind`: `rest` (a Fabric REST or Kusto path), `item-type` (a Fabric item type string),
  `runtime` (a Fabric Spark runtime version) or `storage` (the OneLake endpoint).
- `Surface`: for `rest`, `METHOD /v1/path/{param}` as the code builds it; the guard compares paths
  with every `{param}` reduced to `{}`, so parameter names are free.
- `Status`: `GA`, `preview` or `retired`. For `rest`, `item-type` and `storage` rows a Learn
  reference page does not print a stage; it marks a preview API with a preview notice. `GA` here
  means the page was read on the `Checked` date and carries no preview notice. Where a page
  names a preview sub-feature, that is recorded under the table.
- `End of support`: runtimes only; `n/a` elsewhere.

| Kind | Surface | Status | End of support | Source | Checked | Used by |
|---|---|---|---|---|---|---|
| rest | GET /v1/workspaces | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/list-workspaces | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/fabric_api.py |
| rest | GET /v1/workspaces/{workspace_id}/items | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-items | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/fabric_api.py, plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | POST /v1/workspaces/{workspace_id}/items | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/items/create-item | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/fabric_api.py, src/shape/scale/spark.py, plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | DELETE /v1/workspaces/{workspace_id}/items/{item_id} | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/items/delete-item | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/fabric_api.py, plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | POST /v1/workspaces/{workspace_id}/items/{item_id}/updateDefinition | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/items/update-item-definition | 2026-10-03 | plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | GET /v1/workspaces/{workspace_id}/notebooks | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/notebook/items/list-notebooks | 2026-10-03 | src/shape/scale/spark.py |
| rest | POST /v1/workspaces/{workspace_id}/items/{item_id}/jobs/instances | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job | 2026-10-03 | src/shape/scale/spark.py |
| rest | GET /v1/workspaces/{workspace_id}/items/{item_id}/jobs/instances/{run_id} | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/get-item-job-instance | 2026-10-03 | src/shape/scale/jobs.py |
| rest | POST /v1/workspaces/{workspace_id}/items/{item_id}/jobs/instances/{run_id}/cancel | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/cancel-item-job-instance | 2026-10-03 | src/shape/scale/jobs.py |
| rest | GET /v1/operations/{operation_id} | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations/get-operation-state | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/fabric_api.py, src/shape/scale/spark.py, plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | GET /v1/operations/{operation_id}/result | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations/get-operation-result | 2026-10-03 | plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | GET /v1/workspaces/{workspace_id}/git/connection | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/git/get-connection | 2026-10-03 | plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | GET /v1/workspaces/{workspace_id}/git/status | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/git/get-status | 2026-10-03 | plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | POST /v1/workspaces/{workspace_id}/git/updateFromGit | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/git/update-from-git | 2026-10-03 | plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | POST /v1/workspaces/{workspace_id}/git/commitToGit | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/core/git/commit-to-git | 2026-10-03 | plugins/shape-fabric/tests/test_live_git_sync.py |
| rest | POST /v1/rest/mgmt | GA | n/a | https://learn.microsoft.com/en-us/kusto/api/rest/request | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/kusto.py |
| rest | POST /v1/rest/query | GA | n/a | https://learn.microsoft.com/en-us/kusto/api/rest/request | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/kusto.py, plugins/shape-fabric/tests/test_live.py |
| rest | POST /v1/rest/ingest/{database}/{table} | GA | n/a | https://learn.microsoft.com/en-us/kusto/api/rest/streaming-ingest | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/kusto.py |
| storage | https://onelake.dfs.fabric.microsoft.com | GA | n/a | https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api | 2026-10-03 | src/shape/scale/spark.py, src/shape/scale/http.py, plugins/shape-fabric/src/shape_fabric/onelake.py |
| item-type | Notebook | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/item-management-overview | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/notebook.py, plugins/shape-fabric/src/shape_fabric/scenarios.py, src/shape/scale/spark.py, plugins/shape-fabric/tests/test_live_git_sync.py |
| item-type | Environment | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/item-management-overview | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/scenarios.py |
| item-type | Lakehouse | GA | n/a | https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/item-management-overview | 2026-10-03 | plugins/shape-fabric/src/shape_fabric/scenarios.py |
| runtime | 2.0 | GA | 2028-08-31 | https://learn.microsoft.com/en-us/fabric/data-engineering/lifecycle | 2026-10-03 | integrations/fabric |
| runtime | 1.3 | retired | 2026-09-30 | https://learn.microsoft.com/en-us/fabric/data-engineering/lifecycle | 2026-10-03 | integrations/fabric |

### Notes on the rows

- **Stage wording.** None of the Learn reference pages above prints a stage (GA or preview); each
  was read on 2026-10-03 and none carries a preview notice. That is the evidence for `GA`, and it
  is weaker than a page that says "generally available". The runtime rows are different: the
  lifecycle page prints the stage and the end-of-support date.
- **Preview sub-features.** The Notebook and Eventstream item types are `GA`; Learn marks some of
  their features preview (notebook version history; Eventstream schema-aware streams, the Spark
  notebook and SQL operators). Shape uses none of them.
- **Run notebook.** The code starts a run with `.../jobs/instances?jobType=RunNotebook`. The
  reference page now documents `.../jobs/{jobType}/instances` and says the query-parameter form is
  still supported "for backward compatibility". The row is the path as the code builds it.
- **Operations.** The code follows the `Location` header of an accepted request, which is a
  `/v1/operations/{operation_id}` URL; the guard cannot see a URL that comes from a header, so
  these rows are kept by hand.
- **OneLake.** Shape writes files with the ADLS Gen2 calls (`PUT ?resource=file`,
  `PATCH ?action=append` and `?action=flush`). The OneLake page says OneLake "supports the same
  APIs as Azure Data Lake Storage (ADLS) and Azure Blob Storage"; it does not list the three calls.
- **Streaming ingestion.** The Kusto streaming-ingest page applies to Fabric and Azure Data
  Explorer and prints no stage; the Eventhouse overview does not state one for streaming
  ingestion either.
- **Runtime 1.3** passed its end-of-support date on 2026-09-30; Learn says it enters Long Term
  Support on 2026-10-01 for six months (through March 2027). It is listed `retired` so that a doc
  or plugin README that names it fails the guard. Today only `integrations/fabric` names it
  ("Runtime 1.3 also works"), which the guard does not scan; that text needs a decision.
- **Runtime 2.0** is the latest GA runtime; Learn says it becomes the default for new workspaces
  in late September 2026.

## Git sync

Teams keep `.shape` profiles in the same Git repository as their Fabric items (`shape git-setup`
sets up the text diff). `plugins/shape-fabric/tests/test_live_git_sync.py` (marked `live`) checks
that a `shape/` folder next to the item folders survives Fabric Git integration in both
directions. It needs a workspace that is connected to a Git repository and initialised. It:

1. creates a Notebook item and commits it to Git, so the repository holds an item folder;
2. pushes one commit with `shape/` (two `.shape` files written by `shape profile` from a generated
   table), the `.gitattributes` line from `shape git-setup` and one changed item definition;
3. calls "update from Git", waits, and asserts that it succeeded, that the workspace moved to the
   pushed commit, that the item change arrived (the workspace has no remaining change) and that
   the Git status reports no conflict;
4. changes the item in the workspace and calls "commit to Git";
5. fetches the branch and asserts that the `.shape` files and `.gitattributes` are byte-identical
   to what was pushed and that no file was added under `shape/`;
6. cleans up: removes `shape/` and the item folder from the branch it used, and deletes the item,
   also when a step failed.

The test works on the branch the workspace is connected to (it reads it from the Git connection);
it does not create a branch, because moving a workspace to another branch is a separate set of
calls. It commits only a `shape/` folder and one item folder, and removes them again.

Secrets (a missing one fails the test naming it, in this order): `FABRIC_TENANT_ID`,
`FABRIC_CLIENT_ID`, `FABRIC_CLIENT_SECRET` (a service principal that is a contributor of the
workspace), `FABRIC_WORKSPACE_ID`, `FABRIC_GIT_REMOTE` (an HTTPS remote of the repository the
workspace is connected to) and `FABRIC_GIT_TOKEN` (write access to it). `FABRIC_GIT_USER` is the
user name sent with the token (default `x-access-token`). The service principal also needs its
Git credentials set up for the workspace.

Run by hand:

<!-- example: 0 -->

**Needs a Fabric account. Not run in CI.**

```
pip install -e '.[dev]' -e 'plugins/shape-fabric[entra]'
FABRIC_TENANT_ID=... FABRIC_CLIENT_ID=... FABRIC_CLIENT_SECRET=... FABRIC_WORKSPACE_ID=... \
FABRIC_GIT_REMOTE=https://github.com/<owner>/<repo>.git FABRIC_GIT_TOKEN=... \
SHAPE_LIVE_RESULT=git-sync-result.json \
pytest -m live plugins/shape-fabric/tests/test_live_git_sync.py
```

<!-- owner: Fabric maintainer — supply the transcript for docs/FABRIC_PLATFORM.md example 0. -->


The nightly job `fabric-git-sync-live` runs it only where the secrets are set and uploads the
result file. The result is a `shape-live-check` document (`format`, integer `version`,
`shape_version`, `min_shape_version`, `check`, `passed`, `started`, `finished` and `steps`, each
step with a name, whether it passed and, when it failed, a short error). It holds no secret, tenant
id or workspace id. It follows the [state and compatibility policy](specs/STATE_AND_COMPATIBILITY.md):
`shape_fabric.livecheck.load` reads every version 1 file (also one written before
`min_shape_version` was declared) and refuses a newer `version` with an `UnsupportedVersionError`
that names the first Shape release that reads it.

Last recorded run: not run yet
