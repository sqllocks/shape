"""A design input built from data: types, nullability and lengths from the values, candidate keys
and functional dependencies from :func:`~shape.profile.dependencies.candidate_key` and
:func:`~shape.profile.dependencies.functional_dependency` (the code behind ``shape key`` and
``shape fd``).

Only evidence that holds exactly is reported: a key is a column set whose values are unique and
never null, a dependency is one with no violating group. A dependency also needs support:
its determinant must repeat (at most ``max_group_ratio`` distinct values per row), so a column
that is nearly unique does not appear to determine everything. Determinants with nulls,
constant columns, supersets of a key and non-minimal determinants are left out.

Facts, measures, hierarchies and history needs are not in the data; add them to the result.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from decimal import Decimal
from itertools import combinations
from typing import Any

from shape.design.model import DesignError, DesignInput
from shape.profile.dependencies import candidate_key, functional_dependency

MAX_COLUMNS_FOR_PAIRS = 50


def design_from_rows(
    data: Iterable[Mapping[str, Any]] | Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    name: str,
    max_key_size: int = 2,
    max_determinant: int = 2,
    max_group_ratio: float = 0.5,
) -> DesignInput:
    """The design input for ``data``: rows (one entity called ``name``) or a mapping of table
    name to rows (one entity per table)."""
    if not 0 < max_group_ratio <= 1:
        raise DesignError("max_group_ratio must be in (0, 1]")
    if max_key_size < 1 or max_determinant < 1:
        raise DesignError("max_key_size and max_determinant must be at least 1")
    tables: dict[str, Iterable[Mapping[str, Any]]]
    if isinstance(data, Mapping):
        tables = dict(data)
        if not tables:
            raise DesignError("no tables given (empty mapping)")
    else:
        tables = {name: data}
    entities = [
        _entity(t, list(rows), max_key_size, max_determinant, max_group_ratio)
        for t, rows in tables.items()
    ]
    return DesignInput.from_dict(
        {"format": "shape-design", "version": 1, "name": name, "entities": entities}
    )


def _type(values: list[Any]) -> str:
    kinds = {_kind(v) for v in values if v is not None}
    if not kinds:
        return "string"
    if kinds == {"integer", "float"}:
        return "float"
    return kinds.pop() if len(kinds) == 1 else "string"


def _kind(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "float"
    if isinstance(v, Decimal):
        return "decimal"
    if isinstance(v, dt.datetime):
        return "timestamp"
    if isinstance(v, dt.date):
        return "date"
    if isinstance(v, dt.time):
        return "time"
    if isinstance(v, bytes | bytearray):
        return "binary"
    return "string"


def _entity(
    table: str,
    rows: list[Mapping[str, Any]],
    max_key_size: int,
    max_determinant: int,
    ratio: float,
) -> dict[str, Any]:
    if not rows:
        raise DesignError(f"table {table!r} has no rows")
    columns: list[str] = []
    for r in rows:
        for c in r:
            if c not in columns:
                columns.append(c)
    if not columns:
        raise DesignError(f"table {table!r} has no columns")
    values = {c: [r.get(c) for r in rows] for c in columns}
    attributes: list[dict[str, Any]] = []
    for c in columns:
        kind = _type(values[c])
        attr: dict[str, Any] = {
            "name": c,
            "type": kind,
            "nullable": any(v is None for v in values[c]),
        }
        if kind == "string":
            attr["max_length"] = max([1, *(len(str(v)) for v in values[c] if v is not None)])
        elif kind == "decimal":
            places = [
                -exp
                for v in values[c]
                if isinstance(v, Decimal) and isinstance(exp := v.as_tuple().exponent, int)
            ]
            attr["precision"] = 38
            attr["scale"] = max([0, *places])
        attributes.append(attr)
    if (max_key_size > 1 or max_determinant > 1) and len(columns) > MAX_COLUMNS_FOR_PAIRS:
        raise DesignError(
            f"table {table!r} has {len(columns)} columns; with more than "
            f"{MAX_COLUMNS_FOR_PAIRS} use max_key_size=1 and max_determinant=1"
        )
    n = len(rows)
    distinct = {c: len({repr(v) for v in values[c]}) for c in columns}
    keys: list[tuple[str, ...]] = []
    for size in range(1, max_key_size + 1):
        for fields in combinations(columns, size):
            if any(set(k) <= set(fields) for k in keys):
                continue
            if candidate_key(rows, fields).unique:
                keys.append(fields)
    usable = [c for c in columns if distinct[c] > 1 and None not in values[c]]
    found: dict[tuple[str, ...], list[str]] = {}
    for dep in columns:
        if distinct[dep] <= 1:
            continue
        accepted: list[tuple[str, ...]] = []
        for size in range(1, max_determinant + 1):
            for det in combinations([c for c in usable if c != dep], size):
                if any(set(k) <= set(det) for k in keys):
                    continue
                if any(set(a) <= set(det) for a in accepted):
                    continue
                ev = functional_dependency(rows, det, dep)
                if (
                    ev.violating_groups == 0
                    and not ev.truncated
                    and ev.determinant_groups <= ratio * n
                ):
                    accepted.append(det)
        for det in accepted:
            found.setdefault(det, []).append(dep)
    pos = {c: i for i, c in enumerate(columns)}
    dependencies = [
        {"determinant": list(det), "dependent": found[det]}
        for det in sorted(found, key=lambda d: [pos[c] for c in d])
    ]
    for a in attributes:
        if any(a["name"] in k for k in keys):
            a["nullable"] = False
    return {
        "name": table,
        "attributes": attributes,
        "keys": [list(k) for k in keys],
        "dependencies": dependencies,
    }
