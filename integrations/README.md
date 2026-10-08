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

## Generation in pipelines

Each platform can also generate a domain, profile what it generated and check it against the contract
that the domain's own schema implies, so a generation that drifts from its schema fails the pipeline:

| Platform | Generate | Pipeline (generate, then profile, then check) |
|---|---|---|
| Microsoft Fabric | `fabric/notebooks/shape_generate.ipynb` (Delta tables in the lakehouse), `generateSample` in the User Data Functions | `fabric/pipelines/shape_generate_gate.DataPipeline` with `shape_profile_domain.ipynb` |
| Azure Synapse | `synapse/notebooks/shape_generate_synapse.ipynb` (Delta or Parquet in ADLS Gen2) | `synapse/pipelines/shape_generate_gate_synapse.json` with `shape_profile_domain_synapse.ipynb` |
| Azure Data Factory | `adf/batch/run_generate_gate.py` in the container (Parquet in ADLS Gen2) | `adf/factory/pipeline/shape_generate_gate_batch.json` |

A domain is a plugin: install `sqllocks-shape-domains` next to Shape on every platform (the runbooks say
how). Tests: `tests/integrations/test_fabric_generation.py` (helpers, contract, samples) and, in
`tests/demo/fabric/`, `test_generate.py`, `test_generate_udf.py`, `test_generate_synapse.py` and
`test_generate_adf.py`.
