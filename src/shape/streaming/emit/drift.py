"""A drift plan inside a stream (W2-09): ``shape emit TARGET --drift-plan PLAN.json``.

The plan is the one ``shape generate-drift`` reads (``docs/DRIFT.md``). :class:`DriftEventPlan`
emits its days in order: **day d is generated from** ``plan.schema_at(schema, d)`` **with seed +
d**, exactly as ``shape generate-drift`` does, so for the same ``--rows TABLE=N`` the event values
of day d equal that day's ``generate-drift`` tables. Within a day the tables stream in dependency
order, as they do without a plan.

``_shape_seq`` continues across the days (a table's day-1 events follow its day-0 events), so the
D-12 key ``<table>/<seq>`` is unique for the whole stream. No event field is added: the events of
a day are the day's columns plus the usual ``_shape_*`` fields (a column the plan adds appears from
its start day, one it drops is gone).

The plan's SHA-256 is part of the checkpoint fingerprint, so a checkpoint of another plan is
refused (the digest is of the plan file's bytes, as ``sha256sum`` gives it).

The answer key (``--answer-key``) gains ``kind: "drift"`` records: when the stream reaches a day,
one record for every plan event in effect that day, with ``event`` (the plan's event id), ``table``,
``column``, ``day`` (ISO date), ``day_number`` (0 is the plan's start), ``effect`` (0 to 1: how far
the event has taken hold, from the plan's ramp) and ``seq`` (the first sequence number of the
event's table on that day, so ``key`` is the first event of the table that day).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from shape.errors import ShapeError
from shape.streaming.emit.source import EventBlock, EventPlan

if TYPE_CHECKING:
    from shape.generation.drift_plan import DriftPlan
    from shape.generation.schema import GenSchema
    from shape.streaming.emit.anomaly import AnomalyInjector
    from shape.streaming.emit.faults import AnswerKey


def plan_fingerprint(plan: DriftPlan) -> str:
    """A digest of a plan that is not read from a file (an API caller's): the SHA-256 of its
    canonical answer key."""
    blob = json.dumps(plan.ground_truth(), sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


class DriftEventPlan:
    """The days of ``plan`` as one event sequence (see the module docstring). ``scale``, ``seed``
    (default: the schema's) and ``row_counts`` are as for ``shape generate-drift``; the other
    options are :class:`~shape.streaming.emit.source.EventPlan`'s, applied to each day."""

    def __init__(
        self,
        schema: GenSchema,
        plan: DriftPlan,
        *,
        scale: str | None = None,
        seed: int | None = None,
        row_counts: Mapping[str, int] | None = None,
        tables: Sequence[str] | None = None,
        out_of_order: float = 0.0,
        ooo_window: int = 1000,
        anomaly: AnomalyInjector | None = None,
        envelope: str = "flat",
        by_event_time: bool = False,
        answer_key: AnswerKey | None = None,
        plan_sha256: str | None = None,
    ) -> None:
        from shape.generation.engine import Engine

        self.plan = plan
        self.base_seed = schema.model.seed if seed is None else int(seed)
        self.plan_sha256 = plan_sha256 or plan_fingerprint(plan)
        self.anomaly = anomaly
        self._answer_key = answer_key
        self._days: list[EventPlan] = []
        self.day_events: list[int] = []
        self.day_starts: list[int] = []
        self._bases: list[dict[str, int]] = []  # events of each table before each day
        seen: dict[str, int] = {}
        total = 0
        for day in range(plan.days):
            engine = Engine(
                plan.schema_at(schema, day),
                scale=scale,
                seed=self.base_seed + day,
                row_counts=row_counts,
            )
            events = EventPlan(
                engine,
                tables=tables,
                out_of_order=out_of_order,
                ooo_window=ooo_window,
                anomaly=anomaly,
                envelope=envelope,
                by_event_time=by_event_time,
                answer_key=answer_key,
                seq_base=seen,
            )
            self._bases.append(dict(seen))
            self.day_starts.append(total)
            self.day_events.append(events.total_events)
            total += events.total_events
            for table, n in events.counts.items():
                seen[table] = seen.get(table, 0) + n
            self._days.append(events)
        if not self._days or total == 0:
            raise ShapeError("the drift plan's days have no events to stream")
        self.tables = list(self._days[0].tables)
        self.counts = dict(self._days[0].counts)  # events of each table in one day
        self.total_events = total
        self._fingerprint: str | None = None

    # ---- identity -----------------------------------------------------------------------

    @property
    def answer_key(self) -> AnswerKey | None:
        return self._answer_key

    @answer_key.setter
    def answer_key(self, key: AnswerKey | None) -> None:
        self._answer_key = key
        for day in self._days:
            day.answer_key = key

    def fingerprint(self) -> str:
        """A hash of everything that decides the event sequence, the plan's SHA-256 included; a
        checkpoint of another fingerprint is refused."""
        if self._fingerprint is None:
            doc = {
                "drift_plan_sha256": self.plan_sha256,
                "base_seed": self.base_seed,
                "days": [d.fingerprint() for d in self._days],
            }
            self._fingerprint = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
        return self._fingerprint

    # ---- the events ---------------------------------------------------------------------

    def _record(self, day: int) -> None:
        """The drift records of ``day`` in the answer key."""
        if self._answer_key is None:
            return
        date = self.plan.date_of(day).isoformat()
        for e in self.plan.effects(day):
            if e["table"] not in self.tables:
                continue
            self._answer_key.record(
                "drift",
                e["table"],
                [self._bases[day].get(e["table"], 0)],
                event=e["id"],
                column=e["column"],
                day=date,
                day_number=day,
                effect=e["effect"],
            )

    def blocks(self, offset: int = 0) -> Iterator[EventBlock]:
        """The blocks from event ``offset`` on, day by day; the first is cut to start there."""
        if offset < 0:
            raise ValueError("offset must be 0 or more")
        for day, events in enumerate(self._days):
            start = self.day_starts[day]
            if offset >= start + self.day_events[day]:
                continue
            self._record(day)
            for block in events.blocks(max(0, offset - start)):
                yield EventBlock(start + block.offset, block.table, block.batch)

    def day_of(self, offset: int) -> int:
        """The day that event ``offset`` belongs to."""
        for day in range(len(self._days) - 1, -1, -1):
            if offset >= self.day_starts[day]:
                return day
        return 0


def describe_days(plan: DriftEventPlan) -> list[dict[str, Any]]:
    """For a dry run: each day's date, events and the plan events in effect."""
    return [
        {
            "day": d,
            "date": plan.plan.date_of(d).isoformat(),
            "events": plan.day_events[d],
            "active": [e["id"] for e in plan.plan.effects(d)],
        }
        for d in range(len(plan.day_events))
    ]
