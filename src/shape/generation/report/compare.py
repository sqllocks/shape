"""Fidelity report: score synthetic tables against reference tables, per column and per table.

The scoring is the one the engine's equivalence standard (plan T-21 clause h) asserts per table,
on Arrow tables instead of DataFrames and without pandas or SciPy. A column earns up to 100
points (10 for a matching kind, 10 for the null rate, 10 for the cardinality, then 40 for a
numeric or datetime column: mean 20, spread 10, Kolmogorov-Smirnov 10; or 40 for a categorical
one: value overlap 20, chi-squared 20). A table scores the mean of its columns and a report the
mean of its tables.

Where it differs from a plain comparison, on purpose:

* a column the reference has and the synthetic data lacks scores 0, and drags its table down
  with it (a comparison over the shared columns only would hide it); a table that is missing
  scores 0 the same way;
* a reference with no tables, no columns or no rows fails: there is nothing to be faithful to;
* datetimes are compared in nanoseconds since the epoch whatever their Arrow unit, so two
  columns of the same instants in different units score as the same.

Columns and tables only the synthetic data has are listed, and do not change the score.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

DEFAULT_THRESHOLD = 85.0
DEFAULT_TABLE_THRESHOLD = 70.0
_STD_FLOOR = 1e-9
_MIN_KS_SAMPLE = 5
_DATE_LIKE_SHARE = 0.95
_ISO_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")

NUMERIC = "numeric"
DATETIME = "datetime"
STRING = "string"
OTHER = "other"


@dataclass(frozen=True, slots=True)
class ColumnFidelity:
    """Metrics and the 0-100 score for one reference column.

    ``present`` is False for a column missing from the synthetic data: it scores 0 and the other
    metrics are not computed."""

    column_name: str
    present: bool
    dtype_match: bool
    null_rate_delta: float
    cardinality_ratio: float
    mean_delta: float | None
    std_ratio: float | None
    ks_statistic: float | None
    chi2_statistic: float | None
    chi2_pvalue: float | None
    value_overlap: float | None
    score: float


@dataclass(frozen=True, slots=True)
class TableFidelity:
    table_name: str
    row_count_real: int
    row_count_synth: int
    columns: dict[str, ColumnFidelity]
    score: float
    missing_columns: tuple[str, ...] = ()
    extra_columns: tuple[str, ...] = ()
    present: bool = True
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Pass marks, on the 0-100 scale. ``min_column`` None leaves single columns unjudged."""

    min_overall: float = DEFAULT_THRESHOLD
    min_table: float = DEFAULT_TABLE_THRESHOLD
    min_column: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "min_overall": self.min_overall,
            "min_table": self.min_table,
            "min_column": self.min_column,
        }


