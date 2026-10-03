"""Layer bisect: in which layer of a pipeline did a change first appear?

Each layer is a source of ``shape.yml`` with a baseline registry and name. For every layer the
version at the bad date is diffed against the version at the good date (the newest version on or
before each date), under that source's thresholds and ignore lists.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from shape.history._common import (
    FORMAT_LAYERS,
    VERSION,
    column_matches,
    diff_profiles,
    drift_options,
    filtered_changes,
    json_safe,
    load_project,
)
from shape.history.versions import HistoryError, Versions


class LayerBisectResult:
    """The result of :func:`bisect_layers`. ``to_dict()`` is the JSON of
    ``shape bisect layers --json``."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._doc)

    @property
    def found(self) -> bool:
        return bool(self._doc["found"])

    @property
    def first_layer(self) -> str | None:
        first = self._doc["first_layer"]
        return None if first is None else str(first)

    @property
    def persists(self) -> list[str]:
        return list(self._doc["persists"])

    @property
    def disappears(self) -> list[str]:
        return list(self._doc["disappears"])

    def __repr__(self) -> str:
        return f"LayerBisectResult(first_layer={self.first_layer!r})"


def _day(label: str, value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HistoryError(f"{label}: {value!r} is not a date (use YYYY-MM-DD)") from None


def parse_map(items: Mapping[str, str], layers: Sequence[str]) -> dict[tuple[str, str], str]:
    """``{"LAYER.COL": "COL"}`` as ``{(layer, column in the layer): column}``. A layer name may
    hold dots, so the longest layer name that prefixes the key is the layer."""
    out: dict[tuple[str, str], str] = {}
    for left, canonical in items.items():
        owners = [layer for layer in layers if left.startswith(f"{layer}.")]
        if not owners or not isinstance(canonical, str) or not canonical:
            raise HistoryError(
                f"--map wants LAYER.COLUMN=COLUMN with LAYER one of {', '.join(layers)}, "
                f"got {left}={canonical}"
            )
        layer = max(owners, key=len)
        column = left[len(layer) + 1 :]
        if not column:
            raise HistoryError(f"--map {left}={canonical}: the layer's column is missing")
        out[(layer, column)] = canonical
    return out


def bisect_layers(
    layers: Sequence[str],
    *,
    good_date: str | date,
    bad_date: str | date,
    column: str | None = None,
    mapping: Mapping[str, str] | None = None,
    project: Any = None,
) -> LayerBisectResult:
    """The first of ``layers`` (sources of the project file, in pipeline order) whose version at
    ``bad_date`` differs from its version at ``good_date``, the layers downstream where the
    change persists and those where it is gone. ``column`` names the column in canonical terms;
    ``mapping`` (``{"LAYER.COL": "COL"}``) maps a layer's renamed column to it. Raises
    :class:`HistoryError` for unusable input."""
    if not layers:
        raise HistoryError("give at least one layer (`--layers SOURCE[,SOURCE...]`)")
    good, bad = _day("--good-date", good_date), _day("--bad-date", bad_date)
    if not good < bad:
        raise HistoryError(f"--good-date {good} is not before --bad-date {bad}")
    renames = parse_map(mapping or {}, list(layers))
    proj = load_project(project)
    if proj is None:
        raise HistoryError("shape bisect layers needs a shape.yml (or --project FILE): none found")
    entries: list[dict[str, Any]] = []
    for layer in layers:
        source = proj.source(layer)
        base = source.baseline
        if base is None:
            raise HistoryError(
                f"source {layer!r} declares no baseline in {proj.path}: its registry and name "
                "come from the baseline"
            )
        options, _ = drift_options(proj, layer, None, {})
        history = Versions(Path(base.registry), base.name)
        picks = []
        for label, day in (("good", good), ("bad", bad)):
            eligible = [v for v in history.versions if v.date <= day]
            if not eligible:
                raise HistoryError(
                    f"layer {layer!r}: no version of {base.name!r} on or before {day} "
                    f"({label} date)"
                )
            picks.append(eligible[-1])
        before, after = picks
        changes = filtered_changes(
            diff_profiles(history.profile(before), history.profile(after), options), None, None
        )
        wanted = []
        for c in changes:
            name = c.get("column")
            canonical = _canonical(layer, name, renames)
            if column is not None and not column_matches(canonical, column):
                continue
            if canonical != name:
                c = {**c, "column": canonical, "layer_column": name}
            wanted.append(c)
        entries.append(
            {
                "source": layer,
                "name": base.name,
                "registry": str(base.registry),
                "good": history.describe(before),
                "bad": history.describe(after),
                "changed": bool(wanted),
                "columns": sorted({c["column"] for c in wanted if c["column"] is not None}),
                "changes": wanted,
                "status": "unchanged",
            }
        )
    first = next((i for i, e in enumerate(entries) if e["changed"]), None)
    persists: list[str] = []
    disappears: list[str] = []
    if first is not None:
        origin = set(entries[first]["columns"])
        entries[first]["status"] = "origin"
        for e in entries[first + 1 :]:
            shown = bool(origin & set(e["columns"])) or (not origin and e["changed"])
            e["status"] = "persists" if shown else "disappears"
            (persists if shown else disappears).append(e["source"])
    doc = {
        "format": FORMAT_LAYERS,
        "version": VERSION,
        "good_date": good.isoformat(),
        "bad_date": bad.isoformat(),
        "column": column,
        "project": str(proj.path),
        "found": first is not None,
        "first_layer": None if first is None else entries[first]["source"],
        "persists": persists,
        "disappears": disappears,
        "layers": entries,
        "warnings": [],
    }
    return LayerBisectResult(json_safe(doc))


def _canonical(layer: str, name: Any, renames: dict[tuple[str, str], str]) -> Any:
    """The canonical name of a layer's column (``table.column`` keeps its table prefix)."""
    if not isinstance(name, str):
        return name
    if (layer, name) in renames:
        return renames[(layer, name)]
    table, dot, bare = name.partition(".")
    if dot and (layer, bare) in renames:
        return f"{table}.{renames[(layer, bare)]}"
    return name
