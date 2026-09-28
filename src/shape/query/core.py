"""Safe deterministic query language for Shape artifacts."""

from __future__ import annotations

import re


class ShapeQueryError(ValueError):
    pass


_TOKEN = re.compile(
    r'^(?P<root>rows|column|relationship|classification)\s*(?:\(\s*["\'](?P<arg>[^"\']+)["\']\s*(?:,\s*["\'](?P<arg2>[^"\']+)["\']\s*)?\))?(?P<path>(?:\.[A-Za-z_][A-Za-z0-9_]*)*)$'
)


def query(shape, expression):
    m = _TOKEN.match(expression.strip())
    if not m:
        raise ShapeQueryError("unsupported query syntax")
    for x in (m.group("arg"), m.group("arg2")):
        if x is not None and not re.match(r"^[A-Za-z0-9_ .:@-]+$", x):
            raise ShapeQueryError("unsafe query identifier")
    root = m.group("root")
    a = m.group("arg")
    b = m.group("arg2")
    if root == "rows":
        obj = shape.get("rows")
    elif root == "column":
        if not a:
            raise ShapeQueryError("column requires a name")
        obj = shape.get("columns", {}).get(a)
    elif root == "classification":
        if not a:
            raise ShapeQueryError("classification requires a field")
        obj = (shape.get("classifications") or {}).get(a) or (
            shape.get("columns", {}).get(a) or {}
        ).get("classification")
    else:
        if not a or not b:
            raise ShapeQueryError("relationship requires two fields")
        rel = shape.get("relationships", {})
        obj = None
        for kind, items in rel.items():
            if isinstance(items, list):
                for x in items:
                    vals = set(str(v) for v in x.values()) if isinstance(x, dict) else set()
                    if a in vals and b in vals:
                        obj = {"kind": kind, **x}
                        break
            if obj:
                break
    for part in [x for x in m.group("path").split(".") if x]:
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            raise ShapeQueryError(f"cannot access {part}")
    return obj


class ShapeView:
    def __init__(self, shape):
        self.shape = shape

    def query(self, expression):
        return query(self.shape, expression)

    def column(self, name):
        return self.shape.get("columns", {}).get(name)

    def relationship(self, a, b):
        return query(self.shape, f'relationship("{a}","{b}")')

    def classification(self, name):
        return query(self.shape, f'classification("{name}")')
