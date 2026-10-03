"""``shape timelapse``: one column's statistics across the committed versions of a name.

One frame per version (or per merged window of versions): row count, null rate, distinct
estimate, quantiles p1 to p99, mean, standard deviation and top values, all read from the stored
profiles (nothing is read from data). A share-safe profile contributes what its safe form holds.
A version without the column is a gap. A frame is a change point when ``shape diff`` reports a
change of the column against the last frame before it that has the column.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from shape.history._common import (
    FORMAT_TIMELAPSE,
    VERSION,
    diff_profiles,
    drift_options,
    json_safe,
)
from shape.history.bisect import window_label
from shape.history.versions import HistoryError, Version, Versions
from shape.registry.local import LocalRegistry

WINDOWS = ("day", "week", "month")
QUANTILES = ("p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99")
STATISTICS = ("row_count", "null_rate", "cardinality", "mean", "std", *QUANTILES)
TOP_VALUES = 5
_NUMERIC = ("integer", "float")


class TimelapseResult:
    """The result of :func:`timelapse`. ``to_dict()`` is the JSON of ``shape timelapse``."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._doc)

    @property
    def frames(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._doc["frames"])

    @property
    def change_points(self) -> list[str]:
        return list(self._doc["change_points"])

    def __repr__(self) -> str:
        doc = self._doc
        return f"TimelapseResult({doc['name']}.{doc['column']}, {len(doc['frames'])} frames)"


