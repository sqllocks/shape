"""Stable Python modules compatibility baseline (W7-07).

    python scripts/stable_api_compat.py --check    # exit 1 if a stable module breaks the baseline
    python scripts/stable_api_compat.py --write    # refresh the baseline after an *additive* change

``tests/api/stable_api_baseline.json`` records, for every name a stable module exports (its
``__all__``): the kind, and for callables, classes and their members the full signature with
annotations as strings; for dataclasses the fields and frozen-ness; for exceptions the base
classes. ``compare`` applies the promise in ``docs/API_STABILITY.md`` ("Stable Python modules"):
what the baseline has must be unchanged, additions that cannot break an existing caller pass.
``--write`` is for additive changes only; a breaking change needs a new major version.

The signature comparison is the one of ``scripts/plugin_api_compat.py``.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import importlib.util
import inspect
import json
import sys
import types
import typing
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import plugin_api_compat as _sig  # noqa: E402  (shared signature comparison)

BASELINE = ROOT / "tests" / "api" / "stable_api_baseline.json"
FORMAT = "shape-stable-api-baseline"
VERSION = 1

# The stable modules (docs/API_STABILITY.md, "Stable Python modules").
MODULES: tuple[str, ...] = ("shape.generation.spec_edit", "shape.generation.spec_schema")


class BaselineError(Exception):
    """The baseline file cannot be used (wrong format, or written by a newer version)."""


def _qualname(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _callable(fn: Any) -> dict[str, Any]:
    entry = _sig._method(fn)
    return {"kind": "function", **entry}


def _members(cls: type) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in vars(cls):
        if name.startswith("_") and name != "__init__":
            continue
        raw = inspect.getattr_static(cls, name)
        if isinstance(raw, property):
            sig = inspect.signature(raw.fget) if raw.fget else None
            ret = (
                ""
                if sig is None or sig.return_annotation is sig.empty
                else str(sig.return_annotation)
            )
            out[name] = {"kind": "property", "returns": ret}
        elif isinstance(raw, (classmethod, staticmethod)):
            kind = "classmethod" if isinstance(raw, classmethod) else "staticmethod"
            out[name] = {"kind": kind, **_sig._method(getattr(cls, name))}
        elif callable(raw):
            out[name] = {"kind": "method", **_sig._method(raw)}
    return out


def _entry(obj: Any) -> dict[str, Any]:
    if isinstance(obj, type):
        bases = [_qualname(b) for b in obj.__bases__ if b is not object]
        members = _members(obj)
        if dataclasses.is_dataclass(obj):
            members.pop("__init__", None)
            return {"kind": "dataclass", **_sig._dataclass(obj), "members": members}
        if issubclass(obj, BaseException):
            return {"kind": "exception", "bases": bases, "members": members}
        return {"kind": "class", "bases": bases, "members": members}
    if isinstance(obj, types.GenericAlias) or typing.get_origin(obj) is not None:
        return {"kind": "alias", "value": str(obj)}
    if callable(obj):
        return _callable(obj)
    return {"kind": "constant", "value": repr(obj)}


def snapshot(modules: dict[str, ModuleType] | None = None) -> dict[str, Any]:
    """The shape of the live stable modules, in the form of the committed baseline."""
    if modules is None:
        modules = {name: importlib.import_module(name) for name in MODULES}
    out: dict[str, Any] = {}
    for name, mod in modules.items():
        names: dict[str, Any] = {}
        missing: list[str] = []
        for export in getattr(mod, "__all__", []):
            if hasattr(mod, export):
                names[export] = _entry(getattr(mod, export))
            else:
                missing.append(export)
        out[name] = {"names": names, "missing": missing}
    return {"format": FORMAT, "version": VERSION, "modules": out}


def _compare_members(where: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name, o in old.items():
        n = new.get(name)
        if n is None:
            problems.append(f"{where}.{name}: member was removed")
        elif n["kind"] != o["kind"]:
            problems.append(f"{where}.{name}: kind changed {o['kind']} -> {n['kind']}")
        elif o["kind"] == "property":
            if o["returns"] != n["returns"]:
                problems.append(
                    f"{where}.{name}: type changed {o['returns']!r} -> {n['returns']!r}"
                )
        else:
            problems += _sig._compare_method(f"{where}.{name}", o, n)
    return problems


def compare(baseline: dict[str, Any], live: dict[str, Any]) -> list[str]:
    """Problems that break the promise for callers written against ``baseline`` (empty if none)."""
    problems: list[str] = []
    for mod, old_mod in baseline["modules"].items():
        new_mod = live["modules"].get(mod)
        if new_mod is None:
            problems.append(f"{mod}: module was removed")
            continue
        for gone in new_mod.get("missing", []):
            problems.append(f"{mod}.{gone}: exported in __all__ but missing from the module")
        for name, old in old_mod["names"].items():
            where = f"{mod}.{name}"
            new = new_mod["names"].get(name)
            if new is None:
                problems.append(f"{where}: name was removed")
                continue
            if new["kind"] != old["kind"]:
                problems.append(f"{where}: kind changed {old['kind']} -> {new['kind']}")
                continue
            kind = old["kind"]
            if kind == "function":
                problems += _sig._compare_method(where, old, new)
            elif kind in ("alias", "constant"):
                if old["value"] != new["value"]:
                    problems.append(f"{where}: changed {old['value']!r} -> {new['value']!r}")
            else:
                if kind in ("exception", "class") and old["bases"] != new["bases"]:
                    problems.append(
                        f"{where}: base classes changed {old['bases']} -> {new['bases']}"
                    )
                if kind == "dataclass":
                    problems += [f"{where}: {p}" for p in _sig._compare_type(name, old, new)]
                problems += _compare_members(where, old["members"], new["members"])
    return problems


def load_baseline(path: Path = BASELINE) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BaselineError(f"{path} is not a {FORMAT} file")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise BaselineError(f"{path}: 'version' must be an integer")
    if version > VERSION:
        raise BaselineError(
            f"{path} is version {version}, newer than this script reads ({VERSION}); "
            f"update Shape instead of rewriting the file"
        )
    if version < 1 or not isinstance(data.get("modules"), dict):
        raise BaselineError(f"{path} is not a valid {FORMAT} version {VERSION} file")
    return data


def load_source(directory: Path) -> dict[str, ModuleType]:
    """The stable modules from ``<directory>/<short name>.py`` (a synthetic copy, for tests)."""
    modules: dict[str, ModuleType] = {}
    for name in MODULES:
        short = name.rsplit(".", 1)[1]
        alias = f"_stable_api_copy_{short}"
        spec = importlib.util.spec_from_file_location(alias, directory / f"{short}.py")
        if spec is None or spec.loader is None:
            raise BaselineError(f"cannot load {directory / (short + '.py')}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[alias] = mod
        spec.loader.exec_module(mod)
        modules[name] = mod
    return modules


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    ap.add_argument(
        "--baseline", type=Path, default=BASELINE, help="baseline file (default: committed)"
    )
    ap.add_argument("--source", type=Path, help="directory with copies of the modules to check")
    ns = ap.parse_args(argv)
    live = snapshot(load_source(ns.source) if ns.source else None)
    try:
        if ns.write:
            if ns.baseline.exists():
                problems = compare(load_baseline(ns.baseline), live)
                if problems:
                    print(
                        "refusing to write: these changes break a stable module:", file=sys.stderr
                    )
                    for p in problems:
                        print(" -", p, file=sys.stderr)
                    return 1
            ns.baseline.parent.mkdir(parents=True, exist_ok=True)
            ns.baseline.write_text(
                json.dumps(live, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"wrote {ns.baseline}")
            return 0
        problems = compare(load_baseline(ns.baseline), live)
    except BaselineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for p in problems:
        print("BREAKING:", p)
    print(f"stable modules: {len(problems)} breaking change(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
