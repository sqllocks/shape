"""Validation gates: reusable checks of generated or ingested tables.

Each gate takes a :class:`ValidationContext` (Arrow tables, a gate schema, file paths, a config
dict) and returns a :class:`GateResult` with errors, warnings and details. A gate fails only on
errors. :class:`GateRunner` runs a chosen set of gates, or all nine.

Column types are named with numpy-style dtype names (``int64``, ``float64``, ``str``, ``bool``,
``datetime64[us]``, ``object`` for everything else), the vocabulary ``schema_drift`` baselines
use.
"""

from __future__ import annotations

import importlib
import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .gatespec import GateSchema

_DISTRIBUTION_MIN_SAMPLE = 20


@dataclass
class ValidationContext:
    """What every gate receives. ``config`` keys: ``ranges``, ``date_range``, ``no_future``,
    ``ordering``, ``baseline``, ``distribution_alpha`` (see each gate)."""

    tables: dict[str, pa.Table] = field(default_factory=dict)
    schema: GateSchema | None = None
    file_paths: list[Path] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResult:
    gate_name: str
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"GateResult({self.gate_name}: {status}, {len(self.errors)} errors, "
            f"{len(self.warnings)} warnings)"
        )


class ValidationGate(ABC):
    name: str = "base"

    @abstractmethod
    def check(self, context: ValidationContext) -> GateResult:
        """Run this gate's checks against ``context``."""


# ---------------------------------------------------------------------------------------------
# Arrow helpers
# ---------------------------------------------------------------------------------------------


def dtype_name(t: pa.DataType) -> str:
    """The numpy-style name of an Arrow type (``object`` when numpy has no matching dtype)."""
    if pa.types.is_dictionary(t):
        return dtype_name(t.value_type)
    if pa.types.is_integer(t):
        return str(t)
    if pa.types.is_floating(t):
        return f"float{t.bit_width}"
    if pa.types.is_boolean(t):
        return "bool"
    if pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_string_view(t):
        return "str"
    if pa.types.is_timestamp(t):
        return f"datetime64[{t.unit}, {t.tz}]" if t.tz else f"datetime64[{t.unit}]"
    if pa.types.is_duration(t):
        return f"timedelta64[{t.unit}]"
    return "object"


def _column(table: pa.Table, name: str) -> pa.ChunkedArray:
    return table.column(name)


def _missing(col: pa.ChunkedArray) -> int:
    """Nulls plus NaNs: what a missing-value check counts."""
    n = int(col.null_count)
    if pa.types.is_floating(col.type):
        nans = pc.sum(pc.is_nan(col)).as_py()
        n += int(nans or 0)
    return n


def _present(col: pa.ChunkedArray) -> pa.ChunkedArray:
    """The column without nulls and NaNs."""
    out = col.drop_null()
    if pa.types.is_floating(out.type):
        out = out.filter(pc.invert(pc.is_nan(out)))
    return out


def _count_duplicates(table: pa.Table, columns: list[str]) -> int:
    """Rows that repeat an earlier row on ``columns`` (nulls compare equal)."""
    n = table.num_rows
    if len(columns) == 1:
        return int(n - pc.count_distinct(table.column(columns[0]), mode="all").as_py())
    groups = table.select(columns).group_by(columns, use_threads=False).aggregate([])
    return int(n - groups.num_rows)


def _to_floats(col: pa.ChunkedArray) -> np.ndarray[Any, np.dtype[np.float64]]:
    """Numeric values of ``col`` as floats, with what is not a number dropped (text is parsed
    where it can be)."""
    t = col.type
    try:
        if (
            pa.types.is_integer(t)
            or pa.types.is_floating(t)
            or pa.types.is_boolean(t)
            or (pa.types.is_decimal(t))
        ):
            arr = pc.cast(col, pa.float64(), safe=False)
        elif pa.types.is_string(t) or pa.types.is_large_string(t):
            vals: list[float] = []
            for v in col.to_pylist():
                if v is None:
                    continue
                try:
                    vals.append(float(v))
                except ValueError:
                    continue
            return _drop_nan(np.asarray(vals, dtype=np.float64))
        else:
            return np.empty(0, dtype=np.float64)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
        return np.empty(0, dtype=np.float64)
    return _drop_nan(arr.drop_null().to_numpy(zero_copy_only=False))


