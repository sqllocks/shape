"""The drift engine: one set of rules and thresholds behind ``shape.diff``, ``ShapeMonitor``,
``ShapeTimeline.changes`` and the windowed profiler.

Everything that asks "did the data change?" lands here. Its input is a *view* of each column (type,
null rate, distinct count, moments, quantiles, category proportions, pattern, string length, ...)
read from whatever was profiled: a :class:`shape.profile.reference.Profile`, a window's profile
document from the stream runtime, or a Shape model. Its output is a list of change records::

    {"column": ..., "kind": ..., "baseline": ..., "current": ..., "severity": ..., "score": ...}

``severity`` (``low``, ``medium``, ``high``) is fixed per ``kind``. ``score`` is the size of the
change on a 0 to 1 scale (see ``docs/DRIFT.md``): structural changes score 1, and a numeric change
scores its natural distance (an absolute rate change, a total variation distance, a KS distance).
A change is reported only when it passes its threshold, so a stable column produces no record and
a monitor's maximum score is 0 until something moved.

Thresholds are the defaults in :data:`DEFAULT_THRESHOLDS`; every one can be overridden globally
(``thresholds``) or per column (``column_thresholds``), and columns can be ignored or selected.
The thresholds that depend on sampling noise (``category_tvd``, ``true_rate``, ``ks_distance``,
``outlier_rate``, ``temporal_tvd``) never go below the noise two samples of the given sizes show
on their own: two samples of one distribution do not drift, however tight the setting.
"""

from __future__ import annotations

import fnmatch
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

# The first five are the §12.3 contract and keep their values. The rest are the defaults of the
# comparisons added with them (documented in docs/DRIFT.md).
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "null_rate": 0.05,  # absolute change
    "cardinality_ratio_max": 1.5,
    "cardinality_ratio_min": 0.67,
    "mean_shift_std": 0.5,  # multiples of the baseline standard deviation
    "min_severity": "low",  # changes below this severity are not reported
    "category_tvd": 0.10,  # total variation distance between category proportions
    "true_rate": 0.10,  # absolute change of the share of true values
    "std_ratio_max": 1.5,  # standard deviation ratio, current over baseline
    "std_ratio_min": 0.67,
    "ks_distance": 0.10,  # KS distance between the two distributions, read from the quantiles
    "range_margin_std": 2.0,  # a min or max moving past the baseline's by this many baseline stds
    "length_ratio": 0.25,  # relative change of the mean string length
    "outlier_rate": 0.02,  # absolute change of the outlier rate
    "uniqueness_rate": 0.05,  # absolute change of distinct values per row, for unique-like columns
    "temporal_tvd": 0.20,  # total variation distance of the hour-of-day / day-of-week mix
    "min_rows": 30,  # fewer non-null values than this: no distribution comparison
    "dependency_confidence": 0.02,  # absolute drop of an approximate functional dependency
    "placeholder_share": 0.01,  # absolute rise of the share of rows holding a placeholder value
    "implausible_rate": 0.02,  # absolute rise of the share of implausible rows
    "association_shift": 0.2,  # absolute change of an association measure (V, U, eta, |r|)
    "reference_match_rate": 0.02,  # absolute drop of the share of rows in a reference
    "row_count_ratio_max": 2.0,  # table rows over baseline's: more than double is a change
    "row_count_ratio_min": 0.5,  # fewer than half is one too (a 1.5x extract is not)
}

KIND_SEVERITY: dict[str, str] = {
    "table_added": "high",
    "table_removed": "high",
    "column_added": "high",
    "column_removed": "high",
    "dtype_change": "high",
    "row_count_change": "medium",
    "null_rate_change": "medium",
    "cardinality_change": "medium",
    "uniqueness_change": "medium",
    "mean_shift": "medium",
    "spread_change": "medium",
    "distribution_shift": "medium",
    "category_shift": "medium",
    "true_rate_change": "medium",
    "pattern_change": "medium",
    "distribution_change": "low",
    "new_categorical_values": "low",
    "range_change": "low",
    "length_change": "low",
    "outlier_rate_change": "low",
    "hour_of_day_change": "low",
    "day_of_week_change": "low",
    "dependency_broken": "high",
    "placeholder_surge": "medium",
    "implausible_rate_change": "medium",
    "association_shift": "low",
    "reference_match_change": "high",
}

_NUMERIC = ("integer", "float")
# the column the stream runtime adds to every window it profiles (shape.streaming.runtime)
_WINDOW_TIME = "_shape_event_time"
_UNIQUE_LIKE = 0.95  # distinct values per non-null row at or above this: a key-like column
_KS_ALPHA_001 = 1.95  # KS critical coefficient at alpha = 0.001
_DISTRIBUTION_LABEL_SCORE = 0.2  # a fitted-family name changed; the size is distribution_shift's


