"""Import Generic Module Framework (GMF) JSON modules into native modules.

Reads modules the *user* supplies; nothing here ships a module or any terminology. The common
subset of states, transitions and conditions is converted (``docs/plugins/behavior.md``,
section 6); everything else is reported in :attr:`ImportResult.unsupported`, never dropped
silently. With ``strict=True`` the first unsupported element raises :class:`UnsupportedGmfError`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape_behavior.model import Module
from shape_behavior.timeutil import to_us

GMF_TYPES = (
    "Initial", "Terminal", "Simple", "Delay", "Guard", "SetAttribute", "Counter", "Encounter",
    "EncounterEnd", "ConditionOnset", "ConditionEnd", "MedicationOrder", "MedicationEnd",
    "Procedure", "Observation", "Death", "CallSubmodule", "Symptom", "VitalSign", "Device",
    "DeviceEnd", "CarePlanStart", "CarePlanEnd", "AllergyOnset", "AllergyEnd", "ImagingStudy",
    "SupplyList", "MultiObservation", "DiagnosticReport", "Physiology", "Vaccine",
)  # fmt: skip
_OPS = {
    "==": "==",
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
    "is nil": "is_nil",
    "is not nil": "is_not_nil",
}
_SIMPLE = {
    "Initial": "initial",
    "Terminal": "terminal",
    "Simple": "simple",
    "EncounterEnd": "encounter_end",
    "Death": "death",
}
_TRANSITIONS = (
    "direct_transition",
    "distributed_transition",
    "conditional_transition",
    "complex_transition",
    "lookup_table_transition",
)


class UnsupportedGmfError(ValueError):
    """``strict`` import met a state, transition or condition outside the supported subset."""


@dataclass
class Unsupported:
    """One element that could not be imported: where, what it is and why."""

    state: str
    type: str
    reason: str

    def __str__(self) -> str:
        return f"{self.state}: {self.type}: {self.reason}"


@dataclass
class ImportResult:
    module: Module
    unsupported: list[Unsupported] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [f"module {self.module.name!r}: {len(self.module.states)} states imported"]
        if self.unsupported:
            lines.append(f"unsupported ({len(self.unsupported)}):")
            lines += [f"  - {u}" for u in self.unsupported]
        if self.warnings:
            lines.append(f"warnings ({len(self.warnings)}):")
            lines += [f"  - {w}" for w in self.warnings]
        if not self.unsupported and not self.warnings:
            lines.append("everything was supported")
        return "\n".join(lines)


def is_gmf(doc: Any) -> bool:
    """Whether ``doc`` looks like a GMF module (rather than a native one)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("states"), dict) or "format" in doc:
        return False
    if "gmf_version" in doc:
        return True
    return any(isinstance(s, dict) and s.get("type") in GMF_TYPES for s in doc["states"].values())


def import_gmf(source: Any, *, strict: bool = False) -> ImportResult:
    """Import a GMF module given as a path, a JSON string or a ``dict``."""
    doc: Any = source
    if isinstance(source, Path) or (
        isinstance(source, str) and not source.lstrip().startswith("{")
    ):
        doc = json.loads(Path(source).read_text(encoding="utf-8"))
    elif isinstance(source, str):
        doc = json.loads(source)
    if not is_gmf(doc):
        raise ValueError("not a Generic Module Framework document (needs 'states' of GMF types)")
    return _Importer(doc, strict).run()


