"""Shape-aware ETL runtime: capture before/after every transformation and enforce gates."""

from __future__ import annotations

from dataclasses import dataclass

from shape.capture import capture_rows
from shape.ci import evaluate_ci


def _capture(rows):
    if not rows:
        return {"rows": 0, "columns": {}}
    keys = tuple(rows[0])
    if all(tuple(r) == keys for r in rows):
        from shape.capture.vectorized import capture_columns

        return capture_columns({k: [r[k] for r in rows] for k in keys})
    return capture_rows(rows).to_dict()


@dataclass(frozen=True, slots=True)
class StageEvidence:
    name: str
    before: dict
    after: dict
    gate: dict | None


@dataclass(frozen=True, slots=True)
class ETLResult:
    rows: object
    evidence: tuple[StageEvidence, ...]


class ShapeETL:
    def __init__(self):
        self._stages = []

    def stage(self, name, fn, contract=None, max_drift=1.0):
        self._stages.append((name, fn, contract, max_drift))
        return self

    def run(self, rows):
        current = list(rows)
        evidence = []
        for name, fn, contract, max_drift in self._stages:
            before = _capture(current)
            current = list(fn(current))
            after = _capture(current)
            gate = (
                evaluate_ci(before, after, contract=contract, max_drift=max_drift).to_dict()
                if contract is not None or max_drift < 1
                else None
            )
            if gate and not gate["passed"]:
                raise ValueError(f"Shape ETL gate failed at {name}: {gate}")
            evidence.append(StageEvidence(name, before, after, gate))
        return ETLResult(current, tuple(evidence))