def _drop_nan(a: np.ndarray[Any, np.dtype[np.float64]]) -> np.ndarray[Any, np.dtype[np.float64]]:
    return np.asarray(a[~np.isnan(a)], dtype=np.float64)


def _no_schema(name: str, what: str, *, passed: bool = False) -> GateResult:
    if passed:
        return GateResult(name, True, warnings=[f"No schema provided — {what} skipped"])
    return GateResult(name, False, errors=[f"No schema provided — cannot check {what}"])


# ---------------------------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------------------------


class ReferentialIntegrityGate(ValidationGate):
    """Every foreign-key value of a child column exists in the parent's key column."""

    name = "referential_integrity"

    def check(self, context: ValidationContext) -> GateResult:
        if context.schema is None:
            return _no_schema(self.name, "referential integrity")
        errors: list[str] = []
        warnings: list[str] = []
        orphan_counts: dict[str, int] = {}
        for rel in context.schema.relationships:
            if rel.type == "self_referencing":
                continue
            if rel.parent not in context.tables or rel.child not in context.tables:
                warnings.append(f"Skipping relationship '{rel.name}': missing table(s) in context")
                continue
            parent = context.tables[rel.parent]
            child = context.tables[rel.child]
            for p_col, c_col in zip(rel.parent_columns, rel.child_columns, strict=False):
                if p_col not in parent.column_names:
                    errors.append(f"Parent column '{rel.parent}.{p_col}' not found in DataFrame")
                    continue
                if c_col not in child.column_names:
                    errors.append(f"Child column '{rel.child}.{c_col}' not found in DataFrame")
                    continue
                orphans = _count_orphans(_present(child.column(c_col)), parent.column(p_col))
                orphan_counts[f"{rel.child}.{c_col}->{rel.parent}.{p_col}"] = orphans
                if orphans > 0:
                    errors.append(
                        f"{rel.child}.{c_col} has {orphans:,} orphan FK values "
                        f"not found in {rel.parent}.{p_col}"
                    )
        return GateResult(
            self.name,
            not errors,
            errors,
            warnings,
            {"orphan_counts": orphan_counts},
        )


def _comparable(a: pa.DataType, b: pa.DataType) -> bool:
    """True when equal values of the two types compare equal after a cast, so an Arrow lookup
    answers what Python equality would: numbers with numbers, text with text, or the same
    type. A key stored as text never matches the same key stored as a number."""
    numeric = (pa.types.is_integer, pa.types.is_floating)
    if any(f(a) for f in numeric) and any(f(b) for f in numeric):
        return True
    texts = (pa.types.is_string, pa.types.is_large_string)
    if any(f(a) for f in texts) and any(f(b) for f in texts):
        return True
    return bool(a == b)


def _count_orphans(child_values: pa.ChunkedArray, parent_keys: pa.ChunkedArray) -> int:
    if len(child_values) == 0:
        return 0
    if _comparable(child_values.type, parent_keys.type):
        try:
            keys = pc.cast(parent_keys, child_values.type)
            found = pc.is_in(child_values, value_set=keys.combine_chunks())
            return int(len(child_values) - pc.sum(found).as_py())
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
            pass
    parents = set(parent_keys.to_pylist())
    return sum(1 for v in child_values.to_pylist() if v not in parents)


# Expected schema type -> the dtype-name fragments that satisfy it.
_TYPE_MAP: dict[str, tuple[str, ...]] = {
    "integer": ("int", "Int"),
    "bigint": ("int", "Int"),
    "string": ("object", "string", "str"),
    "float": ("float", "Float"),
    "decimal": ("float", "Float", "object"),
    "date": ("datetime", "object", "date"),
    "datetime": ("datetime", "object"),
    "boolean": ("bool", "Bool", "object"),
    "uuid": ("object", "string", "str"),
}


