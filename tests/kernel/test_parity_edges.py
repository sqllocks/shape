"""Native and reference kernels agree on the edge inputs of #552."""

from __future__ import annotations

import pyarrow as pa
import pytest

from shape.kernel import dispatch, reference


@pytest.fixture(scope="module")
def native():
    return dispatch._import_native()


def _both(native):
    return [native, reference]


def test_a_null_dictionary_value_counts_as_null(native):
    arr = pa.DictionaryArray.from_arrays(pa.array([0, 1, 0]), pa.array(["x", None]))
    batch = pa.record_batch({"a": arr})
    counts = []
    for mod in _both(native):
        state = mod.ProfileState(batch.schema, "exact")
        state.update(batch)
        counts.append(state.finalize()["columns"][0]["null_count"])
    assert counts == [1, 1]


def test_native_temporal_counts_refuses_a_time_zone(native):
    # The native kernel ignored the zone (UTC bins); callers send zoned data to the twin.
    with pytest.raises(ValueError, match="time zone"):
        native.temporal_counts(pa.array([0, 3600], pa.timestamp("s", tz="America/New_York")))


def test_space_saving_counts_saturate_instead_of_wrapping(native):
    for mod in _both(native):
        s = mod.SpaceSaving(2)
        s.update(1, 2**64 - 1)
        s.update(1, 2)
        assert tuple(s.top()[0][:2]) == (1, 2**64 - 1)
        assert s.n == 2**64 - 1


@pytest.mark.parametrize(
    ("values", "top_n"), [([], 3), ([1.0, 2.0, 2.0], 0), ([-(2**63), 2**63 - 1, 0], 3)]
)
def test_count_numeric_edges_agree(native, values, top_n):
    typ = pa.int64() if values and isinstance(values[0], int) else pa.float64()
    got = []
    for mod in _both(native):
        out = mod.count_numeric(pa.array(values, typ), top_n, len(values))
        got.append(
            (out["cardinality"], out["all_whole"], pa.array(out["keys"]).to_pylist(),
             pa.array(out["counts"]).to_pylist())
        )  # fmt: skip
    assert got[0] == got[1]


@pytest.mark.parametrize(
    "call",
    [
        lambda m: m.SpaceSaving(-1),
        lambda m: m.SpaceSaving(4).update(1, -5),
        lambda m: m.Hll(14).update_hash(-1),
        lambda m: m.top_indices(pa.array([3, 1, 2]), -1),
        lambda m: m.ProfileState(pa.schema([("a", pa.int64())])).finalize(-1),
    ],
    ids=["capacity", "count", "hash", "need", "top_n"],
)
def test_negative_unsigned_arguments_are_refused_alike(native, call):
    for mod in _both(native):
        with pytest.raises(OverflowError, match="negative"):
            call(mod)


@pytest.mark.parametrize(
    "make",
    [
        lambda m: m.Hll(14),
        lambda m: m.Kll(200),
        lambda m: m.SpaceSaving(8),
        lambda m: m.ProfileState(pa.schema([("a", pa.int64())])),
    ],
    ids=["hll", "kll", "space_saving", "profile_state"],
)
def test_merging_a_state_into_itself_is_a_value_error_in_both(native, make):
    # Native raised RuntimeError("Already mutably borrowed"); the twin merged.
    for mod in _both(native):
        state = make(mod)
        with pytest.raises(ValueError, match="itself"):
            state.merge(state)
