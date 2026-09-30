"""Read access to a Shape model v2 for the consumers (contracts, drift, quality, query).

Every consumer accepts a v2 model or a v1 capture (which is migrated, see
``shape.spec.migrate``) and reads the v2 vocabulary through these helpers.
"""

from __future__ import annotations

from typing import Any

from .migrate import to_model

_NUMERIC = frozenset({"int", "float"})


def family(kind: Any) -> Any:
    """The type family contracts and drift compare: ``int`` and ``float`` are both
    ``numeric``; every other kind stands for itself. v1 captures only knew ``numeric``."""
    return "numeric" if kind in _NUMERIC else kind


def kind_matches(expected: Any, kind: Any) -> bool:
    """True when a contract's ``kind`` (``numeric`` or a v2 kind) fits a column of ``kind``."""
    return expected in (kind, family(kind))


def model_of(shape: Any) -> dict[str, Any]:
    """``shape`` as a validated v2 model."""
    return to_model(shape)


def table_of(model: dict[str, Any], table: str | None = None) -> dict[str, Any]:
    """The table ``table``, or the only table of the model."""
    tables: dict[str, Any] = model["tables"]
    if table is not None:
        if table not in tables:
            raise KeyError(f"no table {table!r}; the model has {sorted(tables)}")
        return tables[table]  # type: ignore[no-any-return]
    if len(tables) != 1:
        raise ValueError(f"the model has {len(tables)} tables; name one of {sorted(tables)}")
    return next(iter(tables.values()))  # type: ignore[no-any-return]


def columns_of(table: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The columns of a table by name, in file order."""
    return {c["name"]: c for c in table["columns"]}


def median(column: dict[str, Any]) -> Any:
    """The 0.5 quantile of a column, if it has quantiles."""
    q = column.get("quantiles")
    return q.get("0.5") if isinstance(q, dict) else None


def null_rate(column: dict[str, Any], rows: int) -> float:
    """Nulls over rows; 0 for an empty table (P15)."""
    return (column.get("null_count") or 0) / rows if rows > 0 else 0.0


def distinct_bounds(column: dict[str, Any]) -> tuple[float, float]:
    """``(low, high)`` the number of distinct values can be: the exact count twice, or the
    estimate widened by the cardinality error model's relative error (P14)."""
    d = float(column.get("distinct") or 0)
    if column.get("distinct_exact"):
        return d, d
    err = ((column.get("error_models") or {}).get("cardinality") or {}).get("relative_error")
    rel = float(err) if isinstance(err, (int, float)) and err > 0 else 0.0
    return d * (1 - rel), d * (1 + rel)