# --- policy: thresholds, per-column overrides, ignore and only lists ----------------------------

_POLICY_KEYS = {"thresholds", "columns", "ignore", "only"}


def _check_thresholds(th: Mapping[str, Any], where: str = "") -> None:
    unknown = set(th) - set(DEFAULT_THRESHOLDS)
    if unknown:
        raise ValueError(f"unknown thresholds{where}: {sorted(unknown)}")
    for key, value in th.items():
        if key == "min_severity":
            if value not in SEVERITY_RANK:
                raise ValueError("min_severity must be 'low', 'medium' or 'high'")
        elif (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or math.isnan(value)
            or value < 0
        ):
            raise ValueError(f"threshold {key!r}{where} must be a number of 0 or more")


@dataclass(frozen=True)
class Policy:
    """Thresholds (global and per column) plus the ignore and only lists."""

    thresholds: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    columns: dict[str, dict[str, Any]] = field(default_factory=dict)
    ignore: tuple[str, ...] = ()
    only: tuple[str, ...] = ()

    def _matches(self, patterns: Iterable[str], table: str | None, column: str | None) -> bool:
        names = [n for n in (column, f"{table}.{column}" if table and column else None) if n]
        if column is None and table:
            names = [table]  # a table-level change: the table's name
        return any(fnmatch.fnmatchcase(n, p) for p in patterns for n in names)

    def skips(self, table: str | None, column: str | None) -> bool:
        """True when the change on ``table.column`` is ignored (or outside ``only``)."""
        if self.ignore and self._matches(self.ignore, table, column):
            return True
        return bool(self.only) and not self._matches(self.only, table, column)

    def for_column(self, table: str | None, column: str | None) -> dict[str, Any]:
        """The thresholds for one column: the global ones, then the overrides of every matching
        pattern, least specific first (``*``, a glob, the column name, ``table.column``)."""
        th = dict(self.thresholds)
        if not self.columns:
            return th
        full = f"{table}.{column}" if table and column else None

        def rank(pattern: str) -> int:
            if pattern == full:
                return 3
            if pattern == column:
                return 2
            return 0 if pattern == "*" else 1

        for pattern in sorted(self.columns, key=rank):
            if self._matches([pattern], table, column):
                th.update(self.columns[pattern])
        return th


def resolve_policy(
    thresholds: Mapping[str, Any] | None = None,
    *,
    ignore_columns: Iterable[str] | None = None,
    column_thresholds: Mapping[str, Mapping[str, Any]] | None = None,
    only_columns: Iterable[str] | None = None,
    policy: Mapping[str, Any] | str | Path | None = None,
) -> Policy:
    """Build a :class:`Policy`. ``policy`` is a dict or a JSON file with the keys ``thresholds``,
    ``columns`` (pattern to thresholds), ``ignore`` and ``only``; a contract's ``"drift"`` object
    has the same layout. Explicit arguments override the policy's."""
    base: dict[str, Any] = {}
    if policy is not None:
        loaded = _load_policy(policy)
        base = dict(loaded)
    th = dict(DEFAULT_THRESHOLDS)
    for key in ("thresholds", "columns"):
        if not isinstance(base.get(key, {}), Mapping):
            raise ValueError(f"drift policy {key!r} must be an object")
    merged_th = {**base.get("thresholds", {}), **(thresholds or {})}
    _check_thresholds(merged_th)
    th.update(merged_th)
    columns: dict[str, dict[str, Any]] = {}
    for source in (base.get("columns", {}), column_thresholds or {}):
        for pattern, over in source.items():
            if not isinstance(over, Mapping):
                raise ValueError(f"thresholds for column {pattern!r} must be an object")
            _check_thresholds(over, f" for column {pattern!r}")
            columns.setdefault(str(pattern), {}).update(over)
    ignore = tuple(base.get("ignore", ())) + tuple(ignore_columns or ())
    only = tuple(only_columns) if only_columns is not None else tuple(base.get("only", ()))
    return Policy(th, columns, tuple(map(str, ignore)), tuple(map(str, only)))


def _load_policy(policy: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
    if isinstance(policy, (str, Path)):
        try:
            with open(policy, encoding="utf-8") as fh:
                policy = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"drift policy {policy} is not valid JSON: {exc}") from exc
    if not isinstance(policy, Mapping):
        raise ValueError("a drift policy must be a JSON object")
    if "drift" in policy and isinstance(policy["drift"], Mapping):
        policy = policy["drift"]
    unknown = set(policy) - _POLICY_KEYS
    if unknown:
        raise ValueError(f"unknown drift policy keys: {sorted(unknown)}")
    for key in ("ignore", "only"):
        value = policy.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"drift policy {key!r} must be a list of column names")
    return policy


# --- column views --------------------------------------------------------------------------------


