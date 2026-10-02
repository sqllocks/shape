"""Hierarchical sampling of reference records: state, county, city, ZIP, coordinates (#47).

A reference dataset of records (a ZIP file of places) is read as a tree whose levels are named
fields, top first (``("state", "county", "city", "zip")``). A row is drawn one level at a time:
a top value, then a child of it, then a child of that, down to a leaf, and the row takes the
fields of one record under that leaf. Every field of the row therefore comes from the same
record, so a ZIP is always inside its city and a city inside its state, however the levels are
weighted.

Weights: ``weighting="records"`` (the default) lets every record be equally likely, as a uniform
draw of a record would; ``"uniform"`` lets every child of a node be equally likely (each city of
a state is as likely as any other, however many ZIPs it has). ``top_weights`` replaces the top
level's weights (a profile's state shares), by value; the lower levels follow ``weighting``.

Draws are row addressed (``RowStream``): the record of row ``r`` depends only on the seed, the
stream key and ``r``, never on the chunk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .rng import RowStream

Ints = npt.NDArray[np.int64]
Floats = npt.NDArray[np.float64]

WEIGHTINGS = ("records", "uniform")


def _codes(values: Any) -> tuple[Ints, list[Any]]:
    """Dense codes of a column's values (a missing value is the value ``None``) and the labels."""
    arr = values if isinstance(values, (pa.Array, pa.ChunkedArray)) else pa.array(list(values))
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    d = pc.dictionary_encode(arr, null_encoding="encode")
    codes = d.indices.to_numpy(zero_copy_only=False).astype(np.int64)
    return codes, d.dictionary.to_pylist()


