"""The five behavior primitives: validation, boundaries, emitted counts against parameters,
determinism and resume (``shape_behavior.primitives``, ``docs/plugins/behavior.md`` section 11).

Statistical bounds are stated at each assertion: counts are compared with the binomial or
Poisson standard deviation, at five sigma, with fixed seeds (so a run is repeatable, and the
bound is wide enough that a correct implementation never fails by chance).
"""

import json
import math
from collections import Counter

import numpy as np
import pyarrow.compute as pc
import pytest
from shape_behavior import ModuleError, Population, SimConfig, Simulator
from shape_behavior.behaviors import ModuleBehavior, population_settings
from shape_behavior.primitives import (
    PRIMITIVES,
    PrimitiveParamsError,
    build,
    entity_lifecycle,
    event_sequence,
    file_arrival,
    telemetry_series,
    transaction_stream,
)

from shape.plugins import kit

T0 = "2020-01-01"
YEAR = 365.25 * 86400


def simulate(module, size=500, years=1.0, seed=3, **pop):
    settings = {**population_settings([module]), **pop}
    sim = Simulator([module], Population.from_dict(settings, size=size, start=T0), SimConfig(seed))
    from shape_behavior.timeutil import add_years, to_us

    return sim.run_until(add_years(to_us(T0), years))


def kinds(table):
    return Counter(table.column("kind").to_pylist())


def five_sigma_binomial(n, p):
    return 5 * math.sqrt(max(n * p * (1 - p), 1.0))


def five_sigma_poisson(mean):
    return 5 * math.sqrt(max(mean, 1.0))


# -- registry and construction ------------------------------------------------------------------


def test_five_primitives_exist_and_run_unchanged():
    assert sorted(PRIMITIVES) == [
        "entity_lifecycle",
        "event_sequence",
        "file_arrival",
        "telemetry_series",
        "transaction_stream",
    ]
    for name, builder in PRIMITIVES.items():
        m = builder()  # every default runs unchanged
        assert m.name == name and m.doc["format"] == "shape-behavior/1"
        assert m.doc["primitive"] == name and isinstance(m.doc["parameters"], dict)
        assert simulate(m, size=50, years=1.0).num_rows > 0


def test_defaults_are_recorded_as_the_effective_parameters():
    assert telemetry_series(noise=2.0).doc["parameters"]["noise"] == 2.0
    assert telemetry_series().doc["parameters"]["interval"] == "1 day"


def test_build_by_name_and_unknown_names_are_named():
    m = build("event_sequence", {"steps": ["a", "b"], "dropout": 0.5})
    assert m.doc["parameters"]["steps"] == ["a", "b"]
    with pytest.raises(PrimitiveParamsError, match="unknown primitive 'nope'") as e:
        build("nope", {})
    assert e.value.key == "primitive"
    with pytest.raises(PrimitiveParamsError, match="unknown parameter 'speed'") as e:
        build("telemetry_series", {"speed": 1})
    assert e.value.key == "speed"
    with pytest.raises(PrimitiveParamsError, match="params must be an object"):
        build("telemetry_series", [1])  # type: ignore[arg-type]


def test_module_is_deterministic_document():
    assert telemetry_series().digest() == telemetry_series().digest()
    assert telemetry_series().digest() != telemetry_series(level=21).digest()


# -- invalid parameters name the parameter ------------------------------------------------------

