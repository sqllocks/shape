"""P3-02: bounded keyed state (per-key sketches, LRU, TTL, hard cap) and vectorized dedupe."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from collections import OrderedDict

import numpy as np
import pyarrow as pa
import pytest

from shape.streaming import KeyedState, deduplicate_ids
from shape.streaming.dedupe import Deduplicator
from shape.streaming.keyed import KeyedSketches

# ------------------------------------------------------------------ S2, S5


def test_s2_keyed_state_heap_does_not_grow_with_updates():
    """S2: every ``put`` pushed a heap entry and only expiry removed it, so updates to few keys
    with a long TTL grew the heap without bound."""
    s = KeyedState(ttl_seconds=10**9, max_keys=100)
    for i in range(50_000):
        s.put(i % 10, i, float(i))
    assert len(s) == 10
    assert len(s._heap) <= 2 * s.max_keys + 64
    for i in range(50_000):  # and with churn over more keys than the cap
        s.put(f"k{i}", i, float(i))
    assert len(s) <= 100 and len(s._heap) <= 2 * s.max_keys + 64


def test_keyed_state_still_expires_evicts_and_restores():
    s = KeyedState(10, 5)
    for i in range(20):
        s.put(i, i, float(i))
    assert len(s) <= 5
    assert s.get(19, 19.0) == 19 and s.get(0, 19.0) is None
    r = KeyedState.restore(json.loads(json.dumps(s.snapshot())))
    assert len(r) == len(s) and r.get(19, 19.0) == 19
    assert s.get(19, 100.0) is None  # past the TTL


class _CountingSet(set):
    calls = 0

    def __contains__(self, x):
        type(self).calls += 1
        return super().__contains__(x)


def test_s5_deduplicate_ids_asks_the_set_once_per_distinct_id_not_per_row():
    """S5: ``deduplicate_ids`` looped over every row."""
    seen = _CountingSet()
    ids = np.tile(np.arange(10), 20_000)  # 200,000 rows, 10 distinct ids
    keep = deduplicate_ids(ids, seen)
    assert _CountingSet.calls <= 10
    assert keep.sum() == 10 and ids[keep].tolist() == list(range(10))
    assert seen == set(range(10))
    again = deduplicate_ids(np.array([3, 11, 11, 12]), seen)
    assert again.tolist() == [False, True, False, True]


def test_deduplicate_ids_matches_a_row_by_row_reference():
    rng = np.random.default_rng(0)
    seen, ref = set(), set()
    for _ in range(30):
        ids = rng.integers(0, 400, 300)
        want = []
        for x in ids.tolist():
            want.append(x not in ref)
            ref.add(x)
        assert deduplicate_ids(ids, seen).tolist() == want
    assert seen == ref
    mixed = np.array([1, "a", 1, "a", 2.5], dtype=object)  # not sortable: still correct
    assert deduplicate_ids(mixed, set()).tolist() == [True, True, False, False, True]


# ---------------------------------------------------------------- dedupe


class _Reference:
    """The documented semantics, one row at a time on an ``OrderedDict``."""

    def __init__(self, max_keys, ttl):
        self.max_keys, self.ttl = max_keys, ttl
        self.table: OrderedDict = OrderedDict()  # key -> [seq, last time]; insertion order = seq
        self.seq = 0
        self.now = None

    def filter(self, keys, times=None):
        keep, firsts = [], {}
        prev_now = self.now
        for i, k in enumerate(keys):
            if k is None:
                keep.append(True)
                continue
            if k in firsts:
                keep.append(False)
                continue
            e = self.table.get(k)
            alive = e is not None and (
                self.ttl is None or prev_now is None or e[1] + self.ttl > prev_now
            )
            firsts[k] = i
            keep.append(not alive)
        for k in keys:  # last sighting per key in this batch
            if k is None:
                continue
            t = 0.0 if times is None else max(times[j] for j in range(len(keys)) if keys[j] == k)
            e = self.table.get(k)
            alive = e is not None and (
                self.ttl is None or prev_now is None or e[1] + self.ttl > prev_now
            )
            if alive:
                e[1] = max(e[1], t)
            else:
                self.table.pop(k, None)
                self.table[k] = [self.seq, t]
                self.seq += 1
        if times is not None:
            newest = max(times)
            self.now = newest if self.now is None else max(self.now, newest)
        if len(self.table) > self.max_keys:
            if self.ttl is not None and self.now is not None:
                for k in [k for k, e in self.table.items() if e[1] + self.ttl <= self.now]:
                    del self.table[k]
            while len(self.table) > self.max_keys:
                oldest = min(self.table, key=lambda k: self.table[k][0])
                del self.table[oldest]
        return keep


def _batches(rng, n_batches, size, universe, nulls=False, strings=False):
    for _ in range(n_batches):
        ks = rng.integers(0, universe, size).tolist()
        if strings:
            ks = [f"id-{k}" for k in ks]
        if nulls:
            ks = [None if rng.random() < 0.05 else k for k in ks]
        yield ks


@pytest.mark.parametrize("max_keys", [10**6, 150, 40])
@pytest.mark.parametrize("strings", [False, True])
def test_dedupe_matches_the_reference_set(max_keys, strings):
    rng = np.random.default_rng(5)
    d, ref = Deduplicator(max_keys), _Reference(max_keys, None)
    for ks in _batches(rng, 40, 200, 300, nulls=True, strings=strings):
        assert d.filter(pa.array(ks)).tolist() == ref.filter(ks)
        assert len(d) <= max_keys
    assert d.rows == 8000 and d.duplicates > 0


def test_dedupe_with_a_plain_set_when_nothing_is_ever_forgotten():
    rng = np.random.default_rng(1)
    d, seen = Deduplicator(), set()
    for ks in _batches(rng, 25, 500, 2000):
        want = []
        for k in ks:
            want.append(k not in seen)
            seen.add(k)
        assert d.filter(np.array(ks)).tolist() == want
    assert len(d) == len(seen)


@pytest.mark.parametrize("max_keys", [10**6, 60])
def test_dedupe_with_a_ttl_matches_the_reference(max_keys):
    rng = np.random.default_rng(8)
    d, ref = Deduplicator(max_keys, ttl=25.0), _Reference(max_keys, 25.0)
    clock = 0.0
    for ks in _batches(rng, 60, 80, 120):
        times = (clock + rng.integers(-15, 30, len(ks))).astype(float).tolist()  # disordered
        clock += 12
        assert d.filter(ks, times).tolist() == ref.filter(ks, times)
        assert len(d) <= max_keys
    with pytest.raises(ValueError, match="needs event_times"):
        d.filter([1, 2])


def test_dedupe_state_stays_within_its_cap_and_forgets_the_oldest():
    d = Deduplicator(max_keys=1000)
    for start in range(0, 20_000, 500):
        d.filter(np.arange(start, start + 500))
        assert len(d) <= 1000
    assert d.filter(np.array([19_999, 19_500, 0, 5])).tolist() == [False, False, True, True]


def test_dedupe_snapshot_restore_continues_exactly():
    rng = np.random.default_rng(3)
    batches = list(_batches(rng, 30, 150, 400))
    times = [(i * 10 + rng.integers(0, 20, 150)).astype(float) for i in range(30)]
    whole = Deduplicator(200, ttl=60.0)
    want = [whole.filter(ks, t).tolist() for ks, t in zip(batches, times, strict=True)]
    for cut in (0, 1, 9, 29):
        first = Deduplicator(200, ttl=60.0)
        out = [
            first.filter(ks, t).tolist() for ks, t in zip(batches[:cut], times[:cut], strict=True)
        ]
        revived = Deduplicator.restore(json.loads(json.dumps(first.snapshot())))
        out += [
            revived.filter(ks, t).tolist() for ks, t in zip(batches[cut:], times[cut:], strict=True)
        ]
        assert out == want
        assert revived.snapshot() == whole.snapshot()


def test_dedupe_validation():
    d = Deduplicator()
    d.filter(np.array([1, 2]))
    with pytest.raises(TypeError, match="int keys"):
        d.filter(["a"])
    with pytest.raises(ValueError):
        Deduplicator(0)
    with pytest.raises(ValueError):
        Deduplicator(5, ttl=0)
    with pytest.raises(ValueError, match="not a deduplicator"):
        Deduplicator.restore({"format": "x"})
    assert d.filter(np.array([], dtype=np.int64)).tolist() == []
    assert d.filter(pa.chunked_array([[1, 5], [5, 6]])).tolist() == [False, True, False, True]


# ------------------------------------------------------- per-key sketches


def _events(rng, n, keys, with_items=False):
    k = rng.integers(0, keys, n)
    v = rng.normal(50, 10, n)
    v[rng.random(n) < 0.05] = np.nan
    t = rng.uniform(0, 1000, n)
    items = rng.integers(0, 40, n) if with_items else None
    return k, v, t, items


def test_per_key_stats_match_a_numpy_computation():
    rng = np.random.default_rng(2)
    s = KeyedSketches(max_keys=500)
    ks, vs, ts = [], [], []
    for _ in range(20):
        k, v, t, _ = _events(rng, 700, 300)
        s.update(k, v, t)
        ks.append(k), vs.append(v), ts.append(t)
    k, v, t = np.concatenate(ks), np.concatenate(vs), np.concatenate(ts)
    assert len(s) == len(np.unique(k)) and s.events == 14_000
    for key in (0, 17, 150, 299):
        m = k == key
        x = v[m][np.isfinite(v[m])]
        got = s.summary(key)
        assert got["count"] == m.sum() and got["values"] == len(x)
        assert got["mean"] == pytest.approx(x.mean(), rel=1e-9)
        assert got["variance"] == pytest.approx(x.var(ddof=1), rel=1e-9)
        assert got["min"] == x.min() and got["max"] == x.max()
        assert got["first_time"] == t[m].min() and got["last_time"] == t[m].max()
    assert s.summary(10**6) is None and (3 in s) and (10**6 not in s)


def test_per_key_distinct_sketch_is_within_its_bound():
    rng = np.random.default_rng(4)
    s = KeyedSketches(max_keys=50, distinct=True, hll_p=8)
    truth: dict[int, set] = {}
    for _ in range(30):
        k, v, t, items = _events(rng, 400, 20, with_items=True)
        s.update(k, v, t, items=pa.array(items.astype(str)))
        for a, b in zip(k.tolist(), items.tolist(), strict=True):
            truth.setdefault(a, set()).add(b)
    for key, items in truth.items():
        est = s.summary(key)["distinct"]
        assert abs(est - len(items)) / len(items) <= 3 * 1.04 / math.sqrt(256)
    with pytest.raises(ValueError, match="distinct=True"):
        KeyedSketches(5).update([1], items=["a"])


def test_the_cap_is_hard_and_the_least_recently_updated_keys_go_first():
    s = KeyedSketches(max_keys=100)
    for i in range(0, 1000, 10):
        s.update(np.arange(i, i + 10), np.ones(10), np.full(10, float(i)))
        assert len(s) <= 100
    assert s.evicted >= 900 - 100 and len(s) <= 100
    assert 999 in s and 990 in s and 0 not in s and 500 not in s
    # a key that keeps being updated survives while the others rotate
    s = KeyedSketches(max_keys=50)
    s.update([7], [1.0], [0.0])
    for i in range(1, 400):
        s.update([7, 1000 + i], [1.0, 1.0], [float(i)] * 2)
        assert len(s) <= 50
    assert 7 in s and s.summary(7)["count"] == 400


def test_a_batch_with_more_distinct_keys_than_the_cap_is_processed_in_parts():
    s = KeyedSketches(max_keys=64)
    s.update(np.arange(10_000), np.arange(10_000, dtype=float), np.arange(10_000, dtype=float))
    assert len(s) <= 64 and s.events == 10_000
    assert 9_999 in s  # the newest keys are the ones kept


def test_ttl_drops_idle_keys_and_get_is_exact():
    s = KeyedSketches(max_keys=100, ttl=50.0)
    s.update([1, 2], [1.0, 1.0], [0.0, 0.0])
    s.update([2], [1.0], [40.0])
    assert 1 in s and 2 in s
    s.update([3], [1.0], [60.0])  # now = 60: key 1 (last event 0) is idle for 60 > ttl
    assert 1 not in s and 2 in s and 3 in s
    assert s.summary(1) is None
    s.update([3], [1.0], [200.0])
    assert len(s) == 1 and s.expired >= 2  # swept: 1 and 2 are gone, their slots are free
    for i in range(99):  # the freed slots are reused: nothing is evicted to make room
        s.update([5000 + i], [0.0], [200.0])
    assert len(s) == 100 and s.evicted == 0


def test_nulls_nans_and_validation():
    s = KeyedSketches(10)
    s.update(pa.array([1, None, 1, 2]), [1.0, 5.0, float("nan"), float("inf")], [1, 2, 3, 4])
    assert s.null_keys == 1
    one = s.summary(1)
    assert one["count"] == 2 and one["values"] == 1 and one["mean"] == 1.0
    assert s.summary(2)["values"] == 0 and s.summary(2)["mean"] is None
    with pytest.raises(ValueError, match="values has 1 entries for 2 keys"):
        s.update([1, 2], [1.0])
    with pytest.raises(ValueError):
        KeyedSketches(0)
    with pytest.raises(ValueError):
        KeyedSketches(5, ttl=-1)
    with pytest.raises(ValueError, match="not a keyed-sketches"):
        KeyedSketches.restore({"format": "x"})


def test_memory_is_fixed_by_the_cap():
    s = KeyedSketches(max_keys=1000, distinct=True)
    assert s.nbytes == s.bytes_per_key * 1000
    assert s.memory_cap_bytes > s.nbytes
    b = KeyedSketches.for_budget(10_000_000, ttl=5.0)
    assert b.memory_cap_bytes <= 10_000_000 and b.max_keys > 1000
    with pytest.raises(ValueError, match="holds no key"):
        KeyedSketches.for_budget(10)
    before = s.nbytes
    rng = np.random.default_rng(0)
    for _ in range(10):
        k, v, t, items = _events(rng, 5000, 10_000, with_items=True)
        s.update(k, v, t, items=items)
    assert s.nbytes == before and len(s) <= 1000


def test_snapshot_restore_continues_exactly():
    rng = np.random.default_rng(6)
    stream = [_events(rng, 600, 800, with_items=True) for _ in range(12)]

    def make():
        return KeyedSketches(max_keys=300, ttl=400.0, distinct=True)

    def feed(state, parts):
        for k, v, t, items in parts:
            state.update(k, v, t + 0, items=items)

    def view(state):
        keys = sorted(set(range(800)))
        return {k: state.summary(k) for k in keys if k in state}, state.evicted, state.expired

    whole = make()
    feed(whole, stream)
    for cut in (0, 3, 11):
        first = make()
        feed(first, stream[:cut])
        revived = KeyedSketches.restore(json.loads(json.dumps(first.snapshot())))
        assert len(revived) == len(first)
        feed(revived, stream[cut:])
        assert view(revived) == view(whole), f"restored after batch {cut}"


# ------------------------------------------------------------- at scale

_SCALE = """
import json, sys, time
import numpy as np
from shape.streaming.dedupe import Deduplicator
from shape.streaming.keyed import KeyedSketches

