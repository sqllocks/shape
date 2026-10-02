"""Profile to generation: fit a generation schema to a profile, and say what it preserves.

``shape generate --from X.shape`` reads a profile, :func:`fit_schema` turns it into a
:class:`~shape.generation.schema.GenSchema` the engine runs, and :class:`ReconstructionPlan` lists,
for every field of the profile, whether the generated data keeps it. ``shape plan`` prints the
list.

What the fit models (the rest is flagged ``not_modelled``):

* **Marginals.** An enum column draws its values with the profile's exact weights; a numeric column
  draws from its fitted family (normal, uniform, log-normal, exponential) or, when the fit is poor,
  from its quantiles; integers and booleans keep their type; keys are a sequence, a uuid or a draw
  from the parent's keys.
* **Missing values.** Each column's null rate, applied per row.
* **Dependence.** A Gaussian copula over the numeric columns: the profile's Pearson correlations,
  corrected for the marginals' shapes (:func:`calibrate_correlation`), applied by reordering each
  column's own values, so the marginals are untouched.
* **Seasonality.** A timestamp column draws from its month, weekday and hour profiles within its
  observed range (date-only columns come out at midnight).

Stable interface: :func:`fit_schema`, :class:`Fit`, :func:`calibrate_correlation`,
:data:`COPULA_THRESHOLD`.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from shape.generation.fidelity import PlanItem, ReconstructionPlan
from shape.generation.future import normal_cdf
from shape.generation.learn import SchemaBuilder, as_dataset
from shape.generation.schema import GenSchema
from shape.profile.reference.model import ColumnProfile, DatasetProfile, TableProfile

Floats = npt.NDArray[np.float64]

COPULA_THRESHOLD = 0.05
"""Correlations below this (in absolute value) are not modelled: the columns are independent."""

PRESET = "profile"
"""The scale preset with the profile's own row counts."""

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_P, _A, _N = "preserved", "approximate", "not_modelled"

_MARGINAL_FIELDS = (
    "cardinality",
    "cardinality_ratio",
    "is_unique",
    "is_enum",
    "enum_values",
    "value_counts_ext",
    "value_counts_ext_order",
    "mean",
    "std",
    "min_value",
    "max_value",
    "quantiles",
    "distribution",
    "distribution_params",
    "fit_score",
    "outlier_rate",
)
COLUMN_FIELDS = (
    "name",
    "dtype",
    "null_count",
    "null_rate",
    "pattern",
    "is_primary_key",
    "is_foreign_key",
    "fk_ref_table",
    "hour_histogram",
    "dow_histogram",
    "temporal_histogram",
    "string_length",
    *_MARGINAL_FIELDS,
)
"""Every field of a column of a profile; the plan reports each one that has a value."""

# provider -> served by the built-in pools (no optional package)
_NATIVE = (
    "first_name",
    "last_name",
    "name",
    "email",
    "phone_number",
    "ssn",
    "company",
    "street_address",
    "sentence",
    "city",
    "state_abbr",
    "uri",
    "company_email",
    "ipv4",
    "postcode",
    "zip_plus4",
    "pystr",
    "word",
)


@dataclass(frozen=True, slots=True)
class Fit:
    """A fitted schema and its plan."""

    schema: GenSchema
    plan: ReconstructionPlan


# ---- marginals, for the copula ---------------------------------------------------------------


def _marginal_sample(generator: Mapping[str, Any], ctype: str, n: int = 40_000) -> Floats | None:
    """Sorted draws of one column's generator (what the engine will produce), or ``None`` when
    the column has no numeric marginal."""
    from shape.generation.engine import Engine
    from shape.generation.schema import SCHEMA_VERSION

    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model": {"name": "m", "seed": 7},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["k"],
                "columns": {
                    "k": {"name": "k", "type": "integer", "generator": {"strategy": "sequence"}},
                    "x": {"name": "x", "type": ctype, "generator": dict(generator)},
                },
            }
        },
        "generation": {"scale": "s", "scales": {"s": {"t": n}}},
    }
    try:
        out = Engine(GenSchema.from_dict(doc), scale="s", seed=7).generate().tables["t"]["x"]
        values = np.asarray(out.to_numpy(zero_copy_only=False), dtype=np.float64)
    except (ValueError, TypeError, ImportError):
        return None
    values = values[np.isfinite(values)]
    if len(values) < 100:
        return None
    return np.sort(values)


