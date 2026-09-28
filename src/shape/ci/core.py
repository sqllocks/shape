from __future__ import annotations

from dataclasses import dataclass

from shape.contracts import compatibility, evaluate_contract
from shape.drift import gate
from shape.generation.fidelity import certify_shapes


@dataclass(frozen=True, slots=True)
class CIGateReport:
    passed: bool
    checks: dict

    def to_dict(self):
        return {"passed": self.passed, "checks": self.checks}


def evaluate_ci(
    baseline,
    candidate,
    *,
    contract=None,
    compatibility_mode="backward",
    max_drift=0.25,
    min_fidelity=None,
):
    checks = {}
    cr = compatibility(baseline, candidate, compatibility_mode)
    checks["compatibility"] = cr.to_dict()
    if contract is not None:
        checks["contract"] = evaluate_contract(candidate, contract).to_dict()
    dr = gate(baseline, candidate, max_drift)
    checks["drift"] = dr.to_dict()
    if min_fidelity is not None:
        fc = certify_shapes(baseline, candidate)
        checks["fidelity"] = {
            **fc.to_dict(),
            "passed": fc.score >= min_fidelity,
            "minimum": min_fidelity,
        }
    passed = all(v.get("compatible", v.get("passed", True)) for v in checks.values())
    return CIGateReport(passed, checks)