def peak_rss():
    if sys.platform.startswith("linux"):
        return int(open("/proc/self/status").read().split("VmHWM:")[1].split()[0]) * 1024
    if sys.platform == "darwin":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    import ctypes
    from ctypes import wintypes as w
    class C(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("peak", "ws", "a", "b", "c", "d", "pf", "ppf")]
    c = C(); c.cb = ctypes.sizeof(c)
    k = ctypes.windll.kernel32; k.GetCurrentProcess.restype = w.HANDLE
    p = ctypes.windll.psapi
    p.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
    assert p.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.peak

events, keys, batch = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
checkpoint = int(sys.argv[4])
rng = np.random.default_rng(7)
sketches = KeyedSketches(max_keys=keys + keys // 8, ttl=float(keys))  # TTL in event-time seconds
dedupe = Deduplicator(max_keys=keys + keys // 8)

def state_bytes():
    return json.dumps({
        "events": done,
        "live": len(sketches),
        "held": len(dedupe),
        "sketch_arrays": sketches.nbytes,
        "dedupe_arrays": dedupe.nbytes,
        "index": sys.getsizeof(sketches._index),
        "free_slots": len(sketches._free),
        "free_list": sys.getsizeof(sketches._free),
    })

done, at_checkpoint, kept = 0, None, 0
while done < events:
    n = min(batch, events - done)
    k = rng.integers(0, keys, n)
    t = done + np.arange(n, dtype=np.float64)  # one event per second: the TTL is a rolling window
    sketches.update(k, rng.random(n), t)
    kept += int(dedupe.filter(k).sum())
    done += n
    if at_checkpoint is None and done >= checkpoint:
        at_checkpoint = peak_rss()
        print("checkpoint state: " + state_bytes(), file=sys.stderr)
print("final state: " + state_bytes(), file=sys.stderr)
print(at_checkpoint, peak_rss(), len(sketches), len(dedupe), kept)
"""


@pytest.mark.heavy
def test_memory_does_not_grow_after_ten_million_events():
    """Across 10^8 events and 10^6 keys, RSS grows at most 10% after the first 10^7 events.
    The per-key sketches (with a TTL) and the deduplicator are both under their caps."""
    r = subprocess.run(
        [sys.executable, "-c", _SCALE, str(10**8), str(10**6), str(2**20), str(10**7)],
        capture_output=True,
        text=True,
        check=True,
    )
    at_ten_million, final, live, held, kept = (int(x) for x in r.stdout.split())
    assert final <= at_ten_million * 1.10, (at_ten_million, final, r.stderr)
    assert live <= 1_125_000 and held <= 1_125_000 and 0 < kept <= 10**6
