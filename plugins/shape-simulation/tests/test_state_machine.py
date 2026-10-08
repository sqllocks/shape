"""The workflow simulator: presets, anomalies, summaries and reproducibility."""

from __future__ import annotations

import re

import pytest
from shape_simulation.state_machine import (
    PRESET_WORKFLOWS,
    StateDefinition,
    TransitionRule,
    WorkflowConfig,
    WorkflowSimulator,
    get_preset_workflow,
)


def simulate(preset: str = "order_fulfillment", **kw: object):  # type: ignore[no-untyped-def]
    states, transitions = get_preset_workflow(preset)
    cfg = WorkflowConfig(states=states, transitions=transitions, entity_count=400, seed=4, **kw)  # type: ignore[arg-type]
    return WorkflowSimulator(cfg).run(), cfg


def test_presets_exist_and_unknown_is_a_key_error() -> None:
    assert set(PRESET_WORKFLOWS) == {"order_fulfillment", "support_ticket", "employee_onboarding"}
    with pytest.raises(KeyError, match="Available"):
        get_preset_workflow("nope")


def test_events_are_sorted_by_entity_then_time_and_follow_the_graph() -> None:
    result, _ = simulate(anomaly_enabled=False)
    events = result.events.to_pylist()
    assert events == sorted(events, key=lambda e: (e["entity_id"], e["transitioned_at"]))
    allowed = {(r.from_state, r.to_state) for r in get_preset_workflow("order_fulfillment")[1]}
    assert all((e["from_state"], e["to_state"]) in allowed for e in events)
    assert not any(e["is_anomaly"] for e in events) and {e["anomaly_type"] for e in events} == {""}
    for e in events:  # the dwell is the gap to the previous event of the entity, at least the floor
        assert e["dwell_hours"] >= 0.1


def test_every_entity_ends_in_a_terminal_state_without_anomalies() -> None:
    result, cfg = simulate(anomaly_enabled=False)
    terminal = {s.name for s in cfg.states if s.is_terminal}
    summary = result.entity_summary.to_pylist()
    assert len(summary) == 400 and all(r["is_complete"] for r in summary)
    assert {r["final_state"] for r in summary} <= terminal
    assert sum(result.state_distribution.values()) == 400
    assert result.stats["total_entities"] == 400 and result.stats["anomaly_count"] == 0


def test_anomalies_are_flagged_and_stuck_entities_stay_put() -> None:
    result, _ = simulate(
        anomaly_stuck_probability=0.1,
        anomaly_skip_probability=0.1,
        anomaly_backward_probability=0.1,
    )
    events = result.events.to_pylist()
    kinds = {e["anomaly_type"] for e in events}
    assert {"stuck", "skip", "backward", ""} <= kinds
    for e in events:
        assert e["is_anomaly"] == (e["anomaly_type"] != "")
        if e["anomaly_type"] == "stuck":
            assert e["from_state"] == e["to_state"] and e["dwell_hours"] == 0.0
    assert result.stats["anomaly_count"] == sum(e["is_anomaly"] for e in events)


def test_the_same_seed_gives_the_same_ids_and_other_seeds_other_events() -> None:
    a, cfg = simulate()
    b, _ = simulate()
    assert a.events.equals(b.events)
    uuid4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    ids = a.events.column("event_id").to_pylist()
    assert len(set(ids)) == len(ids) and all(uuid4.match(i) for i in ids)
    cfg.seed = 5
    assert not WorkflowSimulator(cfg).run().events.equals(a.events)


def test_an_entity_that_cannot_move_is_summarised_with_its_own_start_state() -> None:
    """Regression (WF-1): the baseline reported such entities in the first initial state."""
    states = [
        StateDefinition("new", is_initial=True),
        StateDefinition("parked", is_initial=True),  # nothing leaves it
        StateDefinition("done", is_terminal=True),
    ]
    cfg = WorkflowConfig(
        states=states,
        transitions=[TransitionRule("new", "done")],
        entity_count=200,
        seed=1,
        anomaly_enabled=False,
    )
    result = WorkflowSimulator(cfg).run()
    stuck = [r for r in result.entity_summary.to_pylist() if r["total_transitions"] == 0]
    assert stuck and {r["initial_state"] for r in stuck} == {"parked"}
    assert {r["final_state"] for r in stuck} == {"parked"}
    assert result.state_distribution["parked"] == len(stuck)


def test_no_initial_state_means_no_events_and_zero_probability_is_refused() -> None:
    empty = WorkflowSimulator(WorkflowConfig(states=[StateDefinition("a")], entity_count=5)).run()
    assert empty.events.num_rows == 0 and empty.entity_summary.num_rows == 0
    assert empty.stats["total_events"] == 0 and empty.stats["mean_completion_hours"] == 0.0
    with pytest.raises(ValueError, match="no positive probability"):
        WorkflowSimulator(
            WorkflowConfig(
                states=[StateDefinition("a", is_initial=True), StateDefinition("b")],
                transitions=[TransitionRule("a", "b", probability=0.0)],
            )
        )


def test_start_time_prefix_and_transition_limit() -> None:
    result, _ = simulate(
        "support_ticket",
        start_time="2025-03-01T08:30:00",
        entity_prefix="ticket",
        max_transitions_per_entity=2,
    )
    first = result.events.to_pylist()[0]
    assert first["entity_id"] == "ticket_000000" and first["transitioned_at"].year == 2025
    per_entity: dict[str, int] = {}
    for e in result.events.to_pylist():
        per_entity[e["entity_id"]] = per_entity.get(e["entity_id"], 0) + 1
    assert max(per_entity.values()) <= 2


def test_every_entity_has_a_summary_row_even_when_none_moves():
    # Issue #441: with no event at all the summary was empty and total_entities was 0, while a
    # run where some entities moved listed the others too.
    states = [StateDefinition("done", is_initial=True, is_terminal=True)]
    transitions = [TransitionRule("done", "done")]
    r = WorkflowSimulator(
        WorkflowConfig(states=states, transitions=transitions, entity_count=5, seed=1)
    ).run()
    assert r.events.num_rows == 0
    assert r.entity_summary.num_rows == 5
    assert r.entity_summary.column("final_state").to_pylist() == ["done"] * 5
    assert r.entity_summary.column("total_transitions").to_pylist() == [0] * 5
    assert r.stats["total_entities"] == 5
    assert r.state_distribution == {"done": 5}
