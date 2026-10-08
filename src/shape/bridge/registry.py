"""The command table: every bridge command, by name."""

from __future__ import annotations

from shape.bridge.annotations import annotate
from shape.bridge.spec import Command


def _build() -> dict[str, Command]:
    from shape.bridge.handlers import (
        catalog,
        chaos,
        demo,
        design,
        flow,
        formats,
        generate,
        history,
        project,
        proposals,
        registrydiff,
        reportcard,
        rules,
        scale,
        stored,
        suites,
        workflow11,
    )

    table: dict[str, Command] = {}
    for module in (
        catalog,
        generate,
        flow,
        scale,
        demo,
        proposals,
        project,
        design,
        formats,
        stored,
        reportcard,
        rules,
        history,
        registrydiff,
        chaos,
        suites,
    ):
        for command in module.COMMANDS:
            if command.name in table:
                raise AssertionError(f"duplicate bridge command {command.name!r}")
            table[command.name] = command
    return annotate(workflow11.extend(table))


COMMANDS: dict[str, Command] = _build()

#: The original 17 commands of the JSON bridge protocol.
PARITY_COMMANDS = (
    "list",
    "describe",
    "generate",
    "dry_run",
    "validate",
    "preview",
    "profile_info",
    "demo_list",
    "demo_run",
    "demo_status",
    "demo_cleanup",
    "scale_generate",
    "stream",
    "stream_status",
    "stream_stop",
    "scale_status",
    "scale_cancel",
)
