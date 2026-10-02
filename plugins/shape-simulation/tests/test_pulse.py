"""Pulse patterns: trip enrichment, events, surge signals, pings and the marts."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pyarrow as pa
import pytest

from shape_simulation.pulse_patterns import CITY_CENTROIDS, PulseDemandConfig, PulseDemandSimulator


def run(tables, **cfg):
    return PulseDemandSimulator(tables, PulseDemandConfig(**cfg)).run()


def test_deterministic_and_seed_sensitive(pulse_tables):
    a, b, c = run(pulse_tables, seed=3), run(pulse_tables, seed=3), run(pulse_tables, seed=4)
    assert all(a.tables[k].equals(b.tables[k]) for k in a.tables) and a.stats == b.stats
    assert not a.tables["trip"].equals(c.tables["trip"])
    assert list(a.tables) == ["trip", "trip_events", "surge_signals", "driver_pings", "fact_revenue_daily", "fact_driver_earnings"]
    assert a.table_map() is a.tables and "PulseSimResult(trip=900" in repr(a)


def test_trip_enrichment(pulse_tables):
    r = run(pulse_tables)
    t, src = r.tables["trip"].to_pydict(), pulse_tables["trip"].to_pydict()
    assert r.tables["trip"].column_names[:11] == pulse_tables["trip"].column_names
    assert r.tables["trip"].column_names[11:] == [
        "pickup_lat", "pickup_lon", "dropoff_lat", "dropoff_lon", "accepted_at", "started_at",
        "completed_at", "wait_min", "trip_date", "eta_promised_min", "eta_actual_min",
        "is_cancelled", "cancel_reason", "source_store",
    ]
    for i, status in enumerate(src["status"]):
        city = CITY_CENTROIDS[int(src["city_id"][i])]
        assert abs(t["pickup_lat"][i] - city[2]) < 0.8 and abs(t["pickup_lon"][i] - city[3]) < 0.8
        assert (t["accepted_at"][i] is None) == (status == "no_driver")
        done = status == "completed"
        assert (t["started_at"][i] is not None) == done == (t["completed_at"][i] is not None) == (t["wait_min"][i] is not None)
        assert (t["fare"][i] is not None) == done == (t["tip"][i] is not None)
        assert t["is_cancelled"][i] == (status == "cancelled") == (t["cancel_reason"][i] is not None)
        assert t["trip_date"][i] == src["requested_at"][i].date()
        assert t["eta_actual_min"][i] >= 1.0
        if done:
            req = src["requested_at"][i]
            assert req <= t["accepted_at"][i] <= t["started_at"][i] <= t["completed_at"][i]
            assert (t["started_at"][i] - t["accepted_at"][i]).total_seconds() / 60 == pytest.approx(t["wait_min"][i], abs=0.01)
            assert (t["completed_at"][i] - t["started_at"][i]).total_seconds() / 60 == pytest.approx(src["duration_min"][i], abs=0.01)
    assert set(t["source_store"]) == {"SQL Database"}
    assert pulse_tables["trip"].column_names == list(src)  # the input is untouched


def test_unknown_city_scatters_around_the_first_metro(pulse_tables):
    trip = pulse_tables["trip"]
    odd = trip.set_column(trip.column_names.index("city_id"), "city_id", pa.array(np.full(trip.num_rows, 9.0)))
    r = run({"trip": odd})
    lat = np.asarray(r.tables["trip"].column("pickup_lat").to_numpy())
    assert abs(lat.mean() - CITY_CENTROIDS[1][2]) < 0.05


def test_trip_events(pulse_tables):
    r = run(pulse_tables)
    ev, src = r.tables["trip_events"].to_pydict(), pulse_tables["trip"].to_pydict()
    kinds: dict[int, list[str]] = {}
    for tid, k in zip(ev["trip_id"], ev["event_type"], strict=True):
        kinds.setdefault(tid, []).append(k)
    want = {"completed": ["requested", "accepted", "started", "completed"], "cancelled": ["requested", "accepted", "cancelled"], "no_driver": ["requested"]}
    for tid, status in zip(src["trip_id"], src["status"], strict=True):
        assert sorted(kinds[tid]) == sorted(want[status])
    assert ev["ts"] == sorted(ev["ts"])
    assert all(e == f"{t}-{k}" for e, t, k in zip(ev["event_id"], ev["trip_id"], ev["event_type"], strict=True))
    trips = r.tables["trip"].to_pydict()
    row = {t: i for i, t in enumerate(trips["trip_id"])}
    for t, k, ts, lat in zip(ev["trip_id"], ev["event_type"], ev["ts"], ev["lat"], strict=True):
        assert lat == trips["pickup_lat"][row[t]]
        if k == "cancelled":
            assert 30 <= (ts - trips["accepted_at"][row[t]]).total_seconds() <= 300
    assert set(ev["source_store"]) == {"Eventhouse"}


def test_surge_signals(pulse_tables):
    r = run(pulse_tables, surge_events_per_week=20.0, surge_recent_days=2, surge_bucket_minutes=15, surge_multiplier_range=(2.0, 3.0))
    s = r.tables["surge_signals"].to_pydict()
    latest = max(pulse_tables["trip"].column("requested_at").to_pylist())
    buckets = 2 * 24 * 4 + 1
    assert len(s["signal_id"]) == 4 * buckets and set(s["city_id"]) == {1, 2, 3, 4}
    assert max(s["ts"]) == latest and min(s["ts"]) == latest - dt.timedelta(days=2)
    assert min(s["multiplier"]) == 1.0 and max(s["multiplier"]) <= 3.0
    for trig, mult in zip(s["trigger"], s["multiplier"], strict=True):
        assert (trig == "baseline") == (mult == 1.0)
    assert sum(1 for m in s["multiplier"] if m > 1.0) > 0 and all(20 <= a < 200 for a in s["active_drivers"])
    assert set(s["zone"]) <= {"downtown", "airport", "north", "south", "east", "west"}


def test_driver_pings(pulse_tables):
    r = run(pulse_tables, live_window_minutes=60 * 24 * 3, max_live_trips=50, ping_interval_seconds=60, gps_jitter_meters=5.0)
    p, trips = r.tables["driver_pings"].to_pydict(), r.tables["trip"].to_pydict()
    row = {t: i for i, t in enumerate(trips["trip_id"])}
    by: dict[int, list[int]] = {}
    for i, t in enumerate(p["trip_id"]):
        by.setdefault(t, []).append(i)
    assert len(by) == 50 and p["ts"] == sorted(p["ts"])
    for t, idx in by.items():
        i = row[t]
        assert trips["status"][i] == "completed"
        secs = (trips["completed_at"][i] - trips["started_at"][i]).total_seconds()
        assert len(idx) == min(max(2, int(secs // 60)), 60)
        assert all(trips["started_at"][i] <= p["ts"][j] <= trips["completed_at"][i] for j in idx)
        assert all(p["driver_id"][j] == trips["driver_id"][i] for j in idx)
        first, last = sorted(idx, key=lambda j: p["ts"][j])[0], sorted(idx, key=lambda j: p["ts"][j])[-1]
        assert abs(p["lat"][first] - trips["pickup_lat"][i]) < 0.001 and abs(p["lat"][last] - trips["dropoff_lat"][i]) < 0.001
    assert all(0 <= s <= 80 for s in p["speed_mph"]) and all(0 <= h <= 360 for h in p["heading"])
    assert p["ping_id"][0] == f"{p['trip_id'][0]}-{int(p['ping_id'][0].rsplit('-', 1)[1])}"
    assert set(p["status"]) == {"on_trip"} and set(p["source_store"]) == {"Eventhouse"}


def test_pings_only_for_the_live_window(pulse_tables):
    r = run(pulse_tables, live_window_minutes=45, max_live_trips=10_000)
    trips = r.tables["trip"].to_pydict()
    now = max(pulse_tables["trip"].column("requested_at").to_pylist())
    live = {
        t
        for t, s, c, st in zip(trips["trip_id"], trips["started_at"], trips["completed_at"], trips["status"], strict=True)
        if st == "completed" and c >= now - dt.timedelta(minutes=45) and s <= now
    }
    assert 5 < len(live) < 100
    assert set(r.tables["driver_pings"].column("trip_id").to_pylist()) == live
    only_open = pulse_tables["trip"].filter(pa.array([s != "completed" for s in pulse_tables["trip"].column("status").to_pylist()]))
    empty = PulseDemandSimulator({"trip": only_open}).run()
    assert empty.tables["driver_pings"].num_rows == 0 and empty.tables["driver_pings"].schema.field("lat").type == pa.float64()
    assert empty.tables["fact_driver_earnings"].num_rows == 0 and empty.tables["fact_revenue_daily"].num_rows > 0


def test_revenue_mart_matches_a_plain_recomputation(pulse_tables):
    r = run(pulse_tables)
    trips = r.tables["trip"].to_pydict()
    groups: dict[tuple[int, float], dict[str, list]] = {}
    for i, status in enumerate(trips["status"]):
        d = trips["requested_at"][i]
        g = groups.setdefault((d.year * 10000 + d.month * 100 + d.day, trips["city_id"][i]), {"all": [], "done": []})
        g["all"].append(i)
        if status == "completed":
            g["done"].append(i)
    rev = r.tables["fact_revenue_daily"].to_pydict()
    assert list(zip(rev["date_key"], rev["city_id"], strict=True)) == sorted(groups)
    for k, (key, g) in enumerate(sorted(groups.items())):
        fares = [trips["fare"][i] for i in g["done"]]
        assert rev["trips"][k] == len(g["all"]) and rev["completed_trips"][k] == len(g["done"])
        assert rev["cancelled_trips"][k] == sum(1 for i in g["all"] if trips["is_cancelled"][i])
        assert rev["gross_revenue"][k] == pytest.approx(round(math.fsum(fares), 2), abs=0.011)
        assert rev["net_revenue"][k] == pytest.approx(round(math.fsum(fares) * 0.75, 2), abs=0.011)
        assert rev["surge_revenue"][k] == pytest.approx(round(math.fsum(trips["fare"][i] for i in g["done"] if trips["surge_mult"][i] > 1.0), 2), abs=0.011)
        assert rev["unique_riders"][k] == len({trips["rider_id"][i] for i in g["done"]})
        assert rev["unique_drivers"][k] == len({trips["driver_id"][i] for i in g["done"]})
    assert r.tables["fact_revenue_daily"].schema.field("completed_trips").type == pa.int64()


def test_earnings_mart_matches_a_plain_recomputation(pulse_tables):
    r = run(pulse_tables)
    trips = r.tables["trip"].to_pydict()
    groups: dict[tuple[int, int], list[int]] = {}
    for i, status in enumerate(trips["status"]):
        if status == "completed":
            d = trips["requested_at"][i]
            groups.setdefault((d.year * 10000 + d.month * 100 + d.day, trips["driver_id"][i]), []).append(i)
    e = r.tables["fact_driver_earnings"].to_pydict()
    assert list(zip(e["date_key"], e["driver_id"], strict=True)) == sorted(groups)
    for k, (_, idx) in enumerate(sorted(groups.items())):
        gross = math.fsum(trips["fare"][i] for i in idx)
        tips = math.fsum(trips["tip"][i] for i in idx)
        hours = round(math.fsum(trips["duration_min"][i] for i in idx) / 60.0, 2)
        assert e["trips"][k] == len(idx) and e["online_hours"][k] == hours
        assert e["gross"][k] == pytest.approx(gross, abs=0.011) and e["tips"][k] == pytest.approx(tips, abs=0.011)
        assert e["platform_fee"][k] == pytest.approx(gross * 0.25, abs=0.011)
        assert e["payout"][k] == pytest.approx(gross * 0.75 + tips, abs=0.011)
        assert e["utilization_pct"][k] == pytest.approx(min(100.0, hours / 8 * 100), abs=0.006)
        assert e["avg_rating"][k] == pytest.approx(np.mean([trips["rating_given"][i] for i in idx]), abs=0.051)
        assert e["city_id"][k] == trips["city_id"][idx[0]]


def test_decimal_and_text_numbers_are_coerced(pulse_tables):
    trip = pulse_tables["trip"]
    dec = trip.set_column(trip.column_names.index("fare"), "fare", trip.column("fare").cast(pa.decimal128(12, 4)))
    text = dec.set_column(dec.column_names.index("tip"), "tip", pa.array(["1.5"] * dec.num_rows))
    r = run({"trip": text})
    assert r.tables["trip"].schema.field("fare").type == pa.float64() and r.tables["fact_revenue_daily"].num_rows > 0
    assert r.tables["trip"].schema.field("tip").type == pa.float64()


def test_arguments_are_checked(pulse_tables):
    with pytest.raises(ValueError, match="'trip' table"):
        PulseDemandSimulator({"rider": pulse_tables["rider"]})
    with pytest.raises(ValueError, match="duration_min"):
        PulseDemandSimulator({"trip": pulse_tables["trip"].drop(["duration_min"])})
    nulls = pulse_tables["trip"].set_column(4, "requested_at", pa.nulls(900, pa.timestamp("us")))
    with pytest.raises(ValueError, match="requested_at"):
        PulseDemandSimulator({"trip": nulls})
