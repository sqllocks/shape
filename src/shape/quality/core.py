from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuleResult:
    rule: str
    passed: bool
    observed: object = None
    expected: object = None
    severity: str = "error"


@dataclass(frozen=True)
class QualityReport:
    results: tuple[RuleResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed or r.severity != "error" for r in self.results)


def _within(v: Any, expect: dict[str, Any]) -> bool:
    """True when ``v`` lies within ``min``/``max``; a value that cannot be compared does not."""
    if v is None:
        return False
    try:
        return bool(
            ("min" not in expect or v >= expect["min"])
            and ("max" not in expect or v <= expect["max"])
        )
    except TypeError:
        return False


def evaluate(summary: dict[str, Any], rules: dict[str, Any]) -> QualityReport:
    """Evaluate quality rules against a summary and return their findings."""
    out = []
    for key, spec in rules.items():
        severity = "error"
        expect = spec
        if isinstance(spec, dict) and "severity" in spec:
            severity = spec.get("severity", "error")
            expect = {k: v for k, v in spec.items() if k != "severity"}
        v = summary.get(key)
        ok = _within(v, expect) if isinstance(expect, dict) else v == expect
        out.append(RuleResult(key, ok, v, expect, severity))
    return QualityReport(tuple(out))
