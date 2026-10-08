"""Safe deterministic query language for Shape artifacts."""

from __future__ import annotations

import re
from typing import Any

from shape.spec.view import columns_of, model_of, table_of


class ShapeQueryError(ValueError):
    pass


_TOKEN = re.compile(
    r'^(?P<root>rows|column|relationship|classification)\s*(?:\(\s*["\'](?P<arg>[^"\']+)["\']\s*(?:,\s*["\'](?P<arg2>[^"\']+)["\']\s*)?\))?(?P<path>(?:\.[A-Za-z_][A-Za-z0-9_]*)*)$'
)


_SYMMETRIC = frozenset(
    {"correlation", "correlations", "covariance", "association", "mutual_information"}
)


def _column(model: dict[str, Any], name: str) -> Any:
    tables = model["tables"]
    if len(tables) == 1:
        table = next(iter(tables.values()))
        return columns_of(table).get(name)
    if "." in name:
        tname, _, cname = name.partition(".")
        if tname in tables:
            return columns_of(tables[tname]).get(cname)
    return None


def _relationship(model: dict[str, Any], a: str, b: str) -> Any:
    """The relationship between exactly ``a`` and ``b``: ``source == a`` and ``target == b``
    (or the other way round for symmetric kinds such as correlations). One that merely
    mentions ``a`` or ``b`` is not a match (P21)."""
    for rel in model.get("relationships", ()):
        ends = (rel["source"], rel["target"])
        if ends == (a, b) or (rel["kind"] in _SYMMETRIC and ends == (b, a)):
            return dict(rel)
    return None


def query(shape: Any, expression: str) -> Any:
    """Evaluate a Shape Query over a v2 model (or a v1 capture, migrated).

    Roots: ``rows`` (or ``rows("table")``), ``column("name")``, ``classification("name")`` and
    ``relationship("a", "b")``, each followed by an optional ``.field.field`` path."""
    m = _TOKEN.match(expression.strip())
    if not m:
        raise ShapeQueryError("unsupported query syntax")
    for x in (m.group("arg"), m.group("arg2")):
        if x is not None and not re.match(r"^[A-Za-z0-9_ .:@-]+$", x):
            raise ShapeQueryError("unsafe query identifier")
    root = m.group("root")
    a = m.group("arg")
    b = m.group("arg2")
    model = model_of(shape)
    obj: Any
    if root == "rows":
        try:
            obj = table_of(model, a)["rows"]
        except (KeyError, ValueError) as e:
            raise ShapeQueryError(str(e)) from e
    elif root == "column":
        if not a:
            raise ShapeQueryError("column requires a name")
        obj = _column(model, a)
    elif root == "classification":
        if not a:
            raise ShapeQueryError("classification requires a field")
        obj = (model.get("classifications") or {}).get(a)
        if obj is None:
            obj = (_column(model, a) or {}).get("classification")
    else:
        if not a or not b:
            raise ShapeQueryError("relationship requires two fields")
        obj = _relationship(model, a, b)
    for part in [x for x in m.group("path").split(".") if x]:
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            raise ShapeQueryError(f"cannot access {part}")
    return obj


class ShapeView:
    def __init__(self, shape: Any) -> None:
        self.shape = shape

    def query(self, expression: str) -> Any:
        return query(self.shape, expression)

    def column(self, name: str) -> Any:
        return _column(model_of(self.shape), name)

    def relationship(self, a: str, b: str) -> Any:
        return query(self.shape, f'relationship("{a}","{b}")')

    def classification(self, name: str) -> Any:
        return query(self.shape, f'classification("{name}")')
