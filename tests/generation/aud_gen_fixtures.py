"""Small schema builders for the AUD-gen regression tests."""

from __future__ import annotations

import copy
from typing import Any

from shape.generation.schema import GenSchema


def col(strategy: str, type_: str = "integer", **gen: Any) -> dict[str, Any]:
    nullable = gen.pop("nullable", False)
    null_rate = gen.pop("null_rate", 0.0)
    return {
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": nullable,
        "null_rate": null_rate,
    }


def build(
    tables: dict[str, tuple[list[str], dict[str, dict[str, Any]]]],
    rows: dict[str, int],
    rels: tuple[tuple[str, str, str, str], ...] = (),
    rules: tuple[dict[str, Any], ...] = (),
    corr: dict[str, list[list[Any]]] | None = None,
    seed: int = 5,
) -> GenSchema:
    """A schema from ``{table: (primary_key, {column: col(...)})}`` at ``rows``."""
    docs = {}
    for t, (pk, cols) in tables.items():
        columns = {}
        for c, cd in cols.items():
            cd = copy.deepcopy(cd)
            cd["name"] = c
            columns[c] = cd
        docs[t] = {"name": t, "primary_key": pk, "columns": columns}
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": seed},
        "tables": docs,
        "relationships": [
            {
                "name": f"{p}_{c}",
                "parent": p,
                "child": c,
                "parent_columns": [pc],
                "child_columns": [cc],
            }
            for p, c, pc, cc in rels
        ],
        "business_rules": list(rules),
        "correlated_columns": corr or {},
        "generation": {"scale": "s", "scales": {"s": rows}},
    }
    return GenSchema.from_dict(doc)
