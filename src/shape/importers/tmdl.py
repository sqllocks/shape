"""TMDL (the folder format of a Power BI semantic model) to the importer model (W5-06).

Reads a ``.tmdl`` file, a folder of them, or a Power BI project folder with a ``definition/``
folder in it. Tables become tables, columns with a ``dataType`` become typed columns, and each
relationship becomes a foreign key from its many side to its one side (the ``fromCardinality`` and
``toCardinality`` decide which side that is; many-to-many and one-to-one keep their type). A
table's key is the column its relationships point at, else its ``isKey`` column, else a generated
one. Measures, calculated columns, hierarchies, partitions and the other model objects (roles,
perspectives, cultures, expressions) are not imported and are listed in the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shape.importers.core import (
    ImpColumn,
    ImpForeignKey,
    ImpModel,
    ImportFormatError,
    ImpTable,
    Report,
    choose_key,
    clean_name,
    unique_name,
)
from shape.importers.documents import read_text

_TYPES = {
    "int64": "integer",
    "double": "float",
    "decimal": "decimal",
    "string": "string",
    "boolean": "boolean",
}
_DATE_FORMATS = ("short date", "long date", "medium date")


@dataclass
class Node:
    """One TMDL object, property or flag."""

    keyword: str
    name: str = ""
    expr: str | None = None
    value: str | None = None  # a property's value
    line: int = 0
    file: str = ""
    children: list[Node] = field(default_factory=list)

    def prop(self, key: str) -> str | None:
        for c in self.children:
            if c.keyword == key and c.value is not None:
                return c.value
        return None

    def has_flag(self, key: str) -> bool:
        return any(c.keyword == key and c.value is None and c.expr is None for c in self.children)

    def of(self, keyword: str) -> list[Node]:
        return [c for c in self.children if c.keyword == keyword and c.value is None]


# ---- reading names ---------------------------------------------------------------------------


def read_name(text: str, file: str, line: int, stop: str = "") -> tuple[str, str]:
    """A TMDL name at the start of ``text`` (plain, or in single quotes with ``''`` for a quote),
    and the rest after it. A plain name ends at white space, ``=`` or a character of ``stop``."""
    text = text.lstrip()
    if text.startswith("'"):
        out: list[str] = []
        i = 1
        while i < len(text):
            ch = text[i]
            if ch == "'":
                if text[i + 1 : i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), text[i + 1 :]
            out.append(ch)
            i += 1
        raise ImportFormatError("unterminated quote in a name", file=file, line=line)
    i = 0
    while i < len(text) and not text[i].isspace() and text[i] != "=" and text[i] not in stop:
        i += 1
    return text[:i], text[i:]


def split_ref(text: str, file: str, line: int, element: str) -> tuple[str, str]:
    """``Table.Column`` (either part may be quoted) to ``(table, column)``."""
    table, rest = read_name(text, file, line, stop=".")
    if not rest.startswith("."):
        raise ImportFormatError(
            f"{text.strip()!r} is not Table.Column", file=file, line=line, element=element
        )
    column, tail = read_name(rest[1:], file, line)
    if not column or tail.strip():
        raise ImportFormatError(
            f"{text.strip()!r} is not Table.Column", file=file, line=line, element=element
        )
    return table, column


# ---- the line parser -------------------------------------------------------------------------


@dataclass
class _Line:
    level: int
    text: str
    number: int


def _lines(text: str, file: str) -> list[_Line]:
    out: list[_Line] = []
    unit = 0
    for number, raw in enumerate(text.splitlines(), 1):
        body = raw.lstrip(" \t")
        if not body or body.startswith("///"):
            continue
        indent = raw[: len(raw) - len(body)]
        if indent.startswith("\t"):
            # Tabs are the indentation; spaces after them belong to the text (M code nests that
            # way), so they are not an error.
            level = len(indent) - len(indent.lstrip("\t"))
            body = indent[level:] + body
        elif "\t" in indent:
            raise ImportFormatError("tabs and spaces mixed in indentation", file=file, line=number)
        elif indent:
            unit = unit or len(indent)
            if len(indent) % unit:
                raise ImportFormatError(
                    f"indentation of {len(indent)} spaces is not a multiple of {unit}",
                    file=file,
                    line=number,
                )
            level = len(indent) // unit
        else:
            level = 0
        out.append(_Line(level, body.rstrip(), number))
    return out


def _is_property(text: str) -> bool:
    word = text.split(":", 1)[0]
    return ":" in text and word.isidentifier() and (text[len(word) : len(word) + 1] == ":")


class _Parser:
    def __init__(self, text: str, file: str) -> None:
        self.file = file
        self.lines = _lines(text, file)
        self.i = 0

    def parse(self) -> list[Node]:
        nodes: list[Node] = []
        while self.i < len(self.lines):
            first = self.lines[self.i]
            if first.level != 0:
                raise ImportFormatError("unexpected indentation", file=self.file, line=first.number)
            nodes.append(self.node(0))
        return nodes

    def node(self, level: int) -> Node:
        line = self.lines[self.i]
        self.i += 1
        text = line.text
        node = Node("", line=line.number, file=self.file)
        word, rest = read_name(text, self.file, line.number, stop=":")
        expression_level = level + 2
        if _is_property(text):
            node.keyword, node.value = word, rest[1:].strip()
            expression_level = level + 1
            self._expression(node, level, expression_level, "")
            return node
        node.keyword = word
        rest = rest.strip()
        if rest.startswith("="):
            node.expr = rest[1:].strip()
            expression_level = level + 1
        elif rest:
            if word == "ref":
                kind, after = read_name(rest, self.file, line.number)
                name, after = read_name(after, self.file, line.number)
                node.keyword, node.name = f"ref {kind}", name
                return node
            name, after = read_name(rest, self.file, line.number)
            node.name = name
            after = after.strip()
            if after.startswith("="):
                node.expr = after[1:].strip()
            elif after:
                node.value = after  # e.g. ``partition P = m`` is handled above; keep the tail
        self._expression(node, level, expression_level, node.expr or "")
        while self.i < len(self.lines) and self.lines[self.i].level > level:
            child = self.lines[self.i]
            if child.level != level + 1:
                raise ImportFormatError(
                    "unexpected indentation", file=self.file, line=child.number, element=node.name
                )
            node.children.append(self.node(level + 1))
        return node

    def _expression(self, node: Node, level: int, depth: int, first: str) -> None:
        """Collect the lines of a multi-line expression (indented ``depth`` or deeper, or between
        ``` fences) onto the node."""
        parts = [first] if first else []
        fenced = first.startswith("```")
        if (
            not fenced
            and self.i < len(self.lines)
            and self.lines[self.i].text.startswith("```")
            and self.lines[self.i].level >= depth
        ):
            fenced = True
        if fenced:
            closed = first.startswith("```") and first.count("```") > 1
            while not closed and self.i < len(self.lines):
                line = self.lines[self.i]
                self.i += 1
                parts.append(line.text)
                closed = line.text.strip() == "```" and len(parts) > 1
            text = "\n".join(parts)
        else:
            while self.i < len(self.lines) and self.lines[self.i].level >= depth:
                parts.append(self.lines[self.i].text)
                self.i += 1
            text = "\n".join(parts)
        if node.value is not None and parts:
            node.value = text
        elif node.expr is not None or parts:
            node.expr = text


def parse_tmdl(text: str, file: str) -> list[Node]:
    """The top-level objects of one TMDL file."""
    return _Parser(text, file).parse()


# ---- the importer ----------------------------------------------------------------------------


def _files(path: Path) -> tuple[Path, list[Path]]:
    if path.is_file():
        return path.parent, [path]
    root = path / "definition" if (path / "definition").is_dir() else path
    files = sorted(root.rglob("*.tmdl"), key=lambda p: p.relative_to(root).as_posix())
    if not files:
        raise ImportFormatError("no .tmdl files found", file=str(path))
    return root, files


def _column_type(node: Node, report: Report, element: str) -> str | None:
    data_type = node.prop("dataType")
    if data_type is None:
        report.skipped(element, "column", "column has no dataType: imported as string")
        return "string"
    key = data_type.strip().lower()
    for tmdl, ours in _TYPES.items():
        if key == tmdl.lower():
            return ours
    if key == "datetime":
        fmt = (node.prop("formatString") or "").strip().strip('"').lower()
        return "date" if fmt in _DATE_FORMATS else "timestamp"
    if key == "binary":
        report.skipped(element, "column", "binary column has no generator: left out")
        return None
    report.skipped(element, "column", f"dataType {data_type!r} is not known: imported as string")
    return "string"


def _scale(format_string: str | None) -> int:
    """The decimals of a number format such as ``#,0.00`` (2 when it does not say)."""
    fmt = (format_string or "").strip().strip('"')
    if "." in fmt:
        tail = fmt.split(".", 1)[1]
        digits = len(tail) - len(tail.lstrip("0#"))
        return min(digits, 18) if digits else 2
    return 2


def import_tmdl(source: str, report: Report) -> ImpModel:
    path = Path(source)
    if not path.exists():
        raise ImportFormatError("file or folder not found", file=source)
    root, files = _files(path)
    nodes: list[Node] = []
    for f in files:
        nodes += parse_tmdl(read_text(f), str(f))
    model = ImpModel(path.stem if path.is_file() else path.resolve().name)
    for n in nodes:
        if n.keyword == "database" and n.name:
            model.name = n.name
    tables: dict[str, ImpTable] = {}
    columns: dict[tuple[str, str], str] = {}
    key_columns: dict[str, list[str]] = {}
    used_tables: set[str] = set()
    defined: dict[str, Node] = {}
    for n in nodes:
        if n.keyword == "table":
            if not n.name:
                raise ImportFormatError(
                    "a table needs a name", file=n.file, line=n.line, element="table"
                )
            if n.name in defined:
                prior = defined[n.name]
                raise ImportFormatError(
                    f"table {n.name!r} is defined twice (also at {prior.file}:{prior.line})",
                    file=n.file,
                    line=n.line,
                    element=f"table {n.name}",
                )
            defined[n.name] = n
            _table(n, model, tables, columns, key_columns, used_tables, report)
        elif n.keyword in ("model", "database", "ref table", "ref"):
            continue
        elif n.keyword == "relationship":
            continue
        else:
            report.skipped(
                f"{n.keyword} {n.name}".strip(),
                n.keyword,
                f"{n.keyword} is not part of the structure: not imported",
            )
    links: list[tuple[ImpTable, ImpForeignKey, str, str]] = []
    keyed: dict[str, str] = {}
    for n in nodes:
        if n.keyword == "relationship":
            _relationship(n, tables, columns, links, keyed, report)
    for name, cols in key_columns.items():
        if name not in keyed and len(cols) == 1:
            keyed[name] = cols[0]
    for tname, table in tables.items():
        key_col = table.column(keyed[tname]) if tname in keyed else None
        # A date or number-with-fraction key cannot be generated unique: such a table gets a
        # generated key instead (the column stays, and relationships still point at it).
        if key_col is not None and key_col.type in ("integer", "string", "uuid"):
            table.primary_key = [keyed[tname]]
            key_col.nullable = False
        else:
            choose_key(table)
    for owner, fk, parent_name, parent_col in links:
        fk.ref_column = parent_col
        col = owner.column(fk.column)
        pcol = tables[parent_name].column(parent_col)
        if col is not None and pcol is not None:
            col.type = pcol.type
    if not model.tables:
        raise ImportFormatError("the model defines no table", file=source)
    return model


def _table(
    node: Node,
    model: ImpModel,
    tables: dict[str, ImpTable],
    columns: dict[tuple[str, str], str],
    key_columns: dict[str, list[str]],
    used_tables: set[str],
    report: Report,
) -> None:
    element = f"table {node.name}"
    table = ImpTable(unique_name(clean_name(node.name), used_tables), source=element)
    tables[node.name] = table
    model.tables.append(table)
    used: set[str] = set()
    for child in node.children:
        where = f"{element}/{child.keyword} {child.name}".strip()
        if child.keyword == "column":
            if child.expr is not None and child.expr != "":
                report.skipped(where, "calculated column", "DAX expression: not imported")
                continue
            col_type = _column_type(child, report, where)
            if col_type is None:
                continue
            col = ImpColumn(
                unique_name(clean_name(child.name), used),
                col_type,
                True,
                source=where,
            )
            if col_type == "decimal":
                col.precision, col.scale = 18, _scale(child.prop("formatString"))
            table.columns.append(col)
            columns[(node.name, child.name)] = col.name
            if child.has_flag("isKey"):
                key_columns.setdefault(node.name, []).append(col.name)
        elif child.keyword == "measure":
            report.skipped(where, "measure", "DAX expression: not imported (structure only)")
        elif child.keyword == "hierarchy":
            levels = [lv.name for lv in child.of("level")]
            report.skipped(
                where,
                "hierarchy",
                "hierarchy is not imported" + (f" (levels: {', '.join(levels)})" if levels else ""),
            )
        elif child.keyword == "partition":
            report.skipped(where, "partition", "data source definition: not imported")
        elif child.keyword in ("calculationGroup", "calculationItem"):
            report.skipped(where, child.keyword, "calculation group: not imported")
        elif child.keyword in ("annotation", "extendedProperty", "changedProperty", "ref"):
            continue
        elif child.value is None and child.expr is None and not child.children and not child.name:
            continue  # a flag (isHidden, ...) of the table
        elif child.value is not None:
            continue  # a property of the table (dataCategory, description, ...)
        else:
            report.skipped(where, child.keyword, f"{child.keyword} is not imported")
    if not table.columns:
        raise ImportFormatError(
            f"table {node.name!r} has no importable column",
            file=node.file,
            line=node.line,
            element=element,
        )


def _relationship(
    node: Node,
    tables: dict[str, ImpTable],
    columns: dict[tuple[str, str], str],
    links: list[tuple[ImpTable, ImpForeignKey, str, str]],
    keyed: dict[str, str],
    report: Report,
) -> None:
    element = f"relationship {node.name}".strip()

    def end(kind: str) -> tuple[str, str]:
        text = node.prop(f"{kind}Column")
        if text is None:
            raise ImportFormatError(
                f"relationship has no {kind}Column", file=node.file, line=node.line, element=element
            )
        table, col = split_ref(text, node.file, node.line, element)
        if table not in tables:
            raise ImportFormatError(
                f"{kind}Column names the unknown table {table!r}",
                file=node.file,
                line=node.line,
                element=element,
            )
        if (table, col) not in columns:
            raise ImportFormatError(
                f"{kind}Column names the unknown column {col!r} of table {table!r}",
                file=node.file,
                line=node.line,
                element=element,
            )
        return table, col

    from_t, from_c = end("from")
    to_t, to_c = end("to")
    from_card = (node.prop("fromCardinality") or "many").strip().lower()
    to_card = (node.prop("toCardinality") or "one").strip().lower()
    for card in (from_card, to_card):
        if card not in ("one", "many"):
            raise ImportFormatError(
                f"cardinality {card!r} is not one or many",
                file=node.file,
                line=node.line,
                element=element,
            )
    kind = "one_to_many"
    child_t, child_c, parent_t, parent_c = from_t, from_c, to_t, to_c
    if (from_card, to_card) == ("one", "many"):
        child_t, child_c, parent_t, parent_c = to_t, to_c, from_t, from_c
    elif (from_card, to_card) == ("one", "one"):
        kind = "one_to_one"
    elif (from_card, to_card) == ("many", "many"):
        kind = "many_to_many"
    child = tables[child_t]
    cname = columns[(child_t, child_c)]
    pname = columns[(parent_t, parent_c)]
    col = child.column(cname)
    if col is not None:
        col.kind, col.nullable = "reference", False
    fk = ImpForeignKey(cname, tables[parent_t].name, pname, kind)
    child.foreign_keys.append(fk)
    links.append((child, fk, parent_t, pname))
    keyed.setdefault(parent_t, pname)
    became = f"foreign key {child.name}.{cname} -> {tables[parent_t].name}.{pname} ({kind})"
    if (node.prop("isActive") or "true").strip().lower() == "false":
        became += ", inactive in the model"
    report.mapped(element, "relationship", became)
    behavior = (node.prop("crossFilteringBehavior") or "").strip()
    if behavior and behavior != "oneDirection":
        report.skipped(element, "crossFilteringBehavior", f"{behavior} filtering is not imported")
