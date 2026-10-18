"""Nested documents to flat, related Arrow tables (W5-07).

JSON documents (and XML records, which :mod:`shape.io.xmlread` turns into the same dictionaries)
come in two shapes:

* ``flatten="struct"``: one table; nested objects stay Arrow structs and arrays stay lists.
* ``flatten="tables"``: one table per level. Every table has an ``_id`` (1, 2, ... in document
  order). An array of objects becomes a child table ``<parent>__<field>`` with ``_parent_id``
  (the parent's ``_id``) and ``_ordinal`` (the position in the array, from 0); an array of
  scalars becomes a child table with a ``value`` column; a nested object becomes prefixed
  columns (``address.city``). The links come back as :class:`~shape.generation.schema.Relationship`
  objects, which ``shape generate`` reads.

Everything here is structural. Nothing is validated against a schema and no value is interpreted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.security.jsondepth import MAX_JSON_DEPTH, check_json_document, check_json_file

from .readers import ReaderError

if TYPE_CHECKING:
    from shape.generation.schema import Relationship

FLATTEN_MODES = ("struct", "tables")
MAX_DOCUMENT_BYTES = 256 * 1024 * 1024
META_KIND = b"shape.nested.kind"
META_PARENT = b"shape.nested.parent"
META_PATH = b"shape.nested.path"
ID, PARENT_ID, ORDINAL, VALUE = "_id", "_parent_id", "_ordinal", "value"
_TABLE_NAME = re.compile(r"[^0-9A-Za-z_]+")


@dataclass(slots=True)
class NestedTables:
    """The tables of one flattened input, how they link, and what the reader noticed."""

    tables: dict[str, pa.Table]
    relationships: list[Relationship] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_flatten(flatten: str | None) -> str | None:
    """Validate the flatten mode and return it; reject an unsupported mode."""
    if flatten is not None and flatten not in FLATTEN_MODES:
        raise ValueError(f"flatten must be one of {list(FLATTEN_MODES)} or None, not {flatten!r}")
    return flatten


def table_name(text: str) -> str:
    """``text`` as a table name: letters, digits and ``_`` only."""
    return _TABLE_NAME.sub("_", text).strip("_") or "table"


class _Acc:
    """Rows of one table while the documents are walked."""

    __slots__ = ("columns", "kind", "name", "parent", "path", "rows")

    def __init__(self, name: str, parent: str | None, path: tuple[str, ...]) -> None:
        self.name = name
        self.parent = parent
        self.path = path
        self.kind = "root" if parent is None else "scalars"
        self.rows: list[dict[str, Any]] = []
        self.columns: dict[str, None] = {}


class _Flattener:
    def __init__(self) -> None:
        self.accs: dict[str, _Acc] = {}

    def acc(self, name: str, parent: str | None, path: tuple[str, ...]) -> _Acc:
        found = self.accs.get(name)
        if found is None:
            found = self.accs[name] = _Acc(name, parent, path)
        elif (found.parent, found.path) != (parent, path):
            raise ReaderError(
                f"two different fields both flatten to the table name {name!r}; "
                "rename one of them or use flatten='struct'"
            )
        return found

    def add(
        self, acc: _Acc, record: Mapping[str, Any], parent_id: int | None, ordinal: int
    ) -> None:
        row: dict[str, Any] = {ID: len(acc.rows) + 1}
        if acc.parent is not None:
            row[PARENT_ID] = parent_id
            row[ORDINAL] = ordinal
        acc.rows.append(row)
        self._fill(acc, row, "", record, ())

    def _fill(
        self,
        acc: _Acc,
        row: dict[str, Any],
        prefix: str,
        obj: Mapping[str, Any],
        rel: tuple[str, ...],
    ) -> None:
        for key, val in obj.items():
            k = str(key)
            col = prefix + k
            if isinstance(val, dict):
                self._fill(acc, row, col + ".", val, (*rel, k))
            elif isinstance(val, list):
                self._array(acc, row[ID], (*rel, k), val)
            else:
                row[col] = val
                acc.columns.setdefault(col)

    def _array(self, parent: _Acc, parent_id: int, rel: tuple[str, ...], items: list[Any]) -> None:
        child = self.acc(f"{parent.name}__{'__'.join(rel)}", parent.name, rel)
        for ordinal, item in enumerate(items):
            if isinstance(item, dict):
                child.kind = "objects"
                self.add(child, item, parent_id, ordinal)
            else:
                self.add(child, {VALUE: item}, parent_id, ordinal)


def _column(values: list[Any], where: str, warnings: list[str]) -> pa.Array:
    """One column. All-null is string, int and float widen to float64, any other mix is read as
    string (non-strings as JSON text) with a warning."""
    kinds = {type(v) for v in values if v is not None}
    try:
        if not kinds or kinds == {str}:
            return pa.array(values, pa.string())
        if kinds == {bool}:
            return pa.array(values, pa.bool_())
        if kinds == {int}:
            return pa.array(values, pa.int64())
        if kinds <= {int, float} and bool not in kinds:
            return pa.array(values, pa.float64())
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
        pass
    warnings.append(f"{where}: values of mixed types were read as string")
    return pa.array(
        [None if v is None else v if isinstance(v, str) else json.dumps(v) for v in values],
        pa.string(),
    )


def _table(acc: _Acc, warnings: list[str]) -> pa.Table:
    system = [ID] + ([PARENT_ID, ORDINAL] if acc.parent is not None else [])
    cols: dict[str, pa.Array] = {}
    for name in system:
        cols[name] = pa.array([r[name] for r in acc.rows], pa.int64())
    for name in acc.columns:
        cols[name] = _column([r.get(name) for r in acc.rows], f"{acc.name}.{name}", warnings)
    meta = {META_KIND: acc.kind.encode(), META_PATH: json.dumps(list(acc.path)).encode()}
    if acc.parent is not None:
        meta[META_PARENT] = acc.parent.encode()
    return pa.table(cols).replace_schema_metadata(meta)


def flatten_documents(name: str, documents: Iterable[Any]) -> NestedTables:
    """Flatten ``documents`` (each a dict) into related tables; the root table is ``name``."""
    from shape.generation.schema import Relationship

    fl = _Flattener()
    root = fl.acc(table_name(name), None, ())
    for i, doc in enumerate(documents):
        if not isinstance(doc, dict):
            raise ReaderError(
                f"document {i} is {type(doc).__name__}, not an object; only objects can be read "
                "as rows"
            )
        fl.add(root, doc, None, 0)
    warnings: list[str] = []
    tables = {a.name: _table(a, warnings) for a in fl.accs.values()}
    rels = [
        Relationship(
            name=f"{a.name}_to_{a.parent}",
            parent=a.parent,
            child=a.name,
            parent_columns=[ID],
            child_columns=[PARENT_ID],
            type="one_to_many",
        )
        for a in fl.accs.values()
        if a.parent is not None
    ]
    return NestedTables(tables, rels, warnings)


def struct_table(documents: list[Any]) -> pa.Table:
    """One table from ``documents``, nested objects as structs and arrays as lists."""
    for i, doc in enumerate(documents):
        if not isinstance(doc, dict):
            raise ReaderError(f"document {i} is {type(doc).__name__}, not an object")
    if not documents:
        return pa.table({})
    try:
        return pa.Table.from_pylist(documents)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        raise ReaderError(
            f"documents cannot be read with flatten='struct' ({exc}); mixed types in one field "
            "need flatten='tables'"
        ) from exc


def load_documents(
    path: str | Path, *, lines: bool, max_bytes: int = MAX_DOCUMENT_BYTES
) -> list[Any]:
    """The documents of a ``.json`` file (one document or an array of them) or of a JSON-lines
    file. Size and depth are checked before parsing; a document over either limit raises
    :class:`ReaderError` naming the limit."""
    path = Path(path)
    size = path.stat().st_size
    if size > max_bytes:
        raise ReaderError(
            f"{path} is {size} bytes, over the {max_bytes}-byte limit (option max_bytes)"
        )
    try:
        if lines:
            check_json_file(path)
        else:
            check_json_document(path.read_bytes())
    except ValueError as exc:
        raise ReaderError(f"cannot parse {path} as JSON: {exc}") from exc
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReaderError(f"cannot parse {path} as JSON: not UTF-8 ({exc})") from exc
    if lines:
        docs: list[Any] = []
        for n, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                docs.append(json.loads(line))
            except (json.JSONDecodeError, RecursionError) as exc:
                raise ReaderError(f"cannot parse {path} line {n} as JSON: {exc}") from exc
        return docs
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ReaderError(f"cannot parse {path} as JSON: {exc}") from exc
    return value if isinstance(value, list) else [value]


def read_nested(
    path: str | Path,
    *,
    lines: bool = False,
    flatten: str = "struct",
    name: str | None = None,
    max_bytes: int = MAX_DOCUMENT_BYTES,
) -> NestedTables:
    """A JSON or JSON-lines file as tables (see the module docstring)."""
    check_flatten(flatten)
    path = Path(path)
    docs = load_documents(path, lines=lines, max_bytes=max_bytes)
    root = name or path.name.split(".")[0]
    if flatten == "tables":
        return flatten_documents(root, docs)
    return NestedTables({table_name(root): struct_table(docs)})


__all__ = [
    "FLATTEN_MODES",
    "MAX_DOCUMENT_BYTES",
    "MAX_JSON_DEPTH",
    "NestedTables",
    "check_flatten",
    "flatten_documents",
    "load_documents",
    "read_nested",
    "struct_table",
    "table_name",
]
