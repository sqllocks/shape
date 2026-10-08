"""Known-answer datasets for DAX measures (``shape fabric known-answer``).

A generated dataset, the semantic model of its schema and, for every measure and every slice, the
exact value a correct DAX engine returns, so a model author can tell whether the measures return
the right numbers. ``answers.json`` (format ``shape-dax-answers``, version 1) holds the answers and
``queries.dax`` the queries to run in any DAX client; ``check-answers`` compares the client's
export with the answers (see :mod:`shape_fabric.answer_check`).

What is exact. Counts, minimums and maximums, and sums of integer and decimal columns are computed
with :class:`~decimal.Decimal` over the values of the written tables and compared exactly. Averages,
ratios and sums of float columns are exact fractions (kept as ``numerator/denominator``) rounded
half-even to ``places`` places (6), because a client rounds them in its own way.

A measure is sliced by a column of a table that filters the measure's table: the same table, or one
reached by following relationships from the child (the "many" side) to the parent (the "one" side).
The exporter's relationships filter in one direction only, so a measure of a parent table is not
sliced by an attribute of its child; such a pair is left out and listed under ``skipped``.

Planted totals (``--plant TABLE.COLUMN=VALUE``) move the values of a decimal column inside the
column's bounds so that the total is exactly ``VALUE`` (:func:`spread`). Nothing is random.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from shape.generation.schema import Column, GenSchema

from .semantic_model import (
    SemanticModelExporter,
    dax_column,
    dax_table,
    default_measure_specs,
)

FORMAT_ANSWERS = "shape-dax-answers"
ANSWERS_VERSION = 1
FORMAT_MEASURES = "shape-dax-measures"
MEASURES_VERSION = 1

PLACES = 6  # the places an average or a ratio is rounded to in answers.json
MAX_PLACES = 30
AGGREGATIONS = ("count", "sum", "avg", "min", "max", "ratio")
_NUMERIC_AGGREGATIONS = ("sum", "avg", "min", "max")
_NUMERIC_KINDS = ("integer", "decimal", "float")
_DECIMAL_DEFAULT = (18, 6)  # precision and scale of a decimal column that declares neither


class KnownAnswerError(ValueError):
    """The input is wrong: a measures file, a plant, a scale, an answers file."""


class PlantError(Exception):
    """A planted total cannot be met (the command exits 1)."""


def read_json(path: str | Path, what: str) -> Any:
    p = Path(path)
    if not p.is_file():
        raise KnownAnswerError(f"{what} {p} does not exist")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KnownAnswerError(f"{what} {p} is not valid JSON: {exc}") from exc


def check_document(doc: Any, fmt: str, newest: int, what: str, path: str | Path) -> None:
    """The ``format`` and the integer ``version`` of a persisted document; a newer version is
    refused with a message that names both versions."""
    if not isinstance(doc, dict) or doc.get("format") != fmt:
        found = doc.get("format") if isinstance(doc, dict) else type(doc).__name__
        raise KnownAnswerError(f"{path} is not a {fmt} file (its format is {found!r})")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise KnownAnswerError(f"{path}: the version of a {what} must be an integer of 1 or more")
    if version > newest:
        raise KnownAnswerError(
            f"{path}: this {what} is version {version}, newer than the version {newest} this "
            "Shape reads; upgrade Shape"
        )


# ---------------------------------------------------------------------------------------------
# names that reach DAX


def dax_string(text: str) -> str:
    """``text`` as a DAX string literal (a quote is doubled)."""
    return '"' + text.replace('"', '""') + '"'


def dax_measure(table: str, name: str) -> str:
    """A reference to a measure, qualified by its table (a closing bracket is doubled)."""
    return f"{dax_table(table)}[{name.replace(']', ']]')}]"


# ---------------------------------------------------------------------------------------------
# the tables: declared types, written as Parquet


def kind_of(arrow_type: pa.DataType) -> str:
    """``integer``, ``decimal``, ``float``, ``boolean``, ``timestamp``, ``date`` or ``string``."""
    if pa.types.is_integer(arrow_type):
        return "integer"
    if pa.types.is_decimal(arrow_type):
        return "decimal"
    if pa.types.is_floating(arrow_type):
        return "float"
    if pa.types.is_boolean(arrow_type):
        return "boolean"
    if pa.types.is_timestamp(arrow_type):
        return "timestamp"
    if pa.types.is_date(arrow_type):
        return "date"
    return "string"


def decimal_shape(column: Column) -> tuple[int, int]:
    """The precision and the scale a decimal column is written with."""
    if column.precision:
        return int(column.precision), int(column.scale or 0)
    return _DECIMAL_DEFAULT


def _to_decimal(arr: pa.ChunkedArray | pa.Array, column: Column, where: str) -> pa.ChunkedArray:
    precision, scale = decimal_shape(column)
    if pa.types.is_floating(arr.type):
        arr = pc.round(arr, scale)
    limit = 10.0 ** (precision - scale)
    bound = pc.max(pc.abs(arr.cast(pa.float64(), safe=False))).as_py()
    if bound is not None and not bound < limit:
        raise ValueError(
            f"{where}: a generated value does not fit DECIMAL({precision},{scale}) "
            f"(|value| must be below {limit:g})"
        )
    out = arr.cast(pa.decimal128(precision, scale), safe=False)
    return out if isinstance(out, pa.ChunkedArray) else pa.chunked_array([out])


def normalise(schema: GenSchema, tables: Mapping[str, pa.Table]) -> dict[str, pa.Table]:
    """The tables with the types the schema declares: ``decimal`` columns as ``decimal128``,
    integers as ``int64``, floats as ``float64`` and nanosecond timestamps as microseconds. The
    answers are computed from these, and these are what is written."""
    out: dict[str, pa.Table] = {}
    for tname, tdef in schema.tables.items():
        table = tables[tname]
        for cname, cdef in tdef.columns.items():
            if cname not in table.column_names:
                continue
            i = table.column_names.index(cname)
            arr = table.column(i)
            where = f"{tname}.{cname}"
            new = arr
            if cdef.type == "decimal":
                new = _to_decimal(arr, cdef, where)
            elif cdef.type == "integer" and not pa.types.is_integer(arr.type):
                new = pc.round(arr).cast(pa.int64(), safe=False)
            elif cdef.type == "integer":
                new = arr.cast(pa.int64())
            elif cdef.type == "float" and not pa.types.is_floating(arr.type):
                new = arr.cast(pa.float64(), safe=False)
            elif pa.types.is_timestamp(arr.type) and arr.type.unit == "ns":
                new = arr.cast(pa.timestamp("us"), safe=False)
            if new is not arr:
                table = table.set_column(i, table.schema.field(i).with_type(new.type), new)
        out[tname] = table
    return out


# ---------------------------------------------------------------------------------------------
# measures


@dataclass(frozen=True)
class Measure:
    name: str
    table: str
    kind: str  # count, sum, avg, min, max or ratio
    column: str | None
    expression: str
    format_string: str
    exact: bool
    numerator: str | None = None  # a ratio's operands, by measure name
    denominator: str | None = None
    column_kind: str | None = None

    @property
    def id(self) -> str:
        return f"{self.table}.{self.name}"

    @property
    def dax(self) -> str:
        return dax_measure(self.table, self.name)

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "table": self.table,
            "kind": self.kind,
            "column": self.column,
            "expression": self.expression,
            "exact": self.exact,
        }
        if self.kind == "ratio":
            doc["numerator"] = self.numerator
            doc["denominator"] = self.denominator
        return doc


def _is_exact(kind: str, column_kind: str | None) -> bool:
    if kind in ("count", "min", "max"):
        return True
    return kind == "sum" and column_kind != "float"


def _format_string(kind: str, column_kind: str | None) -> str:
    if kind == "count" or (kind in ("sum", "min", "max") and column_kind == "integer"):
        return "#,0"
    return "#,0.00"


def default_measures(schema: GenSchema, tables: Mapping[str, pa.Table]) -> list[Measure]:
    """The measures of the exporter, as :class:`Measure` objects."""
    out: list[Measure] = []
    for tdef in schema.tables.values():
        for spec in default_measure_specs(tdef):
            column = spec["column"]
            ckind = kind_of(tables[tdef.name].schema.field(column).type) if column else None
            out.append(
                Measure(
                    name=spec["name"],
                    table=spec["table"],
                    kind=spec["kind"],
                    column=column,
                    expression=spec["expression"],
                    format_string=spec["formatString"],
                    exact=_is_exact(spec["kind"], ckind),
                    column_kind=ckind,
                )
            )
    return out


def _split_attribute(schema: GenSchema, text: str, what: str) -> tuple[str, str]:
    for tname, tdef in schema.tables.items():
        if text.startswith(tname + "."):
            column = text[len(tname) + 1 :]
            if column in tdef.columns:
                return tname, column
    raise KnownAnswerError(f"{what} {text!r}: there is no such TABLE.COLUMN in the schema")


_MEASURE_KEYS = {"name", "table", "aggregation", "column", "numerator", "denominator"}


def load_measures(
    path: str | Path, schema: GenSchema, tables: Mapping[str, pa.Table]
) -> tuple[list[Measure], list[str]]:
    """The measures and the slices of a ``shape-dax-measures`` file."""
    doc = read_json(path, "measures file")
    check_document(doc, FORMAT_MEASURES, MEASURES_VERSION, "measures file", path)
    unknown = set(doc) - {"format", "version", "measures", "slice_by"}
    if unknown:
        raise KnownAnswerError(f"{path}: unknown keys {sorted(unknown)}")
    entries = doc.get("measures")
    if not isinstance(entries, list) or not entries:
        raise KnownAnswerError(f"{path}: 'measures' must be a list with at least one measure")
    names: dict[str, dict[str, Any]] = {}
    for n, entry in enumerate(entries, 1):
        where = f"{path}: measure {n}"
        if not isinstance(entry, dict):
            raise KnownAnswerError(f"{where} must be an object")
        extra = set(entry) - _MEASURE_KEYS
        if extra:
            raise KnownAnswerError(f"{where}: unknown keys {sorted(extra)}")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise KnownAnswerError(f"{where} needs a 'name'")
        if name in names:
            raise KnownAnswerError(f"{where}: the name {name!r} is used twice (names are unique)")
        names[name] = entry
    measures: list[Measure] = []
    for name, entry in names.items():
        measures.append(_measure_from_entry(path, name, entry, schema, tables, names))
    raw_slices = doc.get("slice_by", [])
    if not isinstance(raw_slices, list):
        raise KnownAnswerError(f"{path}: 'slice_by' must be a list of TABLE.COLUMN names")
    slices: list[str] = []
    for item in raw_slices:
        if not isinstance(item, str):
            raise KnownAnswerError(
                f"{path}: slice_by holds {item!r}, a TABLE.COLUMN name is a string"
            )
        _split_attribute(schema, item, "slice_by")
        if item in slices:
            raise KnownAnswerError(f"{path}: slice_by names {item!r} twice")
        slices.append(item)
    return measures, slices


def _measure_from_entry(
    path: str | Path,
    name: str,
    entry: dict[str, Any],
    schema: GenSchema,
    tables: Mapping[str, pa.Table],
    names: Mapping[str, dict[str, Any]],
) -> Measure:
    where = f"{path}: measure {name!r}"
    table = entry.get("table")
    if table not in schema.tables:
        raise KnownAnswerError(f"{where}: no table named {table!r}")
    agg = entry.get("aggregation")
    if agg not in AGGREGATIONS:
        raise KnownAnswerError(
            f"{where}: unknown aggregation {agg!r}; the aggregations are {', '.join(AGGREGATIONS)}"
        )
    column = entry.get("column")
    ref = dax_table(table)
    if agg == "ratio":
        if column is not None:
            raise KnownAnswerError(
                f"{where}: a ratio takes a numerator and a denominator, not a column"
            )
        operands = []
        for key in ("numerator", "denominator"):
            operand = entry.get(key)
            if operand not in names:
                raise KnownAnswerError(
                    f"{where}: the {key} {operand!r} is not a measure of this file"
                )
            if names[operand].get("aggregation") == "ratio":
                raise KnownAnswerError(
                    f"{where}: the {key} {operand!r} is a ratio; a ratio's operands are not ratios"
                )
            operands.append(operand)
        num, den = operands
        expression = (
            f"DIVIDE({dax_measure(names[num]['table'], num)}, "
            f"{dax_measure(names[den]['table'], den)})"
        )
        return Measure(name, table, "ratio", None, expression, "#,0.00", False, num, den)
    if "numerator" in entry or "denominator" in entry:
        raise KnownAnswerError(f"{where}: numerator and denominator belong to a ratio")
    if agg == "count":
        if column is not None:
            raise KnownAnswerError(f"{where}: a count counts the rows of the table, give no column")
        return Measure(name, table, "count", None, f"COUNTROWS({ref})", "#,0", True)
    if not isinstance(column, str) or column not in schema.tables[table].columns:
        raise KnownAnswerError(f"{where}: a {agg} needs a 'column' of {table}; got {column!r}")
    ckind = kind_of(tables[table].schema.field(column).type)
    if ckind not in _NUMERIC_KINDS:
        raise KnownAnswerError(
            f"{where}: {agg} wants a numeric column, {table}.{column} is {ckind}"
        )
    func = {"sum": "SUM", "avg": "AVERAGE", "min": "MIN", "max": "MAX"}[agg]
    expression = f"{func}({dax_column(table, column)})"
    return Measure(
        name,
        table,
        agg,
        column,
        expression,
        _format_string(agg, ckind),
        _is_exact(agg, ckind),
        column_kind=ckind,
    )


# ---------------------------------------------------------------------------------------------
# slices: which tables filter which


def _up_edges(schema: GenSchema) -> dict[str, list[tuple[str, str, str]]]:
    """``child -> [(parent, child column, parent column)]``: the many-to-one steps of the model,
    the first column of each relationship as the exporter writes it."""
    edges: dict[str, list[tuple[str, str, str]]] = {}
    for rel in schema.relationships:
        if rel.child == rel.parent or not rel.child_columns or not rel.parent_columns:
            continue
        edges.setdefault(rel.child, []).append(
            (rel.parent, rel.child_columns[0], rel.parent_columns[0])
        )
    return edges


def find_paths(
    schema: GenSchema, source: str, target: str
) -> list[list[tuple[str, str, str, str]]]:
    """Every path of many-to-one steps from ``source`` to ``target``, each step
    ``(child, parent, child column, parent column)``."""
    edges = _up_edges(schema)
    found: list[list[tuple[str, str, str, str]]] = []

    def walk(table: str, seen: tuple[str, ...], steps: list[tuple[str, str, str, str]]) -> None:
        if table == target:
            found.append(list(steps))
            return
        for parent, cc, pc_ in edges.get(table, []):
            if parent not in seen:
                walk(parent, (*seen, parent), [*steps, (table, parent, cc, pc_)])

    walk(source, (source,), [])
    return found


def default_slices(schema: GenSchema) -> list[str]:
    """The first text column of every dimension (a table that is the parent of a relationship)."""
    parents = {r.parent for r in schema.relationships if r.parent != r.child}
    out: list[str] = []
    for tname, tdef in schema.tables.items():
        if tname not in parents:
            continue
        for cname, cdef in tdef.columns.items():
            if cdef.type == "string" and cname not in tdef.primary_key and not cdef.is_foreign_key:
                out.append(f"{tname}.{cname}")
                break
    return out


# ---------------------------------------------------------------------------------------------
# canonical text of a slice value (the same function reads an expected and an observed value)


def key_text(value: Any, kind: str) -> str | None:
    """A slice value as the canonical text ``answers.json`` stores; ``None`` is the blank group."""
    if value is None:
        return None
    if kind == "integer":
        return str(int(value))
    if kind == "decimal":
        return format(Decimal(value), "f")
    if kind == "float":
        return format(Decimal(repr(float(value))).normalize(), "f")
    if kind == "boolean":
        return "true" if value else "false"
    if kind in ("timestamp", "date"):
        return value.isoformat()
    return str(value)


def key_form(text: str | None, kind: str, where: str) -> str | None:
    """Compare form of a stored or an observed key: equal forms are the same value."""
    if text is None:
        return None
    text = text.strip() if kind != "string" else text
    if kind == "string":
        return text if text != "" else None
    if text == "":
        return None
    try:
        if kind == "integer":
            d = Decimal(text)
            if not d.is_finite() or d != d.to_integral_value():
                raise InvalidOperation
            return str(int(d))
        if kind in ("decimal", "float"):
            d = Decimal(text)
            if not d.is_finite():
                raise InvalidOperation
            return format(d.normalize(), "f")
        if kind == "boolean":
            low = text.lower()
            if low in ("true", "1"):
                return "true"
            if low in ("false", "0"):
                return "false"
            raise ValueError
        parsed = dt.datetime.fromisoformat(text.replace("Z", ""))
        if kind == "date":
            return parsed.date().isoformat()
        return parsed.isoformat()
    except (InvalidOperation, ValueError):
        raise KnownAnswerError(f"{where}: {text!r} is not a valid {kind} value") from None


# ---------------------------------------------------------------------------------------------
# the answers

Result = Decimal | Fraction | None
_CTX_PREC = 120


def round_half_even(value: Fraction, places: int) -> str:
    """``value`` rounded half-even to ``places`` decimal places, as text with that many places."""
    scaled = value * 10**places
    q, r = divmod(scaled.numerator, scaled.denominator)
    twice = 2 * r
    if twice > scaled.denominator or (twice == scaled.denominator and q % 2 == 1):
        q += 1
    sign = "-" if q < 0 else ""
    digits = str(abs(q)).rjust(places + 1, "0")
    return f"{sign}{digits[:-places]}.{digits[-places:]}" if places else f"{sign}{digits}"


def _decimal_of(value: Any) -> Decimal:
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


@dataclass
class _Group:
    rows: int = 0
    n: int = 0
    total: Decimal = Decimal(0)
    low: Decimal | None = None
    high: Decimal | None = None


def _aggregate(
    measure: Measure, keys: Sequence[Any] | None, values: Sequence[Any] | None, rows: int
) -> dict[tuple[Any, ...], Result]:
    """The measure's value for every group of ``keys`` (``None``: one group, the whole table)."""
    groups: dict[tuple[Any, ...], _Group] = {}
    with localcontext() as ctx:
        ctx.prec = _CTX_PREC
        for i in range(rows):
            key = () if keys is None else (keys[i],)
            g = groups.get(key)
            if g is None:
                g = groups[key] = _Group()
            g.rows += 1
            if values is not None and values[i] is not None:
                d = _decimal_of(values[i])
                g.n += 1
                g.total += d
                if g.low is None or d < g.low:
                    g.low = d
                if g.high is None or d > g.high:
                    g.high = d
    out: dict[tuple[Any, ...], Result] = {}
    for key, g in groups.items():
        result: Result
        if measure.kind == "count":
            result = Decimal(g.rows)
        elif g.n == 0:
            result = None
        elif measure.kind == "sum":
            result = g.total if measure.exact else Fraction(g.total)
        elif measure.kind == "avg":
            result = Fraction(g.total) / g.n
        elif measure.kind == "min":
            result = g.low
        else:
            result = g.high
        out[key] = result
    return out