INVALID = [
    (
        "telemetry_series",
        dict(interval="0 days"),
        "telemetry_series: interval must be a positive duration, got '0 days'",
    ),
    (
        "telemetry_series",
        dict(interval="soon"),
        "telemetry_series: interval must be a positive duration, got 'soon'",
    ),
    (
        "telemetry_series",
        dict(interval=5),
        "telemetry_series: interval must be a positive duration, got 5",
    ),
    ("telemetry_series", dict(unit=""), "telemetry_series: unit must be a non-empty string"),
    (
        "telemetry_series",
        dict(level=float("nan")),
        "telemetry_series: level must be a finite number",
    ),
    (
        "telemetry_series",
        dict(noise=-1),
        "telemetry_series: noise must be a non-negative number, got -1",
    ),
    ("telemetry_series", dict(drift="up"), "telemetry_series: drift must be a finite number"),
    (
        "telemetry_series",
        dict(missing_rate=1.5),
        "telemetry_series: missing_rate must be between 0 and 1, got 1.5",
    ),
    (
        "telemetry_series",
        dict(stuck_rate=-0.1),
        "telemetry_series: stuck_rate must be between 0 and 1, got -0.1",
    ),
    (
        "telemetry_series",
        dict(missing_rate=True),
        "telemetry_series: missing_rate must be between 0 and 1, got True",
    ),
    ("event_sequence", dict(steps=[]), "event_sequence: steps must be a list of at least one"),
    ("event_sequence", dict(steps="visit"), "event_sequence: steps must be a list of at least one"),
    (
        "event_sequence",
        dict(steps=["a", "a"]),
        "event_sequence: steps must be unique, got 'a' twice",
    ),
    ("event_sequence", dict(steps=["a", ""]), "event_sequence: steps must be non-empty strings"),
    (
        "event_sequence",
        dict(dropout=1.2),
        "event_sequence: dropout must be between 0 and 1, got 1.2",
    ),
    (
        "event_sequence",
        dict(steps=["a", "b", "c"], dropout=[0.1]),
        "event_sequence: dropout list needs 2 values",
    ),
    (
        "event_sequence",
        dict(gap="-1 hours"),
        "event_sequence: gap must be a non-negative duration, got '-1 hours'",
    ),
    (
        "transaction_stream",
        dict(rate=0),
        "transaction_stream: rate must be a positive number, got 0",
    ),
    (
        "transaction_stream",
        dict(amount={"mu": 1}),
        "transaction_stream: amount needs 'mu' and 'sigma'",
    ),
    (
        "transaction_stream",
        dict(amount={"mu": 1, "sigma": -1}),
        "transaction_stream: amount.sigma must be a non-negative number",
    ),
    (
        "transaction_stream",
        dict(amount={"mu": 1, "sigma": 1, "x": 1}),
        "transaction_stream: amount has unknown key 'x'",
    ),
    (
        "transaction_stream",
        dict(refund_rate=-0.1),
        "transaction_stream: refund_rate must be between 0 and 1",
    ),
    (
        "transaction_stream",
        dict(refund_rate=0.6, reversal_rate=0.4),
        "transaction_stream: refund_rate + reversal_rate must be below 1",
    ),
    (
        "file_arrival",
        dict(schedule="monthly"),
        "file_arrival: schedule must be one of daily, hourly, weekly, got 'monthly'",
    ),
    ("file_arrival", dict(late_rate=2), "file_arrival: late_rate must be between 0 and 1"),
    (
        "file_arrival",
        dict(late_rate=0.5, missing_rate=0.4, duplicate_rate=0.2),
        "file_arrival: late_rate + missing_rate + duplicate_rate must not exceed 1",
    ),
    (
        "file_arrival",
        dict(late_delay="0 hours"),
        "file_arrival: late_delay must be a positive duration",
    ),
    (
        "file_arrival",
        dict(schedule="hourly", late_delay="2 hours"),
        "file_arrival: late_delay must be shorter than the schedule period",
    ),
    (
        "entity_lifecycle",
        dict(states=[]),
        "entity_lifecycle: states must be a list of at least one",
    ),
    ("entity_lifecycle", dict(states=["a", "a"]), "entity_lifecycle: states must be unique"),
    (
        "entity_lifecycle",
        dict(update_rate=-1),
        "entity_lifecycle: update_rate must be a non-negative number, got -1",
    ),
    (
        "entity_lifecycle",
        dict(delete_rate=float("inf")),
        "entity_lifecycle: delete_rate must be a non-negative number",
    ),
]


