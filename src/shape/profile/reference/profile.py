"""The ``Profile`` object returned by ``shape.profile`` and its ``.shape`` artifact form."""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import math
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from shape.artifact import codec
from shape.artifact.io import ArtifactError, read_artifact, write_artifact

from .column import MAX_VALUE_CHARS
from .model import ColumnProfile, DatasetProfile, TableProfile
from .readers import CsvFormat
from .sources import SourceError, load_columns
from .table import _profile_cols_table, profile_dataset_columns

ARTIFACT_FORMAT = "shape"
ARTIFACT_FORMAT_VERSION = 1
ARTIFACT_KIND = "profile"
PROFILE_COMPONENT = "profile.json"

_COLUMN_FIELDS = (
    "name",
    "dtype",
    "null_count",
    "null_rate",
    "cardinality",
    "cardinality_ratio",
    "is_unique",
    "is_enum",
    "mean",
    "std",
    "distribution",
    "distribution_params",
    "pattern",
    "is_primary_key",
    "is_foreign_key",
    "fk_ref_table",
    "quantiles",
    "hour_histogram",
    "dow_histogram",
    "temporal_histogram",
    "string_length",
    "outlier_rate",
    "fit_score",
    "nan_count",
    "inf_count",
    "pattern_rates",
    "pattern_contains_rates",
    "precision",
    "scale",
)


_PLAIN = frozenset({str, int, bool, type(None)})  # leaves _clean returns unchanged
_STR_ONLY = frozenset({str})


_NUMBERS = frozenset({int, float, bool})


def _no_nan(values: Any) -> bool:
    """True when every value is a leaf ``_clean`` returns unchanged: plain str/int/bool/None, or
    numbers without NaN (a sum is NaN when any term is; an inf - inf false alarm only costs the
    slow path)."""
    types = set(map(type, values))
    if _PLAIN.issuperset(types):
        return True
    if _NUMBERS.issuperset(types):
        total = sum(values)
        return bool(total == total)
    return False