class _Importer:
    def __init__(self, doc: dict[str, Any], strict: bool) -> None:
        self.doc = doc
        self.strict = strict
        self.unsupported: list[Unsupported] = []
        self.warnings: list[str] = []
        self.state = ""

    def miss(self, kind: str, reason: str) -> None:
        item = Unsupported(self.state, kind, reason)
        if self.strict:
            raise UnsupportedGmfError(str(item))
        self.unsupported.append(item)

    def run(self) -> ImportResult:
        states: dict[str, Any] = {}
        initial = None
        for name, s in self.doc["states"].items():
            self.state = name
            states[name] = self.convert_state(name, s)
            if s.get("type") == "Initial" and initial is None:
                initial = name
        out: dict[str, Any] = {
            "format": "shape-behavior/1",
            "name": str(self.doc.get("name", "gmf_module")),
            "states": states,
        }
        if initial is not None:
            out["initial"] = initial
        if "remarks" in self.doc:
            out["remarks"] = self.doc["remarks"]
        module = Module(out)
        return ImportResult(module, self.unsupported, self.warnings)

    # -- states --------------------------------------------------------------------------------

    def convert_state(self, name: str, s: dict[str, Any]) -> dict[str, Any]:
        kind = s.get("type")
        out: dict[str, Any] | None = None
        if kind in _SIMPLE:
            out = {"type": _SIMPLE[kind]}
        elif kind == "Delay":
            d = self.duration(s)
            out = {"type": "delay", "delay": d} if d else None
        elif kind == "Guard":
            out = {"type": "guard", "condition": self.condition(s.get("allow") or {})}
        elif kind == "SetAttribute":
            out = self.set_attribute(s)
        elif kind == "Counter":
            out = {
                "type": "counter",
                "attribute": s.get("attribute"),
                "action": s.get("action", "increment"),
                "amount": s.get("amount", 1),
            }
        elif kind == "Encounter":
            out = {
                "type": "encounter",
                "codes": s.get("codes", []),
                "encounter_class": s.get("encounter_class"),
            }
            if s.get("wellness"):
                self.warnings.append(f"{name}: wellness encounter treated as immediate")
        elif kind in ("ConditionOnset", "MedicationOrder", "Procedure"):
            out = self.coded(
                s,
                {
                    "ConditionOnset": "condition_onset",
                    "MedicationOrder": "medication_order",
                    "Procedure": "procedure",
                }[kind],
            )
        elif kind in ("ConditionEnd", "MedicationEnd"):
            out = self.end(s, kind)
        elif kind == "Observation":
            out = self.observation(s)
        else:
            self.miss(
                str(kind), "state type is not in the supported subset; runs as a pass-through"
            )
        if out is None:
            out = {"type": "simple"}
        out = {k: v for k, v in out.items() if v is not None}
        if out["type"] in ("terminal", "death"):
            return out
        t = self.transition(s)
        out["transition"] = t if t is not None else {"stop": True}
        return out

    def duration(self, s: dict[str, Any]) -> dict[str, Any] | None:
        if "exact" in s:
            e = s["exact"]
            return {"kind": "exact", "value": e.get("quantity", 0), "unit": e.get("unit", "days")}
        if "range" in s:
            r = s["range"]
            return {
                "kind": "uniform",
                "low": r.get("low", 0),
                "high": r.get("high", 0),
                "unit": r.get("unit", "days"),
            }
        if "distribution" in s:
            d = self.distribution(s["distribution"])
            if d is not None:
                d["unit"] = s["distribution"].get("unit", "days")
                return d
            return None
        self.miss("Delay", "no exact, range or distribution")
        return None

    def distribution(self, d: dict[str, Any]) -> dict[str, Any] | None:
        kind = str(d.get("kind", "")).upper()
        p = d.get("parameters", {})
        if kind == "EXACT":
            return {"kind": "exact", "value": p.get("value", 0)}
        if kind == "UNIFORM":
            return {"kind": "uniform", "low": p.get("low", 0), "high": p.get("high", 0)}
        if kind == "GAUSSIAN":
            return {
                "kind": "gaussian",
                "mean": p.get("mean", 0),
                "std": p.get("standardDeviation", 0),
            }
        if kind == "EXPONENTIAL":
            return {
                "kind": "exponential",
                "mean": 1.0 / p["rate"] if p.get("rate") else p.get("mean", 1),
            }
        self.miss("distribution", f"kind {d.get('kind')!r} is not supported")
        return None

    def set_attribute(self, s: dict[str, Any]) -> dict[str, Any] | None:
        name = s.get("attribute")
        if "distribution" in s:
            d = self.distribution(s["distribution"])
            return {"type": "set_attribute", "attribute": name, "distribution": d} if d else None
        if "value" in s and s["value"] is not None:
            return {"type": "set_attribute", "attribute": name, "value": s["value"]}
        self.miss(
            "SetAttribute", "no value or distribution (an expression, or clearing the attribute)"
        )
        return None

    def coded(self, s: dict[str, Any], native: str) -> dict[str, Any] | None:
        if not s.get("codes"):
            self.miss(str(s.get("type")), "no codes")
            return None
        out: dict[str, Any] = {"type": native, "codes": s["codes"]}
        if s.get("assign_to_attribute"):
            out["assign_to_attribute"] = s["assign_to_attribute"]
        return out

    def end(self, s: dict[str, Any], kind: str) -> dict[str, Any] | None:
        native = "condition_end" if kind == "ConditionEnd" else "medication_end"
        onset = "condition_onset" if kind == "ConditionEnd" else "medication_order"
        if onset in s:
            return {"type": native, onset: s[onset]}
        if "referenced_by_attribute" in s:
            return {"type": native, "referenced_by_attribute": s["referenced_by_attribute"]}
        if s.get("codes"):
            return {"type": native, "codes": s["codes"]}
        self.miss(kind, "does not say which onset it ends")
        return None

    def observation(self, s: dict[str, Any]) -> dict[str, Any] | None:
        if not s.get("codes"):
            self.miss("Observation", "no codes")
            return None
        out: dict[str, Any] = {"type": "observation", "codes": s["codes"], "unit": s.get("unit")}
        if "exact" in s:
            out["exact"] = s["exact"].get("quantity", 0)
        elif "range" in s:
            out["range"] = {"low": s["range"].get("low", 0), "high": s["range"].get("high", 0)}
        elif "attribute" in s:
            out["attribute"] = s["attribute"]
        else:
            self.miss("Observation", "value comes from a vital sign or is not given")
            return None
        return out

    # -- transitions and conditions ------------------------------------------------------------

    def probability(self, d: Any) -> Any:
        if isinstance(d, dict):
            return {"attribute": d["attribute"], "default": d.get("default", 0)}
        return d

    def distributed(self, items: list[dict[str, Any]], where: str) -> list[dict[str, Any]]:
        branches = [
            {"p": self.probability(i.get("distribution", 0)), "to": i["transition"]} for i in items
        ]
        ps = [b["p"] for b in branches]
        if all(isinstance(p, (int, float)) for p in ps):
            total = float(sum(ps))
            if total <= 0:
                self.miss("distributed_transition", "probabilities sum to zero")
                return [{"p": 1.0, "to": branches[0]["to"]}] if branches else []
            if abs(total - 1.0) > 1e-6:
                self.warnings.append(f"{where}: probabilities sum to {total:.6g}; normalized")
                for b in branches:
                    b["p"] = b["p"] / total
                branches[-1]["p"] += 1.0 - sum(b["p"] for b in branches)
        return branches

    def transition(self, s: dict[str, Any]) -> dict[str, Any] | None:
        where = self.state
        if "direct_transition" in s:
            return {"direct": s["direct_transition"]}
        if "distributed_transition" in s:
            return {"distributed": self.distributed(s["distributed_transition"], where)}
        if "conditional_transition" in s:
            entries = []
            for e in s["conditional_transition"]:
                entry: dict[str, Any] = {"to": e["transition"]}
                if "condition" in e:
                    entry = {"if": self.condition(e["condition"]), **entry}
                entries.append(entry)
            return {"conditional": entries}
        if "complex_transition" in s:
            entries = []
            for e in s["complex_transition"]:
                entry = {}
                if "condition" in e:
                    entry["if"] = self.condition(e["condition"])
                if "transition" in e:
                    entry["to"] = e["transition"]
                else:
                    entry["distributed"] = self.distributed(e.get("distributions", []), where)
                entries.append(entry)
            return {"complex": entries}
        if "lookup_table_transition" in s:
            self.miss("lookup_table_transition", "not supported; the module stops at this state")
        elif s.get("type") not in ("Terminal", "Death"):
            self.warnings.append(f"{where}: no transition; the module stops here")
        return None

    def condition(self, c: dict[str, Any]) -> dict[str, Any]:
        kind = c.get("condition_type")
        if kind in ("True", "False"):
            return {"type": kind.lower()}
        if kind == "Gender":
            return {
                "type": "attribute",
                "attribute": "gender",
                "op": "==",
                "value": c.get("gender"),
            }
        if kind == "Age":
            value = c["quantity"] if "quantity" in c else c.get("value", 0)
            op = self.op(c)
            if op is None:
                return {"type": "false"}
            return {"type": "age", "op": op, "value": value, "unit": c.get("unit", "years")}
        if kind == "Date":
            if "value" in c:
                when: Any = to_us(0) + int(c["value"]) * 1000
            elif "year" in c:
                when = to_us(f"{int(c['year']):04d}-01-01")
            else:
                self.miss("Date", "needs value or year")
                return {"type": "false"}
            op = self.op(c)
            return {"type": "date", "op": op, "value": when} if op else {"type": "false"}
        if kind == "Attribute":
            op = self.op(c)
            if op is None:
                return {"type": "false"}
            if op in ("is_nil", "is_not_nil"):
                return {"type": "attribute", "attribute": c["attribute"], "op": op}
            return {
                "type": "attribute",
                "attribute": c["attribute"],
                "op": op,
                "value": c.get("value"),
            }
        if kind in ("Active Condition", "Active Medication"):
            return {"type": kind.lower().replace(" ", "_"), "codes": c.get("codes", [])}
        if kind in ("And", "Or"):
            return {
                "type": kind.lower(),
                "conditions": [self.condition(x) for x in c.get("conditions", [])],
            }
        if kind == "Not":
            return {
                "type": "not",
                "condition": self.condition(c.get("condition", {"condition_type": "False"})),
            }
        if kind in ("At Least", "At Most"):
            key = "minimum" if kind == "At Least" else "maximum"
            return {
                "type": kind.lower().replace(" ", "_"),
                key: c.get(key, 0),
                "conditions": [self.condition(x) for x in c.get("conditions", [])],
            }
        self.miss(f"condition {kind}", "condition type is not supported; counted as false")
        return {"type": "false"}

    def op(self, c: dict[str, Any]) -> str | None:
        op = _OPS.get(str(c.get("operator")))
        if op is None:
            self.miss(
                f"condition {c.get('condition_type')}",
                f"operator {c.get('operator')!r} is not supported; counted as false",
            )
        return op


__all__ = ["ImportResult", "Unsupported", "UnsupportedGmfError", "import_gmf", "is_gmf"]