def calibrate_correlation(
    sample_a: Floats, sample_b: Floats, target: float, size: int = 50_000
) -> tuple[float, float]:
    """``(rho, reached)``: the Gaussian-copula correlation that gives two columns with the
    marginals ``sample_a`` and ``sample_b`` (sorted draws) a Pearson correlation of ``target``,
    and the correlation it does give. Pearson correlation of a copula is not its ``rho`` when the
    marginals are not normal, so ``rho`` is found by bisection on a fixed set of normal draws; when
    ``target`` is out of reach (these marginals cannot be that correlated) ``rho`` is the end of
    the range and ``reached`` says what that gives."""
    rng = np.random.default_rng(12345)
    z1, z2 = rng.standard_normal(size), rng.standard_normal(size)
    ia = np.minimum((normal_cdf(z1) * len(sample_a)).astype(np.int64), len(sample_a) - 1)
    xa = sample_a[ia]

    def pearson(rho: float) -> float:
        z = rho * z1 + np.sqrt(1.0 - rho * rho) * z2
        ib = np.minimum((normal_cdf(z) * len(sample_b)).astype(np.int64), len(sample_b) - 1)
        xb = sample_b[ib]
        if xa.std() == 0 or xb.std() == 0:
            return 0.0
        return float(np.corrcoef(xa, xb)[0, 1])

    lo, hi = -0.999, 0.999
    r_lo, r_hi = pearson(lo), pearson(hi)
    if target >= r_hi:
        return hi, r_hi
    if target <= r_lo:
        return lo, r_lo
    for _ in range(30):
        mid = (lo + hi) / 2.0
        if pearson(mid) < target:
            lo = mid
        else:
            hi = mid
    rho = (lo + hi) / 2.0
    return rho, pearson(rho)


def _looks_like_key(name: str) -> bool:
    n = name.lower()
    return n in ("id", "pk") or n.endswith(("_id", "_pk", "_fk"))


# ---- the fit ---------------------------------------------------------------------------------


def _is_covered(col: ColumnProfile) -> bool:
    values = col.value_counts_ext or col.enum_values
    return values is not None and 0 < len(values) and len(values) >= col.cardinality


def _decimals(value: Any) -> int | None:
    """The decimal places a number is written with (at most 6), or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    text = repr(float(value))
    if "e" in text or "inf" in text or "nan" in text:
        return None
    places = len(text.split(".")[1].rstrip("0")) if "." in text else 0
    return places if places <= 6 else None


def _inferred_scale(col: ColumnProfile) -> int | None:
    """Decimal places of a float column, read off its minimum and maximum: ``None`` unless both
    are written with at most 6 places and one of them has a fraction (an integer-valued range
    says nothing)."""
    lo, hi = _decimals(col.min_value), _decimals(col.max_value)
    if lo is None or hi is None or max(lo, hi) == 0:
        return None
    return max(lo, hi)


def _month_weights(col: ColumnProfile) -> list[float] | None:
    hist = col.temporal_histogram or {}
    weights = hist.get("month_weights")
    if isinstance(weights, list) and len(weights) == 12 and sum(weights) > 0:
        return [float(w) for w in weights]
    return None


def _fit_column(
    col: ColumnProfile, gen: dict[str, Any], column: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Refine the builder's generator for generation. Returns ``(kind, generator)``; ``column``
    (the schema's column document) is updated in place for its type options."""
    strategy = gen["strategy"]

    if strategy == "sequence":
        return "sequence", gen
    if strategy == "uuid":
        return "uuid", gen
    if strategy == "foreign_key":
        gen = {**gen, "output_type": "int64"} if col.dtype == "integer" else gen
        return "foreign_key", gen

    if strategy == "temporal":
        gen = dict(gen)
        gen.pop("type", None)
        midnight = col.hour_histogram is not None and col.hour_histogram[0] >= 0.999
        profiles = dict(gen.get("profiles") or {})
        month = _month_weights(col)
        if month is not None:
            profiles["month"] = dict(zip(_MONTHS, month, strict=True))
        if midnight:
            profiles.pop("hour_of_day", None)
            gen["granularity"] = "day"
        if profiles:
            gen["pattern"] = "seasonal"
            gen["profiles"] = profiles
        return ("temporal_midnight" if midnight and col.dtype != "date" else "temporal"), gen

    if strategy == "weighted_enum":
        gen = dict(gen)
        if col.dtype == "integer":
            gen["output_type"] = "int64"
        elif col.dtype == "boolean":
            gen["output_type"] = "bool"
            gen["values"] = {str(k).lower(): v for k, v in gen["values"].items()}
        return "enum", gen

    if strategy in ("distribution", "empirical"):
        gen = dict(gen)
        if col.dtype == "integer":
            gen["output_type"] = "int64"
        return ("empirical" if strategy == "empirical" else "distribution"), gen

    if strategy == "faker":
        provider = str(gen.get("provider"))
        gen = {k: v for k, v in gen.items() if k != "max_length"}
        if provider == "postcode" and (col.string_length or {}).get("min", 0) >= 10:
            gen["provider"] = provider = "zip_plus4"
        return ("pattern" if col.pattern else "text") if provider in _NATIVE else "faker", gen
    return "text", gen


