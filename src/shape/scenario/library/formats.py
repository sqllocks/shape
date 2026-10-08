"""Reading the library's JSON files: the scenario, its answer key, the index and the suites.

Every file declares ``format`` and an integer ``version``. A file of another format, without a
version, or from a newer Shape (a higher version than this one reads) is refused with an error that
names the file; unknown keys are refused too, so a typo does not pass as a smaller scenario.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

SCENARIO_FORMAT = "shape-scenario"
EXPECT_FORMAT = "shape-scenario-expect"
LIBRARY_FORMAT = "shape-scenario-library"
SUITE_FORMAT = "shape-suite"
VERSION = 1  # every format here is at version 1

ROOT = Path(__file__).resolve().parent

_SCENARIO_KEYS = {"format", "version", "id", "domain", "scale", "seed", "gates", "defects", "drift"}
_EXPECT_KEYS = {"format", "version", "scenario", "gates_fail", "defects", "drift"}
_LIBRARY_KEYS = {"format", "version", "scenarios"}
_ENTRY_KEYS = {"id", "domain", "description"}
_SUITE_KEYS = {"format", "version", "name", "description", "scenarios"}


class LibraryError(ShapeError, ValueError):
    """A library file is malformed, from a newer Shape, or names something that does not exist."""


class UnknownScenarioError(LibraryError):
    """A scenario name that is not in the library."""


def read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LibraryError(f"{what} not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise LibraryError(f"{what} {path} cannot be read: {exc}") from None
    except json.JSONDecodeError as exc:
        raise LibraryError(f"{what} {path} is not valid JSON: {exc}") from None


def check_header(doc: Any, fmt: str, what: str, allowed: set[str]) -> dict[str, Any]:
    """``doc`` as a mapping of format ``fmt``, version at most :data:`VERSION`, with only the
    ``allowed`` keys."""
    if not isinstance(doc, dict):
        raise LibraryError(f"{what} must be a JSON object")
    if doc.get("format") != fmt:
        raise LibraryError(f"{what} is not a {fmt} file (format is {doc.get('format')!r})")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise LibraryError(f"{what} needs an integer 'version' of 1 or more, got {version!r}")
    if version > VERSION:
        raise LibraryError(
            f"{what} is {fmt} version {version}, written by a newer Shape; this one reads "
            f"up to version {VERSION}: upgrade Shape to use it"
        )
    unknown = sorted(set(doc) - allowed)
    if unknown:
        raise LibraryError(f"{what} has unknown keys: {', '.join(unknown)}")
    return doc


def _strings(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise LibraryError(f"{what} must be a list of names")
    return list(value)


def list_text(value: Any) -> bool:
    """Whether ``value`` is a non-empty list of non-empty text."""
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(v, str) and v.strip() for v in value)
    )


def parse_scenario(doc: Any, what: str) -> dict[str, Any]:
    out = check_header(doc, SCENARIO_FORMAT, what, _SCENARIO_KEYS)
    for key in ("id", "domain"):
        if not isinstance(out.get(key), str) or not out[key]:
            raise LibraryError(f"{what} needs a {key!r}")
    if not isinstance(out.get("scale", "small"), str):
        raise LibraryError(f"{what}: 'scale' must be a name")
    if isinstance(out.get("seed", 42), bool) or not isinstance(out.get("seed", 42), int):
        raise LibraryError(f"{what}: 'seed' must be an integer")
    out["gates"] = _strings(out.get("gates", []), f"{what}: 'gates'")
    defects = out.get("defects", [])
    if not isinstance(defects, list):
        raise LibraryError(f"{what}: 'defects' must be a list")
    drift = out.get("drift")
    if drift is not None:
        if not isinstance(drift, dict) or set(drift) - {"plan", "compare"}:
            raise LibraryError(f"{what}: 'drift' holds 'plan' and 'compare'")
        compare = drift.get("compare")
        if not isinstance(drift.get("plan"), dict) or not isinstance(compare, list) or not compare:
            raise LibraryError(f"{what}: 'drift' needs a 'plan' and a list of 'compare' windows")
        for pair in compare:
            if not (
                isinstance(pair, list)
                and len(pair) == 2
                and all(isinstance(d, int) and not isinstance(d, bool) for d in pair)
            ):
                raise LibraryError(f"{what}: a 'compare' window is a pair of day numbers")
        if out["gates"] or defects:
            raise LibraryError(f"{what}: a drift scenario has no gates or defects")
    if not drift and not out["gates"]:
        raise LibraryError(f"{what} checks nothing: give it gates or a drift plan")
    return out


def parse_expect(doc: Any, what: str) -> dict[str, Any]:
    out = check_header(doc, EXPECT_FORMAT, what, _EXPECT_KEYS)
    if not isinstance(out.get("scenario"), str):
        raise LibraryError(f"{what} needs the 'scenario' it belongs to")
    if "gates_fail" not in out:
        raise LibraryError(f"{what} needs 'gates_fail' (an empty list when none must fail)")
    out["gates_fail"] = _strings(out["gates_fail"], f"{what}: 'gates_fail'")
    defects = out.get("defects", {})
    if not isinstance(defects, dict) or not all(
        isinstance(v, dict) and set(v) == {"min"} and isinstance(v["min"], int)
        for v in defects.values()
    ):
        raise LibraryError(f"{what}: 'defects' maps a defect kind to {{\"min\": rows}}")
    out["defects"] = defects
    windows = out.get("drift", [])
    if not isinstance(windows, list):
        raise LibraryError(f"{what}: 'drift' must be a list of windows")
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"between", "changes"}:
            raise LibraryError(f"{what}: a drift window has 'between' and 'changes'")
        for change in window["changes"]:
            if not (
                isinstance(change, dict)
                and set(change) == {"column", "kinds"}
                and isinstance(change["column"], str)
            ):
                raise LibraryError(f"{what}: a change has a 'column' and its 'kinds'")
            _strings(change["kinds"], f"{what}: 'kinds'")
    out["drift"] = windows
    return out


def parse_index(doc: Any, what: str) -> list[dict[str, str]]:
    out = check_header(doc, LIBRARY_FORMAT, what, _LIBRARY_KEYS)
    entries = out.get("scenarios")
    if not isinstance(entries, list) or not entries:
        raise LibraryError(f"{what} needs a non-empty 'scenarios' list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise LibraryError(f"{what}: an entry has {', '.join(sorted(_ENTRY_KEYS))}")
        if not all(isinstance(entry[k], str) and entry[k] for k in _ENTRY_KEYS):
            raise LibraryError(f"{what}: entry {entry.get('id')!r} has an empty field")
        if entry["id"] in seen:
            raise LibraryError(f"{what}: scenario {entry['id']!r} is listed twice")
        seen.add(entry["id"])
    return [dict(e) for e in entries]


def parse_suite(doc: Any, what: str) -> dict[str, Any]:
    out = check_header(doc, SUITE_FORMAT, what, _SUITE_KEYS)
    scenarios = out.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise LibraryError(f"{what} needs a non-empty 'scenarios' list")
    out["scenarios"] = _strings(scenarios, f"{what}: 'scenarios'")
    for key in ("name", "description"):
        if not isinstance(out.get(key, ""), str):
            raise LibraryError(f"{what}: {key!r} must be text")
    return out
