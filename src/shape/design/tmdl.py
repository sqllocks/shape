"""TMDL export of a star or snowflake design (W5-06): the folder format a Power BI Desktop project
uses for a semantic model.

    from shape.design.tmdl import write_tmdl
    write_tmdl(derive(design, "star"), "retail.SemanticModel", source=design)

writes ``definition/database.tmdl``, ``model.tmdl``, ``relationships.tmdl`` and one
``definition/tables/<table>.tmdl`` per table: typed columns, one relationship per foreign key
(surrogate keys; the first relationship between two tables is active, further ones, as with a
role-playing date dimension, are inactive), the date dimension marked as a date table, and default
measures: a row count per fact and a ``SUM`` per measure declared additive. A non-additive or
semi-additive measure, and one with no declared additivity, gets no sum. Passing ``source`` (the
:class:`~shape.design.model.DesignInput`) is what gives the measures their additivity.

Names are quoted the way :mod:`shape_fabric.semantic_model` quotes them for DAX (a table in
single quotes with a quote doubled, a column's closing bracket doubled) and for M (``#"..."``).
The files have no time stamp, no GUID and no version, tabs for indentation and ``\\n`` line ends:
the same design gives the same bytes.
"""

from __future__ import annotations

import re
from pathlib import Path

from shape.design.model import DesignError, DesignInput
from shape.design.result import Column, SchemaDesign, Table
from shape.generation.ddl_names import snake
from shape.security.names import is_safe_name

COMPATIBILITY_LEVEL = 1604
SOURCE_TYPES = ("lakehouse", "warehouse", "sql_database")
TOM_TYPES = {
    "integer": "int64",
    "string": "string",
    "decimal": "decimal",
    "timestamp": "dateTime",
    "boolean": "boolean",
    "uuid": "string",
    "float": "double",
    "date": "dateTime",
    "time": "string",
    "binary": "binary",
}
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_M_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def tmdl_name(name: str) -> str:
    """A TMDL object name: as it is when it is a plain word, else in single quotes with a quote
    doubled."""
    return name if _PLAIN.match(name) else "'" + name.replace("'", "''") + "'"


def dax_table(name: str) -> str:
    """A table reference in DAX: ``'name'`` with a quote doubled."""
    return "'" + name.replace("'", "''") + "'"


def dax_column(table: str, column: str) -> str:
    """A column reference in DAX: ``'table'[column]`` with a closing bracket doubled."""
    return f"{dax_table(table)}[{column.replace(']', ']]')}]"


def m_text(value: str) -> str:
    return value.replace('"', '""')


def m_identifier(name: str) -> str:
    return name if _M_IDENT.match(name) else f'#"{m_text(name)}"'


def title_case(snake_name: str) -> str:
    return " ".join(w.capitalize() for w in snake_name.split("_") if w)


def _lakehouse(table: str) -> list[str]:
    ident = m_identifier(f"{table}_Data")
    return [
        "let",
        "    Source = Lakehouse.Contents(null),",
        '    Data = Source{[workspaceId="{workspace_id}", itemObjectId="{lakehouse_id}"]}[Data],',
        f'    {ident} = Data{{[schema="{m_text(table)}"]}}[Data]',
        "in",
        f"    {ident}",
    ]


def _sql(server: str, database: str, schema: str, table: str) -> list[str]:
    ident = m_identifier(f"{schema}_{table}")
    return [
        "let",
        f'    Source = Sql.Database("{m_text(server)}", "{m_text(database)}"),',
        f'    {ident} = Source{{[Schema="{m_text(schema)}", Item="{m_text(table)}"]}}[Data]',
        "in",
        f"    {ident}",
    ]


