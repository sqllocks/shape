"""Result dataclasses of the reference profiler (Spindle TableProfile field names)."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Output dataclasses (same field names as Spindle; raw-bearing fields are
# plain attributes here -- Spindle hides them behind InitVar/properties
# (ADR-007) but exposes the same values through .enum_values/.min_value/...)
# ---------------------------------------------------------------------------


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    null_count: int
    null_rate: float
    cardinality: int
    cardinality_ratio: float
    is_unique: bool
    is_enum: bool
    enum_values: dict[str, float] | None
    min_value: Any
    max_value: Any
    mean: float | None
    std: float | None
    distribution: str | None
    distribution_params: dict[str, float] | None
    pattern: str | None
    is_primary_key: bool
    is_foreign_key: bool
    fk_ref_table: str | None
    quantiles: dict[str, float] | None = None
    hour_histogram: list[float] | None = None
    dow_histogram: list[float] | None = None
    temporal_histogram: dict[str, Any] | None = None
    string_length: dict[str, float] | None = None
    outlier_rate: float | None = None
    value_counts_ext: dict[str, float] | None = None
    fit_score: float | None = None


@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: dict[str, ColumnProfile]
    primary_key: list[str]
    detected_fks: dict[str, str]
    correlation_matrix: dict[str, dict[str, float]] | None = None


@dataclass
class DatasetProfile:
    tables: dict[str, TableProfile]
    relationships: list[dict[str, Any]] = field(default_factory=list)


class Timedelta(_dt.timedelta):
    """Stand-in for pandas.Timedelta (same str()/type name) for min/max of duration columns."""

    def __str__(self) -> str:  # pandas: '<days> days HH:MM:SS[.ffffff]', days may be negative
        secs = self.seconds
        sign = "+" if self.days < 0 else ""
        text = f"{self.days} days {sign}{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
        return text + (f".{self.microseconds:06d}" if self.microseconds else "")


class Timestamp(_dt.datetime):
    """Stand-in for pandas.Timestamp (same str()/type name) for min/max of datetime64 columns."""

    def __str__(self) -> str:  # pandas prints 'YYYY-MM-DD HH:MM:SS[.ffffff]' -- same as datetime
        return _dt.datetime.__str__(self)


# ---------------------------------------------------------------------------
