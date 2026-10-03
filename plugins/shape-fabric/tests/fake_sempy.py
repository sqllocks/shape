"""A fake ``sempy.fabric`` for tests that must not need Fabric.

It answers the calls the ``semantic-model://`` source makes (``list_workspaces``,
``list_datasets``, ``list_tables``, ``list_columns``, ``list_relationships``, ``read_table`` and
``evaluate_dax``) from an in-memory model, records every call in ``calls``, and answers
``evaluate_dax`` only for the exact expression form the source writes: the expression is parsed
with strict patterns, so a name that escaped its quoting would not parse and the call would fail.
"""

from __future__ import annotations

import re
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

_Q = r"'((?:[^']|'')*)'"
_COL = r"\[((?:[^\]]|\]\])*)\]"
_ALIAS = r'"((?:[^"]|"")*)"'
_TOPN = re.compile(rf"EVALUATE SELECTCOLUMNS\(TOPN\((\d+), {_Q}\)((?:, {_ALIAS}, {_Q}{_COL})+)\)")
_PAIR = re.compile(rf", {_ALIAS}, {_Q}{_COL}")


@dataclass
class FakeTable:
    """``columns`` are ``(name, data type, hidden)``; ``rows`` a DataFrame of those columns."""

    columns: list[tuple[str, str, bool]]
    rows: pd.DataFrame
    hidden: bool = False


@dataclass
class FakeModel:
    tables: dict[str, FakeTable]
    relationships: list[dict[str, Any]] = field(default_factory=list)
    id: str = "00000000-0000-0000-0000-0000000000aa"


class FakeFabric(types.ModuleType):
    """The ``sempy.fabric`` namespace over ``{workspace name: {model name: FakeModel}}``."""

    def __init__(
        self,
        workspaces: dict[str, dict[str, FakeModel]],
        *,
        read_table_has_mode: bool = True,
        data_type_column: str | None = "Data Type",
    ) -> None:
        super().__init__("sempy.fabric")
        self.workspaces = workspaces
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._type_column = data_type_column
        if not read_table_has_mode:
            self.read_table = self._read_table_no_mode  # type: ignore[method-assign]

    # -- lookups -----------------------------------------------------------------------

    def _ws(self, workspace: str | None) -> dict[str, FakeModel]:
        for i, (name, models) in enumerate(self.workspaces.items()):
            if workspace in (name, _guid(i)):
                return models
        raise ValueError(f"workspace {workspace!r} not found")

    def _model(self, dataset: str, workspace: str | None) -> FakeModel:
        models = self._ws(workspace)
        for name, model in models.items():
            if dataset in (name, model.id):
                return model
        raise ValueError(f"dataset {dataset!r} not found")

    def _table(self, dataset: str, table: str, workspace: str | None) -> FakeTable:
        model = self._model(dataset, workspace)
        if table not in model.tables:
            raise ValueError(f"table {table!r} not found")
        return model.tables[table]

    def _log(self, name: str, **kw: Any) -> None:
        self.calls.append((name, kw))

    def called(self, name: str) -> list[dict[str, Any]]:
        return [kw for n, kw in self.calls if n == name]

    # -- the sempy.fabric surface ------------------------------------------------------

    def list_workspaces(self, **_: Any) -> pd.DataFrame:
        self._log("list_workspaces")
        return pd.DataFrame(
            {"Id": [_guid(i) for i in range(len(self.workspaces))], "Name": list(self.workspaces)}
        )

    def list_datasets(self, workspace: str | None = None, **_: Any) -> pd.DataFrame:
        self._log("list_datasets", workspace=workspace)
        models = self._ws(workspace)
        return pd.DataFrame(
            {"Dataset Name": list(models), "Dataset Id": [m.id for m in models.values()]}
        )

    def list_tables(self, dataset: str, workspace: str | None = None, **_: Any) -> pd.DataFrame:
        self._log("list_tables", dataset=dataset, workspace=workspace)
        model = self._model(dataset, workspace)
        return pd.DataFrame(
            {"Name": list(model.tables), "Hidden": [t.hidden for t in model.tables.values()]}
        )

    def list_columns(
        self, dataset: str, table: str | None = None, workspace: str | None = None, **_: Any
    ) -> pd.DataFrame:
        self._log("list_columns", dataset=dataset, table=table, workspace=workspace)
        model = self._model(dataset, workspace)
        rows = []
        for tname, t in model.tables.items():
            if table is not None and tname != table:
                continue
            for cname, dtype, hidden in t.columns:
                row: dict[str, Any] = {
                    "Table Name": tname,
                    "Column Name": cname,
                    "Type": "Data",
                    "Hidden": hidden,
                }
                if self._type_column:
                    row[self._type_column] = dtype
                rows.append(row)
        return pd.DataFrame(rows, columns=_column_names(self._type_column))

    def list_relationships(
        self, dataset: str, workspace: str | None = None, **_: Any
    ) -> pd.DataFrame:
        self._log("list_relationships", dataset=dataset, workspace=workspace)
        model = self._model(dataset, workspace)
        return pd.DataFrame(
            model.relationships,
            columns=[
                "From Table",
                "From Column",
                "To Table",
                "To Column",
                "Multiplicity",
                "Active",
            ],
        )

    def read_table(
        self,
        dataset: str,
        table: str,
        fully_qualified_columns: bool = False,
        num_rows: int | None = None,
        mode: str = "xmla",
        workspace: str | None = None,
        **_: Any,
    ) -> pd.DataFrame:
        self._log("read_table", dataset=dataset, table=table, mode=mode, workspace=workspace)
        return self._table(dataset, table, workspace).rows.copy()

    def _read_table_no_mode(
        self,
        dataset: str,
        table: str,
        fully_qualified_columns: bool = False,
        num_rows: int | None = None,
        workspace: str | None = None,
    ) -> pd.DataFrame:
        self._log("read_table", dataset=dataset, table=table, workspace=workspace)
        return self._table(dataset, table, workspace).rows.copy()

    def evaluate_dax(
        self, dataset: str, dax_string: str, workspace: str | None = None, **_: Any
    ) -> pd.DataFrame:
        self._log("evaluate_dax", dataset=dataset, dax=dax_string, workspace=workspace)
        m = _TOPN.fullmatch(dax_string)
        if m is None:
            raise ValueError(f"the fake cannot parse this DAX: {dax_string!r}")
        n, table = int(m.group(1)), m.group(2).replace("''", "'")
        tbl = self._table(dataset, table, workspace)
        pairs = [
            (a.replace('""', '"'), t.replace("''", "'"), c.replace("]]", "]"))
            for a, t, c in _PAIR.findall(m.group(3))
        ]
        frame = tbl.rows.head(n)
        out = {}
        for alias, t, c in pairs:
            if t != table:
                raise ValueError(f"column of another table in {dax_string!r}")
            out[f"[{alias}]"] = frame[c].to_list()
        return pd.DataFrame(out)


