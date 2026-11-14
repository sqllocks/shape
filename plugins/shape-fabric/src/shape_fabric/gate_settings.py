"""Read gate policy from a same-workspace Fabric Variable Library's active value set.

No library name means defaults. A requested but unreadable library fails closed.
See integrations/fabric/RUNBOOK.md for the Microsoft Learn API references.
"""

from __future__ import annotations

import importlib
import json
import math
from dataclasses import dataclass
from typing import Any

from shape.drift.engine import resolve_policy


@dataclass(frozen=True)
class GateSettings:
    gate_mode: str
    thresholds: dict[str, Any]
    baseline_path: str


def load_gate_settings(library_name: str = "", *, utils: Any = None) -> GateSettings:
    """Read once using getLibrary/getVariable; never guess service error semantics."""
    values: dict[str, Any] = {"gateMode": "enforce", "thresholds": "{}", "baselinePath": ""}
    if library_name:
        try:
            if utils is None:
                utils = importlib.import_module("notebookutils")
            library = utils.variableLibrary.getLibrary(library_name)
            values = {name: library.getVariable(name) for name in values}
        except Exception:
            raise ValueError(
                "Cannot read Fabric Variable Library gate settings; check the library name, "
                "variables, permissions and supported notebook identity. "
                "Leave variableLibrary empty only to use defaults."
            ) from None
    if values["gateMode"] not in ("enforce", "report"):
        raise ValueError("Variable Library gateMode must be 'enforce' or 'report'")
    try:
        thresholds = json.loads(values["thresholds"])
        if not isinstance(thresholds, dict):
            raise ValueError
        resolve_policy(thresholds=thresholds)
        if any(isinstance(v, (int, float)) and not math.isfinite(v) for v in thresholds.values()):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError(
            "Variable Library thresholds must be a JSON object of valid Shape drift "
            "thresholds (finite nonnegative numbers, or min_severity: low/medium/high)"
        ) from None
    if not isinstance(values["baselinePath"], str) or "\x00" in values["baselinePath"]:
        raise ValueError("Variable Library baselinePath must be a path string without NUL")
    return GateSettings(values["gateMode"], thresholds, values["baselinePath"])
