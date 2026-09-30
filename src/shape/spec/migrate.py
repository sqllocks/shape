"""Version migrations for declarative contracts."""

from __future__ import annotations


def migrate_dict(obj, target=1):
    current = int(obj.get("version", 1))
    if current > target:
        raise ValueError("downgrade is not supported")
    out = dict(obj)
    while current < target:
        raise ValueError(f"no migration registered from version {current}")
    return out


# ---------------------------------------------------------------------------------------------
# Shape model v1 -> v2 (P1-09). Read-only: nothing writes v1 any more.
# ---------------------------------------------------------------------------------------------

import copy  # noqa: E402
from collections.abc import Mapping  # noqa: E402
from typing import Any  # noqa: E402

from .model import MODEL_VERSION, ModelError, is_model, validate_model  # noqa: E402

CAPTURE_ENGINE = "shape-capture-v1"
_COUNTS = (
    "nan_count",
    "pos_inf_count",
    "neg_inf_count",
    "finite_count",
    "true_count",
    "false_count",
)
_QUANTILE_KEYS = {"q25": "0.25", "q50": "0.5", "q75": "0.75"}


def _count(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _number(v: Any) -> float | int | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _scalar(v: Any) -> Any:
    return v if v is None or isinstance(v, (str, int, float)) and not isinstance(v, bool) else None


def _error_models(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(k): dict(m)
        for k, m in raw.items()
        if isinstance(m, Mapping)
        and isinstance(m.get("algorithm"), str)
        and isinstance(m.get("exact"), bool)
    }


def _column_from_v1(name: str, c: Any, rows: int) -> dict[str, Any]:
    """A v1 capture column (``kind`` numeric/text, ``distinct_estimate``, ``topk``, ``q25``...)
    in the vocabulary the profile engine emits."""
    c = c if isinstance(c, Mapping) else {}
    kind = {"numeric": "float", "text": "text"}.get(str(c.get("kind")), "other")
    count = _count(c.get("count"))
    out: dict[str, Any] = {
        "name": name,
        "arrow_type": "unknown",
        "kind": kind,
        "count": rows if count is None else count,
        "null_count": _count(c.get("null_count")) or 0,
    }
    for key in _COUNTS:
        if (n := _count(c.get(key))) is not None:
            out[key] = n
    for key in ("min", "max"):
        if key in c:
            out[key] = _scalar(c[key])
    for key in ("mean", "variance_population", "variance_sample", "m2"):
        if key in c:
            out[key] = _number(c[key])
    est = _number(c.get("distinct_estimate", c.get("distinct")))
    if est is not None and est >= 0:
        out["distinct"] = est
        out["distinct_exact"] = bool(c.get("distinct_exact", False))
    top = c.get("topk", c.get("top"))
    if isinstance(top, (list, tuple)):
        items = []
        for it in top:
            if isinstance(it, (list, tuple)) and all(
                x is None or isinstance(x, (str, int, float)) and not isinstance(x, bool)
                for x in it
            ):
                items.append(list(it))
        out["top"] = items
    quant = {new: _number(c[old]) for old, new in _QUANTILE_KEYS.items() if old in c}
    quant = {k: v for k, v in quant.items() if v is not None}
    if isinstance(c.get("quantiles"), Mapping):
        quant.update({str(k): v for k, v in c["quantiles"].items() if _number(v) is not None})
    if quant:
        out["quantiles"] = quant
    if isinstance(c.get("length"), Mapping):
        out["length"] = dict(c["length"])
    if isinstance(c.get("classification"), str):
        out["classification"] = c["classification"]
    out["error_models"] = _error_models(c.get("error_models"))
    return out


def _relationships_from_v1(raw: Any) -> list[dict[str, Any]]:
    """v1 kept relationships as ``{kind: [{source, target, ...}]}``; v2 keeps a flat list whose
    entries carry their ``kind``. Entries without a source and a target are dropped."""
    if isinstance(raw, list):
        items = [(str(x.get("kind", "relationship")), x) for x in raw if isinstance(x, Mapping)]
    elif isinstance(raw, Mapping):
        items = [
            (str(kind), x)
            for kind, group in raw.items()
            if isinstance(group, (list, tuple))
            for x in group
            if isinstance(x, Mapping)
        ]
    else:
        return []
    out = []
    for kind, x in items:
        if isinstance(x.get("source"), str) and isinstance(x.get("target"), str):
            out.append({**copy.deepcopy(dict(x)), "kind": kind})
    return out


def migrate_capture_v1(obj: Mapping[str, Any], name: str | None = None) -> dict[str, Any]:
    """The v2 model of a v1 capture document ``{"rows": n, "columns": {name: {...}}}``.

    Every field the v1 document carries is kept verbatim under ``x_legacy``, so the v1
    consumers see exactly what they saw before."""
    if not isinstance(obj, Mapping):
        raise ModelError(f"a v1 shape must be an object, got {type(obj).__name__}")
    rows = _count(obj.get("rows")) or 0
    cols = obj.get("columns")
    cols = cols if isinstance(cols, Mapping) else {}
    table_name = name or (obj["name"] if isinstance(obj.get("name"), str) else "") or "table"
    doc: dict[str, Any] = {
        "schema_version": MODEL_VERSION,
        "engine": CAPTURE_ENGINE,
        "mode": "bounded",
        "name": table_name,
        "tables": {
            table_name: {
                "name": table_name,
                "rows": rows,
                "columns": [_column_from_v1(str(k), c, rows) for k, c in cols.items()],
            }
        },
        "x_legacy": copy.deepcopy(dict(obj)),
    }
    if relationships := _relationships_from_v1(obj.get("relationships")):
        doc["relationships"] = relationships
    raw_classes = obj.get("classifications")
    if isinstance(raw_classes, Mapping):
        classes = {str(k): v for k, v in raw_classes.items() if isinstance(v, str)}
        if classes:
            doc["classifications"] = classes
    return validate_model(doc)


def migrate_engine_v1(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The v2 model of a profile-engine v1 document (same layout, schema version 1)."""
    out = copy.deepcopy(dict(doc))
    out["schema_version"] = MODEL_VERSION
    return validate_model(out)


def to_model(obj: Any, name: str | None = None) -> dict[str, Any]:
    """Any Shape document as a validated v2 model: v2 as it is, an engine v1 document or a v1
    capture migrated."""
    if is_model(obj):
        return validate_model(obj)
    if isinstance(obj, Mapping) and obj.get("schema_version") == 1 and "tables" in obj:
        return migrate_engine_v1(obj)
    return migrate_capture_v1(obj, name)


def legacy_view(doc: Mapping[str, Any]) -> dict[str, Any]:
    """What the v1 consumers read: the verbatim v1 document of a migrated capture, else the
    model itself. Removed with those consumers (P1-10)."""
    legacy = doc.get("x_legacy")
    return copy.deepcopy(dict(legacy)) if isinstance(legacy, Mapping) else dict(doc)
