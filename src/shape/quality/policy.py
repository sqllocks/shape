"""Shape-derived quality policy and enforcement."""

from __future__ import annotations

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
    def passed(self):
        return not any(v.severity == "error" for v in self.violations)


def validate_rows(rows: Iterable[Mapping[str, Any]], rules: tuple[Rule, ...]) -> QualityResult:
    out = []
    n = 0
    for i, r in enumerate(rows):
        n += 1
        for q in rules:
            v = r.get(q.field)
            bad = False
            if q.kind == "not_null":
                bad = v is None
            elif q.kind == "min":
                bad = v is not None and v < q.value
            elif q.kind == "max":
                bad = v is not None and v > q.value
            elif q.kind == "in":
                bad = v is not None and v not in q.value
            elif q.kind == "unique":
                continue
            else:
                raise ValueError(f"unknown rule {q.kind}")
            if bad:
                out.append(Violation(i, q.field, q.kind, q.severity, v))
    # exact uniqueness for bounded/reference use
    for q in (x for x in rules if x.kind == "unique"):
        seen = {}
        # uniqueness needs a second pass, so caller should supply sequence for this rule
        if not isinstance(rows, (list, tuple)):
            raise ValueError("unique rule requires materialized rows")
        for i, r in enumerate(rows):
            v = r.get(q.field)
            if v is not None and v in seen:
                out.append(Violation(i, q.field, "unique", q.severity, v))
            elif v is not None:
                seen[v] = i
    return QualityResult(n, tuple(out))
