"""Vectorized, bounded duplicate detection for streams (P3-02).

``Deduplicator.filter(keys)`` returns a keep-mask: ``True`` for the first sighting of a key,
``False`` for a repeat, over a window of recent keys that ``max_keys`` bounds. The seen keys sit
in a few sorted numpy runs (a small log-structured merge tree), so a batch costs a few
``searchsorted`` calls and a merge, never a Python call per row.

Semantics (a reference ``OrderedDict`` model in the tests checks them exactly):

* a batch is deduplicated as a whole: of several equal keys in one batch the first row is kept;
* a key is a repeat when it is in the window, and, with ``ttl``, when its last sighting is less
  than ``ttl`` behind the newest event time of the *earlier* batches (any sighting, kept or not,
  refreshes it);
* afterwards, if more than ``max_keys`` keys are held, expired keys go first, then the keys that
  were first seen longest ago. A forgotten key is kept again when it returns.

Integer keys are compared exactly. Anything else (strings, floats, dates, ...) is compared by
its canonical 64-bit XXH3 hash (T-13), so two different keys are mistaken for one with
probability about ``n^2 / 2^65``. Null keys are never duplicates. A state takes one kind of key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.hashing import hash_column
from shape.streaming.keyed import _pack, _unpack

DEDUPE_FORMAT = "shape-dedupe-v1"


@dataclass
class _Run:
    """Sorted unique keys with the order each was first seen in and its last sighting."""

    keys: np.ndarray  # int64, ascending
    seq: np.ndarray  # int64, unique across the whole state
    time: np.ndarray  # float64

    def __len__(self) -> int:
        return len(self.keys)


def _empty() -> _Run:
    return _Run(np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.float64))


class Deduplicator:
    """Exact-in-window duplicate filter; see the module docstring for the rules."""

    def __init__(self, max_keys: int = 10_000_000, ttl: float | None = None) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        self.max_keys = int(max_keys)
        self.ttl = ttl
        self._runs: list[_Run] = []  # largest (oldest) first
        self._seq = 0
        self._now: float | None = None
        self._kind: str | None = None
        self.rows = 0
        self.duplicates = 0

    def __len__(self) -> int:
        """Entries held, including repeats of one key across runs and keys past their TTL that
        have not been purged yet; at most ``max_keys`` after any ``filter`` call."""
        return sum(len(r) for r in self._runs)

    @property
    def nbytes(self) -> int:
        return 24 * len(self)

    # ---------------------------------------------------------------- filter
    def filter(self, keys: Any, event_times: Any = None) -> np.ndarray:
        """The keep-mask for a batch of keys (``True`` = first sighting)."""
        if self.ttl is not None and event_times is None:
            raise ValueError("a ttl needs event_times")
        k, valid = self._canonical(keys)
        n = len(k)
        t = None if event_times is None else np.asarray(event_times, dtype=np.float64)
        if t is not None and len(t) != n:
            raise ValueError(f"event_times has {len(t)} entries for {n} keys")
        self.rows += n
        keep = np.ones(n, dtype=bool)
        rows = np.flatnonzero(valid if t is None else valid & ~np.isnan(t))
        if rows.size == 0:
            return keep
        kv = k[rows]
        uniq, first, inverse = np.unique(kv, return_index=True, return_inverse=True)
        keep[rows] = False
        tmax = None if t is None else _group_max(t[rows], inverse, len(uniq))
        known = self._lookup(uniq, tmax)
        new = np.flatnonzero(~known)
        keep[rows[first[new]]] = True
        self.duplicates += int(rows.size - new.size)
        if new.size:
            rank = np.empty(new.size, dtype=np.int64)
            rank[np.argsort(first[new], kind="stable")] = np.arange(new.size)
            self._runs.append(
                _Run(
                    uniq[new],
                    self._seq + rank,
                    np.zeros(new.size) if tmax is None else tmax[new],
                )
            )
            self._seq += new.size
            self._compact()
        if tmax is not None:
            newest = float(tmax.max())
            self._now = newest if self._now is None else max(self._now, newest)
        self._cap()
        return keep

    def _canonical(self, keys: Any) -> tuple[np.ndarray, np.ndarray]:
        """int64 identities of the keys and the mask of non-null ones."""
        arr = keys if isinstance(keys, pa.Array | pa.ChunkedArray) else pa.array(keys)
        if isinstance(arr, pa.ChunkedArray):
            arr = arr.combine_chunks()
        if pa.types.is_null(arr.type):  # no key at all (an empty or all-null batch): no kind
            return np.zeros(len(arr), dtype=np.int64), np.zeros(len(arr), dtype=bool)
        kind = "int" if pa.types.is_integer(arr.type) else "hash"
        if self._kind is None:
            self._kind = kind
        elif self._kind != kind:
            raise TypeError(f"this state holds {self._kind} keys, not {kind} keys ({arr.type})")
        valid = np.asarray(arr.is_valid())
        if kind == "int":
            if arr.null_count:
                arr = arr.fill_null(0)
            return arr.to_numpy(zero_copy_only=False).astype(np.int64, copy=False), valid
        h = hash_column(arr)
        ok = np.asarray(h.is_valid())
        return h.fill_null(0).to_numpy(zero_copy_only=False).view(np.int64), ok

    def _lookup(self, uniq: np.ndarray, tmax: np.ndarray | None) -> np.ndarray:
        known = np.zeros(len(uniq), dtype=bool)
        for run in self._runs:
            pos = np.minimum(np.searchsorted(run.keys, uniq), len(run) - 1)
            hit = run.keys[pos] == uniq
            if self.ttl is not None and self._now is not None:
                hit &= run.time[pos] + self.ttl > self._now
            if tmax is not None and hit.any():
                at = pos[hit]
                run.time[at] = np.maximum(run.time[at], tmax[hit])
            known |= hit
        return known

    # ------------------------------------------------------------------ runs
    def _live(self, run: _Run) -> _Run:
        """The run without entries past their TTL."""
        if self.ttl is None or self._now is None:
            return run
        ok = run.time + self.ttl > self._now
        return run if ok.all() else _Run(run.keys[ok], run.seq[ok], run.time[ok])

    def _merge(self, runs: list[_Run]) -> _Run:
        """One sorted run from several: expired entries dropped, a key held twice keeps its
        newest entry."""
        parts = [self._live(r) for r in runs]
        keys = np.concatenate([p.keys for p in parts])
        seq = np.concatenate([p.seq for p in parts])
        time = np.concatenate([p.time for p in parts])
        order = np.lexsort((-seq, keys))  # by key, newest entry first
        keys, seq, time = keys[order], seq[order], time[order]
        first = np.ones(len(keys), dtype=bool)
        first[1:] = keys[1:] != keys[:-1]
        return _Run(keys[first], seq[first], time[first])

    def _compact(self) -> None:
        """Keep run sizes geometric: merge the two newest while the older is at most twice the
        newer."""
        while len(self._runs) >= 2 and len(self._runs[-2]) <= 2 * len(self._runs[-1]):
            newer = self._runs.pop()
            older = self._runs.pop()
            merged = self._merge([older, newer])
            if len(merged):
                self._runs.append(merged)

    def _cap(self) -> None:
        if len(self) <= self.max_keys:
            return
        run = self._merge(self._runs)
        if len(run) > self.max_keys:
            keep = np.argpartition(run.seq, len(run) - self.max_keys)[len(run) - self.max_keys :]
            keep.sort()  # runs stay ordered by key
            run = _Run(run.keys[keep], run.seq[keep], run.time[keep])
        self._runs = [run] if len(run) else []

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> dict[str, Any]:
        """A JSON-safe copy of the window."""
        return {
            "format": DEDUPE_FORMAT,
            "max_keys": self.max_keys,
            "ttl": self.ttl,
            "seq": self._seq,
            "now": self._now,
            "kind": self._kind,
            "counters": {"rows": self.rows, "duplicates": self.duplicates},
            "runs": [
                {"keys": _pack(r.keys), "seq": _pack(r.seq), "time": _pack(r.time)}
                for r in self._runs
            ],
        }

    @classmethod
    def restore(cls, snap: dict[str, Any]) -> Deduplicator:
        if snap.get("format") != DEDUPE_FORMAT:
            raise ValueError("not a deduplicator snapshot")
        obj = cls(snap["max_keys"], snap["ttl"])
        obj._seq, obj._now, obj._kind = int(snap["seq"]), snap["now"], snap["kind"]
        obj.rows = int(snap["counters"]["rows"])
        obj.duplicates = int(snap["counters"]["duplicates"])
        for r in snap["runs"]:
            run = _Run(
                _unpack(r["keys"], np.int64),
                _unpack(r["seq"], np.int64),
                _unpack(r["time"], np.float64),
            )
            if not (len(run.keys) == len(run.seq) == len(run.time)):
                raise ValueError("snapshot run arrays differ in length")
            obj._runs.append(run)
        return obj


def _group_max(values: np.ndarray, inverse: np.ndarray, groups: int) -> np.ndarray:
    out = np.full(groups, -np.inf)
    order = np.argsort(inverse, kind="stable")
    counts = np.bincount(inverse, minlength=groups)
    starts = np.cumsum(counts) - counts
    out[:] = np.maximum.reduceat(values[order], starts)
    return out
