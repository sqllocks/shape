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
