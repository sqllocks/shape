"""The safe profile: a profile that is safe to share by construction.

A full profile carries value-bearing evidence: exact minimum and maximum, every enum value,
the top value counts. The safe profile keeps only the statistics that are safe and sufficient
to describe the data's shape, and has no field that can hold a raw extreme or a value list:

* numeric extremes become winsorized ``bounds`` taken from the quantile fingerprint;
* categorical mass becomes ``categorical_weights`` with every category below the minimum
  cohort ``k`` folded into one ``__OTHER__`` bucket;
* a column is only allowed to keep literal category labels when it is proven a low-entropy
  label set (letters only, no digits); numeric and date columns become a coarse histogram,
  anything else gets hashed keys (default deny);
* a column that looks like personal data (a matching value pattern, or nearly one distinct
  value per row) keeps its pattern and length distribution only;
* the artifact carries a ``redaction_manifest`` that says what was actually suppressed.

``unsafe_full_fidelity=True`` is the single explicit opt-out. It turns the disclosure
controls off, stamps ``unsafe=true`` on the artifact, and ``shape profile validate --safe``
rejects such an artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .cells import OTHER_BUCKET as OTHER_BUCKET
from .cells import non_null_base, suppress_bins, suppress_weights

# Persisted schema version of the safe profile.
SCHEMA_VERSION = 1

# Minimum cohort: a category with fewer than ``K_DEFAULT`` rows is folded into ``__OTHER__``
# (``K_SENSITIVE`` for a column flagged sensitive). ``k`` can be set per profile and per column.
K_DEFAULT = 5
K_SENSITIVE = 11

# A literal category label is kept only when it matches this shape: starts with a letter,
# then letters and a little label punctuation, at most 32 characters. It has no digits, so by
# construction it cannot be an SSN, phone number, account number, date, ZIP code or record id.
SAFE_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z _/&-]{0,31}$")
LABEL_CARDINALITY_CAP = 64
_NUMERIC_DTYPES = frozenset({"integer", "float"})
_TEMPORAL_DTYPES = frozenset({"date", "datetime"})

# Raw-bearing field names that must never exist on the safe model.
FORBIDDEN_RAW_FIELDS = frozenset({"min_value", "max_value", "enum_values", "value_counts_ext"})

# Detected value patterns that mark personal data. A column with one of these keeps its
# pattern and length distribution only, whatever its name. Detection is by value, so it is
# defence in depth rather than a completeness guarantee; the cardinality backstop below
# catches the high-cardinality free text the patterns miss.
PII_PATTERNS = frozenset(
    {"email", "ssn", "credit_card", "phone", "ip_address", "iban", "postal_code"}
)
PII_CARDINALITY_RATIO = 0.95

WIDEN_BOUNDS = {"bounds_lo_quantile": "p0_5", "bounds_hi_quantile": "p99_5"}


@dataclass(frozen=True)
class ColumnConfig:
    """Per-column overrides: an explicit ``k`` wins over the ``sensitive`` flag."""

    k: int | None = None
    sensitive: bool = False


@dataclass(frozen=True)
class SafeConfig:
    """How a safe profile is built. The defaults are the safe settings."""

    k: int | None = None
    sensitive: bool = False
    columns: Mapping[str, ColumnConfig] = field(default_factory=dict)
    pii_gate: bool = True
    pii_cardinality_ratio: float = PII_CARDINALITY_RATIO
    bounds_lo_quantile: str = "p1"
    bounds_hi_quantile: str = "p99"
    unsafe_full_fidelity: bool = False

    def column_k(self, column: str | None) -> int:
        """Effective ``k``; the most specific setting wins.

        Per-column ``k``, then per-column ``sensitive``, then profile ``k``, then profile
        ``sensitive``, then ``K_DEFAULT``. An explicit ``k`` always beats a ``sensitive`` flag
        at the same or a wider level.
        """
        if self.unsafe_full_fidelity:
            return 1
        col = self.columns.get(column) if column is not None else None
        if col is not None:
            if col.k is not None:
                return int(col.k)
            if col.sensitive:
                return K_SENSITIVE
        if self.k is not None:
            return int(self.k)
        if self.sensitive:
            return K_SENSITIVE
        return K_DEFAULT

    def column_sensitive(self, column: str | None) -> bool:
        """True when the effective ``k`` for the column comes from a ``sensitive`` flag."""
        if self.unsafe_full_fidelity:
            return False
        col = self.columns.get(column) if column is not None else None
        if col is not None:
            if col.k is not None:
                return False
            if col.sensitive:
                return True
        if self.k is not None:
            return False
        return self.sensitive

    @property
    def gate_on(self) -> bool:
        return self.pii_gate and not self.unsafe_full_fidelity


def _list_or_none(x: Any) -> list[float] | None:
    return list(x) if isinstance(x, list) else None


def _opt_dict(x: Any) -> dict[str, Any] | None:
    return dict(x) if isinstance(x, dict) else None


def _round_1sig(x: float, rounder: Callable[[float], float]) -> float:
    """Round to one significant figure, so a histogram edge is never a literal value."""
    if x == 0:
        return 0.0
    mag = 10 ** math.floor(math.log10(abs(x)))
    return float(rounder(x / mag) * mag)


def _coerce_number(key: str) -> float | None:
    try:
        return float(str(key).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _coerce_year(key: str) -> int | None:
    m = re.match(r"\s*(\d{4})\b", str(key))
    return int(m.group(1)) if m else None


def _hash_key(key: str) -> str:
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]


def is_safe_label_set(dtype: str, weights: Mapping[str, float]) -> bool:
    """True only when every surviving category key is a proven safe label (default deny)."""
    if dtype not in ("string", "boolean"):
        return False
    keys = [k for k in weights if k != OTHER_BUCKET]
    if not keys or len(keys) > LABEL_CARDINALITY_CAP:
        return False
    return all(SAFE_LABEL_RE.match(str(k)) is not None for k in keys)


def _bucket_numeric(items: list[tuple[str, float]], dtype: str) -> dict[str, Any] | None:
    """A coarse histogram of numeric or date keys, or None when a key does not parse."""
    temporal = dtype in _TEMPORAL_DTYPES
    nums: list[tuple[float, float]] = []
    for key, weight in items:
        num = _coerce_year(key) if temporal else _coerce_number(key)
        if num is None:
            return None
        nums.append((float(num), weight))
    if not nums:
        return None
    lo_raw = min(n for n, _ in nums)
    hi_raw = max(n for n, _ in nums)
    if temporal:
        lo = float(math.floor(lo_raw))
        hi = float(math.ceil(hi_raw) + 1)
    else:
        lo = _round_1sig(lo_raw, math.floor)
        hi = _round_1sig(hi_raw, math.ceil)
    if hi <= lo:
        hi = lo + (abs(lo) if lo else 1.0) + 1.0
    nbins = min(10, max(1, len(nums)))
    width = (hi - lo) / nbins
    bins = [0.0] * nbins
    for num, weight in nums:
        idx = int((num - lo) / width) if width else 0
        bins[max(0, min(nbins - 1, idx))] += weight
    return {
        "kind": "temporal_year" if temporal else "numeric",
        "lo": lo,
        "hi": hi,
        "nbins": nbins,
        "bins": [round(b, 6) for b in bins],
    }


def _route_non_label(
    dtype: str, weights: Mapping[str, float]
) -> tuple[dict[str, float] | None, dict[str, Any] | None, bool]:
    """Route a non-label categorical column away from literal keys.

    Returns ``(categorical_weights, categorical_histogram, histogram_routed)``. Numeric and
    date columns become a coarse histogram; anything else gets hashed keys. The ``__OTHER__``
    residual mass is kept either way.
    """
    other_w = float(weights.get(OTHER_BUCKET, 0.0))
    items = [(k, float(v)) for k, v in weights.items() if k != OTHER_BUCKET]
    if dtype in _NUMERIC_DTYPES or dtype in _TEMPORAL_DTYPES:
        hist = _bucket_numeric(items, dtype)
        if hist is not None:
            if other_w > 0.0:
                hist["other_weight"] = round(other_w, 6)
            return None, hist, True
    hashed = {_hash_key(k): round(w, 6) for k, w in items}
    if other_w > 0.0:
        hashed[OTHER_BUCKET] = round(other_w, 6)
    return hashed, None, False


def _winsorized_bounds(
    quantiles: Mapping[str, float] | None, cfg: SafeConfig
) -> dict[str, float] | None:
    """``{"lo", "hi"}`` from the quantile fingerprint only; the raw extremes are never read.

    A widened request against a fingerprint that lacks the widened endpoints falls back to
    p1/p99 rather than returning nothing.
    """
    if not quantiles:
        return None
    lo = quantiles.get(cfg.bounds_lo_quantile)
    hi = quantiles.get(cfg.bounds_hi_quantile)
    if lo is None:
        lo = quantiles.get("p1")
    if hi is None:
        hi = quantiles.get("p99")
    if lo is None or hi is None:
        return None
    return {"lo": float(lo), "hi": float(hi)}


def pii_gate_fires(
    pattern: str | None, cardinality: int, row_count: int | None, cfg: SafeConfig
) -> bool:
    """True when the column must be reduced to its pattern and length distribution only.

    Two independent triggers, both independent of the column name: the detected value
    pattern is a personal-data class, or the distinct count is within
    ``pii_cardinality_ratio`` of the row count (free text such as names or notes).
    """
    if not cfg.gate_on:
        return False
    if pattern is not None and pattern in PII_PATTERNS:
        return True
    return bool(
        row_count and row_count > 0 and cardinality / row_count >= cfg.pii_cardinality_ratio
    )


def _length_dist(string_length: Mapping[str, float] | None) -> dict[str, float] | None:
    """The numeric length aggregates of a string column; lengths only, never values."""
    if not string_length:
        return None
    return {k: float(v) for k, v in string_length.items() if isinstance(v, (int, float))}


@dataclass
class SafeColumnProfile:
    """The safe statistic set of one column."""

    name: str
    dtype: str
    null_rate: float | None  # None: unknown (no rows were read)
    cardinality: int
    mean: float | str | None = None
    std: float | str | None = None
    quantiles: dict[str, float] | None = None
    distribution: str | None = None
    distribution_params: dict[str, float] | None = None
    bounds: dict[str, float] | None = None
    categorical_weights: dict[str, float] | None = None
    suppressed_category_count: int | None = None
    categorical_histogram: dict[str, Any] | None = None
    pattern: str | None = None
    length_dist: dict[str, float] | None = None
    string_length: dict[str, float] | None = None
    hour_histogram: list[float] | None = None
    dow_histogram: list[float] | None = None
    temporal_histogram: dict[str, Any] | None = None
    # Cells withheld below the minimum cohort: folded categories plus zeroed histogram bins.
    cells_suppressed: int = 0

    @classmethod
    def from_column(
        cls, col: Mapping[str, Any], cfg: SafeConfig, row_count: int | None
    ) -> SafeColumnProfile:
        """Map one column of a full profile (``Profile.to_dict()``) to its safe form."""
        name = str(col["name"])
        dtype = str(col["dtype"])
        quantiles = _opt_dict(col.get("quantiles"))
        distribution_params = _opt_dict(col.get("distribution_params"))
        bounds = _winsorized_bounds(quantiles, cfg)
        cardinality = int(col.get("cardinality") or 0)

        base = non_null_base(row_count, col.get("null_count"), col.get("null_rate") or 0.0)
        k = cfg.column_k(name)
        weights: dict[str, float] | None = None
        suppressed: int | None = None
        if col.get("is_enum"):
            seed = col.get("enum_values") or col.get("value_counts_ext")
            if seed:
                weights, suppressed = suppress_weights(seed, k, base)

        pattern = col.get("pattern")
        string_length = _opt_dict(col.get("string_length"))
        length_dist = None
        if pii_gate_fires(pattern, cardinality, row_count, cfg):
            length_dist = _length_dist(string_length)
            weights = None
            suppressed = None

        histogram: dict[str, Any] | None = None
        histogram_routed = False
        if not cfg.unsafe_full_fidelity and weights and not is_safe_label_set(dtype, weights):
            weights, histogram, histogram_routed = _route_non_label(dtype, weights)

        # A histogram-routed numeric or date column must not also persist quantiles, bounds or
        # literal distribution parameters: p50 could be an exact value.
        if histogram_routed:
            quantiles = None
            bounds = None
            distribution_params = None

        # Every other cell surface obeys the same minimum cohort: temporal histogram bins are
        # zeroed when they stand for fewer than k rows.
        cells = 0
        hour = _list_or_none(col.get("hour_histogram"))
        dow = _list_or_none(col.get("dow_histogram"))
        if hour is not None:
            hour, n = suppress_bins(hour, k, base)
            cells += n
        if dow is not None:
            dow, n = suppress_bins(dow, k, base)
            cells += n
        temporal = _opt_dict(col.get("temporal_histogram"))
        if temporal is not None:
            for part in ("year_weights", "month_weights"):
                w = _list_or_none(temporal.get(part))
                if w is not None:
                    released, n = suppress_bins(w, k, base)
                    cells += n
                    if released is None:
                        del temporal[part]
                    else:
                        temporal[part] = released
        if histogram is not None:
            bins, n = suppress_bins(histogram["bins"], k, base)
            cells += n
            histogram["bins"] = bins if bins is not None else []

        return cls(
            name=name,
            dtype=dtype,
            null_rate=col.get("null_rate"),
            cardinality=cardinality,
            mean=col.get("mean"),
            std=col.get("std"),
            quantiles=quantiles,
            distribution=col.get("distribution"),
            distribution_params=distribution_params,
            bounds=bounds,
            categorical_weights=weights,
            suppressed_category_count=suppressed,
            categorical_histogram=histogram,
            pattern=pattern,
            length_dist=length_dist,
            string_length=string_length,
            hour_histogram=hour,
            dow_histogram=dow,
            temporal_histogram=temporal,
            cells_suppressed=(suppressed or 0) + cells,
        )

    def to_dict(self) -> dict[str, Any]:
        """Plain dict in a fixed key order, so the serialized artifact is byte-stable."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SafeColumnProfile:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class SafeTableProfile:
    name: str
    row_count: int
    columns: dict[str, SafeColumnProfile] = field(default_factory=dict)
    primary_key: list[str] = field(default_factory=list)
    detected_fks: dict[str, str] = field(default_factory=dict)
    correlation_matrix: dict[str, dict[str, float]] | None = None

    @classmethod
    def from_table(cls, table: Mapping[str, Any], cfg: SafeConfig) -> SafeTableProfile:
        row_count = int(table.get("row_count") or 0)
        return cls(
            name=str(table["name"]),
            row_count=row_count,
            columns={
                cname: SafeColumnProfile.from_column(col, cfg, row_count)
                for cname, col in table["columns"].items()
            },
            primary_key=list(table.get("primary_key") or []),
            detected_fks=dict(table.get("detected_fks") or {}),
            correlation_matrix=table.get("correlation_matrix"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "row_count": self.row_count,
            "columns": {n: c.to_dict() for n, c in self.columns.items()},
            "primary_key": list(self.primary_key),
            "detected_fks": dict(self.detected_fks),
            "correlation_matrix": self.correlation_matrix,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SafeTableProfile:
        return cls(
            name=data["name"],
            row_count=data["row_count"],
            columns={n: SafeColumnProfile.from_dict(c) for n, c in data.get("columns", {}).items()},
            primary_key=list(data.get("primary_key", [])),
            detected_fks=dict(data.get("detected_fks", {})),
            correlation_matrix=data.get("correlation_matrix"),
        )


@dataclass
class SafeProfile:
    """The persisted safe profile of one table or a dataset."""

    tables: dict[str, SafeTableProfile] = field(default_factory=dict)
    relationships: list[Any] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    redaction_manifest: dict[str, Any] = field(default_factory=dict)
    unsafe: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "unsafe": self.unsafe,
            "tables": {n: t.to_dict() for n, t in self.tables.items()},
            "relationships": self.relationships,
            "redaction_manifest": self.redaction_manifest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SafeProfile:
        return cls(
            tables={n: SafeTableProfile.from_dict(t) for n, t in data.get("tables", {}).items()},
            relationships=list(data.get("relationships", [])),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            redaction_manifest=dict(data.get("redaction_manifest", {})),
            unsafe=bool(data.get("unsafe", False)),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.write_text(self.to_json(), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> SafeProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "tables" not in data:
            raise ValueError(f"{path} is not a safe profile")
        version = data.get("schema_version", SCHEMA_VERSION)
        if not isinstance(version, int) or version > SCHEMA_VERSION:
            raise ValueError(f"unsupported safe profile schema_version {version!r}")
        return cls.from_dict(data)


def _tables_of(profile: Mapping[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    """The table dicts and relationships of a table or dataset profile dict."""
    if "tables" in profile:
        return dict(profile["tables"]), list(profile.get("relationships") or [])
    if "columns" in profile:
        return {str(profile["name"]): profile}, []
    raise ValueError("not a profile: expected a table or dataset profile dictionary")


def build_redaction_manifest(
    source: Mapping[str, Any], safe: SafeProfile, cfg: SafeConfig
) -> dict[str, Any]:
    """What the scrub actually did, read off the mapping outcome rather than the intent."""
    tables, _ = _tables_of(source)
    out: dict[str, Any] = {}
    for tname, table in tables.items():
        safe_table = safe.tables.get(tname)
        if safe_table is None:
            continue
        row_count = int(table.get("row_count") or 0)
        cols: dict[str, Any] = {}
        for cname, col in table["columns"].items():
            safe_col = safe_table.columns.get(cname)
            if safe_col is None:
                continue
            cols[cname] = {
                "categories_dropped": int(safe_col.suppressed_category_count or 0),
                "cells_suppressed": safe_col.cells_suppressed,
                "bounds_winsorized": safe_col.bounds is not None,
                "pattern_only": pii_gate_fires(
                    col.get("pattern"), int(col.get("cardinality") or 0), row_count, cfg
                ),
                "k": cfg.column_k(cname),
                "sensitive": cfg.column_sensitive(cname),
            }
        out[tname] = cols
    return {
        "unsafe": cfg.unsafe_full_fidelity,
        "k_default": cfg.column_k(None),
        "tables": out,
    }


def to_safe_profile(
    profile: Any,
    config: SafeConfig | None = None,
    *,
    unsafe_full_fidelity: bool = False,
) -> SafeProfile:
    """Build the safe profile of ``profile``.

    ``profile`` is a ``shape.profile`` result, its ``to_dict()``, or the path of a ``.shape``
    profile artifact. The scrub runs by default; ``unsafe_full_fidelity=True`` is the one
    opt-out, and stamps the result ``unsafe``.
    """
    cfg = config or SafeConfig()
    if unsafe_full_fidelity and not cfg.unsafe_full_fidelity:
        cfg = SafeConfig(**{**cfg.__dict__, "unsafe_full_fidelity": True})
    if isinstance(profile, str | Path):
        import shape

        profile = shape.load(str(profile))
    source = profile.to_dict() if hasattr(profile, "to_dict") else profile
    tables, relationships = _tables_of(source)
    safe = SafeProfile(
        tables={n: SafeTableProfile.from_table(t, cfg) for n, t in tables.items()},
        relationships=relationships,
        unsafe=cfg.unsafe_full_fidelity,
    )
    safe.redaction_manifest = build_redaction_manifest(source, safe, cfg)
    return safe


def _assert_no_raw_fields() -> None:
    for klass in (SafeColumnProfile, SafeTableProfile, SafeProfile):
        leaked = {f.name for f in fields(klass)} & FORBIDDEN_RAW_FIELDS
        if leaked:
            raise AssertionError(
                f"{klass.__name__} declares raw-bearing field(s): {sorted(leaked)}"
            )


_assert_no_raw_fields()
