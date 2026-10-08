"""The failure mode catalog: which ways data goes wrong, how each looks and which check catches it.

``failure_modes.json`` (``{"format": "shape-failure-catalog", "version": 1, "modes": [...]}``) is
the one source: ``docs/FAILURE_MODES.md`` is generated from it (``scripts/gen_failure_modes.py``),
``shape failure-modes`` prints it, the ``failure-modes`` suite checks it against what Shape really
reports, and the detective packs and canaries refer to its entries by ``id``.

An entry is ``id`` (a slug), ``title``, ``severity``, ``symptoms``, ``common_causes``,
``detected_by`` (checks written ``drift:KIND``, ``rule:RULE`` or ``gate:NAME`` with the real names
in Shape), ``reproduce`` (``library:SCENARIO``) and, only when ``detected_by`` is empty, ``gap``:
why no check of Shape sees the problem. Every check an entry names must fire for its scenario;
:func:`verify_mode` says which do not.

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shape.scenario.library import formats
from shape.scenario.library.detect import Detection, check_exists, scenario_detections
from shape.scenario.library.formats import (
    LibraryError,
    check_header,
    list_text,
    read_json,
)

if TYPE_CHECKING:
    from shape.scenario.library.suite import SuiteResult

CATALOG_FORMAT = "shape-failure-catalog"
SUITE_NAME = "failure-modes"
SEVERITIES = ("low", "medium", "high", "critical")
MINIMUM_MODES = 20
_ID = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_MODE_KEYS = {
    "id",
    "title",
    "severity",
    "symptoms",
    "common_causes",
    "detected_by",
    "reproduce",
    "gap",
}
_REQUIRED = _MODE_KEYS - {"gap"}


class UnknownModeError(LibraryError):
    """A failure mode id that is not in the catalog."""


def catalog_path(root: Path | None = None) -> Path:
    return (root or formats.ROOT) / "failure_modes.json"


def parse_catalog(doc: Any, what: str) -> list[dict[str, Any]]:
    """The modes of a ``shape-failure-catalog`` document. Raises :class:`LibraryError` for a
    document of another format or a newer version, an entry with a missing or unknown key, a
    duplicate or malformed id, a severity that is not one of :data:`SEVERITIES`, a check that is
    not written ``KIND:NAME`` or that Shape does not have, and a missing ``gap`` for an entry that
    nothing detects."""
    out = check_header(doc, CATALOG_FORMAT, what, {"format", "version", "modes"})
    modes = out.get("modes")
    if not isinstance(modes, list) or not modes:
        raise LibraryError(f"{what} needs a non-empty 'modes' list")
    seen: set[str] = set()
    for mode in modes:
        if not isinstance(mode, dict):
            raise LibraryError(f"{what}: a mode must be an object")
        label = f"{what}: mode {mode.get('id')!r}"
        missing = sorted(_REQUIRED - set(mode))
        if missing:
            raise LibraryError(f"{label} lacks {', '.join(missing)}")
        unknown = sorted(set(mode) - _MODE_KEYS)
        if unknown:
            raise LibraryError(f"{label} has unknown keys: {', '.join(unknown)}")
        mid = mode["id"]
        if not isinstance(mid, str) or not _ID.match(mid):
            raise LibraryError(f"{label}: 'id' must be a slug such as late-arriving-records")
        if mid in seen:
            raise LibraryError(f"{what}: mode {mid!r} is listed twice")
        seen.add(mid)
        if not isinstance(mode["title"], str) or not mode["title"].strip():
            raise LibraryError(f"{label}: 'title' must be text")
        if mode["severity"] not in SEVERITIES:
            raise LibraryError(f"{label}: 'severity' must be one of {', '.join(SEVERITIES)}")
        for key in ("symptoms", "common_causes"):
            if not list_text(mode[key]):
                raise LibraryError(f"{label}: {key!r} must be a non-empty list of text")
        detected = mode["detected_by"]
        if not isinstance(detected, list):
            raise LibraryError(f"{label}: 'detected_by' must be a list of checks")
        for check in detected:
            check_exists(check, f"{label}: a 'detected_by' entry")
        if len(set(detected)) != len(detected):
            raise LibraryError(f"{label}: 'detected_by' names a check twice")
        reproduce = mode["reproduce"]
        if not isinstance(reproduce, str) or not reproduce.startswith("library:"):
            raise LibraryError(f"{label}: 'reproduce' must be library:NAME")
        gap = mode.get("gap")
        if detected and gap is not None:
            raise LibraryError(f"{label}: 'gap' is only for an entry that nothing detects")
        if not detected and not (isinstance(gap, str) and gap.strip()):
            raise LibraryError(f"{label}: nothing detects it, so it needs a 'gap' that says why")
    return [dict(m) for m in modes]


def scenario_of(mode: dict[str, Any]) -> str:
    """The scenario name of a mode's ``reproduce`` (``library:NAME``)."""
    return str(mode["reproduce"]).partition(":")[2]


