"""Shape contracts and deterministic compatibility."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from shape import compat
from shape.spec.view import (
    columns_of,
    distinct_bounds,
    family,
    kind_matches,
    model_of,
    null_rate,
    table_of,
)


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
    def passed(self) -> bool:
        return not any(x.severity == "error" for x in self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "violations": [asdict(x) for x in self.violations]}


def _comparable(a: Any, b: Any) -> bool:
    numbers = (int, float)
    return (isinstance(a, numbers) and isinstance(b, numbers) and not isinstance(a, bool)) or (
        isinstance(a, str) and isinstance(b, str)
    )


def evaluate_contract(
    shape: Any, contract: Mapping[str, Any], table: str | None = None
) -> ContractReport:
    """Check a Shape (a v2 model, or a v1 capture that is migrated) against a contract.

    ``unique`` follows the evidence: with an exact distinct count the column is unique when it
    equals the number of non-null rows; with a sketch estimate it fails only when even the
    estimate widened by its error bound is below that number (P14). The null rate of an empty
    table is 0 (P15)."""
    compat.check_readable("contract", contract)
    t = table_of(model_of(shape), table)
    cols = columns_of(t)
    rows = int(t["rows"])
    out = []
    columns = contract.get("columns", {})
    if not isinstance(columns, Mapping):
        raise ValueError("a contract's 'columns' must be an object of column name to rules")
    for name, spec in columns.items():
        if not isinstance(spec, Mapping):
            raise ValueError(f"the rules for column {name!r} must be an object")
        c = cols.get(name)
        if c is None:
            if spec.get("required", True):
                out.append(
                    ContractViolation(f"columns.{name}", "missing", "required column is absent")
                )
            continue
        typ = spec.get("kind") or spec.get("type")
        if typ and not kind_matches(typ, c.get("kind")):
            out.append(
                ContractViolation(
                    f"columns.{name}.kind",
                    "type",
                    f"expected {typ}",
                    observed=c.get("kind"),
                    expected=typ,
                )
            )
        nr = null_rate(c, rows)
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
        if spec.get("unique"):
            non_null = rows - (c.get("null_count") or 0)
            low, high = distinct_bounds(c)
            if high < non_null:
                exact = bool(c.get("distinct_exact"))
                out.append(
                    ContractViolation(
                        f"columns.{name}",
                        "unique",
                        "column is not unique"
                        if exact
                        else "column is not unique (estimate, widened by its error bound)",
                        observed=low if exact else [low, high],
                        expected=non_null,
                    )
                )
        for metric in ("min", "max"):
            if metric in spec and c.get(metric) is not None:
                if not _comparable(c[metric], spec[metric]):
                    continue
                bad = c[metric] < spec[metric] if metric == "min" else c[metric] > spec[metric]
                if bad:
                    out.append(
                        ContractViolation(
                            f"columns.{name}.{metric}",
                            "range",
                            "range contract violated",
                            observed=c[metric],
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
    def compatible(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "compatible": self.compatible,
            "issues": [asdict(x) for x in self.issues],
        }


def compatibility(before: Any, after: Any, mode: str = "backward") -> CompatibilityReport:
    """Whether ``after`` can stand in for ``before`` (backward), the reverse (forward) or both
    (full). Both arguments are v2 models or v1 captures."""
    if mode not in ("backward", "forward", "full"):
        raise ValueError(f"mode must be backward, forward or full, not {mode!r}")
    old_model, new_model = model_of(before), model_of(after)
    many = len(old_model["tables"]) != 1 or len(new_model["tables"]) != 1

    def one(
        oc: dict[str, Any], nc: dict[str, Any], direction: str, at: str
    ) -> list[CompatibilityIssue]:
        issues = []
        for n, c in oc.items():
            if n not in nc:
                issues.append(
                    CompatibilityIssue(
                        f"{at}columns.{n}",
                        "removed",
                        f"column required by {direction} Shape is absent",
                    )
                )
            elif family(c.get("kind")) != family(nc[n].get("kind")):
                issues.append(
                    CompatibilityIssue(
                        f"{at}columns.{n}.kind",
                        "type_changed",
                        f"{c.get('kind')} -> {nc[n].get('kind')}",
                    )
                )
        return issues

    def pairs(a: dict[str, Any], b: dict[str, Any], direction: str) -> list[CompatibilityIssue]:
        if not many:
            return one(columns_of(table_of(a)), columns_of(table_of(b)), direction, "")
        found: list[CompatibilityIssue] = []
        for t, table in a["tables"].items():
            if t not in b["tables"]:
                found.append(
                    CompatibilityIssue(
                        f"tables.{t}", "removed", f"table required by {direction} Shape is absent"
                    )
                )
            else:
                found += one(
                    columns_of(table), columns_of(b["tables"][t]), direction, f"tables.{t}."
                )
        return found

    issues: list[CompatibilityIssue] = []
    if mode in ("backward", "full"):
        issues += pairs(old_model, new_model, "previous")
    if mode in ("forward", "full"):
        issues += pairs(new_model, old_model, "new")
    return CompatibilityReport(mode, tuple(issues))
