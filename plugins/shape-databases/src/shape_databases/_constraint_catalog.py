"""Verify requested declarations before changing an existing PostgreSQL/MySQL table."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.write_constraints import compile_constraints

from . import _sql


def _tokens(text: str, *, backslashes: bool = True) -> tuple[str, ...]:
    """Keep quoted bytes intact; never evaluate or rewrite catalog expressions."""
    if len(text) > 1048576:
        raise ShapeError("constraint catalog expression exceeds the supported size")
    result = []
    position = 0
    while position < len(text):
        start = position
        char = text[position]
        position += 1
        if char.isspace():
            continue
        if char in "'\"`":
            escaped = backslashes or (char == "'" and start > 0 and text[start - 1] in "eE")
            while position < len(text):
                if escaped and char != "`" and text[position] == "\\":
                    position += 2
                elif text[position] == char:
                    position += 1
                    if position < len(text) and text[position] == char:
                        position += 1
                    else:
                        break
                else:
                    position += 1
            else:
                raise ShapeError("unclosed quoted constraint catalog expression")
        elif char.isalnum() or char == "_":
            while position < len(text) and (text[position].isalnum() or text[position] in "_$"):
                position += 1
        result.append(text[start:position])
    return tuple(result)


def _expression(text: str, *, backslashes: bool = True) -> tuple[str, ...]:
    tokens = _tokens(text, backslashes=backslashes)
    while len(tokens) > 1 and tokens[0] == "(" and tokens[-1] == ")":
        depth = 0
        for _index, token in enumerate(tokens):
            depth += (token == "(") - (token == ")")
            if depth == 0:
                break
        if _index != len(tokens) - 1:
            break
        tokens = tokens[1:-1]
    return tokens


def _mysql_checks(create_sql: str, *, backslashes: bool) -> dict[str, str]:
    """Read CHECKs from SHOW CREATE for a temporary table, absent from I_S."""
    tokens = _tokens(create_sql, backslashes=backslashes)
    result = {}
    for index in range(len(tokens) - 3):
        if tokens[index].upper() != "CONSTRAINT" or tokens[index + 2].upper() != "CHECK":
            continue
        name = tokens[index + 1]
        if name.startswith("`") and name.endswith("`"):
            name = name[1:-1].replace("``", "`")
        start = index + 3
        if tokens[start] != "(":
            raise ShapeError(f"cannot read CHECK {name!r} from the temporary catalog")
        depth = 0
        for end in range(start, len(tokens)):
            depth += (tokens[end] == "(") - (tokens[end] == ")")
            if depth == 0:
                result[name] = " ".join(tokens[start : end + 1])
                break
        else:
            raise ShapeError(f"cannot read CHECK {name!r} from the temporary catalog")
    return result


def _postgres_catalog(cursor: Any, target: str) -> dict[str, Any]:
    cursor.execute(
        "SELECT c.conname, c.contype, ARRAY(SELECT a.attname::text "
        "FROM unnest(c.conkey) WITH ORDINALITY k(num, pos) "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num "
        "ORDER BY k.pos), pg_get_expr(c.conbin,c.conrelid), c.convalidated, "
        "c.condeferrable FROM pg_catalog.pg_constraint c WHERE c.conrelid=%s::regclass "
        "AND c.contype IN ('p','u','c')",
        (target,),
    )
    keys, checks = {}, {}
    for name, kind, names, expression, validated, deferred in cursor.fetchall():
        if kind == "c":
            checks[str(name)] = (str(expression), bool(validated) and not deferred)
        elif validated and not deferred:
            keys[(str(kind), tuple(names))] = str(name)
    cursor.execute(
        "SELECT a.attname, pg_get_expr(d.adbin,d.adrelid) "
        "FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped",
        (target,),
    )
    defaults = {str(name): value for name, value in cursor.fetchall()}
    cursor.execute(
        "SELECT c.conname, ARRAY(SELECT a.attname::text FROM unnest(c.conkey) "
        "WITH ORDINALITY k(num,pos) JOIN pg_catalog.pg_attribute a "
        "ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.pos), "
        "n.nspname, p.relname, ARRAY(SELECT a.attname::text FROM unnest(c.confkey) "
        "WITH ORDINALITY k(num,pos) JOIN pg_catalog.pg_attribute a "
        "ON a.attrelid=c.confrelid AND a.attnum=k.num ORDER BY k.pos), "
        "c.convalidated, c.condeferrable FROM pg_catalog.pg_constraint c "
        "JOIN pg_catalog.pg_class p ON p.oid=c.confrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.relnamespace "
        "WHERE c.conrelid=%s::regclass AND c.contype='f'",
        (target,),
    )
    foreign = {
        str(name): (
            tuple(local),
            str(namespace),
            str(parent),
            tuple(remote),
            bool(valid) and not defer,
        )
        for name, local, namespace, parent, remote, valid, defer in cursor.fetchall()
    }
    return {"keys": keys, "checks": checks, "defaults": defaults, "foreign": foreign}


def _mysql_catalog(cursor: Any, namespace: str, table: str) -> dict[str, Any]:
    target = _sql.qualified(namespace, table, "mysql")
    cursor.execute(f"SHOW INDEX FROM {target}")
    fields = [str(item[0]).casefold() for item in cursor.description]
    indexes: dict[str, list[tuple[int, str]]] = {}
    invalid = set()
    for row in cursor.fetchall():
        item = dict(zip(fields, row, strict=True))
        name = str(item["key_name"])
        if item["non_unique"] or item.get("sub_part") is not None or not item.get("column_name"):
            invalid.add(name)
        indexes.setdefault(name, []).append((int(item["seq_in_index"]), str(item["column_name"])))
    keys = {
        ("p" if name == "PRIMARY" else "u", tuple(col for _, col in sorted(values))): name
        for name, values in indexes.items()
        if name not in invalid
    }
    cursor.execute(f"SHOW FULL COLUMNS FROM {target}")
    fields = [str(item[0]).casefold() for item in cursor.description]
    defaults = {}
    for row in cursor.fetchall():
        item = dict(zip(fields, row, strict=True))
        defaults[str(item["field"])] = item["default"]
    cursor.execute(
        "SELECT t.CONSTRAINT_NAME, c.CHECK_CLAUSE, t.ENFORCED "
        "FROM information_schema.TABLE_CONSTRAINTS t "
        "JOIN information_schema.CHECK_CONSTRAINTS c ON c.CONSTRAINT_SCHEMA=t.CONSTRAINT_SCHEMA "
        "AND c.CONSTRAINT_NAME=t.CONSTRAINT_NAME WHERE t.TABLE_SCHEMA=%s "
        "AND t.TABLE_NAME=%s AND t.CONSTRAINT_TYPE='CHECK'",
        (namespace, table),
    )
    checks = {
        str(name): (str(expr), enforced == "YES") for name, expr, enforced in cursor.fetchall()
    }
    cursor.execute(
        "SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, "
        "REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL "
        "ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION",
        (namespace, table),
    )
    groups: dict[str, list[tuple[str, str, str, str]]] = {}
    for name, local, parent_schema, parent, remote in cursor.fetchall():
        groups.setdefault(str(name), []).append(
            (str(local), str(parent_schema), str(parent), str(remote))
        )
    foreign = {
        name: (tuple(r[0] for r in rows), rows[0][1], rows[0][2], tuple(r[3] for r in rows), True)
        for name, rows in groups.items()
    }
    return {"keys": keys, "checks": checks, "defaults": defaults, "foreign": foreign}


def verify_existing_constraints(
    conn: Any,
    *,
    dialect: str,
    schema_name: str | None,
    table: str,
    schema: pa.Schema,
    columns: Mapping[str, Mapping[str, Any]],
    primary_key: Sequence[str],
    options: Mapping[str, Any],
    first: pa.RecordBatch | None = None,
) -> None:
    """Refuse mismatched declarations before mutation; never commit the connection.

    CHECK/default comparison asks the server to canonicalize an empty temporary
    declaration. It requires temporary-table permission and drops the temporary
    table before returning. Foreign keys are compared from ordered catalog keys.
    """
    if dialect not in {"postgres", "mysql"}:
        raise ShapeError(f"constraint catalog verification does not support {dialect!r}")
    ddl = compile_constraints(
        options,
        columns=schema.names,
        table=table,
        dialect=dialect,
        schema_name=schema_name,
        table_prefix=options.get("table_prefix") or "",
    )
    cursor = conn.cursor()
    temporary: str | None = None
    created = False
    try:
        cursor.execute("SELECT current_schema()" if dialect == "postgres" else "SELECT DATABASE()")
        namespace = schema_name or cursor.fetchone()[0]
        if not namespace:
            raise ShapeError(f"existing table {table!r} has no current schema for verification")
        target = _sql.qualified(namespace, table, dialect)
        actual = (
            _postgres_catalog(cursor, target)
            if dialect == "postgres"
            else _mysql_catalog(cursor, namespace, table)
        )

        def refuse(name: str) -> None:
            raise ShapeError(
                f"existing table {table!r}: constraint {name!r} does not match; no data was loaded"
            )

        if primary_key and ("p", tuple(primary_key)) not in actual["keys"]:
            refuse("PRIMARY KEY (" + ", ".join(primary_key) + ")")
        for names in options.get("unique", []):
            wanted = tuple(names)
            if ("u", wanted) not in actual["keys"] and ("p", wanted) not in actual["keys"]:
                refuse("UNIQUE (" + ", ".join(names) + ")")
        for ref in options.get("foreign_keys", []):
            wanted_fk = (
                tuple(ref["columns"]),
                namespace,
                (options.get("table_prefix") or "") + ref["ref_table"],
                tuple(ref["ref_columns"]),
                True,
            )
            if actual["foreign"].get(ref["name"]) != wanted_fk:
                refuse(ref["name"])
        foreign = options.get("foreign_keys", [])
        if foreign:
            if dialect == "postgres":
                cursor.execute("SELECT current_setting('session_replication_role')")
                enforced = cursor.fetchone()[0] in ("origin", "local")
            else:
                cursor.execute("SELECT @@SESSION.foreign_key_checks")
                enforced = cursor.fetchone()[0] == 1
            if not enforced:
                refuse(foreign[0]["name"])
        for check in options.get("checks", []):
            found = actual["checks"].get(check["name"])
            if not found or not found[1]:
                refuse(check["name"])
        for column, value in options.get("defaults", {}).items():
            if column not in actual["defaults"] or (
                actual["defaults"][column] is None and value is not None
            ):
                refuse("DEFAULT " + column)
        if not options.get("checks") and not options.get("defaults"):
            return
        probe = "shape_check_" + uuid.uuid4().hex
        probe_schema = "pg_temp" if dialect == "postgres" else namespace
        temporary = _sql.qualified(probe_schema, probe, dialect)
        renamed = {}
        definitions = list(ddl.definitions)
        for index, check in enumerate(options.get("checks", [])):
            name = check["name"]
            alternate = "probe_" + uuid.uuid4().hex + "_" + str(index)
            renamed[name] = alternate
            old = "CONSTRAINT " + _sql.quote(name, dialect) + " CHECK "
            new = "CONSTRAINT " + _sql.quote(alternate, dialect) + " CHECK "
            definitions = [
                new + clause[len(old) :] if clause.startswith(old) else clause
                for clause in definitions
            ]
        create = _sql.create_table_sql(
            probe_schema,
            probe,
            schema,
            dialect,
            first=first,
            columns=columns,
            primary_key=primary_key,
            nested=options.get("nested", "native"),
            constraints=replace(
                ddl, definitions=tuple(definitions), foreign_keys=(), after_create=()
            ),
        )
        cursor.execute(create.replace("CREATE TABLE ", "CREATE TEMPORARY TABLE ", 1))
        created = True
        if dialect == "postgres":
            expected = _postgres_catalog(cursor, temporary)
            backslashes = False
        else:
            expected = _mysql_catalog(cursor, namespace, probe)
            cursor.execute("SELECT @@SESSION.sql_mode")
            backslashes = "NO_BACKSLASH_ESCAPES" not in str(cursor.fetchone()[0]).split(",")
            cursor.execute(f"SHOW CREATE TABLE {temporary}")
            expected["checks"] = {
                name: (expression, True)
                for name, expression in _mysql_checks(
                    str(cursor.fetchone()[1]), backslashes=backslashes
                ).items()
            }
        expected["checks"] = {
            name: expected["checks"].get(alternate) for name, alternate in renamed.items()
        }
        for check in options.get("checks", []):
            name = check["name"]
            found, wanted = actual["checks"].get(name), expected["checks"].get(name)
            if (
                not found
                or not wanted
                or not found[1]
                or (
                    _expression(found[0], backslashes=backslashes)
                    != _expression(wanted[0], backslashes=backslashes)
                )
            ):
                refuse(name)
        for column in options.get("defaults", {}):
            if actual["defaults"].get(column) != expected["defaults"].get(column):
                refuse("DEFAULT " + column)
    finally:
        try:
            if created and temporary is not None:
                cursor.execute(
                    f"DROP TABLE IF EXISTS {temporary}"
                    if dialect == "postgres"
                    else f"DROP TEMPORARY TABLE IF EXISTS {temporary}"
                )
        finally:
            cursor.close()
