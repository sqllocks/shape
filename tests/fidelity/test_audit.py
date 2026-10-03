"""AUD-chaos regressions for the fidelity tiers."""

from __future__ import annotations

import pyarrow as pa

from shape.fidelity.tier3 import DriftMonitor, psi_report

_INF = pa.table({"x": [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, float("inf")]})
_FINITE = pa.table({"x": [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]})


def test_psi_report_fails_closed_on_an_infinite_value() -> None:
    """#404: a column whose PSI cannot be computed is an error and drifted, not a pass."""
    col = psi_report(_INF, _FINITE).columns["x"]
    assert (col.method, col.is_drifted) == ("error", True)


def test_drift_monitor_fails_closed_on_an_infinite_value() -> None:
    """#404: the KS path's PSI signal is NaN too; the column fails closed."""
    col = DriftMonitor().compare(_INF, _FINITE).columns["x"]
    assert (col.method, col.is_drifted) == ("error", True)


def test_tiers_read_a_table_with_duplicate_column_names() -> None:
    """#423: the frame reads columns by position, so a repeated name is no KeyError."""
    from shape.fidelity._frame import Frame
    from shape.fidelity.tier2 import run_tier2

    t = pa.table([pa.array(["a"] * 20), pa.array(["b"] * 20)], names=["c", "c"])
    frame = Frame.from_arrow(t)
    assert frame.names == ["c", "c"]
    assert run_tier2(t, t).cardinality["c"].passed


def test_bootstrap_table_names_a_negative_n_rows() -> None:
    """#427: the error names n_rows."""
    import pytest

    from shape.fidelity.tier3 import bootstrap_table

    with pytest.raises(ValueError, match=r"n_rows is 0 or more, got -1"):
        bootstrap_table(pa.table({"x": [1, 2, 3]}), n_rows=-1)


def test_frame_refuses_a_timestamp_outside_the_nanosecond_range() -> None:
    """#555: the conversion to nanoseconds never wraps; the error names the column."""
    import pytest

    from shape.fidelity._frame import Frame

    with pytest.raises(ValueError, match=r"column 't'.*outside the nanosecond range"):
        Frame.from_arrow(pa.table({"t": pa.array([2**62], pa.timestamp("s"))}))
    ok = Frame.from_arrow(pa.table({"t": pa.array([10**9, None], pa.timestamp("s"))}))["t"]
    assert ok.values.tolist() == [10**18, 0]
    assert ok.valid.tolist() == [True, False]
