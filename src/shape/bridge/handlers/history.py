"""``bisect``, ``bisect_layers`` and ``timelapse`` (bridge 1.2).

They call what ``shape bisect``, ``shape bisect layers`` and ``shape timelapse`` call
(``shape.history``) and return the ``to_dict()`` of each result, as ``--json`` prints it. The
bridge never looks for a ``shape.yml`` on its own: ``project`` names it (``bisect_layers`` needs
one; ``bisect`` and ``timelapse`` use it when given). ``timelapse``'s frames are spillable.

Safe by default, as for ``diff``: a registry version that is a full profile holds real values, so
the values the safe-profile gate classifies (a personal-data pattern, or nearly every value
distinct) are withheld from the result unless the request sets ``options.include_raw_values``:
``null`` where the value was, and ``"redacted": true`` on the change or frame. A version stored in
its share-safe form holds only what its safe form holds, and nothing more is withheld from it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    STR,
    STRS,
    arr,
    jsonable,
    mapping,
    nullable,
    obj,
    or_spilled,
)
from shape.bridge.handlers.flow import classified_columns
from shape.bridge.handlers.project import PROJECT_ARG, select
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_WINDOWS = ("day", "week", "month")
_RAW_CHANGE_FIELDS = ("before", "after")


def _no_search(args: dict[str, Any], name: str, ctx: Context) -> tuple[str | None, str | None]:
    """The project file and the source a request names (the engine would search for a file from
    the working folder when it is given ``source`` alone: the bridge never does)."""
    pc = select(args, ctx, named=args.get("source"), hint=name)
    if pc is None:
        return None, None
    return str(pc.project.path), (pc.source.name if pc.source is not None else None)


def classified_in(registry: Any, name: str) -> set[str]:
    """The classified columns of the full profiles at the ends of a name's history; a version in
    the share-safe form (or one that cannot be read) adds none."""
    from shape.history.versions import HistoryError, Versions

    try:
        history = Versions(Path(str(registry)), name)
    except HistoryError:
        return set()  # the engine reports a missing registry or name
    out: set[str] = set()
    for version in {history.versions[0], history.versions[-1]}:
        try:
            out |= classified_columns(history.profile(version))
        except Exception:  # a safe form, or a version the engine reports itself
            continue
    return out


def redact_changes(changes: list[dict[str, Any]], classified: set[str]) -> list[dict[str, Any]]:
    """Changes about a classified column, with ``before`` and ``after`` withheld."""
    out = []
    for change in changes:
        column = change.get("column")
        if isinstance(column, str) and (
            column in classified
            or (change.get("table") is not None and f"{change['table']}.{column}" in classified)
        ):
            change = {
                **change,
                **{k: None for k in _RAW_CHANGE_FIELDS if k in change},
                "redacted": True,
            }
        out.append(change)
    return out


def _violations(items: list[dict[str, Any]], classified: set[str]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        column = item.get("column")
        if isinstance(column, str) and column in classified:
            item = {**item, "observed": None, "expected": None, "redacted": True}
        out.append(item)
    return out


# ---- bisect -------------------------------------------------------------------------------


def prepare_bisect(args: dict[str, Any], ctx: Context) -> None:
    for name in ("registry", "contract", "project"):
        value = args.get(name)
        if value is not None and not Path(str(value)).exists():
            raise BridgeError("input.not_found", f"{name} not found: {value}")


def cmd_bisect(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.history import bisect

    prepare_bisect(args, ctx)
    name = str(args["name"])
    project, source = _no_search(args, name, ctx)
    result = bisect(
        str(args["registry"]),
        name,
        good=str(args["good"]),
        bad=str(args["bad"]),
        column=args.get("column"),
        kind=args.get("kind"),
        contract=args.get("contract"),
        project=project,
        source=source,
        verify_all=bool(args.get("verify_all")),
        coarse=args.get("coarse"),
    )
    doc: dict[str, Any] = jsonable(result.to_dict())
    if not ctx.include_raw:
        classified = classified_in(args["registry"], name)
        for key in ("changes", "changes_vs_good"):
            if key in doc:
                doc[key] = redact_changes(doc[key], classified)
        if "violations" in doc:
            doc["violations"] = _violations(doc["violations"], classified)
    return doc


# ---- bisect_layers ------------------------------------------------------------------------


def prepare_layers(args: dict[str, Any], ctx: Context) -> None:
    if args.get("project") is None:
        raise BridgeError(
            "input.invalid_value",
            "bisect_layers needs a project file: its sources are the layers",
            "give project (the bridge never looks for a shape.yml on its own)",
        )
    if not Path(str(args["project"])).exists():
        raise BridgeError("input.not_found", f"project not found: {args['project']}")


def cmd_layers(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.bridge.handlers.project import load_checked
    from shape.history import bisect_layers

    prepare_layers(args, ctx)
    result = bisect_layers(
        [str(x) for x in args["layers"]],
        good_date=str(args["good_date"]),
        bad_date=str(args["bad_date"]),
        column=args.get("column"),
        mapping=dict(args.get("map") or {}),
        project=str(args["project"]),
    )
    doc: dict[str, Any] = jsonable(result.to_dict())
    if not ctx.include_raw:
        project = load_checked(str(args["project"]))
        for layer in doc["layers"]:
            base = project.sources[layer["source"]].baseline
            classified = classified_in(base.registry, base.name) if base else set()
            layer["changes"] = redact_changes(layer["changes"], classified)
    return doc


# ---- timelapse ----------------------------------------------------------------------------


def prepare_timelapse(args: dict[str, Any], ctx: Context) -> None:
    for name in ("registry", "project"):
        value = args.get(name)
        if value is not None and not Path(str(value)).exists():
            raise BridgeError("input.not_found", f"{name} not found: {value}")


def cmd_timelapse(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.history import timelapse

    prepare_timelapse(args, ctx)
    name = str(args["name"])
    project, source = _no_search(args, name, ctx)
    result = timelapse(
        str(args["registry"]),
        name,
        column=str(args["column"]),
        table=args.get("table"),
        since=args.get("since"),
        until=args.get("until"),
        window=args.get("window"),
        project=project,
        source=source,
    )
    doc: dict[str, Any] = jsonable(result.to_dict())
    frames = doc.pop("frames", [])
    if not ctx.include_raw:
        classified = classified_in(args["registry"], name)
        column = str(args["column"])
        table = doc.get("table")
        if column in classified or (table is not None and f"{table}.{column}" in classified):
            frames = [_redact_frame(f) for f in frames]
    return {**doc, "frames": ctx.spill("the frames", frames)}


def _redact_frame(frame: dict[str, Any]) -> dict[str, Any]:
    top = frame.get("top_values")
    if not top:
        return frame
    return {
        **frame,
        "top_values": [{**t, "value": None} for t in top],
        "redacted": True,
    }


# ---- schemas ------------------------------------------------------------------------------

_CHANGE = obj(
    {"column": nullable(STR), "kind": STR},
    {"before": ANY, "after": ANY, "severity": nullable(STR), "score": ANY, "redacted": BOOL},
)
_VERSION_REF = obj({"ref": STR, "content_id": STR, "business_date": nullable(STR)})
_BISECT = obj(
    {
        "format": {"const": "shape-bisect"},
        "version": INT,
        "name": STR,
        "mode": STR,
        "found": BOOL,
        "changes": arr(_CHANGE),
        "candidates": INT,
        "evaluated": INT,
        "evaluations": arr(ANY),
        "cost": mapping(INT),
        "flips": arr(ANY),
        "warnings": STRS,
    },
    {
        "test": mapping(ANY),
        "good": _VERSION_REF,
        "bad": _VERSION_REF,
        "first_bad": nullable(_VERSION_REF),
        "last_good": nullable(_VERSION_REF),
        "changes_vs_good": arr(_CHANGE),
        "violations": arr(ANY),
        "max_evaluations": INT,
    },
)
_LAYERS = obj(
    {
        "format": {"const": "shape-bisect-layers"},
        "version": INT,
        "good_date": STR,
        "bad_date": STR,
        "project": STR,
        "found": BOOL,
        "first_layer": nullable(STR),
        "persists": STRS,
        "disappears": STRS,
        "layers": arr(
            obj(
                {
                    "source": STR,
                    "status": {"enum": ["origin", "persists", "disappears", "unchanged"]},
                    "changed": BOOL,
                    "changes": arr(_CHANGE),
                    "columns": STRS,
                }
            )
        ),
        "warnings": STRS,
    },
    {"column": nullable(STR)},
)
_FRAME = obj(
    {"date": STR, "end": STR, "versions": INT, "form": nullable(STR), "gap": BOOL},
    {
        "row_count": nullable(INT),
        "null_rate": ANY,
        "cardinality": ANY,
        "mean": ANY,
        "std": ANY,
        "quantiles": ANY,
        "top_values": nullable(arr(obj({"share": ANY}, {"value": ANY}))),
        "change_point": nullable(BOOL),
        "changes": arr(_CHANGE),
        "redacted": BOOL,
    },
)
_TIMELAPSE = obj(
    {
        "format": {"const": "shape-timelapse"},
        "version": INT,
        "name": STR,
        "column": STR,
        "frames": or_spilled(arr(_FRAME)),
    },
    {
        "table": nullable(STR),
        "window": nullable(STR),
        "since": nullable(STR),
        "until": nullable(STR),
        "source": nullable(STR),
        "change_points": ANY,
    },
)

COMMANDS = [
    Command(
        "bisect",
        "Find the first committed version of a name that changed: a binary search over a "
        "registry's history (job-capable).",
        {
            "registry": Arg("string", "the registry folder", True, path="read"),
            "name": Arg("string", "the committed name whose versions are searched", True),
            "good": Arg("string", "a registry ref (tag or content id) known to be good", True),
            "bad": Arg("string", "a later ref known to be bad", True),
            "column": Arg("string", "only changes of this column count"),
            "kind": Arg("string", "only changes of this diff kind count"),
            "contract": Arg(
                "string", "a contract file: a version is bad when the contract fails", path="read"
            ),
            "project": replace(PROJECT_ARG, since="1.2"),
            "source": Arg(
                "string", "the source of `project` whose thresholds apply (needs project)"
            ),
            "verify_all": Arg(
                "boolean", "test every version instead of bisecting; report any that flips back"
            ),
            "coarse": Arg(
                "string",
                "bisect over merged windows first (profiles need sketches)",
                enum=("week", "month"),
            ),
        },
        _BISECT,
        cmd_bisect,
        job=True,
        prepare=prepare_bisect,
        since="1.2",
        effects=("reads_files",),
    ),
    Command(
        "bisect_layers",
        "Find the layer of a pipeline where a change first appears (job-capable).",
        {
            "layers": Arg(
                "array", "sources of the project file, in pipeline order", True, items="string"
            ),
            "good_date": Arg("string", "the date of the good version (YYYY-MM-DD)", True),
            "bad_date": Arg("string", "the date of the bad version (YYYY-MM-DD)", True),
            "column": Arg("string", "the column, by the name the `map` values use"),
            "map": Arg(
                "object",
                'layer columns that are renamed: `{"LAYER.COL": "COL"}`',
            ),
            "project": Arg(
                "string",
                "the shape.yml file whose sources are the layers (the bridge never looks for "
                "one on its own, so give it)",
                path="read",
            ),
        },
        _LAYERS,
        cmd_layers,
        job=True,
        prepare=prepare_layers,
        since="1.2",
        effects=("reads_files",),
    ),
    Command(
        "timelapse",
        "One column's statistics across the committed versions of a name (job-capable).",
        {
            "registry": Arg("string", "the registry folder", True, path="read"),
            "name": Arg("string", "the committed name", True),
            "column": Arg("string", "the column to follow", True),
            "table": Arg("string", "the table of a dataset profile"),
            "since": Arg("string", "the first date, inclusive (YYYY-MM-DD)"),
            "until": Arg("string", "the last date, inclusive (YYYY-MM-DD)"),
            "window": Arg(
                "string",
                "merge the versions of each period (profiles need sketches)",
                enum=_WINDOWS,
            ),
        },
        _TIMELAPSE,
        cmd_timelapse,
        job=True,
        prepare=prepare_timelapse,
        since="1.2",
        effects=("reads_files",),
    ),
]
