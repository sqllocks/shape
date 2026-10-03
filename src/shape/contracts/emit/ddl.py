"""``shape contract emit --to ddl``: a v1 contract as ``CREATE TABLE`` statements.

The table comes from the SQL sink's table emitter, as in ``shape design`` (so quoting and type
spelling are the sink's, per dialect), with the contract's constraints added inside it.

| Contract rule | DDL |
|---|---|
| ``dtype`` | the column type (``integer`` BIGINT, ``float``, ``string`` VARCHAR, ``boolean``, ``date``, ``datetime`` timestamp) |
| ``nullable: false`` | ``NOT NULL`` (other columns are ``NULL``) |
| ``unique: true`` | ``CONSTRAINT UQ_<table>_<column> UNIQUE (col)``; ``UNIQUE NONCLUSTERED (col) NOT ENFORCED`` on Fabric Warehouse |
| ``min`` / ``max`` (numbers) | ``CHECK (col BETWEEN min AND max)``, or ``>= min`` / ``<= max`` for one bound |
| ``allowed_values`` | ``CHECK (col IN (...))`` (a null in the list is dropped: ``nullable`` decides) |
| ``pattern`` (a label) | ``CHECK (col ~ 'regex')`` on PostgreSQL, ``REGEXP`` on MySQL |
| ``required_columns`` | the column exists |
| ``allow_extra_columns: false`` | a table has no other columns |

Not expressible, and listed: ``max_null_rate``, ``distribution``, ``min_true_rate`` /
``max_true_rate``, ``no_placeholder``, ``row_count`` and the joint rules (``fd``, ``implies``,
``reference_pair``, ``max_implausible_rate``); ``pattern`` on T-SQL, which has no regular
expression test; ``min``, ``max`` and ``allowed_values`` on Fabric Warehouse, which has no
``CHECK`` constraint; a bound that is not a number; an unknown ``dtype`` or pattern label. A column
with no ``dtype`` is typed from its rules (a number bound gives ``float`` or ``integer``,
otherwise ``string``) and says so.
"""  # noqa: E501

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from typing import Any

from . import EmitError
from ._common import (
    JOINT_RULES,
    PATTERNS,
    Notes,
    split_rules,
    why_common,
    why_row_count,
)

DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")
_LOGICAL = {
    "boolean": "boolean",
    "integer": "integer",
    "float": "float",
    "string": "string",
    "date": "date",
    "datetime": "timestamp",
}
_NAME_LIMIT = {"tsql": 128, "postgres": 63, "mysql": 64}
_NO_REGEX = "T-SQL has no regular expression test for a CHECK constraint"
_NO_CHECK = "Fabric Warehouse has no CHECK constraint"


def check_dialect(dialect: str) -> str:
    if dialect not in DIALECTS:
        raise EmitError(f"unknown dialect {dialect!r}; choose one of {', '.join(DIALECTS)}")
    return dialect


def why_for(dialect: str) -> Callable[[str, Any], str | None]:
    fabric = dialect == "tsql-fabric-warehouse"

    def why(rule: str, value: Any) -> str | None:
        if rule == "max_null_rate":
            return "the share of nulls is a property of many rows; NULL / NOT NULL is per column"
        common = why_common(rule, value)
        if common or rule not in ("min", "max", "allowed_values", "pattern"):
            return common
        if fabric:
            return _NO_CHECK
        if rule == "pattern" and dialect == "tsql":
            return _NO_REGEX
        return None

    return why


def expressed_table(sub: Mapping[str, Any], dialect: str = "tsql") -> dict[str, Any]:
    """The rules of ``sub`` that DDL states in ``dialect`` (what the emitted text means)."""
    why = why_for(dialect)
    out: dict[str, Any] = {}
    for name, rules in sub.get("columns", {}).items():
        ok, _ = split_rules(rules, why)
        if ok:
            out.setdefault("columns", {})[name] = ok
    for key in ("required_columns", "allow_extra_columns"):
        if key in sub:
            out[key] = sub[key]
    return out


def _quote(name: str, dialect: str) -> str:
    """The sink's identifier quoting (as ``shape.design.ddl`` spells it)."""
    if dialect == "postgres":
        return '"' + name.replace('"', '""') + '"'
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return "[" + name.replace("]", "]]") + "]"


