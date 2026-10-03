"""Plugin API v1 compatibility baseline (W1-05).

    python scripts/plugin_api_compat.py --check    # exit 1 if v1 breaks the committed baseline
    python scripts/plugin_api_compat.py --write    # refresh the baseline after an *additive* change

``tests/plugins/api_v1_baseline.json`` records, per entry-point group, the Protocol and the data
types a plugin of that group touches, exactly as plugin API 1.0 shipped them. ``compare`` applies
the stability promise (``docs/plugins/stability.md``) to a live ``shape.plugins.api.v1``: what the
baseline has must be unchanged, and anything added must not break an existing plugin.
``--write`` is for additive changes only; a breaking change needs a new API major version.
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
BASELINE = ROOT / "tests" / "plugins" / "api_v1_baseline.json"

# The data types a plugin of each group receives or returns (besides Arrow and plain types).
GROUP_TYPES: dict[str, list[str]] = {
    "shape.sources": [],
    "shape.sinks": [],
    "shape.detectors": ["Detection"],
    "shape.fitters": ["FitResult"],
    "shape.strategies": ["GenerationContext"],
    "shape.distributions": ["GenerationContext"],
    "shape.calendars": [],
    "shape.domains": ["DomainDefinition"],
    "shape.chaos": ["ChaosReport"],
    "shape.emitters": [],
    "shape.stream_sources": ["StreamOffset"],
    "shape.transforms": [],
    "shape.commands": [],
    "shape.reports": [],
    "shape.behaviors": [],
}


def _param(p: inspect.Parameter) -> dict[str, Any]:
    return {
        "name": p.name,
        "kind": p.kind.name,
        "annotation": "" if p.annotation is p.empty else str(p.annotation),
        "default": None if p.default is p.empty else repr(p.default),
    }


def _method(fn: Any) -> dict[str, Any]:
    sig = inspect.signature(fn)
    return {
        "params": [_param(p) for n, p in sig.parameters.items() if n != "self"],
        "returns": "" if sig.return_annotation is sig.empty else str(sig.return_annotation),
    }


def _protocol(proto: type) -> dict[str, Any]:
    attrs = {n: str(t) for n, t in getattr(proto, "__annotations__", {}).items()}
    methods = {
        n: _method(o) for n, o in vars(proto).items() if not n.startswith("_") and callable(o)
    }
    return {"attributes": attrs, "methods": methods}


def _dataclass(cls: type) -> dict[str, Any]:
    params = cls.__dataclass_params__  # type: ignore[attr-defined]
    return {
        "frozen": bool(params.frozen),
        "fields": [
            {
                "name": f.name,
                "type": str(f.type),
                "has_default": f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING,
            }
            for f in dataclasses.fields(cls)
        ],
    }


def snapshot(v1: Any | None = None) -> dict[str, Any]:
    """The shape of the live plugin API, in the same form as the committed baseline."""
    if v1 is None:
        from shape.plugins.api import v1 as live

        v1 = live
    return {
        "api": v1.SHAPE_API,
        "groups": {
            group: {
                "protocol": pname,
                "protocol_shape": _protocol(v1.PROTOCOLS[pname]),
                "types": {t: _dataclass(getattr(v1, t)) for t in GROUP_TYPES.get(group, [])},
            }
            for group, pname in v1.GROUPS.items()
        },
    }


def _compare_method(where: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    problems = []
    if old["returns"] != new["returns"]:
        problems.append(f"{where}: return type changed {old['returns']!r} -> {new['returns']!r}")
    op, np_ = old["params"], new["params"]
    for i, o in enumerate(op):
        if i >= len(np_):
            problems.append(f"{where}: parameter {o['name']!r} was removed")
        elif np_[i] != o:
            problems.append(f"{where}: parameter {o['name']!r} changed {o} -> {np_[i]}")
    for extra in np_[len(op) :]:
        if extra["default"] is None and extra["kind"] not in ("VAR_KEYWORD", "VAR_POSITIONAL"):
            problems.append(
                f"{where}: new parameter {extra['name']!r} has no default, so existing "
                f"plugins that implement or call this method break"
            )
    return problems


def _compare_type(where: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    problems = []
    if old["frozen"] != new["frozen"]:
        problems.append(f"{where}: frozen changed {old['frozen']} -> {new['frozen']}")
    of, nf = old["fields"], new["fields"]
    for i, f in enumerate(of):
        if i >= len(nf):
            problems.append(f"{where}: field {f['name']!r} was removed")
        elif nf[i] != f:
            problems.append(f"{where}: field {f['name']!r} changed {f} -> {nf[i]}")
    for extra in nf[len(of) :]:
        if not extra["has_default"]:
            problems.append(f"{where}: new field {extra['name']!r} has no default")
    return problems


def compare(baseline: dict[str, Any], live: dict[str, Any], group: str | None = None) -> list[str]:
    """Problems that break API v1 for plugins written against ``baseline`` (empty if none).

    ``group`` limits the comparison to one entry-point group."""
    problems: list[str] = []
    if group is None and baseline["api"].split(".")[0] != live["api"].split(".")[0]:
        problems.append(f"API major changed {baseline['api']} -> {live['api']}")
    for g, old in baseline["groups"].items():
        if group is not None and g != group:
            continue
        new = live["groups"].get(g)
        if new is None:
            problems.append(f"{g}: group was removed")
            continue
        if new["protocol"] != old["protocol"]:
            problems.append(f"{g}: Protocol renamed {old['protocol']} -> {new['protocol']}")
        po, pn = old["protocol_shape"], new["protocol_shape"]
        name = old["protocol"]
        for attr, ann in po["attributes"].items():
            if attr not in pn["attributes"]:
                problems.append(f"{name}.{attr}: attribute was removed")
            elif pn["attributes"][attr] != ann:
                problems.append(
                    f"{name}.{attr}: type changed {ann!r} -> {pn['attributes'][attr]!r}"
                )
        for attr in pn["attributes"].keys() - po["attributes"].keys():
            problems.append(
                f"{name}.{attr}: new required attribute breaks existing plugins "
                f"(optional capabilities need their own Protocol)"
            )
        for meth, shape in po["methods"].items():
            if meth not in pn["methods"]:
                problems.append(f"{name}.{meth}: method was removed")
            else:
                problems += _compare_method(f"{name}.{meth}", shape, pn["methods"][meth])
        for meth in pn["methods"].keys() - po["methods"].keys():
            problems.append(
                f"{name}.{meth}: new required method breaks existing plugins "
                f"(optional capabilities need their own Protocol)"
            )
        for t, tshape in old["types"].items():
            if t not in new["types"]:
                problems.append(f"{t}: data type was removed from {g}")
            else:
                problems += _compare_type(t, tshape, new["types"][t])
    if group is None:
        for g in live["groups"].keys() - baseline["groups"].keys():
            if not live["groups"][g]["protocol"]:
                problems.append(f"{g}: group has no Protocol")
    return problems


def load_baseline() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(BASELINE.read_text(encoding="utf-8"))
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    ns = ap.parse_args(argv)
    live = snapshot()
    if ns.write:
        old = load_baseline() if BASELINE.exists() else None
        if old is not None:
            problems = compare(old, live)
            if problems:
                print("refusing to write: these changes break plugin API v1:", file=sys.stderr)
                for p in problems:
                    print(" -", p, file=sys.stderr)
                return 1
        BASELINE.write_text(json.dumps(live, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE.relative_to(ROOT)}")
        return 0
    problems = compare(load_baseline(), live)
    for p in problems:
        print("BREAKING:", p)
    print(f"plugin API {live['api']}: {len(problems)} breaking change(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