def _plan_column(
    table: str,
    col: ColumnProfile,
    kind: str,
    rows_match: bool = True,
) -> list[PlanItem]:
    """The plan items of one column's fields, for the kind of generator it got."""
    items: list[PlanItem] = []

    def add(field: str, status: str, reason: str) -> None:
        items.append(PlanItem(f"{table}.{col.name}.{field}", status, reason))

    present = {f for f in COLUMN_FIELDS if _has(col, f)}

    def mark(fields: tuple[str, ...], status: str, reason: str) -> None:
        for f in fields:
            if f in present:
                add(f, status, reason)
                present.discard(f)

    if kind == "temporal_midnight":
        mark(
            ("dtype",),
            _A,
            "every value is a midnight, generated as a midnight timestamp; profiling a timestamp "
            "column of midnights reports `date`",
        )
    mark(("name", "dtype", "is_primary_key", "is_foreign_key", "fk_ref_table"), _P, "kept as it is")
    mark(
        ("null_count", "null_rate"),
        _P,
        "each row is null with the column's null rate, independently of the other columns",
    )

    if kind == "sequence":
        mark(
            ("is_unique", "is_enum", "enum_values", "value_counts_ext", "value_counts_ext_order"),
            _P,
            "a sequence is unique and not an enum",
        )
        count = _P if rows_match else _A
        why = "a sequence with the profile's first value" + (
            "" if rows_match else "; the generated row count differs from the profile's"
        )
        mark(("cardinality", "cardinality_ratio"), count, why)
        mark(("min_value",), _P, "the sequence starts at the profile's minimum")
        mark(
            (
                "max_value",
                "mean",
                "std",
                "quantiles",
                "distribution",
                "distribution_params",
                "outlier_rate",
            ),
            _A,
            "a gap-free sequence: the profile's keys may have gaps",
        )
        mark(("fit_score",), _N, "a goodness of fit is measured again, not generated")
    elif kind == "uuid":
        mark(
            (
                "pattern",
                "is_unique",
                "cardinality",
                "cardinality_ratio",
                "is_enum",
                "string_length",
            ),
            _P,
            "uuid4 strings are unique, 36 characters",
        )
        mark(
            ("enum_values", "value_counts_ext", "value_counts_ext_order", "min_value", "max_value"),
            _N,
            "random uuids: the particular values (and the smallest and largest) are not reproduced",
        )
    elif kind == "foreign_key":
        mark(
            (
                "min_value",
                "max_value",
                "mean",
                "std",
                "quantiles",
                "cardinality",
                "cardinality_ratio",
                "is_unique",
                "is_enum",
                "distribution",
                "distribution_params",
                "outlier_rate",
            ),
            _A,
            "draws uniformly from the parent's keys; how many rows each parent gets is not "
            "modelled",
        )
        mark(
            ("enum_values", "value_counts_ext", "value_counts_ext_order"),
            _N,
            "which parent each row points at is drawn uniformly, not as observed",
        )
        mark(("fit_score",), _N, "a goodness of fit is measured again, not generated")
    elif kind == "enum":
        mark(
            ("enum_values", "value_counts_ext", "value_counts_ext_order"),
            _P,
            "values drawn with the profile's weights",
        )
        mark(
            (
                "cardinality",
                "is_enum",
                "is_unique",
                "min_value",
                "max_value",
                "mean",
                "std",
                "quantiles",
                "outlier_rate",
            ),
            _P,
            "follows from the values and weights",
        )
        mark(
            ("distribution", "distribution_params", "fit_score"),
            _A,
            "which family fits is a threshold decision that sampling error can flip",
        )
        mark(("cardinality_ratio",), _A if not rows_match else _P, "depends on the row count")
        mark(("pattern",), _P, "the values are the profile's values")
        mark(("string_length",), _P, "the values are the profile's values")
    elif kind in ("distribution", "empirical"):
        what = (
            "the fitted family and its parameters"
            if kind == "distribution"
            else ("the profile's quantiles, interpolated")
        )
        mark(
            ("mean", "std", "min_value", "max_value", "quantiles", "outlier_rate"),
            _A,
            f"drawn from {what}; sample statistics differ by sampling error",
        )
        if kind == "distribution":
            mark(
                ("distribution", "distribution_params"),
                _A,
                "the family is generated; it is fitted again from the output",
            )
        else:
            mark(
                ("distribution", "distribution_params"),
                _N,
                "the quantiles are generated, not the family",
            )
        mark(
            (
                "cardinality",
                "cardinality_ratio",
                "is_unique",
                "is_enum",
                "enum_values",
                "value_counts_ext",
                "value_counts_ext_order",
            ),
            _A,
            "continuous draws; the number of distinct values depends on rounding and row count",
        )
        mark(("fit_score",), _N, "a goodness of fit is measured again, not generated")
    elif kind in ("temporal", "temporal_midnight"):
        mark(("hour_histogram", "dow_histogram"), _P, "drawn with the profile's weights")
        mark(
            ("temporal_histogram",),
            _A,
            "month weights are generated; the year weights follow from the observed range",
        )
        mark(("min_value", "max_value"), _A, "drawn inside the observed range")
        mark(
            (
                "cardinality",
                "cardinality_ratio",
                "is_unique",
                "is_enum",
                "enum_values",
                "value_counts_ext",
                "value_counts_ext_order",
                "mean",
                "std",
                "quantiles",
                "distribution",
                "distribution_params",
                "fit_score",
                "outlier_rate",
            ),
            _A,
            "follows from the seasonal profile and the row count",
        )
        mark(("pattern", "string_length"), _A, "timestamps")
    else:  # pattern, text, faker
        provider = {
            "pattern": "a built-in pool in the detected format",
            "text": "a built-in pool guessed from the column name",
            "faker": "the optional `faker` package (not installed by default)",
        }[kind]
        mark(("pattern",), _P if kind == "pattern" else _N, f"values come from {provider}")
        mark(
            (
                "cardinality",
                "cardinality_ratio",
                "is_unique",
                "is_enum",
                "enum_values",
                "value_counts_ext",
                "value_counts_ext_order",
                "string_length",
                "min_value",
                "max_value",
                "mean",
                "std",
                "quantiles",
                "distribution",
                "distribution_params",
                "fit_score",
                "outlier_rate",
            ),
            _N,
            f"synthetic values from {provider}: the real values, their lengths and how often "
            "they repeat are not reproduced",
        )
    for f in sorted(present):  # a field no rule above covered is never reported as preserved
        add(f, _N, "not modelled")
    return items


