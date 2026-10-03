"""``shape plugins list|info|sign|verify|allowlist`` and plugin-contributed ``shape <name>``
subcommands (P2-03, W1-18)."""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from typing import Any

from shape.plugins import trust
from shape.plugins.api import v1
from shape.plugins.host import PluginBlockedError, PluginHost, PluginLoadError, PluginRecord

COMMANDS_GROUP = "shape.commands"


def list_report(host: PluginHost, group: str | None = None) -> list[dict[str, Any]]:
    """Every registered plugin as ``PluginRecord.as_dict()``; imports no plugin code. With an
    allow-list in force each row also has ``allowed`` (a bool) and ``reason`` (why it is blocked,
    else ``None``)."""
    rows = []
    for r in host.records(group):
        row = r.as_dict()
        if host.allowlist_active:
            blocked = r.status == "blocked"
            row["allowed"] = not blocked
            row["reason"] = r.error if blocked else None
        rows.append(row)
    return rows


def format_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no plugins installed"
    width = max(len(f"{r['group']}:{r['name']}") for r in rows)
    lines = [f"{len(rows)} plugin(s)"]
    for r in rows:
        key = f"{r['group']}:{r['name']}"
        line = f"  {key:<{width}}  {r['status']:<8} [{r['source']}]"
        if "allowed" in r:
            line += "  allowed" if r["allowed"] else f"  blocked -- {r['reason']}"
        lines.append(line)
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
    except PluginBlockedError as exc:  # the allow-list's refusal is an input error, not a crash
        print(f"shape: error: {exc}", file=sys.stderr)
        return 2
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


# -- allow-list, signing and verifying (W1-18) ----------------------------------------------

DEFAULT_ALLOWLIST_FILE = "shape-plugin-allowlist.json"


def build_allowlist(host: PluginHost, pin_hashes: bool = False) -> dict[str, Any]:
    """An allow-list document for the plugins installed now. Imports none of them. Core's own
    entry points are not listed (they are always allowed)."""
    by_dist: dict[str, dict[str, Any]] = {}
    for rec in host.records():
        if not rec.group or trust.is_core(rec.source) or rec.source.startswith("<"):
            continue
        entry = by_dist.setdefault(trust.canonical_name(rec.source), {"name": rec.source})
        entry.setdefault("names", []).append(f"{trust.short_group(rec.group)}:{rec.name}")
    plugins = []
    for canon in sorted(by_dist):
        info = by_dist[canon]
        dist = _find_distribution(info["name"])
        item: dict[str, Any] = {"distribution": info["name"]}
        if dist is not None and dist.version:
            item["version"] = f"=={dist.version}"
        if pin_hashes:
            if dist is None:
                raise trust.PluginTrustError(f"{info['name']}: cannot pin hashes without metadata")
            try:
                item["record_sha256"] = trust.record_sha256(trust.installed_files(dist))
            except (FileNotFoundError, OSError):
                raise trust.PluginTrustError(
                    f"{info['name']} has no RECORD, so its hash cannot be pinned"
                ) from None
        item["names"] = sorted(set(info["names"]))
        plugins.append(item)
    return {
        "format": trust.FORMAT,
        "version": trust.VERSION,
        "plugins": plugins,
        "require_signature": False,
        "trusted_keys": [],
    }


def _find_distribution(name: str) -> Any:
    from importlib import metadata

    try:
        return metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None


def cmd_allowlist_init(host: PluginHost, args: argparse.Namespace) -> int:
    """Write an allow-list for the installed plugins (2 when the file exists)."""
    from pathlib import Path

    out = Path(args.output or DEFAULT_ALLOWLIST_FILE)
    doc = build_allowlist(host, pin_hashes=args.pin_hashes)
    if out.suffix.lower() in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise ValueError(
                "writing YAML needs pyyaml (pip install pyyaml); use a .json file"
            ) from None
        text = yaml.safe_dump(doc, sort_keys=False)
    else:
        text = json.dumps(doc, indent=2) + "\n"
    try:
        with open(out, "x", encoding="utf-8") as fh:  # never overwrite
            fh.write(text)
    except FileExistsError:
        raise ValueError(f"{out} already exists; choose another file with -o") from None
    summary = {"path": str(out), "plugins": len(doc["plugins"]), "pinned_hashes": args.pin_hashes}
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"wrote {out}: {len(doc['plugins'])} plugin distribution(s)")
    return 0


def cmd_sign(args: argparse.Namespace, private_key: bytes) -> int:
    kid = trust.sign_wheel(args.wheel, private_key, args.output)
    print(f"signed {args.output or args.wheel} with key {kid}")
    return 0


def _trusted_keys(args: argparse.Namespace) -> dict[str, bytes]:
    if args.key:
        from shape.artifact.keys import load_public_key

        public = load_public_key(args.key)
        return {trust.key_id(public): public}
    path = trust.resolve_allowlist_path()
    if path is None:
        raise ValueError(
            "no key to check against: give --key PUBLIC.pub, or activate an allow-list with "
            f"trusted_keys ({trust.ENV_ALLOWLIST}=PATH)"
        )
    return dict(trust.load_allowlist(path).trusted_keys) or _no_keys(path)


def _no_keys(path: str) -> dict[str, bytes]:
    raise ValueError(f"the allow-list {path} has no trusted_keys; give --key PUBLIC.pub")


def cmd_verify(args: argparse.Namespace) -> int:
    """0 when the signature is valid and the files match RECORD, 1 when it is missing or invalid
    (the reason on stderr), 2 on a usage error."""
    from pathlib import Path

    ref = args.target
    trusted = _trusted_keys(args)
    archive = None
    try:
        if Path(ref).is_file():
            archive, files = trust.open_wheel(ref)
        else:
            dist = _find_distribution(ref)
            if dist is None:
                raise ValueError(f"{ref!r} is neither a wheel file nor an installed distribution")
            try:
                files = trust.installed_files(dist)
            except FileNotFoundError:
                print(f"shape: plugin {ref}: the distribution has no RECORD", file=sys.stderr)
                return 1
        try:
            kid = trust.verify_signature(files, trusted)
        except trust.MissingCryptoError:
            raise
        except trust.PluginTrustError as exc:
            if args.json:
                print(json.dumps({"target": ref, "valid": False, "reason": str(exc)}))
            else:
                print(f"shape: plugin {ref}: {exc}", file=sys.stderr)
            return 1
    finally:
        if archive is not None:
            archive.close()
    if args.json:
        print(json.dumps({"target": ref, "valid": True, "key_id": kid}))
    else:
        print(f"{ref}: signature valid (key {kid})")
    return 0
