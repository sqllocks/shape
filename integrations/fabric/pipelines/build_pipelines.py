"""Generate the Fabric data-pipeline item definitions and (optionally) bind real item IDs.

    python integrations/fabric/pipelines/build_pipelines.py     # regenerate the committed JSON
    python integrations/fabric/pipelines/build_pipelines.py bind OUT_DIR \\
        --workspace-id GUID --notebook shape_profile=GUID --notebook shape_profile_spark=GUID \\
        --function-set GUID

Each pipeline is written as ``<name>/pipeline-content.json`` plus ``<name>/.platform``, the
item-definition parts Fabric imports (Git integration layout / Items REST API). Committed
files hold placeholders of the form ``<<WORKSPACE_ID>>``, ``<<NOTEBOOK_ID:shape_profile>>`` and
``<<FUNCTION_SET_ID>>``; ``bind`` fills them in. See integrations/fabric/RUNBOOK.md.

The notebook exit-value expression and the Functions-activity property names could not be
checked against a live workspace: mark them "verify in the workspace on first run".
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The one expression the gate hinges on. VERIFY IN THE WORKSPACE ON FIRST RUN (DM-4).
EXIT_VALUE = "json(activity('ProfileTable').output.result.exitValue)"
PASSED_EXPRESSION = f"@{EXIT_VALUE}.passed"
FAIL_MESSAGE_EXPRESSION = (
    "@concat('Shape gate failed for ', pipeline().parameters.tableName, ': ', "
    f"string({EXIT_VALUE}.violations))"
)

POLICY = {
    "timeout": "0.12:00:00",
    "retry": 0,
    "retryIntervalInSeconds": 30,
    "secureInput": False,
    "secureOutput": False,
}

NOTEBOOK_PARAMETERS = {
    "tableName": "string",
    "contractPath": "string",
    "baselinePath": "string",
    "outputDir": "string",
    "failOnDrift": "bool",
}


def _expr(text: str) -> dict:
    return {"value": text, "type": "Expression"}


def _notebook_gate(notebook: str, description: str) -> dict:
    return {
        "properties": {
            "description": description,
            "parameters": {
                "tableName": {"type": "string", "defaultValue": "orders_day1"},
                "contractPath": {"type": "string", "defaultValue": "contracts/orders.json"},
                "baselinePath": {"type": "string", "defaultValue": ""},
                "outputDir": {"type": "string", "defaultValue": "shape"},
                "failOnDrift": {"type": "bool", "defaultValue": False},
            },
            "activities": [
                {
                    "name": "ProfileTable",
                    "type": "TridentNotebook",
                    "dependsOn": [],
                    "policy": POLICY,
                    "typeProperties": {
                        "notebookId": f"<<NOTEBOOK_ID:{notebook}>>",
                        "workspaceId": "<<WORKSPACE_ID>>",
                        "parameters": {
                            name: {"value": _expr(f"@pipeline().parameters.{name}"), "type": kind}
                            for name, kind in NOTEBOOK_PARAMETERS.items()
                        },
                    },
                },
                {
                    "name": "CheckGate",
                    "type": "IfCondition",
                    "dependsOn": [
                        {"activity": "ProfileTable", "dependencyConditions": ["Succeeded"]}
                    ],
                    "typeProperties": {
                        "expression": _expr(PASSED_EXPRESSION),
                        "ifTrueActivities": [],
                        "ifFalseActivities": [
                            {
                                "name": "FailGate",
                                "type": "Fail",
                                "dependsOn": [],
                                "typeProperties": {
                                    "message": _expr(FAIL_MESSAGE_EXPRESSION),
                                    "errorCode": "ShapeGateFailed",
                                },
                            }
                        ],
                    },
                },
            ],
        }
    }


def _udf_gate() -> dict:
    def call(name: str, function: str, parameters: dict, depends: list) -> dict:
        return {
            "name": name,
            "type": "UserDataFunctions",
            "dependsOn": depends,
            "policy": POLICY,
            "typeProperties": {
                "functionSetId": "<<FUNCTION_SET_ID>>",
                "workspaceId": "<<WORKSPACE_ID>>",
                "functionName": function,
                "parameters": parameters,
            },
        }

    return {
        "properties": {
            "description": "Shape quality gate using User Data Functions (Functions activity).",
            "parameters": {
                "filePath": {"type": "string", "defaultValue": "demo/day1/orders.parquet"},
                "profilePath": {"type": "string", "defaultValue": "shape/orders/latest.shape"},
                "contract": {"type": "object", "defaultValue": {}},
            },
            "activities": [
                call(
                    "ProfileFile",
                    "profileLakehouseFile",
                    {
                        "filePath": {
                            "value": _expr("@pipeline().parameters.filePath"),
                            "type": "string",
                        },
                        "outputPath": {
                            "value": _expr("@pipeline().parameters.profilePath"),
                            "type": "string",
                        },
                        "maxMegabytes": {"value": 50, "type": "int"},
                    },
                    [],
                ),
                call(
                    "CheckContract",
                    "checkProfile",
                    {
                        "profilePath": {
                            "value": _expr("@pipeline().parameters.profilePath"),
                            "type": "string",
                        },
                        "contract": {
                            "value": _expr("@pipeline().parameters.contract"),
                            "type": "object",
                        },
                        "failOnViolation": {"value": True, "type": "bool"},
                    },
                    [{"activity": "ProfileFile", "dependencyConditions": ["Succeeded"]}],
                ),
            ],
        }
    }


# PF-06: generate a domain, profile the tables, check them against the domain's contract.
GENERATE_EXIT_VALUE = "json(activity('GenerateDomain').output.result.exitValue)"
DOMAIN_EXIT_VALUE = "json(activity('ProfileAndCheck').output.result.exitValue)"
GENERATE_NOTEBOOK_PARAMETERS = {
    "domain": "string",
    "scale": "string",
    "seed": "int",
    "mode": "string",
    "tablePrefix": "string",
    "writeMode": "string",
    "outputDir": "string",
}
DOMAIN_NOTEBOOK_PARAMETERS = {
    "domain": "string",
    "contractPath": "string",
    "tablePrefix": "string",
    "baselinePath": "string",
    "outputDir": "string",
    "failOnDrift": "bool",
}


def _generate_gate() -> dict:
    parameters = {
        "domain": {"type": "string", "defaultValue": "retail"},
        "scale": {"type": "string", "defaultValue": "small"},
        "seed": {"type": "int", "defaultValue": 42},
        "mode": {"type": "string", "defaultValue": ""},
        "tablePrefix": {"type": "string", "defaultValue": ""},
        "writeMode": {"type": "string", "defaultValue": "overwrite"},
        "outputDir": {"type": "string", "defaultValue": "shape"},
        "baselinePath": {"type": "string", "defaultValue": ""},
        "failOnDrift": {"type": "bool", "defaultValue": False},
    }
    generate_args = {
        name: {"value": _expr(f"@pipeline().parameters.{name}"), "type": kind}
        for name, kind in GENERATE_NOTEBOOK_PARAMETERS.items()
    }
    profile_args = {
        name: {"value": _expr(f"@pipeline().parameters.{name}"), "type": kind}
        for name, kind in DOMAIN_NOTEBOOK_PARAMETERS.items()
        if name != "contractPath"
    }
    # the contract is where the generate notebook wrote it
    profile_args["contractPath"] = {
        "value": _expr(f"@{GENERATE_EXIT_VALUE}.contractPath"),
        "type": "string",
    }
    return {
        "properties": {
            "description": (
                "Shape: generate a domain into lakehouse Delta tables, profile them, and check "
                "the profile against the domain's contract."
            ),
            "parameters": parameters,
            "activities": [
                {
                    "name": "GenerateDomain",
                    "type": "TridentNotebook",
                    "dependsOn": [],
                    "policy": POLICY,
                    "typeProperties": {
                        "notebookId": "<<NOTEBOOK_ID:shape_generate>>",
                        "workspaceId": "<<WORKSPACE_ID>>",
                        "parameters": generate_args,
                    },
                },
                {
                    "name": "ProfileAndCheck",
                    "type": "TridentNotebook",
                    "dependsOn": [
                        {"activity": "GenerateDomain", "dependencyConditions": ["Succeeded"]}
                    ],
                    "policy": POLICY,
                    "typeProperties": {
                        "notebookId": "<<NOTEBOOK_ID:shape_profile_domain>>",
                        "workspaceId": "<<WORKSPACE_ID>>",
                        "parameters": profile_args,
                    },
                },
                {
                    "name": "CheckGate",
                    "type": "IfCondition",
                    "dependsOn": [
                        {"activity": "ProfileAndCheck", "dependencyConditions": ["Succeeded"]}
                    ],
                    "typeProperties": {
                        "expression": _expr(f"@{DOMAIN_EXIT_VALUE}.passed"),
                        "ifTrueActivities": [],
                        "ifFalseActivities": [
                            {
                                "name": "FailGate",
                                "type": "Fail",
                                "dependsOn": [],
                                "typeProperties": {
                                    "message": _expr(
                                        "@concat('Shape generated data broke the contract of ', "
                                        "pipeline().parameters.domain, ': ', "
                                        f"string({DOMAIN_EXIT_VALUE}.violations))"
                                    ),
                                    "errorCode": "ShapeContractFailed",
                                },
                            }
                        ],
                    },
                },
            ],
        }
    }


def build() -> dict[str, dict]:
    return {
        "shape_gate_notebook": _notebook_gate(
            "shape_profile", "Shape quality gate using the Python notebook."
        ),
        "shape_gate_spark": _notebook_gate(
            "shape_profile_spark", "Shape quality gate using the PySpark notebook (Environment)."
        ),
        "shape_gate_udf": _udf_gate(),
        "shape_generate_gate": _generate_gate(),
    }


def platform(name: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "DataPipeline", "displayName": name},
        "config": {
            "version": "2.0",
            "logicalId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"shape/{name}")),
        },
    }


def write(dest: Path, replacements: dict[str, str] | None = None) -> None:
    for name, content in build().items():
        folder = dest / f"{name}.DataPipeline"
        folder.mkdir(parents=True, exist_ok=True)
        text = json.dumps(content, indent=2) + "\n"
        for old, new in (replacements or {}).items():
            text = text.replace(old, new)
        (folder / "pipeline-content.json").write_text(text, encoding="utf-8")
        (folder / ".platform").write_text(
            json.dumps(platform(name), indent=2) + "\n", encoding="utf-8"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("bind", help="write copies with real item IDs")
    b.add_argument("out_dir", type=Path)
    b.add_argument("--workspace-id", required=True)
    b.add_argument("--notebook", action="append", default=[], metavar="NAME=GUID")
    b.add_argument("--function-set", required=True)
    args = ap.parse_args()
    if args.cmd == "bind":
        rep = {"<<WORKSPACE_ID>>": args.workspace_id, "<<FUNCTION_SET_ID>>": args.function_set}
        for item in args.notebook:
            name, _, guid = item.partition("=")
            rep[f"<<NOTEBOOK_ID:{name}>>"] = guid
        write(args.out_dir, rep)
        print("bound pipelines written to", args.out_dir)
    else:
        write(HERE)
        print("wrote pipelines to", HERE)


if __name__ == "__main__":
    main()
