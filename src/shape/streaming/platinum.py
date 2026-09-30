"""Bounded, mergeable streaming evidence for Platinum-level relationships."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.hashing import hash_column, hash_value


@dataclass
class MissingnessPairEvidence:
    both_present: int = 0
    left_missing: int = 0
    right_missing: int = 0
    both_missing: int = 0

    def update(self, a, b):
        am = a is None
        bm = b is None
        if am and bm:
            self.both_missing += 1
        elif am:
            self.left_missing += 1
        elif bm:
            self.right_missing += 1
        else:
            self.both_present += 1

    def merge(self, o):
        for k in ("both_present", "left_missing", "right_missing", "both_missing"):
            setattr(self, k, getattr(self, k) + getattr(o, k))
        return self

    def summary(self):
        return {
            k: getattr(self, k)
            for k in ("both_present", "left_missing", "right_missing", "both_missing")
        }


@dataclass
class CovarianceEvidence:
    n: int = 0
    mx: float = 0.0
    my: float = 0.0
    c: float = 0.0
    m2x: float = 0.0
    m2y: float = 0.0

    def update(self, x, y):
        if x is None or y is None:
            return
        x = float(x)
        y = float(y)
        self.n += 1
        dx = x - self.mx
        self.mx += dx / self.n
        dy = y - self.my
        self.my += dy / self.n
        self.c += dx * (y - self.my)
        self.m2x += dx * (x - self.mx)
        self.m2y += dy * (y - self.my)

    def merge(self, o):
        if not o.n:
            return self
        if not self.n:
            self.n, self.mx, self.my, self.c, self.m2x, self.m2y = (
                o.n,
                o.mx,
                o.my,
                o.c,
                o.m2x,
                o.m2y,
            )
            return self
        n = self.n + o.n
        dx = o.mx - self.mx
        dy = o.my - self.my
        self.c += o.c + dx * dy * self.n * o.n / n
        self.m2x += o.m2x + dx * dx * self.n * o.n / n
        self.m2y += o.m2y + dy * dy * self.n * o.n / n
        self.mx = (self.mx * self.n + o.mx * o.n) / n
        self.my = (self.my * self.n + o.my * o.n) / n
        self.n = n
        return self

    def summary(self):
        den = math.sqrt(self.m2x * self.m2y)
        return {"n": self.n, "correlation": None if not den else self.c / den}


@dataclass
class TemporalEvidence:
    n: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    out_of_order: int = 0
    gaps: object = field(default=None)

    def __post_init__(self):
        if self.gaps is None:
            from shape.profile.sketches import KLL

            self.gaps = KLL()

    def update(self, ts):
        if ts is None:
            return
        x = float(ts)
        if self.last_ts is not None:
            if x < self.last_ts:
                self.out_of_order += 1
            self.gaps.update(abs(x - self.last_ts))
        if self.first_ts is None:
            self.first_ts = x
        self.last_ts = x
        self.n += 1

    def summary(self):
        return {
            "n": self.n,
            "first": self.first_ts,
            "last": self.last_ts,
            "out_of_order": self.out_of_order,
            "median_gap": self.gaps.quantile(0.5),
        }


@dataclass
class RelationalEvidence:
    checked: int = 0
    orphans: int = 0

    def update(self, fk, parent_exists: bool):
        if fk is None:
            return
        self.checked += 1
        self.orphans += int(not parent_exists)

    def merge(self, o):
        self.checked += o.checked
        self.orphans += o.orphans
        return self

    def summary(self):
        return {
            "checked": self.checked,
            "orphans": self.orphans,
            "orphan_rate": 0 if not self.checked else self.orphans / self.checked,
        }


@dataclass
class GeoGridEvidence:
    """Fixed-resolution bounded geographic distribution."""

    resolution: float = 0.1
    max_cells: int = 4096
    cells: Counter = field(default_factory=Counter)
    overflow: int = 0

    def update(self, lat, lon):
        if lat is None or lon is None:
            return
        key = (round(float(lat) / self.resolution), round(float(lon) / self.resolution))
        if key in self.cells or len(self.cells) < self.max_cells:
            self.cells[key] += 1
        else:
            self.overflow += 1

    def merge(self, o):
        for k, v in o.cells.items():
            if k in self.cells or len(self.cells) < self.max_cells:
                self.cells[k] += v
            else:
                self.overflow += v
        self.overflow += o.overflow
        return self

    def summary(self):
        return {
            "cells": len(self.cells),
            "overflow": self.overflow,
            "top_cells": self.cells.most_common(20),
        }


@dataclass
class HashedDependencyEvidence:
    """Fixed-memory contingency sketch for categorical/nonlinear dependency evidence."""

    bins: int = 64
    table: dict = field(default_factory=dict)
    n: int = 0

    def _bin(self, v):
        """Bin by the canonical XXH3 hash (T-13): identical in every process (S1)."""
        h = hash_value(v)
        return None if h is None else h % self.bins

    def update(self, a, b):
        ba, bb = self._bin(a), self._bin(b)
        if ba is None or bb is None:  # null and NaN are excluded
            return
        k = (ba, bb)
        self.table[k] = self.table.get(k, 0) + 1
        self.n += 1

    def merge(self, o):
        if self.bins != o.bins:
            raise ValueError("incompatible dependency sketches")
        for k, v in o.table.items():
            self.table[k] = self.table.get(k, 0) + v
        self.n += o.n
        return self

    def summary(self):
        if not self.n:
            return {"n": 0, "mutual_information": None}
        import math

        ra = {}
        cb = {}
        for (i, j), v in self.table.items():
            ra[i] = ra.get(i, 0) + v
            cb[j] = cb.get(j, 0) + v
        mi = 0.0
        for (i, j), v in self.table.items():
            p = v / self.n
            mi += p * math.log(p / ((ra[i] / self.n) * (cb[j] / self.n)))
        return {"n": self.n, "mutual_information": mi, "bins": self.bins}


def update_missingness_batch(state, a, b):
    import numpy as np

    aa = np.asarray(a)
    bb = np.asarray(b)

    def miss(x):
        if x.dtype.kind == "f":
            return np.isnan(x)
        if x.dtype.kind in "iub":
            return np.zeros(x.shape, dtype=bool)
        return np.equal(x, None)

    am = miss(aa)
    bm = miss(bb)
    state.both_missing += int(np.count_nonzero(am & bm))
    state.left_missing += int(np.count_nonzero(am & ~bm))
    state.right_missing += int(np.count_nonzero(~am & bm))
    state.both_present += int(np.count_nonzero(~am & ~bm))
    return state


def covariance_batch(x, y):
    import numpy as np

    a = np.asarray(x, dtype=np.float64)
    b = np.asarray(y, dtype=np.float64)
    mask = np.isfinite(a) & np.isfinite(b)
    a = a[mask]
    b = b[mask]
    s = CovarianceEvidence()
    if a.size:
        s.n = int(a.size)
        s.mx = float(a.mean())
        s.my = float(b.mean())
        da = a - s.mx
        db = b - s.my
        s.c = float(np.dot(da, db))
        s.m2x = float(np.dot(da, da))
        s.m2y = float(np.dot(db, db))
    return s


def relational_batch(fk, parent_count):
    import numpy as np

    a = np.asarray(fk)
    valid = (a >= 0) & (a < parent_count)
    return RelationalEvidence(int(a.size), int(a.size - np.count_nonzero(valid)))


def geo_grid_batch(lat, lon, resolution=0.1, max_cells=4096):
    import numpy as np

    la = np.rint(np.asarray(lat, dtype=np.float64) / resolution).astype(np.int64)
    lo = np.rint(np.asarray(lon, dtype=np.float64) / resolution).astype(np.int64)
    # Pack signed 32-bit grid coordinates into one int64 so np.unique stays on its fast 1-D path.
    ula = la.astype(np.uint64) & np.uint64(0xFFFFFFFF)
    ulo = lo.astype(np.uint64) & np.uint64(0xFFFFFFFF)
    packed = (ula << np.uint64(32)) | ulo
    u, c = np.unique(packed, return_counts=True)
    s = GeoGridEvidence(resolution, max_cells)
    order = np.argsort(c)[::-1]
    for i in order[:max_cells]:
        z = int(u[i])
        aa = (z >> 32) & 0xFFFFFFFF
        bb = z & 0xFFFFFFFF
        if aa >= 2**31:
            aa -= 2**32
        if bb >= 2**31:
            bb -= 2**32
        s.cells[(aa, bb)] = int(c[i])
    if len(order) > max_cells:
        s.overflow = int(c[order[max_cells:]].sum())
    return s


def temporal_batch(ts):
    import numpy as np

    a = np.asarray(ts, dtype=np.float64)
    s = TemporalEvidence()
    if a.size:
        s.n = int(a.size)
        s.first_ts = float(a[0])
        s.last_ts = float(a[-1])
        d = np.diff(a)
        s.out_of_order = int(np.count_nonzero(d < 0))
        for x in np.abs(d)[:: max(1, len(d) // 2048)]:
            s.gaps.update(float(x))
    return s


def hashed_dependency_batch(a, b, bins=64):
    import numpy as np

    x = np.asarray(a)
    y = np.asarray(b)
    s = HashedDependencyEvidence(bins)
    # Numeric/categorical integer fast path; arbitrary objects use bounded Python fallback.
    if x.dtype.kind in "iub" and y.dtype.kind in "iub":
        # the same canonical hash as HashedDependencyEvidence._bin, computed in one native call
        xi = (hash_column(pa.array(x)).to_numpy() % np.uint64(bins)).astype(np.int64)
        yi = (hash_column(pa.array(y)).to_numpy() % np.uint64(bins)).astype(np.int64)
        code = xi * bins + yi
        counts = np.bincount(code, minlength=bins * bins)
        nz = np.flatnonzero(counts)
        s.table = {(int(i // bins), int(i % bins)): int(counts[i]) for i in nz}
        s.n = int(x.size)
        return s
    for aa, bb in zip(x, y, strict=False):
        s.update(aa, bb)
    return s