@dataclass(frozen=True, slots=True)
class FidelityReport:
    tables: dict[str, TableFidelity]
    overall_score: float
    missing_tables: tuple[str, ...] = ()
    extra_tables: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    thresholds: Thresholds = field(default_factory=Thresholds)

    def failures(self, thresholds: Thresholds | None = None) -> list[str]:
        """Every reason the report does not pass; empty when it passes."""
        t = thresholds or self.thresholds
        out = list(self.issues)
        for name in self.missing_tables:
            out.append(f"table {name}: missing from the synthetic data")
        for name, tf in self.tables.items():
            out.extend(f"table {name}: {x}" for x in tf.issues)
            out.extend(
                f"table {name}: column {c} is missing from the synthetic data"
                for c in tf.missing_columns
            )
            if tf.present and tf.score < t.min_table:
                out.append(f"table {name}: score {tf.score:.2f} < {t.min_table:g}")
            if t.min_column is not None:
                out.extend(
                    f"table {name}: column {c.column_name} score {c.score:.2f} < {t.min_column:g}"
                    for c in tf.columns.values()
                    if c.present and c.score < t.min_column
                )
        if self.overall_score < t.min_overall:
            out.append(f"overall score {self.overall_score:.2f} < {t.min_overall:g}")
        return out

    def passed(self, thresholds: Thresholds | None = None) -> bool:
        return not self.failures(thresholds)

    def failing_columns(self, threshold: float = DEFAULT_THRESHOLD) -> list[tuple[str, str, float]]:
        """``(table, column, score)`` below ``threshold``, lowest score first."""
        rows = [
            (t, c.column_name, c.score)
            for t, tf in self.tables.items()
            for c in tf.columns.values()
            if c.score < threshold
        ]
        return sorted(rows, key=lambda r: r[2])

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe mapping (non-finite numbers become null) with the verdict included."""
        fails = self.failures()
        return {
            "overall_score": _num(self.overall_score),
            "passed": not fails,
            "failures": fails,
            "thresholds": self.thresholds.to_dict(),
            "missing_tables": list(self.missing_tables),
            "extra_tables": list(self.extra_tables),
            "issues": list(self.issues),
            "tables": {
                name: {
                    "score": _num(tf.score),
                    "present": tf.present,
                    "row_count_real": tf.row_count_real,
                    "row_count_synth": tf.row_count_synth,
                    "missing_columns": list(tf.missing_columns),
                    "extra_columns": list(tf.extra_columns),
                    "issues": list(tf.issues),
                    "columns": {
                        c: {
                            "score": _num(cf.score),
                            "present": cf.present,
                            "dtype_match": cf.dtype_match,
                            "null_rate_delta": _num(cf.null_rate_delta),
                            "cardinality_ratio": _num(cf.cardinality_ratio),
                            "mean_delta": _num(cf.mean_delta),
                            "std_ratio": _num(cf.std_ratio),
                            "ks_statistic": _num(cf.ks_statistic),
                            "chi2_statistic": _num(cf.chi2_statistic),
                            "chi2_pvalue": _num(cf.chi2_pvalue),
                            "value_overlap": _num(cf.value_overlap),
                        }
                        for c, cf in tf.columns.items()
                    },
                }
                for name, tf in self.tables.items()
            },
        }


def _num(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


# --- statistics -------------------------------------------------------------------------------


def gammaincc(a: float, x: float) -> float:
    """The regularised upper incomplete gamma function Q(a, x) (series for x < a + 1, Lentz's
    continued fraction above). Past the iteration cap (huge ``a`` close to ``x``) it falls back to
    the Wilson-Hilferty normal approximation, which is accurate there."""
    if x <= 0.0:
        return 1.0
    if math.isinf(x):
        return 0.0
    log_pref = a * math.log(x) - x - math.lgamma(a)
    cap = 200_000
    if x < a + 1.0:
        term = total = 1.0 / a
        ap = a
        for _ in range(cap):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-16:
                return max(0.0, 1.0 - total * math.exp(log_pref))
    else:
        tiny = 1e-300
        b = x + 1.0 - a
        c = 1.0 / tiny
        d = 1.0 / b
        h = d
        for i in range(1, cap):
            an = -i * (i - a)
            b += 2.0
            d = an * d + b
            d = tiny if abs(d) < tiny else d
            c = b + an / c
            c = tiny if abs(c) < tiny else c
            d = 1.0 / d
            delta = d * c
            h *= delta
            if abs(delta - 1.0) < 1e-16:
                return min(1.0, h * math.exp(log_pref))
    z = ((x / a) ** (1.0 / 3.0) - (1.0 - 1.0 / (9.0 * a))) / math.sqrt(1.0 / (9.0 * a))
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def chi2_sf(statistic: float, df: int) -> float:
    """Survival function of the chi-squared distribution."""
    return gammaincc(df / 2.0, statistic / 2.0)


def ks_statistic(a: np.ndarray[Any, Any], b: np.ndarray[Any, Any]) -> float:
    """Two-sample Kolmogorov-Smirnov statistic: the largest gap between the empirical CDFs."""
    return ks_sorted(np.sort(a), np.sort(b))


def ks_sorted(a: np.ndarray[Any, Any], b: np.ndarray[Any, Any]) -> float:
    """:func:`ks_statistic` of two samples that are already sorted."""
    pooled = np.concatenate([a, b])
    gap = (
        np.searchsorted(a, pooled, side="right") / a.size
        - np.searchsorted(b, pooled, side="right") / b.size
    )
    return float(np.max(np.abs(gap)))


def _mean(x: np.ndarray[Any, Any]) -> float:
    return float(np.mean(x, dtype=np.float64)) if x.size else 0.0


def _std(x: np.ndarray[Any, Any]) -> float:
    """Sample standard deviation (ddof 1); NaN for fewer than two values."""
    if x.size < 2:
        return float("nan")
    return float(np.std(x, ddof=1, dtype=np.float64))


# --- column preparation -----------------------------------------------------------------------


def _chunked(col: Any) -> pa.ChunkedArray:
    if isinstance(col, pa.ChunkedArray):
        return col
    return pa.chunked_array([col])


def _decoded(col: pa.ChunkedArray) -> pa.ChunkedArray:
    if pa.types.is_dictionary(col.type):
        return col.cast(col.type.value_type)
    return col


def _non_null(col: pa.ChunkedArray) -> pa.ChunkedArray:
    """The values that are neither null nor NaN."""
    valid = pc.is_valid(col)
    if pa.types.is_floating(col.type):
        valid = pc.and_(valid, pc.invert(pc.fill_null(pc.is_nan(col), False)))
    return col if pc.all(valid).as_py() is not False else col.filter(valid)


def _numeric_from_strings(nn: pa.ChunkedArray) -> np.ndarray[Any, Any] | None:
    """The values as floats when every one parses as a number, else None."""
    try:
        out = pc.cast(pc.utf8_trim_whitespace(nn.cast(pa.large_string())), pa.float64())
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return None
    arr = out.to_numpy()
    return None if np.isnan(arr).any() else arr


def _datetime_ns(arr: pa.ChunkedArray) -> np.ndarray[Any, Any]:
    """Epoch nanoseconds (int64) of a timestamp or date column; float64 past the int64 range."""
    if pa.types.is_timestamp(arr.type) and arr.type.tz is not None:
        try:
            arr = pc.local_timestamp(arr)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):  # no time-zone database
            arr = arr.cast(pa.timestamp(arr.type.unit))
    try:
        return np.asarray(arr.cast(pa.timestamp("ns")).cast(pa.int64()).to_numpy())
    except pa.ArrowInvalid:
        unit = arr.type.unit if pa.types.is_timestamp(arr.type) else "s"
        scale = {"s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1.0}[unit]
        if pa.types.is_date(arr.type):
            scale = 86400e9 if pa.types.is_date32(arr.type) else 1e6
        return np.asarray(arr.cast(pa.int64()).to_numpy()).astype(np.float64) * scale


def _datetime_from_strings(nn: pa.ChunkedArray) -> np.ndarray[Any, Any] | None:
    """Epoch nanoseconds when at least 95% of the strings are ISO 8601 dates or datetimes."""
    text = nn.cast(pa.large_string())
    try:
        return np.asarray(text.cast(pa.timestamp("ns")).cast(pa.int64()).to_numpy())
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        pass
    parsed = None
    for fmt in _ISO_FORMATS:
        one = pc.strptime(text, format=fmt, unit="ns", error_is_null=True)
        parsed = one if parsed is None else pc.coalesce(parsed, one)
    if parsed is None or pc.sum(pc.is_valid(parsed).cast(pa.int64())).as_py() < (
        _DATE_LIKE_SHARE * len(text)
    ):
        return None
    return np.asarray(parsed.drop_null().cast(pa.int64()).to_numpy())


@dataclass(slots=True)
class _Prepared:
    """A prepared column: everything the score reads. A batch column fills ``non_null`` and
    ``numbers``; a streamed one (``shape.streaming.emit.live``) fills the optional summary fields
    instead, which then take the place of what would be computed from the values.

    ``n_valid`` and ``distinct`` are computed on demand from ``non_null`` when unset, ``key_counts``
    (the result of ``_keys``) likewise, ``mean`` and ``std`` come from ``numbers``, and
    ``n_numbers`` (how many values ``numbers`` stands for) is its size."""

    kind: str
    n: int
    non_null: pa.ChunkedArray
    numbers: np.ndarray[Any, Any] | None = None  # numeric and datetime kinds
    n_valid: int | None = None
    distinct: int | None = None
    key_counts: tuple[pa.Array, np.ndarray[Any, Any]] | None = None
    mean: float | None = None
    std: float | None = None
    n_numbers: int | None = None
    presorted: bool = False  # ``numbers`` is already sorted


def _prepare(col: Any) -> _Prepared:
    arr = _decoded(_chunked(col))
    nn = _non_null(arr)
    t = arr.type
    if pa.types.is_boolean(t):
        return _Prepared(STRING, len(arr), nn)
    if pa.types.is_integer(t) or pa.types.is_floating(t):
        return _Prepared(NUMERIC, len(arr), nn, np.asarray(nn.to_numpy()))
    if pa.types.is_decimal(t):
        return _Prepared(NUMERIC, len(arr), nn, np.asarray(nn.cast(pa.float64()).to_numpy()))
    if pa.types.is_timestamp(t) or pa.types.is_date(t):
        return _Prepared(DATETIME, len(arr), nn, _datetime_ns(nn))
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        if len(nn) > 0:
            nums = _numeric_from_strings(nn)
            if nums is not None:
                return _Prepared(NUMERIC, len(arr), nn, nums)
            dts = _datetime_from_strings(nn)
            if dts is not None:
                return _Prepared(DATETIME, len(arr), nn, dts)
        return _Prepared(STRING, len(arr), nn)
    if pa.types.is_null(t) or pa.types.is_binary(t) or pa.types.is_large_binary(t):
        return _Prepared(STRING, len(arr), nn)
    return _Prepared(OTHER, len(arr), nn)


# --- categorical comparison -------------------------------------------------------------------


def _family(t: pa.DataType) -> str:
    if pa.types.is_boolean(t):
        return "bool"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "str"
    if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "num"
    if pa.types.is_timestamp(t) or pa.types.is_date(t):
        return "dt"
    return f"other:{t}"


def _keys(p: _Prepared) -> tuple[pa.Array, np.ndarray[Any, Any]]:
    """The distinct values of a column as strings tagged with their kind (so a number and the
    same text never count as one category), with how often each occurs."""
    if p.key_counts is not None:
        return p.key_counts
    counts = pc.value_counts(p.non_null.combine_chunks() if p.non_null.num_chunks else pa.array([]))
    return keys_from_counts(counts.field("values"), counts.field("counts").to_numpy())


def keys_from_counts(
    values: pa.Array, counts: np.ndarray[Any, Any]
) -> tuple[pa.Array, np.ndarray[Any, Any]]:
    """``_keys`` for values already counted: ``values`` are the distinct values, ``counts`` how
    often each occurs."""
    if pa.types.is_null(values.type):
        return pa.array([], pa.large_string()), np.zeros(0, dtype=np.float64)
    try:
        text = values.cast(pa.large_string())
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        text = pa.array([str(v) for v in values.to_pylist()], pa.large_string())
    tag = _family(values.type) + ":"
    keys = pc.binary_join_element_wise(
        pa.scalar(tag, pa.large_string()), text, pa.scalar("", pa.large_string())
    )
    return keys, np.asarray(counts, dtype=np.float64)


def _chi2(
    ka: pa.Array, ca: np.ndarray[Any, Any], kb: pa.Array, cb: np.ndarray[Any, Any]
) -> tuple[float | None, float | None]:
    """Chi-squared of the synthetic counts against the reference counts scaled to the same
    total: ``(statistic, p-value)``, or ``(None, None)`` when it cannot be run."""
    cats = pc.unique(pa.concat_arrays([ka, kb]))
    if len(cats) < 2:
        return None, None
    expected_raw = np.zeros(len(cats))
    observed = np.zeros(len(cats))
    expected_raw[np.asarray(pc.index_in(ka, value_set=cats).to_numpy())] = ca
    observed[np.asarray(pc.index_in(kb, value_set=cats).to_numpy())] = cb
    real_total, synth_total = expected_raw.sum(), observed.sum()
    if real_total == 0 or synth_total == 0:
        return None, None
    expected = expected_raw * (synth_total / real_total)
    expected = np.where(expected == 0, 1e-10, expected)
    if not np.allclose(observed.sum(), expected.sum(), rtol=1e-8):
        return None, None
    stat = float((((observed - expected) ** 2) / expected).sum())
    return stat, chi2_sf(stat, len(cats) - 1)


# --- scoring ----------------------------------------------------------------------------------


def _missing_column(name: str) -> ColumnFidelity:
    return ColumnFidelity(name, False, False, 1.0, 0.0, None, None, None, None, None, None, 0.0)


def prepare_column(col: Any) -> _Prepared:
    """A column's summary for :func:`score_prepared` (the public name of ``_prepare``)."""
    return _prepare(col)


