"""Applying decisions: what a later run does with what a person decided."""

from __future__ import annotations

import copy
import math
from typing import Any

from .model import DecisionError, DecisionFile
from .types import claim_target


def _fk_name(child: str, column: str) -> str:
    return f"fk_{child}_{column}"


def apply_decisions(profile: Any, decisions: DecisionFile) -> Any:
    """A copy of ``profile`` with the relationship decisions applied.

    An *accepted* relationship is added to the profile's relationships and marked on its child
    column, so generation from the profile keeps it. A *rejected* one is removed from both, so a
    foreign key the profiler guessed and a person refused is not generated. Pending and deferred
    proposals change nothing. Applying twice gives the same result as applying once.

    An *accepted* ``type`` decision gives the column the proposed type (``string`` for an
    integer column that holds identifiers, a number for text that is mostly numbers), so
    generation and plans treat it so; one the profile cannot apply (it lacks the column, or the
    values the new type needs) is a :class:`DecisionError` that says how to apply it by
    re-profiling (``shape profile --decisions``).

    PII and semantic decisions are kept in the decision file (``DecisionFile.accepted("pii")``)
    for the commands that need them; they do not change the profile.
    """
    from shape.profile.reference.profile import Profile

    from ._data import dataset_of

    ds = dataset_of(profile)
    data = profile.to_dict() if hasattr(profile, "to_dict") else copy.deepcopy(profile)
    data = copy.deepcopy(data)
    retyped = _apply_types(data, decisions)
    relationship_decisions = [
        e for e in decisions.list(kind="relationship") if e.status in ("accepted", "rejected")
    ]
    if "tables" not in data:
        if any(e.status == "accepted" for e in relationship_decisions):
            raise DecisionError(
                "an accepted relationship needs a multi-table profile; this one has a single table"
            )
        if retyped:
            return Profile(
                data,
                name=getattr(profile, "name", None),
                provenance=getattr(profile, "provenance", None),
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


# ---- type decisions --------------------------------------------------------------------------

_NUMBER_TYPES = ("integer", "float")
_RELABEL = {("integer", "float"), ("datetime", "date"), ("date", "datetime")}
_TEXT_STATS = ("string_length", "pattern", "pattern_rates", "pattern_contains_rates")
_NUMBER_STATS = (
    "mean", "std", "distribution", "distribution_params", "quantiles", "fit_score", "outlier_rate",
)  # fmt: skip


def _apply_types(data: dict[str, Any], decisions: DecisionFile) -> bool:
    """Give every column an accepted ``type`` decision names its proposed type, in ``data`` (a
    copy); True when a column changed."""
    changed = False
    for entry in decisions.list(kind="type"):
        if entry.status != "accepted":
            continue
        table_name, column, target = claim_target(entry.proposal)
        if "tables" in data:
            table = data["tables"].get(table_name)
        else:
            table = data if data.get("name") == table_name else None
        if table is None or column not in table["columns"]:
            raise DecisionError(
                f"the accepted type decision {entry.proposal.id!r} needs column {column!r} in "
                f"table {table_name!r}, which the profile does not have"
            )
        changed |= _retype(table["columns"][column], target, entry.proposal)
    return changed


def _whole(text: str) -> bool:
    return text.lstrip("+-").isdigit()


def _retype(col: dict[str, Any], target: str, proposal: Any) -> bool:
    current = col["dtype"]
    if current == target:
        return False
    if target == "string":
        _to_text(col, proposal.evidence.get("width"))
        return True
    if (current, target) in _RELABEL:  # the values are the same, the name is what changes
        col["dtype"] = target
        return True
    if current == "string" and target in _NUMBER_TYPES:
        _text_to_number(col, target, proposal.id)
        return True
    raise DecisionError(
        f"the accepted type decision {proposal.id!r} cannot be applied to a {current} column "
        f"from the profile alone: re-profile the data with `shape profile --decisions "
        f"DECISIONS.json` (the decision is then read as --types {target})"
    )


def _to_text(col: dict[str, Any], width: Any) -> None:
    """A number column read as text (an identifier: its digits, not its value, are the data)."""
    lo, hi = col.get("min_value"), col.get("max_value")
    if not isinstance(width, int):
        digits = {len(str(int(v[1]))) for v in (lo, hi) if v is not None and _is_int_tag(v)}
        width = digits.pop() if len(digits) == 1 else None
    col["dtype"] = "string"
    for key in (*_NUMBER_STATS, *_TEXT_STATS):
        col[key] = None
    for key in ("min_value", "max_value"):
        v = col.get(key)
        col[key] = (
            ["str", str(int(v[1])).zfill(width or 0)] if v is not None and _is_int_tag(v) else None
        )
    if width:
        w = float(width)
        col["string_length"] = {"min": w, "mean": w, "max": w, "p95": w}
        col["pattern_rates"] = {"digits": 1.0}


def _is_int_tag(v: Any) -> bool:
    if not (isinstance(v, list) and len(v) == 2 and v[0] in ("int", "float")):
        return False
    n = v[1]
    return isinstance(n, (int, float)) and math.isfinite(n) and float(n).is_integer() and n >= 0


def _text_to_number(col: dict[str, Any], target: str, pid: str) -> None:
    """A text column read as numbers, from the frequencies of its values. The profile can do this
    only when it lists every distinct value; the values that are not numbers leave the column."""
    freq = col.get("value_counts_ext") or {}
    order = col.get("value_counts_ext_order") or list(freq)
    if not freq or len(freq) < col.get("cardinality", 0):
        raise DecisionError(
            f"the accepted type decision {pid!r} cannot be applied from the profile alone: it does "
            f"not hold every value of the column; re-profile the data with `shape profile "
            f"--decisions DECISIONS.json` (the decision is then read as --types {target})"
        )
    numbers: dict[str, float] = {}
    for key in order:
        try:
            v = float(key)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            numbers[key] = v
    if not numbers:
        raise DecisionError(
            f"the accepted type decision {pid!r}: no value of the column is a number"
        )
    if target == "integer" and not all(v.is_integer() for v in numbers.values()):
        raise DecisionError(
            f"the accepted type decision {pid!r}: some values are not whole numbers"
        )
    total = sum(freq[k] for k in numbers)
    weights = {k: freq[k] / total for k in numbers}
    mean = sum(weights[k] * v for k, v in numbers.items())
    var = sum(weights[k] * (v - mean) ** 2 for k, v in numbers.items())
    cast = int if target == "integer" else float
    lo, hi = min(numbers.values()), max(numbers.values())
    col["dtype"] = target
    for key in (*_NUMBER_STATS, *_TEXT_STATS):
        col[key] = None
    col["mean"], col["std"] = mean, math.sqrt(var)
    col["min_value"] = [target[:3] if target == "integer" else "float", cast(lo)]
    col["max_value"] = [target[:3] if target == "integer" else "float", cast(hi)]
    col["cardinality"] = len(numbers)
    col["value_counts_ext"] = weights
    col["value_counts_ext_order"] = list(weights)
    col["enum_values"] = dict(weights) if col.get("is_enum") else None
    col["is_unique"] = False


def decided_types(decisions: Any) -> tuple[tuple[str, str, str], ...]:
    """``(table, column, type)`` for every accepted ``type`` decision of ``decisions`` (a
    :class:`DecisionFile` or the path of one): what ``shape profile --decisions`` reads as
    ``--types``."""
    from pathlib import Path

    from shape.io.identifiers import resolve_type

    if isinstance(decisions, (str, Path)):
        decisions = DecisionFile.read(decisions)
    if not isinstance(decisions, DecisionFile):
        raise DecisionError("decisions is a DecisionFile or the path of a decision file")
    out = []
    for entry in decisions.accepted("type"):
        table, column, target = claim_target(entry.proposal)
        resolve_type(target)  # a type Shape does not know is an input error
        out.append((table, column, target))
    return tuple(out)
