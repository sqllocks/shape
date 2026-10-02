"""Conditions: validation, and compilation into vectorized tests over rows of entities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from shape_behavior.timeutil import to_us, unit_us

OPS = ("==", "!=", "<", "<=", ">", ">=")
NIL_OPS = ("is_nil", "is_not_nil")
LEAF_TYPES = ("true", "false", "attribute", "age", "date", "active_condition", "active_medication")
GROUP_TYPES = ("and", "or", "at_least", "at_most")
_CMP: dict[str, Callable[[Any, Any], Any]] = {
    "==": np.equal,
    "!=": np.not_equal,
    "<": np.less,
    "<=": np.less_equal,
    ">": np.greater,
    ">=": np.greater_equal,
}
_Test = Callable[[Any, Any, Any], Any]


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return None


def check(doc: Any, where: str) -> list[str]:
    """Problems with a condition document, empty when it is fine."""
    if not isinstance(doc, dict):
        return [f"{where}: a condition must be an object"]
    kind = doc.get("type")
    if kind in ("true", "false"):
        return []
    if kind == "attribute":
        out = [] if isinstance(doc.get("attribute"), str) else [f"{where}: needs 'attribute'"]
        op = doc.get("op")
        if op in NIL_OPS:
            return out
        if op not in OPS:
            return [*out, f"{where}: op must be one of {', '.join(OPS + NIL_OPS)}"]
        if "value" not in doc:
            out.append(f"{where}: needs 'value'")
        elif isinstance(doc["value"], str) and op not in ("==", "!="):
            out.append(f"{where}: text attributes only compare with == and !=")
        elif _num(doc["value"]) is None and not isinstance(doc["value"], str):
            out.append(f"{where}: value must be a number, a bool or text")
        return out
    if kind in ("age", "date"):
        out = []
        if doc.get("op") not in OPS:
            out.append(f"{where}: op must be one of {', '.join(OPS)}")
        if "value" not in doc:
            return [*out, f"{where}: needs 'value'"]
        try:
            if kind == "age":
                if _num(doc["value"]) is None:
                    raise ValueError("age value must be a number")
                unit_us(doc.get("unit", "years"))
            else:
                to_us(doc["value"])
        except (ValueError, TypeError) as exc:
            out.append(f"{where}: {exc}")
        return out
    if kind in ("active_condition", "active_medication"):
        codes = doc.get("codes")
        if not isinstance(codes, list) or not codes or not all(code_of(c) for c in codes):
            return [f"{where}: needs a non-empty list of 'codes'"]
        return []
    if kind in GROUP_TYPES:
        subs = doc.get("conditions")
        if not isinstance(subs, list) or not subs:
            return [f"{where}: needs a non-empty list of 'conditions'"]
        out = []
        if kind in ("at_least", "at_most"):
            key = "minimum" if kind == "at_least" else "maximum"
            if isinstance(doc.get(key), bool) or not isinstance(doc.get(key), int):
                out.append(f"{where}: needs an integer {key!r}")
        for i, sub in enumerate(subs):
            out += check(sub, f"{where}.conditions[{i}]")
        return out
    if kind == "not":
        return check(doc.get("condition"), f"{where}.condition")
    return [f"{where}: unknown condition type {kind!r}"]


def code_of(c: Any) -> str | None:
    """The code string of a ``codes`` entry (a dict with ``code``, or the string itself)."""
    if isinstance(c, str) and c:
        return c
    if isinstance(c, dict) and isinstance(c.get("code"), str) and c["code"]:
        return str(c["code"])
    return None


def walk(doc: Any) -> list[dict[str, Any]]:
    """Every condition node in ``doc``, the root first."""
    out = [doc]
    if doc.get("type") in GROUP_TYPES:
        for sub in doc["conditions"]:
            out += walk(sub)
    elif doc.get("type") == "not":
        out += walk(doc["condition"])
    return out


class Compiled:
    """A compiled condition: ``test(store, rows, time)`` gives a bool array over the rows;
    ``ready`` (``None`` unless the condition depends only on age) gives the earliest time at
    which it holds, ``INF`` for never."""

    def __init__(self, test: _Test, ready: Callable[[Any, Any], Any] | None = None) -> None:
        self.test = test
        self.ready = ready


def compile_condition(doc: dict[str, Any], bit: Callable[[str, str], int]) -> Compiled:
    """Compile ``doc``; ``bit(kind, code)`` gives the bit of a code in the ``condition`` or
    ``medication`` active set."""
    kind = doc["type"]
    if kind == "true":
        return Compiled(lambda s, r, t: np.ones(len(r), bool))
    if kind == "false":
        return Compiled(lambda s, r, t: np.zeros(len(r), bool))
    if kind == "attribute":
        return Compiled(_attribute_test(doc))
    if kind == "age":
        return _age(doc)
    if kind == "date":
        value = to_us(doc["value"])
        cmp = _CMP[doc["op"]]
        return Compiled(lambda s, r, t: cmp(t, value))
    if kind in ("active_condition", "active_medication"):
        mask = 0
        which = "condition" if kind == "active_condition" else "medication"
        for c in doc["codes"]:
            mask |= 1 << bit(which, str(code_of(c)))
        field = "cond_mask" if which == "condition" else "med_mask"
        m = np.uint64(mask)
        return Compiled(lambda s, r, t: (getattr(s, field)[r] & m) != 0)
    if kind == "not":
        inner = compile_condition(doc["condition"], bit).test
        return Compiled(lambda s, r, t: ~inner(s, r, t))
    subs = [compile_condition(c, bit) for c in doc["conditions"]]
    tests = [c.test for c in subs]
    if kind == "and":
        ready = _and_ready(subs)
        return Compiled(lambda s, r, t: np.logical_and.reduce([f(s, r, t) for f in tests]), ready)
    if kind == "or":
        return Compiled(lambda s, r, t: np.logical_or.reduce([f(s, r, t) for f in tests]))
    count = doc.get("minimum" if kind == "at_least" else "maximum", 0)

    def counted(s: Any, r: Any, t: Any) -> Any:
        n = np.sum([f(s, r, t) for f in tests], axis=0)
        return n >= count if kind == "at_least" else n <= count

    return Compiled(counted)


def _and_ready(subs: list[Compiled]) -> Callable[[Any, Any], Any] | None:
    readies = [c.ready for c in subs]
    if any(r is None for r in readies):
        return None
    fns: list[Callable[[Any, Any], Any]] = [r for r in readies if r is not None]
    return lambda s, r: np.maximum.reduce([f(s, r) for f in fns])


def _attribute_test(doc: dict[str, Any]) -> _Test:
    name, op = doc["attribute"], doc["op"]
    if op == "is_nil":
        return lambda s, r, t: s.is_nil(name, r)
    if op == "is_not_nil":
        return lambda s, r, t: ~s.is_nil(name, r)
    value = doc["value"]
    cmp = _CMP[op]
    if isinstance(value, str):

        def text_test(s: Any, r: Any, t: Any) -> Any:
            if name in s.num:  # a numeric attribute never equals text
                return np.full(len(r), op == "!=")
            code = s.vindex.get(name, {}).get(value, -2)
            return cmp(s.cat[name][r], code) if name in s.cat else np.full(len(r), op == "!=")

        return text_test
    num = float(_num(value) or 0.0)

    def num_test(s: Any, r: Any, t: Any) -> Any:
        if name not in s.num:
            return np.zeros(len(r), bool)
        x = s.num[name][r]
        res = cmp(x, num)
        return res | np.isnan(x) if op == "!=" else res

    return num_test


def _age(doc: dict[str, Any]) -> Compiled:
    threshold = round(float(doc["value"]) * unit_us(doc.get("unit", "years")))
    op = doc["op"]
    cmp = _CMP[op]

    def test(s: Any, r: Any, t: Any) -> Any:
        return cmp(t - s.born[r], threshold)

    ready: Callable[[Any, Any], Any] | None = None
    if op in (">=", ">"):
        # the first microsecond at which age >= value (or > value)
        def ready(s: Any, r: Any) -> Any:
            return s.born[r] + threshold + (1 if op == ">" else 0)

    elif op in ("<", "<="):
        # holds from the start and stops: handled by the caller as "now or never"
        ready = None
    return Compiled(test, ready)
