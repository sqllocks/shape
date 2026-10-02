"""Clickstream patterns: sessions, page views, funnels, bots."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

from shape_simulation.clickstream_patterns import (
    ClickstreamConfig,
    ClickstreamResult,
    ClickstreamSimulator,
)


def run(**cfg):
    return ClickstreamSimulator(ClickstreamConfig(**{"users": 150, **cfg})).run()


def test_same_seed_same_tables_and_other_seed_differs():
    a, b, c = run(seed=3), run(seed=3), run(seed=4)
    assert all(a.table_map()[k].equals(b.table_map()[k]) for k in a.TABLES)
    assert a.stats == b.stats
    assert not a.sessions.equals(c.sessions)


def test_window_starts_at_start_time_not_at_the_clock():
    r = run(start_time="2031-05-06T10:00:00", duration_hours=2.0)
    first = r.sessions.column("started_at").to_pylist()[0]
    assert first.date() == dt.date(2031, 5, 6)
    assert min(r.page_views.column("timestamp").to_pylist()) >= first
    last = max(r.sessions.column("started_at").to_pylist())
    assert last <= dt.datetime(2031, 5, 6, 12, tzinfo=dt.timezone.utc)


def test_schemas_and_types():
    r = run()
    assert r.sessions.column_names == [
        "session_id", "user_id", "started_at", "device_type", "referrer",
        "is_bot", "is_bounce", "user_agent", "duration_seconds",
    ]
    assert r.sessions.schema.field("started_at").type == pa.timestamp("us", "UTC")
    assert r.page_views.column_names[-1] == "referrer_url"
    assert r.funnels.schema.field("stage_order").type == pa.int64()
    assert len(set(r.sessions.column("session_id").to_pylist())) == r.sessions.num_rows


def test_bots_bounces_and_durations():
    r = run(bot_pages_per_session=7)
    s, v = r.sessions.to_pydict(), r.page_views.to_pydict()
    pages: dict[str, int] = {}
    dwell: dict[str, float] = {}
    for sid, d in zip(v["session_id"], v["time_on_page_seconds"], strict=True):
        pages[sid] = pages.get(sid, 0) + 1
        dwell[sid] = dwell.get(sid, 0.0) + d
    for i, sid in enumerate(s["session_id"]):
        if s["is_bot"][i]:
            assert s["device_type"][i] == "bot" and s["user_agent"][i] and pages[sid] == 7
            assert s["user_id"][i].startswith("bot_")
        else:
            assert s["user_agent"][i] is None and s["user_id"][i].startswith("user_")
            assert pages[sid] == 1 if s["is_bounce"][i] else pages[sid] >= 2
        assert abs(s["duration_seconds"][i] - dwell[sid]) <= 0.011 * pages[sid]
    assert set(v["session_id"]) == set(s["session_id"])


def test_views_are_chained_and_sorted():
    r = run()
    v = r.page_views.to_pydict()
    assert v["timestamp"] == sorted(v["timestamp"])
    s = r.sessions.to_pydict()
    ref = dict(zip(s["session_id"], s["referrer"], strict=True))
    first = {}
    for sid, url, back, ts in zip(v["session_id"], v["page_url"], v["referrer_url"], v["timestamp"], strict=True):
        first.setdefault(sid, (ts, back))
    assert all(back == ref[sid] for sid, (_, back) in first.items())
    assert all("{" not in u for u in v["page_url"])


def test_funnel_only_for_engaged_humans_and_ordered():
    r = run(funnel_drop_rate=0.5)
    s = r.sessions.to_pydict()
    engaged = {sid for sid, bot, bounce in zip(s["session_id"], s["is_bot"], s["is_bounce"], strict=True) if not bot and not bounce}
    f = r.funnels.to_pydict()
    assert set(f["session_id"]) == engaged
    by: dict[str, list[tuple[int, bool]]] = {}
    for sid, o, c in zip(f["session_id"], f["stage_order"], f["converted"], strict=True):
        by.setdefault(sid, []).append((o, c))
    for rows in by.values():
        assert [o for o, _ in rows] == list(range(len(rows)))
        assert not any(c for _, c in rows[:-1])
        assert rows[-1][1] == (len(rows) == 5)
    assert r.stats["funnel_conversions"] == sum(1 for rows in by.values() if rows[-1][1])


def test_funnel_disabled_and_no_bots():
    r = run(funnel_enabled=False, bot_traffic_enabled=False)
    assert r.funnels.num_rows == 0 and r.funnels.column_names[0] == "session_id"
    assert r.stats["bot_sessions"] == 0 and not any(r.sessions.column("is_bot").to_pylist())


def test_bot_share_follows_bot_fraction():
    r = run(users=800, bot_fraction=0.3)
    share = r.stats["bot_sessions"] / r.stats["total_sessions"]
    assert 0.27 < share < 0.33


def test_peak_hours_are_busier_than_night():
    r = run(users=1500, duration_hours=24.0)
    hours = [t.hour for t in r.sessions.column("started_at").to_pylist()]
    noon = sum(1 for h in hours if 10 <= h < 14)
    night = sum(1 for h in hours if h < 4)
    assert noon > 2 * night


def test_stats_are_consistent():
    r = run()
    st = r.stats
    assert st["total_sessions"] == r.sessions.num_rows == st["human_sessions"] + st["bot_sessions"]
    assert st["total_page_views"] == r.page_views.num_rows
    assert sum(st["device_type_distribution"].values()) == st["total_sessions"]
    assert st["seed"] == 42 and st["duration_hours"] == 24.0
    assert "ClickstreamResult(sessions=" in repr(r)


def test_no_users_and_no_bots_is_empty_not_an_error():
    r = run(users=0, bot_traffic_enabled=False)
    assert isinstance(r, ClickstreamResult)
    assert r.sessions.num_rows == r.page_views.num_rows == r.funnels.num_rows == 0
    assert r.sessions.schema.field("is_bot").type == pa.bool_()


def test_custom_pool_and_stages():
    r = run(page_pool=["/a", "/b/{id}"], funnel_stages=["x", "y"], referrer_sources=["r1"], device_types=["d1"])
    assert set(r.funnels.column("stage").to_pylist()) <= {"x", "y"}
    assert all(u == "/a" or u.startswith("/b/") for u in r.page_views.column("page_url").to_pylist())
    humans = [d for d in r.sessions.column("device_type").to_pylist() if d != "bot"]
    assert set(humans) == {"d1"}
