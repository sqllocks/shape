"""Shape contracts and deterministic compatibility."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContractViolation:
    path: str
    code: str
    message: str
    severity: str = "error"
    observed: Any = None
    expected: Any = None


@dataclass(frozen=True, slots=True)
class ContractReport:
    violations: tuple[ContractViolation, ...]

    @property
    def passed(self):
        return not any(x.severity == "error" for x in self.violations)

    def to_dict(self):
        return {"passed": self.passed, "violations": [asdict(x) for x in self.violations]}


def evaluate_contract(shape: Mapping[str, Any], contract: Mapping[str, Any]) -> ContractReport:
    out = []
    cols = shape.get("columns", {})
    rows = max(1, int(shape.get("rows") or 0))
    for name, spec in contract.get("columns", {}).items():
        c = cols.get(name)
        if c is None:
            if spec.get("required", True):
                out.append(
                    ContractViolation(f"columns.{name}", "missing", "required column is absent")
                )
            continue
        typ = spec.get("kind") or spec.get("type")
        if typ and c.get("kind") != typ:
            out.append(
                ContractViolation(
                    f"columns.{name}.kind",
                    "type",
                    f"expected {typ}",
                    observed=c.get("kind"),
                    expected=typ,
                )
            )
        nr = (c.get("null_count") or 0) / rows
        if "nullable_max" in spec and nr > float(spec["nullable_max"]):
            out.append(
                ContractViolation(
                    f"columns.{name}.null_rate",
                    "null_rate",
                    "null rate exceeds maximum",
                    observed=nr,
                    expected=spec["nullable_max"],
                )
            )
        if spec.get("unique") and float(c.get("distinct_estimate") or 0) < rows - float(
            c.get("null_count") or 0
        ):
            out.append(ContractViolation(f"columns.{name}", "unique", "column is not unique"))
        for metric, key in (("min", "min"), ("max", "max")):
            if metric in spec and c.get(key) is not None:
                bad = (metric == "min" and c[key] < spec[metric]) or (
                    metric == "max" and c[key] > spec[metric]
                )
                if bad:
                    out.append(
                        ContractViolation(
                            f"columns.{name}.{key}",
                            "range",
                            "range contract violated",
                            observed=c[key],
                            expected=spec[metric],
                        )
                    )
    for name in contract.get("forbid_columns", ()):
        if name in cols:
            out.append(
                ContractViolation(f"columns.{name}", "forbidden", "forbidden column is present")
            )
    return ContractReport(tuple(out))


@dataclass(frozen=True, slots=True)
class CompatibilityIssue:
    path: str
    kind: str
    message: str


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    mode: str
    issues: tuple[CompatibilityIssue, ...]

    @property
    def compatible(self):
        return not self.issues

    def to_dict(self):
        return {
            "mode": self.mode,
            "compatible": self.compatible,
            "issues": [asdict(x) for x in self.issues],
        }


def compatibility(before, after, mode="backward"):
    if mode not in ("backward", "forward", "full"):
        raise ValueError("mode")

    def one(old, new, direction):
        issues = []
        oc = old.get("columns", {})
        nc = new.get("columns", {})
        for n, c in oc.items():
            if n not in nc:
                issues.append(
                    CompatibilityIssue(
                        f"columns.{n}", "removed", f"column required by {direction} Shape is absent"
                    )
                )
            elif c.get("kind") != nc[n].get("kind"):
                issues.append(
                    CompatibilityIssue(
                        f"columns.{n}.kind",
                        "type_changed",
                        f"{c.get('kind')} -> {nc[n].get('kind')}",
                    )
                )
        return issues

    issues = []
    if mode in ("backward", "full"):
        issues += one(before, after, "previous")
    if mode in ("forward", "full"):
        issues += one(after, before, "new")
    return CompatibilityReport(mode, tuple(issues))
