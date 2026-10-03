"""``demo_list``, ``demo_run``, ``demo_status`` and ``demo_cleanup`` (P6-11).

The demo scenarios are run by ``shape demo`` (work package P6-12), which this build does not
include yet. The four commands are specified and tested now: a request is checked against its
schema like any other, and until ``shape demo`` exists the command answers
``policy.capability_unavailable``. Wiring each handler to ``shape demo`` is the whole of what is
left (see ``docs/plans/lane_status/P6-11.md``)."""

from __future__ import annotations

from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    STR,
    STRS,
    arr,
    mapping,
    nullable,
    obj,
)
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command, Handler

PENDING = "P6-12"
_SESSION = Arg("string", "the demo session id", True)


def _unavailable(command: str) -> BridgeError:
    return BridgeError(
        "policy.capability_unavailable",
        f"{command} needs `shape demo`, which this build does not include yet",
        "use generate, scale_generate or stream for now",
    )


def _handler(command: str) -> Handler:
    def run(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
        raise _unavailable(command)

    return run


COMMANDS = [
    Command(
        "demo_list",
        "List the demo scenarios.",
        {},
        obj(
            {
                "scenarios": arr(
                    obj(
                        {
                            "name": STR,
                            "description": STR,
                            "supported_modes": STRS,
                            "domains": STRS,
                            "default_rows": INT,
                            "tags": STRS,
                        }
                    )
                ),
                "count": INT,
            }
        ),
        _handler("demo_list"),
        pending=PENDING,
    ),
    Command(
        "demo_run",
        "Run a demo scenario.",
        {
            "scenario": Arg("string", "the scenario name (default: retail)"),
            "mode": Arg("string", "the demo mode (default: inference)"),
            "rows": Arg("integer", "rows to generate (default 50000)", minimum=1),
            "domain": Arg("string", "the domain, where the scenario takes one"),
            "input_file": Arg("string", "an input data file, where the scenario takes one"),
            "connection": Arg("string", "a connection name or reference"),
            "output_formats": Arg("array", "output formats to write", items="string"),
            "dry_run": Arg("boolean", "plan the demo without running it"),
            "seed": Arg("integer", "the seed (default 42)"),
            "scale_mode": Arg("string", "auto, local_single, local_mp or fabric_spark"),
        },
        obj(
            {
                "success": BOOL,
                "session_id": STR,
                "scenario": STR,
                "mode": STR,
                "fidelity_score": nullable({"type": "number"}),
                "error": nullable(STR),
                "artifact_count": INT,
            },
            {"fabric_run_id": STR, "status": STR, "scale_mode": STR},
        ),
        _handler("demo_run"),
        pending=PENDING,
    ),
    Command(
        "demo_status",
        "The manifest of a demo session (and a Fabric run's live state).",
        {
            "session_id": _SESSION,
            "token": Arg("string", "a Fabric token; never stored", secret=True),
        },
        obj({"session_id": STR, "manifest": mapping(ANY)}, {"fabric": mapping(ANY)}),
        _handler("demo_status"),
        pending=PENDING,
    ),
    Command(
        "demo_cleanup",
        "Remove what a demo session created.",
        {"session_id": _SESSION, "dry_run": Arg("boolean", "list what would be removed only")},
        obj({"session_id": STR}, {"removed": arr(ANY), "dry_run": BOOL}),
        _handler("demo_cleanup"),
        pending=PENDING,
    ),
]