def _render(result: Result, places: int) -> str:
    assert result is not None
    if isinstance(result, Decimal):
        return format(result, "f")
    return round_half_even(result, places)


def _column_values(table: pa.Table, column: str) -> list[Any]:
    return table.column(column).to_pylist()


def _slice_keys(
    schema: GenSchema,
    tables: Mapping[str, pa.Table],
    path: list[tuple[str, str, str, str]],
    base: str,
    attribute: tuple[str, str],
    cache: dict[Any, Any],
) -> list[Any]:
    """The value of ``attribute`` for every row of ``base``, reached along ``path``; ``None`` for a
    row with no parent (a null or an unmatched key), as DAX shows it under the blank group."""
    rows: list[int | None] = list(range(tables[base].num_rows))
    for child, parent, cc, pcol in path:
        lookup_key = ("lookup", parent, pcol)
        lookup = cache.get(lookup_key)
        if lookup is None:
            values = _column_values(tables[parent], pcol)
            lookup = {}
            for index, value in enumerate(values):
                if value is None:
                    continue
                if value in lookup:
                    raise KnownAnswerError(
                        f"{parent}.{pcol} holds {value!r} twice: it cannot be the one side of a "
                        "relationship"
                    )
                lookup[value] = index
            cache[lookup_key] = lookup
        fk = _column_values(tables[child], cc)
        rows = [None if r is None else lookup.get(fk[r]) for r in rows]
    table, column = attribute
    values = _column_values(tables[table], column)
    kind = kind_of(tables[table].schema.field(column).type)
    return [None if r is None else key_text(values[r], kind) for r in rows]