@pytest.mark.parametrize(("name", "kwargs", "message"), INVALID)
def test_invalid_parameter_raises_module_error_naming_it(name, kwargs, message):
    with pytest.raises(ModuleError) as e:
        PRIMITIVES[name](**kwargs)
    assert message in str(e.value)
    assert any(message in p for p in e.value.problems)


def test_every_problem_is_reported_at_once():
    with pytest.raises(ModuleError) as e:
        telemetry_series(interval="0 days", noise=-1)
    assert len(e.value.problems) == 2


# -- event_sequence -----------------------------------------------------------------------------


def test_event_sequence_funnel_shares_match_the_continue_probabilities():
    steps = ["visit", "view", "add_to_cart", "checkout"]
    n, p = 4000, 0.6  # dropout 0.4 at each step
    t = simulate(event_sequence(steps=steps, dropout=0.4), size=n, years=1.0)
    c = kinds(t)
    assert c["visit"] == n  # every entity enters the funnel
    expected = n
    for step in steps[1:]:
        expected *= p
        # bound: five binomial sigmas around n * p^k
        assert abs(c[step] - expected) <= five_sigma_binomial(n, expected / n), (step, c[step])


def test_event_sequence_per_step_probabilities_and_order_and_gap():
    m = event_sequence(steps=["a", "b", "c"], dropout=[0.0, 1.0], gap="10 minutes")
    t = simulate(m, size=100, years=1.0)
    assert kinds(t) == Counter({"a": 100, "b": 100})  # stops after b with certainty
    rows = t.to_pylist()
    by_entity: dict[int, list[dict]] = {}
    for r in rows:
        by_entity.setdefault(r["entity_id"], []).append(r)
    for evs in by_entity.values():
        assert [e["kind"] for e in evs] == ["a", "b"]
        assert (evs[1]["time"] - evs[0]["time"]).total_seconds() == 600


def test_event_sequence_boundaries_dropout_zero_and_one():
    steps = ["a", "b", "c"]
    full = kinds(simulate(event_sequence(steps=steps, dropout=0), size=200))
    assert full == Counter({"a": 200, "b": 200, "c": 200})
    first = kinds(simulate(event_sequence(steps=steps, dropout=1), size=200))
    assert first == Counter({"a": 200})
    single = kinds(simulate(event_sequence(steps=["only"], dropout=0.5), size=50))
    assert single == Counter({"only": 50})
    zero_gap = simulate(event_sequence(steps=["a", "b"], dropout=0, gap="0 seconds"), size=10)
    assert zero_gap.num_rows == 20


def test_event_sequence_gap_longer_than_the_run_cuts_the_funnel_at_the_horizon():
    t = simulate(event_sequence(steps=["a", "b"], dropout=0, gap="2 years"), size=50, years=0.5,
                 arrival={"kind": "at_start"})  # fmt: skip
    assert kinds(t) == Counter({"a": 50})


# -- telemetry_series ---------------------------------------------------------------------------


def test_telemetry_reading_count_value_and_unit():
    m = telemetry_series(interval="1 hour", unit="kPa", level=100, noise=0, drift=0,
                         missing_rate=0, stuck_rate=0)  # fmt: skip
    t = simulate(m, size=20, years=10 / 365.25)  # ten days
    assert kinds(t) == Counter({"reading": 20 * (10 * 24 + 1)})  # readings at t0 and each hour
    assert set(t.column("unit").to_pylist()) == {"kPa"}
    assert set(t.column("value").to_pylist()) == {100.0}


def test_telemetry_missing_rate_matches():
    m = telemetry_series(interval="1 hour", missing_rate=0.2, stuck_rate=0, noise=1)
    n_slots = 100 * (30 * 24 + 1)
    t = simulate(m, size=100, years=30 / 365.25)
    got = t.num_rows
    # bound: five binomial sigmas around 80 % of the slots
    assert abs(got - 0.8 * n_slots) <= five_sigma_binomial(n_slots, 0.8)


