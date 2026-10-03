"""Bounded keyed state (P3-02).

``KeyedState`` and ``PartitionedKeyedState`` hold one value per key with an event-time TTL and a
cap on the number of keys. ``KeyedSketches`` keeps a small sketch per key (count, moments,
min/max, first and last event time and, optionally, a distinct-count sketch of an item column)
in numpy arrays that are allocated once: its memory is fixed by ``max_keys``, whatever the
number of events or distinct keys, and a batch of events updates it in a handful of array
operations rather than one Python call per event.

Bounds, for every class here: the number of keys never exceeds the cap. The least recently
updated keys are evicted first (LRU), and a key whose last event is older than the TTL (in
event time) is dropped.
"""

from __future__ import annotations

import base64
import heapq
import zlib
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.hashing import hash_column, hash_value


@dataclass
class _Entry:
    value: object
    expires_at: float
    version: int


class KeyedState:
    """One value per key, with a TTL in event time and a hard cap on keys.

    A heap of ``(expires_at, version, key)`` finds the key to expire or evict next. Updating a key
    pushes a new heap entry and leaves the old one to be skipped, so the heap is rebuilt from the
    live entries whenever it grows past twice the key cap: it never holds more than
    ``2 * max_keys + 64`` entries (S2: it used to grow with every update).
    """

    def __init__(self, ttl_seconds: float, max_keys: int = 100000) -> None:
        if ttl_seconds <= 0 or max_keys < 1:
            raise ValueError("invalid state bounds")
        self.ttl = ttl_seconds
        self.max_keys = max_keys
        self._d: dict[Hashable, _Entry] = {}
        self._heap: list[tuple[float, int, Hashable]] = []
        self._version = 0

    def _expire(self, now: float) -> None:
        while self._heap and self._heap[0][0] <= now:
            exp, ver, key = heapq.heappop(self._heap)
            e = self._d.get(key)
            if e is not None and e.version == ver and e.expires_at <= now:
                self._d.pop(key, None)

    def _push(self, e: _Entry, key: Hashable) -> None:
        heapq.heappush(self._heap, (e.expires_at, e.version, key))
        if len(self._heap) > 2 * self.max_keys + 64:
            self._heap = [(x.expires_at, x.version, k) for k, x in self._d.items()]
            heapq.heapify(self._heap)

    def put(self, key: Hashable, value: object, event_time: float) -> None:
        self._expire(event_time)
        self._version += 1
        e = _Entry(value, event_time + self.ttl, self._version)
        self._d[key] = e
        self._push(e, key)
        while len(self._d) > self.max_keys:
            _, ver, k = heapq.heappop(self._heap)
            e2 = self._d.get(k)
            if e2 is not None and e2.version == ver:
                self._d.pop(k, None)

    def get(self, key: Hashable, now: float) -> object | None:
        self._expire(now)
        e = self._d.get(key)
        return None if e is None else e.value

    def snapshot(self) -> dict[str, Any]:
        return {
            "ttl_seconds": self.ttl,
            "max_keys": self.max_keys,
            "items": [(k, e.value, e.expires_at) for k, e in self._d.items()],
        }

    @classmethod
    def restore(cls, s: dict[str, Any]) -> KeyedState:
        x = cls(s["ttl_seconds"], s["max_keys"])
        for k, v, exp in s["items"]:
            x._version += 1
            e = _Entry(v, exp, x._version)
            x._d[k] = e
            heapq.heappush(x._heap, (exp, e.version, k))
        return x

    def __len__(self) -> int:
        return len(self._d)


