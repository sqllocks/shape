"""P3-01: bounded-mode profile state snapshots. Rust and the Python twin share one format (the
two kernels accumulate floating-point moments in different orders, so equal input does not give
equal bytes across kernels), read each other's, and a restored state continues exactly as the
original would have."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pyarrow as pa
import pytest

from shape.kernel import dispatch, reference

_spec = importlib.util.spec_from_file_location(
    "_profile_kernel_fixtures", Path(__file__).with_name("test_profile_kernel.py")
)
assert _spec and _spec.loader
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
_same, _table = _fixtures._same, _fixtures._table


@pytest.fixture(scope="module")
def native():
    return dispatch._import_native()


def _feed(impl, table: pa.Table, batch_rows: int = 700, state=None):
    st = state or impl.ProfileState(table.schema, "bounded")
    for b in table.to_batches(max_chunksize=batch_rows):
        st.update(b)
    return st


def test_restore_gives_the_same_state_and_the_same_bytes(native):
    table = _table(5000, seed=6)
    for impl in (native, reference):
        st = _feed(impl, table)
        data = st.snapshot()
        back = impl.ProfileState.from_snapshot(table.schema, data)
        assert back.snapshot() == data
        assert back.rows == st.rows and back.mode == "bounded"
        _same(back.finalize(), st.finalize())


def test_each_kernel_restores_the_other_kernels_snapshot(native):
    table = _table(4000, seed=7)
    rust = _feed(native, table)
    py = _feed(reference, table)
    py_from_rust = reference.ProfileState.from_snapshot(table.schema, rust.snapshot())
    rust_from_py = native.ProfileState.from_snapshot(table.schema, py.snapshot())
    _same(py_from_rust.finalize(), rust.finalize())
    _same(rust_from_py.finalize(), py.finalize())
    # and a state restored across kernels keeps accumulating
    more = _table(900, seed=70)
    _feed(reference, more, 300, py_from_rust)
    _feed(native, more, 300, rust_from_py)
    _same(py_from_rust.finalize(), rust_from_py.finalize())


@pytest.mark.parametrize("cut", [400, 2000, 4800])
def test_a_restored_state_continues_exactly_like_an_uninterrupted_one(native, cut):
    # the cut sits on a batch boundary: the Python twin adds moments per batch
    table = _table(5000, seed=8)
    for impl in (native, reference):
        whole = _feed(impl, table, 400)
        first = _feed(impl, table.slice(0, cut), 400)
        resumed = impl.ProfileState.from_snapshot(table.schema, first.snapshot())
        _feed(impl, table.slice(cut), 400, resumed)
        assert resumed.snapshot() == whole.snapshot()
        _same(resumed.finalize(), whole.finalize())


def test_an_empty_state_round_trips(native):
    schema = _table(10).schema
    for impl in (native, reference):
        data = impl.ProfileState(schema, "bounded").snapshot()
        assert impl.ProfileState.from_snapshot(schema, data).snapshot() == data


def test_exact_mode_cannot_be_snapshotted(native):
    schema = _table(10).schema
    for impl in (native, reference):
        with pytest.raises(ValueError, match="bounded"):
            impl.ProfileState(schema, "exact").snapshot()


def test_bad_snapshots_are_rejected(native):
    table = _table(300, seed=9)
    good = _feed(native, table).snapshot()
    other = pa.schema([("x", pa.int64())])
    for impl in (native, reference):
        with pytest.raises(ValueError):
            impl.ProfileState.from_snapshot(table.schema, b"")
        with pytest.raises(ValueError, match="not a profile snapshot"):
            impl.ProfileState.from_snapshot(table.schema, b"XXXX" + good[4:])
        with pytest.raises(ValueError):
            impl.ProfileState.from_snapshot(table.schema, good[:-1])
        with pytest.raises(ValueError):
            impl.ProfileState.from_snapshot(table.schema, good + b"\0")
        with pytest.raises(ValueError):
            impl.ProfileState.from_snapshot(other, good)