def test_telemetry_stuck_rate_is_the_share_of_repeated_readings():
    m = telemetry_series(interval="1 hour", missing_rate=0, stuck_rate=0.25, noise=1.0, drift=0)
    t = simulate(m, size=50, years=40 / 365.25)
    rows = sorted(
        zip(
            t.column("entity_id").to_pylist(),
            t.column("time").to_pylist(),
            t.column("value").to_pylist(),
            strict=True,
        )
    )
    repeats = total = 0
    for (e1, _, v1), (e2, _, v2) in zip(rows, rows[1:], strict=False):
        if e1 == e2:
            total += 1
            repeats += v1 == v2
    # bound: five binomial sigmas (a fresh gaussian reading equals the last with probability 0)
    assert abs(repeats - 0.25 * total) <= five_sigma_binomial(total, 0.25)


def test_telemetry_noise_level_and_drift():
    m = telemetry_series(
        interval="1 day", level=10, noise=2.0, drift=3.0, missing_rate=0, stuck_rate=0
    )
    t = simulate(m, size=200, years=2.0)
    values = np.array(t.column("value").to_pylist())
    times = np.array([x.timestamp() for x in t.column("time").to_pylist()])
    years = (times - times.min()) / YEAR
    slope, intercept = np.polyfit(years, values, 1)
    assert abs(slope - 3.0) < 0.05  # drift per year; standard error of the slope is about 0.01
    assert abs(intercept - 10.0) < 0.15
    resid = values - (intercept + slope * years)
    assert abs(resid.std() - 2.0) < 0.05  # gaussian noise


def test_telemetry_boundaries_missing_one_and_interval_longer_than_run():
    none = simulate(telemetry_series(missing_rate=1), size=30, years=1.0)
    assert none.num_rows == 0
    all_stuck = simulate(telemetry_series(stuck_rate=1, missing_rate=0, noise=1), size=5, years=0.2)
    per_entity: dict[int, set] = {}
    for e, v in zip(all_stuck.column("entity_id").to_pylist(),
                    all_stuck.column("value").to_pylist(), strict=True):  # fmt: skip
        per_entity.setdefault(e, set()).add(v)
    assert all(len(v) == 1 for v in per_entity.values())  # first reading repeated for ever
    one = simulate(telemetry_series(interval="5 years", missing_rate=0), size=12, years=1.0)
    assert one.num_rows == 12  # the reading at the entity's arrival only


def test_telemetry_reading_state_type_is_registered_and_whole_array():
    from shape_behavior.extension import get_handler

    handler = get_handler("telemetry_reading")
    assert handler is not None
    assert handler.validate({"type": "telemetry_reading"})  # missing fields are reported


# -- transaction_stream -------------------------------------------------------------------------


def test_transaction_rate_per_year_and_exponential_gaps():
    rate, n = 12.0, 1000
    t = simulate(transaction_stream(rate=rate, refund_rate=0, reversal_rate=0), size=n, years=3.0)
    tx = t.filter(pc.equal(t.column("kind"), "transaction"))
    expected = rate * 3.0 * n
    assert abs(tx.num_rows - expected) <= five_sigma_poisson(expected)
    # gaps between consecutive transactions of one entity: mean 1/rate years, coefficient of
    # variation 1 (exponential); bounds are wide for the sample size (about 36,000 gaps)
    by_entity: dict[int, list[float]] = {}
    for e, ts in zip(
        tx.column("entity_id").to_pylist(), tx.column("time").to_pylist(), strict=True
    ):
        by_entity.setdefault(e, []).append(ts.timestamp())
    gaps = np.concatenate([np.diff(sorted(v)) for v in by_entity.values() if len(v) > 1]) / YEAR
    assert abs(gaps.mean() * rate - 1) < 0.05
    assert abs(gaps.std() / gaps.mean() - 1) < 0.05


def test_transaction_amounts_are_lognormal():
    t = simulate(transaction_stream(rate=20, amount={"mu": 3.0, "sigma": 0.5}), size=500, years=2.0)
    tx = t.filter(pc.equal(t.column("kind"), "transaction"))
    logs = np.log(np.array(tx.column("value").to_pylist()))
    assert abs(logs.mean() - 3.0) < 0.02 and abs(logs.std() - 0.5) < 0.02
    assert all(c for c in tx.column("code").to_pylist())  # a transaction id


