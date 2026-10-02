"""Fidelity reports: score synthetic tables against reference tables, and render the result."""

from __future__ import annotations

from .compare import (
    ColumnFidelity,
    FidelityReport,
    TableFidelity,
    Thresholds,
    compare_column,
    compare_table,
    compare_tables,
)
from .render import render_report

__all__ = [
    "ColumnFidelity",
    "FidelityReport",
    "TableFidelity",
    "Thresholds",
    "compare_column",
    "compare_table",
    "compare_tables",
    "render_report",
]