class SchemaConformanceGate(ValidationGate):
    """Expected tables and columns exist (errors), no extra columns (warning), and column
    types fit the declared ones (warning)."""

    name = "schema_conformance"

    def check(self, context: ValidationContext) -> GateResult:
        schema = context.schema
        if schema is None:
            return _no_schema(self.name, "conformance")
        errors: list[str] = []
        warnings: list[str] = []
        details: dict[str, Any] = {}
        for tname, tdef in schema.tables.items():
            if tname not in context.tables:
                errors.append(f"Expected table '{tname}' not found in data")
                continue
            table = context.tables[tname]
            expected = set(tdef.columns)
            actual = set(table.column_names)
            missing = expected - actual
            extra = actual - expected
            if missing:
                errors.append(f"Table '{tname}' missing columns: {sorted(missing)}")
            if extra:
                warnings.append(f"Table '{tname}' has unexpected columns: {sorted(extra)}")
            details[tname] = {
                "expected_columns": sorted(expected),
                "actual_columns": sorted(actual),
                "missing": sorted(missing),
                "extra": sorted(extra),
            }
            for cname, cdef in tdef.columns.items():
                if cname not in actual:
                    continue
                want = cdef.type.lower()
                compatible = _TYPE_MAP.get(want)
                if compatible is None:
                    continue
                got = dtype_name(table.schema.field(cname).type)
                if not any(t in got for t in compatible):
                    warnings.append(
                        f"Table '{tname}' column '{cname}': expected type compatible with "
                        f"'{want}', got '{got}'"
                    )
        return GateResult(self.name, not errors, errors, warnings, details)


class NullConstraintGate(ValidationGate):
    """Columns declared non-nullable hold no nulls."""

    name = "null_constraint"

    def check(self, context: ValidationContext) -> GateResult:
        if context.schema is None:
            return _no_schema(self.name, "null constraints")
        errors: list[str] = []
        details: dict[str, Any] = {}
        for tname, tdef in context.schema.tables.items():
            if tname not in context.tables:
                continue
            table = context.tables[tname]
            nulls: dict[str, int] = {}
            for cname, cdef in tdef.columns.items():
                if cname not in table.column_names or cdef.nullable:
                    continue
                n = _missing(_column(table, cname))
                if n > 0:
                    nulls[cname] = n
                    errors.append(
                        f"Table '{tname}' column '{cname}' is non-nullable "
                        f"but has {n:,} null values"
                    )
            if nulls:
                details[tname] = nulls
        return GateResult(self.name, not errors, errors, details=details)


class UniqueConstraintGate(ValidationGate):
    """Primary-key columns hold no duplicates."""

    name = "unique_constraint"

    def check(self, context: ValidationContext) -> GateResult:
        if context.schema is None:
            return _no_schema(self.name, "unique constraints")
        errors: list[str] = []
        details: dict[str, Any] = {}
        for tname, tdef in context.schema.tables.items():
            if tname not in context.tables or not tdef.primary_key:
                continue
            table = context.tables[tname]
            pk = [c for c in tdef.primary_key if c in table.column_names]
            if not pk:
                continue
            dups = _count_duplicates(table, pk)
            if dups == 0:
                continue
            if len(pk) == 1:
                errors.append(f"Table '{tname}' PK column '{pk[0]}' has {dups:,} duplicate values")
                details[tname] = {"column": pk[0], "duplicates": dups}
            else:
                errors.append(f"Table '{tname}' composite PK {pk} has {dups:,} duplicate rows")
                details[tname] = {"columns": pk, "duplicates": dups}
        return GateResult(self.name, not errors, errors, details=details)