def _literal(value: Any, dialect: str) -> str:
    """An SQL literal of a JSON value, spelled as the SQL sink spells its INSERT values."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        if dialect == "postgres":
            return "TRUE" if value else "FALSE"
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else "NULL"
    text = str(value).replace("'", "''")
    if dialect == "mysql":
        text = text.replace("\\", "\\\\")
    return f"N'{text}'" if dialect == "tsql" else f"'{text}'"


def _constraint_name(prefix: str, table: str, column: str, dialect: str) -> str:
    name = f"{prefix}_{table}_{column}"
    limit = _NAME_LIMIT["tsql" if dialect == "tsql-fabric-warehouse" else dialect]
    if len(name) <= limit:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[: limit - 9]}_{digest}"


def _guess_logical(rules: Mapping[str, Any]) -> str:
    bounds = [rules[k] for k in ("min", "max") if k in rules]
    bounds = [b for b in bounds if isinstance(b, (int, float)) and not isinstance(b, bool)]
    if not bounds:
        return "string"
    return "integer" if all(isinstance(b, int) for b in bounds) else "float"


def _column_names(sub: Mapping[str, Any]) -> list[str]:
    names = list(sub.get("columns", {}))
    names += [c for c in sub.get("required_columns", []) if c not in names]
    return names


def table_ddl(table: str, sub: Mapping[str, Any], dialect: str, notes: Notes) -> str:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.plugins.host import default_host

    sink = default_host().get("shape.sinks", "sql")
    fabric = dialect == "tsql-fabric-warehouse"
    base = "tsql" if fabric else dialect
    why = why_for(dialect)

    def q(name: str) -> str:
        return _quote(name, dialect)

    def lit(value: Any) -> str:
        return _literal(value, base)

    columns = sub.get("columns", {})
    names = _column_names(sub)
    meta: dict[str, dict[str, Any]] = {}
    constraints: list[str] = []
    for name in names:
        rules = columns.get(name, {})
        ok, no = split_rules(rules, why)
        for rule, reason in no.items():
            notes.add(table, name, rule, reason)
        if ok.get("dtype") is None:
            logical = _guess_logical(rules)
            if "dtype" not in no:
                notes.add(
                    table,
                    name,
                    "dtype",
                    f"the contract gives no dtype; the column is typed {logical}",
                )
        else:
            logical = _LOGICAL[ok["dtype"]]
        info: dict[str, Any] = {"type": logical, "nullable": rules.get("nullable") is not False}
        texts = [v for v in ok.get("allowed_values", []) if isinstance(v, str)]
        if texts:
            info["max_length"] = max(255, *(len(v) for v in texts))
        meta[name] = info
        if ok.get("unique") is True:
            cname = q(_constraint_name("UQ", table, name, dialect))
            tail = f" NONCLUSTERED ({q(name)}) NOT ENFORCED" if fabric else f" ({q(name)})"
            constraints.append(f"CONSTRAINT {cname} UNIQUE{tail}")
        lo, hi = ok.get("min"), ok.get("max")
        if lo is not None or hi is not None:
            if lo is not None and hi is not None:
                expr = f"{q(name)} BETWEEN {lit(lo)} AND {lit(hi)}"
            elif lo is not None:
                expr = f"{q(name)} >= {lit(lo)}"
            else:
                expr = f"{q(name)} <= {lit(hi)}"
            cname = q(_constraint_name("CK", table, name + "_range", dialect))
            constraints.append(f"CONSTRAINT {cname} CHECK ({expr})")
        if "allowed_values" in ok:
            values = ", ".join(lit(v) for v in ok["allowed_values"] if v is not None)
            cname = q(_constraint_name("CK", table, name + "_values", dialect))
            constraints.append(f"CONSTRAINT {cname} CHECK ({q(name)} IN ({values}))")
        if "pattern" in ok:
            op = "~" if dialect == "postgres" else "REGEXP"
            cname = q(_constraint_name("CK", table, name + "_pattern", dialect))
            constraints.append(
                f"CONSTRAINT {cname} CHECK ({q(name)} {op} {lit(PATTERNS[ok['pattern']])})"
            )
    if "row_count" in sub:
        notes.add(
            table,
            None,
            "row_count",
            why_row_count(sub["row_count"]) or "a row count is not a constraint of a table",
        )
    for rule in JOINT_RULES:
        if rule in sub:
            notes.add(table, None, rule, "a database constraint relates columns of one row only")
    schema = pa.schema([pa.field(n, pa.string()) for n in names])
    text: str = sink._ddl(
        table, q(table), schema, meta, [], base, fabric, {"drop": False}, base == "tsql"
    )
    if constraints:
        head, tail = text.rsplit("\n);", 1)
        text = head + "".join(f",\n    {c}" for c in constraints) + "\n);" + tail
    return text


def render(tables: Mapping[str, Mapping[str, Any]], dialect: str, notes: Notes) -> str:
    from ._common import FORMAT, VERSION

    check_dialect(dialect)
    parts = [f"-- Contract emitted by Shape ({FORMAT}, version {VERSION}), dialect {dialect}"]
    for name, sub in tables.items():
        label = " ".join(name.splitlines())
        parts.append(f"-- table: {label}\n" + table_ddl(name, sub, dialect, notes))
    return "\n\n".join(parts) + "\n"
