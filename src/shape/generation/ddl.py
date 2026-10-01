"""Read SQL ``CREATE TABLE`` DDL into a :class:`~shape.generation.schema.GenSchema` (P4-01b).

SQL Server and Fabric Warehouse, PostgreSQL, MySQL and ANSI SQL, by regular expressions (there is
no SQL parser dependency). The parser recovers tables, columns, types, nullability, primary keys
(inline and table level), foreign keys (inline, table level and ``ALTER TABLE ... ADD
CONSTRAINT``), and picks a first generator for every column from its type and name. Foreign keys
the DDL does not declare are found by the ``<table>_id`` naming convention. Smart inference
(:mod:`shape.generation.ddl_infer`) then upgrades those first generators.

    from shape.generation.ddl import from_ddl
    schema, notes = from_ddl(open("tables.sql").read(), domain="shop", smart=True)

Stable interface: :class:`DdlParser`, :func:`from_ddl`, :func:`apply_scale` and
:class:`DdlError`.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.errors import ShapeError
from shape.generation.ddl_names import snake, words
from shape.generation.schema import (
    Column,
    Generation,
    GenSchema,
    Model,
    Relationship,
    Table,
)

# Binary types have no generator: the column is left out of the schema.
_BINARY = (
    "varbinary",
    "binary",
    "binary varying",
    "image",
    "bytea",
    "blob",
    "tinyblob",
    "mediumblob",
    "longblob",
)

_UNIFORM_DATE: dict[str, Any] = {
    "strategy": "temporal",
    "pattern": "uniform",
    "range_ref": "model.date_range",
}

# SQL type -> first generator (None: no generator, the column is skipped).
TYPE_MAP: dict[str, dict[str, Any] | None] = {
    "int": {"strategy": "distribution", "distribution": "uniform", "min": 1, "max": 10000},
    "integer": {"strategy": "distribution", "distribution": "uniform", "min": 1, "max": 10000},
    "bigint": {"strategy": "distribution", "distribution": "uniform", "min": 1, "max": 1000000},
    "smallint": {"strategy": "distribution", "distribution": "uniform", "min": 1, "max": 1000},
    "tinyint": {"strategy": "distribution", "distribution": "uniform", "min": 0, "max": 255},
    "bit": {"strategy": "weighted_enum", "values": {"1": 0.85, "0": 0.15}},
    "boolean": {"strategy": "weighted_enum", "values": {"true": 0.85, "false": 0.15}},
    "bool": {"strategy": "weighted_enum", "values": {"true": 0.85, "false": 0.15}},
    "datetime": dict(_UNIFORM_DATE),
    "datetime2": dict(_UNIFORM_DATE),
    "date": dict(_UNIFORM_DATE),
    "datetimeoffset": dict(_UNIFORM_DATE),
    "timestamp": dict(_UNIFORM_DATE),
    "timestamptz": dict(_UNIFORM_DATE),
    "time": dict(_UNIFORM_DATE),
    "decimal": {
        "strategy": "distribution",
        "distribution": "normal",
        "mean": 100,
        "std_dev": 50,
        "min": 0,
    },
    "numeric": {
        "strategy": "distribution",
        "distribution": "normal",
        "mean": 100,
        "std_dev": 50,
        "min": 0,
    },
    "money": {
        "strategy": "distribution",
        "distribution": "log_normal",
        "mean": 4.5,
        "sigma": 1.0,
        "min": 0,
    },
    "smallmoney": {
        "strategy": "distribution",
        "distribution": "log_normal",
        "mean": 3.0,
        "sigma": 0.8,
        "min": 0,
    },
    "float": {"strategy": "distribution", "distribution": "normal", "mean": 0, "std_dev": 1},
    "real": {"strategy": "distribution", "distribution": "normal", "mean": 0, "std_dev": 1},
    "double precision": {
        "strategy": "distribution",
        "distribution": "normal",
        "mean": 0,
        "std_dev": 1,
    },
    "uniqueidentifier": {"strategy": "uuid"},
    "uuid": {"strategy": "uuid"},
    **dict.fromkeys(_BINARY),
}

_STATUS_ENUM: dict[str, Any] = {
    "strategy": "weighted_enum",
    "values": {"active": 0.7, "inactive": 0.2, "pending": 0.1},
}


def _faker(provider: str) -> dict[str, Any]:
    return {"strategy": "faker", "provider": provider}


_GENDER_ENUM: dict[str, Any] = {"strategy": "weighted_enum", "values": {"M": 0.49, "F": 0.51}}


# Column name -> generator for string types (exact, case-insensitive).
NAME_EXACT: dict[str, dict[str, Any]] = {
    "first_name": _faker("first_name"),
    "firstname": _faker("first_name"),
    "last_name": _faker("last_name"),
    "lastname": _faker("last_name"),
    "email": _faker("email"),
    "email_address": _faker("email"),
    "phone": _faker("phone_number"),
    "phone_number": _faker("phone_number"),
    "address": _faker("street_address"),
    "street_address": _faker("street_address"),
    "city": _faker("city"),
    "state": _faker("state_abbr"),
    "zip": _faker("zipcode"),
    "zip_code": _faker("zipcode"),
    "zipcode": _faker("zipcode"),
    "postal_code": _faker("zipcode"),
    "country": _faker("country"),
    "company": _faker("company"),
    "company_name": _faker("company"),
    "url": _faker("url"),
    "website": _faker("url"),
    "ssn": _faker("ssn"),
    "description": _faker("sentence"),
    "notes": _faker("sentence"),
    "comment": _faker("sentence"),
    "comments": _faker("sentence"),
    "username": _faker("user_name"),
    "user_name": _faker("user_name"),
    "ip_address": _faker("ipv4"),
    "status": _STATUS_ENUM,
    "gender": _GENDER_ENUM,
    "sex": _GENDER_ENUM,
}

# Single-character string columns hold a code, so they get a short value set chosen by the name's
# words (the first group with a word in the name wins).
_CODE_SETS: tuple[tuple[frozenset[str], dict[str, float]], ...] = (
    (frozenset({"gender", "sex"}), {"M": 0.49, "F": 0.51}),
    (frozenset({"status", "state"}), {"A": 0.7, "I": 0.2, "P": 0.1}),
    (
        frozenset({"is", "has", "can", "flag", "ind", "indicator", "active", "enabled", "deleted"}),
        {"Y": 0.85, "N": 0.15},
    ),
)
_CODE_DEFAULT = {"A": 0.5, "B": 0.3, "C": 0.2}


def _code_generator(name: str) -> dict[str, Any]:
    """A one-character string column holds a code (``gender CHAR(1)``): a short value set chosen
    by the words of its name."""
    found = set(words(name))
    values = next((v for names, v in _CODE_SETS if names & found), _CODE_DEFAULT)
    return {"strategy": "weighted_enum", "values": dict(values)}


# Name suffix -> generator for string types, tried in this order.
NAME_SUFFIX: dict[str, dict[str, Any] | str] = {
    "_name": _faker("name"),
    "_email": _faker("email"),
    "_phone": _faker("phone_number"),
    "_date": dict(_UNIFORM_DATE),
    "_code": {"strategy": "pattern", "format": "{seq:6}"},
    "_type": {
        "strategy": "weighted_enum",
        "values": {"type_a": 0.5, "type_b": 0.3, "type_c": 0.2},
    },
    "_status": _STATUS_ENUM,
    "_id": "fk_candidate",  # handled by foreign-key detection
}

_STRING_TYPES = {
    "nvarchar",
    "varchar",
    "char",
    "nchar",
    "text",
    "ntext",
    "character varying",
    "character",
    "clob",
}
_SERIAL_TYPES = {"serial", "bigserial", "smallserial"}

# Largest accepted DDL text (guards the regular expressions against pathological input).
_MAX_DDL_SIZE = 10 * 1024 * 1024

_NAME = r"[\w.\[\]\"` ]"
_UNQUOTE = re.compile(r'[\[\]"`]')
_CREATE_TABLE_HEADER = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(" + _NAME + r"+?)\s*\(", re.IGNORECASE
)
_ALTER_FK = re.compile(
    r"ALTER\s+TABLE\s+(" + _NAME + r"+?)\s+"
    r"ADD\s+CONSTRAINT\s+" + _NAME + r"+\s+"
    r"FOREIGN\s+KEY\s*\(\s*(" + _NAME + r"+)\s*\)\s*"
    r"REFERENCES\s+(" + _NAME + r"+?)\s*\(\s*(" + _NAME + r"+)\s*\)",
    re.IGNORECASE,
)
_INLINE_FK = re.compile(
    r"FOREIGN\s+KEY\s*\(\s*(" + _NAME + r"+)\s*\)\s*"
    r"REFERENCES\s+(" + _NAME + r"+?)\s*\(\s*(" + _NAME + r"+)\s*\)",
    re.IGNORECASE,
)
_TABLE_PK = re.compile(
    r"(?:CONSTRAINT\s+" + _NAME + r"+\s+)?"
    r"PRIMARY\s+KEY\s*(?:(?:NON)?CLUSTERED\s*)?"
    r"\(\s*([\w,\s.\[\]\"` ]+)\s*\)",
    re.IGNORECASE,
)
_ASC_DESC = re.compile(r"\s+(?:ASC|DESC)\b", re.IGNORECASE)
_TYPE_SPEC = re.compile(r"([\w\s]+?)\s*(?:\(\s*(\d+|max)\s*(?:,\s*(\d+)\s*)?\))?$", re.IGNORECASE)
_REFERENCES = re.compile(
    r"\bREFERENCES\s+(" + _NAME + r"+?)\s*\(\s*(" + _NAME + r"+)\s*\)", re.IGNORECASE
)
_REFERENCES_PK = re.compile(r"\bREFERENCES\s+([\w.\[\]\"`]+)", re.IGNORECASE)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_IDENTITY = re.compile(r"IDENTITY\s*(?:\(\s*\d+\s*,\s*\d+\s*\))?", re.IGNORECASE)


class DdlError(ShapeError):
    """The DDL could not be read."""


@dataclass
class _ParsedColumn:
    name: str
    raw_type: str
    base_type: str
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    nullable: bool = True
    is_identity: bool = False
    is_serial: bool = False
    is_auto_increment: bool = False
    is_primary_key: bool = False
    default: str | None = None
    references: tuple[str, str] | None = None  # (parent table, parent column or "" for its key)


@dataclass
class _ParsedTable:
    name: str
    columns: list[_ParsedColumn] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[dict[str, str]] = field(default_factory=list)


@dataclass
class _ForeignKey:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str


def _unquote(name: str) -> str:
    return _UNQUOTE.sub("", name).strip()


def _table_name(raw: str) -> str:
    """The unqualified table name of a possibly schema-qualified, quoted one."""
    name = _unquote(raw)
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name.strip()


class DdlParser:
    """``parse_string`` / ``parse_file``: DDL text to a :class:`GenSchema` with a first
    generator per column (no smart inference)."""

    def parse_file(self, path: str | Path) -> GenSchema:
        return self.parse_string(Path(path).read_text(encoding="utf-8"))

    def parse_string(self, sql: str) -> GenSchema:
        if len(sql) > _MAX_DDL_SIZE:
            raise DdlError(
                f"DDL input exceeds the maximum size ({len(sql):,} bytes > "
                f"{_MAX_DDL_SIZE:,} bytes); split it into smaller files"
            )
        sql = self._strip_comments(sql)
        parsed = self._extract_tables(sql)
        alter_fks = self._extract_alter_fks(sql)
        names = {t.name.lower(): t.name for t in parsed}
        fks = self._collect_fks(parsed, alter_fks)
        return self._build_schema(parsed, self._resolve_key_references(parsed, fks), names)

    # ---- comments ---------------------------------------------------------------------

    @staticmethod
    def _strip_comments(sql: str) -> str:
        """Remove ``/* */`` and ``--`` comments (before anything counts parentheses or commas),
        leaving single-quoted string literals alone."""
        out: list[str] = []
        i, n = 0, len(sql)
        in_string = False
        while i < n:
            ch = sql[i]
            if in_string:
                out.append(ch)
                if ch == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append(sql[i + 1])
                        i += 2
                        continue
                    in_string = False
                i += 1
                continue
            if ch == "'":
                in_string = True
                out.append(ch)
                i += 1
                continue
            if ch == "-" and i + 1 < n and sql[i + 1] == "-":
                j = sql.find("\n", i)
                if j == -1:
                    break
                i = j
                continue
            if ch == "/" and i + 1 < n and sql[i + 1] == "*":
                j = sql.find("*/", i + 2)
                if j == -1:
                    break
                i = j + 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    # ---- extraction -------------------------------------------------------------------

    def _extract_tables(self, sql: str) -> list[_ParsedTable]:
        tables = []
        for match in _CREATE_TABLE_HEADER.finditer(sql):
            body = self._paren_body(sql, match.end() - 1)
            if body is not None:
                tables.append(self._parse_create_table(match.group(1), body))
        return tables

    @staticmethod
    def _paren_body(sql: str, open_pos: int) -> str | None:
        if sql[open_pos] != "(":
            return None
        depth = 0
        for i in range(open_pos, len(sql)):
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
                if depth == 0:
                    return sql[open_pos + 1 : i].strip()
        return None

    def _parse_create_table(self, raw_name: str, body: str) -> _ParsedTable:
        table = _ParsedTable(name=_table_name(raw_name))
        for part in self._split_columns(body):
            part = part.strip()
            if not part:
                continue
            first = part.split()[0].upper() if part.split() else ""
            if first in ("CONSTRAINT", "UNIQUE", "CHECK", "INDEX", "PRIMARY", "FOREIGN"):
                pk = _TABLE_PK.search(part)
                if pk:
                    raw_cols = _ASC_DESC.sub("", pk.group(1))
                    table.primary_key = [_unquote(c.strip()) for c in raw_cols.split(",")]
                fk = _INLINE_FK.search(part)
                if fk:
                    table.foreign_keys.append(
                        {
                            "child_column": _unquote(fk.group(1)),
                            "parent_table": _table_name(fk.group(2)),
                            "parent_column": _unquote(fk.group(3)),
                        }
                    )
                continue
            col = self._parse_column(part)
            if col:
                table.columns.append(col)
                if col.references:
                    table.foreign_keys.append(
                        {
                            "child_column": col.name,
                            "parent_table": col.references[0],
                            "parent_column": col.references[1],
                        }
                    )
                if col.is_primary_key and not table.primary_key:
                    table.primary_key = [col.name]
        return table

    def _parse_column(self, definition: str) -> _ParsedColumn | None:
        definition = definition.strip()
        if not definition:
            return None
        if definition.startswith(("[", '"', "`")):
            closing = "]" if definition.startswith("[") else definition[0]
            try:
                end = definition.index(closing, 1) + 1
            except ValueError:
                return None
            name = _unquote(definition[:end])
            rest = definition[end:].strip()
        else:
            parts = definition.split(None, 1)
            if len(parts) < 2:
                return None
            name = _unquote(parts[0])
            rest = parts[1]
        if not rest:
            return None

        upper = rest.upper()
        is_identity = bool(_IDENTITY.search(upper))
        no_identity = _IDENTITY.sub("", rest).strip()
        is_auto = "AUTO_INCREMENT" in upper
        clean = re.sub(r"AUTO_INCREMENT", "", no_identity, flags=re.IGNORECASE).strip()

        type_part = clean
        for kw in (
            "NOT NULL",
            "NULL",
            "DEFAULT",
            "PRIMARY KEY",
            "CONSTRAINT",
            "REFERENCES",
            "UNIQUE",
            "CHECK",
            "COLLATE",
            "GENERATED",
        ):
            idx = type_part.upper().find(kw)
            if idx > 0:
                type_part = type_part[:idx]
        type_part = type_part.strip().rstrip(",")

        base, max_length, precision, scale = self._parse_type(type_part)
        references = self._column_references(rest)
        default = None
        m = re.search(r"DEFAULT\s+(\S+)", rest, re.IGNORECASE)
        if m:
            default = m.group(1).strip("'\"(),")
        return _ParsedColumn(
            name=name,
            raw_type=type_part.strip(),
            base_type=base,
            max_length=max_length,
            precision=precision,
            scale=scale,
            nullable="NOT NULL" not in upper,
            is_identity=is_identity,
            is_serial=base.lower() in _SERIAL_TYPES,
            is_auto_increment=is_auto,
            is_primary_key="PRIMARY KEY" in upper,
            default=default,
            references=references,
        )

    @staticmethod
    def _column_references(rest: str) -> tuple[str, str] | None:
        """The parent of a column-level ``[CONSTRAINT name] REFERENCES parent(col)`` clause (any
        ``ON DELETE ...`` after it is ignored); ``col`` is empty when the clause names only the
        parent table, whose primary key it then means."""
        text = _STRING_LITERAL.sub("''", rest)
        match = _REFERENCES.search(text)
        if match:
            return _table_name(match.group(1)), _unquote(match.group(2))
        match = _REFERENCES_PK.search(text)
        return (_table_name(match.group(1)), "") if match else None

    @staticmethod
    def _parse_type(type_str: str) -> tuple[str, int | None, int | None, int | None]:
        """``(base_type, max_length, precision, scale)`` of ``NVARCHAR(50)``, ``DECIMAL(18,2)``."""
        type_str = type_str.strip()
        match = _TYPE_SPEC.match(type_str)
        if not match:
            return type_str.lower(), None, None, None
        base = match.group(1).strip().lower()
        p1 = (
            int(match.group(2)) if match.group(2) and match.group(2).isdigit() else None
        )  # MAX: none
        p2 = int(match.group(3)) if match.group(3) else None
        if base in ("decimal", "numeric"):
            return base, None, p1, p2
        if base in _STRING_TYPES or base in _BINARY:
            return base, p1, None, None
        return base, p1, p2, None

    @staticmethod
    def _split_columns(body: str) -> list[str]:
        """Split a ``CREATE TABLE`` body at top-level commas."""
        parts: list[str] = []
        depth = 0
        current: list[str] = []
        for ch in body:
            if ch == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            current.append(ch)
        if current:
            parts.append("".join(current))
        return parts

    @staticmethod
    def _extract_alter_fks(sql: str) -> list[_ForeignKey]:
        return [
            _ForeignKey(
                child_table=_table_name(m.group(1)),
                child_column=_unquote(m.group(2)),
                parent_table=_table_name(m.group(3)),
                parent_column=_unquote(m.group(4)),
            )
            for m in _ALTER_FK.finditer(sql)
        ]

    @staticmethod
    def _collect_fks(tables: list[_ParsedTable], alter_fks: list[_ForeignKey]) -> list[_ForeignKey]:
        fks = [
            _ForeignKey(t.name, fk["child_column"], fk["parent_table"], fk["parent_column"])
            for t in tables
            for fk in t.foreign_keys
        ]
        return fks + alter_fks

    @staticmethod
    def _resolve_key_references(
        tables: list[_ParsedTable], fks: list[_ForeignKey]
    ) -> list[_ForeignKey]:
        """Foreign keys that name only the parent table (``REFERENCES parent``) point at its
        single-column primary key; one the parent does not give is dropped."""
        keys = {t.name.lower(): t.primary_key for t in tables}
        out: list[_ForeignKey] = []
        for fk in fks:
            if not fk.parent_column:
                key = keys.get(fk.parent_table.lower(), [])
                if len(key) != 1:
                    continue
                fk = _ForeignKey(fk.child_table, fk.child_column, fk.parent_table, key[0])
            out.append(fk)
        return out

    # ---- schema -----------------------------------------------------------------------

    def _build_schema(
        self,
        parsed: list[_ParsedTable],
        fks: list[_ForeignKey],
        names: dict[str, str],
    ) -> GenSchema:
        fk_index = {(f.child_table.lower(), f.child_column.lower()): f for f in fks}
        keys = {pt.name.lower(): pt.primary_key for pt in parsed}
        convention: list[_ForeignKey] = []
        tables: dict[str, Table] = {}
        for pt in parsed:
            columns: dict[str, Column] = {}
            for pc in pt.columns:
                generator = self._resolve_generator(pc, pt, fk_index, names, keys)
                if generator is None:
                    continue
                if (
                    generator.get("strategy") == "foreign_key"
                    and (pt.name.lower(), pc.name.lower()) not in fk_index
                ):
                    ref = generator.get("ref", "")
                    if "." in ref:
                        parent, parent_col = ref.split(".", 1)
                        convention.append(_ForeignKey(pt.name, pc.name, parent, parent_col))
                columns[pc.name] = Column(
                    name=pc.name,
                    type=self._logical_type(pc),
                    generator=generator,
                    nullable=pc.nullable,
                    max_length=pc.max_length,
                    precision=pc.precision,
                    scale=pc.scale,
                )
            tables[pt.name] = Table(name=pt.name, columns=columns, primary_key=pt.primary_key)

        schema = GenSchema(
            model=Model(
                name="ddl_import",
                description="Imported from SQL DDL",
                domain="custom",
                schema_mode="3nf",
                date_range={"start": "2024-01-01", "end": "2025-12-31"},
            ),
            tables=tables,
            relationships=self._relationships(fks + convention, tables),
            generation=self._generation(tables),
        )
        fit_string_lengths(schema)
        return schema

    def _resolve_generator(
        self,
        col: _ParsedColumn,
        table: _ParsedTable,
        fk_index: dict[tuple[str, str], _ForeignKey],
        names: dict[str, str],
        keys: dict[str, list[str]],
    ) -> dict[str, Any] | None:
        """The best first generator for a column, or ``None`` for a binary column."""
        if col.is_identity or col.is_serial or col.is_auto_increment:
            return {"strategy": "sequence", "start": 1}

        fk = fk_index.get((table.name.lower(), col.name.lower()))
        if fk is not None:
            if fk.parent_table.lower() == table.name.lower():
                return {
                    "strategy": "self_referencing",
                    "pk_column": fk.parent_column,
                    "levels": 3,
                    "root_count": 8,
                }
            return {
                "strategy": "foreign_key",
                "ref": f"{fk.parent_table}.{fk.parent_column}",
                "distribution": "pareto",
            }

        # A key the DDL does not declare, guessed by the ``<table>_id`` convention (also
        # ``CustomerId``): it points at the parent's single-column primary key, and is not
        # guessed when the parent has none (a reference to a column that does not exist).
        lower = snake(col.name)
        if lower.endswith("_id") and lower != "id":
            candidate = lower[:-3]
            real = self._table_for_singular(candidate, names) or self._table_for_singular(
                candidate.replace("_", ""), names
            )
            if real is not None and real.lower() != table.name.lower():
                key = keys.get(real.lower(), [])
                if len(key) == 1:
                    return {
                        "strategy": "foreign_key",
                        "ref": f"{real}.{key[0]}",
                        "distribution": "pareto",
                    }

        if col.name in table.primary_key and len(table.primary_key) == 1:
            return {"strategy": "sequence", "start": 1}

        base = col.base_type.lower()
        if base in _BINARY:
            return None
        if base in _STRING_TYPES:
            if col.max_length == 1:
                return _code_generator(col.name)
            gen = self._string_heuristic(col)
            if gen:
                return gen
            length = col.max_length or 255
            if length <= 10:
                return {"strategy": "pattern", "format": "{seq:6}"}
            return {"strategy": "faker", "provider": "text", "max_nb_chars": min(length, 200)}
        if base in TYPE_MAP:
            gen = TYPE_MAP[base]
            return None if gen is None else copy.deepcopy(gen)
        return {"strategy": "faker", "provider": "text", "max_nb_chars": 50}

    @staticmethod
    def _table_for_singular(candidate: str, names: dict[str, str]) -> str | None:
        """The table a ``<candidate>_id`` column points at: the singular name or a plural
        (``s``, ``es``, ``ies``), or the candidate singularised."""
        if candidate in names:
            return names[candidate]
        if candidate + "s" in names:
            return names[candidate + "s"]
        if candidate + "es" in names:
            return names[candidate + "es"]
        if candidate.endswith("y") and candidate[:-1] + "ies" in names:
            return names[candidate[:-1] + "ies"]
        if candidate.endswith("ies") and candidate[:-3] + "y" in names:
            return names[candidate[:-3] + "y"]
        if candidate.endswith(("ses", "xes", "zes")) and candidate[:-2] in names:
            return names[candidate[:-2]]
        if candidate.endswith("s") and not candidate.endswith("ss") and candidate[:-1] in names:
            return names[candidate[:-1]]
        return None

    @staticmethod
    def _string_heuristic(col: _ParsedColumn) -> dict[str, Any] | None:
        name = col.name.lower()
        if name in NAME_EXACT:
            return copy.deepcopy(NAME_EXACT[name])
        for suffix, gen in NAME_SUFFIX.items():
            if name.endswith(suffix) and gen != "fk_candidate":
                return copy.deepcopy(gen) if isinstance(gen, dict) else None
        return None

    @staticmethod
    def _logical_type(col: _ParsedColumn) -> str:
        base = col.base_type.lower()
        if base in _SERIAL_TYPES or col.is_identity:
            return "integer"
        if base in ("int", "integer", "bigint", "smallint", "tinyint"):
            return "integer"
        if base in ("bit", "boolean", "bool"):
            return "boolean"
        if base in ("decimal", "numeric", "money", "smallmoney"):
            return "decimal"
        if base in ("float", "real", "double precision"):
            return "float"
        if base in ("datetime", "datetime2", "datetimeoffset", "timestamp", "timestamptz"):
            return "timestamp"
        if base == "date":
            return "date"
        if base == "time":
            return "time"
        if base in ("uniqueidentifier", "uuid"):
            return "uuid"
        if base in _BINARY:
            return "binary"
        return "string"

    @staticmethod
    def _relationships(fks: list[_ForeignKey], tables: dict[str, Table]) -> list[Relationship]:
        return [
            Relationship(
                name=f"fk_{fk.child_table}_{fk.child_column}",
                parent=fk.parent_table,
                child=fk.child_table,
                parent_columns=[fk.parent_column],
                child_columns=[fk.child_column],
                type="one_to_many",
            )
            for fk in fks
            if fk.parent_table in tables and fk.child_table in tables
        ]

    @staticmethod
    def _generation(tables: dict[str, Table]) -> Generation:
        """Scale presets: 1k/10k/100k rows for tables with no parent, 2.5k/25k/250k for the
        rest."""
        small: dict[str, int] = {}
        medium: dict[str, int] = {}
        large: dict[str, int] = {}
        for name, table in tables.items():
            if table.fk_dependencies:
                small[name], medium[name], large[name] = 2500, 25000, 250000
            else:
                small[name], medium[name], large[name] = 1000, 10000, 100000
        return Generation(scale="small", scales={"small": small, "medium": medium, "large": large})


_PATTERN_TOKEN = re.compile(r"\{(\w+)(?::(\d+))?\}")


def _max_rows(schema: GenSchema) -> dict[str, int]:
    """The most rows each table gets in any scale preset (derived counts included)."""
    from shape.generation.engine import calculate_row_counts

    most: dict[str, int] = {}
    chosen = schema.generation.scale
    try:
        for preset in schema.generation.scales:
            schema.generation.scale = preset
            for table, rows in calculate_row_counts(schema).items():
                most[table] = max(most.get(table, 0), rows)
    finally:
        schema.generation.scale = chosen
    return most


def _pattern_length(fmt: str, rows: int) -> int:
    """The longest value of a ``pattern`` format: literals, ``{seq:w}`` (``w`` digits, more when
    the table has more rows) and ``{random:w}``; another column's value counts as ``w`` or 0."""
    total = 0
    last = 0
    for m in _PATTERN_TOKEN.finditer(fmt):
        total += m.start() - last
        last = m.end()
        token, width = m.group(1), int(m.group(2)) if m.group(2) else 0
        if token == "seq":
            total += max(width, len(str(rows)))
        elif token == "random":
            total += width or 4
        else:
            total += width
    return total + len(fmt) - last


