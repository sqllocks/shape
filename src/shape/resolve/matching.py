"""Pairwise matching: a weighted mean of per-field similarities.

String fields use the measures of :mod:`shape.resolve.distances`; ``numeric`` and ``date`` fields
use a tolerance: the similarity falls linearly from 1 (equal) to 0 (a difference of ``tolerance``
or more). A numeric ``tolerance`` is absolute, or a share of the larger magnitude with
``relative=True``; a date tolerance is in days. With no tolerance only equal values match. A field
that is missing on either side is left out of the mean (its weight does not count); a pair with no
comparable field scores 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.resolve.distances import STRING_MEASURES, normalize_text, phonetic_key, similarity_pairs

KINDS = ("exact", "phonetic", "numeric", "date", *STRING_MEASURES)
_US_PER_DAY = 86_400_000_000.0


@dataclass(frozen=True)
class FieldMatch:
    """How one column contributes to a pair's score."""

    column: str
    kind: str = "text"
    weight: float = 1.0
    tolerance: float | None = None
    relative: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown match kind {self.kind!r}; choose one of {', '.join(KINDS)}")
        if not self.weight > 0:
            raise ValueError(f"the weight of {self.column} must be above 0")
        if self.tolerance is not None and self.tolerance < 0:
            raise ValueError(f"the tolerance of {self.column} cannot be negative")

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"column": self.column, "kind": self.kind, "weight": self.weight}
        if self.tolerance is not None:
            out["tolerance"] = self.tolerance
        if self.relative:
            out["relative"] = True
        return out


@dataclass(frozen=True)
class MatchResult:
    """Scores of the pairs, and the similarity of each field (NaN where a value is missing)."""

    scores: npt.NDArray[np.float64]
    similarities: npt.NDArray[np.float64]
    fields: tuple[FieldMatch, ...]


def _numbers(col: pa.ChunkedArray, f: FieldMatch) -> npt.NDArray[np.float64]:
    t = col.type
    if not (pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t)):
        raise ValueError(f"match field {f.column!r} is numeric but the column is {t}")
    return np.asarray(col.cast(pa.float64()).to_numpy(zero_copy_only=False), dtype=np.float64)


def _days(col: pa.ChunkedArray, f: FieldMatch) -> npt.NDArray[np.float64]:
    t = col.type
    if not (pa.types.is_date(t) or pa.types.is_timestamp(t)):
        raise ValueError(f"match field {f.column!r} is a date but the column is {t}")
    us = col.cast(pa.timestamp("us")).cast(pa.int64()).to_numpy(zero_copy_only=False)
    out = np.asarray(us, dtype=np.float64) / _US_PER_DAY
    valid = np.asarray(col.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    out[~valid] = np.nan
    return out


def _tolerance_similarity(
    a: npt.NDArray[np.float64], b: npt.NDArray[np.float64], tol: float, relative: bool
) -> npt.NDArray[np.float64]:
    diff = np.abs(a - b)
    if relative:
        denom = np.maximum(np.abs(a), np.abs(b))
        diff = np.divide(diff, denom, out=np.zeros_like(diff), where=denom > 0)
    if tol <= 0:
        return np.asarray(diff == 0, dtype=np.float64)
    return np.asarray(np.clip(1.0 - diff / tol, 0.0, 1.0), dtype=np.float64)


def _present(v: object) -> bool:
    return v is not None and (not isinstance(v, str) or v.strip() != "")


def _field_similarity(
    table: pa.Table, i: npt.NDArray[np.int64], j: npt.NDArray[np.int64], f: FieldMatch
) -> npt.NDArray[np.float64]:
    if f.column not in table.column_names:
        raise ValueError(f"match field: no column {f.column!r}")
    col = table.column(f.column)
    sim = np.full(len(i), np.nan, dtype=np.float64)
    if f.kind in ("numeric", "date"):
        vals = _numbers(col, f) if f.kind == "numeric" else _days(col, f)
        a, b = vals[i], vals[j]
        ok = ~(np.isnan(a) | np.isnan(b))
        sim[ok] = _tolerance_similarity(
            a[ok], b[ok], float(f.tolerance or 0.0), f.relative and f.kind == "numeric"
        )
        return sim
    vals_py = col.to_pylist()
    ok = np.array(
        [
            _present(vals_py[x]) and _present(vals_py[y])
            for x, y in zip(i.tolist(), j.tolist(), strict=True)
        ],
        dtype=bool,
    )
    if not ok.any():
        return sim
    left = [vals_py[x] for x in i[ok].tolist()]
    right = [vals_py[y] for y in j[ok].tolist()]
    if f.kind == "exact":
        same = [
            (normalize_text(x) == normalize_text(y))
            if isinstance(x, str) and isinstance(y, str)
            else x == y
            for x, y in zip(left, right, strict=True)
        ]
        sim[ok] = np.array(same, dtype=np.float64)
    elif f.kind == "phonetic":
        sim[ok] = np.array(
            [
                float(phonetic_key(x) == phonetic_key(y) != "")
                for x, y in zip(left, right, strict=True)
            ]
        )
    else:
        sim[ok] = similarity_pairs(f.kind, [str(x) for x in left], [str(y) for y in right])
    return sim


def score_pairs(
    table: pa.Table, pairs: npt.NDArray[np.int64], fields: list[FieldMatch]
) -> MatchResult:
    """Score each ``(i, j)`` row of ``pairs`` against the ``fields``."""
    if not fields:
        raise ValueError("matching needs at least one field")
    pairs = np.asarray(pairs, dtype=np.int64).reshape(-1, 2)
    i, j = pairs[:, 0], pairs[:, 1]
    sims = (
        np.stack([_field_similarity(table, i, j, f) for f in fields], axis=1)
        if len(pairs)
        else np.empty((0, len(fields)))
    )
    if not len(pairs):
        for f in fields:  # still validate the columns
            _field_similarity(table, i, j, f)
    weights = np.array([f.weight for f in fields], dtype=np.float64)
    present = ~np.isnan(sims)
    num = np.where(present, sims, 0.0) @ weights
    den = present.astype(np.float64) @ weights
    scores = np.divide(num, den, out=np.zeros(len(pairs)), where=den > 0)
    return MatchResult(scores, sims, tuple(fields))
