"""Profile a whole semantic model: every table as a dataset profile, with the model's declared
relationships (``shape profile-model``), the way ``shape profile-db`` does for a SQL schema.

The tables are read through the ``semantic-model://`` source (:mod:`shape_fabric.semantic_source`)
and profiled together by Shape's own profiler, so a table's column statistics equal those of the
same rows profiled from a Parquet file. What the model adds, from
``sempy.fabric.list_relationships``:

* each relationship is recorded in the profile's ``relationships`` with ``evidence: "declared"``
  and ``source: "semantic model"``, plus ``active`` (``false`` for an inactive one: still a
  declared key, as with a role-playing date table) and ``type`` (``one_to_many``, ``one_to_one``
  or ``many_to_many``);
* the child column of a one-to-many or one-to-one relationship is marked a foreign key
  (``is_foreign_key``, ``fk_ref_table``, ``fk_evidence: "declared"``, ``detected_fks``) and the
  "one" side, when the profile has no key for that table, becomes its primary key;
* a many-to-many relationship is recorded and nothing else: it is not a foreign key;
* a hidden column is profiled and carries ``hidden: true``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import semantic_source as ss

EVIDENCE = "declared"
SOURCE = "semantic model"
_KINDS = {"m:1": "one_to_many", "1:m": "one_to_many", "1:1": "one_to_one", "m:m": "many_to_many"}


@dataclass(frozen=True)
class DeclaredRelationship:
    child: str
    child_column: str
    parent: str
    parent_column: str
    kind: str
    active: bool

    def label(self) -> str:
        return f"{self.child}[{self.child_column}] -> {self.parent}[{self.parent_column}]"


def _warn(message: str) -> None:
    print(f"shape: warning: semantic-model {message}", file=sys.stderr)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() != "false"
    return bool(value)


def read_relationships(workspace: str, model: str) -> list[DeclaredRelationship]:
    """The model's relationships (``sempy.fabric.list_relationships``), the "many" side as the
    child. One with a multiplicity this code does not know is reported on stderr and left out."""
    fab = ss.check_model(workspace, model)
    frame = ss._frame(
        fab, "list_relationships", workspace, model, dataset=model, workspace=workspace
    )
    out: list[DeclaredRelationship] = []
    for _, row in frame.iterrows():
        frm, fcol = str(row["From Table"]), str(row["From Column"])
        to, tcol = str(row["To Table"]), str(row["To Column"])
        raw = str(row["Multiplicity"])
        mult = raw.strip().lower().replace(" ", "").replace("*", "m")
        active = True
        for key in ("Active", "Is Active"):
            if key in frame.columns:
                active = _truthy(row[key])
                break
        if mult not in _KINDS:
            _warn(
                f"relationship {frm}[{fcol}] -> {to}[{tcol}] has multiplicity {raw}; not recorded"
            )
            continue
        if mult == "1:m":  # the "from" side is the one side
            out.append(DeclaredRelationship(to, tcol, frm, fcol, _KINDS[mult], active))
        else:
            out.append(DeclaredRelationship(frm, fcol, to, tcol, _KINDS[mult], active))
    return out


def _check_cap(max_rows: Any) -> None:
    if max_rows is not None and (
        isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 0
    ):
        raise ShapeError(f"max_rows must be an integer of at least 0, got {max_rows!r}")


def _chosen(workspace: str, model: str, tables: list[str] | None) -> list[str]:
    names = ss.table_names(workspace, model)
    if tables is None:
        if not names:
            raise ShapeError(f"semantic model {model} in workspace {workspace} has no tables")
        return names
    if not tables:
        raise ShapeError("tables must name at least one table")
    chosen: list[str] = []
    for t in tables:
        if t not in names:
            raise ShapeError(f"semantic model {model} in workspace {workspace} has no table {t}")
        if t not in chosen:
            chosen.append(t)
    return chosen


def profile_model(
    workspace: str,
    model: str,
    *,
    tables: list[str] | None = None,
    max_rows: int | None = None,
) -> Any:
    """The dataset profile of a semantic model (a ``shape.profile.reference.Profile``).

    ``tables`` selects some tables (default: every table, hidden ones too) and ``max_rows`` caps
    the rows read from each (a DAX ``TOPN``). A relationship is recorded when both its tables
    are profiled."""
    _check_cap(max_rows)
    chosen = _chosen(workspace, model, tables)
    data: dict[str, pa.Table] = {}
    hidden: dict[str, set[str]] = {}
    for table in chosen:
        infos = ss.column_infos(workspace, model, table)
        if not infos:
            raise ShapeError(
                f"semantic model {model} in workspace {workspace} has no columns in table {table}"
            )
        ss.warn_unknown(workspace, model, table, infos)
        data[table] = ss.read_table(workspace, model, table, infos, max_rows=max_rows)
        hidden[table] = {c.name for c in infos if c.hidden}
    relationships = read_relationships(workspace, model)

    import shape
    from shape.profile.reference import Profile

    doc = shape.profile(data).to_dict()
    for table, names in hidden.items():
        for name in names:
            doc["tables"][table]["columns"][name]["hidden"] = True
    from .semantic_metadata import read_measures, read_roles

    measures = read_measures(workspace, model)
    for table in chosen:
        doc["tables"][table]["measures"] = measures.get(table, [])
    doc["roles"] = read_roles(workspace, model)
    _declare(doc, relationships)
    return Profile(
        doc,
        name=model,
        provenance={
            "source": "semantic-model",
            "workspace": workspace,
            "model": model,
            "max_rows": max_rows,
        },
    )


def _declare(doc: dict[str, Any], relationships: list[DeclaredRelationship]) -> None:
    tables = doc["tables"]
    usable: list[DeclaredRelationship] = []
    for r in relationships:
        if r.child not in tables or r.parent not in tables:
            continue  # a table that was not profiled
        missing = [
            f"{t}[{c}]"
            for t, c in ((r.child, r.child_column), (r.parent, r.parent_column))
            if c not in tables[t]["columns"]
        ]
        if missing:
            _warn(f"relationship {r.label()} names {missing[0]}, which the profile does not have")
            continue
        usable.append(r)
    declared: list[dict[str, Any]] = []
    names: set[str] = set()
    # an active relationship claims its child column before an inactive one does
    for r in sorted(usable, key=lambda x: not x.active):
        name = base = f"fk_{r.child}_{r.child_column}"
        n = 1
        while name in names:
            n += 1
            name = f"{base}_{n}"
        names.add(name)
        declared.append(
            {
                "name": name,
                "parent": r.parent,
                "child": r.child,
                "parent_columns": [r.parent_column],
                "child_columns": [r.child_column],
                "type": r.kind,
                "evidence": EVIDENCE,
                "source": SOURCE,
                "active": r.active,
            }
        )
        if r.kind == "many_to_many":
            continue
        child = tables[r.child]
        col = child["columns"][r.child_column]
        if not col.get("is_foreign_key") or col.get("fk_evidence") != EVIDENCE:
            col["is_foreign_key"] = True
            col["fk_ref_table"] = r.parent
            col["fk_evidence"] = EVIDENCE
            child["detected_fks"][r.child_column] = r.parent
        parent = tables[r.parent]
        if not parent["primary_key"]:
            parent["primary_key"] = [r.parent_column]
            parent["columns"][r.parent_column]["is_primary_key"] = True
    covered = {(d["child"], tuple(d["child_columns"])) for d in declared}
    kept = [
        {**d, "evidence": "data"}
        for d in doc["relationships"]
        if (d["child"], tuple(d["child_columns"])) not in covered
    ]
    order = {name: i for i, name in enumerate(tables)}
    doc["relationships"] = sorted(
        [*declared, *kept], key=lambda d: (order.get(d["child"], 0), d["name"])
    )