def _guid(i: int) -> str:
    return f"11111111-1111-1111-1111-{i:012d}"


def _column_names(type_column: str | None) -> list[str]:
    names = ["Table Name", "Column Name", "Type", "Hidden"]
    return [*names, type_column] if type_column else names


def install(monkeypatch: Any, fabric: FakeFabric) -> FakeFabric:
    """Make ``import sempy.fabric`` find ``fabric`` for the length of the test."""
    pkg = types.ModuleType("sempy")
    pkg.fabric = fabric  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sempy", pkg)
    monkeypatch.setitem(sys.modules, "sempy.fabric", fabric)
    return fabric


def retail() -> dict[str, dict[str, FakeModel]]:
    """Workspace ``Sales`` with model ``Retail``: Customer (1) -> Orders (many), a hidden key
    column, a column of a type the source does not know, and awkward names."""
    customer = FakeTable(
        columns=[
            ("CustomerKey", "Int64", True),
            ("Name", "String", False),
            ("Code", "Variant", False),
            ("Joined", "DateTime", False),
        ],
        rows=pd.DataFrame(
            {
                "CustomerKey": [1, 2, 3, 4],
                "Name": ["Ann", "Bo", None, "Di"],
                "Code": ["a", "b", "c", "d"],
                "Joined": pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01", None]),
            }
        ),
    )
    orders = FakeTable(
        columns=[
            ("OrderKey", "Int64", False),
            ("CustomerKey", "Int64", False),
            ("Amount", "Decimal", False),
            ("Quantity", "Double", False),
            ("Paid", "Boolean", False),
            ("Blob", "Binary", False),
        ],
        rows=pd.DataFrame(
            {
                "OrderKey": [10, 11, 12, 13, 14],
                "CustomerKey": [1, 1, 2, 3, 4],
                "Amount": [10.5, 20.25, 0.0, 99.99, 5.0],
                "Quantity": [1.0, 2.5, 3.0, 4.0, 5.5],
                "Paid": [True, False, True, True, None],
                "Blob": [b"a", b"b", b"c", b"d", None],
            }
        ),
    )
    odd = FakeTable(
        columns=[("It's [odd]", "String", False), ('a"b', "Int64", False)],
        rows=pd.DataFrame({"It's [odd]": ["x", "y", "z"], 'a"b': [1, 2, 3]}),
    )
    empty = FakeTable(columns=[("Id", "Int64", False)], rows=pd.DataFrame({"Id": []}))
    model = FakeModel(
        tables={"Customer": customer, "Orders": orders, "Odd 'table'": odd, "Empty": empty},
        relationships=[
            {
                "From Table": "Orders",
                "From Column": "CustomerKey",
                "To Table": "Customer",
                "To Column": "CustomerKey",
                "Multiplicity": "m:1",
                "Active": True,
            }
        ],
    )
    return {"Sales": {"Retail": model}}
