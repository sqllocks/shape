from dataclasses import dataclass


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
    def passed(self):
        return all(r.passed or r.severity != "error" for r in self.results)


def evaluate(summary: dict, rules: dict):
    out = []
    for key, spec in rules.items():
        severity = "error"
        expect = spec
        if isinstance(spec, dict) and "severity" in spec:
            severity = spec.get("severity", "error")
            expect = {k: v for k, v in spec.items() if k != "severity"}
        v = summary.get(key)
        if isinstance(expect, dict):
            ok = v is not None and (
                ("min" not in expect or v >= expect["min"])
                and ("max" not in expect or v <= expect["max"])
            )
        else:
            ok = v == expect
        out.append(RuleResult(key, ok, v, expect, severity))
    return QualityReport(tuple(out))