class HierarchyTree:
    """The levels of a dataset of records as a tree of nodes, with one weight per node."""

    def __init__(self, columns: Mapping[str, Any], levels: Sequence[str]) -> None:
        if not levels:
            raise ValueError("a hierarchy needs at least one level")
        if len(set(levels)) != len(levels):
            raise ValueError("the levels of a hierarchy are different fields")
        missing = [lv for lv in levels if lv not in columns]
        if missing:
            raise ValueError(f"the dataset has no field {missing[0]!r}; fields: {list(columns)}")
        self.levels = tuple(levels)
        self.n_records = len(columns[levels[0]])
        if self.n_records == 0:
            raise ValueError("an empty dataset has no hierarchy")
        node_of: list[Ints] = []  # per level: the node id of each record
        labels: list[list[Any]] = []  # per level: the label of each node
        parent: list[Ints] = []  # per level: the parent node of each node
        prev = np.zeros(self.n_records, dtype=np.int64)
        for depth, name in enumerate(levels):
            codes, names = _codes(columns[name])
            key = prev * (len(names) + 1) + codes
            uniq, inverse = np.unique(key, return_inverse=True)
            node_of.append(inverse.astype(np.int64))
            labels.append([names[int(u % (len(names) + 1))] for u in uniq])
            parent.append((uniq // (len(names) + 1)).astype(np.int64))
            prev = node_of[-1]
            del depth
        self.labels = labels
        self.parent = parent
        self.node_of = node_of
        self.record_count = [np.bincount(n, minlength=len(lab)) for n, lab in zip(node_of, labels)]
        # nodes are sorted by (parent, value): the children of a node are one contiguous range
        self.child_start: list[Ints] = [np.zeros(1, dtype=np.int64)]
        self.child_count: list[Ints] = [np.array([len(labels[0])], dtype=np.int64)]
        for depth in range(1, len(levels)):
            counts = np.bincount(parent[depth], minlength=len(labels[depth - 1]))
            starts = np.cumsum(counts) - counts
            self.child_start.append(starts.astype(np.int64))
            self.child_count.append(counts.astype(np.int64))
        order = np.argsort(node_of[-1], kind="stable")
        counts = self.record_count[-1]
        self.leaf_records = order.astype(np.int64)
        self.leaf_start = (np.cumsum(counts) - counts).astype(np.int64)

    def top_index(self) -> dict[Any, int]:
        return {lab: i for i, lab in enumerate(self.labels[0])}

    def _weights(self, depth: int, weighting: str, top_weights: Mapping[Any, float] | None) -> Floats:
        if depth == 0 and top_weights is not None:
            index = self.top_index()
            w = np.zeros(len(self.labels[0]))
            for value, weight in top_weights.items():
                i = index.get(value, index.get(str(value)))
                if i is not None:
                    w[i] = max(float(weight), 0.0)
            if w.sum() <= 0:
                raise ValueError(
                    f"top_weights name no value of {self.levels[0]!r}; values start "
                    f"{[str(x) for x in self.labels[0][:5]]}"
                )
            return w
        if weighting == "records":
            return self.record_count[depth].astype(np.float64)
        return np.ones(len(self.labels[depth]))

    def draw(
        self,
        streams: Sequence[RowStream],
        row_start: int,
        n_rows: int,
        *,
        weighting: str = "records",
        top_weights: Mapping[Any, float] | None = None,
    ) -> Ints:
        """The record index of each row ``row_start .. row_start + n_rows - 1``. ``streams`` are
        ``len(levels) + 1`` independent streams: one per level and one for the choice among the
        records of a leaf."""
        if weighting not in WEIGHTINGS:
            raise ValueError(f"weighting must be one of {WEIGHTINGS}")
        if len(streams) != len(self.levels) + 1:
            raise ValueError("one stream per level and one for the leaf record are needed")
        node = np.zeros(n_rows, dtype=np.int64)
        for depth in range(len(self.levels)):
            u = streams[depth].uniform(row_start, n_rows)
            w = self._weights(depth, weighting, top_weights)
            cum = np.cumsum(w)
            if depth == 0:
                node = np.minimum(np.searchsorted(cum, u * cum[-1], side="right"), len(w) - 1)
                continue
            start = self.child_start[depth][node]
            count = self.child_count[depth][node]
            end = start + count
            before = np.where(start > 0, cum[np.maximum(start - 1, 0)], 0.0)
            total = cum[end - 1] - before
            picked = np.searchsorted(cum, before + u * total, side="right")
            node = np.clip(picked, start, end - 1)
        leaf_count = self.record_count[-1][node]
        u = streams[-1].uniform(row_start, n_rows)
        within = np.minimum((u * leaf_count).astype(np.int64), leaf_count - 1)
        return self.leaf_records[self.leaf_start[node] + within]


class HierarchicalSampler:
    """Draw rows of a dataset of records level by level (see the module docstring)."""

    def __init__(
        self,
        columns: Mapping[str, Any],
        levels: Sequence[str],
        *,
        weighting: str = "records",
        top_weights: Mapping[Any, float] | None = None,
    ) -> None:
        if weighting not in WEIGHTINGS:
            raise ValueError(f"weighting must be one of {WEIGHTINGS}")
        self.columns = {
            k: (v.combine_chunks() if isinstance(v, pa.ChunkedArray) else pa.array(list(v)))
            if not isinstance(v, pa.Array)
            else v
            for k, v in columns.items()
        }
        self.tree = HierarchyTree(self.columns, levels)
        self.weighting = weighting
        self.top_weights = dict(top_weights) if top_weights is not None else None

    @property
    def levels(self) -> tuple[str, ...]:
        return self.tree.levels

    def streams(self, seed: int, table: str, key: str) -> list[RowStream]:
        return [
            RowStream(seed, table, key, f"hierarchy/{i}") for i in range(len(self.levels) + 1)
        ]

    def records(
        self, n: int, seed: int = 0, *, start: int = 0, table: str = "", key: str = ""
    ) -> Ints:
        """The dataset row of each of ``n`` rows."""
        return self.tree.draw(
            self.streams(seed, table, key),
            start,
            n,
            weighting=self.weighting,
            top_weights=self.top_weights,
        )

    def sample(
        self,
        n: int,
        seed: int = 0,
        *,
        fields: Sequence[str] | None = None,
        start: int = 0,
        table: str = "",
        key: str = "",
    ) -> dict[str, pa.Array]:
        """``n`` rows: each takes ``fields`` (default: every field) of one record."""
        idx = pa.array(self.records(n, seed, start=start, table=table, key=key))
        names = list(fields) if fields is not None else list(self.columns)
        return {f: pc.take(self.columns[f], idx) for f in names}


def hierarchy_violations(
    rows: Mapping[str, Any], reference: Mapping[str, Any], levels: Sequence[str]
) -> int:
    """The number of ``rows`` whose values at ``levels`` are not one record of ``reference``
    (a ZIP outside its city, a city outside its state)."""
    ref = {
        tuple(vals)
        for vals in zip(*(reference[lv].to_pylist() for lv in levels), strict=True)
    }
    got = zip(*(rows[lv].to_pylist() for lv in levels), strict=True)
    return sum(tuple(v) not in ref for v in got)