def _clean(v: Any) -> Any:
    """NaN becomes the string ``"NaN"``; numpy scalars become Python scalars."""
    t = type(v)
    if t is str or t is int or t is bool or v is None:  # the common leaves, with no further checks
        return v
    if t is float:
        return "NaN" if v != v else v
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    if isinstance(v, dict):
        # value counts: str keys over plain values need no per-entry work (types checked in C)
        if _STR_ONLY.issuperset(map(type, v)) and _no_nan(v.values()):
            return dict(v)
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        if _no_nan(v):
            return list(v)
        return [_clean(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        return _clean(v.item())
    return v


def _tag_scalar(v: Any) -> list[Any] | None:
    """Tag a min/max value with its Python type so type drift stays visible."""
    if v is None:
        return None
    type_name = type(v).__name__
    if isinstance(v, bool):
        return ["bool", bool(v)]
    if isinstance(v, int):
        return ["int", int(v)]
    if isinstance(v, float):
        return ["float", None if math.isnan(v) else float(v)]
    if isinstance(v, str):
        return ["str", v if len(v) <= MAX_VALUE_CHARS else v[:MAX_VALUE_CHARS] + "\u2026"]
    if type_name == "Timestamp":
        return ["timestamp", str(v)]
    if isinstance(v, _dt.datetime):
        return ["datetime", str(v)]
    if isinstance(v, _dt.date):
        return ["date", str(v)]
    return [type_name, str(v)]


def _column_dict(cp: ColumnProfile) -> dict[str, Any]:
    d = {f: _clean(getattr(cp, f)) for f in _COLUMN_FIELDS}
    d["min_value"] = _tag_scalar(cp.min_value)
    d["max_value"] = _tag_scalar(cp.max_value)
    d["enum_values"] = _clean(cp.enum_values)
    d["value_counts_ext"] = _clean(cp.value_counts_ext)
    # key order is the frequency order of the top values; keep it explicitly
    d["value_counts_ext_order"] = list(cp.value_counts_ext) if cp.value_counts_ext else None
    return d


def table_to_dict(tp: TableProfile) -> dict[str, Any]:
    """A table profile as a JSON-ready dict."""
    out = {
        "name": tp.name,
        "row_count": tp.row_count,
        "primary_key": list(tp.primary_key),
        "detected_fks": dict(tp.detected_fks),
        "correlation_matrix": _clean(tp.correlation_matrix),
        "columns": {c: _column_dict(cp) for c, cp in tp.columns.items()},
    }
    if tp.correlation_truncated:
        out["correlation_truncated"] = True
    return out


def dataset_to_dict(dp: DatasetProfile) -> dict[str, Any]:
    """A multi-table profile as a JSON-ready dict."""
    return {
        "tables": {n: table_to_dict(t) for n, t in dp.tables.items()},
        "relationships": _clean(dp.relationships),
    }


def _plain(tagged: Any) -> Any:
    """``["int", 5]`` -> ``5`` for summaries."""
    if isinstance(tagged, list) and len(tagged) == 2:
        return tagged[1]
    return None


def _column_summary(col: dict[str, Any]) -> dict[str, Any]:
    return {
        "dtype": col["dtype"],
        "null_rate": col["null_rate"],
        "cardinality": col["cardinality"],
        "is_unique": col["is_unique"],
        "is_primary_key": col["is_primary_key"],
        "is_foreign_key": col["is_foreign_key"],
        "fk_ref_table": col["fk_ref_table"],
        "distribution": col["distribution"],
        "pattern": col["pattern"],
        "min": _plain(col["min_value"]),
        "max": _plain(col["max_value"]),
        "mean": col["mean"],
        "std": col["std"],
    }


def _table_summary(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": table["name"],
        "row_count": table["row_count"],
        "primary_key": list(table["primary_key"]),
        "columns": {c: _column_summary(col) for c, col in table["columns"].items()},
    }


class Profile:
    """A data profile. ``to_dict()`` is the full profile as JSON-ready dicts."""

    def __init__(self, data: dict[str, Any], *, name: str | None = None) -> None:
        if "tables" not in data and "columns" not in data:
            raise ValueError("not a profile: expected a table or dataset profile dictionary")
        self._data = data
        self.name = name or (data.get("name") if "columns" in data else None) or "dataset"

    @property
    def is_dataset(self) -> bool:
        """True for a multi-table profile."""
        return "tables" in self._data

    @property
    def tables(self) -> dict[str, dict[str, Any]]:
        """Table profile dictionaries by name (one entry for a single-table profile)."""
        if self.is_dataset:
            return dict(self._data["tables"])
        return {self._data["name"]: self._data}

    def to_dict(self) -> dict[str, Any]:
        """The full profile as JSON-ready dicts."""
        return copy.deepcopy(self._data)

    def summary(self) -> dict[str, Any]:
        """A small JSON-safe dict: name, row_count and per-column statistics."""
        if not self.is_dataset:
            out = _table_summary(self._data)
            out["name"] = self.name if self.name else out["name"]
            return out
        return {
            "name": self.name,
            "row_count": sum(t["row_count"] for t in self._data["tables"].values()),
            "tables": {n: _table_summary(t) for n, t in self._data["tables"].items()},
            "relationships": copy.deepcopy(self._data["relationships"]),
        }

    def to_html(self) -> str:
        """A self-contained HTML report (no external assets)."""
        from shape.report import render_html

        return render_html(self)

    def __repr__(self) -> str:
        if self.is_dataset:
            return f"Profile(dataset, tables={list(self._data['tables'])})"
        return (
            f"Profile({self._data['name']!r}, rows={self._data['row_count']}, "
            f"columns={len(self._data['columns'])})"
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Profile) and _encode(self._data) == _encode(other._data)

    __hash__ = None  # type: ignore[assignment]


def profile(
    source: Any,
    *,
    name: str | None = None,
    delimiter: str | None = None,
    encoding: str | None = None,
    quotechar: str | None = None,
    header: bool = True,
) -> Profile:
    """Profile a path, glob, directory, Delta table, Arrow table or DataFrame.

    Pass a ``dict`` of such sources to profile several tables and detect foreign keys.

    CSV options: ``delimiter`` (default: sniffed among comma, semicolon, tab and pipe),
    ``encoding`` (default UTF-8), ``quotechar`` (default ``"``) and ``header=False`` for a file
    without a header row (columns are then ``f0``, ``f1``, ...).
    """
    fmt = CsvFormat(delimiter, encoding, quotechar, header)
    with np.errstate(all="ignore"):  # inf / NaN inputs are data, not numpy warnings
        return _profile(source, name, fmt)


def _load_tables(
    sources: dict[str, Any], csv: CsvFormat | None = None
) -> dict[str, tuple[list[Any], int]]:
    """Read every table, concurrently when there are several (the readers release the GIL), so
    the small tables' reads hide behind the largest one's. Errors surface in table order."""
    if len(sources) == 1:
        ((name, src),) = sources.items()
        _, cols, rows = load_columns(src, name, None, csv)
        _warn_delimiter(name, src, cols, csv)
        return {name: (cols, rows)}
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futures = [(n, ex.submit(load_columns, src, n, None, csv)) for n, src in sources.items()]
        loaded = [(n, f.result()) for n, f in futures]
    for n, (_, cols, _rows) in loaded:
        _warn_delimiter(n, sources[n], cols, csv)
    return {n: (cols, rows) for n, (_, cols, rows) in loaded}


def _warn_delimiter(name: str, src: Any, cols: list[Any], csv: CsvFormat | None) -> None:
    """A CSV that came out as one column whose name holds a likely delimiter was probably split
    on the wrong one: say so instead of profiling it silently."""
    if len(cols) != 1 or not isinstance(src, (str, Path)) or not str(src).lower().endswith(".csv"):
        return
    col_name = str(cols[0].name)
    found = [d for d in (";", "\t", "|", ",") if d in col_name]
    if found:
        warnings.warn(
            f"{name!r} was read as one column called {col_name!r}, which contains "
            f"{found[0]!r}: the file may use that delimiter. Pass delimiter={found[0]!r} "
            "(shape profile --delimiter).",
            UserWarning,
            stacklevel=4,
        )


def _profile(source: Any, name: str | None, csv: CsvFormat | None = None) -> Profile:
    if isinstance(source, dict):
        if not source:
            raise SourceError("an empty dict of tables cannot be profiled")
        cols_by_t = _load_tables({str(k): v for k, v in source.items()}, csv)
        return Profile(dataset_to_dict(profile_dataset_columns(cols_by_t)), name=name)
    table_name, cols, rows = load_columns(source, name, None, csv)
    _warn_delimiter(table_name, source, cols, csv)
    table = _profile_cols_table(table_name, cols, rows, None)
    return Profile(table_to_dict(table), name=name)


# --- .shape artifact ---------------------------------------------------------------


def _encode(data: dict[str, Any]) -> bytes:
    # No sort_keys: the order of enum_values / value_counts_ext is meaningful.
    return codec.dumps(data, sort_keys=False)


def save(p: Profile, path: str | Path) -> str:
    """Write ``p`` to a ``.shape`` artifact and return its content id (sha256)."""
    if not isinstance(p, Profile):
        raise TypeError(f"save() expects a Profile, got {type(p).__name__}")
    body = _encode(p._data)
    content_id = hashlib.sha256(body).hexdigest()
    manifest = {
        "format": ARTIFACT_FORMAT,
        "format_version": ARTIFACT_FORMAT_VERSION,
        "kind": ARTIFACT_KIND,
        "name": p.name,
        "shape_content_id": content_id,
    }
    write_artifact(str(path), manifest, {PROFILE_COMPONENT: body})
    return content_id


def load(path: str | Path) -> Profile:
    """Read a ``.shape`` artifact written by :func:`save`."""
    manifest, parts = read_artifact(str(path))
    if manifest.get("format") != ARTIFACT_FORMAT or manifest.get("kind") != ARTIFACT_KIND:
        raise ArtifactError(f"{path} is not a Shape profile artifact")
    version = manifest.get("format_version")
    if not isinstance(version, int) or not 1 <= version <= ARTIFACT_FORMAT_VERSION:
        raise ArtifactError("unsupported Shape profile artifact version")
    body = parts.get(PROFILE_COMPONENT)
    if body is None:
        raise ArtifactError("profile component missing")
    if hashlib.sha256(body).hexdigest() != manifest.get("shape_content_id"):
        raise ArtifactError("Shape content identity mismatch")
    try:
        data = codec.loads(body)
    except (ValueError, TypeError, RecursionError) as e:
        raise ArtifactError(f"invalid {PROFILE_COMPONENT}: {e}") from e
    if not isinstance(data, dict):
        raise ArtifactError(f"invalid {PROFILE_COMPONENT}: not an object")
    return Profile(data, name=str(manifest.get("name") or "") or None)
