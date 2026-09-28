from dataclasses import dataclass

from shape.errors import ShapeSecurityError
from shape.privacy import LeakageReport
from shape.security import Sensitivity


@dataclass(frozen=True)
class ReleaseDecision:
    allowed: bool
    reason: str
    evidence: tuple[str, ...] = ()


def authorize_release(
    source: Sensitivity,
    target: Sensitivity,
    explicit: bool = False,
    leakage: LeakageReport | None = None,
    approval_id: str | None = None,
):
    if target.dominates(source):
        return ReleaseDecision(True, "target is at least as restrictive")
    if not explicit:
        raise ShapeSecurityError("lower-trust release requires explicit governed authorization")
    if leakage is not None and not leakage.releasable:
        raise ShapeSecurityError("leakage assessment contains high/critical findings")
    if not approval_id:
        raise ShapeSecurityError("lower-trust release requires approval evidence")
    return ReleaseDecision(True, "governed lower-trust release authorized", (approval_id,))
