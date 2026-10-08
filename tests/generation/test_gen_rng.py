from __future__ import annotations

import numpy as np
import pytest

from shape.generation.rng import RowStream, normal_from_raw, stream_key, uniform_from_raw


def test_words_are_the_numpy_philox_known_answers():
    s = RowStream(7, "t", "c")
    key = stream_key(7, "t", "c")
    oracle = np.random.Philox(key=key, counter=0).random_raw(64)
    assert (s.raw(0, 64)[:, 0] == oracle).all()
    # block j of the stream is Philox(key, counter=j)
    for j in (1, 5, 1000):
        block = np.random.Philox(key=key, counter=j).random_raw(4)
        assert (s.raw(4 * j, 4)[:, 0] == block).all()


def test_rows_with_several_words():
    s = RowStream(1, "t", "c")
    flat = s.raw(0, 30)[:, 0]
    wide = s.raw(0, 10, per_row=3)
    assert (wide.reshape(-1) == flat).all()
    assert (s.raw(4, 3, per_row=3).reshape(-1) == flat[12:21]).all()


@pytest.mark.parametrize("chunk", [1, 7, 64, 1000])
def test_independent_of_chunk_layout(chunk):
    s = RowStream(3, "orders", "price", "v")
    whole = s.uniform(0, 5000)
    parts = np.concatenate([s.uniform(a, min(chunk, 5000 - a)) for a in range(0, 5000, chunk)])
    assert (whole == parts).all()


def test_streams_are_separate():
    keys = {
        stream_key(1, "t", "c"),
        stream_key(2, "t", "c"),
        stream_key(1, "u", "c"),
        stream_key(1, "t", "d"),
        stream_key(1, "t", "c", "null"),
        stream_key(-1, "t", "c"),
    }
    assert len(keys) == 6
    assert stream_key(1, "t", "c") == stream_key(1, "t", "c")  # deterministic


def test_distributions():
    s = RowStream(11, "t", "c")
    u = s.uniform(0, 200_000)
    assert 0.0 <= u.min() and u.max() < 1.0
    assert abs(u.mean() - 0.5) < 0.01
    z = s.normal(0, 200_000)
    assert np.isfinite(z).all()
    assert abs(z.mean()) < 0.01 and abs(z.std() - 1.0) < 0.01
    assert uniform_from_raw(np.array([0], dtype=np.uint64))[0] == 0.0
    assert normal_from_raw(np.array([[2**64 - 1, 0]], dtype=np.uint64)).shape == (1,)


def test_empty_and_invalid_reads():
    s = RowStream(1, "t", "c")
    assert s.raw(5, 0, per_row=2).shape == (0, 2)
    with pytest.raises(ValueError):
        s.raw(-1, 3)
    with pytest.raises(ValueError):
        s.raw(0, 3, per_row=0)
