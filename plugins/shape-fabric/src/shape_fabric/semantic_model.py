"""Power BI / Fabric semantic model export: a generation schema as a ``.bim`` file (TOM JSON).

``shape fabric export-model DOMAIN`` writes a Tabular Object Model document at compatibility level
1604 with typed columns, relationships, one Power Query (M) partition per table for a Lakehouse,
Warehouse or SQL Database source, and DAX measures (a row count for every table, a total and an
average for each decimal or float column, a total for each other integer column). Standard
library only.

A measure name is unique across the model, as Tabular requires: a name that two measures would
share (``Total Unit Price`` for ``product.unit_price`` and ``order_line.unit_price``) is qualified
with its table, ``Total Unit Price (product)``; every other name is left as it is.

    exporter = SemanticModelExporter()
    exporter.export_bim(schema, source_type="lakehouse", output_path="retail.bim")

Names that reach M or DAX are quoted: a table or column name cannot end a literal or reach out of
a reference (the name of a table in a schema file is not under Shape's control).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shape.generation.schema import Column, GenSchema, Relationship, Table

COMPATIBILITY_LEVEL = 1604
SOURCE_TYPES = ("lakehouse", "warehouse", "sql_database")

TOM_TYPE_MAP: dict[str, str] = {
    "integer": "int64",
    "string": "string",
    "decimal": "decimal",
    "timestamp": "dateTime",
    "boolean": "boolean",
    "uuid": "string",
    "float": "double",
    "date": "dateTime",
    "time": "string",
    "binary": "string",
}

_M_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def m_text(value: str) -> str:
    """``value`` as the inside of an M text literal (a quote is doubled)."""
    return value.replace('"', '""')


def m_identifier(name: str) -> str:
    """``name`` as an M identifier: as it is when it is a plain word, else ``#"..."``."""
    return name if _M_IDENT.match(name) else f'#"{m_text(name)}"'


def dax_table(name: str) -> str:
    """A table reference: ``'name'`` with a quote doubled."""
    return "'" + name.replace("'", "''") + "'"


def dax_column(table: str, column: str) -> str:
    """A column reference: ``'table'[column]`` with a closing bracket doubled."""
    return f"{dax_table(table)}[{column.replace(']', ']]')}]"


def title_case(snake_name: str) -> str:
    return " ".join(w.capitalize() for w in snake_name.split("_"))


def _lakehouse(table: str) -> str:
    ident = m_identifier(f"{table}_Data")
    return (
        "let\n"
        "    Source = Lakehouse.Contents(null),\n"
        '    Data = Source{[workspaceId="{workspace_id}", itemObjectId="{lakehouse_id}"]}[Data],\n'
        f'    {ident} = Data{{[schema="{m_text(table)}"]}}[Data]\n'
        "in\n"
        f"    {ident}"
    )


def _sql(server: str, database: str, schema: str, table: str) -> str:
    ident = m_identifier(f"{schema}_{table}")
    return (
        "let\n"
        f'    Source = Sql.Database("{m_text(server)}", "{m_text(database)}"),\n'
        f'    {ident} = Source{{[Schema="{m_text(schema)}", Item="{m_text(table)}"]}}[Data]\n'
        "in\n"
        f"    {ident}"
    )


def _qualify_shared_measure_names(tables: list[dict[str, Any]]) -> None:
    """Give each measure whose name another measure of the model shares the name ``NAME (TABLE)``.

    Names are compared case-blind, as Tabular compares them; a name no other measure has is kept.
    """
    counts: dict[str, int] = {}
    for table in tables:
        for measure in table.get("measures", []):
            key = measure["name"].casefold()
            counts[key] = counts.get(key, 0) + 1
    for table in tables:
        for measure in table.get("measures", []):
            if counts[measure["name"].casefold()] > 1:
                measure["name"] = f"{measure['name']} ({table['name']})"


