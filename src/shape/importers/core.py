"""The shared mapping core of the schema importers (W5-06).

Every format module reads its source into the small intermediate model below (tables, columns with
the constraints a generator can use, foreign keys) and records what it did with each source element
in a :class:`Report`. :func:`build_spec` then turns the model into a generation spec through
:class:`~shape.generation.spec_edit.SpecDocument`, choosing each column's strategy from its type
and constraints the way the DDL import does (``shape from-ddl``): a sequence for an integer key,
a ``foreign_key`` for a reference, a ``choice`` for an ``enum``, a bounded ``distribution`` for a
number with ``minimum`` and ``maximum``, a ``faker`` provider for an ``email`` and so on.

The report is a persisted format: ``{"format": "shape-import-report", "version": 1, ...}``.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.errors import ShapeError
from shape.generation.ddl import NAME_EXACT, NAME_SUFFIX
from shape.generation.spec_edit import SpecDocument

REPORT_FORMAT = "shape-import-report"
REPORT_VERSION = 1

#: The logical column types of a generation spec the importers produce.
TYPES = ("integer", "string", "decimal", "float", "boolean", "date", "timestamp", "time", "uuid")

_UNSAFE = re.compile(r"[\x00-\x1f/\\:.]")


class ImportFormatError(ShapeError):
    """The input is malformed. ``file``, ``line`` (where the format has lines) and ``element`` say
    where."""

    def __init__(
        self,
        message: str,
        *,
        file: str | None = None,
        line: int | None = None,
        element: str | None = None,
    ) -> None:
        self.message = message
        self.file = file
        self.line = line
        self.element = element
        where = ""
        if file:
            where = f"{file}:{line}: " if line is not None else f"{file}: "
        elif line is not None:
            where = f"line {line}: "
        what = f"{element}: " if element else ""
        super().__init__(f"{where}{what}{message}")

    def located(self, file: str) -> ImportFormatError:
        """The same error with ``file`` filled in, when the place that raised it had none."""
        if self.file is not None:
            return self
        return ImportFormatError(self.message, file=file, line=self.line, element=self.element)


class StrictImportError(ShapeError):
    """``--strict`` and at least one source element was not imported. No spec is written."""

    def __init__(self, report: Report) -> None:
        self.report = report
        first = report.not_imported[0]
        super().__init__(
            f"{len(report.not_imported)} element(s) not imported (--strict): "
            f"{first['element']}: {first['reason']}"
        )


# ---- the report ------------------------------------------------------------------------------


@dataclass
class Report:
    """What became of each source element. ``imported`` entries say what the element became;
    ``not_imported`` entries say why it was left out or approximated."""

    source_file: str
    source_format: str
    imported: list[dict[str, str]] = field(default_factory=list)
    not_imported: list[dict[str, str]] = field(default_factory=list)

    def mapped(self, element: str, kind: str, became: str) -> None:
        entry = {"element": element, "kind": kind, "became": became}
        if entry not in self.imported:
            self.imported.append(entry)

    def skipped(self, element: str, kind: str, reason: str) -> None:
        entry = {"element": element, "kind": kind, "reason": reason}
        if entry not in self.not_imported:
            self.not_imported.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": REPORT_FORMAT,
            "version": REPORT_VERSION,
            "source": {"file": self.source_file, "format": self.source_format},
            "summary": {
                "imported": len(self.imported),
                "not_imported": len(self.not_imported),
            },
            "imported": self.imported,
            "not_imported": self.not_imported,
        }

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"


# ---- the intermediate model ------------------------------------------------------------------


@dataclass
class ImpColumn:
    name: str
    type: str = "string"
    nullable: bool = True
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    pattern: str | None = None
    format: str | None = None
    precision: int | None = None
    scale: int | None = None
    constant: Any = None
    has_constant: bool = False
    source: str = ""
    kind: str = "column"


@dataclass
class ImpForeignKey:
    column: str
    ref_table: str
    ref_column: str
    cardinality: str = "one_to_many"


@dataclass
class ImpTable:
    name: str
    columns: list[ImpColumn] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ImpForeignKey] = field(default_factory=list)
    source: str = ""

    def column(self, name: str) -> ImpColumn | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class ImpModel:
    name: str
    tables: list[ImpTable] = field(default_factory=list)

    def table(self, name: str) -> ImpTable | None:
        return next((t for t in self.tables if t.name == name), None)


def clean_name(name: str) -> str:
    """``name`` as a table or column name of a spec: a path separator, a dot or a control
    character becomes ``_`` (a table name is also a file name, and ``table.column`` is how a
    reference is written)."""
    cleaned = _UNSAFE.sub("_", str(name)).strip()
    return cleaned or "_"


def unique_name(base: str, used: set[str]) -> str:
    """``base``, or ``base_2``, ``base_3`` ... when it is taken; the name is added to ``used``."""
    name = base
    n = 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


def add_key(table: ImpTable, name: str = "id") -> str:
    """Give ``table`` a generated integer key when it has none; returns the key column."""
    if table.primary_key:
        return table.primary_key[0]
    used = {c.name for c in table.columns}
    key = unique_name(name, used)
    table.columns.insert(0, ImpColumn(key, "integer", nullable=False, source=table.source))
    table.primary_key = [key]
    return key


# ---- keys ------------------------------------------------------------------------------------


def choose_key(table: ImpTable) -> None:
    """The key of ``table``: a column named ``id`` or ``<table>_id`` of a key type, else a
    generated integer ``id`` column."""
    if table.primary_key:
        return
    fk_cols = {f.column for f in table.foreign_keys}
    for want in ("id", f"{table.name}_id", f"{table.name}id"):
        col = next((c for c in table.columns if c.name.lower() == want.lower()), None)
        if (
            col is not None
            and col.name not in fk_cols
            and col.type in ("integer", "string", "uuid")
            and col.enum is None
        ):
            table.primary_key = [col.name]
            col.nullable = False
            return
    used = {c.name for c in table.columns}
    key = unique_name("id", used)
    table.columns.insert(
        0, ImpColumn(key, "integer", nullable=False, source=table.source, kind="generated column")
    )
    table.primary_key = [key]


def finish_model(
    model: ImpModel, links: list[tuple[ImpTable, ImpForeignKey, ImpTable]]
) -> ImpModel:
    """Choose every table's key, then point each foreign key of ``links`` (owner table, key,
    referenced table) at the referenced table's key, taking the key's type."""
    for table in model.tables:
        choose_key(table)
    for owner, fk, target in links:
        fk.ref_column = target.primary_key[0]
        col = owner.column(fk.column)
        key = target.column(fk.ref_column)
        if col is not None and key is not None:
            col.type = key.type if key.type in ("integer", "uuid", "string") else "integer"
            if key.type == "string":
                col.max_length = key.max_length
    return model


def break_cycles(model: ImpModel, report: Report | None = None) -> None:
    """The generator needs tables in an order where every table follows the ones it points at, so
    a cycle of foreign keys (``A`` refers to ``B`` which refers to ``A``) cannot be generated.
    Each cycle is broken at one foreign key, preferring a nullable reference between named
    schemas to the link of a child table to its parent; that column becomes a plain integer
    column, and the report says so (it is not imported as a key)."""
    by_name = {t.name: t for t in model.tables}
    while True:
        cycle = _find_cycle(model, by_name)
        if cycle is None:
            return

        def rank(edge: tuple[ImpTable, ImpForeignKey]) -> tuple[int, int]:
            col = edge[0].column(edge[1].column)
            return (
                0 if col is not None and col.kind == "reference" else 1,
                0 if col is not None and col.nullable else 1,
            )

        owner, fk = min(cycle, key=rank)
        owner.foreign_keys.remove(fk)
        col = owner.column(fk.column)
        if col is not None:
            col.type = "integer"
            col.max_length = None
            if report is not None:
                path = " -> ".join([e[0].name for e in cycle] + [cycle[0][0].name])
                report.skipped(
                    col.source or f"{owner.name}.{col.name}",
                    "foreign key",
                    f"{owner.name}.{col.name} -> {fk.ref_table} closes a cycle ({path}): "
                    "imported as a plain integer column",
                )


def _find_cycle(
    model: ImpModel, by_name: dict[str, ImpTable]
) -> list[tuple[ImpTable, ImpForeignKey]] | None:
    state: dict[str, int] = {}
    stack: list[tuple[ImpTable, ImpForeignKey]] = []

    def visit(table: ImpTable) -> list[tuple[ImpTable, ImpForeignKey]] | None:
        state[table.name] = 1
        for fk in table.foreign_keys:
            target = by_name.get(fk.ref_table)
            if target is None or target is table:
                continue
            stack.append((table, fk))
            if state.get(target.name) == 1:
                start = next(i for i, (t, _) in enumerate(stack) if t is target)
                return stack[start:]
            if target.name not in state:
                found = visit(target)
                if found is not None:
                    return found
            stack.pop()
        state[table.name] = 2
        return None

    for t in model.tables:
        if t.name not in state:
            found = visit(t)
            if found is not None:
                return found
    return None


# ---- strategy choice -------------------------------------------------------------------------

_DEFAULT_INT = (1, 10000)
_UNIFORM_DATE: dict[str, Any] = {
    "strategy": "temporal",
    "pattern": "uniform",
    "range_ref": "model.date_range",
}
_FAKER_FORMATS = {"email": "email", "uri": "url", "url": "url", "ipv4": "ipv4", "hostname": "ipv4"}
FORMAT_TYPES = {"date-time": "timestamp", "date": "date", "uuid": "uuid", "time": "time"}
# Only the classes a pattern token can produce: ``[A-Z0-9]{n}`` is ``{random:n}`` and ``\d{n}`` is
# ``{seq:n}`` (a sequence of digits).
_PATTERN_PART = re.compile(
    r"(?P<random>\[A-Z0-9\]\{(?P<r>\d{1,3})\})"
    r"|(?P<digits>(?:\\d|\[0-9\])\{(?P<d>\d{1,3})\})"
    r"|(?P<literal>[A-Za-z0-9 _\-]+)"
)


def pattern_to_format(pattern: str) -> str | None:
    """A ``pattern`` strategy format for a simple regular expression (literals, ``\\d{n}``,
    ``[0-9]{n}`` and ``[A-Z0-9]{n}``, anchored or not), or ``None`` when the expression has
    anything else in it."""
    body = pattern
    if body.startswith("^"):
        body = body[1:]
    if body.endswith("$"):
        body = body[:-1]
    out: list[str] = []
    pos = 0
    tokens = 0
    while pos < len(body):
        m = _PATTERN_PART.match(body, pos)
        if m is None:
            return None
        if m.group("random"):
            out.append("{random:" + m.group("r") + "}")
            tokens += 1
        elif m.group("digits"):
            out.append("{seq:" + m.group("d") + "}")
            tokens += 1
        else:
            out.append(m.group("literal"))
        pos = m.end()
    if not tokens:
        return None
    return "".join(out)


def _bounds(col: ImpColumn, integer: bool) -> tuple[float, float]:
    lo, hi = col.minimum, col.maximum
    if lo is not None and hi is not None and lo > hi:
        raise ImportFormatError(
            f"minimum {lo:g} is greater than maximum {hi:g}", element=col.source or col.name
        )
    if lo is None and hi is None:
        return (float(_DEFAULT_INT[0]), float(_DEFAULT_INT[1]))
    if lo is None:
        assert hi is not None
        lo = min(1.0, hi) if integer else hi - 10000.0
    if hi is None:
        hi = lo + 10000.0
    return lo, hi


def _faker_text(length: int | None) -> dict[str, Any]:
    n = 50 if length is None else length
    if n < 5:
        return {"strategy": "pattern", "format": "{random:" + str(max(n, 1)) + "}"}
    return {"strategy": "faker", "provider": "text", "args": {"max_nb_chars": min(n, 200)}}


def choose_generator(
    table: ImpTable, col: ImpColumn, report: Report | None = None
) -> dict[str, Any]:
    """The generator of ``col``, from its type and constraints. A constraint that cannot be
    honored is recorded in ``report`` as not imported (the column still gets a generator)."""
    where = col.source or f"{table.name}.{col.name}"

    def skipped(kind: str, reason: str) -> None:
        if report is not None:
            report.skipped(where, kind, reason)

    fk = next((f for f in table.foreign_keys if f.column == col.name), None)
    if fk is not None:
        if fk.ref_table == table.name:
            return {
                "strategy": "self_referencing",
                "pk_column": fk.ref_column,
                "levels": 3,
                "root_count": 8,
            }
        return {
            "strategy": "foreign_key",
            "ref": f"{fk.ref_table}.{fk.ref_column}",
            "distribution": "pareto",
        }
    is_key = col.name in table.primary_key and len(table.primary_key) == 1
    if col.has_constant:
        return {"strategy": "constant", "value": copy.deepcopy(col.constant)}
    if col.enum:
        return {"strategy": "choice", "values": list(col.enum)}
    if is_key:
        if col.type == "integer":
            return {"strategy": "sequence", "start": 1}
        if col.type == "uuid":
            return {"strategy": "uuid"}
        width = 8 if col.max_length is None else max(1, min(col.max_length, 8))
        return {"strategy": "pattern", "format": "{seq:" + str(width) + "}"}
    t = col.type
    if t == "integer":
        lo, hi = _bounds(col, True)
        return {
            "strategy": "distribution",
            "distribution": "uniform",
            "min": int(lo),
            "max": int(hi),
            "output_type": "int64",
        }
    if t in ("decimal", "float"):
        gen: dict[str, Any]
        if col.minimum is None and col.maximum is None:
            gen = {
                "strategy": "distribution",
                "distribution": "normal",
                "mean": 100,
                "std_dev": 50,
                "min": 0,
            }
        else:
            lo, hi = _bounds(col, False)
            gen = {"strategy": "distribution", "distribution": "uniform", "min": lo, "max": hi}
        if t == "decimal":
            gen["output_type"] = "decimal"
        return gen
    if t == "boolean":
        return {"strategy": "weighted_enum", "values": {"true": 0.85, "false": 0.15}}
    if t == "timestamp":
        return {**_UNIFORM_DATE, "output_type": "timestamp"}
    if t in ("date", "time"):
        return dict(_UNIFORM_DATE)
    if t == "uuid":
        return {"strategy": "uuid"}
    # strings
    if col.pattern is not None:
        fmt = pattern_to_format(col.pattern)
        if fmt is not None:
            return {"strategy": "pattern", "format": fmt}
        skipped("pattern", f"pattern {col.pattern!r} is not a simple pattern: imported as text")
    if col.format in _FAKER_FORMATS:
        return {"strategy": "faker", "provider": _FAKER_FORMATS[col.format]}
    if col.format is not None and col.format not in FORMAT_TYPES:
        skipped("format", f"format {col.format!r} is not known: imported as text")
    named = NAME_EXACT.get(col.name.lower())
    if named is not None and col.max_length is None:
        return copy.deepcopy(named)
    if col.max_length is None:
        for suffix, gen_or_flag in NAME_SUFFIX.items():
            if col.name.lower().endswith(suffix) and isinstance(gen_or_flag, dict):
                return copy.deepcopy(gen_or_flag)
    return _faker_text(col.max_length)


# ---- the spec --------------------------------------------------------------------------------


def _describe(gen: dict[str, Any]) -> str:
    detail = gen.get("distribution") or gen.get("provider") or gen.get("ref")
    return f"{gen['strategy']} {detail}" if detail else str(gen["strategy"])


def _is_nullable_by_default(table: ImpTable, col: ImpColumn) -> bool:
    return col.nullable and col.name not in table.primary_key


def build_spec(model: ImpModel, report: Report | None = None) -> SpecDocument:
    """The generation spec for ``model``, written through :class:`SpecDocument` and checked
    against the published schema and the whole-spec rules. Raises
    :class:`~shape.generation.spec_edit.SpecError` if it is not valid (a bug in an importer)."""
    doc = SpecDocument.from_dict(
        {
            "schema_version": 1,
            "model": {
                "name": f"{clean_name(model.name)}_import",
                "description": "Imported by shape import-schema",
                "domain": "custom",
                "schema_mode": "3nf",
                "locale": "en_US",
                "seed": 42,
                "date_range": {"start": "2024-01-01", "end": "2025-12-31"},
            },
            "tables": {},
            "relationships": [],
            "business_rules": [],
        }
    )
    if not model.tables:
        raise ImportFormatError("the input defines no table")
    break_cycles(model, report)
    for t in model.tables:
        doc.add_table(t.name, primary_key=list(t.primary_key))
        if report is not None and t.source:
            report.mapped(t.source, "table", f"table {t.name}")
        for c in t.columns:
            props: dict[str, Any] = {"nullable": _is_nullable_by_default(t, c)}
            props["null_rate"] = 0.05 if props["nullable"] else 0.0
            if c.max_length is not None:
                props["max_length"] = c.max_length
            if c.precision is not None:
                props["precision"] = c.precision
            if c.scale is not None:
                props["scale"] = c.scale
            gen = choose_generator(t, c, report)
            doc.add_column(t.name, c.name, c.type, gen, **props)
            if report is not None and c.source:
                report.mapped(c.source, c.kind, f"{t.name}.{c.name} ({c.type}, {_describe(gen)})")
    relationships: list[dict[str, Any]] = []
    for t in model.tables:
        for fk in t.foreign_keys:
            col = t.column(fk.column)
            relationships.append(
                {
                    "name": f"fk_{t.name}_{fk.column}",
                    "parent": fk.ref_table,
                    "child": t.name,
                    "parent_columns": [fk.ref_column],
                    "child_columns": [fk.column],
                    "type": "self_referencing" if fk.ref_table == t.name else fk.cardinality,
                    "cardinality": {},
                    "optional": bool(col.nullable) if col else False,
                }
            )
    doc.set("/relationships", relationships)
    scales: dict[str, dict[str, int]] = {"small": {}, "medium": {}, "large": {}}
    for t in model.tables:
        base = 2500 if t.foreign_keys else 1000
        for label, factor in (("small", 1), ("medium", 10), ("large", 100)):
            scales[label][t.name] = base * factor
    doc.set(
        "/generation",
        {"scale": "small", "scales": scales, "derived_counts": {}, "output": {}},
    )
    doc.raise_for_errors()
    return doc


def write_outputs(
    doc: SpecDocument | None, report: Report, out: str | Path | None, report_path: str | Path | None
) -> None:
    """Write the spec (when there is one) and the report."""
    if doc is not None and out is not None:
        doc.save(out)
    if report_path is not None:
        Path(report_path).write_text(report.dumps(), encoding="utf-8", newline="\n")