class RangeConstraintGate(ValidationGate):
    """Numeric columns stay within ``config["ranges"]``: ``{"table.column": {"min": 0,
    "max": 100}}``."""

    name = "range_constraint"

    def check(self, context: ValidationContext) -> GateResult:
        ranges: dict[str, dict[str, float]] = context.config.get("ranges", {})
        if not ranges:
            return GateResult(
                self.name, True, warnings=["No range constraints configured — nothing to check"]
            )
        errors: list[str] = []
        warnings: list[str] = []
        details: dict[str, Any] = {}
        for key, bounds in ranges.items():
            parts = key.split(".", 1)
            if len(parts) != 2:
                warnings.append(f"Invalid range key '{key}' — expected 'table.column'")
                continue
            tname, cname = parts
            if tname not in context.tables:
                warnings.append(f"Table '{tname}' not found in data")
                continue
            table = context.tables[tname]
            if cname not in table.column_names:
                warnings.append(f"Column '{cname}' not found in table '{tname}'")
                continue
            values = _to_floats(_column(table, cname))
            if values.size == 0:
                continue
            lo, hi = float(values.min()), float(values.max())
            info: dict[str, Any] = {"actual_min": lo, "actual_max": hi}
            min_bound = bounds.get("min")
            max_bound = bounds.get("max")
            if min_bound is not None:
                below = int((values < min_bound).sum())
                if below > 0:
                    errors.append(
                        f"{tname}.{cname}: {below:,} values below minimum {min_bound} "
                        f"(actual min: {lo})"
                    )
                    info["below_min"] = below
            if max_bound is not None:
                above = int((values > max_bound).sum())
                if above > 0:
                    errors.append(
                        f"{tname}.{cname}: {above:,} values above maximum {max_bound} "
                        f"(actual max: {hi})"
                    )
                    info["above_max"] = above
            details[key] = info
        return GateResult(self.name, not errors, errors, warnings, details)


def _timestamp_scalar(value: Any, type_: pa.DataType) -> pa.Scalar:
    """``value`` (an ISO string or datetime) as a scalar comparable with a timestamp column."""
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if type_.tz is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return pa.scalar(dt, pa.timestamp("us", tz=type_.tz)).cast(type_)


class TemporalConsistencyGate(ValidationGate):
    """Datetime columns lie in ``config["date_range"]`` (``{"start", "end"}``), the columns of
    ``config["no_future"]`` (``"table.column"``) hold no future dates, and each
    ``config["ordering"]`` rule (``{"table", "start", "end"}``) has ``end >= start``."""

    name = "temporal_consistency"

    def check(self, context: ValidationContext) -> GateResult:
        errors: list[str] = []
        warnings: list[str] = []
        details: dict[str, Any] = {}
        config = context.config
        date_range = config.get("date_range", {})

        if date_range:
            checked = 0
            for tname, table in context.tables.items():
                for cname in table.column_names:
                    col = _column(table, cname)
                    if not pa.types.is_timestamp(col.type):
                        continue
                    checked += 1
                    col = col.drop_null()
                    if len(col) == 0:
                        continue
                    key = f"{tname}.{cname}"
                    if "start" in date_range:
                        before = _count_true(
                            pc.less(col, _timestamp_scalar(date_range["start"], col.type))
                        )
                        if before > 0:
                            errors.append(f"{key}: {before:,} dates before {date_range['start']}")
                            details.setdefault(key, {})["before_range"] = before
                    if "end" in date_range:
                        after = _count_true(
                            pc.greater(col, _timestamp_scalar(date_range["end"], col.type))
                        )
                        if after > 0:
                            errors.append(f"{key}: {after:,} dates after {date_range['end']}")
                            details.setdefault(key, {})["after_range"] = after
            if not checked:
                warnings.append(
                    "date_range checked nothing: no column has a timestamp type "
                    "(CSV and JSONL dates load as text; convert them first)"
                )

        for spec in config.get("no_future", []):
            parts = spec.split(".", 1)
            if len(parts) != 2:
                continue
            tname, cname = parts
            if tname not in context.tables or cname not in context.tables[tname].column_names:
                continue
            col = _column(context.tables[tname], cname)
            if not pa.types.is_timestamp(col.type):
                warnings.append(f"{spec}: not a timestamp column ({col.type}); not checked")
                continue
            now = datetime.now(UTC) if col.type.tz else datetime.now()
            future = _count_true(pc.greater(col.drop_null(), _timestamp_scalar(now, col.type)))
            if future > 0:
                errors.append(f"{spec}: {future:,} values are in the future")
                details.setdefault(spec, {})["future_dates"] = future

        for rule in config.get("ordering", []):
            tname = rule.get("table", "")
            start_col = rule.get("start", "")
            end_col = rule.get("end", "")
            if tname not in context.tables:
                warnings.append(f"Table '{tname}' not found for ordering check")
                continue
            table = context.tables[tname]
            if start_col not in table.column_names or end_col not in table.column_names:
                warnings.append(f"Columns '{start_col}'/'{end_col}' not found in '{tname}'")
                continue
            both = pc.and_(
                pc.is_valid(_column(table, start_col)), pc.is_valid(_column(table, end_col))
            )
            subset = table.filter(both)
            try:
                violations = _count_true(pc.less(subset.column(end_col), subset.column(start_col)))
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
                warnings.append(
                    f"Cannot compare '{start_col}' and '{end_col}' in '{tname}' "
                    "— incompatible types"
                )
                continue
            if violations > 0:
                errors.append(f"{tname}: {violations:,} rows where '{end_col}' < '{start_col}'")
                details[f"{tname}.{end_col}<{start_col}"] = violations
        return GateResult(self.name, not errors, errors, warnings, details)


