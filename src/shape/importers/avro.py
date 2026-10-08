"""Avro schemas (``.avsc``) to the importer model (W5-06), parsed in Python.

A record is a table. A record defined inline in a field, or in an array, is a child table with a
generated foreign key to its parent; a field that names a record defined elsewhere is a foreign
key column. An array of scalars is a child value table, a map a child table with ``key`` and
``value`` columns. A union with ``null`` makes the column nullable; a union with several other
branches is a string. Logical types ``decimal``, ``date``, ``timestamp-millis``,
``timestamp-micros`` and ``uuid`` (and the time and local timestamp ones) set the column type.
``bytes`` and ``fixed`` have no generator, so their columns are left out and reported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shape.importers.core import (
    ImpColumn,
    ImpForeignKey,
    ImpModel,
    ImportFormatError,
    ImpTable,
    Report,
    clean_name,
    finish_model,
    unique_name,
)
from shape.importers.documents import Document, load_document

_PRIMITIVE = {
    "boolean": "boolean",
    "int": "integer",
    "long": "integer",
    "float": "float",
    "double": "float",
    "string": "string",
}
_LOGICAL = {
    ("int", "date"): "date",
    ("int", "time-millis"): "time",
    ("long", "time-micros"): "time",
    ("long", "timestamp-millis"): "timestamp",
    ("long", "timestamp-micros"): "timestamp",
    ("long", "local-timestamp-millis"): "timestamp",
    ("long", "local-timestamp-micros"): "timestamp",
    ("string", "uuid"): "uuid",
}
_NAMED = ("record", "error", "enum", "fixed")


class _Walker:
    def __init__(self, doc: Document, report: Report) -> None:
        self.doc = doc
        self.report = report
        self.model = ImpModel(Path(doc.path).stem)
        self.tables: dict[str, ImpTable] = {}
        self.enums: dict[str, list[str]] = {}
        self.fixed: set[str] = set()
        self._used: set[str] = set()
        self._links: list[tuple[ImpTable, ImpForeignKey, ImpTable]] = []

    def at(self, pointer: str) -> str:
        return "#" + pointer

    def fail(self, message: str, pointer: str) -> ImportFormatError:
        return ImportFormatError(
            message, file=self.doc.path, line=self.doc.line(pointer), element=self.at(pointer)
        )

    # ---- names ----

    def fullname(self, node: dict[str, Any], ns: str, pointer: str) -> tuple[str, str, str]:
        """``(simple name, fullname, namespace)`` of a named type."""
        name = node.get("name")
        if not isinstance(name, str) or not name:
            raise self.fail(f"a {node.get('type')} needs a name", pointer)
        if "." in name:
            ns, name = name.rsplit(".", 1)
        elif isinstance(node.get("namespace"), str):
            ns = node["namespace"]
        return name, f"{ns}.{name}" if ns else name, ns

    def lookup(self, ref: str, ns: str, registry: dict[str, Any]) -> str | None:
        for candidate in (f"{ns}.{ref}" if ns and "." not in ref else None, ref):
            if candidate is not None and candidate in registry:
                return candidate
        return None

    # ---- tables ----

    def record(self, node: dict[str, Any], pointer: str, ns: str) -> ImpTable:
        name, full, ns = self.fullname(node, ns, pointer)
        fields = node.get("fields")
        if not isinstance(fields, list) or not fields:
            raise self.fail(f"record {name!r} needs a non-empty fields array", pointer)
        table = ImpTable(unique_name(clean_name(name), self._used), source=self.at(pointer))
        self.model.tables.append(table)
        self.tables[full] = table
        used: set[str] = set()
        for i, field in enumerate(fields):
            self.field(table, field, f"{pointer}/fields/{i}", ns, used)
        return table

    def field(self, table: ImpTable, field: Any, pointer: str, ns: str, used: set[str]) -> None:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str):
            raise self.fail("a field needs a name", pointer)
        if "type" not in field:
            raise self.fail(f"field {field['name']!r} has no type", pointer)
        self.typed(table, str(field["name"]), field["type"], f"{pointer}/type", ns, used, False)

    # ---- types ----

    def typed(
        self,
        table: ImpTable,
        name: str,
        node: Any,
        pointer: str,
        ns: str,
        used: set[str],
        nullable: bool,
    ) -> None:
        element = self.at(pointer)
        base = clean_name(name)
        if isinstance(node, list):
            live = [(b, f"{pointer}/{i}") for i, b in enumerate(node) if b != "null"]
            has_null = len(live) != len(node)
            if len(live) == 1:
                self.typed(table, name, live[0][0], live[0][1], ns, used, has_null)
                return
            if not live:
                self.report.skipped(element, "field", "a null-only field has no generator")
                return
            self.report.skipped(
                element,
                "union",
                f"union with {len(live)} non-null branches: imported as string",
            )
            table.columns.append(
                ImpColumn(unique_name(base, used), "string", nullable=True, source=element)
            )
            return
        if isinstance(node, str):
            if node in _PRIMITIVE:
                col = ImpColumn(unique_name(base, used), _PRIMITIVE[node], nullable, source=element)
                table.columns.append(col)
                return
            if node in ("bytes", "null"):
                self._left_out(node, name, element)
                return
            found = self.lookup(node, ns, self.tables)
            if found is not None:
                self._reference(table, base, self.tables[found], used, nullable, element)
                return
            found = self.lookup(node, ns, self.enums)
            if found is not None:
                self._enum(table, base, self.enums[found], used, nullable, element)
                return
            if self.lookup(node, ns, dict.fromkeys(self.fixed)) is not None:
                self._left_out("fixed", name, element)
                return
            raise self.fail(f"unknown type {node!r}", pointer)
        if not isinstance(node, dict):
            raise self.fail("a type must be a name, a union or a definition", pointer)
        kind = node.get("type")
        if isinstance(kind, (dict, list)) or kind in _PRIMITIVE or kind == "bytes":
            self._logical(table, base, node, kind, pointer, used, nullable, ns)
        elif kind in ("record", "error"):
            child = self.record(node, pointer, ns)
            self._owner(table, child, element)
        elif kind == "enum":
            sname, full, _ = self.fullname(node, ns, pointer)
            symbols = node.get("symbols")
            if (
                not isinstance(symbols, list)
                or not symbols
                or not all(isinstance(x, str) for x in symbols)
            ):
                raise self.fail(f"enum {sname!r} needs a non-empty symbols array", pointer)
            self.enums[full] = list(symbols)
            self._enum(table, base, self.enums[full], used, nullable, element)
        elif kind == "fixed":
            _, full, _ = self.fullname(node, ns, pointer)
            self.fixed.add(full)
            self._left_out("fixed", name, element)
        elif kind == "array":
            self._array(table, base, node, pointer, ns, used)
        elif kind == "map":
            self._map(table, base, node, pointer, ns, used)
        else:
            raise self.fail(f"unknown type {kind!r}", pointer)

    def _left_out(self, kind: str, name: str, element: str) -> None:
        self.report.skipped(element, kind, f"{kind} column {name!r} has no generator: left out")

    def _logical(
        self,
        table: ImpTable,
        base: str,
        node: dict[str, Any],
        kind: Any,
        pointer: str,
        used: set[str],
        nullable: bool,
        ns: str,
    ) -> None:
        element = self.at(pointer)
        if isinstance(kind, (dict, list)):
            self.typed(table, base, kind, f"{pointer}/type", ns, used, nullable)
            return
        logical = node.get("logicalType")
        if logical == "decimal" and kind in ("bytes", "fixed"):
            precision, scale = node.get("precision"), node.get("scale", 0)
            if not isinstance(precision, int) or precision < 1:
                raise self.fail("a decimal needs a positive integer precision", pointer)
            if not isinstance(scale, int) or not 0 <= scale <= precision:
                raise self.fail("a decimal's scale must be between 0 and its precision", pointer)
            col = ImpColumn(
                unique_name(base, used),
                "decimal",
                nullable,
                precision=precision,
                scale=scale,
                source=element,
            )
            if precision - scale <= 15:
                col.minimum, col.maximum = 0.0, float(10 ** (precision - scale) - 1)
            table.columns.append(col)
            return
        if kind == "bytes":
            self._left_out("bytes", base, element)
            return
        mapped = _LOGICAL.get((str(kind), str(logical))) if logical else None
        if logical and mapped is None:
            self.report.skipped(
                element, "logicalType", f"logical type {logical!r} is not known: read as {kind}"
            )
        col = ImpColumn(
            unique_name(base, used), mapped or _PRIMITIVE[str(kind)], nullable, source=element
        )
        if mapped is None and kind == "string" and isinstance(node.get("maxLength"), int):
            col.max_length = node["maxLength"]
        table.columns.append(col)

    def _enum(
        self,
        table: ImpTable,
        base: str,
        symbols: list[str],
        used: set[str],
        nullable: bool,
        element: str,
    ) -> None:
        table.columns.append(
            ImpColumn(
                unique_name(base, used), "string", nullable, enum=list(symbols), source=element
            )
        )

    def _reference(
        self,
        table: ImpTable,
        base: str,
        target: ImpTable,
        used: set[str],
        nullable: bool,
        element: str,
    ) -> None:
        col = ImpColumn(
            unique_name(f"{base}_id", used), "integer", nullable, source=element, kind="reference"
        )
        table.columns.append(col)
        fk = ImpForeignKey(col.name, target.name, "")
        table.foreign_keys.append(fk)
        self._links.append((table, fk, target))

    def _owner(self, parent: ImpTable, child: ImpTable, element: str, shared: bool = False) -> None:
        names = {c.name for c in child.columns}
        col = ImpColumn(
            unique_name(f"{parent.name}_id", names),
            "integer",
            nullable=shared,
            source=element,
            kind="generated column",
        )
        child.columns.append(col)
        fk = ImpForeignKey(col.name, parent.name, "")
        child.foreign_keys.append(fk)
        self._links.append((child, fk, parent))

    def _is_record(self, node: Any) -> bool:
        return isinstance(node, dict) and node.get("type") in ("record", "error")

    def _named_record(self, node: Any, ns: str) -> ImpTable | None:
        if isinstance(node, str):
            found = self.lookup(node, ns, self.tables)
            if found is not None:
                return self.tables[found]
        return None

    def _value_table(
        self,
        table: ImpTable,
        base: str,
        items: Any,
        pointer: str,
        ns: str,
        element: str,
        keyed: bool,
    ) -> bool:
        """A child table holding the scalar items (or map values), linked to ``table``; ``False``
        when the items are not scalars (nothing is added)."""
        if isinstance(items, list) or (
            isinstance(items, dict) and items.get("type") in ("array", "map")
        ):
            return False
        child = ImpTable(
            unique_name(clean_name(f"{table.name}_{base}"), self._used), source=element
        )
        cols: set[str] = {"id"}
        if keyed:
            child.columns.append(
                ImpColumn(unique_name("key", cols), "string", False, max_length=20, source=element)
            )
        self.typed(child, "value", items, pointer, ns, cols, False)
        if child.column("value") is None:
            self._used.discard(child.name)
            return False
        self.model.tables.append(child)
        self._owner(table, child, element)
        return True

    def _array(
        self,
        table: ImpTable,
        base: str,
        node: dict[str, Any],
        pointer: str,
        ns: str,
        used: set[str],
    ) -> None:
        if "items" not in node:
            raise self.fail("an array needs items", pointer)
        items, ipointer, element = node["items"], f"{pointer}/items", self.at(pointer)
        if self._is_record(items):
            self._owner(table, self.record(items, ipointer, ns), element)
            return
        target = self._named_record(items, ns)
        if target is not None:
            self._owner(table, target, element, shared=True)
            return
        if self._value_table(table, base, items, ipointer, ns, element, False):
            return
        self.report.skipped(element, "array", "array of arrays, maps or unions: imported as string")
        table.columns.append(
            ImpColumn(unique_name(base, used), "string", nullable=True, source=element)
        )

    def _map(
        self,
        table: ImpTable,
        base: str,
        node: dict[str, Any],
        pointer: str,
        ns: str,
        used: set[str],
    ) -> None:
        if "values" not in node:
            raise self.fail("a map needs values", pointer)
        values, element = node["values"], self.at(pointer)
        if (
            not self._is_record(values)
            and self._named_record(values, ns) is None
            and self._value_table(table, base, values, f"{pointer}/values", ns, element, True)
        ):
            return
        self.report.skipped(
            element, "map", "map of records, arrays, maps or unions: imported as string"
        )
        table.columns.append(
            ImpColumn(unique_name(base, used), "string", nullable=True, source=element)
        )

    def top(self, node: Any, pointer: str) -> None:
        if isinstance(node, list):
            for i, n in enumerate(node):
                self.top(n, f"{pointer}/{i}")
            return
        if isinstance(node, dict) and node.get("type") in ("record", "error"):
            self.record(node, pointer, "")
        elif isinstance(node, dict) and node.get("type") == "enum":
            sname, full, _ = self.fullname(node, "", pointer)
            self.enums[full] = list(node.get("symbols") or [])
            self.report.skipped(
                self.at(pointer), "enum", f"enum {sname!r} is not a table: used where it is named"
            )
        else:
            raise self.fail("a top-level Avro schema must be a record (or a list of them)", pointer)


def import_avro(path: str, report: Report) -> ImpModel:
    doc = load_document(path)
    walker = _Walker(doc, report)
    walker.top(doc.data, "")
    if not walker.model.tables:
        raise ImportFormatError("the schema defines no record", file=path)
    return finish_model(walker.model, walker._links)
