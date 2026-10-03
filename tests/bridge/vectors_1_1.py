"""The test vectors of the commands and arguments added in bridge 1.1 (W7-04).

``make_vectors.py`` merges these into the published vectors: ``FILES`` are new vector files (one
per new command) and ``EXTENDS`` adds setup and cases to the vector files of 1.0 commands (the
1.0 cases stay as they are)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_1_1 import shop_tables, write_dataset, write_design

D = "${DIR}"
V11 = "1.1"


def case(
    name: str,
    command: str,
    args: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    **flags: Any,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "api_version": V11,
        "id": name,
        "command": command,
        "args": args or {},
    }
    if options:
        request["options"] = options
    return {"name": name, "request": request, **flags}


def setup(command: str, **args: Any) -> dict[str, Any]:
    return {"api_version": V11, "id": "setup", "command": command, "args": args}


PROFILE_SHOP = setup("profile", source=f"{D}/shop", dataset=True, output=f"{D}/shop.shape")
PROPOSE = setup(
    "proposals_propose",
    profile=f"{D}/shop.shape",
    decisions=f"{D}/decisions.json",
    data=[f"{D}/shop"],
)
CUST = "relationship:orders.customer_id->customers.customer_id"

FILES: dict[str, dict[str, Any]] = {
    "proposals_propose": {
        "setup": [PROFILE_SHOP],
        "cases": [
            case(
                "shop",
                "proposals_propose",
                {
                    "profile": f"{D}/shop.shape",
                    "decisions": f"{D}/decisions.json",
                    "data": [f"{D}/shop"],
                },
            ),
            case(
                "missing-profile",
                "proposals_propose",
                {"profile": f"{D}/none.shape", "decisions": f"{D}/decisions.json"},
            ),
            case(
                "unknown-kind",
                "proposals_propose",
                {
                    "profile": f"{D}/shop.shape",
                    "decisions": f"{D}/decisions.json",
                    "kinds": ["mystery"],
                },
                valid_request=False,
            ),
        ],
    },
    "proposals_list": {
        "setup": [PROFILE_SHOP, PROPOSE],
        "cases": [
            case("all", "proposals_list", {"decisions": f"{D}/decisions.json"}),
            case(
                "relationships-only",
                "proposals_list",
                {"decisions": f"{D}/decisions.json", "kind": "relationship"},
            ),
            case("missing-file", "proposals_list", {"decisions": f"{D}/none.json"}),
            case(
                "bad-status",
                "proposals_list",
                {"decisions": f"{D}/decisions.json", "status": "done"},
                valid_request=False,
            ),
        ],
    },
    "proposals_decide": {
        "setup": [PROFILE_SHOP, PROPOSE],
        "cases": [
            case(
                "accept",
                "proposals_decide",
                {
                    "decisions": f"{D}/decisions.json",
                    "proposal": CUST,
                    "verb": "accept",
                    "actor": "ana",
                    "note": "orders belong to customers",
                },
            ),
            case(
                "unknown-proposal",
                "proposals_decide",
                {
                    "decisions": f"{D}/decisions.json",
                    "proposal": "pii:nothing.here",
                    "verb": "accept",
                    "actor": "ana",
                },
            ),
            case(
                "bad-verb",
                "proposals_decide",
                {"decisions": f"{D}/decisions.json", "proposal": CUST, "verb": "maybe"},
                valid_request=False,
            ),
        ],
    },
}

PROJECT = f"{D}/shape.yml"
PROJECT_YML = """\
format: shape-project
version: 1
name: vectors
sources:
  orders:
    path: a.csv
    contract: contract.json
    ignore: [status]
    columns:
      amount: {owner: finance@example.com}
gates:
  range_constraint: {mode: observe}
"""

FILES["project_validate"] = {
    "cases": [
        case("good-file", "project_validate", {"path": PROJECT}),
        case(
            "problems",
            "project_validate",
            {"text": "format: shape-project\nversion: 1\nsources:\n  validate: {path: ''}\n"},
        ),
        case("newer-version", "project_validate", {"text": PROJECT_YML.replace("1", "2", 1)}),
        case("missing-file", "project_validate", {"path": f"{D}/none.yml"}),
        case(
            "text-and-path",
            "project_validate",
            {"text": PROJECT_YML, "path": PROJECT},
        ),
    ]
}
FILES["project_show"] = {
    "cases": [
        case("sources", "project_show", {"path": PROJECT}),
        case("missing-file", "project_show", {"path": f"{D}/none.yml"}),
        case("needs-a-path", "project_show", {}, valid_request=False),
    ]
}

FILES["design"] = {
    "cases": [
        case("star", "design", {"input": f"{D}/retail.design.json", "mode": "star"}),
        case(
            "postgres-3nf",
            "design",
            {"input": f"{D}/tiny.design.json", "dialect": "postgres", "schema_name": "dw"},
        ),
        case("lint-errors", "design", {"input": f"{D}/failing.design.json", "mode": "star"}),
        case("missing-file", "design", {"input": f"{D}/none.design.json"}),
        case(
            "unknown-mode",
            "design",
            {"input": f"{D}/retail.design.json", "mode": "galaxy"},
            valid_request=False,
        ),
    ]
}
FILES["design_from_data"] = {
    "cases": [
        case("a-csv", "design_from_data", {"source": f"{D}/a.csv", "name": "orders"}),
        case("missing-file", "design_from_data", {"source": f"{D}/none.csv"}),
    ]
}

#: Setup and cases added to the vector files of 1.0 commands.
EXTENDS: dict[str, dict[str, Any]] = {
    "profile": {
        "cases": [
            case(
                "source-name",
                "profile",
                {"source": "orders", "project": PROJECT, "output": f"{D}/orders.shape"},
            ),
            case(
                "bad-project",
                "profile",
                {"source": "orders", "project": f"{D}/none.yml", "output": f"{D}/o.shape"},
            ),
        ]
    },
    "diff": {
        "cases": [
            case(
                "project-policy",
                "diff",
                {"before": f"{D}/a.shape", "after": f"{D}/b.shape", "project": PROJECT},
            ),
            case(
                "unknown-source",
                "diff",
                {
                    "before": f"{D}/a.shape",
                    "after": f"{D}/b.shape",
                    "project": PROJECT,
                    "source": "ghost",
                },
            ),
        ]
    },
    "check": {
        "cases": [
            case(
                "project-owner",
                "check",
                {
                    "profile": f"{D}/a.shape",
                    "contract": f"{D}/contract_bad.json",
                    "project": PROJECT,
                    "source": "orders",
                },
            ),
            case(
                "source-needs-a-project",
                "check",
                {"profile": f"{D}/a.shape", "contract": f"{D}/contract.json", "source": "orders"},
            ),
        ]
    },
    "verify": {
        "cases": [
            case(
                "observed-gate",
                "verify",
                {"path": "orders", "project": PROJECT, "config": f"{D}/ranges.json"},
            ),
            case(
                "bad-project",
                "verify",
                {"path": f"{D}/a.csv", "project": f"{D}/none.yml"},
            ),
        ]
    },
}


def write_fixtures(folder: Path) -> None:
    write_dataset(folder / "shop", shop_tables())
    for name in ("retail", "tiny", "failing"):
        write_design(folder, name)
    (folder / "shape.yml").write_text(PROJECT_YML)
    (folder / "ranges.json").write_text(
        json.dumps(
            {"format": "shape-verify-config", "version": 1, "ranges": {"a.amount": {"max": 50}}},
            indent=2,
        )
        + "\n"
    )
