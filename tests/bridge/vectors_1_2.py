"""The test vectors of the commands and arguments added in bridge 1.2 (W7-05).

``make_vectors.py`` merges these into the published vectors, as it merges ``vectors_1_1.py``:
``FILES`` are new vector files (one per new command) and ``EXTENDS`` adds setup and cases to the
vector files of earlier commands (their earlier cases stay as they are)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

D = "${DIR}"
V12 = "1.2"


def case(
    name: str,
    command: str,
    args: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    **flags: Any,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "api_version": V12,
        "id": name,
        "command": command,
        "args": args or {},
    }
    if options:
        request["options"] = options
    return {"name": name, "request": request, **flags}


def setup(command: str, **args: Any) -> dict[str, Any]:
    return {"api_version": V12, "id": "setup", "command": command, "args": args}


PROFILE_SHOP = setup("profile", source=f"{D}/shop", dataset=True, output=f"{D}/shop.shape")
PROPOSE_RULES = setup(
    "proposals_propose",
    profile=f"{D}/shop.shape",
    decisions=f"{D}/rules.json",
    kinds=["rule"],
    data=[f"{D}/shop"],
)


def accept(rule: str) -> dict[str, Any]:
    return setup(
        "proposals_decide", decisions=f"{D}/rules.json", proposal=rule, verb="accept", actor="ana"
    )


ACCEPTED = [
    accept("rule:customers.customer_id.unique"),
    accept("rule:orders.amount.dtype"),
    accept("rule:orders.row_count"),
]

FILES: dict[str, dict[str, Any]] = {
    "proposals_contract": {
        "setup": [PROFILE_SHOP, PROPOSE_RULES, *ACCEPTED],
        "cases": [
            case(
                "accepted-rules",
                "proposals_contract",
                {"decisions": f"{D}/rules.json", "output": f"{D}/contract_rules.json"},
            ),
            case(
                "merge",
                "proposals_contract",
                {
                    "decisions": f"{D}/rules.json",
                    "output": f"{D}/contract_merged.json",
                    "merge": f"{D}/contract_base.json",
                },
            ),
            case(
                "conflict",
                "proposals_contract",
                {
                    "decisions": f"{D}/rules.json",
                    "output": f"{D}/contract_conflict.json",
                    "merge": f"{D}/contract_conflicting.json",
                },
            ),
            case(
                "missing-output",
                "proposals_contract",
                {"decisions": f"{D}/rules.json"},
                valid_request=False,
            ),
        ],
    },
}

EXTENDS: dict[str, dict[str, Any]] = {
    "proposals_propose": {
        "cases": [
            case(
                "rules",
                "proposals_propose",
                {
                    "profile": f"{D}/shop.shape",
                    "decisions": f"{D}/rules.json",
                    "kinds": ["rule"],
                    "data": [f"{D}/shop"],
                },
            ),
        ]
    },
}


def write_fixtures(folder: Path) -> None:
    (folder / "contract_base.json").write_text(
        json.dumps({"tables": {"orders": {"row_count": {"min": 1}}}}, indent=2) + "\n"
    )
    (folder / "contract_conflicting.json").write_text(
        json.dumps({"tables": {"orders": {"row_count": {"min": 5}}}}, indent=2) + "\n"
    )