class SemanticModelExporter:
    """Export a :class:`~shape.generation.schema.GenSchema` as a Power BI ``.bim`` model."""

    def export_bim(
        self,
        schema: GenSchema,
        source_type: str = "lakehouse",
        source_name: str = "",
        output_path: str | Path = "model.bim",
        include_measures: bool = True,
        schema_name: str = "dbo",
    ) -> Path:
        """Write the model to ``output_path`` (parent folders are created); return the path."""
        tom = self.to_dict(
            schema,
            source_type=source_type,
            source_name=source_name,
            include_measures=include_measures,
            schema_name=schema_name,
        )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(tom, fh, indent=2, ensure_ascii=False)
        return path

    def to_dict(
        self,
        schema: GenSchema,
        source_type: str = "lakehouse",
        source_name: str = "",
        include_measures: bool = True,
        schema_name: str = "dbo",
    ) -> dict[str, Any]:
        """The TOM model as a dict."""
        if source_type not in SOURCE_TYPES:
            raise ValueError(
                f"unknown source type {source_type!r}; choose one of {', '.join(SOURCE_TYPES)}"
            )
        from shape import __version__

        domain = schema.model.domain
        tables = [
            self._table(tdef, source_type, source_name, schema_name, include_measures)
            for tdef in schema.tables.values()
        ]
        _qualify_shared_measure_names(tables)
        return {
            "name": f"Shape{domain.replace('_', ' ').title().replace(' ', '')}",
            "compatibilityLevel": COMPATIBILITY_LEVEL,
            "model": {
                "culture": schema.model.locale.replace("_", "-"),
                "tables": tables,
                "relationships": [self._relationship(r) for r in schema.relationships],
                "roles": [],
                "annotations": [
                    {"name": "generated_by", "value": f"Shape v{__version__}"},
                    {"name": "domain", "value": domain},
                    {"name": "schema_mode", "value": schema.model.schema_mode},
                ],
            },
        }

    # ---- tables, columns, relationships ----------------------------------------------------

    def _table(
        self,
        tdef: Table,
        source_type: str,
        source_name: str,
        schema_name: str,
        include_measures: bool,
    ) -> dict[str, Any]:
        table: dict[str, Any] = {
            "name": tdef.name,
            "columns": [self._column(c, tdef) for c in tdef.columns.values()],
            "partitions": [
                {
                    "name": tdef.name,
                    "source": {
                        "type": "m",
                        "expression": self._m_expression(
                            tdef.name, source_type, source_name, schema_name
                        ),
                    },
                }
            ],
        }
        if tdef.description:
            table["description"] = tdef.description
        if include_measures:
            measures = self._measures(tdef)
            if measures:
                table["measures"] = measures
        return table

    @staticmethod
    def _column(cdef: Column, tdef: Table) -> dict[str, Any]:
        tom_type = TOM_TYPE_MAP.get(cdef.type, "string")
        col: dict[str, Any] = {"name": cdef.name, "dataType": tom_type, "isHidden": False}
        if cdef.name in tdef.primary_key:
            col["isKey"] = True
            col["summarizeBy"] = "none"
        if cdef.is_foreign_key:  # join keys are not for the end user
            col["isHidden"] = True
            col["summarizeBy"] = "none"
        if tom_type in ("string", "dateTime"):
            col["summarizeBy"] = "none"
        col["sourceColumn"] = cdef.name
        return col

    @staticmethod
    def _relationship(rel: Relationship) -> dict[str, Any]:
        return {
            "name": rel.name,
            "fromTable": rel.child,
            "fromColumn": rel.child_columns[0] if rel.child_columns else "",
            "toTable": rel.parent,
            "toColumn": rel.parent_columns[0] if rel.parent_columns else "",
            "crossFilteringBehavior": "oneDirection",
            "isActive": True,
        }

    @staticmethod
    def _m_expression(table: str, source_type: str, source_name: str, schema_name: str) -> str:
        if source_type == "lakehouse":
            return _lakehouse(table)
        return _sql(source_name, source_name, schema_name, table)

    # ---- DAX measures -----------------------------------------------------------------------

    @staticmethod
    def _measures(tdef: Table) -> list[dict[str, Any]]:
        measures: list[dict[str, Any]] = [
            {
                "name": f"{title_case(tdef.name)} Count",
                "expression": f"COUNTROWS({dax_table(tdef.name)})",
                "formatString": "#,0",
            }
        ]
        for cname, cdef in tdef.columns.items():
            if cname in tdef.primary_key or cdef.is_foreign_key:
                continue
            title = title_case(cname)
            ref = dax_column(tdef.name, cname)
            if cdef.type in ("decimal", "float"):
                measures.append(
                    {
                        "name": f"Total {title}",
                        "expression": f"SUM({ref})",
                        "formatString": "#,0.00",
                    }
                )
                measures.append(
                    {
                        "name": f"Avg {title}",
                        "expression": f"AVERAGE({ref})",
                        "formatString": "#,0.00",
                    }
                )
            elif cdef.type == "integer":
                measures.append(
                    {"name": f"Total {title}", "expression": f"SUM({ref})", "formatString": "#,0"}
                )
        return measures
