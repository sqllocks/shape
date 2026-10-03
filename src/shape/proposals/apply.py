"""Applying decisions: what a later run does with what a person decided."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .model import DecisionError, DecisionFile


def _fk_name(child: str, column: str) -> str:
    return f"fk_{child}_{column}"


def apply_decisions(profile: Any, decisions: DecisionFile) -> Any:
    """A copy of ``profile`` (a ``Profile``, its dict, or a ``.shape`` path) with the
    relationship decisions applied.

    An *accepted* relationship is added to the profile's relationships and marked on its child
    column, so generation from the profile keeps it. A *rejected* one is removed from both, so a
    foreign key the profiler guessed and a person refused is not generated. Pending and deferred
    proposals change nothing. Applying twice gives the same result as applying once.

    PII and semantic decisions are kept in the decision file (``DecisionFile.accepted("pii")``)
    for the commands that need them; they do not change the profile.
    """
    from shape.profile.reference.profile import Profile

    from ._data import dataset_of

    if isinstance(profile, (str, Path)):
        import shape

        profile = shape.load(str(profile))
    ds = dataset_of(profile)
    data = profile.to_dict() if hasattr(profile, "to_dict") else copy.deepcopy(profile)
    data = copy.deepcopy(data)
    relationship_decisions = [
        e for e in decisions.list(kind="relationship") if e.status in ("accepted", "rejected")
    ]
    if "tables" not in data:
        if any(e.status == "accepted" for e in relationship_decisions):
            raise DecisionError(
                "an accepted relationship needs a multi-table profile; this one has a single table"
            )
        return profile
    rels: list[dict[str, Any]] = [dict(r) for r in data.get("relationships") or []]

    for entry in relationship_decisions:
        claim = entry.proposal.claim
        child, parent = claim["child"], claim["parent"]
        ccols, pcols = list(claim["child_columns"]), list(claim["parent_columns"])
        col = ccols[0]
        if entry.status == "accepted":
            for t, c in ((child, ccols), (parent, pcols)):
                if t not in ds.tables:
                    raise DecisionError(
                        f"the accepted relationship {entry.proposal.id!r} needs table {t!r}, "
                        "which the profile does not have"
                    )
                missing = [x for x in c if x not in ds.tables[t].columns]
                if missing:
                    raise DecisionError(
                        f"the accepted relationship {entry.proposal.id!r} needs column "
                        f"{missing[0]!r} in table {t!r}, which the profile does not have"
                    )
        rels = [
            r
            for r in rels
            if not (r["child"] == child and r["child_columns"] == ccols and r["parent"] == parent)
        ]
        table = data["tables"].get(child)
        if entry.status == "accepted":
            rels.append(
                {
                    "name": _fk_name(child, col),
                    "parent": parent,
                    "child": child,
                    "parent_columns": pcols,
                    "child_columns": ccols,
                    "type": claim.get("type", "one_to_many"),
                }
            )
            table["detected_fks"][col] = parent
            table["columns"][col]["is_foreign_key"] = True
            table["columns"][col]["fk_ref_table"] = parent
        elif table is not None and table["detected_fks"].get(col) == parent:
            del table["detected_fks"][col]
            if col in table["columns"]:
                table["columns"][col]["is_foreign_key"] = False
                table["columns"][col]["fk_ref_table"] = None
    data["relationships"] = sorted(rels, key=lambda r: r["name"])
    name = getattr(profile, "name", None)
    provenance = getattr(profile, "provenance", None)
    return Profile(data, name=name, provenance=provenance)
