"""Read-only semantic metadata and the conservative known-answer DAX subset (W9-12)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.schema import GenSchema

from . import semantic_source as ss

if TYPE_CHECKING:
    from .known_answer import Measure


def read_measures(workspace: str, model: str) -> dict[str, list[dict[str, Any]]]:
    """Measures as additive table metadata; never passed as data columns."""
    fab = ss.check_model(workspace, model)
    try:
        frame = fab.list_measures(dataset=model, workspace=workspace)
    except Exception:  # noqa: BLE001 - remote bodies can carry credentials
        raise ShapeError(
            f"semantic model {model} in workspace {workspace}: cannot list measures"
        ) from None
    out: dict[str, list[dict[str, Any]]] = {}
    import pandas as pd  # type: ignore[import-untyped]

    def text(value: Any) -> str:
        return "" if pd.isna(value) else str(value)

    for _, row in frame.iterrows():
        hidden = row.get("Measure Hidden", False)
        measure = {
            "name": text(row["Measure Name"]),
            "expression": text(row["Measure Expression"]),
            "formatString": text(row.get("Format String", "")),
            "displayFolder": text(row.get("Measure Display Folder", "")),
            "hidden": False if pd.isna(hidden) else ss._truthy(hidden),
        }
        out.setdefault(str(row["Table Name"]), []).append(measure)
    return out


_PERMISSIONS = {
    "none": "none",
    "read": "read",
    "readrefresh": "readRefresh",
    "refresh": "refresh",
    "administrator": "administrator",
}


def _permission(value: Any) -> str:
    key = str(value).casefold()
    if key not in _PERMISSIONS:
        raise ShapeError("unsupported TOM model permission")
    return _PERMISSIONS[key]


def read_roles(workspace: str, model: str) -> list[dict[str, Any]]:
    """sempy 0.14.2: ``connect_semantic_model(..., readonly=True).model.Roles``."""
    fab = ss.check_model(workspace, model)
    if not callable(getattr(fab, "connect_semantic_model", None)):
        raise ShapeError(
            "this sempy has no connect_semantic_model; upgrade semantic-link-sempy "
            "(verified with 0.14.2)"
        )
    try:
        with fab.connect_semantic_model(dataset=model, workspace=workspace, readonly=True) as tom:
            return [
                {
                    "name": str(role.Name),
                    "modelPermission": _permission(role.ModelPermission),
                    "tablePermissions": [
                        {
                            "name": str(permission.Name),
                            "filterExpression": str(permission.FilterExpression or ""),
                        }
                        for permission in role.TablePermissions
                    ],
                }
                for role in tom.model.Roles
            ]
    except Exception:  # noqa: BLE001 - sempy and pythonnet expose their own errors
        raise ShapeError(
            f"semantic model {model} in workspace {workspace}: cannot read TOM roles"
        ) from None


def check_role(workspace: str, model: str, role: Any) -> str:
    if not isinstance(role, str) or not role:
        raise ShapeError("as_role must be a non-empty role name")
    names = [str(r["name"]) for r in read_roles(workspace, model)]
    if role not in names:
        raise ShapeError(
            f"unknown semantic model role {role!r}; roles: {', '.join(names) or '(none)'}"
        )
    # sempy inserts a role inside a quoted XMLA connection property. Refuse delimiters instead
    # of allowing an account-controlled role name to become another connection property.
    if '"' in role or ";" in role:
        raise ShapeError("as_role contains an unsupported XMLA connection delimiter")
    return role


def profile_document(profile: Any) -> dict[str, Any]:
    return dict(profile.to_dict() if hasattr(profile, "to_dict") else profile)


def export_metadata(profile: Any) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    doc = profile_document(profile)
    tables = doc.get("tables", {doc.get("name", "table"): doc})
    measures: dict[str, list[dict[str, Any]]] = {}
    for name, table in tables.items():
        measures[name] = []
        for raw in table.get("measures", []):
            measure = dict(raw)
            measure["isHidden"] = measure.pop("hidden", False)
            measures[name].append(measure)
    return measures, list(doc.get("roles", []))


_TABLE = r"(?P<table>'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_]*)"
_COLUMN = r"\[(?P<column>(?:[^\]]|\]\])*)\]"
_AGGREGATE = re.compile(
    rf"\s*(SUM|AVERAGE|MIN|MAX)\s*\(\s*{_TABLE}\s*{_COLUMN}\s*\)\s*", re.IGNORECASE
)
_COUNT = re.compile(rf"\s*COUNTROWS\s*\(\s*{_TABLE}\s*\)\s*", re.IGNORECASE)


def profile_measures(
    profile: Any, schema: GenSchema, tables: Mapping[str, pa.Table]
) -> tuple[list[Measure], list[dict[str, Any]]]:
    """Compute only exact supported whole-expression aggregates; arbitrary DAX is skipped.

    Expressions remain unchanged in the exported model, including unsupported expressions.
    """
    from .known_answer import Measure, _is_exact

    doc = profile_document(profile)
    definitions = doc.get("tables", {doc.get("name", "table"): doc})
    out: list[Measure] = []
    skipped: list[dict[str, Any]] = []
    for owner, definition in definitions.items():
        for raw in definition.get("measures", []):
            expression = raw["expression"]
            count = _COUNT.fullmatch(expression)
            aggregate = _AGGREGATE.fullmatch(expression)
            kind, table, column = "", "", None
            if count:
                kind, table = "count", count["table"]
            elif aggregate:
                kind = {"SUM": "sum", "AVERAGE": "avg", "MIN": "min", "MAX": "max"}[
                    aggregate[1].upper()
                ]
                table, column = aggregate["table"], aggregate["column"].replace("]]", "]")
            if table.startswith("'"):
                table = table[1:-1].replace("''", "'")
            column_kind = None
            supported = bool(kind) and table in schema.tables and table in tables
            if supported and column is not None:
                supported = column in schema.tables[table].columns
                if supported:
                    column_kind = schema.tables[table].columns[column].type
                    supported = column_kind in ("integer", "decimal", "float")
            if not supported:
                skipped.append(
                    {
                        "slice": None,
                        "measure": f"{owner}.{raw['name']}",
                        "reason": "unsupported DAX expression",
                    }
                )
                continue
            out.append(
                Measure(
                    raw["name"],
                    table,
                    kind,
                    column,
                    expression,
                    raw.get("formatString", ""),
                    _is_exact(kind, column_kind),
                    column_kind=column_kind,
                    home_table=owner if owner != table else None,
                )
            )
    return out, skipped
