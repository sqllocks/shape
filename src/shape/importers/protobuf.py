"""Protobuf ``proto3`` text files to the importer model (W5-06), parsed without ``protoc``.

A message is a table (a nested message is ``Outer_Inner``). A field of message type is a foreign
key column to that message's table; a ``repeated`` message field is a nullable foreign key column
on the message's table pointing back at its owner; a ``repeated`` scalar or enum is a child value
table and a ``map`` of scalars a child ``key``/``value`` table. An enum is a string column with
the enum's value names. ``google.protobuf.Timestamp`` is a timestamp, the wrapper types are
nullable scalars. ``import`` is followed only inside the directory of the input. Services,
``oneof`` exclusivity, ``bytes`` fields and the other well-known types are not imported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

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
from shape.importers.documents import read_text

_SCALARS = {
    "double": "float",
    "float": "float",
    "int32": "integer",
    "int64": "integer",
    "uint32": "integer",
    "uint64": "integer",
    "sint32": "integer",
    "sint64": "integer",
    "fixed32": "integer",
    "fixed64": "integer",
    "sfixed32": "integer",
    "sfixed64": "integer",
    "bool": "boolean",
    "string": "string",
}
_WRAPPERS = {
    "DoubleValue": "float",
    "FloatValue": "float",
    "Int64Value": "integer",
    "UInt64Value": "integer",
    "Int32Value": "integer",
    "UInt32Value": "integer",
    "BoolValue": "boolean",
    "StringValue": "string",
}
_NOT_IMPORTED_WKT = ("Duration", "Any", "Struct", "Value", "ListValue", "Empty", "FieldMask")
_TOKEN = re.compile(
    r"""(?P<ws>\s+)
    |(?P<line>//[^\n]*)
    |(?P<block>/\*.*?\*/)
    |(?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
    |(?P<num>-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|(?:inf|nan)(?![A-Za-z0-9_])))
    |(?P<id>\.?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
    |(?P<sym>[{}\[\]()<>=;,:])""",
    re.VERBOSE | re.DOTALL,
)


@dataclass
class _Tok:
    kind: str
    text: str
    line: int


@dataclass
class PField:
    name: str
    type: str
    number: int
    line: int
    label: str = ""
    key_type: str | None = None
    oneof: str | None = None


@dataclass
class PEnum:
    name: str
    full: str
    values: list[str]
    line: int


@dataclass
class PMessage:
    name: str
    full: str
    line: int
    fields: list[PField] = field(default_factory=list)
    nested: list[PMessage] = field(default_factory=list)
    enums: list[PEnum] = field(default_factory=list)
    parent: PMessage | None = None


@dataclass
class ProtoFile:
    path: str
    package: str = ""
    imports: list[tuple[str, int]] = field(default_factory=list)
    messages: list[PMessage] = field(default_factory=list)
    enums: list[PEnum] = field(default_factory=list)
    services: list[tuple[str, int]] = field(default_factory=list)


def _tokenize(text: str, path: str) -> list[_Tok]:
    out: list[_Tok] = []
    pos, line = 0, 1
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            if text.startswith("/*", pos):
                raise ImportFormatError("unterminated comment", file=path, line=line)
            raise ImportFormatError(f"unexpected character {text[pos]!r}", file=path, line=line)
        kind = str(m.lastgroup)
        chunk = m.group()
        if kind not in ("ws", "line", "block"):
            out.append(_Tok(kind, chunk, line))
        line += chunk.count("\n")
        pos = m.end()
    return out


class _Parser:
    def __init__(self, text: str, path: str) -> None:
        self.path = path
        self.toks = _tokenize(text, path)
        self.i = 0

    # ---- token helpers ----

    def fail(
        self, message: str, tok: _Tok | None = None, element: str | None = None
    ) -> ImportFormatError:
        t = tok or (self.toks[min(self.i, len(self.toks) - 1)] if self.toks else None)
        return ImportFormatError(message, file=self.path, line=t.line if t else 1, element=element)

    def peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> _Tok:
        t = self.peek()
        if t is None:
            raise self.fail("unexpected end of file", self.toks[-1] if self.toks else None)
        self.i += 1
        return t

    def expect(self, text: str) -> _Tok:
        t = self.next()
        if t.text != text:
            raise self.fail(f"expected {text!r}, found {t.text!r}", t)
        return t

    def accept(self, text: str) -> bool:
        t = self.peek()
        if t is not None and t.text == text and t.kind != "str":
            self.i += 1
            return True
        return False

    def ident(self, what: str) -> _Tok:
        t = self.next()
        if t.kind != "id" or t.text.startswith("."):
            raise self.fail(f"expected {what}, found {t.text!r}", t)
        return t

    def skip_statement(self) -> None:
        """Skip to the end of a statement: past ``;``, or past a balanced ``{ ... }``."""
        depth = 0
        while True:
            t = self.next()
            if t.kind == "sym" and t.text in "{[(":
                depth += 1
            elif t.kind == "sym" and t.text in "}])":
                depth -= 1
                if depth == 0 and t.text == "}":
                    return
            elif t.text == ";" and depth == 0 and t.kind == "sym":
                return

    # ---- grammar ----

    def parse(self) -> ProtoFile:
        proto = ProtoFile(self.path)
        first = self.peek()
        if first is None or first.text != "syntax":
            raise self.fail('a proto3 file starts with syntax = "proto3";', first)
        self.next()
        self.expect("=")
        value = self.next()
        if value.kind != "str" or value.text[1:-1] != "proto3":
            raise self.fail(f"only proto3 is supported, found syntax = {value.text}", value)
        self.expect(";")
        while self.peek() is not None:
            t = self.next()
            if t.text == ";":
                continue
            if t.text == "package":
                proto.package = self.ident("a package name").text
                self.expect(";")
            elif t.text == "import":
                if self.peek() is not None and self.peek().text in ("public", "weak"):  # type: ignore[union-attr]
                    self.next()
                name = self.next()
                if name.kind != "str":
                    raise self.fail("expected a file name in quotes", name)
                proto.imports.append((name.text[1:-1], name.line))
                self.expect(";")
            elif t.text == "option":
                self.skip_statement()
            elif t.text == "message":
                proto.messages.append(self.message(None, proto.package, t))
            elif t.text == "enum":
                proto.enums.append(self.enum(proto.package, t))
            elif t.text == "service":
                name = self.ident("a service name")
                proto.services.append((name.text, t.line))
                self.skip_to_block_end()
            elif t.text == "extend":
                raise self.fail("extend is a proto2 construct", t)
            else:
                raise self.fail(f"unexpected {t.text!r}", t)
        return proto

    def skip_to_block_end(self) -> None:
        while self.next().text != "{":
            pass
        depth = 1
        while depth:
            t = self.next()
            if t.kind == "sym" and t.text == "{":
                depth += 1
            elif t.kind == "sym" and t.text == "}":
                depth -= 1

    def enum(self, scope: str, start: _Tok) -> PEnum:
        name = self.ident("an enum name")
        full = f"{scope}.{name.text}" if scope else name.text
        self.expect("{")
        values: list[str] = []
        seen_numbers: set[int] = set()
        while not self.accept("}"):
            t = self.next()
            if t.text in ("option", "reserved"):
                self.skip_statement()
                continue
            if t.text == ";":
                continue
            if t.kind != "id":
                raise self.fail(f"expected an enum value, found {t.text!r}", t, full)
            self.expect("=")
            num = self.next()
            if num.kind != "num":
                raise self.fail("expected the enum value number", num, f"{full}.{t.text}")
            if not values and int(num.text, 0) != 0:
                raise self.fail(
                    "the first enum value must be zero in proto3", num, f"{full}.{t.text}"
                )
            seen_numbers.add(int(num.text, 0))
            values.append(t.text)
            if self.accept("["):
                while not self.accept("]"):
                    self.next()
            self.expect(";")
        if not values:
            raise self.fail(f"enum {name.text!r} has no values", name, full)
        return PEnum(name.text, full, values, start.line)

    def message(self, parent: PMessage | None, scope: str, start: _Tok) -> PMessage:
        name = self.ident("a message name")
        full = f"{scope}.{name.text}" if scope else name.text
        msg = PMessage(name.text, full, start.line, parent=parent)
        self.expect("{")
        numbers: dict[int, str] = {}
        while not self.accept("}"):
            t = self.next()
            if t.text == ";":
                continue
            if t.text in ("option", "reserved", "extensions"):
                self.skip_statement()
            elif t.text == "message":
                msg.nested.append(self.message(msg, full, t))
            elif t.text == "enum":
                msg.enums.append(self.enum(full, t))
            elif t.text == "oneof":
                group = self.ident("a oneof name").text
                self.expect("{")
                while not self.accept("}"):
                    if self.accept("option"):
                        self.skip_statement()
                        continue
                    self.field(msg, self.next(), numbers, oneof=group)
            else:
                self.field(msg, t, numbers)
        return msg

    def field(
        self, msg: PMessage, first: _Tok, numbers: dict[int, str], oneof: str | None = None
    ) -> None:
        label = ""
        t = first
        if t.text in ("repeated", "optional", "required") and t.kind == "id":
            if t.text == "required":
                raise self.fail("required is a proto2 construct", t, msg.full)
            label = t.text
            t = self.next()
        key_type: str | None = None
        if t.text == "map" and self.peek() is not None and self.peek().text == "<":  # type: ignore[union-attr]
            self.next()
            key_type = self.ident("a map key type").text
            self.expect(",")
            type_name = self.next()
            if type_name.kind != "id":
                raise self.fail("expected a map value type", type_name, msg.full)
            self.expect(">")
            ftype = type_name.text
        elif t.kind == "id":
            ftype = t.text
        else:
            raise self.fail(f"expected a field, found {t.text!r}", t, msg.full)
        name = self.ident("a field name")
        self.expect("=")
        num = self.next()
        if num.kind != "num" or not num.text.lstrip("-").isdigit():
            raise self.fail("expected the field number", num, f"{msg.full}.{name.text}")
        number = int(num.text)
        if number < 1 or number > 536870911 or 19000 <= number <= 19999:
            raise self.fail(f"field number {number} is not allowed", num, f"{msg.full}.{name.text}")
        if number in numbers:
            raise self.fail(
                f"field number {number} is used by {numbers[number]!r} and {name.text!r}",
                num,
                f"{msg.full}.{name.text}",
            )
        numbers[number] = name.text
        if self.accept("["):
            while not self.accept("]"):
                self.next()
        self.expect(";")
        msg.fields.append(PField(name.text, ftype, number, name.line, label, key_type, oneof))


def parse_proto(text: str, path: str) -> ProtoFile:
    """The messages and enums of a ``proto3`` file; :class:`ImportFormatError` with the line for
    anything malformed."""
    return _Parser(text, path).parse()


# ---- the importer ----------------------------------------------------------------------------


def _all_messages(messages: list[PMessage]) -> list[PMessage]:
    out: list[PMessage] = []
    for m in messages:
        out.append(m)
        out += _all_messages(m.nested)
    return out


def _all_enums(messages: list[PMessage], top: list[PEnum]) -> list[PEnum]:
    return top + [e for m in _all_messages(messages) for e in m.enums]


class _Importer:
    def __init__(self, path: str, report: Report) -> None:
        self.path = path
        self.report = report
        self.model = ImpModel(Path(path).stem)
        self.messages: dict[str, PMessage] = {}
        self.enums: dict[str, PEnum] = {}
        self.tables: dict[str, ImpTable] = {}
        self._used: set[str] = set()
        self._links: list[tuple[ImpTable, ImpForeignKey, ImpTable]] = []
        self._loaded: set[Path] = set()
        self._missing: set[str] = set()

    def register(self, proto: ProtoFile) -> None:
        for m in _all_messages(proto.messages):
            self.messages[m.full] = m
        for e in _all_enums(proto.messages, proto.enums):
            self.enums[e.full] = e

    def load_imports(self, proto: ProtoFile, directory: Path) -> None:
        for name, _line in proto.imports:
            element = f"import {name}"
            if name.startswith("google/protobuf/"):
                self.report.mapped(element, "import", "well-known types are built in")
                continue
            target = directory / name
            safe = not Path(name).is_absolute() and ".." not in Path(name).parts
            if not safe or not target.is_file():
                self._missing.add(Path(name).stem)
                self.report.skipped(
                    element,
                    "import",
                    "not found in the directory of the input (imports are not followed "
                    "elsewhere): types from it are imported as string",
                )
                continue
            resolved = target.resolve()
            if resolved in self._loaded:
                continue
            self._loaded.add(resolved)
            sub = parse_proto(read_text(target), str(target))
            self.register(sub)
            self.report.mapped(element, "import", "read for the types it defines")
            self.load_imports(sub, target.parent)

    def resolve(self, name: str, scope: PMessage | None, package: str) -> tuple[str, object] | None:
        if name in _SCALARS:
            return "scalar", _SCALARS[name]
        if name == "bytes":
            return "bytes", None
        if name.startswith("."):
            candidates = [name[1:]]
        else:
            scopes: list[str] = []
            cur = scope
            while cur is not None:
                scopes.append(cur.full)
                cur = cur.parent
            if package:
                scopes.append(package)
            scopes.append("")
            candidates = [f"{s}.{name}" if s else name for s in scopes]
        for c in candidates:
            if c in self.messages:
                return "message", self.messages[c]
            if c in self.enums:
                return "enum", self.enums[c]
        bare = name.removeprefix(".").removeprefix("google.protobuf.")
        if bare == "Timestamp":
            return "scalar", "timestamp"
        if bare in _WRAPPERS:
            return "wrapper", _WRAPPERS[bare]
        if bare in _NOT_IMPORTED_WKT:
            return "wkt", bare
        return None

    def table_for(self, msg: PMessage, package: str) -> ImpTable:
        found = self.tables.get(msg.full)
        if found is not None:
            return found
        rel = msg.full.removeprefix(f"{package}.") if package else msg.full
        table = ImpTable(
            unique_name(clean_name(rel.replace(".", "_")), self._used), source=f"message {msg.full}"
        )
        self.tables[msg.full] = table
        self.model.tables.append(table)
        used: set[str] = set()
        for f in msg.fields:
            self.field(table, msg, f, used, package)
        return table

    def field(
        self, table: ImpTable, msg: PMessage, f: PField, used: set[str], package: str
    ) -> None:
        element = f"{msg.full}.{f.name}"
        base = clean_name(f.name)
        nullable = f.label == "optional" or f.oneof is not None
        if f.oneof is not None:
            self.report.skipped(
                element,
                "oneof",
                f"member of oneof {f.oneof!r}: imported as a nullable column, "
                "that only one member is set is not enforced",
            )
        if f.key_type is not None:
            self.map_field(table, msg, f, used, package, element)
            return
        resolved = self.resolve(f.type, msg, package)
        if resolved is None:
            self.unknown(table, f, used, element, base)
            return
        kind, target = resolved
        repeated = f.label == "repeated"
        if kind in ("bytes", "wkt"):
            what = (
                "bytes fields have no generator" if kind == "bytes" else f"{target} is not imported"
            )
            self.report.skipped(element, "field", f"{what}: field left out")
            return
        if kind == "message":
            assert isinstance(target, PMessage)
            child = self.table_for(target, package)
            if repeated:
                self.owner_link(table, child, element, shared=True)
            else:
                col = ImpColumn(
                    unique_name(f"{base}_id", used),
                    "integer",
                    True,
                    source=element,
                    kind="reference",
                )
                table.columns.append(col)
                fk = ImpForeignKey(col.name, child.name, "")
                table.foreign_keys.append(fk)
                self._links.append((table, fk, child))
            return
        col = self.scalar_column(base, kind, target, nullable or kind == "wrapper", element)
        if repeated:
            self.value_table(table, f.name, col, element)
        else:
            col.name = unique_name(base, used)
            table.columns.append(col)

    def scalar_column(
        self, name: str, kind: str, target: object, nullable: bool, element: str
    ) -> ImpColumn:
        if kind == "enum":
            assert isinstance(target, PEnum)
            return ImpColumn(name, "string", nullable, enum=list(target.values), source=element)
        return ImpColumn(name, str(target), nullable, source=element)

    def value_table(self, owner: ImpTable, fname: str, value: ImpColumn, element: str) -> None:
        child = ImpTable(
            unique_name(clean_name(f"{owner.name}_{fname}"), self._used), source=element
        )
        value.name, value.nullable = "value", False
        child.columns.append(value)
        self.model.tables.append(child)
        self.owner_link(owner, child, element)

    def map_field(
        self, table: ImpTable, msg: PMessage, f: PField, used: set[str], package: str, element: str
    ) -> None:
        resolved = self.resolve(f.type, msg, package)
        key = _SCALARS.get(f.key_type or "")
        if (
            key not in ("integer", "string", "boolean")
            or resolved is None
            or resolved[0]
            not in (
                "scalar",
                "enum",
            )
        ):
            self.report.skipped(
                element, "map", "map with a message, bytes or unusual value type: field left out"
            )
            return
        child = ImpTable(
            unique_name(clean_name(f"{table.name}_{f.name}"), self._used), source=element
        )
        keycol = ImpColumn(
            "key", key, False, max_length=20 if key == "string" else None, source=element
        )
        child.columns.append(keycol)
        value = self.scalar_column("value", resolved[0], resolved[1], False, element)
        child.columns.append(value)
        self.model.tables.append(child)
        self.owner_link(table, child, element)

    def unknown(self, table: ImpTable, f: PField, used: set[str], element: str, base: str) -> None:
        if not self._missing:
            raise ImportFormatError(
                f"type {f.type!r} is not defined in the input or its imports",
                file=self.path,
                line=f.line,
                element=element,
            )
        self.report.skipped(
            element,
            "type",
            f"type {f.type!r} is not defined; it may be in a file that was not found "
            f"({', '.join(sorted(self._missing))}): imported as string",
        )
        table.columns.append(
            ImpColumn(unique_name(base, used), "string", nullable=True, source=element)
        )

    def owner_link(
        self, parent: ImpTable, child: ImpTable, element: str, shared: bool = False
    ) -> None:
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


def import_protobuf(path: str, report: Report) -> ImpModel:
    text = read_text(path)
    proto = parse_proto(text, path)
    importer = _Importer(path, report)
    importer.register(proto)
    importer.load_imports(proto, Path(path).resolve().parent)
    for m in _all_messages(proto.messages):
        importer.table_for(m, proto.package)
    for name, line in proto.services:
        report.skipped(f"service {name}", "service", f"services are not imported (line {line})")
    if not importer.model.tables:
        raise ImportFormatError("the file defines no message", file=path)
    return finish_model(importer.model, importer._links)