def _count_true(mask: Any) -> int:
    return int(pc.sum(pc.cast(mask, pa.int64())).as_py() or 0)


def _read_file_rows(path: Path) -> tuple[int, int]:
    """Read a whole data file and return ``(rows, columns)``; raises when it is unreadable."""
    from shape.io import CsvOptions, read_table

    suffix = path.suffix.lower()
    if suffix == ".tsv":
        table = read_table(path, csv=CsvOptions(delimiter="\t"))
    else:
        table = read_table(path)
    return table.num_rows, table.num_columns


class FileFormatGate(ValidationGate):
    """Output files exist, are not empty, and read in full (parquet, csv, tsv, jsonl)."""

    name = "file_format"

    def check(self, context: ValidationContext) -> GateResult:
        if not context.file_paths:
            return GateResult(
                self.name, True, warnings=["No file paths provided — nothing to check"]
            )
        errors: list[str] = []
        warnings: list[str] = []
        details: dict[str, Any] = {}
        for file_path in context.file_paths:
            path = Path(file_path)
            key = str(path)
            if not path.exists():
                errors.append(f"File not found: {key}")
                continue
            if path.stat().st_size == 0:
                errors.append(f"File is empty (0 bytes): {key}")
                continue
            suffix = path.suffix.lower()
            info: dict[str, Any] = {"size_bytes": path.stat().st_size, "format": suffix}
            if suffix not in (".parquet", ".csv", ".tsv", ".jsonl"):
                warnings.append(f"Unknown file format '{suffix}' for {key}")
                details[key] = info
                continue
            try:
                info["rows"], info["columns"] = _read_file_rows(path)
                info["readable"] = True
            except Exception as exc:  # noqa: BLE001 - any reader failure means unreadable
                errors.append(f"Failed to read {key}: {exc}")
                info["readable"] = False
            details[key] = info
        return GateResult(self.name, not errors, errors, warnings, details)


