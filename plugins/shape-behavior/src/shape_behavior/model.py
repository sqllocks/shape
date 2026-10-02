"""Module documents: validation, canonical form and loading.

Reference: ``docs/plugins/behavior.md``, section 2.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.resources
import json
from pathlib import Path
from typing import Any

from shape_behavior import conditions, dist
from shape_behavior.extension import get_handler
from shape_behavior.timeutil import parse_duration

FORMAT = "shape-behavior/1"
BUILTIN_TYPES = (
    "initial",
    "terminal",
    "simple",
    "delay",
    "guard",
    "set_attribute",
    "counter",
    "encounter",
    "encounter_end",
    "condition_onset",
    "condition_end",
    "medication_order",
    "medication_end",
    "procedure",
    "observation",
    "death",
    "event",
)
NO_TRANSITION = ("terminal", "death")
EVENT_KINDS = {  # state type -> the event kind it emits (the rest emit nothing)
    "encounter": "encounter",
    "encounter_end": "encounter_end",
    "condition_onset": "condition_onset",
    "condition_end": "condition_end",
    "medication_order": "medication_order",
    "medication_end": "medication_end",
    "procedure": "procedure",
    "observation": "observation",
    "death": "death",
}
TRANSITION_KINDS = ("direct", "distributed", "conditional", "complex", "stop")


class ModuleError(ValueError):
    """A module document is invalid; ``problems`` lists every problem found."""

    def __init__(self, problems: list[str], name: str = "") -> None:
        self.problems = problems
        head = f"module {name!r} is invalid" if name else "module is invalid"
        super().__init__(head + ":\n  " + "\n  ".join(problems))


def examples() -> list[str]:
    """The names of the built-in example modules."""
    root = importlib.resources.files("shape_behavior") / "examples"
    return sorted(p.name[:-5] for p in root.iterdir() if p.name.endswith(".json"))


class Module:
    """A validated state-machine module. Build with ``Module(doc)`` or :func:`load_module`."""

    def __init__(self, doc: dict[str, Any]) -> None:
        if not isinstance(doc, dict):
            raise ModuleError(["a module document must be a JSON object"])
        self.doc: dict[str, Any] = copy.deepcopy(doc)
        problems = _validate(self.doc)
        if problems:
            raise ModuleError(problems, str(doc.get("name", "")))
        self.name: str = str(self.doc["name"])
        self.import_report: Any = None

    @property
    def states(self) -> dict[str, dict[str, Any]]:
        return self.doc["states"]  # type: ignore[no-any-return]

    @property
    def initial(self) -> str:
        explicit = self.doc.get("initial")
        if explicit:
            return str(explicit)
        return next(n for n, s in self.states.items() if s["type"] == "initial")

    def to_dict(self) -> dict[str, Any]:
        """A deep copy of the document."""
        return copy.deepcopy(self.doc)

    def digest(self) -> str:
        """A content hash of the canonical document (guards resuming with changed modules)."""
        text = json.dumps(self.doc, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(text.encode()).hexdigest()

    def attribute_uses(self) -> dict[str, set[str]]:
        """Each entity attribute the module touches, with the kinds (``num``/``cat``) implied."""
        uses: dict[str, set[str]] = {}

        def note(name: str, kind: str | None) -> None:
            uses.setdefault(name, set())
            if kind:
                uses[name].add(kind)

        for name, spec in self.doc.get("attributes", {}).items():
            note(name, attribute_kind(spec))
        for state in self.states.values():
            _state_uses(state, note)
        return uses

    def codes(self) -> tuple[list[str], list[str]]:
        """The codes tracked in the active condition set and the active medication set."""
        conds: list[str] = []
        meds: list[str] = []

        def add(into: list[str], c: Any) -> None:
            code = conditions.code_of(c)
            if code and code not in into:
                into.append(code)

        for state in self.states.values():
            kind = state["type"]
            for cond in _conditions_of(state):
                for node in conditions.walk(cond):
                    if node["type"] == "active_condition":
                        for c in node["codes"]:
                            add(conds, c)
                    elif node["type"] == "active_medication":
                        for c in node["codes"]:
                            add(meds, c)
            target = {
                "condition_onset": conds,
                "condition_end": conds,
                "medication_order": meds,
                "medication_end": meds,
            }.get(kind)
            if target is not None:
                starts = kind in ("condition_onset", "medication_order")
                for c in state.get("codes", [])[:1] if starts else state.get("codes", []):
                    add(target, c)
        return conds, meds


def attribute_kind(spec: Any) -> str | None:
    """``cat`` or ``num`` for a population/module attribute spec (``None`` if unclear)."""
    if not isinstance(spec, dict):
        return None
    kind = spec.get("kind")
    if kind == "categorical":
        return "cat"
    if kind in ("exact", "constant") and isinstance(spec.get("value"), str):
        return "cat"
    return "num" if kind in dist.KINDS else None


def _conditions_of(state: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if isinstance(state.get("condition"), dict):
        out.append(state["condition"])
    t = state.get("transition")
    if isinstance(t, dict):
        for entry in t.get("conditional", []) + t.get("complex", []):
            if isinstance(entry, dict) and isinstance(entry.get("if"), dict):
                out.append(entry["if"])
    return out


def _state_uses(state: dict[str, Any], note: Any) -> None:
    kind = state["type"]
    if kind == "set_attribute":
        v = state.get("value")
        note(state["attribute"], "cat" if isinstance(v, str) else "num")
    elif kind == "counter":
        note(state["attribute"], "num")
    elif kind == "observation" and "attribute" in state:
        note(state["attribute"], "num")
    elif kind == "event":
        if "value_from" in state:
            note(state["value_from"], "num")
        if "text_from" in state:
            note(state["text_from"], "cat")
    elif kind in ("condition_onset", "medication_order") and "assign_to_attribute" in state:
        note(state["assign_to_attribute"], "cat")
    elif kind in ("condition_end", "medication_end") and "referenced_by_attribute" in state:
        note(state["referenced_by_attribute"], "cat")
    else:
        handler = get_handler(kind)
        uses = getattr(handler, "uses", None)
        if uses is not None:
            for name, k in uses(state).items():
                note(name, k)
    for cond in _conditions_of(state):
        for node in conditions.walk(cond):
            if node["type"] == "attribute":
                v = node.get("value")
                note(
                    node["attribute"],
                    "cat" if isinstance(v, str) else ("num" if v is not None else None),
                )
    t = state.get("transition")
    if isinstance(t, dict):
        branches = list(t.get("distributed", []))
        for entry in t.get("complex", []):
            if isinstance(entry, dict):
                branches += entry.get("distributed", [])
        for b in branches:
            if isinstance(b, dict) and isinstance(b.get("p"), dict):
                note(str(b["p"].get("attribute")), "num")


# -- validation -------------------------------------------------------------------------------


def _validate(doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if doc.get("format", FORMAT) != FORMAT:
        problems.append(f"format must be {FORMAT!r}, got {doc.get('format')!r}")
    if not isinstance(doc.get("name"), str) or not doc["name"]:
        problems.append("'name' is required (a non-empty string)")
    states = doc.get("states")
    if not isinstance(states, dict) or not states:
        return [*problems, "'states' is required (an object of named states)"]
    for sname, state in states.items():
        if not isinstance(state, dict) or not isinstance(state.get("type"), str):
            problems.append(f"state {sname!r}: needs a 'type'")
    if problems:
        return problems
    initials = [n for n, s in states.items() if s["type"] == "initial"]
    explicit = doc.get("initial")
    if explicit is not None:
        if explicit not in states:
            problems.append(f"'initial' names an unknown state {explicit!r}")
    elif len(initials) != 1:
        problems.append("needs exactly one 'initial' state (or an 'initial' key naming one)")
    for aname, spec in doc.get("attributes", {}).items():
        problems += _check_attribute_spec(spec, f"attributes.{aname}")
    for sname, state in states.items():
        problems += _check_state(sname, state, states)
    return problems


def _check_attribute_spec(spec: Any, where: str) -> list[str]:
    if isinstance(spec, dict) and spec.get("kind") == "categorical":
        values = spec.get("values")
        if not isinstance(values, dict) or not values:
            return [f"{where}: categorical needs 'values' (value to weight)"]
        if any(
            isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0 for w in values.values()
        ):
            return [f"{where}: weights must be non-negative numbers"]
        if sum(values.values()) <= 0:
            return [f"{where}: weights sum to zero"]
        return []
    if (
        isinstance(spec, dict)
        and spec.get("kind") in ("exact", "constant")
        and isinstance(spec.get("value"), str)
    ):
        return []
    return dist.check(spec, where)


def _check_state(sname: str, state: dict[str, Any], states: dict[str, Any]) -> list[str]:
    where = f"state {sname!r}"
    kind = state["type"]
    out: list[str] = []
    handler = get_handler(kind)
    if kind not in BUILTIN_TYPES and handler is None:
        return [f"{where}: unknown state type {kind!r}"]
    out += _check_fields(where, kind, state, states)
    if handler is not None and kind not in BUILTIN_TYPES:
        out += [f"{where}: {p}" for p in handler.validate(state)]
    t = state.get("transition")
    if kind in NO_TRANSITION:
        if t is not None:
            out.append(f"{where}: a {kind} state has no transition")
    elif t is None:
        out.append(f"{where}: needs a 'transition' (use {{\"stop\": true}} to end the module here)")
    else:
        out += _check_transition(where, t, states)
    return out


def _codes_ok(state: dict[str, Any], where: str, *, required: bool) -> list[str]:
    codes = state.get("codes")
    if codes is None:
        return [f"{where}: needs 'codes'"] if required else []
    if not isinstance(codes, list) or not all(conditions.code_of(c) for c in codes):
        return [f"{where}: 'codes' must be a list of {{system, code, display}} objects"]
    if required and not codes:
        return [f"{where}: needs at least one code"]
    return []


def _check_fields(
    where: str, kind: str, state: dict[str, Any], states: dict[str, Any]
) -> list[str]:
    out: list[str] = []
    if kind == "delay":
        out += dist.check(state.get("delay"), f"{where}.delay", duration=True)
    elif kind == "guard":
        out += conditions.check(state.get("condition"), f"{where}.condition")
        if "poll" in state:
            try:
                parse_duration(state["poll"])
            except ValueError as exc:
                out.append(f"{where}.poll: {exc}")
    elif kind == "set_attribute":
        if not isinstance(state.get("attribute"), str):
            out.append(f"{where}: needs 'attribute'")
        if ("value" in state) == ("distribution" in state):
            out.append(f"{where}: needs exactly one of 'value' or 'distribution'")
        elif "distribution" in state:
            out += dist.check(state["distribution"], f"{where}.distribution")
    elif kind == "counter":
        if not isinstance(state.get("attribute"), str):
            out.append(f"{where}: needs 'attribute'")
        if state.get("action", "increment") not in ("increment", "decrement"):
            out.append(f"{where}: action must be 'increment' or 'decrement'")
    elif kind in ("encounter", "encounter_end"):
        out += _codes_ok(state, where, required=False)
    elif kind in ("condition_onset", "medication_order"):
        out += _codes_ok(state, where, required=True)
    elif kind in ("condition_end", "medication_end"):
        onset_type = "condition_onset" if kind == "condition_end" else "medication_order"
        refs = [k for k in (onset_type, "codes", "referenced_by_attribute") if k in state]
        if len(refs) != 1:
            out.append(
                f"{where}: needs exactly one of '{onset_type}', 'codes', 'referenced_by_attribute'"
            )
        elif refs[0] == onset_type:
            target = states.get(state[onset_type])
            if not isinstance(target, dict) or target.get("type") != onset_type:
                out.append(f"{where}: '{onset_type}' must name a {onset_type} state")
        elif refs[0] == "codes":
            out += _codes_ok(state, where, required=True)
    elif kind == "procedure":
        out += _codes_ok(state, where, required=True)
    elif kind == "observation":
        out += _codes_ok(state, where, required=True)
        given = [k for k in ("exact", "range", "attribute") if k in state]
        if len(given) != 1:
            out.append(f"{where}: needs exactly one of 'exact', 'range', 'attribute'")
        elif given[0] == "exact" and not isinstance(state["exact"], (int, float)):
            out.append(f"{where}: 'exact' must be a number")
        elif given[0] == "range":
            r = state["range"]
            if not (
                isinstance(r, dict)
                and isinstance(r.get("low"), (int, float))
                and isinstance(r.get("high"), (int, float))
                and r["low"] <= r["high"]
            ):
                out.append(f"{where}: 'range' needs numeric low <= high")
    elif kind == "event":
        if not isinstance(state.get("event"), str) or not state["event"]:
            out.append(f"{where}: needs 'event' (the event kind)")
        out += _codes_ok(state, where, required=False)
        if "payload" in state and not isinstance(state["payload"], dict):
            out.append(f"{where}: 'payload' must be an object")
    return out


def _targets(where: str, t: Any, states: dict[str, Any], out: list[str]) -> None:
    if t not in states:
        out.append(f"{where}: transitions to an unknown state {t!r}")


def _check_branches(where: str, branches: Any, states: dict[str, Any]) -> list[str]:
    if not isinstance(branches, list) or not branches:
        return [f"{where}: needs a non-empty list"]
    out: list[str] = []
    total = 0.0
    constant = True
    for i, b in enumerate(branches):
        if not isinstance(b, dict) or "p" not in b or "to" not in b:
            out.append(f"{where}[{i}]: needs 'p' and 'to'")
            continue
        _targets(f"{where}[{i}]", b["to"], states, out)
        p = b["p"]
        if isinstance(p, dict):
            constant = False
            if not isinstance(p.get("attribute"), str) or not isinstance(
                p.get("default"), (int, float)
            ):
                out.append(
                    f"{where}[{i}]: a probability object needs 'attribute' and a numeric 'default'"
                )
        elif isinstance(p, bool) or not isinstance(p, (int, float)) or p < 0:
            out.append(f"{where}[{i}]: 'p' must be a non-negative number")
        else:
            total += p
    if constant and not out and abs(total - 1.0) > 1e-6:
        out.append(f"{where}: probabilities sum to {total:.6g}, not 1")
    return out


def _check_transition(where: str, t: Any, states: dict[str, Any]) -> list[str]:
    if not isinstance(t, dict) or len([k for k in t if k in TRANSITION_KINDS]) != 1 or len(t) != 1:
        return [f"{where}.transition: needs exactly one of {', '.join(TRANSITION_KINDS)}"]
    ((kind, body),) = t.items()
    path = f"{where}.transition.{kind}"
    out: list[str] = []
    if kind == "stop":
        return [] if body is True else [f"{path}: use true"]
    if kind == "direct":
        _targets(path, body, states, out)
    elif kind == "distributed":
        out += _check_branches(path, body, states)
    elif kind in ("conditional", "complex"):
        if not isinstance(body, list) or not body:
            return [f"{path}: needs a non-empty list"]
        for i, entry in enumerate(body):
            here = f"{path}[{i}]"
            if not isinstance(entry, dict):
                out.append(f"{here}: must be an object")
                continue
            if "if" in entry:
                out += conditions.check(entry["if"], f"{here}.if")
            elif i != len(body) - 1:
                out.append(f"{here}: only the last entry may omit 'if'")
            if "to" in entry:
                _targets(here, entry["to"], states, out)
            elif kind == "complex" and "distributed" in entry:
                out += _check_branches(f"{here}.distributed", entry["distributed"], states)
            else:
                out.append(
                    f"{here}: needs 'to'" + (" or 'distributed'" if kind == "complex" else "")
                )
    return out


def _registered_module(name: str) -> Module | None:
    """The module of a behavior registered under ``shape.behaviors`` (the engine loads by name)."""
    from shape.plugins.host import PluginLoadError, default_host

    host = default_host()
    if host.record("shape.behaviors", name) is None:
        return None
    try:
        found = host.get("shape.behaviors", name)
    except PluginLoadError as exc:
        raise ModuleError([str(exc)], name) from exc
    module = getattr(found, "module", None)
    if not isinstance(module, Module):
        raise ModuleError(
            ["the behavior is registered but exposes no `module`, so the engine cannot run it"],
            name,
        )
    return module


def load_module(source: Any, *, strict: bool = False) -> Module:
    """A :class:`Module` from a path, a ``dict``, a JSON string or a built-in example name.

    A document in Generic Module Framework format is imported (``strict`` as for
    :func:`shape_behavior.gmf.import_gmf`; the import report is on ``module.import_report``).
    """
    if isinstance(source, Module):
        return source
    doc: Any = source
    if isinstance(source, (str, Path)):
        text = str(source)
        path = Path(text)
        if text.lstrip().startswith("{"):
            doc = json.loads(text)
        elif path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
        elif text in examples():
            doc = json.loads(
                (
                    importlib.resources.files("shape_behavior") / "examples" / f"{text}.json"
                ).read_text(encoding="utf-8")
            )
        else:
            registered = _registered_module(text)
            if registered is None:
                raise FileNotFoundError(
                    f"no module file, built-in example or registered behavior named {text!r}; "
                    f"examples: {', '.join(examples())}"
                )
            return registered
    from shape_behavior.gmf import import_gmf, is_gmf

    if is_gmf(doc):
        result = import_gmf(doc, strict=strict)
        result.module.import_report = result
        return result.module
    return Module(doc)