def _has(col: ColumnProfile, field: str) -> bool:
    if field == "value_counts_ext_order":
        return bool(col.value_counts_ext)
    value = getattr(col, field, None)
    return value is not None


def fit_schema(
    profile: Any,
    *,
    domain: str = "profile",
    copula_threshold: float = COPULA_THRESHOLD,
    rows: int | None = None,
) -> Fit:
    """The generation schema that reproduces ``profile``, and what it preserves.

    The schema's ``profile`` scale preset has the profile's row counts; ``rows`` replaces the row
    count of a single-table profile (the plan then reports the row-count dependent fields as
    approximate)."""
    dataset: DatasetProfile = as_dataset(profile)
    base = SchemaBuilder().build(dataset, domain_name=domain, correlation_threshold=2.0)
    doc = copy.deepcopy(base.to_dict())
    items: list[PlanItem] = []
    parent_scale = {t: tp.row_count for t, tp in dataset.tables.items()}
    if rows is not None:
        if len(parent_scale) != 1:
            raise ValueError("--rows sets the row count of a one-table profile; use a scale preset")
        if rows < 1:
            raise ValueError("the row count must be at least 1")
        parent_scale = {next(iter(parent_scale)): int(rows)}

    kinds: dict[tuple[str, str], str] = {}
    for tname, tp in dataset.tables.items():
        for cname, cp in tp.columns.items():
            column = doc["tables"][tname]["columns"][cname]
            kind, gen = _fit_column(cp, column["generator"], column)
            column["generator"] = gen
            kinds[(tname, cname)] = kind
            if kind == "pattern" or kind == "text":
                column["max_length"] = None
            if cp.dtype == "integer" and kind in ("distribution", "empirical"):
                column["scale"] = 0
            elif cp.dtype == "float" and kind in ("distribution", "empirical"):
                column["scale"] = _inferred_scale(cp)
            items.extend(_plan_column(tname, cp, kind, parent_scale[tname] == tp.row_count))
        if "_row_id" in doc["tables"][tname]["columns"]:
            items.append(
                PlanItem(
                    f"{tname}._row_id",
                    _A,
                    "the table has no key: a surrogate `_row_id` column is added, which the "
                    "source does not have",
                )
            )
        items.extend(_table_items(tname, tp))

    # the copula: calibrated correlations between the numeric, non-key columns
    pairs_by_table: dict[str, list[list[Any]]] = {}
    for tname, tp in dataset.tables.items():
        pairs, pair_items = _correlations(tname, tp, doc["tables"][tname], kinds, copula_threshold)
        items.extend(pair_items)
        if pairs:
            pairs_by_table[tname] = pairs
    doc["correlated_columns"] = pairs_by_table
    if pairs_by_table:
        doc["generation"]["output"] = {
            **doc["generation"]["output"],
            "copula_nulls": "rank",
            "copula_threshold": 0.0,
        }

    doc["generation"]["scales"][PRESET] = dict(parent_scale)
    doc["generation"]["scale"] = PRESET
    items.extend(_dataset_items(dataset))
    return Fit(GenSchema.from_dict(doc), ReconstructionPlan(tuple(items)))