def compare_column(name: str, real: Any, synth: Any) -> ColumnFidelity:
    """Score one synthetic column against its reference column."""
    return score_prepared(name, _prepare(real), _prepare(synth))


def _valid(p: _Prepared) -> int:
    return len(p.non_null) if p.n_valid is None else p.n_valid


def _distinct(p: _Prepared) -> int:
    if p.distinct is not None:
        return p.distinct
    return int(pc.count_distinct(p.non_null, mode="only_valid").as_py())


def _ordered(p: _Prepared) -> np.ndarray[Any, Any]:
    assert p.numbers is not None
    return p.numbers if p.presorted else np.sort(p.numbers)


def _sampled(p: _Prepared) -> int:
    assert p.numbers is not None
    return int(p.numbers.size) if p.n_numbers is None else p.n_numbers


def _moments(p: _Prepared) -> tuple[float, float]:
    """``(mean, std)`` of a numeric or datetime column."""
    if p.mean is not None and p.std is not None:
        return p.mean, p.std
    assert p.numbers is not None
    return _mean(p.numbers), _std(p.numbers)


def score_prepared(name: str, r: _Prepared, s: _Prepared) -> ColumnFidelity:
    """Score a prepared synthetic column ``s`` against its prepared reference column ``r``."""
    dtype_match = r.kind == s.kind
    real_null = (r.n - _valid(r)) / r.n if r.n > 0 else 0.0
    synth_null = (s.n - _valid(s)) / s.n if s.n > 0 else 0.0
    null_rate_delta = abs(real_null - synth_null)
    real_card = _distinct(r)
    synth_card = _distinct(s)
    cardinality_ratio = synth_card / max(real_card, 1)

    mean_delta = std_ratio = ks = chi2_stat = chi2_p = overlap = None
    points = 0.0
    max_points = 30.0
    points += 10 if dtype_match else 0
    points += 10 * (1.0 - null_rate_delta)
    points += 10 * max(0.0, 1.0 - abs(1.0 - cardinality_ratio))

    if r.kind == s.kind and r.kind in (NUMERIC, DATETIME):
        assert r.numbers is not None and s.numbers is not None
        real_mean, real_std = _moments(r)
        synth_mean, synth_std = _moments(s)
        mean_delta = abs(real_mean - synth_mean) / max(real_std, _STD_FLOOR)
        std_ratio = synth_std / max(real_std, _STD_FLOOR)
        max_points += 20
        points += 20 * max(0.0, 1.0 - mean_delta)
        max_points += 10
        points += 10 * max(0.0, 1.0 - abs(1.0 - std_ratio))
        max_points += 10
        if _sampled(r) >= _MIN_KS_SAMPLE and _sampled(s) >= _MIN_KS_SAMPLE:
            ks = ks_sorted(_ordered(r), _ordered(s))
            points += 10 * (1.0 - ks)
        else:
            points += 5
    else:
        ka, ca = _keys(r)
        kb, cb = _keys(s)
        max_points += 20
        if len(ka) or len(kb):
            inter = int(pc.sum(pc.is_in(ka, value_set=kb).cast(pa.int64())).as_py() or 0)
            union = len(ka) + len(kb) - inter
            overlap = inter / union if union > 0 else 0.0
            points += 20 * overlap
        else:
            overlap = 1.0
            points += 20
        max_points += 20
        if len(ka) and len(kb):
            chi2_stat, chi2_p = _chi2(ka, ca, kb, cb)
            if chi2_stat is None:
                points += 10
            elif chi2_p is not None and chi2_p > 0.05:
                points += 20
            else:
                points += 20 * (1.0 - min(chi2_stat / 100.0, 1.0))
        else:
            points += 10

    return ColumnFidelity(
        name,
        True,
        dtype_match,
        null_rate_delta,
        float(cardinality_ratio),
        mean_delta,
        std_ratio,
        ks,
        chi2_stat,
        chi2_p,
        overlap,
        float(points / max_points * 100.0),
    )


