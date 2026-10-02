"""Parity case: ``clickstream_patterns`` (sessions, page views, funnels, bot traffic)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import harness as h
import numpy as np
import pyarrow as pa
from harness import Col, Report, Run, TableSpec

NAME = "clickstream"
SIM = "clickstream"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
POOL = ["/", "/products", "/cart", "/checkout", "/about", "/contact", "/blog", "/search", "/account", "/faq"]
URLS = (*(rf"{p}" if p != "/" else "/" for p in POOL), r"/products/\d+", r"/blog/post-\d+")
SOURCES = ("direct", "google", "bing", "facebook", "twitter", "email", "reddit")
USERS = (r"user_\d{6}", r"bot_[0-9a-f]{8}")
DAY_US = 86_400 * 1_000_000


def inputs(quick: bool) -> None:
    return None


def configs(quick: bool) -> dict[str, dict[str, Any]]:
    users = 350 if quick else 1000
    return {
        "default": {"users": users},
        "variant": {
            "users": users - 50,
            "duration_hours": 36.0,
            "avg_sessions_per_user": 1.5,
            "avg_pages_per_session": 7.0,
            "bounce_rate": 0.5,
            "bot_fraction": 0.05,
            "bot_pages_per_session": 20,
            "funnel_stages": ["landing", "product", "cart", "checkout"],
            "funnel_drop_rate": 0.25,
        },
    }


def controls(quick: bool) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        "bounce_rate 0.35 -> 0.55": ("default", {"bounce_rate": 0.55}),
        "funnel_drop_rate 0.40 -> 0.65": ("default", {"funnel_drop_rate": 0.65}),
    }


def run_shape(cfg: dict[str, Any], seed: int, inputs: Any) -> Run:
    from shape_simulation.clickstream_patterns import ClickstreamConfig, ClickstreamSimulator

    r = ClickstreamSimulator(ClickstreamConfig(**{**cfg, "seed": seed})).run()
    return Run(r.table_map(), r.stats)


def _floor_day(table: pa.Table) -> int:
    ts = h.numbers(table.column("started_at"), 0) * 1e6
    return int(ts.min() // DAY_US * DAY_US)


def _facts(run: Run, cfg: dict[str, Any]) -> dict[str, Any]:
    """Properties of one run that both tools must share."""
    s, v, f = (run.tables[k].to_pydict() for k in ("sessions", "page_views", "funnels"))
    sid = set(s["session_id"])
    bots = {i for i, b in zip(s["session_id"], s["is_bot"], strict=True) if b}
    bounces = {i for i, b in zip(s["session_id"], s["is_bounce"], strict=True) if b}
    per: dict[str, int] = {}
    dwell: dict[str, float] = {}
    for sess, d in zip(v["session_id"], v["time_on_page_seconds"], strict=True):
        per[sess] = per.get(sess, 0) + 1
        dwell[sess] = dwell.get(sess, 0.0) + d
    human_views = [n for k, n in per.items() if k not in bots and k not in bounces]
    stages: dict[str, list[tuple[int, Any, bool]]] = {}
    for sess, order, at, conv in zip(f["session_id"], f["stage_order"], f["reached_at"], f["converted"], strict=True):
        stages.setdefault(sess, []).append((order, at, conv))
    contiguous = all(
        [o for o, _, _ in rows] == list(range(len(rows)))
        and all(rows[i][1] <= rows[i + 1][1] for i in range(len(rows) - 1))
        and not any(c for _, _, c in rows[:-1])
        for rows in stages.values()
    )
    last = [max(o for o, _, _ in rows) for rows in stages.values()]
    return {
        "pv_fk": set(v["session_id"]) <= sid,
        "funnel_fk": set(f["session_id"]) <= sid,
        "bot_flags": all(
            (b == (d == "bot") == (ua is not None))
            for b, d, ua in zip(s["is_bot"], s["device_type"], s["user_agent"], strict=True)
        ),
        "duration_sum": max(abs(d - dwell.get(i, 0.0)) for i, d in zip(s["session_id"], s["duration_seconds"], strict=True)) <= 0.011 * max(per.values()),
        "funnel_eligible_only": not (set(f["session_id"]) & (bots | bounces)),
        "bot_pages": all(per[i] == cfg.get("bot_pages_per_session", 50) for i in bots),
        "bounce_pages": all(per[i] == 1 for i in bounces - bots),
        "human_pages_min": min(human_views) >= 2 if human_views else True,
        "sorted": s["started_at"] == sorted(s["started_at"]) and v["timestamp"] == sorted(v["timestamp"]),
        "funnel_shape": contiguous,
        "per_session_views": np.array(sorted(human_views), dtype=float),
        "bot_views": np.array([per[i] for i in bots], dtype=float),
        "last_stage": [str(x) for x in last],
        "funnel_sessions": len(stages),
    }


def compare(rep: Report, shape: Run, base: dict[int, Run], cfg: dict[str, Any], inputs: Any, quick: bool) -> None:
    so = _floor_day(shape.tables["sessions"])
    bo = {s: _floor_day(r.tables["sessions"]) for s, r in base.items()}
    mapping = {
        "sessions": TableSpec(
            columns={
                "session_id": Col("id", regex=UUID),
                "user_id": Col("pattern", regexes=USERS),
                "started_at": Col("time", origin=(so, bo[h.REF_SEED])),
                "device_type": Col("enum"),
                "referrer": Col("enum"),
                "is_bot": Col("enum"),
                "is_bounce": Col("enum"),
                "user_agent": Col("enum"),
            }
        ),
        "page_views": TableSpec(
            columns={
                "view_id": Col("id", regex=UUID),
                "session_id": Col("skip"),
                "user_id": Col("pattern", regexes=USERS),
                "page_url": Col("pattern", regexes=URLS),
                "timestamp": Col("time", origin=(so, bo[h.REF_SEED])),
                "referrer_url": Col("pattern", regexes=(*URLS, *SOURCES)),
            }
        ),
        "funnels": TableSpec(
            columns={
                "session_id": Col("skip"),
                "user_id": Col("pattern", regexes=USERS),
                "stage_order": Col("enum"),
                "reached_at": Col("time", origin=(so, bo[h.REF_SEED])),
            }
        ),
    }
    for name, spec in mapping.items():
        h.compare_table(rep, name, shape.tables[name], {s: r.tables[name] for s, r in base.items()}, spec)
    h.compare_stats(
        rep,
        shape.stats,
        {s: r.stats for s, r in base.items()},
        exact=("duration_hours",),
        counts=(
            "total_sessions",
            "human_sessions",
            "bot_sessions",
            "bounce_sessions",
            "total_page_views",
            "funnel_eligible_sessions",
            "funnel_conversions",
            "unique_users",
        ),
        skip=("seed",),
    )
    fs = _facts(shape, cfg)
    fb = {s: _facts(r, cfg) for s, r in base.items()}
    for key in (
        "pv_fk",
        "funnel_fk",
        "bot_flags",
        "duration_sum",
        "funnel_eligible_only",
        "bot_pages",
        "bounce_pages",
        "human_pages_min",
        "sorted",
        "funnel_shape",
    ):
        h.invariant(rep, f"invariant:{key}", fs[key], {s: f[key] for s, f in fb.items()})
        rep.add(f"holds:{key}", bool(fs[key]), shape=fs[key])
    h.compare_vector(rep, "page views per human session", fs["per_session_views"], {s: f["per_session_views"] for s, f in fb.items()})
    h.compare_categories(rep, "last funnel stage reached", fs["last_stage"], {s: f["last_stage"] for s, f in fb.items()})


def probes(ctx: h.Context) -> list[Report]:
    """SIM-1: a seed does not reproduce a baseline run; it does reproduce a Shape run."""
    cfg = {"users": 60, "bot_traffic_enabled": False}
    rep = Report("SIM-1 clickstream reproducibility")
    first = h.baseline_once(SIM, cfg, None, 5, "a")
    second = h.baseline_once(SIM, cfg, None, 5, "b")
    ids_a = set(first.tables["sessions"].column("session_id").to_pylist())
    ids_b = set(second.tables["sessions"].column("session_id").to_pylist())
    rep.add("baseline: same seed, different session ids", not (ids_a & ids_b), shared=len(ids_a & ids_b))
    one, two = run_shape(cfg, 5, None), run_shape(cfg, 5, None)
    rep.add("shape: same seed, identical tables", all(one.tables[k].equals(two.tables[k]) for k in one.tables))
    first_ts = one.tables["sessions"].column("started_at").to_pylist()[0]
    rep.add("shape: the window starts at start_time", first_ts.date() == dt.date(2024, 1, 1), first=str(first_ts))
    return [rep]
