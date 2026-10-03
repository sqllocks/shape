"""Helpers shared by the X12 tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shape_healthcare_standards.x12.sink import (
    X12Claim837ISink,
    X12Claim837PSink,
    X12Enrollment834Sink,
    X12Remittance835Sink,
)

ALL_SINKS = [X12Claim837PSink, X12Claim837ISink, X12Remittance835Sink, X12Enrollment834Sink]


def run(sink_cls: Any, tables: Any, tmp_path: Path, **options: Any) -> list[Path]:
    """Write one format from the tables into ``tmp_path`` and return the files."""
    sink = sink_cls()
    batches = tables[sink.primary].to_batches()
    sink.write(str(tmp_path), sink.primary, batches, tables=tables, **options)
    return sorted(tmp_path.glob("*.x12"))