class SchemaDriftGate(ValidationGate):
    """Compare the tables with ``config["baseline"]`` (``{"table": {"columns": {"col":
    "int64"}}}``): new tables and columns are additive (warnings); removed tables or columns and
    changed types are breaking (errors)."""

    name = "schema_drift"

    def check(self, context: ValidationContext) -> GateResult:
        baseline: dict[str, dict[str, Any]] = context.config.get("baseline", {})
        if not baseline:
            return GateResult(
                self.name, True, warnings=["No baseline schema configured — nothing to check"]
            )
        errors: list[str] = []
        warnings: list[str] = []
        additive: list[str] = []
        breaking: list[str] = []

        def add(change: str, *, is_breaking: bool) -> None:
            (errors if is_breaking else warnings).append(change)
            (breaking if is_breaking else additive).append(change)

        for tname in baseline:
            if tname not in context.tables:
                add(f"Table '{tname}' removed", is_breaking=True)
        for tname, table in context.tables.items():
            if tname not in baseline:
                add(f"New table '{tname}' added", is_breaking=False)
                continue
            base_cols: dict[str, str] = baseline[tname].get("columns", {})
            actual = {f.name: dtype_name(f.type) for f in table.schema}
            for cname in base_cols:
                if cname not in actual:
                    add(f"Table '{tname}': column '{cname}' removed", is_breaking=True)
            for cname in actual:
                if cname not in base_cols:
                    add(f"Table '{tname}': new column '{cname}'", is_breaking=False)
            for cname, was in base_cols.items():
                if cname in actual and actual[cname] != was:
                    add(
                        f"Table '{tname}': column '{cname}' type changed "
                        f"from '{was}' to '{actual[cname]}'",
                        is_breaking=True,
                    )
        return GateResult(
            self.name,
            not errors,
            errors,
            warnings,
            {"additive": additive, "breaking": breaking},
        )


def _scipy_stats() -> Any:
    try:
        return importlib.import_module("scipy.stats")
    except ImportError:
        return None


class DistributionGate(ValidationGate):
    """Columns follow the distribution their schema declares. A column with a ``distribution``
    is tested against that ``scipy.stats`` distribution (Kolmogorov-Smirnov); a column with an
    ``enum`` has its category frequencies tested against the declared weights (chi-squared).
    A p-value below ``config["distribution_alpha"]`` (default 0.05) is a warning, never an
    error. Needs scipy, and skips with a warning when it is missing."""

    name = "distribution"

    def check(self, context: ValidationContext) -> GateResult:
        if context.schema is None:
            return _no_schema(self.name, "distribution checks", passed=True)
        stats = _scipy_stats()
        if stats is None:
            return GateResult(
                self.name,
                True,
                warnings=[
                    "scipy is not installed — distribution checks skipped. "
                    "Install with: pip install sqllocks-shape[scipy]"
                ],
            )
        alpha: float = context.config.get("distribution_alpha", 0.05)
        warnings: list[str] = []
        details: dict[str, Any] = {}
        for tname, tdef in context.schema.tables.items():
            if tname not in context.tables:
                continue
            table = context.tables[tname]
            for cname, cdef in tdef.columns.items():
                if cname not in table.column_names:
                    continue
                key = f"{tname}.{cname}"
                if cdef.distribution is not None:
                    self._ks(
                        stats,
                        key,
                        cdef.distribution,
                        _column(table, cname),
                        alpha,
                        warnings,
                        details,
                    )
                elif cdef.enum:
                    self._chi2(
                        stats, key, cdef.enum, _column(table, cname), alpha, warnings, details
                    )
        return GateResult(self.name, True, [], warnings, details)

    @staticmethod
    def _ks(
        stats: Any,
        key: str,
        spec: Any,
        col: pa.ChunkedArray,
        alpha: float,
        warnings: list[str],
        details: dict[str, Any],
    ) -> None:
        dist_name = spec.get("name")
        if not dist_name:
            return
        values = _to_floats(col)
        if len(values) < _DISTRIBUTION_MIN_SAMPLE:
            warnings.append(f"{key}: too few rows ({len(values)}) for KS test — skipped")
            return
        try:
            dist = getattr(stats, dist_name)(**spec.get("params", {}))
            ks, p = stats.kstest(values, dist.cdf)
        except Exception as exc:  # noqa: BLE001 - a bad name or parameters is a warning
            warnings.append(f"{key}: KS test failed — {exc}")
            return
        details[key] = {"ks_statistic": round(float(ks), 4), "p_value": round(float(p), 4)}
        if p < alpha:
            warnings.append(
                f"{key}: KS test p={p:.4f} < α={alpha} — "
                f"distribution may have drifted from schema ({dist_name})"
            )

    @staticmethod
    def _chi2(
        stats: Any,
        key: str,
        expected: Any,
        col: pa.ChunkedArray,
        alpha: float,
        warnings: list[str],
        details: dict[str, Any],
    ) -> None:
        present = _present(col)
        n = len(present)
        if n < _DISTRIBUTION_MIN_SAMPLE:
            warnings.append(f"{key}: too few rows ({n}) for chi-squared test — skipped")
            return
        counts = {row["values"]: int(row["counts"]) for row in pc.value_counts(present).to_pylist()}
        for value in expected:
            if value not in counts:
                warnings.append(f"{key}: expected enum value '{value}' is missing from data")
        common = [v for v in expected if v in counts]
        if len(common) < 2:
            return
        obs = [counts[v] for v in common]
        total = sum(expected[v] for v in common)
        exp = [n * (expected[v] / total) for v in common]
        try:
            chi2, p = stats.chisquare(obs, f_exp=exp)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{key}: chi-squared test failed — {exc}")
            return
        if not math.isfinite(float(chi2)):
            warnings.append(f"{key}: chi-squared test failed — non-finite statistic")
            return
        details[key] = {"chi2": round(float(chi2), 4), "p_value": round(float(p), 4)}
        if p < alpha:
            warnings.append(
                f"{key}: chi-squared p={p:.4f} < α={alpha} — enum distribution may have drifted"
            )


