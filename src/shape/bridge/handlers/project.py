"""``project_validate``, ``project_show`` and the project file under ``profile``, ``diff``,
``check`` and ``verify`` (bridge 1.1).

They call what ``shape project validate`` and ``--project FILE --source NAME`` call
(``shape.project``, ``shape.cli.project``). The bridge never looks for a ``shape.yml`` on its own:
a request that does not name one (``project``) behaves exactly as before, whatever the working
folder holds. Reading a project file needs the optional PyYAML (extra ``yaml``).
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import BOOL, INT, STR, arr, mapping, nullable, obj
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

if TYPE_CHECKING:
    from shape.cli.project import Context as ProjectContext
    from shape.project import Project

_ROOT_KEYS = ("format", "version", "name", "sources", "gates", "document")
_LINE = re.compile(r"\bline (\d+)")
_PYYAML = (
    'reading shape.yml needs PyYAML: pip install "sqllocks-shape[yaml]" (the extra is called yaml)'
)

PROJECT_ARG = Arg(
    "string",
    "a shape.yml project file; its settings apply as for `--project FILE` (the bridge never "
    "looks for one on its own)",
    path="read",
    since="1.1",
)

#: The ``project`` block of a result, as ``shape diff|check|verify --json`` writes it.
PROJECT_BLOCK = obj(
    {"file": STR, "format": STR, "version": INT}, {"source": STR, "baseline": mapping({})}
)


def need_yaml() -> None:
    """``policy.capability_unavailable`` naming the ``yaml`` extra when PyYAML is missing."""
    if importlib.util.find_spec("yaml") is None:
        raise BridgeError(
            "policy.capability_unavailable",
            _PYYAML,
            "install the yaml extra, or use another command",
        )


def split_problem(text: str) -> dict[str, Any]:
    """One problem of ``shape project validate`` as ``path`` (the key path, or ``document``),
    ``message`` and ``line`` (YAML syntax and duplicate keys name one, else ``None``)."""
    path, sep, message = text.partition(": ")
    root = re.split(r"[.\[]", path, maxsplit=1)[0]
    plain = not any(c.isspace() for c in path) or path.startswith("sources.")
    if not (sep and root in _ROOT_KEYS and plain):
        path, message = "document", text
    found = _LINE.search(text)
    return {"path": path, "message": message, "line": int(found[1]) if found else None}


def load_checked(path: str) -> Project:
    """The project file at ``path``, with the errors of the bridge."""
    from shape.project import ProjectError, ProjectVersionError, load_project

    need_yaml()
    try:
        return load_project(path)
    except ProjectVersionError as exc:
        raise BridgeError(
            "input.unsupported_format_version", str(exc), "upgrade Shape to read it"
        ) from exc
    except ProjectError as exc:
        raise BridgeError("input.invalid_schema", str(exc)) from exc


# ---- project_validate ---------------------------------------------------------------------


def cmd_validate(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.project import ProjectError, ProjectVersionError, parse_project

    if ("text" in args) == ("path" in args):
        raise BridgeError("usage.invalid_argument", "give exactly one of text and path")
    need_yaml()
    if "path" in args:
        where = str(args["path"])
        try:
            text = Path(where).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"valid": False, "problems": [split_problem("not a UTF-8 text file")]}
    else:
        where, text = "shape.yml", str(args["text"])
    try:
        parse_project(text, where)
    except ProjectVersionError as exc:
        raise BridgeError(
            "input.unsupported_format_version", str(exc), "upgrade Shape to read it"
        ) from exc
    except ProjectError as exc:
        return {"valid": False, "problems": [split_problem(p) for p in exc.problems]}
    return {"valid": True, "problems": []}


# ---- project_show -------------------------------------------------------------------------


def _baseline(b: Any) -> dict[str, Any] | None:
    if b is None:
        return None
    full = {
        "kind": b.kind,
        "registry": b.registry,
        "name": b.name,
        "window": b.window,
        "artifact": b.artifact,
        "ref": b.ref,
    }
    return {k: v for k, v in full.items() if v is not None}


def cmd_show(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    project = load_checked(str(args["path"]))
    return {
        "file": str(project.path),
        "format": "shape-project",
        "version": project.version,
        "name": project.name,
        "sources": {
            name: {
                "path": s.path,
                "dataset": s.dataset,
                "contract": s.contract,
                "baseline": _baseline(s.baseline),
            }
            for name, s in project.sources.items()
        },
        "gates": dict(project.gates),
    }


# ---- the project under the workflow commands ----------------------------------------------


def select(
    args: dict[str, Any], ctx: Context, *, named: str | None = None, hint: str | None = None
) -> ProjectContext | None:
    """The project a request runs under, or ``None`` when it names none.

    Mirrors ``shape.cli.project.context`` without the search for a file: ``named`` selects a
    source (``input.unknown_source`` when the file has no such source); without it a project
    with one source selects it, one with several selects the source named ``hint`` (the name of
    the profile being worked on) if there is one, and otherwise no source applies (a warning)."""
    from shape.cli.project import Context as ProjectContext

    path = args.get("project")
    if path is None:
        if named is not None:
            raise BridgeError(
                "usage.invalid_argument",
                "source names a source of a project file: give project too",
            )
        return None
    project = load_checked(str(path))
    if named is not None:
        if named not in project.sources:
            known = ", ".join(sorted(project.sources)) or "none"
            raise BridgeError(
                "input.unknown_source",
                f"no source {named!r} in {project.path} (sources: {known})",
                "run `project_show` for the sources",
            )
        return ProjectContext(project, project.sources[named])
    if len(project.sources) == 1:
        return ProjectContext(project, next(iter(project.sources.values())))
    if hint is not None and hint in project.sources:
        return ProjectContext(project, project.sources[hint])
    ctx.warn(
        "project_source_not_selected",
        f"{project.path} defines {len(project.sources)} sources and none was selected: its "
        "source settings were not applied (give source)",
    )
    return ProjectContext(project, None)


def source_named_by(value: str, pc: ProjectContext | None) -> Any:
    """The project source ``value`` names, when it is not an existing path and not a URI (an
    existing path always wins, as on the command line); else ``None``."""
    if pc is None or os.path.exists(value) or "://" in value:
        return None
    return pc.project.sources.get(value)


# ---- the commands -------------------------------------------------------------------------

_PROBLEM = obj({"path": STR, "message": STR, "line": nullable(INT)})
_SOURCE = obj(
    {"path": STR, "dataset": BOOL, "contract": nullable(STR), "baseline": nullable(mapping({}))}
)

COMMANDS = [
    Command(
        "project_validate",
        "Check the text or the file of a shape.yml and report every problem with its key path.",
        {
            "text": Arg("string", "the project file's text (give this or path)"),
            "path": Arg("string", "a project file (give this or text)", path="read"),
        },
        obj({"valid": BOOL, "problems": arr(_PROBLEM)}),
        cmd_validate,
        since="1.1",
        effects=("reads_files",),
    ),
    Command(
        "project_show",
        "Show a project file: its sources (paths resolved against its folder) and gate modes.",
        {"path": Arg("string", "the project file", True, path="read")},
        obj(
            {
                "file": STR,
                "format": STR,
                "version": INT,
                "name": nullable(STR),
                "sources": mapping(_SOURCE),
                "gates": mapping({"enum": ["observe", "enforce"]}),
            }
        ),
        cmd_show,
        since="1.1",
        effects=("reads_files",),
    ),
]
