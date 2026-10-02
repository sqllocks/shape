"""Business workflow events: state machines with dwell times (P6-04a).

Entities move through the states of a business process (an order: created, confirmed, shipped,
delivered or returned) with a probability and a dwell time for each transition, until they reach a
terminal state, get stuck (an anomaly) or hit the per-entity transition limit. The result is the
transition events, one summary row per entity, and the distribution of final states.

Example::

    states, transitions = get_preset_workflow("order_fulfillment")
    cfg = WorkflowConfig(states=states, transitions=transitions, entity_count=1000)
    result = WorkflowSimulator(config=cfg).run()
    result.events            # an Arrow table, one row per transition

The random draws are made in a fixed order (an entity's initial state and start offset; then per
step the transition, the anomaly roll, the dwell time), so a seed reproduces a run. Event ids come
from a separate stream of the same seed, so the same seed also gives the same ids.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

EVENT_COLUMNS = (
    "event_id",
    "entity_id",
    "from_state",
    "to_state",
    "transitioned_at",
    "dwell_hours",
    "is_anomaly",
    "anomaly_type",
)
SUMMARY_COLUMNS = (
    "entity_id",
    "initial_state",
    "final_state",
    "total_transitions",
    "total_hours",
    "is_complete",
)
_EVENT_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("entity_id", pa.string()),
        ("from_state", pa.string()),
        ("to_state", pa.string()),
        ("transitioned_at", pa.timestamp("us")),
        ("dwell_hours", pa.float64()),
        ("is_anomaly", pa.bool_()),
        ("anomaly_type", pa.string()),
    ]
)
_SUMMARY_SCHEMA = pa.schema(
    [
        ("entity_id", pa.string()),
        ("initial_state", pa.string()),
        ("final_state", pa.string()),
        ("total_transitions", pa.int64()),
        ("total_hours", pa.float64()),
        ("is_complete", pa.bool_()),
    ]
)


@dataclass
class StateDefinition:
    """A state of a workflow.

    Args:
        name: Unique name of the state.
        is_initial: Entities can start here.
        is_terminal: The workflow ends here (no outgoing transitions).
        metadata: Free key-value pairs attached to the state.
    """

    name: str
    is_initial: bool = False
    is_terminal: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionRule:
    """A directed edge of the workflow.

    Args:
        from_state: Source state.
        to_state: Destination state.
        probability: Relative weight, normalised over the rules leaving ``from_state``.
        dwell_hours_mean: Mean hours spent in ``from_state`` before the transition.
        dwell_hours_std: Standard deviation of the (normal) dwell time.
        min_dwell_hours: Floor of the sampled dwell time.
    """

    from_state: str
    to_state: str
    probability: float = 1.0
    dwell_hours_mean: float = 1.0
    dwell_hours_std: float = 0.5
    min_dwell_hours: float = 0.1


@dataclass
class WorkflowConfig:
    """Configuration for :class:`WorkflowSimulator`.

    Args:
        states: The states.
        transitions: The transitions between them.
        entity_count: Entities to simulate.
        entity_prefix: Prefix of the entity ids (``<prefix>_000000``).
        start_time: ISO timestamp the entities start from (each within the first hour).
        max_transitions_per_entity: Safety limit on the steps of one entity.
        anomaly_enabled: Inject anomalous transitions.
        anomaly_skip_probability: Chance of skipping a state.
        anomaly_stuck_probability: Chance of an entity getting stuck.
        anomaly_backward_probability: Chance of moving back to a visited state.
        seed: Random seed.
    """

    states: list[StateDefinition] = field(default_factory=list)
    transitions: list[TransitionRule] = field(default_factory=list)
    entity_count: int = 100
    entity_prefix: str = "entity"
    start_time: str = "2024-01-01T00:00:00"
    max_transitions_per_entity: int = 20
    anomaly_enabled: bool = True
    anomaly_skip_probability: float = 0.02
    anomaly_stuck_probability: float = 0.01
    anomaly_backward_probability: float = 0.01
    seed: int = 42


@dataclass
class WorkflowResult:
    """Result of :meth:`WorkflowSimulator.run`.

    Attributes:
        events: Every transition (``EVENT_COLUMNS``), sorted by entity and time.
        entity_summary: One row per entity (``SUMMARY_COLUMNS``).
        state_distribution: Entities per final state.
        stats: ``total_events``, ``total_entities``, ``anomaly_count``,
            ``mean_completion_hours`` and ``config_seed``.
    """

    events: pa.Table
    entity_summary: pa.Table
    state_distribution: dict[str, int] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"WorkflowResult(events={self.events.num_rows}, "
            f"entities={self.entity_summary.num_rows}, stats_keys={list(self.stats)})"
        )


class WorkflowSimulator:
    """Simulate business-process event streams with a state machine."""

    def __init__(self, config: WorkflowConfig) -> None:
        self._config = config
        self._rng = np.random.default_rng(config.seed)
        # Ids come from a stream of their own, so they never move the main draws.
        self._ids = np.random.default_rng(np.random.SeedSequence([config.seed, 0x1D5]))
        self._initial_states = [s.name for s in config.states if s.is_initial]
        self._terminal_states = {s.name for s in config.states if s.is_terminal}
        self._transitions_by_state: dict[str, list[TransitionRule]] = {}
        self._probs_by_state: dict[str, np.ndarray[Any, Any]] = {}
        grouped: dict[str, list[TransitionRule]] = defaultdict(list)
        for rule in config.transitions:
            grouped[rule.from_state].append(rule)
        for state, rules in grouped.items():
            raw = np.array([r.probability for r in rules], dtype=np.float64)
            total = raw.sum()
            if not total > 0:
                raise ValueError(f"the transitions leaving {state!r} have no positive probability")
            self._transitions_by_state[state] = rules
            self._probs_by_state[state] = raw / total

    # ---- public API ---------------------------------------------------------------------

    def run(self) -> WorkflowResult:
        cfg = self._config
        base_time = dt.datetime.fromisoformat(cfg.start_time)
        rows: list[tuple[str, str, str, dt.datetime, float, bool, str]] = []
        first_state: dict[str, str] = {}
        for i in range(cfg.entity_count):
            entity_id = f"{cfg.entity_prefix}_{i:06d}"
            initial, events = self._simulate_entity(entity_id, base_time)
            first_state[entity_id] = initial
            rows.extend(events)

        events_table = self._events_table(rows)
        summary = self._entity_summary(events_table, first_state)
        return WorkflowResult(
            events=events_table,
            entity_summary=summary,
            state_distribution=self._state_distribution(summary),
            stats=self._stats(events_table, summary),
        )

    # ---- one entity ---------------------------------------------------------------------

    def _simulate_entity(
        self, entity_id: str, base_time: dt.datetime
    ) -> tuple[str, list[tuple[str, str, str, dt.datetime, float, bool, str]]]:
        """``(initial state, events)`` of one entity; an event is ``(entity, from, to, at,
        dwell hours, is anomaly, anomaly type)``."""
        cfg = self._config
        events: list[tuple[str, str, str, dt.datetime, float, bool, str]] = []
        if not self._initial_states:
            return "", events
        current = self._initial_states[int(self._rng.integers(0, len(self._initial_states)))]
        initial = current
        now = base_time + dt.timedelta(hours=float(self._rng.uniform(0, 1)))
        visited = [current]

        for _ in range(cfg.max_transitions_per_entity):
            if current in self._terminal_states:
                break
            rule = self._pick_transition(current)
            if rule is None:
                break
            normal_next = rule.to_state
            if cfg.anomaly_enabled:
                actual, anomaly_type, is_anomaly = self._maybe_inject_anomaly(
                    current, normal_next, visited
                )
            else:
                actual, anomaly_type, is_anomaly = normal_next, "", False

            if anomaly_type == "stuck":
                events.append((entity_id, current, current, now, 0.0, True, "stuck"))
                break

            dwell = self._compute_dwell(rule)
            at = now + dt.timedelta(hours=dwell)
            events.append(
                (entity_id, current, actual, at, round(dwell, 4), is_anomaly, anomaly_type)
            )
            current = actual
            now = at
            visited.append(current)
        return initial, events

    def _pick_transition(self, state: str) -> TransitionRule | None:
        rules = self._transitions_by_state.get(state)
        if not rules:
            return None
        index = int(self._rng.choice(len(rules), p=self._probs_by_state[state]))
        return rules[index]

    def _compute_dwell(self, rule: TransitionRule) -> float:
        sample = float(self._rng.normal(rule.dwell_hours_mean, rule.dwell_hours_std))
        return max(rule.min_dwell_hours, sample)

    def _maybe_inject_anomaly(
        self, current: str, normal_next: str, visited: list[str]
    ) -> tuple[str, str, bool]:
        """``(actual next state, anomaly type, is anomaly)``: one roll decides between stuck
        (the entity stops), skip (jump to a state after the normal next one) and backward (return
        to a visited, non-terminal state); the rolls are cumulative in that order."""
        cfg = self._config
        roll = float(self._rng.random())
        threshold = cfg.anomaly_stuck_probability
        if roll < threshold:
            return current, "stuck", True

        threshold += cfg.anomaly_skip_probability
        if roll < threshold:
            skip_rules = self._transitions_by_state.get(normal_next)
            if skip_rules:
                target = skip_rules[int(self._rng.integers(0, len(skip_rules)))].to_state
                return target, "skip", True

        threshold += cfg.anomaly_backward_probability
        if roll < threshold:
            candidates = [s for s in visited if s != current and s not in self._terminal_states]
            if candidates:
                return candidates[int(self._rng.integers(0, len(candidates)))], "backward", True
        return normal_next, "", False

    # ---- tables -------------------------------------------------------------------------

    def _events_table(
        self, rows: list[tuple[str, str, str, dt.datetime, float, bool, str]]
    ) -> pa.Table:
        n = len(rows)
        if n == 0:
            return _EVENT_SCHEMA.empty_table()
        entity = np.array([r[0] for r in rows])
        at = np.array([r[3] for r in rows], dtype="datetime64[us]").astype("int64")
        # Entity, then time; rows of one entity are already in time order, ties stay in order.
        order = np.lexsort((at, np.unique(entity, return_inverse=True)[1]))
        ids = self._event_ids(n)
        cols = list(zip(*rows, strict=True))
        table = pa.table(
            {
                "event_id": pa.array(ids, pa.string()),
                "entity_id": pa.array(cols[0], pa.string()),
                "from_state": pa.array(cols[1], pa.string()),
                "to_state": pa.array(cols[2], pa.string()),
                "transitioned_at": pa.array(cols[3], pa.timestamp("us")),
                "dwell_hours": pa.array(cols[4], pa.float64()),
                "is_anomaly": pa.array(cols[5], pa.bool_()),
                "anomaly_type": pa.array(cols[6], pa.string()),
            }
        )
        return table.take(pa.array(order))

    def _event_ids(self, n: int) -> list[str]:
        high = self._ids.integers(0, 1 << 63, size=n, dtype=np.uint64)
        low = self._ids.integers(0, 1 << 63, size=n, dtype=np.uint64)
        return [
            str(uuid.UUID(int=(int(h) << 64) | int(lo), version=4))
            for h, lo in zip(high.tolist(), low.tolist(), strict=True)
        ]

    def _entity_summary(self, events: pa.Table, first_state: dict[str, str]) -> pa.Table:
        cfg = self._config
        if events.num_rows == 0:
            return _SUMMARY_SCHEMA.empty_table()
        entity = events.column("entity_id").to_pylist()
        from_state = events.column("from_state").to_pylist()
        to_state = events.column("to_state").to_pylist()
        dwell = events.column("dwell_hours").to_pylist()
        records: dict[str, list[Any]] = {}
        for i, e in enumerate(entity):
            rec = records.get(e)
            if rec is None:
                records[e] = [from_state[i], to_state[i], 1, dwell[i]]
            else:
                rec[1] = to_state[i]
                rec[2] += 1
                rec[3] += dwell[i]
        for i in range(cfg.entity_count):  # entities that never moved
            eid = f"{cfg.entity_prefix}_{i:06d}"
            if eid not in records:
                start = first_state.get(eid, "")
                records[eid] = [start, start, 0, 0.0]
        ids = sorted(records)
        return pa.table(
            {
                "entity_id": pa.array(ids, pa.string()),
                "initial_state": pa.array([records[e][0] for e in ids], pa.string()),
                "final_state": pa.array([records[e][1] for e in ids], pa.string()),
                "total_transitions": pa.array([records[e][2] for e in ids], pa.int64()),
                "total_hours": pa.array(
                    [round(float(records[e][3]), 4) for e in ids], pa.float64()
                ),
                "is_complete": pa.array(
                    [records[e][1] in self._terminal_states for e in ids], pa.bool_()
                ),
            }
        )

    @staticmethod
    def _state_distribution(summary: pa.Table) -> dict[str, int]:
        counts: dict[str, int] = {}
        for state in summary.column("final_state").to_pylist():
            counts[state] = counts.get(state, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def _stats(self, events: pa.Table, summary: pa.Table) -> dict[str, Any]:
        anomalies = sum(1 for v in events.column("is_anomaly").to_pylist() if v)
        complete = [
            h
            for h, c in zip(
                summary.column("total_hours").to_pylist(),
                summary.column("is_complete").to_pylist(),
                strict=True,
            )
            if c
        ]
        mean = round(float(np.mean(complete)), 4) if complete else 0.0
        return {
            "total_events": events.num_rows,
            "total_entities": summary.num_rows,
            "anomaly_count": anomalies,
            "mean_completion_hours": mean,
            "config_seed": self._config.seed,
        }


# ---- preset workflows ---------------------------------------------------------------------


def _preset_order_fulfillment() -> tuple[list[StateDefinition], list[TransitionRule]]:
    """Order fulfillment: created -> confirmed -> shipped -> delivered."""
    states = [
        StateDefinition("created", is_initial=True),
        StateDefinition("confirmed"),
        StateDefinition("shipped"),
        StateDefinition("delivered", is_terminal=True),
        StateDefinition("cancelled", is_terminal=True),
        StateDefinition("returned", is_terminal=True),
    ]
    t = TransitionRule
    transitions = [
        t("created", "confirmed", 0.90, 2.0, 0.5),
        t("created", "cancelled", 0.10, 1.0, 0.3),
        t("confirmed", "shipped", 0.95, 24.0, 6.0),
        t("confirmed", "cancelled", 0.05, 4.0, 1.0),
        t("shipped", "delivered", 0.92, 72.0, 24.0),
        t("shipped", "returned", 0.08, 120.0, 36.0),
    ]
    return states, transitions


def _preset_support_ticket() -> tuple[list[StateDefinition], list[TransitionRule]]:
    """Support ticket: opened -> triaged -> in_progress -> resolved -> closed."""
    states = [
        StateDefinition("opened", is_initial=True),
        StateDefinition("triaged"),
        StateDefinition("in_progress"),
        StateDefinition("escalated"),
        StateDefinition("resolved"),
        StateDefinition("closed", is_terminal=True),
        StateDefinition("reopened"),
    ]
    t = TransitionRule
    transitions = [
        t("opened", "triaged", 0.95, 1.0, 0.5),
        t("opened", "closed", 0.05, 0.5, 0.2),
        t("triaged", "in_progress", 0.80, 4.0, 2.0),
        t("triaged", "escalated", 0.20, 2.0, 1.0),
        t("in_progress", "resolved", 0.85, 8.0, 4.0),
        t("in_progress", "escalated", 0.15, 6.0, 3.0),
        t("escalated", "in_progress", 0.70, 12.0, 6.0),
        t("escalated", "resolved", 0.30, 24.0, 8.0),
        t("resolved", "closed", 0.90, 2.0, 1.0),
        t("resolved", "reopened", 0.10, 48.0, 24.0),
        t("reopened", "in_progress", 1.0, 2.0, 1.0),
    ]
    return states, transitions


def _preset_employee_onboarding() -> tuple[list[StateDefinition], list[TransitionRule]]:
    """Employee onboarding: applied -> screening -> interview -> offer -> hired."""
    states = [
        StateDefinition("applied", is_initial=True),
        StateDefinition("screening"),
        StateDefinition("interview"),
        StateDefinition("offer"),
        StateDefinition("hired", is_terminal=True),
        StateDefinition("rejected", is_terminal=True),
        StateDefinition("withdrawn", is_terminal=True),
    ]
    t = TransitionRule
    transitions = [
        t("applied", "screening", 0.70, 48.0, 24.0),
        t("applied", "rejected", 0.25, 72.0, 24.0),
        t("applied", "withdrawn", 0.05, 24.0, 12.0),
        t("screening", "interview", 0.60, 120.0, 48.0),
        t("screening", "rejected", 0.35, 96.0, 36.0),
        t("screening", "withdrawn", 0.05, 48.0, 24.0),
        t("interview", "offer", 0.40, 72.0, 24.0),
        t("interview", "rejected", 0.50, 48.0, 24.0),
        t("interview", "withdrawn", 0.10, 24.0, 12.0),
        t("offer", "hired", 0.80, 168.0, 72.0),
        t("offer", "rejected", 0.05, 120.0, 48.0),
        t("offer", "withdrawn", 0.15, 96.0, 48.0),
    ]
    return states, transitions


PRESET_WORKFLOWS: dict[str, Any] = {
    "order_fulfillment": _preset_order_fulfillment,
    "support_ticket": _preset_support_ticket,
    "employee_onboarding": _preset_employee_onboarding,
}


def get_preset_workflow(name: str) -> tuple[list[StateDefinition], list[TransitionRule]]:
    """``(states, transitions)`` of a preset workflow: ``order_fulfillment``, ``support_ticket``
    or ``employee_onboarding``.

    Raises:
        KeyError: ``name`` is not a preset.
    """
    if name not in PRESET_WORKFLOWS:
        available = ", ".join(sorted(PRESET_WORKFLOWS))
        raise KeyError(f"Unknown preset {name!r}. Available: {available}")
    states, transitions = PRESET_WORKFLOWS[name]()
    return list(states), list(transitions)
