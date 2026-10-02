"""Field-by-field comparison of Shape's learned schema with the baseline's.

Internal harness (Shape venv). Both documents are brought to Shape's schema layout with
``schema_import.to_native`` (the importer's expansion of the baseline layout), then compared on
every model, table, column, relationship, scale and correlated-pair field. A difference is allowed
only when it is one of ``shape.generation.learn.DIFFERENCES`` and the column shows exactly the
pattern that rule produces; everything else is reported.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import schema_import  # noqa: E402

from shape.generation.learn import DIFFERENCES  # noqa: E402


def _explain(shape_col: dict[str, Any], base_col: dict[str, Any]) -> str | None:
    """The name of the deliberate difference that explains two columns, or ``None``."""
    sg, bg = shape_col["generator"], base_col["generator"]
    numeric = base_col["type"] in ("integer", "decimal")
    same_rest = all(shape_col[k] == base_col[k] for k in shape_col if k != "generator")
    if not (numeric and same_rest):
        return None
    if bg.get("strategy") == "weighted_enum" and sg.get("strategy") in ("distribution", "empirical"):
        # only when the baseline's list was cut short
        if len(bg["values"]) >= 500:
            return "truncated_enum"
    if (
        bg.get("strategy") == "distribution"
        and bg.get("distribution") == "normal"
        and sg.get("strategy") == "distribution"
        and sg.get("distribution") == "exponential"
    ):
        return "exponential"
    return None


def compare(shape_doc: dict[str, Any], baseline_doc: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    """``(unexplained differences, explained differences)``; the explained ones carry the rule
    name, the column and the reason."""
    s = schema_import.to_native(shape_doc) if "schema_version" not in shape_doc else shape_doc
    b = schema_import.to_native(baseline_doc)
    bad: list[str] = []
    explained: list[dict[str, str]] = []

    def check(path: str, x: Any, y: Any) -> None:
        if x != y:
            bad.append(f"{path}: shape {json.dumps(x, default=str)[:160]} != baseline {json.dumps(y, default=str)[:160]}")

    for key in ("name", "description", "domain", "schema_mode", "locale", "seed", "date_range"):
        check(f"model.{key}", s["model"][key], b["model"][key])
    check("table names", list(s["tables"]), list(b["tables"]))
    for tname in s["tables"].keys() & b["tables"].keys():
        st, bt = s["tables"][tname], b["tables"][tname]
        for key in ("description", "primary_key"):
            check(f"{tname}.{key}", st[key], bt[key])
        check(f"{tname} column names", list(st["columns"]), list(bt["columns"]))
        for cname in st["columns"].keys() & bt["columns"].keys():
            sc, bc = st["columns"][cname], bt["columns"][cname]
            if sc == bc:
                continue
            rule = _explain(sc, bc)
            if rule is not None:
                explained.append(
                    {"column": f"{tname}.{cname}", "rule": rule, "reason": DIFFERENCES[rule]}
                )
                continue
            for key in sc:
                check(f"{tname}.{cname}.{key}", sc[key], bc.get(key))
    check("relationships", s["relationships"], b["relationships"])
    check("business_rules", s["business_rules"], b["business_rules"])
    check("generation", s["generation"], b["generation"])
    check("correlated_columns", s["correlated_columns"], b["correlated_columns"])
    return bad, explained
