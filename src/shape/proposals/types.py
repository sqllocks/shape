"""Column type proposals (W2-07): where the declared, inferred and identifier evidence disagree.

One proposal per column, ``type:TABLE.COLUMN``, whose claim is the type the evidence points to:
the narrower type the values of a declared ``string`` or ``float`` column all parse as, the
candidate type of a column that is mostly one type, or ``string`` for an integer column that may
hold identifiers. The evidence is the finding of :func:`shape.types_report`, its confidence and
the parse shares. Accepting one is applied by ``apply_decisions`` (generation and plans) and by
``shape profile --decisions`` (read as ``--types``). Nothing is accepted automatically.
"""

from __future__ import annotations

import re
from typing import Any

from shape.profile.types_report import types_report

from ._data import DataSource
from .model import Proposal

# how sure a proposal is, by what it rests on: every value of a declared column parses as the
# narrower type (a measurement), a suspect rests on the name and the width (a hint)
_DECLARED = 0.9
_LOW_CONFIDENCE = 0.9
_RULE = 0.8  # the identifier rule's own judgment (a fixed width and an identifier name)
_NAME_SUSPECT = 0.6
_WIDTH_SUSPECT = 0.55

# a finding's order of weight when a column has several: the most informative proposal wins
_PRIORITY = {
    "identifier_suspect": 0,
    "declared_differs": 1,
    "low_confidence": 2,
}


def _identifier_confidence(reason: str) -> float:
    if reason.startswith("a fixed width"):
        return _RULE
    if reason.startswith("its name"):
        return _NAME_SUSPECT
    return _WIDTH_SUSPECT


def _width(reason: str) -> int | None:
    m = re.search(r"(\d+) digits", reason)
    return int(m.group(1)) if m else None


def propose_types(
    profile: Any, data: DataSource | None = None, *, min_confidence: float = 0.5
) -> list[Proposal]:
    """Type proposals for the findings of ``types_report(profile)`` and for every identifier
    suspect, the most confident first. ``data`` is accepted so the proposal functions share a
    signature; the evidence is in the profile. A profile written before type inference was
    recorded has none, and proposes nothing."""
    if isinstance(min_confidence, bool) or not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be from 0 to 1")
    prof = _profile_of(profile)
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for f in types_report(prof):
        if f["kind"] not in _PRIORITY:
            continue
        key = (f["table"], f["column"])
        if key not in best or _PRIORITY[f["kind"]] < _PRIORITY[best[key]["kind"]]:
            best[key] = f
    out: list[Proposal] = []
    for (table, column), f in best.items():
        kind = f["kind"]
        shares = f.get("parse_shares") or {}
        if kind == "identifier_suspect":
            proposed = "string"
            conf = _identifier_confidence(f["identifier"])
        elif kind == "declared_differs":
            proposed = f["inferred"]
            conf = _DECLARED * float(shares.get(proposed) or f["confidence"] or 0.0)
        else:
            proposed = f["inferred"]
            conf = _LOW_CONFIDENCE * float(f["confidence"] or 0.0)
        if proposed is None or conf < min_confidence:
            continue
        evidence: dict[str, Any] = {
            "finding": kind,
            "source": f.get("source"),
            "profile_type": f.get("profile_type"),
            "declared": f["declared"],
            "inferred": f["inferred"],
            "confidence": f["confidence"],
            "parse_shares": shares,
            "identifier": f.get("identifier"),
        }
        width = _width(f["identifier"]) if f.get("identifier") else None
        if width is not None:
            evidence["width"] = width
        out.append(
            Proposal(
                f"type:{table}.{column}",
                "type",
                f"{table}.{column}",
                {"type": proposed, "table": table, "column": column},
                conf,
                evidence,
            )  # fmt: skip
        )
    return sorted(out, key=lambda p: (-p.confidence, p.id))


def _profile_of(profile: Any) -> Any:
    """A ``Profile`` from a ``Profile``, its ``to_dict()`` or a ``.shape`` path."""
    from pathlib import Path

    from shape.profile.reference.profile import Profile

    if isinstance(profile, Profile):
        return profile
    if isinstance(profile, (str, Path)):
        import shape

        return shape.load(str(profile))
    if isinstance(profile, dict):
        return Profile(profile)
    raise ValueError("expected a Profile, its dictionary or the path of a .shape profile")


def claim_target(proposal: Any) -> tuple[str, str, str]:
    """``(table, column, type)`` of a ``type`` proposal. The claim names the table and column; a
    hand-written one may leave them out, and then the subject ``TABLE.COLUMN`` is split at its
    last dot."""
    claim = proposal.claim
    target = claim.get("type")
    table, column = claim.get("table"), claim.get("column")
    if not (isinstance(table, str) and isinstance(column, str)):
        table, _, column = proposal.subject.rpartition(".")
    if not (isinstance(target, str) and table and column):
        from .model import DecisionError

        raise DecisionError(
            f"the type proposal {proposal.id!r} needs a claim {{'type': ...}} and a subject "
            "TABLE.COLUMN"
        )
    return table, column, target
