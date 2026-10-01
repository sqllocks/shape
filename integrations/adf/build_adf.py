"""Generate the Azure Data Factory definitions for the Batch Custom activity gate.

Run from the repo root:
    python integrations/adf/build_adf.py && ruff format integrations

Writes the ADF Git-integration layout under ``integrations/adf/factory/``: ``pipeline/``,
``dataset/`` and ``linkedService/``. The committed files hold placeholders of the form
``<<STORAGE_ACCOUNT>>``; fill them in (and add secrets through Key Vault references, never in the
JSON) before importing. tests/demo/fabric/test_adf.py fails if the committed files drift from this
script.

Gate pattern: a Custom activity runs the Shape container image on an Azure Batch pool
(``batch/run_gate.py``); the exit code fails the activity; the gate document it writes to ADLS is
read back by a Lookup activity (run on ``Completed``, so it also runs after a failed gate), and an
If Condition on ``passed`` fails the pipeline with the violations.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "factory"
PIPELINE_NAME = "shape_gate_batch"

POLICY = {
    "timeout": "0.02:00:00",
    "retry": 0,
    "retryIntervalInSeconds": 30,
    "secureInput": False,
    "secureOutput": False,
}

# The container command. ADF passes the pipeline's values in activity.json (extendedProperties),
# so nothing user-supplied is spliced into the shell command. VERIFY IN THE WORKSPACE ON FIRST
# RUN: the pool needs Docker (a container-enabled VM image), and the task user must be allowed to
# run it (autoUserSpecification below).
COMMAND = (
    '@concat(\'docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp '
    '-v "$AZ_BATCH_TASK_WORKING_DIR:/work" -w /work \', '
    "pipeline().parameters.image, ' python /work/run_gate.py --activity /work/activity.json')"
)

# The one expression the gate hinges on: the Lookup reads gate.json (written by run_gate.py).
PASSED_EXPRESSION = "@activity('ReadGate').output.firstRow.passed"
FAIL_MESSAGE_EXPRESSION = (
    "@concat('Shape gate failed for ', pipeline().parameters.sourceUrl, ': ', "
    "if(empty(activity('ReadGate').output.firstRow.error), "
    "string(activity('ReadGate').output.firstRow.violations), "
    "activity('ReadGate').output.firstRow.error))"
)
OUTPUT_URL = (
    "@concat('abfss://', pipeline().parameters.outputFileSystem, '@', "
    "pipeline().parameters.storageAccount, '.dfs.core.windows.net/', "
    "pipeline().parameters.outputFolder, '/', pipeline().RunId)"
)

PARAMETERS = {
    "sourceUrl": (
        "string",
        "abfss://data@<<STORAGE_ACCOUNT>>.dfs.core.windows.net/orders/orders.parquet",
    ),
    "contractUrl": (
        "string",
        "abfss://shape@<<STORAGE_ACCOUNT>>.dfs.core.windows.net/contracts/orders.json",
    ),
    "baselineUrl": ("string", ""),
    "failOnDrift": ("bool", False),
    "storageAccount": ("string", "<<STORAGE_ACCOUNT>>"),
    "outputFileSystem": ("string", "shape"),
    "outputFolder": ("string", "gates/orders"),
    "image": ("string", "ghcr.io/sqllocks/shape:0.9.0"),
    "scriptsFolder": ("string", "shape-batch"),
    "managedIdentityClientId": ("string", ""),
}


def _expr(text: str) -> dict:
    return {"value": text, "type": "Expression"}


def _param(name: str) -> dict:
    return _expr(f"@pipeline().parameters.{name}")


def pipeline() -> dict:
    return {
        "name": PIPELINE_NAME,
        "properties": {
            "description": (
                "Shape quality gate on an Azure Batch pool: the Shape container profiles the "
                "source and checks the contract; the exit code fails the Custom activity; the "
                "gate document in ADLS is read back by a Lookup to branch."
            ),
            "activities": [
                {
                    "name": "ProfileAndCheck",
                    "type": "Custom",
                    "dependsOn": [],
                    "policy": POLICY,
                    "userProperties": [],
                    "linkedServiceName": {
                        "referenceName": "ShapeBatch",
                        "type": "LinkedServiceReference",
                    },
                    "typeProperties": {
                        "command": _expr(COMMAND),
                        "resourceLinkedService": {
                            "referenceName": "ShapeBatchStorage",
                            "type": "LinkedServiceReference",
                        },
                        "folderPath": _param("scriptsFolder"),
                        "extendedProperties": {
                            "sourceUrl": _param("sourceUrl"),
                            "contractUrl": _param("contractUrl"),
                            "baselineUrl": _param("baselineUrl"),
                            "failOnDrift": _param("failOnDrift"),
                            "managedIdentityClientId": _param("managedIdentityClientId"),
                            "outputUrl": _expr(OUTPUT_URL),
                        },
                        "retentionTimeInDays": 1,
                        "autoUserSpecification": "pool-admin",
                    },
                },
                {
                    "name": "ReadGate",
                    "type": "Lookup",
                    "dependsOn": [
                        {"activity": "ProfileAndCheck", "dependencyConditions": ["Completed"]}
                    ],
                    "policy": {**POLICY, "timeout": "0.00:10:00"},
                    "userProperties": [],
                    "typeProperties": {
                        "source": {
                            "type": "JsonSource",
                            "storeSettings": {
                                "type": "AzureBlobFSReadSettings",
                                "recursive": False,
                                "enablePartitionDiscovery": False,
                            },
                            "formatSettings": {"type": "JsonReadSettings"},
                        },
                        "dataset": {
                            "referenceName": "ShapeGateJson",
                            "type": "DatasetReference",
                            "parameters": {
                                "fileSystem": _param("outputFileSystem"),
                                "folderPath": _expr(
                                    "@concat(pipeline().parameters.outputFolder, '/', "
                                    "pipeline().RunId)"
                                ),
                            },
                        },
                        "firstRowOnly": True,
                    },
                },
                {
                    "name": "CheckGate",
                    "type": "IfCondition",
                    "dependsOn": [{"activity": "ReadGate", "dependencyConditions": ["Succeeded"]}],
                    "userProperties": [],
                    "typeProperties": {
                        "expression": _expr(PASSED_EXPRESSION),
                        "ifTrueActivities": [],
                        "ifFalseActivities": [
                            {
                                "name": "FailGate",
                                "type": "Fail",
                                "dependsOn": [],
                                "userProperties": [],
                                "typeProperties": {
                                    "message": _expr(FAIL_MESSAGE_EXPRESSION),
                                    "errorCode": "ShapeGateFailed",
                                },
                            }
                        ],
                    },
                },
            ],
            "parameters": {
                name: {"type": kind, "defaultValue": default}
                for name, (kind, default) in PARAMETERS.items()
            },
            "annotations": ["shape"],
        },
    }


def dataset() -> dict:
    return {
        "name": "ShapeGateJson",
        "properties": {
            "linkedServiceName": {"referenceName": "ShapeAdls", "type": "LinkedServiceReference"},
            "parameters": {"fileSystem": {"type": "string"}, "folderPath": {"type": "string"}},
            "annotations": [],
            "type": "Json",
            "typeProperties": {
                "location": {
                    "type": "AzureBlobFSLocation",
                    "fileName": "gate.json",
                    "folderPath": _expr("@dataset().folderPath"),
                    "fileSystem": _expr("@dataset().fileSystem"),
                }
            },
        },
    }


def _key_vault_secret(secret: str) -> dict:
    return {
        "type": "AzureKeyVaultSecret",
        "store": {"referenceName": "ShapeKeyVault", "type": "LinkedServiceReference"},
        "secretName": secret,
    }


def linked_services() -> dict[str, dict]:
    return {
        # the ADLS Gen2 account the Lookup reads gate.json from (factory managed identity)
        "ShapeAdls": {
            "name": "ShapeAdls",
            "properties": {
                "annotations": [],
                "type": "AzureBlobFS",
                "typeProperties": {"url": "https://<<STORAGE_ACCOUNT>>.dfs.core.windows.net"},
            },
        },
        # Blob storage that holds the batch/ scripts the Custom activity downloads to the node
        "ShapeBatchStorage": {
            "name": "ShapeBatchStorage",
            "properties": {
                "annotations": [],
                "type": "AzureBlobStorage",
                "typeProperties": {
                    "serviceEndpoint": "https://<<STORAGE_ACCOUNT>>.blob.core.windows.net"
                },
            },
        },
        # the Azure Batch account and pool (a Linux pool with Docker); the key is a Key Vault secret
        "ShapeBatch": {
            "name": "ShapeBatch",
            "properties": {
                "annotations": [],
                "type": "AzureBatch",
                "typeProperties": {
                    "accountName": "<<BATCH_ACCOUNT>>",
                    "accessKey": _key_vault_secret("<<BATCH_KEY_SECRET_NAME>>"),
                    "batchUri": "https://<<BATCH_ACCOUNT>>.<<BATCH_REGION>>.batch.azure.com",
                    "poolName": "<<BATCH_POOL>>",
                    "linkedServiceName": {
                        "referenceName": "ShapeBatchStorage",
                        "type": "LinkedServiceReference",
                    },
                },
            },
        },
        "ShapeKeyVault": {
            "name": "ShapeKeyVault",
            "properties": {
                "annotations": [],
                "type": "AzureKeyVault",
                "typeProperties": {"baseUrl": "https://<<KEY_VAULT>>.vault.azure.net/"},
            },
        },
    }


def build() -> dict[str, dict]:
    files = {f"pipeline/{PIPELINE_NAME}.json": pipeline(), "dataset/ShapeGateJson.json": dataset()}
    for name, doc in linked_services().items():
        files[f"linkedService/{name}.json"] = doc
    return files


def main() -> None:
    for rel, doc in build().items():
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print("wrote", path)


if __name__ == "__main__":
    main()
