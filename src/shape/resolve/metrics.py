"""Pairwise precision, recall and F1 of a clustering against known clusters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import cast

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class PairMetrics:
    """Every pair of rows in one cluster is a pair. ``tp``: pairs in a predicted and a true
    cluster; ``fp``: predicted only; ``fn``: true only. With nothing to find (or predict) the
    matching ratio is 1.0."""

    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _pairs(counts: npt.NDArray[np.int64]) -> int:
    return int((counts * (counts - 1) // 2).sum())


def pair_metrics(predicted: npt.NDArray[np.int64], truth: npt.NDArray[np.int64]) -> PairMetrics:
    """Score ``predicted`` cluster labels against ``truth`` labels (one label per row)."""
    p = np.asarray(predicted)
    t = np.asarray(truth)
    if p.shape != t.shape:
        raise ValueError("pair_metrics needs two label arrays of the same length")
    if p.size == 0:
        return PairMetrics(1.0, 1.0, 1.0, 0, 0, 0)
    _, pi = np.unique(p, return_inverse=True)
    _, ti = np.unique(t, return_inverse=True)
    pred_pairs = _pairs(np.bincount(pi).astype(np.int64))
    true_pairs = _pairs(np.bincount(ti).astype(np.int64))
    joint = np.unique(
        pi.astype(np.int64) * (int(cast(Callable[[], np.int64], ti.max)()) + 1) + ti,
        return_counts=True,
    )[1]
    tp = _pairs(joint.astype(np.int64))
    precision = tp / pred_pairs if pred_pairs else 1.0
    recall = tp / true_pairs if true_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return PairMetrics(precision, recall, f1, tp, pred_pairs - tp, true_pairs - tp)
