"""What a ``shape-result`` document says, as findings and a verdict (W6-01).

The pull request comment, the status badge and the webhook notification all read the same thing
out of a result document: the findings (table, column, kind, severity), the planned changes that
are listed apart from them, and one verdict. Only these names are ever read, so a value of the
data (a baseline, a current value, an allowed value) can never reach any of the three.

The verdict follows the command's exit code, which is what a CI step is gated on:

``fail``   the command exited with anything but 0 (a check failed, or the command could not run);
``drift``  it exited 0 and still reported findings (drift, with no enforced gate failing);
``pass``   it exited 0 and reported none.

A finding that a planned change covers (``planned`` set, see ``docs/PLANNED_CHANGES.md``) is not
a finding: it is listed on its own and does not make a result drift.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shape.cli import machine

SEVERITIES = ("high", "medium", "low")
VERDICTS = ("pass", "drift", "fail")
#: the rank of a verdict when several results are combined: the worst one wins
_RANK = {"pass": 0, "drift": 1, "fail": 2}
#: findings listed in a notification at most (the counts hold the full number)
MAX_LISTED = 200


class ResultError(ValueError):
    """A file is not a ``shape-result`` document that can be read."""


@dataclass(frozen=True, slots=True)
class Finding:
    table: str
    column: str
    kind: str
    severity: str
    planned: str | None = None

    def sort_key(self) -> tuple[int, str, str, str, str]:
        rank = SEVERITIES.index(self.severity) if self.severity in SEVERITIES else len(SEVERITIES)
        return (rank, self.table, self.column, self.kind, self.severity)

    def as_dict(self) -> dict[str, str]:
        return {
            "table": self.table,
            "column": self.column,
            "kind": self.kind,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class Result:
    """One source's outcome, as read from one result document."""

    source: str
    command: str
    exit_code: int
    findings: tuple[Finding, ...]
    planned: tuple[Finding, ...]

    @property
    def verdict(self) -> str:
        return verdict_of(self.exit_code, len(self.findings))


def verdict_of(exit_code: int, findings: int) -> str:
    if exit_code != 0:
        return "fail"
    return "drift" if findings else "pass"


def worst(verdicts: Sequence[str]) -> str:
    return max(verdicts, key=_RANK.__getitem__, default="pass")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _entries(payload: Mapping[str, Any]) -> list[Finding]:
    """The findings a result document lists: the changes of ``diff``, the violations of
    ``check`` and the failed gates of ``verify``."""
    found: list[Finding] = []
    for key, kind_keys, default in (
        ("changes", ("kind",), "medium"),
        ("violations", ("kind", "rule"), "high"),
    ):
        items = payload.get(key)
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            kind = next((_text(item[k]) for k in kind_keys if item.get(k)), "change")
            planned = item.get("planned")
            found.append(
                Finding(
                    _text(item.get("table")),
                    _text(item.get("column")),
                    kind,
                    _text(item.get("severity")) or default,
                    _text(planned) if planned else None,
                )
            )
    gates = payload.get("gates")
    for gate in gates if isinstance(gates, list) else []:
        if isinstance(gate, Mapping) and gate.get("passed") is False:
            found.append(Finding("", "", _text(gate.get("gate")) or "gate", "high"))
    return found


def result_from(doc: Mapping[str, Any], fallback_source: str) -> Result:
    """The :class:`Result` of a ``shape-result`` document (already checked by :func:`load`)."""
    block = doc.get("project")
    named = block.get("source") if isinstance(block, Mapping) else None
    entries = _entries(doc)
    return Result(
        source=_text(named) or fallback_source,
        command=_text(doc.get("command")),
        exit_code=int(doc.get("exit_code", 0)),
        findings=tuple(sorted((f for f in entries if f.planned is None), key=Finding.sort_key)),
        planned=tuple(sorted((f for f in entries if f.planned is not None), key=Finding.sort_key)),
    )


def load(path: str | os.PathLike[str]) -> Result:
    """Read one result document. Raises :class:`ResultError` (exit 2 for the commands) for a file
    that is missing, is not JSON, is not a ``shape-result`` or is of a newer version."""
    import json

    where = Path(path)
    try:
        text = where.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ResultError(f"{where}: no such file") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ResultError(f"{where}: cannot be read as text ({type(exc).__name__})") from None
    try:
        doc = json.loads(text)
    except ValueError:
        raise ResultError(f"{where}: not valid JSON") from None
    try:
        kind = machine.check_document(doc)
    except ValueError as exc:
        raise ResultError(f"{where}: {exc}") from None
    if kind != machine.RESULT_FORMAT:
        raise ResultError(f"{where}: a {kind} document, not a shape-result")
    exit_code = doc.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ResultError(f"{where}: exit_code must be an integer")
    return result_from(doc, where.stem)


def load_all(paths: Sequence[str | os.PathLike[str]]) -> list[Result]:
    return [load(p) for p in paths]
