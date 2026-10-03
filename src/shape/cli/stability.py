"""Which top-level commands are stable and which are experimental (W1-10).

``docs/CLI_STABILITY.md`` is the promise; this module is what the code and the checks read. A
command not listed in ``STABLE`` is experimental, and says so at the start of its ``--help``
(``annotate``). ``scripts/cli_surface.py`` snapshots the parser against
``tests/cli/cli_surface_v1.json``; ``DEPRECATIONS`` lists what was deprecated, so that removing it
after the deprecation period is not reported as a break.
"""

from __future__ import annotations

import argparse

#: Commands that keep their name, subcommands, flags and positionals through 1.x.
STABLE = frozenset(
    {
        "doctor",
        "version",
        "plugins",
        "capture",
        "profile",
        "diff",
        "inspect",
        "show",
        "cat",
        "git-setup",
        "keygen",
        "sign",
        "validate",
        "verify",
        "check",
        "generate",
        "describe",
        "list",
        "presets",
        "pack",
        "registry",
        "compatibility",
        "plan",
        "drift",
    }
)

EXPERIMENTAL_MARK = "(experimental) "

#: Deprecated commands and flags: ``{"path": "check --old", "use": "--new", "removed_in": "1.3"}``.
#: ``path`` is the command (with subcommands) and the flag, as ``cli_surface.py`` names them.
DEPRECATIONS: list[dict[str, str]] = []


def level(command: str) -> str:
    return "stable" if command in STABLE else "experimental"


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:  # type: ignore[type-arg]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def annotate(parser: argparse.ArgumentParser) -> None:
    """Start the ``--help`` of every experimental command with ``(experimental)``."""
    sub = _subparsers(parser)
    if sub is None:
        return
    for choice in sub._choices_actions:
        if level(choice.dest) == "experimental":
            choice.help = EXPERIMENTAL_MARK + (choice.help or "").strip()
    for name, command in sub.choices.items():
        if level(name) == "experimental" and not (command.description or "").startswith(
            EXPERIMENTAL_MARK
        ):
            command.description = EXPERIMENTAL_MARK + (command.description or "").strip()
