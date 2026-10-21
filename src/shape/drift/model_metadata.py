"""Semantic measure and role changes: reported metadata, with no numeric drift score."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

KINDS = frozenset({"measure_change", "role_change"})


def _document(profile: Any) -> Mapping[str, Any]:
    if isinstance(profile, Mapping):
        return profile
    data = getattr(profile, "_data", None)
    if isinstance(data, Mapping):
        return data
    doc = profile.to_dict() if hasattr(profile, "to_dict") else profile
    return doc if isinstance(doc, Mapping) else {}


def _named(entries: Any) -> dict[str, Any]:
    if not isinstance(entries, list):
        return {}
    return {
        str(item["name"]): item for item in entries if isinstance(item, Mapping) and "name" in item
    }


def records(baseline: Any, current: Any) -> list[tuple[str | None, str | None, dict[str, Any]]]:
    before, after = _document(baseline), _document(current)
    out: list[tuple[str | None, str | None, dict[str, Any]]] = []

    def compare(table: str | None, kind: str, left: Any, right: Any) -> None:
        b, a = _named(left), _named(right)

        def normalized(value: Any) -> Any:
            if kind != "role_change" or value is None:
                return value
            return {
                **value,
                "tablePermissions": sorted(
                    value.get("tablePermissions", []), key=lambda p: p["name"]
                ),
            }

        for name in sorted(set(b) | set(a)):
            if normalized(b.get(name)) != normalized(a.get(name)):
                record: dict[str, Any] = {
                    "column": None,
                    "kind": kind,
                    "baseline": deepcopy(b.get(name)),
                    "current": deepcopy(a.get(name)),
                    "severity": "low",
                    "score": 0.0,
                    "detail": {"name": name, "numeric_gate": False},
                }
                if table is not None:
                    record["table"] = table
                out.append((table, None, record))

    compare(None, "role_change", before.get("roles", []), after.get("roles", []))
    bt = before.get("tables", {before.get("name", "table"): before})
    at = after.get("tables", {after.get("name", "table"): after})
    if isinstance(bt, Mapping) and isinstance(at, Mapping):
        for table in sorted(set(bt) | set(at)):
            compare(
                str(table),
                "measure_change",
                bt.get(table, {}).get("measures", []),
                at.get(table, {}).get("measures", []),
            )
    return out