def _day(label: str, value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HistoryError(f"{label}: {value!r} is not a date (use YYYY-MM-DD)") from None


def _groups(versions: list[Version], window: str | None) -> list[list[Version]]:
    if window is None:
        return [[v] for v in versions]
    out: list[list[Version]] = []
    keys: list[str] = []
    for v in versions:
        key = v.date.isoformat() if window == "day" else window_label(v.date, window)
        if keys and keys[-1] == key:
            out[-1].append(v)
        else:
            keys.append(key)
            out.append([v])
    return out


class _Loaded:
    """What a frame was read from: a profile (raw), or a share-safe document."""

    def __init__(self, versions: list[Version], profile: Any = None, safe: Any = None) -> None:
        self.versions = versions
        self.profile = profile
        self.safe = safe

    @property
    def tables(self) -> dict[str, dict[str, Any]]:
        if self.profile is not None:
            return dict(self.profile.tables)
        return dict(self.safe["tables"])


def _load_group(history: Versions, group: list[Version]) -> _Loaded:
    if len(group) == 1:
        safe = history.safe_document(group[0])
        if safe is not None:
            return _Loaded(group, safe=safe)
        return _Loaded(group, profile=history.profile(group[0]))
    from shape.profile.merge import merge_profiles

    for v in group:
        if history.safe_document(v) is not None:
            raise HistoryError(
                f"{history.name}@{v.content_id[:12]} ({v.date}) is a share-safe profile, which "
                "cannot be merged into a window: commit raw profiles with --sketches, or leave "
                "--window out"
            )
    profiles = [history.profile(v) for v in group]
    missing = [v for v, p in zip(group, profiles, strict=True) if p.sketches is None]
    if missing:
        raise HistoryError(
            f"{history.name}@{missing[0].content_id[:12]} ({missing[0].date}) has no sketch "
            "state, which --window needs to merge versions: commit profiles made with "
            "`shape profile --sketches`, or leave --window out"
        )
    return _Loaded(group, profile=merge_profiles(profiles, name=history.name))


def _top_values(col: dict[str, Any], safe: bool) -> list[dict[str, Any]] | None:
    if safe:
        shares = col.get("categorical_weights")
    elif col.get("dtype") not in _NUMERIC or col.get("is_enum"):
        shares = col.get("value_counts_ext")
    else:  # a continuous column: its stored value counts are single values, not categories
        shares = None
    if not isinstance(shares, dict) or not shares:
        return None
    ranked = sorted(shares.items(), key=lambda kv: -kv[1])[:TOP_VALUES]
    return [{"value": str(k), "share": v} for k, v in ranked]


def _frame(loaded: _Loaded, table: dict[str, Any] | None, column: str) -> dict[str, Any]:
    versions = loaded.versions
    out: dict[str, Any] = {
        "date": versions[0].date.isoformat(),
        "end": versions[-1].date.isoformat(),
        "versions": len(versions),
        "content_ids": [v.content_id for v in versions],
        "form": "safe" if loaded.safe is not None else "raw",
        "gap": True,
        "row_count": None,
        "null_rate": None,
        "cardinality": None,
        "mean": None,
        "std": None,
        "quantiles": None,
        "top_values": None,
        "change_point": False,
        "changes": [],
    }
    col = None if table is None else table["columns"].get(column)
    if col is None:
        return out
    quantiles = col.get("quantiles")
    out.update(
        {
            "gap": False,
            "row_count": table["row_count"] if table is not None else None,
            "null_rate": col.get("null_rate"),
            "cardinality": col.get("cardinality"),
            "mean": col.get("mean"),
            "std": col.get("std"),
            "quantiles": (
                {k: quantiles.get(k) for k in QUANTILES} if isinstance(quantiles, dict) else None
            ),
            "top_values": _top_values(col, loaded.safe is not None),
        }
    )
    return json_safe(out)  # type: ignore[no-any-return]


def timelapse(
    registry: str | Path | LocalRegistry,
    name: str,
    *,
    column: str,
    table: str | None = None,
    since: str | date | None = None,
    until: str | date | None = None,
    window: str | None = None,
    project: Any = None,
    source: str | None = None,
    thresholds: dict[str, Any] | None = None,
    ignore_columns: list[str] | None = None,
    column_thresholds: dict[str, dict[str, Any]] | None = None,
    only_columns: list[str] | None = None,
    policy: dict[str, Any] | str | Path | None = None,
) -> TimelapseResult:
    """The timelapse of ``column`` over the versions of ``name`` between ``since`` and ``until``
    (inclusive). ``window`` (``day``, ``week`` or ``month``) merges the versions of each period
    (profiles with sketch state); without it there is one frame per version. ``table`` picks the
    table of a dataset profile. The thresholds are those of ``shape.diff`` and decide the change
    points. Raises :class:`HistoryError` for unusable input."""
    if window is not None and window not in WINDOWS:
        raise HistoryError(f"--window is day, week or month, not {window!r}")
    first, last = _day("--since", since), _day("--until", until)
    if first is not None and last is not None and first > last:
        raise HistoryError(f"--since {first} is after --until {last}")
    options, source_name = drift_options(
        project,
        source,
        name,
        {
            "thresholds": thresholds,
            "ignore_columns": ignore_columns,
            "column_thresholds": column_thresholds,
            "only_columns": only_columns,
            "policy": policy,
        },
    )
    history = Versions(registry, name)
    chosen = [
        v
        for v in history.versions
        if (first is None or v.date >= first) and (last is None or v.date <= last)
    ]
    if not chosen:
        raise HistoryError(
            f"no versions of {name!r} between {first or 'the start'} and {last or 'the end'}"
        )
    loaded = [_load_group(history, g) for g in _groups(chosen, window)]
    holders = [tn for lo in loaded for tn, t in lo.tables.items() if column in t["columns"]]
    all_tables = {tn for lo in loaded for tn in lo.tables}
    if table is not None and table not in all_tables:
        raise HistoryError(
            f"table {table!r} is in none of the {len(chosen)} versions of {name!r} "
            f"(tables: {', '.join(sorted(all_tables))})"
        )
    where = table
    if where is None:
        names = sorted(set(holders))
        if len(names) > 1:
            raise HistoryError(
                f"column {column!r} is in several tables ({', '.join(names)}): choose one with "
                "--table"
            )
        where = names[0] if names else None
    if where is None or not any(
        column in lo.tables.get(where, {"columns": {}})["columns"] for lo in loaded
    ):
        raise HistoryError(
            f"column {column!r} is in none of the {len(chosen)} versions of {name!r}"
            + (f" (table {where})" if where else "")
        )
    frames = [_frame(lo, lo.tables.get(where), column) for lo in loaded]
    notes: list[str] = []
    previous: _Loaded | None = None
    undecided = 0
    for lo, frame in zip(loaded, frames, strict=True):
        if frame["gap"]:
            continue
        if previous is None:
            frame["change_point"] = False
        elif previous.safe is not None or lo.safe is not None:
            frame["change_point"] = None
            undecided += 1
        else:
            wanted = f"{where}.{column}" if lo.profile.is_dataset else column
            found = [
                c
                for c in diff_profiles(previous.profile, lo.profile, options)
                if c.get("column") == wanted
            ]
            frame["change_point"] = bool(found)
            frame["changes"] = sorted({c["kind"] for c in found})
        previous = lo
    safe_frames = sum(1 for f in frames if f["form"] == "safe")
    if safe_frames:
        notes.append(
            f"{safe_frames} frame(s) come from share-safe profiles: they show what the safe form "
            f"holds, and {undecided} frame(s) next to them are not compared, so no change point "
            "is decided for them"
        )
    if window is not None and any(f["versions"] > 1 for f in frames):
        notes.append(
            "a merged window holds no value counts, so its top values are empty; its row count "
            "is the sum over its versions"
        )
    doc = {
        "format": FORMAT_TIMELAPSE,
        "version": VERSION,
        "name": name,
        "column": column,
        "table": where,
        "window": window,
        "since": first.isoformat() if first else None,
        "until": last.isoformat() if last else None,
        "source": source_name,
        "frames": frames,
        "change_points": [f["date"] for f in frames if f["change_point"]],
        "notes": notes,
    }
    return TimelapseResult(json_safe(doc))


# ---- reading a written timelapse ----------------------------------------------------------------


def load_timelapse(path: str | Path) -> dict[str, Any]:
    """Read a timelapse JSON file written by ``shape timelapse -o OUT.json``."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise HistoryError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT_TIMELAPSE:
        raise HistoryError(f"{path} is not a timelapse (format {FORMAT_TIMELAPSE!r})")
    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise HistoryError(f"{path}: the version must be an integer from 1, got {version!r}")
    if version > VERSION:
        raise HistoryError(
            f"{path}: version {version} is newer than this Shape understands (it reads up to "
            f"version {VERSION}): upgrade Shape"
        )
    if not isinstance(doc.get("frames"), list):
        raise HistoryError(f"{path}: a timelapse holds a list of frames")
    return doc


# ---- text ---------------------------------------------------------------------------------------

_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: Sequence[float | None]) -> str:
    """One character per value, scaled between the smallest and the largest; ``·`` for a gap."""
    known = [v for v in values if v is not None]
    if not known:
        return "·" * len(values)
    lo, hi = min(known), max(known)
    out = []
    for v in values:
        if v is None:
            out.append("·")
        elif hi == lo:
            out.append(_BLOCKS[3])
        else:
            out.append(_BLOCKS[min(7, int((v - lo) / (hi - lo) * 8))])
    return "".join(out)


def _series(frames: list[dict[str, Any]], stat: str) -> list[float | None]:
    out: list[float | None] = []
    for f in frames:
        v = f["quantiles"].get(stat) if stat in QUANTILES and f["quantiles"] else f.get(stat)
        out.append(None if stat in QUANTILES and not f["quantiles"] else v)
    return out


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.6g}"


def render_text(doc: dict[str, Any]) -> str:
    """The timelapse as text: a line per statistic with a sparkline, and the change points."""
    frames = doc["frames"]
    where = f"{doc['table']}." if doc.get("table") and doc["table"] != doc["name"] else ""
    head = (
        f"{doc['name']}.{where}{doc['column']}  {frames[0]['date']} .. {frames[-1]['end']}  "
        f"{len(frames)} frames, {len(doc['change_points'])} change point(s)"
    )
    if doc.get("window"):
        head += f"  [{doc['window']} windows]"
    lines = [head]
    for stat in STATISTICS:
        series = _series(frames, stat)
        if all(v is None for v in series):
            continue
        known = [v for v in series if v is not None]
        lines.append(f"{stat:<12}{sparkline(series)}  {_fmt(known[0])} .. {_fmt(known[-1])}")
    marks = "".join("^" if f["change_point"] else " " for f in frames)
    if doc["change_points"]:
        lines.append(f"{'change':<12}{marks}  at {', '.join(doc['change_points'])}")
    else:
        lines.append(f"{'change':<12}(none)")
    latest = next((f for f in reversed(frames) if f["top_values"]), None)
    if latest:
        shown = "  ".join(f"{t['value']} {t['share']:.1%}" for t in latest["top_values"])
        lines.append(f"{'top values':<12}{shown}  (as of {latest['date']})")
    lines += [f"note: {n}" for n in doc.get("notes", [])]
    return "\n".join(lines)


# ---- html ---------------------------------------------------------------------------------------


def render_html(doc: dict[str, Any]) -> str:
    """One self-contained HTML file: inline data, inline svg and script, no network request. It
    has a play control, a slider over the frames, a chart of any statistic with the change
    points marked, the quantiles of the current frame and its top values."""
    import html as htmllib

    data = json.dumps(doc, sort_keys=True, ensure_ascii=True)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = htmllib.escape(f"Shape timelapse: {doc['name']}.{doc['column']}")
    from shape.history._page import PAGE

    return PAGE.replace("@@TITLE@@", title).replace("@@DATA@@", data)
