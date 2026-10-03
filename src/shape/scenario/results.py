"""Which checks a ``shape-result`` document says fired.

The canary check and the game-day runner both read the ``--json`` result (W1-14) of a user's own
``shape diff``, ``shape check`` or ``shape verify`` and ask the same question as the catalog: does
``drift:KIND``, ``rule:RULE`` or ``gate:NAME`` appear? The result of a ``diff`` lists its changes,
a ``check`` its violations, and a ``verify`` prints its gate table (a gate that failed is a
detection). A gate named in the catalog by its scenario name counts when ``shape verify`` reports
its own name for the same gate (:data:`GATE_ALIASES`).

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import re
from typing import Any

from shape.errors import ShapeError

RESULT_FORMAT = "shape-result"
SUPPORTED_COMMANDS = ("diff", "check", "verify")
#: The gates ``shape verify`` runs (``docs/VERIFY.md``).
VERIFY_GATES = (
    "schema_conformance",
    "null_constraint",
    "unique_constraint",
    "referential_integrity",
    "range_constraint",
    "temporal_consistency",
    "file_format",
    "schema_drift",
    "distribution",
    "memorization",
    "utility",
)
#: The scenario gate that ``shape verify`` calls by another name.
GATE_ALIASES = {"null_check": "null_constraint", "uniqueness": "unique_constraint"}
_GATE_LINE = re.compile(r"^(\w+)\s+(PASS|FAIL)\s+\d+\s+\d+", re.MULTILINE)


class ResultError(ShapeError, ValueError):
    """A result document is not a ``shape-result`` of ``diff``, ``check`` or ``verify``."""


def fired(doc: Any, what: str = "the result") -> set[str]:
    """The checks that fired according to the ``shape-result`` document ``doc``. Raises
    :class:`ResultError` for anything that is not one, or is the result of another command."""
    if not isinstance(doc, dict) or doc.get("format") != RESULT_FORMAT:
        raise ResultError(f"{what} is not a {RESULT_FORMAT} document (run the command with --json)")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ResultError(f"{what} needs an integer 'version' of 1 or more, got {version!r}")
    command = doc.get("command")
    if command not in SUPPORTED_COMMANDS:
        raise ResultError(
            f"{what} is the result of {command!r}; only shape {', '.join(SUPPORTED_COMMANDS)} "
            f"results say which checks fired"
        )
    out: set[str] = set()
    if command == "diff":
        changes = doc.get("changes")
        if not isinstance(changes, list):
            raise ResultError(f"{what} has no list of 'changes'")
        out |= {f"drift:{c['kind']}" for c in changes if isinstance(c, dict) and "kind" in c}
    elif command == "check":
        violations = doc.get("violations")
        if not isinstance(violations, list):
            raise ResultError(f"{what} has no list of 'violations'")
        for v in violations:
            if not isinstance(v, dict) or "rule" not in v:
                continue
            rule = str(v["rule"])
            if (
                v.get("column") is None and ":" in rule
            ):  # a table rule of a dataset: "t:row_count.min"
                rule = rule.partition(":")[2]
            out.add(f"rule:{rule}")
    else:
        text = doc.get("output")
        if not isinstance(text, str):
            raise ResultError(f"{what} has no 'output' text to read the gates from")
        out |= {f"gate:{m.group(1)}" for m in _GATE_LINE.finditer(text) if m.group(2) == "FAIL"}
    return out


def satisfied(expected: str, found: set[str]) -> bool:
    """Whether the expectation ``expected`` (a check, or alternatives joined by ``|``) is among
    the checks ``found``. A scenario gate also counts when its ``shape verify`` name failed."""
    for alternative in expected.split("|"):
        if alternative in found:
            return True
        kind, _, name = alternative.partition(":")
        if kind == "gate" and f"gate:{GATE_ALIASES.get(name, name)}" in found:
            return True
    return False
