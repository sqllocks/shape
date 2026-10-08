"""Leakage heuristics for Shapes and generated data; conservative, not a declassification proof."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LeakageFinding:
    kind: str
    severity: str
    detail: str


@dataclass(frozen=True, slots=True)
class LeakageReport:
    findings: tuple[LeakageFinding, ...]

    @property
    def releasable(self) -> bool:
        return not any(x.severity in {"high", "critical"} for x in self.findings)


def assess_summary(summary: dict, rare_threshold: int = 5) -> LeakageReport:
    out = []
    for item in summary.get("topk", []) or []:
        if len(item) >= 2 and item[1] <= rare_threshold:
            out.append(
                LeakageFinding("rare_value", "high", f"retained category count <= {rare_threshold}")
            )
    if summary.get("count", 0) and summary.get("distinct_estimate", 0) >= 0.95 * summary["count"]:
        out.append(
            LeakageFinding(
                "near_unique", "high", "field appears near-unique and may be identifying"
            )
        )
    return LeakageReport(tuple(out))
