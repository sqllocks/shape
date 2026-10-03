"""The test vectors of the commands and arguments added in bridge 1.1 (W7-04).

``make_vectors.py`` merges these into the published vectors: ``FILES`` are new vector files (one
per new command) and ``EXTENDS`` adds setup and cases to the vector files of 1.0 commands (the
1.0 cases stay as they are)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from data_1_1 import mail_tables, shop_tables, write_dataset  # noqa: F401

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

#: Setup and cases added to the vector files of 1.0 commands.
EXTENDS: dict[str, dict[str, Any]] = {}


def write_fixtures(folder: Path) -> None:
    write_dataset(folder / "shop", shop_tables())
