"""Drift over time, planted on purpose: a plan of events applied to a generation schema one day at
a time, with an answer key (``ground_truth.json``) that lists every event.

``shape time-travel`` evolves *rows* (growth, churn, updates) and ``shape continue`` writes the next
batch of changes; neither changes what the data *looks like*. A :class:`DriftPlan` does: it keeps
the schema as the source of truth and, for each day, makes a copy with the events that are active
that day applied. The day's tables come from the ordinary engine, so every strategy, rule and
relationship of the schema still holds, and the per-day schemas (``schema_at``) are a diffable
history of the drift.

Events (``DriftEvent.kind``), each aimed at ``table.column``:

``null_rate``
    ``{"to": 0.3}``: the column's null rate moves to 0.3.
``category_weights``
    ``{"weights": {"completed": 40, "shipped": 40, "cancelled": 20}}``: the category mix moves
    to these (relative) weights; a key the column did not have appears.
``new_category``
    ``{"value": "lost", "share": 0.02}``: a new value takes this share of the rows.
``distribution``
    ``{"params": {"mean": {"add": 0.3365}, "sigma": {"factor": 1.5}}}`` (a bare number sets the
    parameter), or ``{"scale": 1.4}``: every value times 1.4 (``log_normal``: ``mean`` plus
    ``ln 1.4``; ``normal``: ``mean`` and ``std_dev`` times 1.4; ``uniform``: ``min`` and ``max``;
    ``pareto``: ``min``). ``min`` and ``max`` clips scale along.
``add_column``
    ``{"definition": {"type": "string", "generator": {...}}}``: the column exists from the start
    day.
``drop_column``
    the column is missing from the start day.
``type_change``
    ``{"to": {"type": "string", "generator": {...}}}``: the column is generated as this instead.
``rename_column``
    ``{"column": "orders.status", "to": "order_status"}`` (the column may also be given as
    ``table`` and ``column``): the column has the new name from the start day, in its place among
    the columns. Keys, relationships, foreign-key references and correlations that name it follow
    the rename. A column that a rule or another column's generator names is refused, since the
    rename would break it. Events on the renamed column must use the name it has on their days.

Timing: ``start`` (an ISO date or a day number, day 0 being the plan's start), ``end`` (exclusive:
the event is over and the schema reverts) and ``ramp_days`` (the change builds up over this many
days, then holds). A ramp moves the value in a straight line from the original to the target; a
step is a ramp of 0 days; a window has an ``end``. ``add_column``, ``drop_column``,
``type_change`` and ``rename_column`` are on or off, so they take no ramp.

``DriftPlan.expected_changes(day_a, day_b)`` says which ``shape.diff`` changes the events
that differ between two days should produce, so a test can plant drift, profile two days and check
that the diff finds it. A rename is one record: ``kinds`` is the dropped old name and ``rename``
holds the new name and the kinds that find it (``column_added``).
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shape.errors import ShapeError
from shape.generation.schema import Column, GenSchema
from shape.kernel import pmath

if TYPE_CHECKING:
    from shape.generation.engine import GenerationResult

KINDS = (
    "null_rate",
    "category_weights",
    "new_category",
    "distribution",
    "add_column",
    "drop_column",
    "type_change",
    "rename_column",
)
_ON_OFF = ("add_column", "drop_column", "type_change", "rename_column")
GROUND_TRUTH_VERSION = 1
GROUND_TRUTH_FORMAT = "shape-drift-ground-truth"
PLAN_FORMAT = "shape-drift-plan"
PLAN_VERSION = 1

# the shape.diff change kinds each event produces (any one of them satisfies the event)
_DETECTED_AS = {
    "null_rate": ("null_rate_change",),
    "category_weights": ("category_shift", "new_categorical_values"),
    "new_category": ("new_categorical_values", "category_shift"),
    "distribution": ("mean_shift", "spread_change", "distribution_shift", "range_change"),
    "add_column": ("column_added",),
    "drop_column": ("column_removed",),
    "type_change": ("dtype_change",),
    "rename_column": ("column_removed",),  # plus column_added, under the new name
}


class DriftPlanError(ShapeError, ValueError):
    """A drift plan is malformed, or names something the schema does not have."""


def _date(value: Any, what: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise DriftPlanError(f"{what} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


@dataclass(frozen=True)
class DriftEvent:
    """One planted change. See the module docstring for the ``kind``s and their ``spec``."""

    kind: str
    table: str
    column: str
    start: int | str | dt.date
    spec: Mapping[str, Any] = field(default_factory=dict)
    end: int | str | dt.date | None = None
    ramp_days: int = 0
    id: str = ""

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> DriftEvent:
        known = {"id", "kind", "table", "column", "start", "end", "ramp_days"}
        spec = {k: v for k, v in doc.items() if k not in known}
        table, column = doc.get("table"), doc.get("column")
        if table is None and isinstance(column, str) and "." in column:
            table, _, column = column.partition(".")  # "orders.status" names both
        present = {
            "kind": doc.get("kind"),
            "table": table,
            "column": column,
            "start": doc.get("start"),
        }
        missing = [k for k, v in present.items() if v is None]
        if missing:
            raise DriftPlanError(f"a drift event needs {missing}: {dict(doc)}")
        return cls(
            kind=str(doc["kind"]),
            table=str(table),
            column=str(column),
            start=doc["start"],
            spec=spec,
            end=doc.get("end"),
            ramp_days=int(doc.get("ramp_days", 0)),
            id=str(doc.get("id", "")),
        )


@dataclass(frozen=True)
class _Resolved:
    """An event with its days as numbers."""

    event: DriftEvent
    id: str
    start: int
    end: int | None

    def weight(self, day: int) -> float:
        """How far the event has taken effect on ``day``: 0 (not at all) to 1 (fully)."""
        if day < self.start or (self.end is not None and day >= self.end):
            return 0.0
        ramp = self.event.ramp_days
        return 1.0 if ramp <= 0 else min(1.0, (day - self.start + 1) / ramp)


class DriftPlan:
    """A list of :class:`DriftEvent` over ``days`` days from ``start``."""

    def __init__(
        self,
        events: list[DriftEvent | Mapping[str, Any]],
        *,
        start: str | dt.date = "2026-01-01",
        days: int = 30,
    ) -> None:
        if days < 1:
            raise DriftPlanError("a plan covers at least one day")
        self.start = _date(start, "start")
        self.days = days
        self._events = [e if isinstance(e, DriftEvent) else DriftEvent.from_dict(e) for e in events]
        self._resolved = [self._resolve(i, e) for i, e in enumerate(self._events, 1)]
        ids = [r.id for r in self._resolved]
        if len(set(ids)) != len(ids):
            raise DriftPlanError(f"event ids must be unique: {ids}")

    # ---- construction ------------------------------------------------------------------------

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> DriftPlan:
        """``{"start": "2026-01-01", "days": 30, "events": [{...}, ...]}``, optionally declaring
        ``"format": "shape-drift-plan"`` and ``"version": 1``; a newer version is refused."""
        if "format" in doc and doc["format"] != PLAN_FORMAT:
            raise DriftPlanError(
                f"not a drift plan: its format is {doc['format']!r}, expected {PLAN_FORMAT!r}"
            )
        if "version" in doc:
            version = doc["version"]
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise DriftPlanError(
                    f"a drift plan's version is an integer of at least 1, got {version!r}"
                )
            if version > PLAN_VERSION:
                raise DriftPlanError(
                    f"unsupported drift plan version {version}: this Shape reads up to version "
                    f"{PLAN_VERSION}; it was written by a newer Shape release (upgrade Shape)"
                )
        unknown = set(doc) - {"format", "version", "start", "days", "events"}
        if unknown:
            raise DriftPlanError(f"unknown drift plan keys: {sorted(unknown)}")
        events = doc.get("events")
        if not isinstance(events, list) or not events:
            raise DriftPlanError("a drift plan needs a non-empty 'events' list")
        return cls(events, start=doc.get("start", "2026-01-01"), days=int(doc.get("days", 30)))

    @classmethod
    def load(cls, path: str | Path) -> DriftPlan:
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DriftPlanError(f"drift plan {path} is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise DriftPlanError("a drift plan must be a JSON object")
        return cls.from_dict(doc)

    def _day(self, value: Any, what: str) -> int:
        if isinstance(value, bool):
            raise DriftPlanError(f"{what} must be a date or a day number")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.lstrip("-").isdigit():
            return int(value)
        return (_date(value, what) - self.start).days

    def _resolve(self, index: int, event: DriftEvent) -> _Resolved:
        if event.kind not in KINDS:
            raise DriftPlanError(f"unknown event kind {event.kind!r}; the kinds are {list(KINDS)}")
        start = self._day(event.start, "start")
        end = None if event.end is None else self._day(event.end, "end")
        if end is not None and end <= start:
            raise DriftPlanError(f"event {event.table}.{event.column}: end must be after start")
        if event.ramp_days < 0:
            raise DriftPlanError("ramp_days must be 0 or more")
        if event.ramp_days and event.kind in _ON_OFF:
            raise DriftPlanError(f"a {event.kind} event is on or off: it takes no ramp")
        self._check_spec(event)
        return _Resolved(event, event.id or f"e{index}", start, end)

    @staticmethod
    def _check_spec(event: DriftEvent) -> None:
        spec, kind = event.spec, event.kind
        need = {
            "null_rate": "to",
            "category_weights": "weights",
            "new_category": "value",
            "distribution": None,
            "add_column": "definition",
            "drop_column": None,
            "type_change": "to",
            "rename_column": "to",
        }[kind]
        if need and need not in spec:
            raise DriftPlanError(f"a {kind} event needs {need!r}: {event.table}.{event.column}")
        if kind == "distribution" and not ({"params", "scale"} & set(spec)):
            raise DriftPlanError("a distribution event needs 'params' or 'scale'")
        if kind == "null_rate" and not 0 <= float(spec["to"]) <= 1:
            raise DriftPlanError("a null_rate event's 'to' must be between 0 and 1")
        if kind == "new_category" and not 0 < float(spec.get("share", 0.05)) < 1:
            raise DriftPlanError("a new_category event's 'share' must be between 0 and 1")
        if kind == "rename_column":
            new = spec["to"]
            if not isinstance(new, str) or not new.strip():
                raise DriftPlanError(
                    f"a rename_column event's 'to' must be a column name: "
                    f"{event.table}.{event.column}"
                )
            if new == event.column:
                raise DriftPlanError(
                    f"rename_column: {event.table}.{event.column} is renamed to the same name"
                )

    @property
    def events(self) -> list[DriftEvent]:
        return list(self._events)

    # ---- days --------------------------------------------------------------------------------

    def day_number(self, day: int | str | dt.date) -> int:
        """A day given as a number (0 is the plan's start) or a date, as a number."""
        n = self._day(day, "day")
        if not 0 <= n < self.days:
            raise DriftPlanError(f"day {day} is outside the plan (days 0 to {self.days - 1})")
        return n

    def date_of(self, day: int) -> dt.date:
        return self.start + dt.timedelta(days=day)

    def active(self, day: int | str | dt.date) -> list[str]:
        """The ids of the events that have taken effect on ``day``."""
        n = self.day_number(day)
        return [r.id for r in self._resolved if r.weight(n) > 0]

    def effects(self, day: int | str | dt.date) -> list[dict[str, Any]]:
        """The events in effect on ``day``: ``{id, kind, table, column, effect}`` with ``effect``
        0 to 1 (how far the event has taken hold; the ``by_day`` weights of the answer key)."""
        n = self.day_number(day)
        return [
            {
                "id": r.id,
                "kind": r.event.kind,
                "table": r.event.table,
                "column": r.event.column,
                "effect": round(r.weight(n), 6),
            }
            for r in self._resolved
            if r.weight(n) > 0
        ]

    # ---- the schema on a day -----------------------------------------------------------------

    def schema_at(self, schema: GenSchema, day: int | str | dt.date) -> GenSchema:
        """A copy of ``schema`` with the events of ``day`` applied (``schema`` is not changed)."""
        n = self.day_number(day)
        out = copy.deepcopy(schema)
        for r in self._resolved:
            _apply(out, r, r.weight(n))
        return out

    def generate_day(
        self,
        schema: GenSchema,
        day: int | str | dt.date,
        *,
        row_counts: Mapping[str, int] | None = None,
        scale: str | None = None,
        seed: int | None = None,
    ) -> GenerationResult:
        """The tables of one day. The seed is ``seed`` (default: the schema's) plus the day number,
        so every day is a fresh sample and a rerun gives the same day."""
        from shape.generation.engine import Engine

        n = self.day_number(day)
        base = schema.model.seed if seed is None else int(seed)
        engine = Engine(
            self.schema_at(schema, n), scale=scale, seed=base + n, row_counts=row_counts
        )
        return engine.generate()

    def generate(
        self,
        schema: GenSchema,
        *,
        row_counts: Mapping[str, int] | None = None,
        scale: str | None = None,
        seed: int | None = None,
    ) -> Iterator[tuple[dt.date, GenerationResult]]:
        """Every day of the plan, in order: ``(date, result)``."""
        for n in range(self.days):
            yield (
                self.date_of(n),
                self.generate_day(schema, n, row_counts=row_counts, scale=scale, seed=seed),
            )

    def write(
        self,
        schema: GenSchema,
        out_dir: str | Path,
        *,
        row_counts: Mapping[str, int] | None = None,
        scale: str | None = None,
        seed: int | None = None,
        fmt: str = "parquet",
    ) -> list[Path]:
        """Write ``OUT/<date>/<table>.<fmt>``, ``OUT/_specs/<date>.json`` (each day's schema) and
        ``OUT/ground_truth.json``; returns the files."""
        out = Path(out_dir)
        files: list[Path] = []
        # every day's schema first: a plan that fails on a later day writes nothing (#737)
        for n in range(self.days):
            day_schema = self.schema_at(schema, n)
            errors = [i for i in day_schema.validate() if i.level == "error"]
            if errors:
                raise DriftPlanError(
                    f"day {self.date_of(n).isoformat()}: "
                    + "; ".join(f"{i.location}: {i.message}" for i in errors)
                )
        specs = out / "_specs"
        specs.mkdir(parents=True, exist_ok=True)
        for n in range(self.days):
            day = self.date_of(n).isoformat()
            result = self.generate_day(schema, n, row_counts=row_counts, scale=scale, seed=seed)
            files += _write_tables(result.tables, fmt, out / day)
            spec = specs / f"{day}.json"
            spec.write_text(
                json.dumps(self.schema_at(schema, n).to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            files.append(spec)
        truth = out / "ground_truth.json"
        truth.write_text(
            json.dumps(self.ground_truth(), indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        return [*files, truth]

    # ---- the answer key ----------------------------------------------------------------------

    def ground_truth(self) -> dict[str, Any]:
        """Every planted event with its dates, what it changes and the ``shape.diff`` change kinds
        that find it, and for every day which events had taken effect (and how far)."""
        events = []
        for r in self._resolved:
            e = r.event
            ramp_end = r.start + max(e.ramp_days, 1) - 1
            last = ramp_end if r.end is None else min(ramp_end, r.end - 1)
            peak = r.weight(last)  # below 1 when the window ends before the ramp does
            events.append(
                {
                    "id": r.id,
                    "kind": e.kind,
                    "table": e.table,
                    "column": e.column,
                    "start": self.date_of(r.start).isoformat(),
                    "end": None if r.end is None else self.date_of(r.end).isoformat(),
                    "ramp_days": e.ramp_days,
                    "full_effect_from": self.date_of(ramp_end).isoformat() if peak >= 1 else None,
                    "peak_weight": round(peak, 6),
                    "shape": _shape_name(e),
                    "spec": json.loads(json.dumps(dict(e.spec), default=str)),
                    "detected_as": list(_DETECTED_AS[e.kind]),
                }
            )
        days = [
            {
                "date": self.date_of(n).isoformat(),
                "events": {r.id: round(r.weight(n), 6) for r in self._resolved if r.weight(n) > 0},
            }
            for n in range(self.days)
        ]
        return {
            "format": GROUND_TRUTH_FORMAT,
            "version": GROUND_TRUTH_VERSION,
            "start": self.start.isoformat(),
            "days": self.days,
            "events": events,
            "by_day": days,
        }

    def expected_changes(
        self, day_a: int | str | dt.date, day_b: int | str | dt.date
    ) -> list[dict[str, Any]]:
        """What ``shape.diff(profile(day_a), profile(day_b))`` should report: one record per event
        whose effect differs between the two days, with the column (``table.column`` when the plan
        names more than one table), the diff kinds that count as finding it (any one will do) and
        ``size``, the share of the event's full effect that lies between the days. Small ramp
        steps stay under the diff's thresholds: compare days far enough apart."""
        a, b = self.day_number(day_a), self.day_number(day_b)
        multi = len({r.event.table for r in self._resolved}) > 1
        out = []
        for r in self._resolved:
            size = abs(r.weight(b) - r.weight(a))
            if size == 0:
                continue
            e = r.event
            record: dict[str, Any] = {
                "event": r.id,
                "column": f"{e.table}.{e.column}" if multi else e.column,
                "kinds": list(_DETECTED_AS[e.kind]),
                "size": size,
            }
            if e.kind == "rename_column":
                # the diff sees a dropped column and an added one; the key records one rename
                new = str(e.spec["to"])
                record["rename"] = {
                    "from": record["column"],
                    "to": f"{e.table}.{new}" if multi else new,
                    "added_kinds": ["column_added"],
                }
            out.append(record)
        return out


def _write_tables(tables: Mapping[str, Any], fmt: str, directory: Path) -> list[Path]:
    """Each table through the format's sink (``csv``, ``parquet``, ``jsonl``)."""
    from shape.plugins.host import default_host

    sink = default_host().get("shape.sinks", fmt)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, table in tables.items():
        target = directory / f"{name}.{fmt}"
        sink.write(str(target), name, iter(table.to_batches()), schema=table.schema)
        written.append(target)
    return written


def _shape_name(e: DriftEvent) -> str:
    if e.end is not None:
        return "ramp_window" if e.ramp_days else "window"
    return "ramp" if e.ramp_days else "step"


# --- applying one event to a schema --------------------------------------------------------------


def _column(schema: GenSchema, event: DriftEvent) -> Column:
    table = schema.tables.get(event.table)
    if table is None:
        raise DriftPlanError(f"event {event.kind}: the schema has no table {event.table!r}")
    col = table.columns.get(event.column)
    if col is None:
        raise DriftPlanError(f"event {event.kind}: no column {event.table}.{event.column}")
    return col


def _apply(schema: GenSchema, r: _Resolved, w: float) -> None:
    e = r.event
    if e.kind == "add_column":
        table = schema.tables.get(e.table)
        if table is None:
            raise DriftPlanError(f"add_column: the schema has no table {e.table!r}")
        if e.column in table.columns:
            raise DriftPlanError(f"add_column: {e.table}.{e.column} already exists")
        if w > 0:
            doc = e.spec["definition"]
            table.columns[e.column] = Column(
                name=e.column,
                type=str(doc.get("type", "string")),
                generator=copy.deepcopy(doc["generator"]),
                nullable=bool(doc.get("nullable", False)),
                null_rate=float(doc.get("null_rate", 0.0)),
                max_length=doc.get("max_length"),
                precision=doc.get("precision"),
                scale=doc.get("scale"),
            )
        return
    if e.kind == "rename_column":
        _check_rename(schema, e)  # an unusable rename is an error on every day, not only after it
        if w > 0:
            _rename(schema, e)
        return
    if w <= 0:  # not in effect on this day: its column may not exist yet, or any more
        return
    col = _column(schema, e)
    if e.kind == "drop_column":
        _check_droppable(schema, e)
        del schema.tables[e.table].columns[e.column]
    elif e.kind == "null_rate":
        target = float(e.spec["to"])
        col.null_rate = col.null_rate + w * (target - col.null_rate)
        col.nullable = col.nullable or col.null_rate > 0
    elif e.kind in ("category_weights", "new_category"):
        _apply_weights(col, e, w)
    elif e.kind == "distribution":
        _apply_distribution(col, e, w)
    elif e.kind == "type_change":
        doc = e.spec["to"]
        col.type = str(doc.get("type", col.type))
        col.generator = copy.deepcopy(doc["generator"])
        for key in ("nullable", "null_rate", "max_length", "precision", "scale"):
            if key in doc:
                setattr(col, key, doc[key])


def _check_rename(schema: GenSchema, e: DriftEvent) -> None:
    new = str(e.spec["to"])
    table = schema.tables.get(e.table)
    if table is None:
        raise DriftPlanError(
            f"rename_column: cannot rename {e.table}.{e.column} to {new!r}: "
            f"the schema has no table {e.table!r}"
        )
    if e.column not in table.columns:
        raise DriftPlanError(
            f"rename_column: cannot rename {e.table}.{e.column} to {new!r}: "
            f"there is no column {e.table}.{e.column}"
        )
    if new in table.columns:
        raise DriftPlanError(
            f"rename_column: cannot rename {e.table}.{e.column} to {new!r}: "
            f"{e.table}.{new} already exists"
        )


def _names(value: Any, name: str) -> bool:
    """Whether ``name`` appears as a whole word in a generator or rule (any nesting)."""
    pattern = re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w])")
    return bool(pattern.search(json.dumps(value, default=str)))


def _rename(schema: GenSchema, e: DriftEvent) -> None:
    old, new, tname = e.column, str(e.spec["to"]), e.table
    table = schema.tables[tname]
    for other in table.columns.values():
        if other.name != old and _names(other.generator, old):
            raise DriftPlanError(
                f"rename_column: {tname}.{old} is named by the generator of {tname}.{other.name}"
            )
    for rule in schema.business_rules:
        if _names([rule.rule, rule.via, rule.when], old) and rule.table in (None, tname):
            raise DriftPlanError(f"rename_column: {tname}.{old} is named by rule {rule.name}")
    table.columns = {
        (new if key == old else key): _renamed(col, new) if key == old else col
        for key, col in table.columns.items()
    }
    table.primary_key = [new if c == old else c for c in table.primary_key]
    for rel in schema.relationships:
        if rel.parent == tname:
            rel.parent_columns = [new if c == old else c for c in rel.parent_columns]
        if rel.child == tname:
            rel.child_columns = [new if c == old else c for c in rel.child_columns]
    for other_table in schema.tables.values():  # foreign keys that point at the column
        for col in other_table.columns.values():
            if col.fk_ref_table == tname and col.fk_ref_column == old:
                col.generator["ref"] = f"{tname}.{new}"
    for triple in schema.correlated_columns.get(tname, []):
        for i in (0, 1):
            if triple[i] == old:
                triple[i] = new


def _renamed(col: Column, new: str) -> Column:
    col.name = new
    return col


def _check_droppable(schema: GenSchema, e: DriftEvent) -> None:
    table = schema.tables[e.table]
    if e.column in table.primary_key:
        raise DriftPlanError(f"drop_column: {e.table}.{e.column} is part of the primary key")
    for rel in schema.relationships:
        if (rel.parent == e.table and e.column in rel.parent_columns) or (
            rel.child == e.table and e.column in rel.child_columns
        ):
            raise DriftPlanError(f"drop_column: {e.table}.{e.column} is in relationship {rel.name}")


def _normalised(weights: Mapping[str, float]) -> dict[str, float]:
    values = {str(k): float(v) for k, v in weights.items()}
    if any(v < 0 for v in values.values()) or sum(values.values()) <= 0:
        raise DriftPlanError("category weights must be non-negative with a positive sum")
    total = sum(values.values())
    return {k: v / total for k, v in values.items()}


def _apply_weights(col: Column, e: DriftEvent, w: float) -> None:
    if col.strategy != "weighted_enum":
        raise DriftPlanError(
            f"{e.kind}: {e.table}.{e.column} is generated by {col.strategy!r}, not weighted_enum"
        )
    base = _normalised(col.generator["values"])
    if e.kind == "new_category":
        share = float(e.spec.get("share", 0.05))
        value = str(e.spec["value"])
        target = {k: v * (1 - share) for k, v in base.items() if k != value}
        target[value] = base.get(value, 0.0) * (1 - share) + share
    else:
        target = _normalised(e.spec["weights"])
    mixed = {k: (1 - w) * base.get(k, 0.0) + w * target.get(k, 0.0) for k in {*base, *target}}
    order = [*col.generator["values"], *(k for k in target if k not in base)]
    col.generator["values"] = {
        k: mixed[k]
        for k in order
        if k in base or mixed[k] > 0  # a new value waits for its day
    }


def _param_home(gen: dict[str, Any], key: str) -> dict[str, Any]:
    """The mapping a parameter lives in: the spec's ``params`` when it has one, else the spec."""
    params = gen.get("params")
    if isinstance(params, dict) and (key in params or key not in gen):
        return params
    return gen


def _change_param(gen: dict[str, Any], key: str, op: Any, w: float, who: str) -> None:
    home = _param_home(gen, key)
    if isinstance(op, Mapping):
        ops = dict(op)
    else:
        ops = {"set": op}
    if len(ops) != 1 or next(iter(ops)) not in ("set", "add", "factor"):
        raise DriftPlanError(f"{who}: parameter {key!r} takes a number, set, add or factor")
    name, amount = next(iter(ops.items()))
    amount = float(amount)
    if name == "add":
        home[key] = float(home.get(key, 0.0)) + w * amount
        return
    if key not in home:
        raise DriftPlanError(f"{who}: {key!r} is not in the generator; give it a value to {name}")
    current = float(home[key])
    target = amount if name == "set" else current * amount
    home[key] = current + w * (target - current)


def _scale_params(gen: dict[str, Any], factor: float, w: float, who: str) -> None:
    family = str(gen.get("distribution", "uniform"))
    if factor <= 0:
        raise DriftPlanError(f"{who}: scale must be positive")
    f = 1.0 + w * (factor - 1.0)
    if family == "log_normal":
        _change_param(gen, "mean", {"add": float(pmath.log(factor))}, w, who)
    elif family == "normal":
        _change_param(gen, "mean", {"factor": factor}, w, who)
        for name in ("std_dev", "sigma", "std"):
            if name in _param_home(gen, name):
                _change_param(gen, name, {"factor": factor}, w, who)
    elif family == "uniform":
        for name in ("min", "max"):
            _change_param(gen, name, {"factor": factor}, w, who)
    elif family == "pareto":
        _change_param(gen, "min", {"factor": factor}, w, who)
    else:
        raise DriftPlanError(f"{who}: 'scale' is not defined for {family!r}; use 'params'")
    if family in ("log_normal", "normal", "pareto"):
        for name in ("min", "max"):  # clips scale with the values
            home = _param_home(gen, name)
            if name in home and home[name] is not None:
                home[name] = float(home[name]) * f


def _apply_distribution(col: Column, e: DriftEvent, w: float) -> None:
    who = f"{e.table}.{e.column}"
    if col.strategy != "distribution":
        raise DriftPlanError(
            f"distribution: {who} is generated by {col.strategy!r}; a distribution event needs a "
            "column of the 'distribution' strategy"
        )
    gen = col.generator
    if "scale" in e.spec:
        _scale_params(gen, float(e.spec["scale"]), w, who)
    for key, op in dict(e.spec.get("params", {})).items():
        _change_param(gen, key, op, w, who)
