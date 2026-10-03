"""What the 1.0 commands do to the system and which of their arguments are paths (bridge 1.1).

Bridge 1.1 publishes, for a client that asks a person before touching files, ``x-path`` on every
argument that is a filesystem path (``"read"`` or ``"write"``), ``x-name-or-path`` on ``domain``
and ``effects`` on every command. The 1.0 commands are declared in their own modules, which 1.1
leaves alone; the annotations are applied here, once, when the command table is built. A command
added in 1.1 declares its own on its :class:`~shape.bridge.spec.Arg` and
:class:`~shape.bridge.spec.Command`.
"""

from __future__ import annotations

from dataclasses import replace

from shape.bridge.spec import Command

#: command -> argument -> ``"read"`` or ``"write"``.
PATHS: dict[str, dict[str, str]] = {
    "validate": {"schema_path": "read"},
    "generate": {"output_dir": "write"},
    "profile": {"source": "read", "output": "write"},
    "diff": {"before": "read", "after": "read"},
    "check": {"profile": "read", "contract": "read"},
    "verify": {"path": "read", "schema": "read", "config": "read"},
    "demo_run": {"input_file": "read"},
}

#: Commands with a ``domain`` argument: an installed name first, a schema file path otherwise.
NAME_OR_PATH = (
    "describe",
    "dry_run",
    "profile_info",
    "generate",
    "preview",
    "scale_generate",
    "stream",
)

#: command -> what it does outside the bridge's own job files. ``network`` is a Fabric run or a
#: remote sink; ``cancels`` stops a running job or stream.
EFFECTS: dict[str, tuple[str, ...]] = {
    "list": (),
    "describe": ("reads_files",),
    "dry_run": ("reads_files",),
    "validate": ("reads_files",),
    "profile_info": ("reads_files",),
    "generate": ("reads_files", "writes_files"),
    "preview": ("reads_files",),
    "profile": ("reads_files", "writes_files"),
    "diff": ("reads_files",),
    "check": ("reads_files",),
    "verify": ("reads_files",),
    "scale_generate": ("reads_files", "writes_files", "network", "cancels"),
    "stream": ("reads_files", "writes_files", "network", "cancels"),
    "stream_status": (),
    "stream_stop": ("cancels",),
    "scale_status": ("network",),
    "scale_cancel": ("network", "cancels"),
    "job_status": ("network",),
    "job_cancel": ("network", "cancels"),
    "job_list": (),
    "demo_list": (),
    "demo_run": ("reads_files", "writes_files", "network"),
    "demo_status": ("network",),
    "demo_cleanup": ("writes_files", "network"),
}


def annotate(commands: dict[str, Command]) -> dict[str, Command]:
    """``commands`` with the path annotations and effects of the 1.0 commands filled in."""
    out: dict[str, Command] = {}
    for name, command in commands.items():
        if name not in EFFECTS:
            # a 1.1 command carries its own annotations; all its arguments are new with it
            args = {k: replace(a, since=command.since) for k, a in command.args.items()}
            out[name] = replace(command, args=args)
            continue
        args = dict(command.args)
        for arg_name, mode in PATHS.get(name, {}).items():
            args[arg_name] = replace(args[arg_name], path=mode)
        if name in NAME_OR_PATH:
            args["domain"] = replace(args["domain"], name_or_path=True)
        out[name] = replace(command, args=args, effects=EFFECTS[name])
    return out