# ---------------------------------------------------------------------------------------------
# Registry and runner
# ---------------------------------------------------------------------------------------------

_GATE_REGISTRY: dict[str, Callable[[], ValidationGate]] = {
    "referential_integrity": ReferentialIntegrityGate,
    "schema_conformance": SchemaConformanceGate,
    "null_constraint": NullConstraintGate,
    "unique_constraint": UniqueConstraintGate,
    "range_constraint": RangeConstraintGate,
    "temporal_consistency": TemporalConsistencyGate,
    "file_format": FileFormatGate,
    "schema_drift": SchemaDriftGate,
    "distribution": DistributionGate,
}


def _unknown_gate(name: str) -> ValueError:
    return ValueError(f"Unknown gate '{name}'. Available: {sorted(_GATE_REGISTRY)}")


class GateRunner:
    """Run gates, by name or instance, against a context. No argument runs all nine."""

    def __init__(self, gates: list[str | ValidationGate] | None = None) -> None:
        self._gates: list[ValidationGate] = []
        if not gates:
            self._gates = [factory() for factory in _GATE_REGISTRY.values()]
            return
        for gate in gates:
            if isinstance(gate, str):
                factory = _GATE_REGISTRY.get(gate)
                if factory is None:
                    raise _unknown_gate(gate)
                self._gates.append(factory())
            else:
                self._gates.append(gate)

    @staticmethod
    def available_gates() -> list[str]:
        return sorted(_GATE_REGISTRY)

    @staticmethod
    def register_gate(name: str, gate_cls: Callable[[], ValidationGate]) -> None:
        """Register a custom gate under ``name``."""
        _GATE_REGISTRY[name] = gate_cls

    def run_all(self, context: ValidationContext) -> list[GateResult]:
        return [gate.check(context) for gate in self._gates]

    def run_gate(self, gate_name: str, context: ValidationContext) -> GateResult:
        factory = _GATE_REGISTRY.get(gate_name)
        if factory is None:
            raise _unknown_gate(gate_name)
        return factory().check(context)

    @staticmethod
    def summary(results: list[GateResult]) -> dict[str, Any]:
        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]
        return {
            "total_gates": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "total_errors": sum(len(r.errors) for r in results),
            "total_warnings": sum(len(r.warnings) for r in results),
            "passed_gates": [r.gate_name for r in passed],
            "failed_gates": [r.gate_name for r in failed],
            "all_passed": not failed,
        }
