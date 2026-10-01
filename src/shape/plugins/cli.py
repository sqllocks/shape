"""``shape plugins list|info`` and plugin-contributed ``shape <name>`` subcommands (P2-03)."""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from typing import Any

from shape.plugins.api import v1
from shape.plugins.host import PluginHost, PluginLoadError, PluginRecord

COMMANDS_GROUP = "shape.commands"


def list_report(host: PluginHost, group: str | None = None) -> list[dict[str, Any]]:
    """Every registered plugin as ``PluginRecord.as_dict()``; imports no plugin code."""
    return [r.as_dict() for r in host.records(group)]


def format_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no plugins installed"
    width = max(len(f"{r['group']}:{r['name']}") for r in rows)
    lines = [f"{len(rows)} plugin(s)"]
    for r in rows:
        key = f"{r['group']}:{r['name']}"
        lines.append(f"  {key:<{width}}  {r['status']:<8} [{r['source']}]")
    return "\n".join(lines)


def find_records(host: PluginHost, ref: str) -> list[PluginRecord]:
    """Resolve ``GROUP:NAME`` or a bare ``NAME`` (which may match in several groups)."""
    if ":" in ref:
        group, _, name = ref.partition(":")
        rec = host.record(group, name)
        return [rec] if rec else []
    return [r for r in host.records() if r.name == ref]


def describe(host: PluginHost, rec: PluginRecord) -> dict[str, Any]:
    """Load ``rec`` and return its record plus the documentation of the loaded object."""
    out = rec.as_dict()
    protocol = v1.GROUPS.get(rec.group)
    out["protocol"] = protocol
    try:
        obj = host.get(rec.group, rec.name)
    except PluginLoadError:
        pass
    else:
        out["doc"] = inspect.getdoc(obj) or ""
        for attr in ("schemes", "families", "help"):
            val = getattr(obj, attr, None)
            if val is not None:
                out[attr] = list(val) if isinstance(val, (list, tuple)) else val
    out.update(rec.as_dict())  # status, api and error as they are after the load attempt
    return out


def format_info(info: dict[str, Any]) -> str:
    lines = [
        f"{info['group']}:{info['name']}",
        f"  protocol  {info['protocol']}",
        f"  source    {info['source']}",
        f"  target    {info['target']}",
        f"  status    {info['status']}",
        f"  api       {info['api'] or '-'}",
    ]
    for key in ("schemes", "families"):
        if info.get(key):
            lines.append(f"  {key:<9} {', '.join(info[key])}")
    if info.get("help"):
        lines.append(f"  help      {info['help']}")
    if info["error"]:
        lines.append(f"  error     {info['error']}")
    if info.get("doc"):
        lines.append("")
        lines.append(info["doc"])
    return "\n".join(lines)


def cmd_list(host: PluginHost, args: argparse.Namespace) -> int:
    rows = list_report(host, args.group)
    if args.json:
        print(json.dumps(rows, sort_keys=True))
    else:
        print(format_list(rows))
    return 0


def cmd_info(host: PluginHost, args: argparse.Namespace) -> int:
    """0 on success, 1 when the plugin failed to load, 2 when it is unknown or ambiguous."""
    recs = find_records(host, args.plugin)
    if not recs:
        print(
            f"shape: error: no plugin {args.plugin!r} (see `shape plugins list`)", file=sys.stderr
        )
        return 2
    if len(recs) > 1:
        names = ", ".join(f"{r.group}:{r.name}" for r in recs)
        print(f"shape: error: {args.plugin!r} is ambiguous; use one of: {names}", file=sys.stderr)
        return 2
    info = describe(host, recs[0])
    if args.json:
        print(json.dumps(info, sort_keys=True, default=str))
    else:
        print(format_info(info))
    return 0 if info["status"] == "ok" else 1


def command_names(host: PluginHost, reserved: set[str]) -> dict[str, PluginRecord]:
    """Plugin command records by name; a name a built-in command already uses is skipped."""
    return {r.name: r for r in host.records(COMMANDS_GROUP) if r.name not in reserved}


def run_command(host: PluginHost, name: str, argv: list[str]) -> int:
    """Load the plugin command ``name`` and run it with ``argv``.

    A command that cannot load, or that raises, exits 1 with its error on stderr. Argument
    errors exit 2 (argparse), and ``SystemExit`` from the command is passed through.
    """
    try:
        cmd = host.get(COMMANDS_GROUP, name)
    except (PluginLoadError, KeyError) as exc:
        print(f"shape: plugin command {name!r} failed to load: {exc}", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(
        prog=f"shape {name}", description=getattr(cmd, "help", None) or None
    )
    try:
        cmd.configure(parser)
        ns = parser.parse_args(argv)
        return int(cmd.run(ns) or 0)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:  # a plugin is third-party code: report it, never a traceback
        print(
            f"shape: plugin command {name!r} failed: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return 1
