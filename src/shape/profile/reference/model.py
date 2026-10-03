"""Result dataclasses of the reference profiler (field names of the profile JSON)."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Output dataclasses (raw-bearing fields are plain attributes here and are read through
# .enum_values/.min_value/...)
# ---------------------------------------------------------------------------


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    null_count: int
    null_rate: float | None  # None: unknown (no rows were read), never a made-up 0.0
    cardinality: int
    cardinality_ratio: float | None
    is_unique: bool | None
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
    nan_count: int = 0  # float values that are NaN (not counted as nulls)
    inf_count: int = 0  # float values that are +inf or -inf
    precision: int | None = None  # decimal columns: the declared precision and scale (#24)
    scale: int | None = None
    pattern_rates: dict[str, float] | None = None  # share of values that are wholly a pattern
    pattern_contains_rates: dict[str, float] | None = None  # share that contain an SSN/email/card
    placeholders: list[dict[str, Any]] | None = None  # sentinel values and their evidence (#47)
    # univariate depth (W3-07): distribution_candidates, distribution_by_bic, zero_share,
    # zero_inflation, heaping, benford, tail_index; only the ones that apply are present
    univariate: dict[str, Any] | None = None


@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: dict[str, ColumnProfile]
    primary_key: list[str]
    detected_fks: dict[str, str]
    correlation_matrix: dict[str, dict[str, float]] | None = None
    correlation_truncated: bool = False  # only each column's strongest pairs are kept (#37)
    joint: dict[str, Any] | None = None  # dependencies, keys, associations (#47)


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