def _fitted_generator(col: Column, table: str, rows: dict[str, int]) -> dict[str, Any]:
    """``col``'s generator, changed only if it can make a value longer than the column."""
    gen = col.generator
    limit = col.max_length or 0
    strategy = gen.get("strategy")
    if strategy == "pattern" and isinstance(gen.get("format"), str):
        if _pattern_length(gen["format"], rows.get(table, 0)) > limit:
            return {"strategy": "pattern", "format": f"{{random:{limit}}}"}
    elif strategy == "weighted_enum" and isinstance(gen.get("values"), dict):
        fits = {k: v for k, v in gen["values"].items() if len(str(k)) <= limit}
        if len(fits) == len(gen["values"]):
            return gen
        if len(fits) >= 2 and sum(fits.values()) > 0:
            total = sum(fits.values())
            return {**gen, "values": {k: v / total for k, v in fits.items()}}
        return _code_generator(col.name)
    return gen


def fit_string_lengths(schema: GenSchema) -> None:
    """Make every string column's generator fit its declared length. A fixed pattern, or a
    value set, that can produce a value longer than ``CHAR(n)``/``VARCHAR(n)`` is replaced:
    a pattern by ``n`` random characters, a value set by the values that fit (or, if fewer than
    two do, a code set). Text from the faker and native providers is cut at the length by the
    strategy itself. Idempotent."""
    rows = _max_rows(schema)
    for tname, table in schema.tables.items():
        for col in table.columns.values():
            if col.type == "string" and col.max_length:
                col.generator = _fitted_generator(col, tname, rows)


