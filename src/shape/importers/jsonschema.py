"""JSON Schema (draft 2020-12 and draft 7) to the importer model (W5-06).

The :class:`SchemaWalker` is shared with the OpenAPI importer. Rules:

* an object schema is a table; a nested object (inline) is a child table with a generated foreign
  key to its parent; an array of objects is a child table the same way;
* an object that is a ``$ref`` to a named schema is that schema's table, once: a property that
  points at it becomes a foreign key column, and an array of it a nullable foreign key column on
  the referenced table;
* an array of scalars is a child value table (``id``, the parent key, ``value``);
* ``allOf`` merges its object branches; ``oneOf`` and ``anyOf`` with one non-null branch are that
  branch (nullable), with more it is a string and says so in the report.

Only ``$ref`` inside the document is followed; every other kind of reference is reported.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from shape.importers.core import (
    FORMAT_TYPES,
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
from shape.importers.documents import Document

_MAX_REF_DEPTH = 32
_NUMERIC_TYPES = {"integer": "integer", "number": "float", "boolean": "boolean", "string": "string"}
# Keywords that carry no structure for a generator: annotations and the like.
_SILENT = frozenset(
    {
        "$schema", "$id", "$anchor", "$comment", "$defs", "definitions", "title", "description",
        "default", "examples", "example", "deprecated", "readOnly", "writeOnly", "externalDocs",
        "xml", "required", "properties", "additionalProperties", "type", "format", "enum", "const",
        "pattern", "minimum", "maximum", "maxLength", "items", "allOf", "oneOf", "anyOf", "$ref",
        "nullable", "exclusiveMinimum", "exclusiveMaximum",
    }
)  # fmt: skip
# Keywords with an effect on data that no generator reads: reported when present.
_UNUSED = (
    "multipleOf", "minLength", "minItems", "maxItems", "uniqueItems", "contains", "minProperties",
    "maxProperties", "patternProperties", "propertyNames", "not", "if", "then", "else",
    "dependentRequired", "dependentSchemas", "dependencies", "unevaluatedProperties",
    "unevaluatedItems", "prefixItems", "discriminator", "contentEncoding", "contentMediaType",
)  # fmt: skip


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class SchemaWalker:
    """Walks schemas of one document into an :class:`ImpModel`."""

    def __init__(self, doc: Document, report: Report, root_name: str) -> None:
        self.doc = doc
        self.report = report
        self.model = ImpModel(root_name)
        self.named: dict[str, ImpTable] = {}
        self._used_tables: set[str] = set()
        self._owner_fks: list[tuple[ImpTable, ImpForeignKey, ImpTable]] = []

    # ---- addressing ----

    def at(self, pointer: str) -> str:
        return "#" + pointer

    def fail(self, message: str, pointer: str) -> ImportFormatError:
        return ImportFormatError(
            message, file=self.doc.path, line=self.doc.line(pointer), element=self.at(pointer)
        )

    def lookup(self, pointer: str) -> Any:
        node: Any = self.doc.data
        for token in (_unescape(t) for t in pointer.split("/")[1:]):
            if isinstance(node, dict) and token in node:
                node = node[token]
            elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
                node = node[int(token)]
            else:
                raise KeyError(pointer)
        return node

    def deref(self, schema: Any, pointer: str) -> tuple[Any, str, bool]:
        """Follow ``$ref`` links inside the document: the schema, its pointer, and whether a
        reference was followed. A reference that leaves the document returns ``(None, ...)``."""
        via = False
        for _ in range(_MAX_REF_DEPTH):
            if not isinstance(schema, dict) or "$ref" not in schema:
                return schema, pointer, via
            ref = schema["$ref"]
            if not isinstance(ref, str):
                raise self.fail("$ref must be a string", pointer + "/$ref")
            if ref != "#" and not ref.startswith("#/"):
                return None, pointer, via
            target = "" if ref == "#" else ref[1:]
            try:
                schema = self.lookup(target)
            except KeyError:
                raise self.fail(f"$ref {ref!r} does not exist in the document", pointer) from None
            pointer, via = target, True
        raise self.fail("$ref chain is circular", pointer)

    def ref_target(self, schema: Any, pointer: str) -> tuple[str, bool]:
        """The pointer of the named schema ``schema`` refers to, with whether it is one: a
        ``$ref``, or a ``oneOf``/``anyOf`` whose only non-null branch is a ``$ref``."""
        resolved, target, via = self.deref(schema, pointer)
        if via:
            return target, True
        if isinstance(resolved, dict):
            for key in ("oneOf", "anyOf"):
                branches = resolved.get(key)
                if not isinstance(branches, list):
                    continue
                live = []
                for i, b in enumerate(branches):
                    bres, _, _ = self.deref(b, f"{pointer}/{key}/{i}")
                    if not (isinstance(bres, dict) and bres.get("type") == "null"):
                        live.append((b, f"{pointer}/{key}/{i}"))
                if len(live) == 1:
                    return self.ref_target(*live[0])
        return pointer, False

    # ---- the effective schema ----

    def effective(self, schema: Any, pointer: str, what: str) -> tuple[dict[str, Any] | None, bool]:
        """The schema with ``$ref`` followed, ``allOf`` merged and a single non-null ``oneOf`` or
        ``anyOf`` branch taken; ``(None, nullable)`` when it cannot be reduced to one schema (the
        reason is in the report). The flag says the schema allows ``null``."""
        resolved, target, _ = self.deref(schema, pointer)
        if resolved is None:
            self.report.skipped(
                self.at(pointer),
                "$ref",
                f"reference {schema.get('$ref')!r} leaves the document: imported as string",
            )
            return None, False
        if not isinstance(resolved, dict):
            if resolved is True or resolved is False:
                return {}, False
            raise self.fail("a schema must be an object", target)
        nullable = bool(resolved.get("nullable"))
        merged = dict(resolved)
        for branch_key in ("oneOf", "anyOf"):
            branches = merged.get(branch_key)
            if not isinstance(branches, list):
                continue
            live: list[tuple[Any, str]] = []
            for i, b in enumerate(branches):
                bp = f"{target}/{branch_key}/{i}"
                bres, _, _ = self.deref(b, bp)
                if isinstance(bres, dict) and bres.get("type") == "null":
                    nullable = True
                else:
                    live.append((b, bp))
            if len(live) == 1:
                inner, inner_null = self.effective(live[0][0], live[0][1], what)
                merged.pop(branch_key)
                if inner is None:
                    return None, nullable
                merged = {**merged, **inner}
                nullable = nullable or inner_null
            else:
                self.report.skipped(
                    self.at(pointer),
                    what,
                    f"{branch_key} with {len(live)} non-null branches: imported as string",
                )
                return None, nullable
        all_of = merged.pop("allOf", None)
        if isinstance(all_of, list):
            props: dict[str, Any] = {}
            required: list[str] = list(merged.get("required", []))
            props.update(merged.get("properties", {}))
            base: dict[str, Any] = {}
            for i, b in enumerate(all_of):
                part, part_null = self.effective(b, f"{target}/allOf/{i}", what)
                if part is None:
                    return None, nullable
                nullable = nullable or part_null
                props.update(part.get("properties", {}))
                required += [r for r in part.get("required", []) if r not in required]
                base.update({k: v for k, v in part.items() if k not in ("properties", "required")})
            merged = {**base, **merged}
            if props:
                merged["properties"] = props
            if required:
                merged["required"] = required
            self.report.mapped(self.at(pointer), "allOf", "branches merged into one schema")
        return merged, nullable

    @staticmethod
    def kind(schema: dict[str, Any]) -> str:
        t = schema.get("type")
        types = [x for x in t if x != "null"] if isinstance(t, list) else [t]
        if "object" in types or (t is None and "properties" in schema):
            return "object"
        if "array" in types:
            return "array"
        return "scalar"

    def _scalar(
        self, schema: dict[str, Any], pointer: str, name: str, nullable: bool
    ) -> tuple[ImpColumn, bool]:
        element = self.at(pointer)
        t = schema.get("type")
        types = [x for x in t if x != "null"] if isinstance(t, list) else ([t] if t else [])
        if isinstance(t, list) and "null" in t:
            nullable = True
        col = ImpColumn(name, "string", source=element)
        if len(types) > 1:
            self.report.skipped(
                element, "type", f"type {types!r} has several non-null types: imported as string"
            )
        elif types:
            if types[0] not in _NUMERIC_TYPES:
                self.report.skipped(element, "type", f"type {types[0]!r} is not known: string")
            else:
                col.type = _NUMERIC_TYPES[types[0]]
        col.max_length = (
            schema.get("maxLength") if isinstance(schema.get("maxLength"), int) else None
        )
        if col.max_length is not None and col.max_length < 1:
            raise self.fail("maxLength must be at least 1", pointer)
        fmt = schema.get("format")
        if isinstance(fmt, str):
            col.format = fmt
            if col.type == "string":
                if fmt in FORMAT_TYPES:
                    col.type = FORMAT_TYPES[fmt]
        if isinstance(schema.get("pattern"), str):
            col.pattern = schema["pattern"]
            try:
                re.compile(col.pattern)
            except re.error as exc:
                raise self.fail(f"pattern is not a regular expression ({exc})", pointer) from exc
        col.minimum = _number(schema.get("minimum"))
        col.maximum = _number(schema.get("maximum"))
        for key, attr, step in (
            ("exclusiveMinimum", "minimum", 1),
            ("exclusiveMaximum", "maximum", -1),
        ):
            bound = _number(schema.get(key))
            if bound is None:
                if schema.get(key) is True:
                    self.report.skipped(element, key, f"{key} true (draft 4) is read as inclusive")
                continue
            if col.type == "integer":
                setattr(col, attr, bound + step)
            else:
                setattr(col, attr, bound)
                self.report.skipped(
                    element, key, f"{key} {bound:g} is imported as an inclusive bound"
                )
        if "const" in schema:
            col.constant, col.has_constant = schema["const"], True
            if col.type == "string" and not types and not isinstance(schema["const"], str):
                col.type = _infer_type([schema["const"]])
        if "enum" in schema:
            values = schema["enum"]
            if not isinstance(values, list) or not values:
                raise self.fail("enum must be a non-empty array", pointer + "/enum")
            if None in values:
                nullable = True
                values = [v for v in values if v is not None]
            if not values:
                pass
            elif any(isinstance(v, (dict, list)) for v in values):
                self.report.skipped(element, "enum", "enum has non-scalar values: not used")
            else:
                if not types:
                    col.type = _infer_type(values)
                if col.type == "string":
                    values = [v if isinstance(v, str) else _text(v) for v in values]
                col.enum = list(dict.fromkeys(values))
        col.nullable = nullable
        return col, nullable

    def _unused(self, schema: dict[str, Any], pointer: str) -> None:
        for key in _UNUSED:
            if key in schema:
                self.report.skipped(self.at(pointer), key, f"{key} is not used by a generator")

    # ---- tables ----

    def _new_table(self, base: str, pointer: str) -> ImpTable:
        table = ImpTable(unique_name(clean_name(base), self._used_tables), source=self.at(pointer))
        self.model.tables.append(table)
        return table

    def named_table(self, pointer: str, name: str, schema: dict[str, Any]) -> ImpTable:
        """The table of a named object schema (built once; recursion points back at it)."""
        found = self.named.get(pointer)
        if found is not None:
            return found
        table = self._new_table(name, pointer)
        self.named[pointer] = table
        self._fill(table, schema, pointer)
        return table

    def object_table(self, name: str, schema: dict[str, Any], pointer: str) -> ImpTable:
        table = self._new_table(name, pointer)
        self._fill(table, schema, pointer)
        return table

    def _fill(self, table: ImpTable, schema: dict[str, Any], pointer: str) -> None:
        eff, _ = self.effective(schema, pointer, "schema")
        if eff is None:
            return
        self._unused(eff, pointer)
        props = eff.get("properties") or {}
        if not isinstance(props, dict):
            raise self.fail("properties must be an object", pointer + "/properties")
        required = eff.get("required", [])
        if not isinstance(required, list):
            raise self.fail("required must be an array", pointer + "/required")
        used_cols = {table_col.name for table_col in table.columns}
        for pname, pschema in props.items():
            self._property(table, str(pname), pschema, pointer, pname in required, used_cols)

    def _property(
        self,
        table: ImpTable,
        pname: str,
        pschema: Any,
        owner_pointer: str,
        required: bool,
        used_cols: set[str],
    ) -> None:
        ptr = f"{owner_pointer}/properties/{_escape(pname)}"
        element = self.at(ptr)
        base = clean_name(pname)
        eff, nullable = self.effective(pschema, ptr, "property")
        if eff is None:
            table.columns.append(
                ImpColumn(unique_name(base, used_cols), "string", nullable=True, source=element)
            )
            return
        self._unused(eff, ptr)
        nullable = nullable or not required
        kind = self.kind(eff)
        target, via_ref = self.ref_target(pschema, ptr)
        if kind == "object":
            if not eff.get("properties"):
                self.report.skipped(
                    element, "object", "object without properties: imported as string"
                )
                table.columns.append(
                    ImpColumn(unique_name(base, used_cols), "string", nullable=True, source=element)
                )
            elif via_ref:
                child = self.named_table(target, target.rsplit("/", 1)[-1], eff)
                col = ImpColumn(
                    unique_name(f"{base}_id", used_cols),
                    "integer",
                    nullable,
                    source=element,
                    kind="reference",
                )
                table.columns.append(col)
                fk = ImpForeignKey(col.name, child.name, "")
                table.foreign_keys.append(fk)
                self._owner_fks.append((table, fk, child))
            else:
                child = self.object_table(f"{table.name}_{pname}", eff, ptr)
                self._owner_link(table, child, element)
        elif kind == "array":
            self._array(table, base, pname, eff, ptr, element, used_cols)
        else:
            col, _ = self._scalar(eff, ptr, unique_name(base, used_cols), nullable)
            table.columns.append(col)

    def _array(
        self,
        table: ImpTable,
        cname: str,
        pname: str,
        eff: dict[str, Any],
        ptr: str,
        element: str,
        used_cols: set[str],
    ) -> None:
        items = eff.get("items")
        if items is None or isinstance(items, list):
            why = "tuple-form items" if isinstance(items, list) else "array without items"
            self.report.skipped(element, "array", f"{why}: imported as string")
            table.columns.append(
                ImpColumn(unique_name(cname, used_cols), "string", nullable=True, source=element)
            )
            return
        ieff, _inull = self.effective(items, f"{ptr}/items", "items")
        if ieff is None:
            table.columns.append(
                ImpColumn(unique_name(cname, used_cols), "string", nullable=True, source=element)
            )
            return
        self._unused(ieff, f"{ptr}/items")
        ikind = self.kind(ieff)
        itarget, ivia = self.ref_target(items, f"{ptr}/items")
        if ikind == "object" and ieff.get("properties"):
            if ivia:
                child = self.named_table(itarget, itarget.rsplit("/", 1)[-1], ieff)
            else:
                child = self.object_table(f"{table.name}_{pname}", ieff, f"{ptr}/items")
            self._owner_link(table, child, element)
        elif ikind == "scalar":
            child = self._new_table(f"{table.name}_{pname}", ptr)
            child_cols = {"id"}
            value, _ = self._scalar(ieff, f"{ptr}/items", "value", False)
            value.name = unique_name("value", child_cols)
            child.columns.append(value)
            self._owner_link(table, child, element)
        else:
            self.report.skipped(
                element,
                "array",
                f"array of {ikind}s is not imported as a table: imported as string",
            )
            table.columns.append(
                ImpColumn(unique_name(cname, used_cols), "string", nullable=True, source=element)
            )

    def _owner_link(self, parent: ImpTable, child: ImpTable, element: str) -> None:
        """A foreign key column on ``child`` to ``parent`` (nullable when ``child`` is a shared
        named table that other owners also use)."""
        names = {c.name for c in child.columns}
        col = ImpColumn(
            unique_name(f"{parent.name}_id", names),
            "integer",
            nullable=child in self.named.values(),
            source=element,
            kind="generated column",
        )
        child.columns.append(col)
        fk = ImpForeignKey(col.name, parent.name, "")
        child.foreign_keys.append(fk)
        self._owner_fks.append((child, fk, parent))

    def finish(self) -> ImpModel:
        """Choose each table's key and point every generated foreign key at it."""
        return finish_model(self.model, self._owner_fks)


