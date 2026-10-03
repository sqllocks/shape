"""Candidate foreign keys, each a proposal with the evidence that supports it.

For every pair of a child column and a unique parent column in another (or the same) table the
evidence is:

* **name**: the child column is named after the parent's key, or after the parent table (singular
  or plural) with an ``_id`` style suffix;
* **containment** (needs the data): the share of the child's non-null rows whose value exists in
  the parent column;
* **type**: the two columns hold the same kind of value;
* **range**: the child's minimum and maximum lie within the parent's;
* **cardinality**: the parent column is unique and the child has no more distinct values than it.

The confidence is the weighted sum 0.40 name + 0.35 containment + 0.10 range + 0.15 cardinality.
Without data there is no containment: the other three are rescaled and the result is capped at
0.85. The profiler's own detected foreign keys are included (marked ``profiler_detected``), so
nothing it found is lost and nothing is computed twice.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from shape.profile.dependencies import candidate_key

from ._data import DataSource, dataset_of, load_data
from .model import Proposal

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.profile.reference.model import ColumnProfile, DatasetProfile, TableProfile

W_NAME, W_CONTAINMENT, W_RANGE, W_CARDINALITY = 0.40, 0.35, 0.10, 0.15
NO_DATA_CAP = 0.85
MIN_CONTAINMENT = 0.5  # below this share of matching rows the column is not a key reference
UNNAMED_MIN_DISTINCT = 20  # a column with no name evidence needs this many distinct values
_GENERIC_KEYS = {"id", "key", "pk", "code", "_id"}
_SUFFIXES = ("_id", "id", "_key", "_pk", "_fk", "_ref", "_code")


def _stems(table: str) -> set[str]:
    t = table.lower()
    out = {t}
    if t.endswith("ies") and len(t) > 3:
        out.add(t[:-3] + "y")
    elif t.endswith("s") and not t.endswith("ss") and len(t) > 1:
        out.add(t[:-1])
    return out


def name_evidence(child: str, parent_table: str, parent_key: str) -> dict[str, Any]:
    """How well ``child`` is named after ``parent_table.parent_key``."""
    c, k = child.lower(), parent_key.lower()
    if c == k and k not in _GENERIC_KEYS:
        return {"score": 1.0, "rule": "same name as the parent key"}
    stems = _stems(parent_table)
    for s in sorted(stems):
        if any(c == f"{s}{suffix}" for suffix in _SUFFIXES):
            return {"score": 0.9, "rule": "parent table name with a key suffix"}
    if k not in _GENERIC_KEYS and len(c) > len(k) and c.endswith(f"_{k}"):
        return {"score": 0.6, "rule": "ends with the parent key name"}
    for s in sorted(stems):
        if c.endswith(f"_{s}_id") or c.endswith(f"_{s}id"):
            return {"score": 0.6, "rule": "ends with the parent table name and a key suffix"}
        if c == s:
            return {"score": 0.5, "rule": "same name as the parent table"}
    return {"score": 0.0, "rule": "no name evidence"}


def _is_parent_key(col: ColumnProfile, rows: int) -> bool:
    if col.dtype not in ("integer", "string") or rows < 1:
        return False
    if col.is_primary_key:
        return True
    return bool(col.is_unique) and col.null_count == 0 and col.cardinality == rows


def _type_evidence(child: ColumnProfile, parent: ColumnProfile) -> dict[str, Any]:
    ok = child.dtype == parent.dtype or {child.dtype, parent.dtype} == {"integer", "float"}
    return {"compatible": ok, "child": child.dtype, "parent": parent.dtype}


def _range_evidence(child: ColumnProfile, parent: ColumnProfile) -> dict[str, Any]:
    pair = (child.min_value, child.max_value, parent.min_value, parent.max_value)
    if any(v is None for v in pair):
        return {"within": None}
    try:
        within = bool(pair[2] <= pair[0] and pair[1] <= pair[3])
    except TypeError:
        return {"within": None}
    return {
        "within": within,
        "child": [_plain(pair[0]), _plain(pair[1])],
        "parent": [_plain(pair[2]), _plain(pair[3])],
    }


def _plain(v: Any) -> Any:
    return v if isinstance(v, (int, float, str, bool)) else str(v)


def _comparable(a: pa.Array, b: pa.Array) -> tuple[pa.Array, pa.Array] | None:
    import pyarrow as pa

    try:
        if pa.types.is_integer(a.type) and pa.types.is_integer(b.type):
            return a.cast(pa.int64()), b.cast(pa.int64())
        if (pa.types.is_integer(a.type) or pa.types.is_floating(a.type)) and (
            pa.types.is_integer(b.type) or pa.types.is_floating(b.type)
        ):
            return a.cast(pa.float64()), b.cast(pa.float64())
        if (pa.types.is_string(a.type) or pa.types.is_large_string(a.type)) and (
            pa.types.is_string(b.type) or pa.types.is_large_string(b.type)
        ):
            return a.cast(pa.large_string()), b.cast(pa.large_string())
        return (a, b) if a.type == b.type else None
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return None


def _containment(child: pa.ChunkedArray, parent: pa.ChunkedArray) -> tuple[float, int] | None:
    """(share of the child's non-null rows found in the parent, child distinct values)."""
    import pyarrow.compute as pc  # type: ignore[import-untyped]

    values = child.drop_null().combine_chunks()
    if len(values) == 0:
        return None
    pair = _comparable(values, parent.drop_null().combine_chunks())
    if pair is None:
        return None
    c, p = pair
    found = pc.sum(pc.cast(pc.is_in(c, value_set=p), "int64")).as_py() or 0
    return found / len(c), len(pc.unique(c))


def _parent_unique(parent: pa.ChunkedArray) -> bool:
    """Uniqueness of the parent column, by the existing key discovery (``shape key``)."""
    rows = ({"k": v} for v in parent.to_pylist())
    evidence = candidate_key(rows, ("k",))
    return evidence.unique


def _candidates(ds: DatasetProfile) -> Iterator[tuple[str, str, str, str, bool]]:
    """(child table, child column, parent table, parent column, profiler_detected)."""
    detected: set[tuple[str, str, str, str]] = set()
    for rel in ds.relationships:
        if rel.get("type") == "many_to_many":
            continue  # recorded in the profile, never a foreign key
        for cc, pcol in zip(
            rel.get("child_columns", []), rel.get("parent_columns", []), strict=False
        ):
            detected.add((rel["child"], cc, rel["parent"], pcol))
    for tname, tp in ds.tables.items():
        for cc, parent in tp.detected_fks.items():
            pk = ds.tables[parent].primary_key if parent in ds.tables else []
            if pk:
                detected.add((tname, cc, parent, pk[0]))
    seen = set(detected)
    for key in sorted(detected):
        yield (*key, True)
    for ptable, pt in ds.tables.items():
        for pname, pcol in pt.columns.items():
            if not _is_parent_key(pcol, pt.row_count):
                continue
            for ctable, ct in ds.tables.items():
                for cname, ccol in ct.columns.items():
                    key = (ctable, cname, ptable, pname)
                    if key in seen or (ctable == ptable and cname == pname):
                        continue
                    if ccol.is_primary_key and len(ct.primary_key) == 1:
                        continue  # a lone own key: not a reference
                    seen.add(key)
                    yield (*key, False)


def _confidence(
    name: float, containment: float | None, range_ok: bool | None, cardinality_ok: bool
) -> float:
    r = 1.0 if range_ok else 0.0
    k = 1.0 if cardinality_ok else 0.0
    if containment is None:
        return (
            NO_DATA_CAP
            * (W_NAME * name + W_RANGE * r + W_CARDINALITY * k)
            / (W_NAME + W_RANGE + W_CARDINALITY)
        )
    return W_NAME * name + W_CONTAINMENT * containment + W_RANGE * r + W_CARDINALITY * k


def propose_relationships(
    profile: Any, data: DataSource | None = None, *, min_confidence: float = 0.5
) -> list[Proposal]:
    """Candidate foreign keys of a multi-table ``profile`` as proposals, the most confident first.

    ``data`` (a mapping of table name to table or file, or a directory) adds the containment
    evidence; without it the proposals rest on names, types, ranges and cardinality alone and
    are less confident."""
    if isinstance(min_confidence, bool) or not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be from 0 to 1")
    ds = dataset_of(profile)
    tables = load_data(data, ds)
    found: dict[str, Proposal] = {}
    named_children: set[tuple[str, str]] = set()
    pending: list[tuple[tuple[str, str, str, str, bool], Proposal]] = []
    for ctable, cname, ptable, pname, detected in _candidates(ds):
        pt: TableProfile | None = ds.tables.get(ptable)
        ct: TableProfile | None = ds.tables.get(ctable)
        if pt is None or ct is None or pname not in pt.columns or cname not in ct.columns:
            continue
        ccol, pcol = ct.columns[cname], pt.columns[pname]
        type_ev = _type_evidence(ccol, pcol)
        if not type_ev["compatible"]:
            continue
        if ccol.cardinality < 1 or ct.row_count < 1:
            continue
        name_ev = name_evidence(cname, ptable, pname)
        range_ev = _range_evidence(ccol, pcol)
        evidence: dict[str, Any] = {"name": name_ev, "type": type_ev, "range": range_ev}
        child_distinct = ccol.cardinality
        contained: float | None = None
        parent_unique = _is_parent_key(pcol, pt.row_count)
        if ctable in tables and ptable in tables:
            result = _containment(tables[ctable][cname], tables[ptable][pname])
            if result is None:
                continue
            contained, child_distinct = result
            evidence["containment"] = {"fraction": contained, "child_distinct": child_distinct}
            parent_unique = _parent_unique(tables[ptable][pname])
            if contained < MIN_CONTAINMENT and not detected:
                continue
        if not parent_unique and not detected:
            continue
        parent_distinct = pcol.cardinality
        evidence["cardinality"] = {
            "parent_unique": parent_unique,
            "parent_distinct": parent_distinct,
            "child_distinct": child_distinct,
        }
        if detected:
            evidence["profiler_detected"] = True
        score = name_ev["score"]
        if score == 0.0 and not detected:
            # no name evidence: only a column that matches the parent's values closely and is
            # not a small code qualifies, and never without the data
            if contained is None or contained < 0.99 or child_distinct < UNNAMED_MIN_DISTINCT:
                continue
        conf = _confidence(score, contained, range_ev["within"], child_distinct <= parent_distinct)
        if conf < min_confidence:
            continue
        if score >= 0.6:
            named_children.add((ctable, cname))
        pid_subject = f"{ctable}.{cname}->{ptable}.{pname}"
        pending.append(
            (
                (ctable, cname, ptable, pname, detected),
                Proposal(
                    f"relationship:{pid_subject}",
                    "relationship",
                    pid_subject,
                    {
                        "child": ctable,
                        "child_columns": [cname],
                        "parent": ptable,
                        "parent_columns": [pname],
                        "type": "one_to_many",
                    },
                    conf,
                    evidence,
                ),
            )
        )
    for (ctable, cname, _pt, _pn, _d), p in pending:
        if p.evidence["name"]["score"] == 0.0 and (ctable, cname) in named_children:
            continue  # a better-named parent exists for this column
        found[p.id] = p
    return sorted(found.values(), key=lambda p: (-p.confidence, p.id))
