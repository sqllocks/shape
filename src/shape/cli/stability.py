"""Which top-level commands are stable and which are experimental (W1-10).

``docs/CLI_STABILITY.md`` is the promise; this module is what the code and the checks read. A
command not listed in ``STABLE`` or ``STABLE_PLUGIN_COMMANDS`` is experimental, and says so
at the start of its ``--help``
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

#: Only core can promote a first-party plugin command. No plugin is promoted yet.
STABLE_PLUGIN_COMMANDS: dict[str, str] = {}

#: Documented Fabric spellings share a freeze; setup is the one renamed child.
FABRIC_COMMAND_ALIASES = {
    "publish": "fabric publish",
    "notebook": "fabric notebook",
    "deploy-notebook": "fabric deploy-notebook",
    "setup-fabric": "fabric setup",
    "export-model": "fabric export-model",
    "known-answer": "fabric known-answer",
    "check-answers": "fabric check-answers",
    "publish-report": "fabric publish-report",
}
FABRIC_DISTRIBUTION = "sqllocks-shape-fabric"


def plugin_commands() -> dict[str, str]:
    """Listed command paths, including both documented Fabric spellings."""
    commands = dict(STABLE_PLUGIN_COMMANDS)
    if commands.get("fabric") == FABRIC_DISTRIBUTION:
        for name in FABRIC_COMMAND_ALIASES:
            commands.setdefault(name, FABRIC_DISTRIBUTION)
    for name, alias in FABRIC_COMMAND_ALIASES.items():
        if commands.get(name) == FABRIC_DISTRIBUTION:
            commands[alias] = FABRIC_DISTRIBUTION
    return commands


def command_distribution(name: str) -> str | None:
    """Required owner of a command entry point, including the Fabric alias dispatcher."""
    owner = plugin_commands().get(name)
    if name == "fabric" and owner is None:
        if any(p.startswith("fabric ") for p in plugin_commands()):
            return FABRIC_DISTRIBUTION
    return owner


EXPERIMENTAL_MARK = "(experimental) "

#: Deprecated commands and flags: ``{"path": "check --old", "use": "--new", "removed_in": "1.3"}``.
#: ``path`` is the command (with subcommands) and the flag, as ``cli_surface.py`` names them.
DEPRECATIONS: list[dict[str, str]] = []


def level(command: str) -> str:
    return "stable" if command in STABLE or command in plugin_commands() else "experimental"


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
            help_text = (choice.help or "").strip()
            if help_text.startswith("(plugin ") and help_text.endswith(")"):
                choice.help = help_text[:-1] + ", experimental)"
            elif not help_text.startswith(EXPERIMENTAL_MARK):
                choice.help = EXPERIMENTAL_MARK + help_text
    for name, command in sub.choices.items():
        if level(name) == "experimental" and not (command.description or "").startswith(
            EXPERIMENTAL_MARK
        ):
            command.description = EXPERIMENTAL_MARK + (command.description or "").strip()


def annotate_plugin(parser: argparse.ArgumentParser, name: str) -> None:
    """Mark the loaded parser, with independent levels for Fabric child spellings."""

    def visit(node: argparse.ArgumentParser, path: str) -> None:
        stable = level(path if name == "fabric" else name) == "stable"
        if not stable and not (node.description or "").startswith(EXPERIMENTAL_MARK):
            node.description = EXPERIMENTAL_MARK + (node.description or "").strip()
        sub = _subparsers(node)
        if sub is None:
            return
        for choice in sub._choices_actions:
            child_path = f"{path} {choice.dest}"
            if level(child_path if name == "fabric" else name) != "stable":
                choice.help = EXPERIMENTAL_MARK + (choice.help or "").strip()
        for child_name, child in sub.choices.items():
            visit(child, f"{path} {child_name}")

    visit(parser, name)