@dataclass
class View:
    """What the engine reads of one column."""

    dtype: str
    non_null: int
    null_rate: float | None
    cardinality: int | None
    primary_key: bool = False
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    quantiles: list[tuple[float, float]] = field(default_factory=list)
    pattern: str | None = None
    categories: dict[str, float] | None = None
    length_mean: float | None = None
    outlier_rate: float | None = None
    distribution: str | None = None
    hour: list[float] | None = None
    dow: list[float] | None = None
    span_days: float | None = None
    origin: str = "profile"  # "profile" (the reference profiler) or "engine" (profile engine)
    placeholders: list[dict[str, Any]] = field(default_factory=list)  # sentinel values (#47)
    top_values: dict[str, float] | None = None  # share of the non-null values, most frequent first

    @property
    def unique_rate(self) -> float | None:
        if self.cardinality is None or not self.non_null:
            return None
        return self.cardinality / self.non_null

    @property
    def unique_like(self) -> bool:
        rate = self.unique_rate
        return rate is not None and rate >= _UNIQUE_LIKE

    @property
    def sequential(self) -> bool:
        """A dense unique integer column: a counter or surrogate key. Its mean and spread only
        say how many rows were loaded and where the counter stood."""
        if self.dtype != "integer" or not self.unique_like:
            return False
        if self.min is None or self.max is None or self.cardinality is None:
            return False
        return (self.max - self.min + 1) <= 2 * self.cardinality

    @property
    def key_like(self) -> bool:
        return self.primary_key or self.sequential

    @property
    def flag(self) -> bool:
        """A boolean, or a column that only holds 0 and 1 (``1.0`` and ``0.0``, ``True`` and
        ``False`` too)."""
        if self.dtype == "boolean":
            return True
        if not self.categories or self.dtype not in ("integer", "float", "string"):
            return False
        return all(_flag_value(k) is not None for k in self.categories)

    @property
    def true_rate(self) -> float | None:
        if not self.flag or self.categories is None:
            return None
        return sum(v for k, v in self.categories.items() if _flag_value(k))


def _flag_value(key: str) -> bool | None:
    """``True``, ``1`` and ``1.0`` are true, ``False``, ``0`` and ``0.0`` false; else neither."""
    if key in ("True", "False"):
        return key == "True"
    try:
        number = float(key)
    except ValueError:
        return None
    return number == 1.0 if number in (0.0, 1.0) else None


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _plain(tagged: Any) -> Any:
    return tagged[1] if isinstance(tagged, list) and len(tagged) == 2 else None


def _level(key: str) -> float | None:
    """A quantile key to its level: ``p0_5`` and ``0.005`` are both 0.005 of the data."""
    try:
        if key.startswith("p"):
            return float(key[1:].replace("_", ".")) / 100.0
        return float(key)
    except ValueError:
        return None


def _quantile_pairs(q: Any) -> list[tuple[float, float]]:
    if not isinstance(q, Mapping):
        return []
    pairs = []
    for key, value in q.items():
        level, v = _level(str(key)), _number(value)
        if level is not None and v is not None and 0.0 <= level <= 1.0:
            pairs.append((level, v))
    return sorted(pairs)