def _tabs(line: str) -> str:
    """Leading runs of four spaces as tabs: TMDL indents with tabs, M code included."""
    body = line.lstrip(" ")
    return "\t" * ((len(line) - len(body)) // 4) + body


def _format(column: Column) -> str | None:
    if column.type == "date":
        return "Short Date"
    if column.type == "timestamp":
        return "General Date"
    if column.type == "decimal":
        scale = column.scale if column.scale is not None else 2
        return "#,0." + "0" * scale if scale else "#,0"
    return None


def _measures(table: Table, fact_title: str, additive: list[tuple[str, Column]]) -> list[str]:
    lines = [
        f"\tmeasure {tmdl_name(fact_title + ' Count')} = COUNTROWS({dax_table(table.name)})",
        "\t\tformatString: #,0",
        "",
    ]
    for measure, column in additive:
        name = tmdl_name(f"{fact_title} Total {title_case(snake(measure))}")
        expr = f"SUM({dax_column(table.name, column.name)})"
        fmt = "#,0" if column.type == "integer" else "#,0.00"
        lines += [f"\tmeasure {name} = {expr}", f"\t\tformatString: {fmt}", ""]
    return lines


def _table_text(
    table: Table,
    sums: set[str],
    fact_title: str | None,
    additive: list[tuple[str, Column]],
    foreign: set[str],
    source_type: str,
    source_name: str,
    schema_name: str,
) -> str:
    lines = [f"table {tmdl_name(table.name)}"]
    key = table.primary_key[0] if len(table.primary_key) == 1 else None
    if table.kind == "date":
        lines.append("\tdataCategory: Time")
    lines.append("")
    if table.kind == "fact" and fact_title is not None:
        lines += _measures(table, fact_title, additive)
    for c in table.columns:
        lines.append(f"\tcolumn {tmdl_name(c.name)}")
        lines.append(f"\t\tdataType: {TOM_TYPES.get(c.type, 'string')}")
        fmt = _format(c)
        if fmt is not None:
            lines.append(f"\t\tformatString: {fmt}")
        # A date table is keyed by its date column; a dimension by its one-column surrogate key.
        if (table.kind == "date" and c.name == "date") or (
            c.name == key and table.kind not in ("date", "fact")
        ):
            lines.append("\t\tisKey")
        if c.name in foreign or c.name == key:
            lines.append("\t\tisHidden")
        lines.append(f"\t\tsummarizeBy: {'sum' if c.name in sums else 'none'}")
        lines.append(f"\t\tsourceColumn: {c.name}")
        lines.append("")
    m = (
        _lakehouse(table.name)
        if source_type == "lakehouse"
        else _sql(source_name, source_name, schema_name, table.name)
    )
    lines.append(f"\tpartition {tmdl_name(table.name)} = m")
    lines.append("\t\tmode: import")
    lines.append("\t\tsource =")
    lines += ["\t\t\t" + _tabs(x) for x in m]
    return "\n".join(lines) + "\n"


def tmdl_files(
    design: SchemaDesign,
    *,
    source: DesignInput | None = None,
    source_type: str = "lakehouse",
    source_name: str = "",
    schema_name: str = "dbo",
) -> dict[str, str]:
    """The files of the TMDL folder as ``{path relative to the definition folder: text}``, in
    the order they are written."""
    if design.mode not in ("star", "snowflake"):
        raise DesignError(
            f"TMDL export needs a star or snowflake design, not {design.mode!r}: a semantic "
            "model is built over facts and dimensions"
        )
    if source_type not in SOURCE_TYPES:
        raise DesignError(
            f"unknown source type {source_type!r}; choose one of {', '.join(SOURCE_TYPES)}"
        )
    declared: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    if source is not None:
        for f in source.facts:
            declared[f"fact_{snake(f.name)}"] = (
                title_case(snake(f.name)),
                [(m.name, m.name) for m in f.measures if m.additivity == "additive"],
            )
    files: dict[str, str] = {}
    files["database.tmdl"] = (
        f"database {tmdl_name(design.name)}\n\tcompatibilityLevel: {COMPATIBILITY_LEVEL}\n"
    )
    refs = "".join(f"ref table {tmdl_name(t.name)}\n" for t in design.tables)
    files["model.tmdl"] = (
        "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tdiscourageImplicitMeasures\n\n" + refs
    )
    foreign: dict[str, set[str]] = {t.name: set() for t in design.tables}
    rel_lines: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()
    used_names: set[str] = set()
    for t in design.tables:
        for fk in t.foreign_keys:
            if len(fk.columns) != 1:
                raise DesignError(
                    f"table {t.name!r}: a composite foreign key ({', '.join(fk.columns)}) "
                    "cannot be a TMDL relationship"
                )
            foreign[t.name].add(fk.columns[0])
            name = f"{t.name}_{fk.columns[0]}_{fk.ref_table}"
            if name in used_names:
                raise DesignError(f"two relationships would be named {name!r}")
            used_names.add(name)
            pair = (t.name, fk.ref_table)
            rel_lines.append(f"relationship {tmdl_name(name)}")
            rel_lines.append(f"\tfromColumn: {tmdl_name(t.name)}.{tmdl_name(fk.columns[0])}")
            rel_lines.append(
                f"\ttoColumn: {tmdl_name(fk.ref_table)}.{tmdl_name(fk.ref_columns[0])}"
            )
            if pair in seen_pairs:
                rel_lines.append("\tisActive: false")
            seen_pairs.add(pair)
            rel_lines.append("")
    if rel_lines:
        files["relationships.tmdl"] = "\n".join(rel_lines).rstrip("\n") + "\n"
    used_files: dict[str, str] = {}
    for t in design.tables:
        title: str | None = None
        additive: list[tuple[str, Column]] = []
        if t.kind == "fact":
            title, wanted = declared.get(t.name, (title_case(t.name.removeprefix("fact_")), []))
            by_name = {c.name: c for c in t.columns}
            additive = [(m, by_name[col]) for m, col in wanted if col in by_name]
        sums = {c.name for _, c in additive}
        text = _table_text(
            t, sums, title, additive, foreign[t.name], source_type, source_name, schema_name
        )
        file = _file_name(t.name)
        if file in used_files:
            raise DesignError(f"tables {used_files[file]!r} and {t.name!r} share the file {file!r}")
        used_files[file] = t.name
        files[f"tables/{file}.tmdl"] = text
    return files


def _file_name(table: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.\- ]", "_", table).strip(" .") or "_"
    if not is_safe_name(name):
        raise DesignError(f"table name {table!r} cannot be a file name")
    return name


def write_tmdl(
    design: SchemaDesign,
    directory: str | Path,
    *,
    source: DesignInput | None = None,
    source_type: str = "lakehouse",
    source_name: str = "",
    schema_name: str = "dbo",
) -> list[Path]:
    """Write the TMDL folder under ``directory/definition`` and return the files written, in
    order. A ``definition`` folder that already holds ``.tmdl`` files this export would not write
    (an earlier export of other tables) is refused rather than mixed with them."""
    files = tmdl_files(
        design,
        source=source,
        source_type=source_type,
        source_name=source_name,
        schema_name=schema_name,
    )
    root = Path(directory) / "definition"
    if root.is_dir():
        stale = sorted(
            p.relative_to(root).as_posix()
            for p in root.rglob("*.tmdl")
            if p.relative_to(root).as_posix() not in files
        )
        if stale:
            raise DesignError(
                f"{root} already holds TMDL files this design does not write "
                f"({', '.join(stale[:5])}): use an empty folder"
            )
    written: list[Path] = []
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written
