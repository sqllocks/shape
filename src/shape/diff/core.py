from dataclasses import dataclass
from typing import Any

from shape.spec.view import columns_of, model_of


@dataclass(frozen=True)
class Delta:
    path: str
    kind: str
    before: Any
    after: Any
    magnitude: float | None = None


def diff_mapping(a: dict[str, Any], b: dict[str, Any], prefix: str = "") -> list[Delta]:
    out: list[Delta] = []
    for k in sorted(set(a) | set(b)):
        p = f"{prefix}.{k}" if prefix else k
        if k not in a:
            out.append(Delta(p, "added", None, b[k]))
        elif k not in b:
            out.append(Delta(p, "removed", a[k], None))
        elif isinstance(a[k], dict) and isinstance(b[k], dict):
            out.extend(diff_mapping(a[k], b[k], p))
        elif a[k] != b[k]:
            mag = None
            if isinstance(a[k], (int, float)) and isinstance(b[k], (int, float)):
                mag = abs(float(b[k]) - float(a[k]))
            out.append(Delta(p, "changed", a[k], b[k], mag))
    return out


def diff_models(before: Any, after: Any) -> list[Delta]:
    """Typed deltas between two Shapes (v2 models, or v1 captures that are migrated): rows per
    table and every column field, as ``tables.<table>.columns.<column>.<field>``; added and
    removed tables and columns are one delta each."""
    b, a = model_of(before), model_of(after)
    out: list[Delta] = []
    for tname in sorted(set(b["tables"]) | set(a["tables"])):
        path = f"tables.{tname}"
        if tname not in b["tables"]:
            out.append(Delta(path, "added", None, a["tables"][tname]["name"]))
            continue
        if tname not in a["tables"]:
            out.append(Delta(path, "removed", b["tables"][tname]["name"], None))
            continue
        bt, at = b["tables"][tname], a["tables"][tname]
        out.extend(diff_mapping({"rows": bt["rows"]}, {"rows": at["rows"]}, path))
        out.extend(diff_mapping(columns_of(bt), columns_of(at), f"{path}.columns"))
    return out
