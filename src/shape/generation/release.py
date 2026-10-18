"""Synthetic release gate combines fidelity and privacy evidence."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    allowed: bool
    reasons: tuple[str, ...]


def release_decision(fidelity_certificate, k_result=None, min_k=5):
    """Apply the fidelity result and minimum equivalence-group threshold to a release decision."""
    reasons = []
    if not fidelity_certificate.passed:
        reasons.append("fidelity_tolerance_failed")
    if k_result is not None and k_result.k < min_k:
        reasons.append(f"k_anonymity_below_{min_k}")
    return ReleaseDecision(not reasons, tuple(reasons))
