"""Checkpoint state decompresses within a bound; a larger field is a corrupt checkpoint (#298)."""

from __future__ import annotations

import base64
import json
import zlib

import numpy as np
import pyarrow as pa
import pytest

from shape.streaming import runtime
from shape.streaming.dedupe import Deduplicator
from shape.streaming.keyed import KeyedSketches, _pack, _unpack
from shape.streaming.runtime import GlobalProfiler, restore_profiler

SCHEMA = pa.schema([("x", pa.int64()), ("_shape_event_time", pa.timestamp("us", tz="UTC"))])
MB = 1 << 20


def _bomb(nbytes: int) -> str:
    """A field of a few kilobytes that decompresses to ``nbytes`` zero bytes."""
    return base64.b64encode(zlib.compress(bytes(nbytes), 9)).decode("ascii")


def _keyed(n: int = 3) -> KeyedSketches:
    s = KeyedSketches(max_keys=10, ttl=None, distinct=False)
    s.update(
        np.arange(n, dtype=np.int64), np.arange(n, dtype=np.float64), np.arange(n, dtype=float)
    )
    return s


def test_pack_unpack_round_trips_and_accepts_the_exact_bound():
    a = np.arange(7, dtype=np.int64)
    assert _unpack(_pack(a), np.int64, 7).tolist() == a.tolist()
    assert _unpack(_pack(a[:0]), np.int64, 0).tolist() == []


def test_unpack_refuses_one_item_over_the_bound():
    a = np.arange(8, dtype=np.int64)
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        _unpack(_pack(a), np.int64, 7)


def test_unpack_refuses_a_decompression_bomb():
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        _unpack(_bomb(64 * MB), np.uint8, 1000)


def test_unpack_refuses_garbage_and_a_truncated_stream():
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        _unpack("not base64 or zlib!!", np.int64, 10)
    full = base64.b64decode(_pack(np.arange(100, dtype=np.int64)))
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        _unpack(base64.b64encode(full[: len(full) // 2]).decode(), np.int64, 100)


def test_unpack_refuses_a_length_that_is_not_a_whole_number_of_items():
    text = base64.b64encode(zlib.compress(b"\x00" * 9)).decode()
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        _unpack(text, np.int64, 10)


def test_keyed_restore_refuses_an_oversized_array_field():
    snap = json.loads(json.dumps(_keyed().snapshot()))
    assert KeyedSketches.restore(snap).events == 3
    snap["arrays"]["count"] = _bomb(64 * MB)
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        KeyedSketches.restore(snap)


def test_keyed_restore_refuses_an_array_longer_than_the_declared_live_count():
    snap = json.loads(json.dumps(_keyed(3).snapshot()))
    snap["arrays"]["count"] = _pack(np.arange(4, dtype=np.int64))
    with pytest.raises(ValueError, match="corrupt checkpoint|entries"):
        KeyedSketches.restore(snap)


def test_dedupe_restore_refuses_an_oversized_run_field():
    d = Deduplicator(10)
    d.filter(np.arange(5, dtype=np.int64))
    snap = json.loads(json.dumps(d.snapshot()))
    assert Deduplicator.restore(snap).rows == 5
    snap["runs"][0]["keys"] = _bomb(64 * MB)
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        Deduplicator.restore(snap)


def test_dedupe_restore_accepts_a_run_of_exactly_max_keys_and_refuses_one_more():
    d = Deduplicator(5)
    d.filter(np.arange(5, dtype=np.int64))
    snap = json.loads(json.dumps(d.snapshot()))
    assert len(Deduplicator.restore(snap)) == 5
    over = np.arange(6, dtype=np.int64)
    snap["runs"][0] = {"keys": _pack(over), "seq": _pack(over), "time": _pack(over.astype(float))}
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        Deduplicator.restore(snap)


def test_window_state_restore_refuses_an_oversized_state(monkeypatch):
    snap = json.loads(json.dumps(GlobalProfiler(SCHEMA).snapshot()))
    assert restore_profiler(snap).rows_in == 0
    monkeypatch.setattr(runtime, "MAX_STATE_BYTES", 1 * MB)
    snap["state"]["state"] = _bomb(8 * MB)
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        restore_profiler(snap)


def test_checkpoints_written_before_the_bound_still_restore():
    """Compatibility: fields written by the old unbounded ``_pack`` (same format) restore."""
    legacy = base64.b64encode(zlib.compress(np.arange(4, dtype="<i8").tobytes(), 6)).decode()
    assert _unpack(legacy, np.int64, 4).tolist() == [0, 1, 2, 3]
    snap = json.loads(json.dumps(_keyed(4).snapshot()))
    assert snap["format"] == "shape-keyed-sketches-v1"
    assert KeyedSketches.restore(snap).events == 4
