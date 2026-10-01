"""A small evaluator for the subset of the Azure Data Factory expression language that the
Synapse and ADF pipeline definitions use, so tests can run their gate expressions against real
exit values and gate documents.

Supported: string literals (``'it''s'``), numbers, ``true``/``false``/``null``, property paths
(``a.b.c``), and the functions concat, string, json, if, empty, equals, and, or, not. The
context has ``parameters`` (pipeline parameters), ``run_id``, ``activities`` (name -> output,
read as ``activity('name').output...``) and ``dataset``. Anything else raises, so an expression
that uses a function this evaluator does not know fails loudly instead of passing unchecked.
"""

from __future__ import annotations

import json
import re
from typing import Any

_TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|([A-Za-z_][A-Za-z0-9_]*)|(-?\d+(?:\.\d+)?)|([().,]))")


class ExpressionError(Exception):
    pass


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m:
            raise ExpressionError(f"cannot tokenize {text[pos:]!r}")
        pos = m.end()
        for kind, value in zip(("str", "name", "num", "punct"), m.groups(), strict=True):
            if value is not None:
                out.append((kind, value))
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], ctx: dict[str, Any]):
        self.t, self.i, self.ctx = tokens, 0, ctx

    def peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None or (value is not None and tok[1] != value):
            raise ExpressionError(f"expected {value!r}, got {tok!r}")
        self.i += 1
        return tok

    def expression(self) -> Any:
        value = self.primary()
        while self.peek() == ("punct", "."):
            self.take(".")
            key = self.take()[1]
            value = self.member(value, key)
        return value

    @staticmethod
    def member(value: Any, key: str) -> Any:
        if not isinstance(value, dict) or key not in value:
            raise ExpressionError(f"no property {key!r} on {type(value).__name__}")
        return value[key]

    def args(self) -> list[Any]:
        self.take("(")
        out: list[Any] = []
        if self.peek() == ("punct", ")"):
            self.take(")")
            return out
        while True:
            out.append(self.expression())
            if self.peek() == ("punct", ","):
                self.take(",")
                continue
            self.take(")")
            return out

    def primary(self) -> Any:
        kind, value = self.take()
        if kind == "str":
            return value[1:-1].replace("''", "'")
        if kind == "num":
            return float(value) if "." in value else int(value)
        if kind != "name":
            raise ExpressionError(f"unexpected {value!r}")
        if value in ("true", "false", "null") and self.peek() != ("punct", "("):
            return {"true": True, "false": False, "null": None}[value]
        if self.peek() != ("punct", "("):
            raise ExpressionError(f"bare name {value!r}")
        if value == "if":  # lazy: only the taken branch is evaluated
            return self.lazy_if()
        return self.call(value, self.args())

    def lazy_if(self) -> Any:
        self.take("(")
        cond = self.expression()
        self.take(",")
        a = self.expression()
        self.take(",")
        b = self.expression()
        self.take(")")
        return a if cond else b

    def call(self, name: str, a: list[Any]) -> Any:
        c = self.ctx
        if name == "pipeline":
            return {"parameters": c["parameters"], "RunId": c.get("run_id", "run-1")}
        if name == "dataset":
            return c["dataset"]
        if name == "activity":
            return {"output": c["activities"][a[0]]}
        if name == "concat":
            return "".join(_text(x) for x in a)
        if name == "string":
            return _text(a[0])
        if name == "json":
            return json.loads(a[0])
        if name == "empty":
            return a[0] in (None, "", [], {})
        if name == "equals":
            return a[0] == a[1]
        if name == "and":
            return all(a)
        if name == "or":
            return any(a)
        if name == "not":
            return not a[0]
        raise ExpressionError(f"function {name!r} is not supported by the test evaluator")


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return "" if value is None else str(value)


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """Evaluate one ``@...`` expression (or ``{"value": ..., "type": "Expression"}``)."""
    if isinstance(expression, dict):
        expression = expression["value"]
    if not expression.startswith("@"):
        raise ExpressionError(f"not an expression: {expression!r}")
    parser = _Parser(_tokens(expression[1:]), context)
    value = parser.expression()
    if parser.peek() is not None:
        raise ExpressionError(f"trailing input in {expression!r}")
    return value