def load_catalog(root: Path | None = None) -> list[dict[str, Any]]:
    """The catalog's modes, in file order. Every scenario a mode reproduces with must exist in the
    library at ``root``."""
    from shape.scenario.library.run import list_scenarios

    path = catalog_path(root)
    modes = parse_catalog(read_json(path, "the failure mode catalog"), f"catalog {path.name}")
    if len(modes) < MINIMUM_MODES:
        raise LibraryError(f"the catalog has {len(modes)} modes; it needs at least {MINIMUM_MODES}")
    known = {e["id"] for e in list_scenarios(root)}
    for mode in modes:
        if scenario_of(mode) not in known:
            raise LibraryError(
                f"mode {mode['id']!r} reproduces with the scenario {scenario_of(mode)!r}, "
                f"which is not in the library"
            )
    return modes


def get_mode(mode_id: str, root: Path | None = None) -> dict[str, Any]:
    modes = load_catalog(root)
    for mode in modes:
        if mode["id"] == mode_id:
            return mode
    raise UnknownModeError(
        f"unknown failure mode {mode_id!r}; the catalog has: {', '.join(m['id'] for m in modes)}"
    )


# ---- checking the catalog against Shape --------------------------------------------------------


@dataclass
class ModeResult:
    """What one mode's scenario made Shape report."""

    mode: str
    scenario: str
    fired: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def met(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "scenario": self.scenario,
            "met": self.met,
            "fired": self.fired,
            "missing": self.missing,
        }


def verify_mode(
    mode: dict[str, Any],
    *,
    scale: str | None = None,
    seed: int | None = None,
    root: Path | None = None,
    detections: set[Detection] | None = None,
) -> ModeResult:
    """Run the scenario of ``mode`` and list which of its ``detected_by`` checks did not fire."""
    found = (
        detections
        if detections is not None
        else scenario_detections(scenario_of(mode), scale=scale, seed=seed, root=root)
    )
    fired = sorted({d.check for d in found})
    missing = [c for c in mode["detected_by"] if c not in fired]
    return ModeResult(str(mode["id"]), scenario_of(mode), fired, missing)


def attach_to_suite(result: SuiteResult, root: Path | None = None) -> None:
    """Add to the scenario results of the ``failure-modes`` suite a mismatch for every check a
    mode names that did not fire, so that the suite fails like any other."""
    from shape.scenario.library.run import Mismatch

    by_scenario = {r.outcome.scenario: r for r in result.results}
    for mode in load_catalog(root):
        scenario = by_scenario.get(scenario_of(mode))
        if scenario is None or not mode["detected_by"]:
            continue
        checked = verify_mode(
            mode, scale=scenario.outcome.scale, seed=scenario.outcome.seed, root=root
        )
        for check in checked.missing:
            scenario.mismatches.append(
                Mismatch(
                    f"{check} fires for the failure mode {mode['id']}",
                    f"{check} did not fire (fired: {', '.join(checked.fired) or 'nothing'})",
                )
            )


# ---- the document -------------------------------------------------------------------------------

_HEADER = """\
# Failure modes

<!-- Generated by `python scripts/gen_failure_modes.py` from
`src/shape/scenario/library/failure_modes.json`. Do not edit. -->

The ways data goes wrong, how each one looks, which Shape check catches it and a scenario that
plants it. Print an entry with `shape failure-modes show ID`; plant one with
`shape pack run library:SCENARIO`; check the whole catalog against what Shape reports with
`shape suite run failure-modes`.

Checks are written `drift:KIND` (a change kind of `shape diff`), `rule:RULE` (a rule of
`shape check`) or `gate:NAME` (a validation gate of `shape verify`). Every check listed here fires
for the entry's scenario; the suite fails when one does not. An entry no check catches says so.
"""


def render_markdown(modes: list[dict[str, Any]]) -> str:
    """``docs/FAILURE_MODES.md``: an index table and one section per mode."""
    out = [_HEADER, "| Id | Severity | Detected by |", "|---|---|---|"]
    for m in modes:
        checks = ", ".join(f"`{c}`" for c in m["detected_by"]) or "no Shape check"
        out.append(f"| [`{m['id']}`](#{m['id']}) | {m['severity']} | {checks} |")
    out.append("")
    for m in modes:
        out.append(f"## {m['id']}\n")
        out.append(f"**{m['title']}** (severity: {m['severity']})\n")
        out.append("Symptoms:\n")
        out.extend(f"- {s}" for s in m["symptoms"])
        out.append("\nCommon causes:\n")
        out.extend(f"- {c}" for c in m["common_causes"])
        out.append("")
        if m["detected_by"]:
            out.append("Detected by: " + ", ".join(f"`{c}`" for c in m["detected_by"]) + "\n")
        else:
            out.append(f"Detected by: no Shape check. {m['gap']}\n")
        out.append(f"Reproduce: `shape pack run {m['reproduce']}`\n")
    return "\n".join(out).rstrip("\n") + "\n"