class PartitionedKeyedState:
    """Deterministic keyed state sharded by stable key hash."""

    def __init__(
        self, partitions: int, ttl_seconds: float, max_keys_per_partition: int = 100000
    ) -> None:
        if partitions < 1:
            raise ValueError("partitions")
        self.partitions = partitions
        self.states = [KeyedState(ttl_seconds, max_keys_per_partition) for _ in range(partitions)]

    def partition_for(self, key: Hashable) -> int:
        import hashlib

        b = repr((type(key).__name__, key)).encode("utf-8")
        return int.from_bytes(hashlib.blake2b(b, digest_size=8).digest(), "big") % self.partitions

    def put(self, key: Hashable, value: object, event_time: float) -> None:
        self.states[self.partition_for(key)].put(key, value, event_time)

    def get(self, key: Hashable, now: float) -> object | None:
        return self.states[self.partition_for(key)].get(key, now)

    def snapshot(self) -> dict[str, Any]:
        return {"partitions": self.partitions, "states": [s.snapshot() for s in self.states]}

    @classmethod
    def restore(cls, s: dict[str, Any]) -> PartitionedKeyedState:
        obj = cls.__new__(cls)
        obj.partitions = s["partitions"]
        obj.states = [KeyedState.restore(x) for x in s["states"]]
        return obj

    def __len__(self) -> int:
        return sum(map(len, self.states))


# ----------------------------------------------------------------- per-key sketches

SKETCH_FORMAT = "shape-keyed-sketches-v1"
_SKETCH_COUNTERS = ("batches", "events", "null_keys", "evicted", "expired")
_DICT_BYTES_PER_KEY = 120  # estimate: a dict slot, the hash key's int object and the slot's int
_PER_KEY_FIELDS = (  # (name, dtype): one array each, one entry per slot
    ("key", np.uint64),
    ("count", np.int64),
    ("n_values", np.int64),
    ("mean", np.float64),
    ("m2", np.float64),
    ("minimum", np.float64),
    ("maximum", np.float64),
    ("first_time", np.float64),
    ("last_time", np.float64),
    ("touch", np.int64),
    ("born", np.int64),
)


def _leading_zeros64(x: np.ndarray) -> np.ndarray:
    """Leading zero bits of each uint64 (64 for zero), exact (floats hold 32 bits exactly)."""
    hi = (x >> np.uint64(32)).astype(np.float64)
    lo = (x & np.uint64(0xFFFFFFFF)).astype(np.float64)
    _, e_hi = np.frexp(hi)
    _, e_lo = np.frexp(lo)
    return np.where(hi > 0, 32 - e_hi, np.where(lo > 0, 64 - e_lo, 64)).astype(np.int64)


def _pack(a: np.ndarray) -> str:
    return base64.b64encode(zlib.compress(np.ascontiguousarray(a).tobytes(), 6)).decode("ascii")


def _unpack(text: str, dtype: Any) -> np.ndarray:
    return np.frombuffer(
        zlib.decompress(base64.b64decode(text)), dtype=np.dtype(dtype).newbyteorder("<")
    ).astype(dtype)