def _text(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _infer_type(values: Iterable[Any]) -> str:
    vals = list(values)
    if all(isinstance(v, bool) for v in vals):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return "integer"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "float"
    return "string"


def root_name(path: str) -> str:
    """The name a file gives its root table: ``order.schema.json`` is ``order``."""
    name = Path(path).name
    while True:
        stem, ext = Path(name).stem, Path(name).suffix.lower()
        if ext in (".json", ".yaml", ".yml", ".schema") and stem:
            name = stem
        else:
            return name


def import_jsonschema(doc: Document, report: Report) -> ImpModel:
    """Read a JSON Schema document (draft 2020-12 or draft 7)."""
    data = doc.data
    if not isinstance(data, dict):
        raise ImportFormatError("a JSON Schema must be an object", file=doc.path, line=1)
    dialect = data.get("$schema")
    if isinstance(dialect, str):
        report.mapped("#/$schema", "draft", dialect)
    walker = SchemaWalker(doc, report, root_name(doc.path))
    defs_key = "$defs" if "$defs" in data else ("definitions" if "definitions" in data else None)
    resolved, top_ptr, _ = walker.deref(data, "")
    top: Any = resolved
    if isinstance(resolved, dict) and walker.kind(resolved) == "array":
        items = resolved.get("items")
        if isinstance(items, dict):
            top, top_ptr, _ = walker.deref(items, top_ptr + "/items")
    eff, _ = walker.effective(top, top_ptr, "schema")
    base = str(data.get("title") or root_name(doc.path))
    if eff is not None and walker.kind(eff) == "object" and eff.get("properties"):
        walker.named_table(top_ptr, base, top)
    elif defs_key is None:
        raise walker.fail("the schema is not an object with properties and has no definitions", "")
    else:
        for name, sub in data[defs_key].items():
            ptr = f"/{defs_key}/{_escape(str(name))}"
            seff, _ = walker.effective(sub, ptr, "schema")
            if seff is not None and walker.kind(seff) == "object" and seff.get("properties"):
                walker.named_table(ptr, str(name), sub)
    if defs_key is not None and isinstance(data[defs_key], dict):
        for name in data[defs_key]:
            ptr = f"/{defs_key}/{_escape(str(name))}"
            if ptr not in walker.named:
                sub = data[defs_key][name]
                seff, _ = walker.effective(sub, ptr, "schema")
                if seff is not None and walker.kind(seff) == "object":
                    walker.report.skipped(
                        walker.at(ptr), "definition", "not referenced from the root schema"
                    )
    if not walker.model.tables:
        raise walker.fail("no object schema with properties to import", "")
    return walker.finish()