def test_refunds_and_reversals_follow_at_their_rates_and_reference_the_original():
    refund, reversal, n = 0.10, 0.05, 1500
    t = simulate(transaction_stream(rate=20, refund_rate=refund, reversal_rate=reversal),
                 size=n, years=2.0)  # fmt: skip
    c = kinds(t)
    tx = c["transaction"]
    # each transaction is followed by a refund (reversal) before the next one with the given
    # probability; the last transaction's follow-up may fall after the horizon (at most n of them)
    for kind, p in (("refund", refund), ("reversal", reversal)):
        expected = tx * p
        assert abs(c[kind] - expected) <= five_sigma_binomial(tx, p) + 0.1 * n * p, kind
    original = {}
    order = {}
    for r in sorted(t.to_pylist(), key=lambda r: (r["entity_id"], r["time"], r["seq"])):
        if r["kind"] == "transaction":
            original[(r["entity_id"], r["code"])] = r
            order[r["entity_id"]] = r["code"]
        elif r["kind"] in ("refund", "reversal"):
            assert r["ref"] == order[r["entity_id"]]  # the most recent transaction
            o = original[(r["entity_id"], r["ref"])]
            assert r["time"] > o["time"] and r["value"] == o["value"]
            order[r["entity_id"]] = None  # at most one follow-up per transaction
    assert all(r["ref"] is None for r in t.to_pylist() if r["kind"] == "transaction")


def test_transaction_boundaries_no_follow_ups_and_ids_are_unique():
    t = simulate(transaction_stream(rate=30, refund_rate=0, reversal_rate=0), size=100, years=1.0)
    assert set(kinds(t)) == {"transaction"}
    ids = [
        (e, c)
        for e, c in zip(
            t.column("entity_id").to_pylist(), t.column("code").to_pylist(), strict=True
        )
    ]
    assert len(set(ids)) == len(ids)
    only_refunds = simulate(
        transaction_stream(rate=30, refund_rate=0.99, reversal_rate=0), size=100
    )
    assert kinds(only_refunds)["refund"] > 0 and "reversal" not in kinds(only_refunds)


# -- file_arrival -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schedule", "slots_per_day"), [("daily", 1), ("hourly", 24), ("weekly", 1 / 7)]
)
def test_file_arrival_one_outcome_per_slot(schedule, slots_per_day):
    m = file_arrival(schedule=schedule, late_rate=0, missing_rate=0, duplicate_rate=0)
    days = 28
    t = simulate(m, size=10, years=days / 365.25)
    slots = int(days * slots_per_day) + 1  # a slot at t0, the last at or before the horizon
    assert kinds(t) == Counter({"file_arrived": 10 * slots})
    payloads = {json.loads(p)["slot"] for p in t.column("payload").to_pylist()}
    assert len(payloads) == slots
    assert min(payloads) == "2020-01-01T00:00:00"


def test_file_arrival_outcome_shares():
    late, missing, dup, n = 0.15, 0.10, 0.05, 40
    m = file_arrival(schedule="hourly", late_rate=late, missing_rate=missing, duplicate_rate=dup)
    t = simulate(m, size=n, years=100 / 365.25)
    c = kinds(t)
    slots = n * (100 * 24 + 1)
    # bound: five binomial sigmas of the slot count (the last day's late events can fall after
    # the horizon: allow the slots of the final late_delay window)
    slack = n * 2
    assert abs(c["file_late"] - late * slots) <= five_sigma_binomial(slots, late) + slack
    assert abs(c["file_missing"] - missing * slots) <= five_sigma_binomial(slots, missing) + slack
    assert abs(c["file_duplicate"] - dup * slots) <= five_sigma_binomial(slots, dup) + slack
    arrived = (1 - late - missing) * slots  # on time or duplicated
    assert (
        abs(c["file_arrived"] - arrived) <= five_sigma_binomial(slots, 1 - late - missing) + slack
    )