def _sort_key(key: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple((k is not None, "" if k is None else k) for k in key)


def compute_answers(
    schema: GenSchema,
    tables: Mapping[str, pa.Table],
    measures: Sequence[Measure],
    slices: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """``(queries, skipped)`` of ``answers.json``: one query for the grand total and one for every
    slice, each holding every measure the slice can filter."""
    by_name = {m.name: m for m in measures}
    queries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    cache: dict[Any, Any] = {}
    cache_values: dict[tuple[str, str | None], dict[tuple[Any, ...], Result]] = {}

    def values_of(measure: Measure, keys: list[Any] | None, slice_name: str | None) -> Any:
        cache_key = (measure.id, slice_name)
        if cache_key not in cache_values:
            table = tables[measure.table]
            column_values = _column_values(table, measure.column) if measure.column else None
            cache_values[cache_key] = _aggregate(measure, keys, column_values, table.num_rows)
        return cache_values[cache_key]

    plan: list[tuple[str | None, str | None]] = [(None, None), *[(s, s) for s in slices]]
    for number, (attribute, _) in enumerate(plan, 1):
        qid = f"q{number:02d}"
        usable: list[Measure] = []
        keys_for: dict[str, list[Any] | None] = {}
        slice_kind: str | None = None
        if attribute is None:
            usable = list(measures)
            keys_for = {m.id: None for m in measures}
        else:
            stable, scolumn = _split_attribute(schema, attribute, "slice")
            slice_kind = kind_of(tables[stable].schema.field(scolumn).type)
            reasons: dict[str, str] = {}
            for m in measures:
                if m.kind == "ratio":
                    continue
                if m.table == stable:
                    keys_for[m.id] = [
                        key_text(v, slice_kind) for v in _column_values(tables[stable], scolumn)
                    ]
                    usable.append(m)
                    continue
                paths = find_paths(schema, m.table, stable)
                if not paths:
                    reasons[m.id] = (
                        f"no many-to-one relationship path from {m.table} to {stable}: "
                        f"{attribute} does not filter {m.table} (relationships filter in one "
                        "direction, from the one side to the many side)"
                    )
                elif len(paths) > 1:
                    reasons[m.id] = (
                        f"{len(paths)} relationship paths from {m.table} to {stable}: the slice "
                        "is ambiguous"
                    )
                else:
                    try:
                        keys_for[m.id] = _slice_keys(
                            schema, tables, paths[0], m.table, (stable, scolumn), cache
                        )
                        usable.append(m)
                    except KnownAnswerError as exc:
                        reasons[m.id] = str(exc)
            for m in measures:
                if m.kind != "ratio":
                    continue
                operands = [by_name[m.numerator or ""], by_name[m.denominator or ""]]
                if all(o.id in keys_for for o in operands):
                    usable.append(m)
                else:
                    lost = [o.id for o in operands if o.id not in keys_for]
                    reasons[m.id] = f"its operand {', '.join(lost)} is not sliced by {attribute}"
            order = {m.id: i for i, m in enumerate(measures)}
            usable.sort(key=lambda m: order[m.id])
            for m in measures:
                if m.id in reasons:
                    skipped.append({"slice": attribute, "measure": m.id, "reason": reasons[m.id]})
            if not usable:
                continue
        per_measure: dict[str, dict[tuple[Any, ...], Result]] = {}
        for m in usable:
            if m.kind == "ratio":
                continue
            per_measure[m.id] = values_of(m, keys_for[m.id], attribute)
        for m in usable:
            if m.kind != "ratio":
                continue
            num = per_measure[by_name[m.numerator or ""].id]
            den = per_measure[by_name[m.denominator or ""].id]
            ratio: dict[tuple[Any, ...], Result] = {}
            for key in set(num) | set(den):
                a, b = num.get(key), den.get(key)
                if a is None or b is None or b == 0:
                    continue  # DIVIDE gives a blank
                ratio[key] = Fraction(a) / Fraction(b)
            per_measure[m.id] = ratio
        all_keys = sorted({k for r in per_measure.values() for k in r}, key=_sort_key)
        rows: list[dict[str, Any]] = []
        for key in all_keys:
            row: dict[str, Any] = {"key": list(key), "values": {}}
            fractions: dict[str, str] = {}
            for m in usable:
                result = per_measure[m.id].get(key)
                if result is None:
                    continue
                row["values"][m.id] = _render(result, PLACES)
                if isinstance(result, Fraction):
                    fractions[m.id] = f"{result.numerator}/{result.denominator}"
            if fractions:
                row["fractions"] = fractions
            if row["values"]:
                rows.append(row)
        queries.append(
            {
                "id": qid,
                "slice": attribute,
                "slice_type": slice_kind,
                "measures": [m.id for m in usable],
                "rows": rows,
            }
        )
    return queries, skipped


# ---------------------------------------------------------------------------------------------
# planted totals


def spread(values: Sequence[int], target: int, low: int, high: int) -> list[int]:
    """``values`` (integers, in units of the column's last place) moved so that they add up to
    ``target``, every one inside ``[low, high]``.

    Deterministic and proportional: the whole difference is shared out in proportion to each
    value's room to move (up towards ``high`` when the total must grow, down towards ``low`` when
    it must shrink), whole units by largest remainder, ties to the earlier row. Nothing is random.
    A value that starts outside the bounds is first brought inside."""
    vals = [min(max(int(v), low), high) for v in values]
    n = len(vals)
    if not n * low <= target <= n * high:
        raise PlantError(f"a total of {target} is not between {n * low} and {n * high}")
    diff = target - sum(vals)
    if diff == 0:
        return vals
    sign = 1 if diff > 0 else -1
    room = [(high - v) if sign > 0 else (v - low) for v in vals]
    diff = abs(diff)
    total_room = sum(room)
    shares = [diff * r // total_room for r in room]
    remainder = diff - sum(shares)
    fractions = [diff * r % total_room for r in room]
    for i in sorted(range(n), key=lambda i: (-fractions[i], i))[:remainder]:
        shares[i] += 1
    return [v + sign * s for v, s in zip(vals, shares, strict=True)]


def parse_plant(spec: str, schema: GenSchema) -> tuple[str, str, Decimal]:
    """``TABLE.COLUMN=VALUE`` as ``(table, column, total)``; the column must be a decimal column."""
    left, eq, right = spec.partition("=")
    table, dot, column = left.partition(".")
    if not eq or not dot or not table or not column or not right.strip():
        raise KnownAnswerError(f"--plant wants TABLE.COLUMN=VALUE, got {spec!r}")
    if table not in schema.tables:
        raise KnownAnswerError(f"--plant {spec!r}: no table named {table!r}")
    cdef = schema.tables[table].columns.get(column)
    if cdef is None:
        raise KnownAnswerError(f"--plant {spec!r}: {table} has no column {column!r}")
    if cdef.type != "decimal":
        raise KnownAnswerError(
            f"--plant {spec!r}: {table}.{column} is a {cdef.type} column; only a decimal column "
            "can have a planted total"
        )
    try:
        total = Decimal(right.strip())
    except InvalidOperation:
        raise KnownAnswerError(f"--plant {spec!r}: {right.strip()!r} is not a number") from None
    if not total.is_finite():
        raise KnownAnswerError(f"--plant {spec!r}: {right.strip()!r} is not a finite number")
    return table, column, total


def _declared_bounds(column: Column) -> tuple[Decimal | None, Decimal | None]:
    """The bounds a generator declares for its values: ``min`` and ``max`` (of a distribution, as
    a key of the generator or of its ``params``), ``low`` and ``high`` of a uniform one, the
    smallest and largest ``values`` of a choice, or a constant."""
    gen = column.generator
    params: dict[str, Any] = {**gen, **(gen.get("params") or {})}
    lows: list[Decimal] = []
    highs: list[Decimal] = []

    def number(x: Any) -> Decimal | None:
        try:
            d = Decimal(str(x))
        except InvalidOperation:
            return None
        return d if d.is_finite() else None

    for key, bucket in (("min", lows), ("low", lows), ("max", highs), ("high", highs)):
        d = number(params[key]) if key in params else None
        if d is not None:
            bucket.append(d)
    values = gen.get("values")
    if isinstance(values, dict):
        values = list(values)
    if column.strategy in ("weighted_enum", "choice") and isinstance(values, list):
        nums = [n for n in (number(v) for v in values) if n is not None]
        if nums and len(nums) == len(values):
            lows.append(min(nums))
            highs.append(max(nums))
    if column.strategy == "constant" and "value" in gen:
        d = number(gen["value"])
        if d is not None:
            lows.append(d)
            highs.append(d)
    return (max(lows) if lows else None, min(highs) if highs else None)


def column_bounds(
    column: Column, precision: int, scale: int, observed_min: int | None
) -> tuple[int, int]:
    """The least and the greatest value a planted column may hold, in units of its last place:
    the generator's declared bounds, inside what the column's precision can store; a generator
    that declares no lower bound lets the column go down to 0 (to the type's limit when the
    generated values already go below 0)."""
    limit = 10**precision - 1
    unit = Decimal(10) ** scale
    declared_low, declared_high = _declared_bounds(column)
    low = -limit if (observed_min is not None and observed_min < 0) else 0
    high = limit
    if declared_low is not None:
        low = max(-limit, math.ceil(declared_low * unit))
    if declared_high is not None:
        high = min(limit, math.floor(declared_high * unit))
    if low > high:
        raise PlantError(f"{column.name}: the generator's bounds leave no value a plant could use")
    return low, high


def _units(value: Decimal | None, scale: int) -> int | None:
    return None if value is None else int(value.scaleb(scale))


def plant_total(table: pa.Table, table_name: str, column: Column, total: Decimal) -> pa.Table:
    """``table`` with the values of ``column`` moved, inside the generator's bounds, so that they
    add up to ``total``. Null values stay null and every other column is untouched."""
    index = table.column_names.index(column.name)
    arr = table.column(index)
    precision, scale = arr.type.precision, arr.type.scale
    name = f"{table_name}.{column.name}"
    requested = format(total, "f")
    scaled = total.scaleb(scale)
    if scaled != scaled.to_integral_value():
        raise PlantError(
            f"cannot plant {name} = {requested}: the column has {scale} decimal places, so a "
            f"total with more places can never be met"
        )
    units = [_units(v, scale) for v in arr.to_pylist()]
    present = [u for u in units if u is not None]
    low, high = column_bounds(column, precision, scale, min(present) if present else None)
    n = len(present)
    reach_low, reach_high = Decimal(n * low).scaleb(-scale), Decimal(n * high).scaleb(-scale)
    if not n * low <= int(scaled) <= n * high:
        raise PlantError(
            f"cannot plant {name} = {requested}: {n} values between "
            f"{Decimal(low).scaleb(-scale):.{scale}f} and {Decimal(high).scaleb(-scale):.{scale}f} "
            f"can only add up to between {reach_low:.{scale}f} and {reach_high:.{scale}f}"
        )
    moved = iter(spread(present, int(scaled), low, high))
    new_units = [None if u is None else next(moved) for u in units]
    values = [None if u is None else Decimal(u).scaleb(-scale) for u in new_units]
    new = pa.array(values, type=pa.decimal128(precision, scale))
    return table.set_column(index, table.schema.field(index), pa.chunked_array([new]))


# ---------------------------------------------------------------------------------------------
# queries.dax


def queries_dax(answers: Mapping[str, Any]) -> str:
    """One ``EVALUATE SUMMARIZECOLUMNS`` for every query of ``answers``."""
    by_id = {m["id"]: m for m in answers["measures"]}
    blocks: list[str] = []
    for q in answers["queries"]:
        title = f"// {q['id']}" + (f" {q['slice']}" if q["slice"] else "")
        parts: list[str] = []
        order = ""
        if q["slice"]:
            ref = dax_column(*_slice_parts(answers, q["slice"]))
            parts.append(f"    {ref}")
            order = f"\nORDER BY {ref}"
        for mid in q["measures"]:
            m = by_id[mid]
            parts.append(f"    {dax_string(mid)}, {dax_measure(m['table'], m['name'])}")
        blocks.append(
            "\n".join([title, "EVALUATE", "SUMMARIZECOLUMNS(", ",\n".join(parts), ")" + order])
        )
    return "\n\n".join(blocks) + "\n"


def _slice_parts(answers: Mapping[str, Any], attribute: str) -> tuple[str, str]:
    tables = answers["tables"]
    for tname in tables:
        if attribute.startswith(tname + "."):
            return tname, attribute[len(tname) + 1 :]
    raise KnownAnswerError(f"the slice {attribute!r} names no table of the answers")


# ---------------------------------------------------------------------------------------------
# answers.json


def load_answers(path: str | Path) -> dict[str, Any]:
    """An ``answers.json``, read strictly: its format and version, and the shape of everything
    ``check-answers`` reads."""
    doc = read_json(path, "answers file")
    check_document(doc, FORMAT_ANSWERS, ANSWERS_VERSION, "answers file", path)

    def bad(what: str) -> KnownAnswerError:
        return KnownAnswerError(f"{path}: {what}")

    places = doc.get("places")
    if isinstance(places, bool) or not isinstance(places, int) or not 0 <= places <= MAX_PLACES:
        raise bad(f"'places' must be an integer from 0 to {MAX_PLACES}")
    if not isinstance(doc.get("tables"), dict):
        raise bad("'tables' must be an object")
    measures = doc.get("measures")
    if not isinstance(measures, list):
        raise bad("'measures' must be a list")
    ids: dict[str, dict[str, Any]] = {}
    for m in measures:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or "exact" not in m:
            raise bad("every measure needs an 'id' and 'exact'")
        ids[m["id"]] = m
    queries = doc.get("queries")
    if not isinstance(queries, list) or not queries:
        raise bad("'queries' must be a list with at least one query")
    seen: set[str] = set()
    for q in queries:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or q["id"] in seen:
            raise bad("every query needs a unique 'id'")
        seen.add(q["id"])
        if not isinstance(q.get("measures"), list) or any(x not in ids for x in q["measures"]):
            raise bad(f"query {q['id']}: 'measures' must name measures of the file")
        if q.get("slice") is not None and not isinstance(q.get("slice_type"), str):
            raise bad(f"query {q['id']}: a slice needs a 'slice_type'")
        width = 0 if q.get("slice") is None else 1
        if not isinstance(q.get("rows"), list):
            raise bad(f"query {q['id']}: 'rows' must be a list")
        for row in q["rows"]:
            if not (
                isinstance(row, dict)
                and isinstance(row.get("key"), list)
                and len(row["key"]) == width
                and isinstance(row.get("values"), dict)
            ):
                raise bad(f"query {q['id']}: a row needs a 'key' of {width} and 'values'")
            for mid, text in row["values"].items():
                if mid not in q["measures"] or not isinstance(text, str):
                    raise bad(f"query {q['id']}: a value for {mid!r} is not a measure's text")
                try:
                    Decimal(text)
                except InvalidOperation:
                    raise bad(f"query {q['id']}: {text!r} is not a number") from None
            for frac in (row.get("fractions") or {}).values():
                top, slash, bottom = str(frac).partition("/")
                if (
                    not slash
                    or not top.lstrip("-").isdigit()
                    or not bottom.isdigit()
                    or bottom == "0"
                ):
                    raise bad(f"query {q['id']}: {frac!r} is not a fraction")
    return doc


# ---------------------------------------------------------------------------------------------
# the build


@dataclass
class Built:
    directory: Path
    answers: dict[str, Any]
    tables: dict[str, int]
    measures: int
    queries: int
    plants: list[dict[str, str]] = field(default_factory=list)


def dump_json(doc: Any) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def write_model(schema: GenSchema, path: Path, measures: Sequence[Measure] | None) -> None:
    """``model.bim``: the exporter's model; with ``measures`` those replace the defaults."""
    given: dict[str, list[dict[str, Any]]] | None = None
    if measures is not None:
        given = {}
        for m in measures:
            given.setdefault(m.table, []).append(
                {"name": m.name, "expression": m.expression, "formatString": m.format_string}
            )
    tom = SemanticModelExporter().to_dict(schema, measures=given)
    path.write_text(dump_json(tom), encoding="utf-8")


def build(
    schema: GenSchema,
    directory: str | Path,
    *,
    tables: Mapping[str, pa.Table],
    scale: str,
    seed: int,
    measures_file: str | Path | None = None,
    plants: Iterable[str] = (),
    progress: Callable[[str], None] | None = None,
) -> Built:
    """Plant the totals, compute the answers and write ``data/``, ``model.bim``, ``answers.json``
    and ``queries.dax`` into ``directory``. Nothing is written when an input is wrong or a plant
    cannot be met."""
    typed = normalise(schema, tables)
    planned: list[tuple[str, str, Decimal]] = []
    for spec in plants:
        item = parse_plant(spec, schema)
        if any(p[:2] == item[:2] for p in planned):
            raise KnownAnswerError(f"--plant names {item[0]}.{item[1]} twice")
        planned.append(item)
    for tname, cname, total in planned:
        typed[tname] = plant_total(typed[tname], tname, schema.tables[tname].columns[cname], total)
    if measures_file is not None:
        measures, slices = load_measures(measures_file, schema, typed)
        custom: list[Measure] | None = measures
    else:
        measures, slices = default_measures(schema, typed), default_slices(schema)
        custom = None
    queries, skipped = compute_answers(schema, typed, measures, slices)
    answers: dict[str, Any] = {
        "format": FORMAT_ANSWERS,
        "version": ANSWERS_VERSION,
        "source": {"domain": schema.model.domain, "scale": scale, "seed": seed},
        "places": PLACES,
        "tables": {name: table.num_rows for name, table in typed.items()},
        "plants": [
            {"table": t, "column": c, "total": format(total, "f")} for t, c, total in planned
        ],
        "measures": [m.document() for m in measures],
        "queries": queries,
        "skipped": skipped,
    }
    for name in typed:
        if Path(name).name != name or name in ("", ".", ".."):
            raise KnownAnswerError(f"the table name {name!r} cannot be a file name")
    out = Path(directory)
    (out / "data").mkdir(parents=True, exist_ok=True)
    for name, table in typed.items():
        pq.write_table(table, out / "data" / f"{name}.parquet")
        if progress:
            progress(name)
    write_model(schema, out / "model.bim", custom)
    (out / "queries.dax").write_text(queries_dax(answers), encoding="utf-8")
    (out / "answers.json").write_text(dump_json(answers), encoding="utf-8")
    return Built(
        out,
        answers,
        answers["tables"],
        len(measures),
        len(queries),
        answers["plants"],
    )