class KeyedSketches:
    """A sketch per key, in memory that ``max_keys`` fixes.

    Each event has a key and, optionally, a numeric ``value``, an event ``time`` (seconds) and an
    ``item``. Per key it tracks the event count, the count, mean, variance, min and max of the
    finite values, the first and last event time, and (with ``distinct=True``) a HyperLogLog of
    the items (precision ``hll_p``, 6 by default: 64 one-byte registers, about 13% error).

    Keys are identified by their canonical 64-bit XXH3 hash (T-13: ``1`` and ``1.0`` are the same
    key; null keys are skipped and counted in ``null_keys``); two keys collide with probability
    about ``n^2 / 2^65``. Without ``times``, the batch number is the event time, so the TTL then
    counts batches.

    * **LRU:** when a new key arrives and all ``max_keys`` slots are taken, the least recently
      updated keys are evicted (in chunks of about 1.5% of the cap, which amortizes the scan).
      A batch with more distinct keys than the cap is processed in parts.
    * **TTL:** a key whose last event time is ``ttl`` or more behind the newest event time seen
      is dropped. Dropping is swept every ``ttl / 16`` of event time; ``get`` is exact.
    * **Memory:** ``nbytes`` (the arrays) plus about 120 bytes per live key for the index.
    """

    def __init__(
        self,
        max_keys: int,
        ttl: float | None = None,
        *,
        distinct: bool = False,
        hll_p: int = 6,
    ) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        if not 4 <= hll_p <= 18:
            raise ValueError("hll_p must be 4..18")
        self.max_keys = int(max_keys)
        self.ttl = ttl
        self.distinct = distinct
        self.hll_p = hll_p
        cap = self.max_keys
        self._key = np.zeros(cap, dtype=np.uint64)
        self._count = np.zeros(cap, dtype=np.int64)
        self._n_values = np.zeros(cap, dtype=np.int64)
        self._mean = np.zeros(cap, dtype=np.float64)
        self._m2 = np.zeros(cap, dtype=np.float64)
        self._minimum = np.zeros(cap, dtype=np.float64)
        self._maximum = np.zeros(cap, dtype=np.float64)
        self._first_time = np.zeros(cap, dtype=np.float64)
        self._last_time = np.zeros(cap, dtype=np.float64)
        self._touch = np.zeros(cap, dtype=np.int64)
        self._born = np.zeros(cap, dtype=np.int64)
        self._alive = np.zeros(cap, dtype=bool)
        self._registers = (
            np.zeros((self.max_keys, 1 << hll_p), dtype=np.uint8) if distinct else None
        )
        self._index: dict[int, int] = {}
        self._free: list[int] = []
        self._used = 0  # slots below this have been handed out at least once
        self._tick = 0  # LRU clock: one per processed chunk
        self._born_next = 0  # allocation counter (tie-break that does not depend on slot numbers)
        self._now: float | None = None  # newest event time seen
        self._swept: float | None = None
        self.batches = 0
        self.events = 0
        self.null_keys = 0
        self.evicted = 0
        self.expired = 0

    # ------------------------------------------------------------------ sizes
    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, key: object) -> bool:
        return self._slot(key) is not None

    def _slot(self, key: object) -> int | None:
        """The slot of a live key; ``None`` if it is unknown or its TTL has passed."""
        h = hash_value(key)
        slot = None if h is None else self._index.get(h)
        if slot is None:
            return None
        if self.ttl is not None and self._now is not None:
            if self._last_time[slot] + self.ttl <= self._now:
                return None
        return slot

    @property
    def bytes_per_key(self) -> int:
        return (
            sum(np.dtype(d).itemsize for _, d in _PER_KEY_FIELDS)
            + 1
            + ((1 << self.hll_p) if self.distinct else 0)
        )

    @property
    def nbytes(self) -> int:
        """Bytes held by the per-key arrays (fixed at construction)."""
        return self.bytes_per_key * self.max_keys

    @property
    def memory_cap_bytes(self) -> int:
        """The arrays plus an estimate for the index of a full state."""
        return self.nbytes + _DICT_BYTES_PER_KEY * self.max_keys

    @classmethod
    def for_budget(
        cls, max_bytes: int, ttl: float | None = None, *, distinct: bool = False, hll_p: int = 6
    ) -> KeyedSketches:
        """A state whose ``memory_cap_bytes`` is at most ``max_bytes``."""
        per_key = cls(1, ttl, distinct=distinct, hll_p=hll_p).bytes_per_key + _DICT_BYTES_PER_KEY
        keys = max_bytes // per_key
        if keys < 1:
            raise ValueError(f"a budget of {max_bytes} bytes holds no key ({per_key} bytes each)")
        return cls(keys, ttl, distinct=distinct, hll_p=hll_p)

    def _arrays(self) -> dict[str, np.ndarray]:
        return {
            "key": self._key,
            "count": self._count,
            "n_values": self._n_values,
            "mean": self._mean,
            "m2": self._m2,
            "minimum": self._minimum,
            "maximum": self._maximum,
            "first_time": self._first_time,
            "last_time": self._last_time,
            "touch": self._touch,
            "born": self._born,
        }

    # ----------------------------------------------------------------- update
    def update(
        self,
        keys: Any,
        values: Any = None,
        times: Any = None,
        items: Any = None,
    ) -> None:
        """Add a batch of events. ``keys`` and ``items`` are arrays (Arrow, numpy or lists);
        ``values`` and ``times`` are numeric arrays of the same length."""
        key_hash, key_ok = _hashes(keys)
        n = len(key_hash)
        v = None if values is None else np.asarray(values, dtype=np.float64)
        if items is not None and not self.distinct:
            raise ValueError("pass distinct=True to track items")
        item_hash, item_ok = (None, None) if items is None else _hashes(items)
        self.batches += 1
        t = (
            np.full(n, float(self.batches))
            if times is None
            else np.asarray(times, dtype=np.float64)
        )
        for other, name in ((v, "values"), (t, "times"), (item_hash, "items")):
            if other is not None and len(other) != n:
                raise ValueError(f"{name} has {len(other)} entries for {n} keys")
        self.events += n
        self.null_keys += int(n - np.count_nonzero(key_ok))
        rows = np.flatnonzero(key_ok & ~np.isnan(t))
        if rows.size == 0:
            return
        self._add(
            key_hash[rows],
            None if v is None else v[rows],
            t[rows],
            None if item_hash is None or item_ok is None else (item_hash[rows], item_ok[rows]),
        )

    def _add(
        self,
        h: np.ndarray,
        v: np.ndarray | None,
        t: np.ndarray,
        items: tuple[np.ndarray, np.ndarray] | None,
    ) -> None:
        uniq, inverse = np.unique(h, return_inverse=True)
        if len(uniq) > self.max_keys:  # more distinct keys than slots: process in two parts
            half = len(h) // 2
            for lo, hi in ((0, half), (half, len(h))):
                self._add(
                    h[lo:hi],
                    None if v is None else v[lo:hi],
                    t[lo:hi],
                    None if items is None else (items[0][lo:hi], items[1][lo:hi]),
                )
            return
        self._tick += 1
        before = self._now  # a key idle for the TTL by then is gone, even if not swept yet
        newest = float(t.max())
        self._now = newest if self._now is None else max(self._now, newest)
        counts = np.bincount(inverse, minlength=len(uniq))
        slots = self._slots_for(uniq, before)
        self._count[slots] += counts
        order = np.argsort(inverse, kind="stable")
        starts = np.cumsum(counts) - counts
        t_sorted = t[order]
        self._last_time[slots] = np.maximum(
            self._last_time[slots], np.maximum.reduceat(t_sorted, starts)
        )
        self._first_time[slots] = np.minimum(
            self._first_time[slots], np.minimum.reduceat(t_sorted, starts)
        )
        if v is not None:
            self._add_values(slots, inverse, v, order, starts)
        if items is not None:
            self._add_items(slots, inverse, *items)
        self._sweep()

    def _add_values(
        self,
        slots: np.ndarray,
        inverse: np.ndarray,
        v: np.ndarray,
        order: np.ndarray,
        starts: np.ndarray,
    ) -> None:
        groups = len(slots)
        finite = np.isfinite(v)
        n_b = np.bincount(inverse, weights=finite, minlength=groups)
        if not n_b.any():
            return
        total = np.bincount(inverse, weights=np.where(finite, v, 0.0), minlength=groups)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_b = np.where(n_b > 0, total / n_b, 0.0)
        dev = np.where(finite, v - mean_b[inverse], 0.0)
        m2_b = np.bincount(inverse, weights=dev * dev, minlength=groups)
        lo = np.minimum.reduceat(np.where(finite, v, np.inf)[order], starts)
        hi = np.maximum.reduceat(np.where(finite, v, -np.inf)[order], starts)
        hit = n_b > 0
        s = slots[hit]
        nb = n_b[hit]
        na = self._n_values[s].astype(np.float64)
        n = na + nb
        delta = mean_b[hit] - self._mean[s]
        self._mean[s] += delta * nb / n
        self._m2[s] += m2_b[hit] + delta * delta * na * nb / n
        self._n_values[s] += nb.astype(np.int64)
        self._minimum[s] = np.minimum(self._minimum[s], lo[hit])
        self._maximum[s] = np.maximum(self._maximum[s], hi[hit])

    def _add_items(
        self, slots: np.ndarray, inverse: np.ndarray, h: np.ndarray, ok: np.ndarray
    ) -> None:
        assert self._registers is not None
        p = self.hll_p
        h = h[ok]
        slot = slots[inverse[ok]]
        idx = (h >> np.uint64(64 - p)).astype(np.int64)
        rank = (_leading_zeros64(h << np.uint64(p)) + 1).clip(max=64 - p + 1).astype(np.uint8)
        flat = self._registers.reshape(-1)
        np.maximum.at(flat, slot * (1 << p) + idx, rank)

    # ----------------------------------------------------------------- slots
    def _slots_for(self, uniq: np.ndarray, now: float | None = None) -> np.ndarray:
        get = self._index.get
        slots = np.fromiter((get(k, -1) for k in uniq.tolist()), dtype=np.int64, count=len(uniq))
        if self.ttl is not None and now is not None:
            found = np.flatnonzero(slots >= 0)
            stale = found[self._last_time[slots[found]] + self.ttl <= now]
            if stale.size:  # expired keys that the sweep has not reached: they start afresh
                self._drop(slots[stale])
                self.expired += int(stale.size)
                slots[stale] = -1
        missing = np.flatnonzero(slots < 0)
        self._touch[slots[slots >= 0]] = self._tick  # keys in this batch are not evicted
        if missing.size == 0:
            return slots
        spare = len(self._free) + (self.max_keys - self._used)
        if missing.size > spare:
            self._evict(missing.size - spare)
        fresh = np.empty(missing.size, dtype=np.int64)
        from_free = min(len(self._free), missing.size)
        for i in range(from_free):
            fresh[i] = self._free.pop()
        rest = missing.size - from_free
        fresh[from_free:] = np.arange(self._used, self._used + rest)
        self._used += rest
        self._index.update(zip(uniq[missing].tolist(), fresh.tolist(), strict=True))
        self._key[fresh] = uniq[missing]
        self._alive[fresh] = True
        for arr in (self._count, self._n_values, self._mean, self._m2):
            arr[fresh] = 0
        self._minimum[fresh] = np.inf
        self._maximum[fresh] = -np.inf
        self._first_time[fresh] = np.inf
        self._last_time[fresh] = -np.inf
        self._touch[fresh] = self._tick
        self._born[fresh] = np.arange(self._born_next, self._born_next + missing.size)
        self._born_next += missing.size
        if self._registers is not None:
            self._registers[fresh] = 0
        slots[missing] = fresh
        return slots

    def _drop(self, victims: np.ndarray) -> None:
        index = self._index
        for k in self._key[victims].tolist():
            del index[k]
        self._alive[victims] = False
        self._free.extend(victims.tolist())

    def _evict(self, at_least: int) -> None:
        """Drop the least recently updated keys (never one touched by the current chunk)."""
        candidates = np.flatnonzero(self._alive & (self._touch < self._tick))
        want = min(max(at_least, self.max_keys // 64 + 1), len(candidates))
        order = np.lexsort((self._born[candidates], self._touch[candidates]))
        self._drop(candidates[order[:want]])
        self.evicted += want

    def _sweep(self) -> None:
        """Drop keys idle for the TTL, when ``ttl / 16`` of event time has passed since the
        last sweep."""
        if self.ttl is None or self._now is None:
            return
        if self._swept is not None and self._now - self._swept < self.ttl / 16:
            return
        self._swept = self._now
        gone = np.flatnonzero(self._alive & (self._last_time + self.ttl <= self._now))
        if gone.size:
            self._drop(gone)
            self.expired += int(gone.size)

    # ------------------------------------------------------------------- read
    def summary(self, key: object) -> dict[str, Any] | None:
        """The sketch of one key, or ``None`` if it is unknown or its TTL has passed."""
        slot = self._slot(key)
        if slot is None:
            return None
        n = int(self._n_values[slot])
        out: dict[str, Any] = {
            "count": int(self._count[slot]),
            "values": n,
            "mean": float(self._mean[slot]) if n else None,
            "variance": float(self._m2[slot] / (n - 1)) if n > 1 else None,
            "min": float(self._minimum[slot]) if n else None,
            "max": float(self._maximum[slot]) if n else None,
            "first_time": float(self._first_time[slot]),
            "last_time": float(self._last_time[slot]),
        }
        if self._registers is not None:
            from shape.kernel.reference.pysketch import HyperLogLog

            regs = [int(r) for r in self._registers[slot]]
            out["distinct"] = float(HyperLogLog(self.hll_p, regs).estimate())
        return out

    # --------------------------------------------------------------- snapshot
    def snapshot(self) -> dict[str, Any]:
        """A JSON-safe copy of the live keys (slot numbers are not kept; the LRU order is)."""
        live = np.flatnonzero(self._alive)
        out: dict[str, Any] = {
            "format": SKETCH_FORMAT,
            "max_keys": self.max_keys,
            "ttl": self.ttl,
            "distinct": self.distinct,
            "hll_p": self.hll_p,
            "tick": self._tick,
            "born": self._born_next,
            "now": self._now,
            "swept": self._swept,
            "counters": {
                "batches": self.batches,
                "events": self.events,
                "null_keys": self.null_keys,
                "evicted": self.evicted,
                "expired": self.expired,
            },
            "live": int(live.size),
            "arrays": {name: _pack(arr[live]) for name, arr in self._arrays().items()},
        }
        if self._registers is not None:
            out["arrays"]["registers"] = _pack(self._registers[live])
        return out

    @classmethod
    def restore(cls, snap: dict[str, Any]) -> KeyedSketches:
        if snap.get("format") != SKETCH_FORMAT:
            raise ValueError("not a keyed-sketches snapshot")
        obj = cls(snap["max_keys"], snap["ttl"], distinct=snap["distinct"], hll_p=snap["hll_p"])
        n = int(snap["live"])
        if n > obj.max_keys:
            raise ValueError("snapshot holds more keys than its cap")
        for name, arr in obj._arrays().items():
            data = _unpack(snap["arrays"][name], arr.dtype)
            if len(data) != n:
                raise ValueError(f"snapshot array {name!r} has {len(data)} entries, not {n}")
            arr[:n] = data
        if obj._registers is not None:
            obj._registers[:n] = _unpack(snap["arrays"]["registers"], np.uint8).reshape(
                n, 1 << obj.hll_p
            )
        obj._alive[:n] = True
        obj._used = n
        obj._index = dict(zip(obj._key[:n].tolist(), range(n), strict=True))
        obj._tick, obj._born_next = int(snap["tick"]), int(snap["born"])
        obj._now, obj._swept = snap["now"], snap["swept"]
        for name, value in snap["counters"].items():
            if name not in _SKETCH_COUNTERS:  # never an arbitrary attribute from a file
                raise ValueError(f"snapshot holds an unknown counter {name!r}")
            setattr(obj, name, int(value))
        return obj


def _hashes(values: Any) -> tuple[np.ndarray, np.ndarray]:
    """Canonical 64-bit hashes of an array, and the mask of entries that have one (nulls and NaN
    have none)."""
    arr = values if isinstance(values, pa.Array) else pa.array(values)
    h = hash_column(arr)
    ok = np.asarray(h.is_valid())
    return h.fill_null(0).to_numpy(zero_copy_only=False).astype(np.uint64, copy=False), ok