def test_file_arrival_late_and_duplicate_times_and_slot_payload():
    m = file_arrival(
        schedule="daily", late_rate=1, missing_rate=0, duplicate_rate=0, late_delay="5 hours"
    )
    t = simulate(m, size=3, years=10 / 365.25)
    assert set(kinds(t)) == {"file_late"}
    for r in t.to_pylist():
        slot = np.datetime64(json.loads(r["payload"])["slot"])
        assert np.datetime64(r["time"], "s") - slot == np.timedelta64(5, "h")
    d = file_arrival(schedule="daily", late_rate=0, missing_rate=0, duplicate_rate=1)
    rows = simulate(d, size=1, years=5 / 365.25).to_pylist()
    first, second = rows[0], rows[1]
    assert (first["kind"], second["kind"]) == ("file_arrived", "file_duplicate")
    assert json.loads(first["payload"]) == json.loads(second["payload"])
    assert (second["time"] - first["time"]).total_seconds() == 86400 / 10


def test_file_arrival_boundaries():
    assert set(kinds(simulate(file_arrival(missing_rate=1, late_rate=0, duplicate_rate=0)))) == {
        "file_missing"
    }
    assert set(kinds(simulate(file_arrival(late_rate=1, missing_rate=0, duplicate_rate=0)))) == {
        "file_late"
    }
    short = simulate(
        file_arrival(late_rate=0, missing_rate=0, duplicate_rate=0), size=4, years=0.5 / 365.25
    )
    assert short.num_rows == 4  # a run shorter than the schedule holds only the first slot
    eq = file_arrival(late_rate=0.5, missing_rate=0.5, duplicate_rate=0)  # rates may sum to 1
    assert eq.doc["parameters"]["late_rate"] == 0.5


# -- entity_lifecycle ---------------------------------------------------------------------------


def test_lifecycle_create_update_delete_counts():
    u, d, n, years = 4.0, 0.5, 2000, 3.0
    t = simulate(entity_lifecycle(update_rate=u, delete_rate=d), size=n, years=years,
                 arrival={"kind": "at_start"})  # fmt: skip
    c = kinds(t)
    assert c["created"] == n
    deleted_share = 1 - math.exp(-d * years)  # time to delete is exponential whatever the updates
    assert abs(c["deleted"] - n * deleted_share) <= five_sigma_binomial(n, deleted_share)
    # updates accrue at rate u while the entity is alive; expected live time per entity
    live = (1 - math.exp(-d * years)) / d
    expected = n * u * live
    assert abs(c["updated"] - expected) <= five_sigma_poisson(expected) + 0.05 * expected


def test_lifecycle_order_versions_states_and_nothing_after_delete():
    states = ["new", "active", "suspended"]
    t = simulate(entity_lifecycle(states=states, update_rate=6, delete_rate=1), size=300, years=2.0)
    by_entity: dict[int, list[dict]] = {}
    for r in sorted(t.to_pylist(), key=lambda r: (r["entity_id"], r["time"], r["seq"])):
        by_entity.setdefault(r["entity_id"], []).append(r)
    for evs in by_entity.values():
        assert evs[0]["kind"] == "created" and evs[0]["text"] == "new" and evs[0]["value"] == 1
        assert all(e["kind"] == "updated" for e in evs[1:-1] if e["kind"] != "deleted")
        assert "deleted" not in [e["kind"] for e in evs[:-1]]
        for prev, cur in zip(evs, evs[1:], strict=False):
            assert cur["text"] in states
            if cur["kind"] == "updated":
                assert cur["text"] != prev["text"] and cur["value"] == prev["value"] + 1
            else:
                assert cur["kind"] == "deleted" and cur["value"] == prev["value"]


