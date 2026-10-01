# Pipeline integrations

Shape runs inside the pipelines of three platforms. Each has notebooks or a container, a pipeline
definition, tests, and a runbook with a live dry-run checklist (a live run is the owner's).

| Platform | What runs Shape | Pipeline | Runbook |
|---|---|---|---|
| Microsoft Fabric | Python and PySpark notebooks (exact, or distributed per partition), User Data Functions | `fabric/pipelines/` (Notebook and Functions activities) | [`fabric/RUNBOOK.md`](fabric/RUNBOOK.md) |
| Azure Synapse | PySpark notebook on a Spark pool | `synapse/pipelines/` (Notebook activity, If Condition, Fail) | [`synapse/RUNBOOK.md`](synapse/RUNBOOK.md) |
| Azure Data Factory | the Shape container image on an Azure Batch pool (Custom activity) | `adf/factory/` (Custom, Lookup, If Condition, Fail) | [`adf/RUNBOOK.md`](adf/RUNBOOK.md) |

All of them use one gate pattern: profile, check a contract, and fail the pipeline with the
violations when it does not hold. Tests: `pytest tests/demo/fabric` (needs Java for Spark; see the
runbooks).
