"""Clustering matched pairs into entities.

Both methods return one label per row: the smallest row index of the row's cluster, so labels are
stable and a row that matched nothing is its own cluster. ``connected_components`` follows every
match (a chain A-B, B-C puts A, B and C together); ``center_clusters`` is the greedy center
algorithm (best score first; a cluster grows only around its center), which does not chain.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

METHODS = ("connected", "center")


def connected_components(n: int, pairs: npt.NDArray[np.int64]) -> npt.NDArray[np.int64]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in np.asarray(pairs, dtype=np.int64).reshape(-1, 2).tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            parent[hi] = lo
    return np.fromiter((find(x) for x in range(n)), dtype=np.int64, count=n)


def center_clusters(
    n: int, pairs: npt.NDArray[np.int64], scores: npt.NDArray[np.float64]
) -> npt.NDArray[np.int64]:
    pairs = np.asarray(pairs, dtype=np.int64).reshape(-1, 2)
    lo, hi = pairs.min(axis=1), pairs.max(axis=1)
    order = np.lexsort((hi, lo, -np.asarray(scores, dtype=np.float64)))
    owner = np.full(n, -1, dtype=np.int64)  # the center a row belongs to
    is_center = np.zeros(n, dtype=bool)
    for k in order.tolist():
        a, b = int(lo[k]), int(hi[k])
        if owner[a] < 0 and owner[b] < 0:
            owner[a] = a
            owner[b] = a
            is_center[a] = True
        elif owner[a] < 0 and is_center[b]:
            owner[a] = b
        elif owner[b] < 0 and is_center[a]:
            owner[b] = a
    owner = np.where(owner < 0, np.arange(n), owner)
    # relabel by the smallest member of each cluster
    smallest = np.full(n, n, dtype=np.int64)
    np.minimum.at(smallest, owner, np.arange(n))
    return smallest[owner].astype(np.int64)
