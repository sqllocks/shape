"""``demo_list``, ``demo_run``, ``demo_status`` and ``demo_cleanup`` (P6-11).

Each command calls the matching function of ``shape.demo.api``, as ``shape demo`` does. The
progress a run prints goes to standard error: standard output carries only the reply. A session
the demo cannot find, and a scenario, mode or setting it cannot use, are ``input.invalid_value``;
so is a Spark session asked after without a Fabric token.

A request served as api_version 1.0 or 1.1 gets the answer those versions gave
(``policy.capability_unavailable``: the commands were published there before ``shape demo``
existed), so the frozen 1.0 and 1.1 vectors still hold; 1.2 runs them."""

from __future__ import annotations

import sys
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
from shape.bridge.spec import Arg, Command

_RUNS_FROM_MINOR = 2  # api_version 1.2: the first that runs the demo commands (#541)
_SESSION = Arg("string", "the demo session id", True)


def _runtime() -> Any:
    from shape.demo.runtime import DemoRuntime

    return DemoRuntime(out=sys.stderr)


def _as_served(command: str, ctx: Context) -> None:
    """The answer of 1.0 and 1.1, which published the demo commands before they ran (P6-12)."""
    if ctx.minor < _RUNS_FROM_MINOR:
        raise BridgeError(
            "policy.capability_unavailable",
            f"{command} needs `shape demo`, which this build does not include yet",
            "use generate, scale_generate or stream for now",
        )


def cmd_demo_list(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.demo.api import demo_list

    _as_served("demo_list", ctx)
    return demo_list()


def cmd_demo_run(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.demo.api import demo_run

    _as_served("demo_run", ctx)
    return demo_run(args, runtime=_runtime())


def cmd_demo_status(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.demo.api import demo_status

    _as_served("demo_status", ctx)
    return demo_status(args["session_id"], token=args.get("token"), runtime=_runtime())


def cmd_demo_cleanup(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.demo.api import demo_cleanup

    _as_served("demo_cleanup", ctx)
    result = demo_cleanup(
        args["session_id"], dry_run=bool(args.get("dry_run", False)), runtime=_runtime()
    )
    # the published result holds ``removed`` as a list: one entry per target
    result["removed"] = [{"target": t, "names": n} for t, n in result["removed"].items()]
    return result


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
        cmd_demo_list,
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
        cmd_demo_run,
    ),
    Command(
        "demo_status",
        "The manifest of a demo session (and a Fabric run's live state).",
        {
            "session_id": _SESSION,
            "token": Arg("string", "a Fabric token; never stored", secret=True),
        },
        obj({"session_id": STR, "manifest": mapping(ANY)}, {"fabric": mapping(ANY)}),
        cmd_demo_status,
    ),
    Command(
        "demo_cleanup",
        "Remove what a demo session created.",
        {"session_id": _SESSION, "dry_run": Arg("boolean", "list what would be removed only")},
        obj({"session_id": STR}, {"removed": arr(ANY), "dry_run": BOOL}),
        cmd_demo_cleanup,
    ),
]
