from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Delta:
    path: str
    kind: str
    before: Any
    after: Any
    magnitude: float | None = None


def diff_mapping(a: dict, b: dict, prefix=""):
    out = []
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
