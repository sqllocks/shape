"""The core commands of ``shape``, found by walking the parsers (W1-14).

"Core" means the commands Shape itself defines (``shape.cli.main`` and the ``shape profile``
subcommands of ``shape.cli.profiles`` and ``shape.privacy.cli``); a command a plugin adds is not
one. The exit-code registry, the ``--json``/``--dry-run`` coverage test and the help epilogs all
use this one walk, so none of them can miss a command.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandInfo:
    path: str  # "registry commit"
    parser: argparse.ArgumentParser
    aliases: tuple[str, ...] = ()  # other names of the same command ("show" for "inspect")
    invoke: tuple[str, ...] = ()  # words that reach it, with a placeholder for a parent's argument

    @property
    def words(self) -> tuple[str, ...]:
        return tuple(self.path.split())


def leaves(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...] = (), invoke: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], argparse.ArgumentParser, tuple[str, ...], tuple[str, ...]]]:
    """Every command (a parser with no sub-commands) below ``parser``: its words, its parser, its
    aliases and the words that invoke it (``shape registry ROOT commit`` needs a ROOT)."""
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not actions:
        if prefix:
            yield prefix, parser, (), invoke
        return
    own = tuple(
        "X"
        for a in parser._actions
        if not a.option_strings and a.nargs not in ("?", "*") and a is not actions[0]
    )
    by_parser: dict[int, list[str]] = {}
    parsers: dict[int, argparse.ArgumentParser] = {}
    for name, sub in actions[0].choices.items():
        by_parser.setdefault(id(sub), []).append(name)
        parsers[id(sub)] = sub
    for key, names in by_parser.items():
        for words, leaf, aliases, call in leaves(
            parsers[key], (*prefix, names[0]), (*invoke, *own, names[0])
        ):
            if len(words) == len(prefix) + 1:
                aliases = tuple(names[1:])
            yield words, leaf, aliases, call


def core_commands() -> list[CommandInfo]:
    """Every core command, once, in a stable order."""
    from shape.cli.main import _build_parser
    from shape.cli.profiles import _parser as profiles_parser
    from shape.privacy.cli import _parser as privacy_parser

    found: dict[str, CommandInfo] = {}
    for words, parser, aliases, call in leaves(_build_parser()):  # type: ignore[no-untyped-call]
        found[" ".join(words)] = CommandInfo(" ".join(words), parser, aliases, call)
    for factory in (profiles_parser, privacy_parser):
        for words, parser, aliases, call in leaves(factory(), ("profile",), ("profile",)):
            path = " ".join(words)
            found.setdefault(path, CommandInfo(path, parser, aliases, call))
    return list(found.values())