def test_lifecycle_boundaries():
    only_create = simulate(entity_lifecycle(update_rate=0, delete_rate=0), size=40)
    assert kinds(only_create) == Counter({"created": 40})
    no_delete = kinds(simulate(entity_lifecycle(update_rate=5, delete_rate=0), size=40))
    assert "deleted" not in no_delete and no_delete["updated"] > 0
    only_delete = kinds(simulate(entity_lifecycle(update_rate=0, delete_rate=20), size=40, years=3))
    assert only_delete == Counter({"created": 40, "deleted": 40})
    one_state = simulate(entity_lifecycle(states=["only"], update_rate=5, delete_rate=0), size=20)
    assert set(one_state.column("text").to_pylist()) == {"only"}


# -- determinism and resume ---------------------------------------------------------------------

SMALL = {
    "event_sequence": {},
    "telemetry_series": {"interval": "6 hours"},
    "transaction_stream": {"rate": 40},
    "file_arrival": {"schedule": "daily", "late_rate": 0.2, "duplicate_rate": 0.1},
    "entity_lifecycle": {"update_rate": 20, "delete_rate": 1},
}


def _windows(sim, years):
    from shape_behavior.timeutil import add_years, to_us

    return [sim.run_until(add_years(to_us(T0), y)) for y in years]


@pytest.mark.parametrize("name", sorted(SMALL))
def test_same_seed_same_events_and_resume_equals_straight_run(name, tmp_path):
    m = build(name, SMALL[name])
    pop = Population.from_dict(population_settings([m]), size=150, start=T0)
    straight = _windows(Simulator([m], pop, SimConfig(seed=9)), [0.5, 1.0, 1.5, 2.0])
    again = _windows(Simulator([m], pop, SimConfig(seed=9)), [0.5, 1.0, 1.5, 2.0])
    other = _windows(Simulator([m], pop, SimConfig(seed=10)), [0.5, 1.0, 1.5, 2.0])
    assert all(a.equals(b) for a, b in zip(straight, again, strict=True))
    assert not all(a.equals(b) for a, b in zip(straight, other, strict=True))
    assert sum(t.num_rows for t in straight) > 0

    sim = Simulator([m], pop, SimConfig(seed=9))
    first = _windows(sim, [0.5, 1.0])
    path = tmp_path / "ck.npz"
    sim.checkpoint().save(path)
    resumed = Simulator.resume(path, [build(name, SMALL[name])])
    rest = _windows(resumed, [1.5, 2.0])
    assert all(a.equals(b) for a, b in zip(first + rest, straight, strict=True))


# -- registration and the conformance kit -------------------------------------------------------

NAMES = sorted(PRIMITIVES)


@pytest.mark.parametrize("name", NAMES)
def test_registered_behavior_passes_the_kit_and_declares_its_kinds(name):
    from shape.plugins.host import PluginHost

    host = PluginHost()
    rec = host.record("shape.behaviors", name)
    assert rec is not None and rec.source == "sqllocks-shape-behavior"
    obj = host.get("shape.behaviors", name)
    assert isinstance(obj, ModuleBehavior) and obj.name == name
    kit.check_behavior(obj)
    kit.check_plugin("shape.behaviors", obj)
    table = obj.simulate(100, 4, 1.0)
    assert set(table.column("kind").to_pylist()) - {"entity_end"} <= set(obj.events)
    assert obj.module.doc["parameters"] == PRIMITIVES[name]().doc["parameters"]


def test_declared_event_kinds():
    from shape.plugins.host import PluginHost

    ev = {n: set(PluginHost().get("shape.behaviors", n).events) for n in NAMES}
    assert ev["event_sequence"] == {"visit", "view", "add_to_cart", "checkout"}
    assert ev["telemetry_series"] == {"reading"}
    assert ev["transaction_stream"] == {"transaction", "refund", "reversal"}
    assert ev["file_arrival"] == {"file_arrived", "file_late", "file_missing", "file_duplicate"}
    assert ev["entity_lifecycle"] == {"created", "updated", "deleted"}


def test_light_name_list_matches_the_builders():
    from shape_behavior.params import PRIMITIVE_NAMES

    assert list(PRIMITIVE_NAMES) == sorted(PRIMITIVES)
