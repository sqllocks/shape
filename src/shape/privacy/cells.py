"""Minimum-cohort enforcement: small-cell suppression for released evidence.

A *cell* is one category of a value count or enum, one bin of a histogram, or one top-value
entry: a count of rows. A released cell must either be absent (zero) or stand for at least ``k``
rows. This module turns that rule into code for every surface that carries cells:

* category weights (``suppress_weights``): cells below ``k`` fold into one ``__OTHER__``
  bucket; if that bucket is itself below ``k``, the smallest surviving cells join it until it
  reaches ``k`` (complementary suppression), and when the whole column has fewer than ``k``
  rows nothing is released;
* histograms (``suppress_bins``): a bin below ``k`` is zeroed, and proportion histograms are
  renormalized so they still sum to what they did; with no bin left the histogram is dropped;
* ``suppress_column_cells``: all of the above on a column dict, for ``release_for`` and
  ``suppress_shape``. A value list that carries no counts cannot be checked, so it is removed.

Evidence that stores proportions (rounded to six places) is converted to counts with a lower
bound: ``ceil(p * base - 5e-7 * base)``. A cell is released only when that lower bound reaches
``k``, so the true count is at least ``k`` however the proportion was rounded. For a base of up
to one million rows the bound equals the exact count.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

OTHER_BUCKET = "__OTHER__"
PROPORTION_ROUNDING = 5e-7  # proportions are stored rounded to six decimal places

_COUNT_LIST_KEYS = ("histogram", "hour_histogram", "dow_histogram")
_COUNT_MAP_KEYS = ("value_counts_ext", "enum_values")


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def count_lower_bound(proportion: float, base: int) -> int:
    """A count the cell is certain to reach, from a proportion of ``base`` rows."""
    return max(0, math.ceil(proportion * base - PROPORTION_ROUNDING * base - 1e-9))


def non_null_base(
    row_count: int | None, null_count: int | None = None, null_rate: float = 0.0
) -> int:
    """Rows a column's proportions are taken over: its non-null rows."""
    if not row_count or row_count <= 0:
        return 0
    if null_count is not None:
        return max(0, int(row_count) - int(null_count))
    return max(0, int(row_count) - int(round(float(null_rate) * int(row_count))))


def suppress_weights(
    weights: Mapping[str, float], k: int, base: int | None
) -> tuple[dict[str, float], int]:
    """Fold every category below ``k`` rows into ``__OTHER__``.

    ``weights`` maps category to a proportion of ``base`` non-null rows. Returns
    ``(released weights, categories folded)``. The ``__OTHER__`` bucket is released only when it
    stands for at least ``k`` rows; smaller buckets absorb the smallest surviving category until
    they do. With no usable ``base``, or ``k <= 1``, nothing can be judged and the weights pass
    through. When the whole column has fewer than ``k`` rows the result is empty. The input is
    never modified.
    """
    if not weights:
        return {}, 0
    if not base or base <= 0 or k <= 1:
        return dict(weights), 0
    surviving: dict[str, float] = {}
    other_w = 0.0
    other_n = 0
    folded = 0
    for key, w in weights.items():
        w = float(w)
        if key == OTHER_BUCKET:
            other_w += w
            other_n += count_lower_bound(w, base)
        elif count_lower_bound(w, base) < k:
            other_w += w
            other_n += count_lower_bound(w, base)
            folded += 1
        else:
            surviving[key] = w
    while other_w > 0.0 and other_n < k and surviving:
        smallest = min(surviving, key=lambda c: surviving[c])
        w = surviving.pop(smallest)
        other_w += w
        other_n += count_lower_bound(w, base)
        folded += 1
    if other_w > 0.0 and other_n < k:
        return {}, folded
    if folded > 0 or other_w > 0.0:
        surviving[OTHER_BUCKET] = other_w
    return surviving, folded


def suppress_counts(counts: Mapping[str, int], k: int) -> tuple[dict[str, int], int]:
    """``suppress_weights`` for integer row counts. Returns ``(released counts, folded)``."""
    if not counts:
        return {}, 0
    if k <= 1:
        return dict(counts), 0
    surviving: dict[str, int] = {}
    other = 0
    folded = 0
    for key, n in counts.items():
        if key == OTHER_BUCKET:
            other += n
        elif n < k:
            other += n
            folded += 1
        else:
            surviving[key] = n
    while 0 < other < k and surviving:
        smallest = min(surviving, key=lambda c: surviving[c])
        other += surviving.pop(smallest)
        folded += 1
    if 0 < other < k:
        return {}, folded
    if folded > 0 or other > 0:
        surviving[OTHER_BUCKET] = other
    return surviving, folded


