"""Helpers shared by the history operations: drift options, change records, JSON-safe values."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shape.history.versions import HistoryError

FORMAT_BISECT = "shape-bisect"
FORMAT_LAYERS = "shape-bisect-layers"
FORMAT_TIMELAPSE = "shape-timelapse"
VERSION = 1


def json_safe(value: Any) -> Any:
    """``value`` as plain JSON: tuples become lists, non-finite numbers ``None``."""
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


def load_project(project: Any) -> Any:
    from shape.project import Project, find_project, load_project

    if isinstance(project, Project):
        return project
    if project is not None:
        return load_project(project)
    found = find_project()
    return load_project(found) if found is not None else None


def drift_options(
    project: Any,
    source: str | None,
    hint: str | None,
    options: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """The ``shape.diff`` keywords for a run: the project source's policy (``source``, else the
    only source, else the one named ``hint``) underneath the explicit ``options``, which win."""
    from shape.cli.project import merge_diff_options

    chosen = None
    if project is not None or source is not None:
        proj = load_project(project)
        if proj is None:
            raise HistoryError("--source needs a shape.yml: none was found from here upwards")
        if source is not None:
            chosen = proj.source(source)
        elif len(proj.sources) == 1:
            chosen = next(iter(proj.sources.values()))
        elif hint is not None and hint in proj.sources:
            chosen = proj.sources[hint]
    merged = merge_diff_options(chosen, options, options.get("ignore_columns") is not None)
    return merged, (chosen.name if chosen is not None else None)


def column_matches(record_column: Any, wanted: str) -> bool:
    """A change's column (``table.column`` in a dataset) is the column asked for."""
    if not isinstance(record_column, str):
        return False
    return record_column == wanted or record_column.endswith(f".{wanted}")


def change_record(change: Mapping[str, Any]) -> dict[str, Any]:
    """A ``shape.diff`` change as the history results report it: column, kind, before, after."""
    out: dict[str, Any] = {
        "column": change.get("column"),
        "kind": change["kind"],
        "before": json_safe(change.get("baseline")),
        "after": json_safe(change.get("current")),
        "severity": change.get("severity"),
        "score": json_safe(change.get("score")),
    }
    if change.get("table") is not None:
        out["table"] = change["table"]
    return out


def filtered_changes(
    changes: list[dict[str, Any]], column: str | None, kind: str | None
) -> list[dict[str, Any]]:
    return [
        change_record(c)
        for c in changes
        if (column is None or column_matches(c.get("column"), column))
        and (kind is None or c["kind"] == kind)
    ]


def diff_profiles(before: Any, after: Any, options: dict[str, Any]) -> list[dict[str, Any]]:
    import shape

    result = shape.diff(
        before,
        after,
        thresholds=options.get("thresholds"),
        ignore_columns=options.get("ignore_columns"),
        column_thresholds=options.get("column_thresholds"),
        only_columns=options.get("only_columns"),
        policy=options.get("policy"),
    )
    return [dict(c) for c in result.changes]


def as_path(value: str | Path | None) -> str | None:
    return None if value is None else str(value)