def _table_items(tname: str, tp: TableProfile) -> list[PlanItem]:
    out = [
        PlanItem(
            f"{tname}.row_count", _P, "the `profile` scale preset has the profile's row count"
        ),
        PlanItem(f"{tname}.primary_key", _P, "generated as a unique key"),
    ]
    if tp.detected_fks:
        out.append(
            PlanItem(f"{tname}.detected_fks", _P, "generated as foreign keys to the parent's keys")
        )
    return out


def _correlations(
    tname: str,
    tp: TableProfile,
    table_doc: dict[str, Any],
    kinds: Mapping[tuple[str, str], str],
    threshold: float,
) -> tuple[list[list[Any]], list[PlanItem]]:
    matrix = tp.correlation_matrix or {}
    items: list[PlanItem] = []
    pairs: list[list[Any]] = []
    samples: dict[str, Floats | None] = {}
    seen: set[frozenset[str]] = set()
    for a, row in matrix.items():
        for b, r in row.items():
            key = frozenset((a, b))
            if a == b or key in seen:
                continue
            seen.add(key)
            evidence = f"{tname}.correlation_matrix[{a},{b}]"
            if abs(r) < threshold:
                items.append(
                    PlanItem(
                        evidence,
                        _A,
                        f"|r| = {abs(r):.3f} is below {threshold}: the columns are generated "
                        "independently",
                    )
                )
                continue
            blocked = [
                c
                for c in (a, b)
                if _looks_like_key(c)
                or kinds.get((tname, c)) in ("sequence", "foreign_key", "uuid")
            ]
            if blocked:
                items.append(
                    PlanItem(
                        evidence,
                        _N,
                        f"{', '.join(blocked)} is a key (or named like one): keys are never "
                        "reordered",
                    )
                )
                continue
            for c in (a, b):
                if c not in samples:
                    col = table_doc["columns"][c]
                    samples[c] = _marginal_sample(col["generator"], col["type"])
            sa, sb = samples[a], samples[b]
            if sa is None or sb is None:
                items.append(
                    PlanItem(evidence, _N, "a column has no numeric marginal to correlate")
                )
                continue
            rho, reached = calibrate_correlation(sa, sb, float(r))
            pairs.append([a, b, round(rho, 6)])
            if abs(reached - r) <= 0.02:
                items.append(
                    PlanItem(
                        evidence,
                        _A,
                        f"Gaussian copula, rho {rho:.3f} calibrated so the Pearson correlation is "
                        f"{reached:.3f} (profile: {r:.3f})",
                    )
                )
            else:
                items.append(
                    PlanItem(
                        evidence,
                        _A,
                        f"these marginals cannot be correlated at {r:.3f}: the closest "
                        "copula gives "
                        f"{reached:.3f}",
                    )
                )
    return pairs, items


def _dataset_items(dataset: DatasetProfile) -> list[PlanItem]:
    items = [
        PlanItem(
            "dataset.missingness_joint",
            _N,
            "which values are missing together: every column's nulls are independent",
        ),
        PlanItem(
            "dataset.dependencies",
            _N,
            "functional dependencies and conditional distributions between columns: "
            "columns are independent except for the correlations above",
        ),
        PlanItem("dataset.row_order", _N, "the order of the rows is not reproduced"),
    ]
    for rel in dataset.relationships:
        items.append(PlanItem(f"relationship:{rel.get('name')}", _P, "generated as a foreign key"))
    return items


__all__ = [
    "COLUMN_FIELDS",
    "COPULA_THRESHOLD",
    "PRESET",
    "Fit",
    "calibrate_correlation",
    "fit_schema",
]