def suppress_bins(
    bins: Sequence[float], k: int, base: int | None, *, proportions: bool = True
) -> tuple[list[float] | None, int]:
    """Zero every histogram bin that stands for fewer than ``k`` rows.

    Proportion histograms (``proportions=True``, over ``base`` rows) are renormalized so the
    survivors keep the total they had; count histograms keep their counts. Returns
    ``(released bins, bins suppressed)``, or ``(None, n)`` when no bin survives. With no usable
    ``base`` (proportions) or ``k <= 1`` the bins pass through.
    """
    if k <= 1 or (proportions and (not base or base <= 0)):
        return list(bins), 0
    keep: list[bool] = []
    for b in bins:
        if b <= 0:
            keep.append(True)
        elif proportions:
            keep.append(count_lower_bound(float(b), int(base or 0)) >= k)
        else:
            keep.append(b >= k)
    dropped = keep.count(False)
    if dropped == 0:
        return list(bins), 0
    survivors = [b for b, ok in zip(bins, keep, strict=True) if ok and b > 0]
    if not survivors:
        return None, dropped
    out = [b if ok else 0 for b, ok in zip(bins, keep, strict=True)]
    if proportions:
        total = sum(bins)
        scale = total / sum(out)
        out = [round(b * scale, 6) for b in out]
    return out, dropped


def _cells_in_map(values: Mapping[Any, Any]) -> str:
    """``counts`` when every value is an integer, ``proportions`` when all are numbers."""
    if all(_is_int(v) for v in values.values()):
        return "counts"
    if all(_is_num(v) for v in values.values()):
        return "proportions"
    return "unknown"


def suppress_column_cells(
    column: Mapping[str, Any], k: int, base: int
) -> tuple[dict[str, Any], int, list[str]]:
    """Apply the minimum cell count to every cell surface of a column dict.

    ``base`` is the column's non-null row count, used for evidence that stores proportions.
    Returns ``(column, cells suppressed, surfaces removed outright)``. A value list that carries
    no counts, or a cell structure of an unknown shape, cannot be checked and is removed.
    """
    out = dict(column)
    cells = 0
    removed: list[str] = []

    top = out.get("topk")
    if isinstance(top, list):
        keep = [i for i in top if isinstance(i, list | tuple) and len(i) > 1 and _is_num(i[1])]
        keep = [i for i in keep if i[1] >= k]
        cells += len(top) - len(keep)
        out["topk"] = keep

    for key in _COUNT_MAP_KEYS:
        v = out.get(key)
        if v is None:
            continue
        mode = _cells_in_map(v) if isinstance(v, Mapping) else "unknown"
        if mode == "counts":
            out[key], n = suppress_counts(v, k)
            cells += n
        elif mode == "proportions":
            out[key], n = suppress_weights(v, k, base)
            cells += n
        else:
            del out[key]
            removed.append(key)

    for key in _COUNT_LIST_KEYS:
        v = out.get(key)
        if v is None:
            continue
        if not (isinstance(v, list) and all(_is_num(b) for b in v)):
            del out[key]
            removed.append(key)
            continue
        bins, n = suppress_bins(v, k, base, proportions=not all(_is_int(b) for b in v))
        cells += n
        if bins is None:
            del out[key]
            removed.append(key)
        else:
            out[key] = bins

    temporal = out.get("temporal_histogram")
    if isinstance(temporal, Mapping):
        t = dict(temporal)
        for part in ("year_weights", "month_weights"):
            w = t.get(part)
            if isinstance(w, list) and all(_is_num(b) for b in w):
                bins, n = suppress_bins(w, k, base)
                cells += n
                if bins is None:
                    del t[part]
                else:
                    t[part] = bins
        out["temporal_histogram"] = t
    elif temporal is not None:
        del out["temporal_histogram"]
        removed.append("temporal_histogram")

    return out, cells, removed
