"""CLI surface snapshot and compatibility check (W1-10).

    python scripts/cli_surface.py --check    # exit 1 if a stable command, flag or choice is gone
    python scripts/cli_surface.py --write    # refresh the baseline after an *additive* change

``tests/cli/cli_surface_v1.json`` (``format: "shape-cli-surface"``, ``version: 1``) records every
core command and subcommand of ``shape.cli.main`` with its stability level, its flags (each option
string, whether it takes a value, its choices) and its positionals, as CLI 1.0 shipped them.
``compare`` applies the promise in ``docs/CLI_STABILITY.md``: what the baseline holds for a stable
command must still be there, unless ``shape.cli.stability.DEPRECATIONS`` lists it; additions pass
and are reported. Plugin commands are not part of the snapshot (see ``docs/plugins/stability.md``).

Exit codes: 0 compatible, 1 a stable command, flag or choice was removed or renamed, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
BASELINE = ROOT / "tests" / "cli" / "cli_surface_v1.json"
FORMAT = "shape-cli-surface"
VERSION = 1


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:  # type: ignore[type-arg]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _choices(action: argparse.Action) -> list[str] | None:
    return None if action.choices is None else sorted(str(c) for c in action.choices)


def _node(parser: argparse.ArgumentParser) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    positionals: list[dict[str, Any]] = []
    subs = _subparsers(parser)
    for action in parser._actions:
        if action is subs or isinstance(action, argparse._HelpAction):
            continue
        if action.option_strings:
            for opt in action.option_strings:
                flags[opt] = {"takes_value": action.nargs != 0, "choices": _choices(action)}
        else:
            positionals.append(
                {
                    "name": action.metavar or action.dest,
                    "nargs": None if action.nargs is None else str(action.nargs),
                    "choices": _choices(action),
                }
            )
    node: dict[str, Any] = {"flags": flags, "positionals": positionals, "subcommands": {}}
    if subs is not None:
        for name, child in subs.choices.items():
            node["subcommands"][name] = _node(child)
    return node


def snapshot(parser: argparse.ArgumentParser | None = None) -> dict[str, Any]:
    """The surface of the live core parser, in the same form as the committed baseline."""
    from shape.cli.stability import level

    if parser is None:
        from shape.cli.main import _build_parser

        parser = _build_parser()
    root = _node(parser)
    commands: dict[str, Any] = {}
    for name, child in root["subcommands"].items():
        lvl = level(name)
        commands[name] = _retag(child, lvl)
    return {
        "format": FORMAT,
        "version": VERSION,
        "global_flags": root["flags"],
        "commands": commands,
    }


def _retag(node: dict[str, Any], lvl: str) -> dict[str, Any]:
    node["stability"] = lvl
    for child in node["subcommands"].values():
        _retag(child, lvl)
    return node


def _deprecated(path: str) -> bool:
    from shape.cli.stability import DEPRECATIONS

    return any(d["path"] == path for d in DEPRECATIONS)


def _compare_flags(
    where: str, old: dict[str, Any], new: dict[str, Any], problems: list[str], notes: list[str]
) -> None:
    for opt, o in old.items():
        path = f"{where} {opt}"
        n = new.get(opt)
        if n is None:
            if _deprecated(path):
                notes.append(f"{path}: removed after its deprecation")
            else:
                problems.append(f"{path}: flag was removed or renamed")
            continue
        if o["takes_value"] != n["takes_value"]:
            problems.append(
                f"{path}: now {'takes' if n['takes_value'] else 'does not take'} a value"
            )
        oc, nc = o["choices"], n["choices"]
        if oc is not None:
            if nc is None:
                notes.append(f"{path}: choices are no longer restricted")
            else:
                for gone in sorted(set(oc) - set(nc)):
                    if _deprecated(f"{path}={gone}"):
                        notes.append(f"{path}={gone}: removed after its deprecation")
                    else:
                        problems.append(f"{path}: choice {gone!r} was removed")
        elif nc is not None:
            problems.append(f"{path}: now restricted to {nc}")
    for opt in sorted(new.keys() - old.keys()):
        notes.append(f"{where} {opt}: new flag")


def _compare_positionals(
    where: str, old: list[dict[str, Any]], new: list[dict[str, Any]], problems: list[str]
) -> None:
    for i, o in enumerate(old):
        if i >= len(new):
            problems.append(f"{where}: positional {o['name']!r} was removed")
            continue
        n = new[i]
        if o["nargs"] != n["nargs"]:
            problems.append(f"{where}: positional {o['name']!r} nargs {o['nargs']} -> {n['nargs']}")
        oc, nc = o["choices"], n["choices"]
        if oc is not None and nc is not None:
            for gone in sorted(set(oc) - set(nc)):
                problems.append(f"{where}: positional {o['name']!r} choice {gone!r} was removed")
        elif oc is None and nc is not None:
            problems.append(f"{where}: positional {o['name']!r} is now restricted to {nc}")
    for extra in new[len(old) :]:
        if extra["nargs"] in (None, "+") or extra["nargs"].isdigit():
            problems.append(f"{where}: new required positional {extra['name']!r}")


def _compare_node(
    where: str, old: dict[str, Any], new: dict[str, Any], problems: list[str], notes: list[str]
) -> None:
    _compare_flags(where, old["flags"], new["flags"], problems, notes)
    _compare_positionals(where, old["positionals"], new["positionals"], problems)
    for name, child in old["subcommands"].items():
        path = f"{where} {name}"
        live = new["subcommands"].get(name)
        if live is None:
            if _deprecated(path):
                notes.append(f"{path}: removed after its deprecation")
            else:
                problems.append(f"{path}: subcommand was removed or renamed")
            continue
        _compare_node(path, child, live, problems, notes)
    for name in sorted(new["subcommands"].keys() - old["subcommands"].keys()):
        notes.append(f"{where} {name}: new subcommand")


def compare(baseline: dict[str, Any], live: dict[str, Any]) -> tuple[list[str], list[str]]:
    """``(problems, notes)``: what breaks the promise for stable commands, and what was added."""
    problems: list[str] = []
    notes: list[str] = []
    _compare_flags("shape", baseline["global_flags"], live["global_flags"], problems, notes)
    for name, old in baseline["commands"].items():
        live_cmd = live["commands"].get(name)
        if old["stability"] != "stable":
            if live_cmd is None:
                notes.append(f"{name}: experimental command was removed")
            continue
        if live_cmd is None:
            if _deprecated(name):
                notes.append(f"{name}: removed after its deprecation")
            else:
                problems.append(f"{name}: command was removed or renamed")
            continue
        if live_cmd["stability"] != "stable":
            problems.append(f"{name}: demoted from stable to {live_cmd['stability']}")
            continue
        _compare_node(name, old, live_cmd, problems, notes)
    for name in sorted(live["commands"].keys() - baseline["commands"].keys()):
        notes.append(f"{name}: new {live['commands'][name]['stability']} command")
    return problems, notes


def load_baseline(path: Path = BASELINE) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"{path}: not a {FORMAT} file")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise ValueError(f"{path}: surface version {data.get('version')!r} is not readable")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--baseline", type=Path, default=BASELINE, help=argparse.SUPPRESS)
    try:
        ns = ap.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2  # argparse: usage error is 2
    live = snapshot()
    try:
        if ns.write:
            if ns.baseline.exists():
                problems, _ = compare(load_baseline(ns.baseline), live)
                if problems:
                    print(
                        "refusing to write: these changes break the CLI promise:", file=sys.stderr
                    )
                    for p in problems:
                        print(" -", p, file=sys.stderr)
                    return 1
            ns.baseline.write_text(
                json.dumps(live, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"wrote {ns.baseline}")
            return 0
        problems, notes = compare(load_baseline(ns.baseline), live)
    except (OSError, ValueError) as exc:
        print(f"cli_surface: error: {exc}", file=sys.stderr)
        return 2
    for n in notes:
        print("note:", n)
    for p in problems:
        print("BREAKING:", p)
    print(f"CLI surface: {len(problems)} breaking change(s), {len(notes)} note(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
