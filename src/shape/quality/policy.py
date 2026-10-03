"""Shape-derived quality policy and enforcement."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Rule:
    field: str
    kind: str
    value: Any = None
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class Violation:
    row: int
    field: str
    rule: str
    severity: str
    observed: Any


@dataclass(frozen=True, slots=True)
class QualityResult:
    rows: int
    violations: tuple[Violation, ...]

    @property
    def passed(self) -> bool:
        return not any(v.severity == "error" for v in self.violations)


_KINDS = ("not_null", "min", "max", "in", "unique")


def _breaks(rule: Rule, v: Any) -> bool:
    """True when ``v`` breaks ``rule`` (a value that cannot be compared with the bound breaks
    it: ``"x"`` is not at least 0)."""
    if rule.kind == "not_null":
        return v is None
    if v is None:
        return False
    try:
        if rule.kind == "min":
            return bool(v < rule.value)
        if rule.kind == "max":
            return bool(v > rule.value)
        return v not in rule.value
    except TypeError:
        return True


def _key(v: Any) -> Any:
    """A hashable stand-in for ``v`` (a list or dict compares by its JSON text)."""
    try:
        hash(v)
    except TypeError:
        return ("unhashable", json.dumps(v, sort_keys=True, default=str))
    return v


def validate_rows(rows: Iterable[Mapping[str, Any]], rules: tuple[Rule, ...]) -> QualityResult:
    """Check every row against ``rules``. An unknown rule kind, or a ``unique`` rule on rows that
    are not a list or tuple (uniqueness needs a second pass), is refused before a row is read."""
    for q in rules:
        if q.kind not in _KINDS:
            raise ValueError(f"unknown rule {q.kind} (known: {', '.join(_KINDS)})")
    unique = [q for q in rules if q.kind == "unique"]
    if unique and not isinstance(rows, (list, tuple)):
        raise ValueError("unique rule requires materialized rows")
    out: list[Violation] = []
    n = 0
    for i, r in enumerate(rows):
        n += 1
        for q in rules:
            if q.kind == "unique":
                continue
            v = r.get(q.field)
            if _breaks(q, v):
                out.append(Violation(i, q.field, q.kind, q.severity, v))
    # exact uniqueness for bounded/reference use
    for q in unique:
        seen: set[Any] = set()
        for i, r in enumerate(rows):
            v = r.get(q.field)
            if v is None:
                continue
            k = _key(v)
            if k in seen:
                out.append(Violation(i, q.field, "unique", q.severity, v))
            else:
                seen.add(k)
    return QualityResult(n, tuple(out))