def apply_scale(schema: GenSchema, spec: str) -> None:
    """Apply a scale override such as ``small:customer=5000,order=25000``: select that preset
    and set the listed tables' row counts in it."""
    name, _, overrides = spec.partition(":")
    schema.generation.scale = name
    if not overrides:
        return
    preset = schema.generation.scales.get(name, {})
    for pair in overrides.split(","):
        if "=" in pair:
            table, count = pair.split("=", 1)
            try:
                preset[table.strip()] = int(count.strip())
            except ValueError:
                raise DdlError(f"bad row count in scale override {pair!r}") from None
    schema.generation.scales[name] = preset
    fit_string_lengths(schema)


def from_ddl(
    sql: str,
    *,
    domain: str = "custom",
    smart: bool = True,
    scale: str | None = None,
) -> tuple[GenSchema, list[Any]]:
    """DDL text to ``(schema, annotations)``. With ``smart`` the inference pipeline upgrades the
    first generators and returns what it decided (one annotation per decision); without it the
    list is empty. ``domain`` names the schema (``<domain>_ddl_import``); ``scale`` is an
    :func:`apply_scale` spec."""
    schema = DdlParser().parse_string(sql)
    schema.model.domain = domain
    schema.model.name = f"{domain}_ddl_import"
    annotations: list[Any] = []
    if smart:
        from shape.generation.ddl_infer import SchemaInference

        annotations = SchemaInference().run(schema)
        fit_string_lengths(schema)
    if scale:
        apply_scale(schema, scale)
    return schema, annotations
