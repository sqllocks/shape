"""Golden records: one surviving row per cluster, chosen column by column.

A :class:`Survivorship` rule says which member's value survives for a column. Missing values never
survive while a present one exists. Ties go to the earliest row, so the result is deterministic.
The lineage says which row each value came from.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

RULES = (
    "first",
    "last",
    "most_common",
    "longest",
    "shortest",
    "min",
    "max",
    "most_recent",
    "priority",
)


@dataclass(frozen=True)
class Survivorship:
    """A survivorship rule.

    ``first`` and ``last``: the first or last present value in row order; ``most_common``;
    ``longest`` and ``shortest`` (by text length); ``min`` and ``max``; ``most_recent``: the value
    of the row with the largest ``by`` column; ``priority``: the value of the row whose ``by``
    column ranks first in ``order`` (a value not in ``order`` ranks last).
    """

    rule: str = "first"
    by: str | None = None
    order: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValueError(
                f"unknown survivorship rule {self.rule!r}; choose one of {', '.join(RULES)}"
            )
        if self.rule in ("most_recent", "priority") and not self.by:
            raise ValueError(f"survivorship {self.rule} needs by=COLUMN")
        if self.rule == "priority" and not self.order:
            raise ValueError("survivorship priority needs order=(first, second, ...)")
        object.__setattr__(self, "order", tuple(self.order))

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"rule": self.rule}
        if self.by:
            out["by"] = self.by
        if self.order:
            out["order"] = list(self.order)
        return out


@dataclass(frozen=True)
class Golden:
    """The golden table (original columns, ``_cluster_id``, ``_cluster_size``) and its lineage."""

    table: pa.Table
    lineage: list[dict[str, Any]]


def _pick(
    rule: Survivorship, rows: list[int], values: list[Any], by: list[Any] | None
) -> int | None:
    present = [r for r in rows if values[r] is not None]
    if not present:
        return None
    r = rule.rule
    if r == "first":
        return present[0]
    if r == "last":
        return present[-1]
    if r == "most_common":
        counts = Counter(values[x] for x in present)
        best = max(counts.values())
        return next(x for x in present if counts[values[x]] == best)
    if r in ("longest", "shortest"):
        lengths = {x: len(str(values[x])) for x in present}
        target = max(lengths.values()) if r == "longest" else min(lengths.values())
        return next(x for x in present if lengths[x] == target)
    if r in ("min", "max"):
        target = (max if r == "max" else min)(values[x] for x in present)
        return next(x for x in present if values[x] == target)
    assert by is not None
    if r == "most_recent":
        stamped = [x for x in present if by[x] is not None]
        if not stamped:
            return present[0]
        best = max(by[x] for x in stamped)
        return next(x for x in stamped if by[x] == best)
    rank = {v: k for k, v in enumerate(rule.order)}
    return min(present, key=lambda x: (rank.get(by[x], len(rank)), x))


def build_golden(
    table: pa.Table,
    labels: npt.NDArray[np.int64],
    rules: Mapping[str, Survivorship] | None = None,
) -> Golden:
    """One row per cluster of ``labels`` (clusters in label order, members in row order)."""
    labels = np.asarray(labels)
    if len(labels) != table.num_rows:
        raise ValueError("build_golden needs one label per row")
    rules = dict(rules or {})
    names = table.column_names
    for col, rule in rules.items():
        if col not in names:
            raise ValueError(f"survivorship: no column {col!r}")
        if rule.by is not None and rule.by not in names:
            raise ValueError(f"survivorship for {col!r}: no column {rule.by!r}")
    members: dict[int, list[int]] = {}
    for row, lab in enumerate(labels.tolist()):
        members.setdefault(int(lab), []).append(row)
    cluster_ids = sorted(members)
    columns = {name: table.column(name).to_pylist() for name in names}
    chosen: dict[str, list[int | None]] = {name: [] for name in names}
    lineage: list[dict[str, Any]] = []
    for cid in cluster_ids:
        rows = members[cid]
        survivors: dict[str, int | None] = {}
        for name in names:
            rule = rules.get(name, Survivorship("first"))
            by = columns[rule.by] if rule.by else None
            pick = _pick(rule, rows, columns[name], by)
            chosen[name].append(pick)
            survivors[name] = pick
        lineage.append({"cluster": cid, "members": rows, "survivors": survivors})
    arrays = []
    for field in table.schema:
        picks = chosen[field.name]
        vals = [None if p is None else columns[field.name][p] for p in picks]
        arrays.append(pa.array(vals, type=field.type))
    arrays.append(pa.array(cluster_ids, type=pa.int64()))
    arrays.append(pa.array([len(members[c]) for c in cluster_ids], type=pa.int64()))
    out = pa.Table.from_arrays(arrays, names=[*names, "_cluster_id", "_cluster_size"])
    return Golden(out, lineage)