def compare_table(name: str, real: pa.Table, synth: pa.Table | None) -> TableFidelity:
    """Score one synthetic table (``None``: it is missing) against its reference table."""
    ref_cols = list(real.column_names)
    issues: list[str] = []
    if not ref_cols:
        issues.append("the reference table has no columns")
    if real.num_rows == 0:
        issues.append("the reference table has no rows")
    if synth is None:
        cols = {c: _missing_column(c) for c in ref_cols}
        return TableFidelity(
            name, real.num_rows, 0, cols, 0.0, tuple(ref_cols), (), False, tuple(issues)
        )
    have = set(synth.column_names)
    columns: dict[str, ColumnFidelity] = {}
    for c in ref_cols:
        columns[c] = (
            compare_column(c, real.column(c), synth.column(c)) if c in have else _missing_column(c)
        )
    score = float(np.mean([c.score for c in columns.values()])) if columns else 0.0
    if issues:  # an empty reference is not a perfect one, whatever the columns score
        score = 0.0
    missing = tuple(c for c in ref_cols if c not in have)
    ref_set = set(ref_cols)
    extra = tuple(c for c in synth.column_names if c not in ref_set)
    return TableFidelity(
        name, real.num_rows, synth.num_rows, columns, score, missing, extra, True, tuple(issues)
    )


def compare_tables(
    real: Mapping[str, pa.Table],
    synthetic: Mapping[str, pa.Table],
    thresholds: Thresholds | None = None,
) -> FidelityReport:
    """Score ``synthetic`` against ``real``: per column, per table, and overall.

    A reference table that is missing from ``synthetic`` scores 0. The overall score is the mean
    over the reference's tables; an empty reference scores 0 and fails."""
    tables = {n: compare_table(n, t, synthetic.get(n)) for n, t in sorted(real.items())}
    overall = float(np.mean([t.score for t in tables.values()])) if tables else 0.0
    issues = () if tables else ("the reference has no tables",)
    return FidelityReport(
        tables,
        overall,
        tuple(n for n in tables if n not in synthetic),
        tuple(n for n in sorted(synthetic) if n not in real),
        issues,
        thresholds or Thresholds(),
    )