def _days(value: Any) -> float | None:
    """A min/max of a temporal column (an ISO string, or microseconds) as days since the epoch."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 86_400_000_000.0
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp() / 86_400.0
        except ValueError:
            return None
    return None


def _complete(proportions: Mapping[str, float] | None) -> dict[str, float] | None:
    """The proportions when they cover all the non-null rows (not just the top values)."""
    if not proportions:
        return None
    total = sum(proportions.values())
    return {str(k): float(v) for k, v in proportions.items()} if total >= 0.999 else None


_PATTERN_DATE = {"date"}


def view_of_profile_column(col: Mapping[str, Any], rows: int) -> View:
    """A column of a :class:`~shape.profile.reference.Profile` (the profile's dict form)."""
    null_count = int(col.get("null_count") or 0)
    dtype = str(col["dtype"])
    categories: dict[str, float] | None = None
    if dtype != "datetime" and col.get("pattern") not in _PATTERN_DATE:
        # the profiler's enum rule says which columns are categories; a boolean always is
        categories = _complete(
            col.get("enum_values") or (col.get("value_counts_ext") if dtype == "boolean" else None)
        )
    length = col.get("string_length") or {}
    mn, mx = _plain(col.get("min_value")), _plain(col.get("max_value"))
    view = View(
        dtype=dtype,
        non_null=max(rows - null_count, 0),
        null_rate=col.get("null_rate"),
        cardinality=col.get("cardinality"),
        primary_key=bool(col.get("is_primary_key")),
        mean=_number(col.get("mean")),
        std=_number(col.get("std")),
        min=_number(mn) if dtype in _NUMERIC else None,
        max=_number(mx) if dtype in _NUMERIC else None,
        quantiles=_quantile_pairs(col.get("quantiles")),
        pattern=col.get("pattern"),
        categories=categories,
        length_mean=_number(length.get("mean")) if dtype == "string" else None,
        outlier_rate=_number(col.get("outlier_rate")),
        distribution=col.get("distribution"),
        placeholders=list(col.get("placeholders") or ()),
        top_values=col.get("value_counts_ext"),
    )
    if dtype == "datetime":
        view.hour = col.get("hour_histogram")
        view.dow = col.get("dow_histogram")
        lo, hi = _days(mn), _days(mx)
        view.span_days = None if lo is None or hi is None else hi - lo
    return view


_ENGINE_DTYPE = {
    "int": "integer",
    "float": "float",
    "text": "string",
    "bool": "boolean",
    "temporal": "datetime",
}
_ENGINE_PATTERN_SHARE = 0.9  # as the reference profiler: this share of the values matches


def view_of_engine_column(col: Mapping[str, Any], rows: int) -> View:
    """A column of a profile-engine table entry or a Shape model (the stream runtime's windows
    use the same layout)."""
    kind = str(col.get("kind", ""))
    dtype = _ENGINE_DTYPE.get(kind, kind)
    count = int(col.get("count") or rows)
    null_count = int(col.get("null_count") or 0)
    non_null = max(count - null_count, 0)
    distinct = _number(col.get("distinct"))
    variance = _number(col.get("variance_sample"))
    if variance is None:
        variance = _number(col.get("variance_population"))
    if variance is None and _number(col.get("m2")) is not None and non_null > 1:
        variance = float(col["m2"]) / (non_null - 1)
    categories: dict[str, float] | None = None
    top = col.get("top")
    if kind == "bool" and non_null:
        categories = {
            "True": int(col.get("true_count") or 0) / non_null,
            "False": int(col.get("false_count") or 0) / non_null,
        }
    elif isinstance(top, list) and top and non_null and kind != "temporal" and distinct is not None:
        counts = {str(row[0]): int(row[1]) for row in top}
        repeats = distinct * 2 <= non_null and distinct < non_null
        small = distinct < 200 or (distinct / non_null < 0.30 and distinct < 50_000)
        if repeats and small and sum(counts.values()) >= non_null:  # the profiler's enum rule
            categories = {k: v / non_null for k, v in counts.items()}
    length = col.get("length") or {}
    patterns = col.get("patterns") or {}
    pattern = None
    if non_null and isinstance(patterns, Mapping):
        best = max(patterns.items(), key=lambda kv: kv[1], default=None)
        if best is not None and best[1] >= _ENGINE_PATTERN_SHARE * non_null:
            pattern = {"mac": "mac_address", "ipv4": "ip_address", "ipv6": "ip_address"}.get(
                best[0], best[0]
            )
    if pattern in _PATTERN_DATE:
        categories = None
        dtype = "datetime"  # the reference profiler types date strings as datetime
    view = View(
        origin="engine",
        dtype=dtype,
        non_null=non_null,
        null_rate=(null_count / count) if count else None,
        cardinality=None if distinct is None else int(round(distinct)),
        mean=_number(col.get("mean")),
        std=math.sqrt(variance) if variance is not None and variance >= 0 else None,
        min=_number(col.get("min")) if dtype in _NUMERIC else None,
        max=_number(col.get("max")) if dtype in _NUMERIC else None,
        quantiles=_quantile_pairs(col.get("quantiles")),
        pattern=pattern,
        categories=categories,
        length_mean=_number(length.get("mean")) if dtype == "string" else None,
    )
    if kind == "temporal":
        hour, dow = col.get("hour_hist"), col.get("dow_hist")
        view.hour = _normalise(hour)
        view.dow = _normalise(dow)
        lo, hi = _days(col.get("min")), _days(col.get("max"))
        view.span_days = None if lo is None or hi is None else hi - lo
    return view


def _normalise(counts: Any) -> list[float] | None:
    if not isinstance(counts, list) or not counts:
        return None
    total = float(sum(counts))
    return [c / total for c in counts] if total > 0 else None


@dataclass
class TableView:
    rows: int
    columns: dict[str, View]
    joint: Mapping[str, Any] | None = None  # the profile's joint analysis (#47)

    @property
    def window(self) -> bool:
        """A stream runtime window (it carries the event-time column): how many rows it holds
        is the window's size, not the table's."""
        return _WINDOW_TIME in self.columns


def _profile_tables(obj: Any) -> dict[str, TableView]:
    out: dict[str, TableView] = {}
    for name, table in obj.tables.items():
        rows = int(table["row_count"])
        out[name] = TableView(
            rows,
            {c: view_of_profile_column(col, rows) for c, col in table["columns"].items()},
            table.get("joint"),
        )
    return out


def _engine_table(table: Mapping[str, Any]) -> TableView:
    rows = int(table.get("rows", table.get("row_count", 0)) or 0)
    cols = table["columns"]
    if isinstance(cols, Mapping):
        items = [(str(k), v) for k, v in cols.items()]
    else:
        items = [(str(c["name"]), c) for c in cols]
    return TableView(rows, {n: view_of_engine_column(c, rows) for n, c in items})


def tables_of(obj: Any) -> tuple[dict[str, TableView], bool]:
    """``(tables, is_dataset)`` for anything the engine reads: a ``Profile``; a window of the
    stream runtime (``WindowProfile``) or its profile document; a profile-engine document or a
    Shape model (``{"tables": ...}``). A dataset is named by table in the change records."""
    if hasattr(obj, "is_dataset") and hasattr(obj, "tables"):
        return _profile_tables(obj), bool(obj.is_dataset)
    inner = getattr(obj, "profile", None)  # a WindowProfile
    if isinstance(inner, Mapping):
        obj = inner
    if isinstance(obj, Mapping):
        if isinstance(obj.get("tables"), Mapping):
            tables = {str(n): _engine_table(t) for n, t in obj["tables"].items()}
            return tables, len(tables) > 1
        if isinstance(obj.get("columns"), list):
            return {str(obj.get("name", "table")): _engine_table(obj)}, False
        if isinstance(obj.get("columns"), Mapping):  # a v1 capture: migrate it to a model
            from shape.spec.view import model_of

            return tables_of(model_of(obj))
    raise TypeError(
        f"expected a Profile, a window profile or a profile document, not {type(obj).__name__}"
    )


# --- the comparisons -----------------------------------------------------------------------------


def _change(
    column: str | None, kind: str, baseline: Any, current: Any, score: float
) -> dict[str, Any]:
    return {
        "column": column,
        "kind": kind,
        "baseline": baseline,
        "current": current,
        "severity": KIND_SEVERITY[kind],
        "score": round(min(1.0, max(0.0, float(score))), 4),
    }


def _ratio_score(ratio: float) -> float:
    return 0.0 if ratio <= 0 else 1.0 - min(ratio, 1.0 / ratio)


def _tvd(a: Mapping[Any, float], b: Mapping[Any, float]) -> float:
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def _tvd_noise(a: Mapping[Any, float], b: Mapping[Any, float], n1: int, n2: int) -> float:
    """The total variation distance two samples of one distribution show on their own."""
    spread = 1.0 / n1 + 1.0 / n2
    total = 0.0
    for k in set(a) | set(b):
        p = 0.5 * (a.get(k, 0.0) + b.get(k, 0.0))
        total += math.sqrt(max(p * (1.0 - p), 0.0) * spread)
    return 0.4 * total


def _largest_moves(
    a: Mapping[str, float], b: Mapping[str, float], limit: int = 10
) -> tuple[dict[str, float], dict[str, float]]:
    """The proportions of the ``limit`` categories that moved most, for the change record."""
    keys = sorted(set(a) | set(b), key=lambda k: (-abs(a.get(k, 0.0) - b.get(k, 0.0)), k))[:limit]
    return (
        {k: round(a.get(k, 0.0), 4) for k in keys},
        {k: round(b.get(k, 0.0), 4) for k in keys},
    )


def _cdf(view: View) -> tuple[np.ndarray, np.ndarray] | None:
    import numpy as np

    pairs = list(view.quantiles)
    if view.min is not None:
        pairs.append((0.0, view.min))
    if view.max is not None:
        pairs.append((1.0, view.max))
    if len(pairs) < 3:
        return None
    xs = np.array([v for _, v in pairs], dtype=float)
    ys = np.array([q for q, _ in pairs], dtype=float)
    order = np.lexsort((ys, xs))
    xs, ys = xs[order], np.maximum.accumulate(ys[order])
    uniq, idx = np.unique(xs, return_index=True)
    last = np.append(idx[1:], len(xs)) - 1  # the highest level of each repeated x
    return uniq, ys[last]


def _ks(base: View, cur: View) -> float | None:
    """The KS distance between two columns, from their quantiles (piecewise-linear CDFs)."""
    import numpy as np

    a, b = _cdf(base), _cdf(cur)
    if a is None or b is None:
        return None
    xs = np.union1d(a[0], b[0])
    fa = np.interp(xs, a[0], a[1], left=0.0, right=1.0)
    fb = np.interp(xs, b[0], b[1], left=0.0, right=1.0)
    return float(np.max(np.abs(fa - fb)))


def _summary(view: View) -> dict[str, float]:
    """A few quantiles of a column, for the change record."""
    return {
        f"p{round(level * 100):02d}": round(v, 6)
        for level in (0.05, 0.25, 0.5, 0.75, 0.95)
        if (v := _quantile_at(view, level)) is not None
    }


def _quantile_at(view: View, level: float) -> float | None:
    for lv, v in view.quantiles:
        if abs(lv - level) < 1e-9:
            return v
    return None


def _ks_critical(base: View, cur: View) -> float:
    """The KS distance two samples of one distribution stay under (alpha = 0.001)."""
    return _KS_ALPHA_001 * math.sqrt(
        (base.non_null + cur.non_null) / (base.non_null * cur.non_null)
    )


def _family_evidence(base: View, cur: View, enough: bool) -> bool:
    """True when a changed fitted-family name is backed by the data. The family a column is fit
    as flips between two samples of one distribution, so the name alone is not a change: the
    samples must also differ by more than the noise samples of their sizes show (the KS critical
    value, with no floor, so a small real change still counts). Without quantiles to compare, only
    samples of ``min_rows`` or more are trusted to name a family."""
    ks = _ks(base, cur)
    if ks is None:
        return enough
    if not base.non_null or not cur.non_null:
        return False
    return ks > _ks_critical(base, cur)


def _diff_column(name: str, base: View, cur: View, th: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # The two profilers type whole-valued floats differently, and Shape models know one numeric
    # family: int against float is a type change only between two reference profiles.
    cross_numeric = (
        base.dtype in _NUMERIC
        and cur.dtype in _NUMERIC
        and (base.origin != cur.origin or base.origin == "engine")
    )
    if base.dtype != cur.dtype and not cross_numeric:
        out.append(_change(name, "dtype_change", base.dtype, cur.dtype, 1.0))
    b_null, c_null = base.null_rate, cur.null_rate
    if b_null is not None and c_null is not None and abs(c_null - b_null) > th["null_rate"]:
        out.append(_change(name, "null_rate_change", b_null, c_null, abs(c_null - b_null)))
    out.extend(_diff_cardinality(name, base, cur, th))
    # a distribution needs values on both sides, whatever ``min_rows`` (0 is allowed) says
    enough = min(base.non_null, cur.non_null) >= max(th["min_rows"], 1)
    keyed = base.key_like or cur.key_like
    flag = base.flag and cur.flag
    if base.dtype in _NUMERIC and cur.dtype in _NUMERIC and not keyed and not flag:
        out.extend(_diff_numeric(name, base, cur, th, enough))
    fitted = "engine" not in (base.origin, cur.origin)  # the engine's documents carry no fit
    if (
        fitted
        and not keyed
        and not flag
        and base.distribution != cur.distribution
        and _family_evidence(base, cur, enough)
    ):
        out.append(
            _change(
                name,
                "distribution_change",
                base.distribution,
                cur.distribution,
                _DISTRIBUTION_LABEL_SCORE,
            )
        )
    if flag:
        b_rate, c_rate = base.true_rate, cur.true_rate
        if b_rate is not None and c_rate is not None and enough:
            se = math.sqrt(
                max(0.25 if not 0 < b_rate < 1 else b_rate * (1 - b_rate), 0.0)
                * (1.0 / base.non_null + 1.0 / cur.non_null)
            )
            if abs(c_rate - b_rate) > max(th["true_rate"], 4.0 * se):
                out.append(_change(name, "true_rate_change", b_rate, c_rate, abs(c_rate - b_rate)))
    elif base.categories is not None and cur.categories is not None:
        out.extend(_diff_categories(name, base, cur, th, enough))
    if base.pattern != cur.pattern and "string" in (base.dtype, cur.dtype):
        out.append(_change(name, "pattern_change", base.pattern, cur.pattern, 1.0))
    if base.length_mean is not None and cur.length_mean is not None:
        rel = abs(cur.length_mean - base.length_mean) / max(base.length_mean, 1e-9)
        if rel > th["length_ratio"]:
            out.append(_change(name, "length_change", base.length_mean, cur.length_mean, rel))
    if base.dtype == "datetime" and cur.dtype == "datetime":
        out.extend(_diff_temporal(name, base, cur, th, enough))
    return out


def _diff_cardinality(
    name: str, base: View, cur: View, th: Mapping[str, Any]
) -> list[dict[str, Any]]:
    b_card, c_card = base.cardinality, cur.cardinality
    if b_card is None or c_card is None:
        return []
    b_rate, c_rate = base.unique_rate, cur.unique_rate
    n_base, n_cur = base.non_null, cur.non_null
    if b_rate is not None and c_rate is not None:
        if (base.unique_like or cur.unique_like) and abs(c_rate - b_rate) > th["uniqueness_rate"]:
            # A unique column has as many distinct values as rows: compare distinct values per
            # row, not counts, so a bigger or smaller extract of the same ids is not a change.
            # A smaller sample is naturally more unique, so a rise is reported unless the
            # current sample is much smaller, and a fall unless it is much larger (a key aside).
            fell = c_rate < b_rate
            if (fell and (n_cur <= 2 * n_base or base.key_like)) or (
                not fell and 2 * n_cur >= n_base
            ):
                return [
                    _change(
                        name,
                        "uniqueness_change",
                        round(b_rate, 6),
                        round(c_rate, 6),
                        abs(c_rate - b_rate),
                    )
                ]
        if base.unique_like and cur.unique_like:
            return []
        if max(n_base, n_cur) > 1.5 * max(min(n_base, n_cur), 1) and (b_rate > 0.5 or c_rate > 0.5):
            return []  # the count of distinct values mostly tracks the sample size here
    if b_card > 0:
        ratio = c_card / b_card
        if ratio > th["cardinality_ratio_max"] or ratio < th["cardinality_ratio_min"]:
            return [_change(name, "cardinality_change", b_card, c_card, _ratio_score(ratio))]
    elif c_card > 0:
        return [_change(name, "cardinality_change", b_card, c_card, 1.0)]
    return []


def _diff_numeric(
    name: str, base: View, cur: View, th: Mapping[str, Any], enough: bool
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    b_std = base.std
    if base.mean is not None and cur.mean is not None:
        # in baseline standard deviations; a model without a spread (a v1 capture) is read in
        # multiples of the mean instead, so a 1% move is not a shift
        scale = b_std if b_std is not None else abs(base.mean)
        shift = abs(cur.mean - base.mean)
        if shift > th["mean_shift_std"] * scale:
            z = shift / scale if scale else math.inf
            score = 1.0 if math.isinf(z) else z / (1.0 + z)
            out.append(_change(name, "mean_shift", base.mean, cur.mean, score))
    if not enough:
        return out
    if b_std is not None and cur.std is not None:
        if b_std > 0:
            ratio = cur.std / b_std
            if ratio > th["std_ratio_max"] or ratio < th["std_ratio_min"]:
                out.append(_change(name, "spread_change", b_std, cur.std, _ratio_score(ratio)))
        elif cur.std > 0:
            out.append(_change(name, "spread_change", b_std, cur.std, 1.0))
    if base.categories is None or cur.categories is None:
        ks = _ks(base, cur)
        if ks is not None:
            if ks > max(th["ks_distance"], _ks_critical(base, cur)):
                out.append(_change(name, "distribution_shift", _summary(base), _summary(cur), ks))
    out.extend(_diff_range(name, base, cur, th))
    b_out, c_out = base.outlier_rate, cur.outlier_rate
    if b_out is not None and c_out is not None:
        p = 0.5 * (b_out + c_out)
        se = math.sqrt(p * (1.0 - p) * (1.0 / base.non_null + 1.0 / cur.non_null))
        if abs(c_out - b_out) > max(th["outlier_rate"], 4.0 * se):
            out.append(_change(name, "outlier_rate_change", b_out, c_out, abs(c_out - b_out)))
    return out


def _diff_range(name: str, base: View, cur: View, th: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A min or max that moved past the baseline's, with the tail quantile moving too (one
    stray value is an outlier, not a range change)."""
    std = base.std
    if not std or None in (base.min, base.max, cur.min, cur.max):
        return []
    assert base.min is not None and base.max is not None
    assert cur.min is not None and cur.max is not None
    median = _quantile_at(base, 0.5)
    moved = 0.0
    for level, lo_side in ((0.99, False), (0.01, True)):
        edge_b, edge_c = (base.min, cur.min) if lo_side else (base.max, cur.max)
        ext = (edge_b - edge_c) if lo_side else (edge_c - edge_b)
        tail_b, tail_c = _quantile_at(base, level), _quantile_at(cur, level)
        # the scale is the spread, or the reach of the baseline's own tail when that is longer:
        # the extremes of a heavy tail differ a lot between two samples of one distribution
        reach = 0.0 if tail_b is None or median is None else abs(tail_b - median)
        if ext <= th["range_margin_std"] * max(std, reach):
            continue
        if tail_b is not None and tail_c is not None:
            tail_ext = (tail_b - tail_c) if lo_side else (tail_c - tail_b)
            if tail_ext <= 0.1 * std:
                continue
        moved = max(moved, ext / std)
    if moved <= 0:
        return []
    return [
        _change(
            name,
            "range_change",
            {"min": base.min, "max": base.max},
            {"min": cur.min, "max": cur.max},
            moved / (1.0 + moved),
        )
    ]


def _diff_categories(
    name: str, base: View, cur: View, th: Mapping[str, Any], enough: bool
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    assert base.categories is not None and cur.categories is not None
    new = [v for v in cur.categories if v not in base.categories]
    if new:
        mass = sum(cur.categories[v] for v in new)
        out.append(
            _change(
                name,
                "new_categorical_values",
                sorted(base.categories),
                sorted(cur.categories),
                mass,
            )
        )
    if enough:
        tvd = _tvd(base.categories, cur.categories)
        noise = _tvd_noise(base.categories, cur.categories, base.non_null, cur.non_null)
        if tvd > max(th["category_tvd"], 2.0 * noise):
            out.append(
                _change(
                    name,
                    "category_shift",
                    *_largest_moves(base.categories, cur.categories),
                    tvd,
                )
            )
    return out


def _diff_temporal(
    name: str, base: View, cur: View, th: Mapping[str, Any], enough: bool
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not enough:
        return out
    parts = [("hour_of_day_change", base.hour, cur.hour)]
    if (base.span_days or 0) >= 14 and (cur.span_days or 0) >= 14:
        parts.append(("day_of_week_change", base.dow, cur.dow))
    for kind, b, c in parts:
        if b is None or c is None or len(b) != len(c):
            continue
        bd, cd = dict(enumerate(b)), dict(enumerate(c))
        tvd = _tvd(bd, cd)
        noise = _tvd_noise(bd, cd, base.non_null, cur.non_null)
        if tvd > max(th["temporal_tvd"], 2.0 * noise):
            out.append(_change(name, kind, [round(v, 4) for v in b], [round(v, 4) for v in c], tvd))
    return out


def _diff_rows(base: TableView, cur: TableView, th: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The table's row count moved past the ratio thresholds. Row counts are exact, so there is
    no sampling noise to allow for. A stream window is not compared (its size is its width), and
    an empty baseline against a filled table scores 1."""
    b_rows, c_rows = base.rows, cur.rows
    if b_rows == c_rows or base.window or cur.window:
        return []
    if b_rows <= 0:
        return [_change(None, "row_count_change", b_rows, c_rows, 1.0)]
    ratio = c_rows / b_rows
    if ratio > th["row_count_ratio_max"] or ratio < th["row_count_ratio_min"]:
        return [_change(None, "row_count_change", b_rows, c_rows, _ratio_score(ratio))]
    return []


def _qualify(table: str, name: str, dataset: bool) -> str:
    return f"{table}.{name}" if dataset else name


def diff_records(
    baseline: Any,
    current: Any,
    policy: Policy,
) -> list[tuple[str | None, str | None, dict[str, Any]]]:
    """``(table, column, record)`` for every change that passes ``policy`` (the table is ``None``
    when neither side is a dataset)."""
    b_tables, b_dataset = tables_of(baseline)
    c_tables, c_dataset = tables_of(current)
    for side, found in (("baseline", b_tables), ("current", c_tables)):
        if not found:
            raise ValueError(f"the {side} holds no table to compare")
    dataset = b_dataset or c_dataset
    changes: list[tuple[str | None, str | None, dict[str, Any]]] = []
    if dataset:
        for tname in b_tables:
            if tname not in c_tables:
                changes.append((tname, None, _change(None, "table_removed", tname, None, 1.0)))
        for tname in c_tables:
            if tname not in b_tables:
                changes.append((tname, None, _change(None, "table_added", None, tname, 1.0)))
        pairs = [(t, b_tables[t], c_tables[t]) for t in b_tables if t in c_tables]
    else:
        ((bn, bt),) = b_tables.items()
        ((_, ct),) = c_tables.items()
        pairs = [(bn, bt, ct)]
    for tname, bt, ct in pairs:
        th_rows = policy.for_column(tname if dataset else None, None)
        for ch in _diff_rows(bt, ct, th_rows):
            changes.append((tname, None, ch))
        for cname in bt.columns:
            if cname not in ct.columns and cname != _WINDOW_TIME:
                ch = _change(
                    _qualify(tname, cname, dataset), "column_removed", "present", "absent", 1.0
                )
                changes.append((tname, cname, ch))
        for cname in ct.columns:
            if cname not in bt.columns and cname != _WINDOW_TIME:
                ch = _change(
                    _qualify(tname, cname, dataset), "column_added", "absent", "present", 1.0
                )
                changes.append((tname, cname, ch))
        for cname, bv in bt.columns.items():
            if cname in ct.columns:
                th = policy.for_column(tname if dataset else None, cname)
                for ch in _diff_column(_qualify(tname, cname, dataset), bv, ct.columns[cname], th):
                    changes.append((tname, cname, ch))
    out: list[tuple[str | None, str | None, dict[str, Any]]] = []
    for owner, col, record in changes:
        scope = owner if dataset else None
        if policy.skips(scope, col):
            continue
        if (
            SEVERITY_RANK[record["severity"]]
            >= SEVERITY_RANK[policy.for_column(scope, col)["min_severity"]]
        ):
            out.append((scope, col, record))
    from .joint import diff_joint

    for tname, bt, ct in pairs:
        out.extend(diff_joint(tname if dataset else None, bt, ct, policy))
    return out


def diff_tables(baseline: Any, current: Any, policy: Policy) -> list[dict[str, Any]]:
    """Compare two profiled things and return the change records that pass ``policy``."""
    return [ch for _, _, ch in diff_records(baseline, current, policy)]
