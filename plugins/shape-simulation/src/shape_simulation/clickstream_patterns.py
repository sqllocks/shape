"""Clickstream / web telemetry patterns: sessions, page views, funnels and bot traffic.

Generates web analytics data for a population of users over a time window: multi-page sessions
weighted towards business hours, single-page bounces, a conversion funnel and crawler sessions.

Usage::

    from shape_simulation.clickstream_patterns import ClickstreamConfig, ClickstreamSimulator

    result = ClickstreamSimulator(ClickstreamConfig(users=1000, duration_hours=24)).run()
    result.sessions, result.page_views, result.funnels      # Arrow tables
    result.stats                                            # summary numbers

The same configuration (seed included) gives the same tables: ids come from the seed and the
window starts at ``start_time``, not at the moment the simulation runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape_simulation._patterns import (
    TablesResult,
    float_array,
    parse_start,
    pick,
    timestamps,
    uuid_strings,
    value_counts,
)

_BOT_USER_AGENTS: list[str] = [
    "Googlebot/2.1",
    "Bingbot/2.0",
    "Amazonbot/0.1",
    "GPTBot/1.0",
    "AhrefsBot/7.0",
]

_SESSION_SCHEMA = pa.schema(
    [
        ("session_id", pa.string()),
        ("user_id", pa.string()),
        ("started_at", pa.timestamp("us", "UTC")),
        ("device_type", pa.string()),
        ("referrer", pa.string()),
        ("is_bot", pa.bool_()),
        ("is_bounce", pa.bool_()),
        ("user_agent", pa.string()),
        ("duration_seconds", pa.float64()),
    ]
)
_PAGE_VIEW_SCHEMA = pa.schema(
    [
        ("view_id", pa.string()),
        ("session_id", pa.string()),
        ("user_id", pa.string()),
        ("page_url", pa.string()),
        ("timestamp", pa.timestamp("us", "UTC")),
        ("time_on_page_seconds", pa.float64()),
        ("referrer_url", pa.string()),
    ]
)
_FUNNEL_SCHEMA = pa.schema(
    [
        ("session_id", pa.string()),
        ("user_id", pa.string()),
        ("stage", pa.string()),
        ("stage_order", pa.int64()),
        ("reached_at", pa.timestamp("us", "UTC")),
        ("converted", pa.bool_()),
    ]
)


@dataclass
class ClickstreamConfig:
    """Configuration for :class:`ClickstreamSimulator`.

    Args:
        users: Number of distinct users to simulate.
        duration_hours: Total simulation window in hours.
        start_time: When the window starts (ISO-8601; a missing zone means UTC).
        avg_sessions_per_user: Average number of sessions each user starts.
        avg_pages_per_session: Average page views for non-bounced sessions.
        bounce_rate: Fraction of human sessions that are single-page bounces.
        funnel_enabled: Whether to track conversion funnel progression.
        funnel_stages: Ordered list of funnel stage names.
        funnel_drop_rate: Per-stage probability of a user abandoning.
        bot_traffic_enabled: Whether to inject bot/crawler sessions.
        bot_fraction: Fraction of all sessions that come from bots.
        bot_pages_per_session: Number of pages a bot crawls per session.
        page_pool: URL templates available for page views (``{id}`` and ``{slug}`` expand).
        referrer_sources: Traffic sources for session attribution.
        device_types: Device type labels for human sessions.
        seed: Random seed for reproducibility.
    """

    users: int = 1000
    duration_hours: float = 24.0
    start_time: str = "2024-01-01T00:00:00"
    avg_sessions_per_user: float = 2.5
    avg_pages_per_session: float = 5.0
    bounce_rate: float = 0.35
    funnel_enabled: bool = True
    funnel_stages: list[str] = field(
        default_factory=lambda: ["landing", "product", "cart", "checkout", "confirmation"]
    )
    funnel_drop_rate: float = 0.40
    bot_traffic_enabled: bool = True
    bot_fraction: float = 0.15
    bot_pages_per_session: int = 50
    page_pool: list[str] = field(
        default_factory=lambda: [
            "/",
            "/products",
            "/products/{id}",
            "/cart",
            "/checkout",
            "/about",
            "/contact",
            "/blog",
            "/blog/{slug}",
            "/search",
            "/account",
            "/faq",
        ]
    )
    referrer_sources: list[str] = field(
        default_factory=lambda: [
            "direct",
            "google",
            "bing",
            "facebook",
            "twitter",
            "email",
            "reddit",
        ]
    )
    device_types: list[str] = field(default_factory=lambda: ["desktop", "mobile", "tablet"])
    seed: int = 42

    def __post_init__(self) -> None:
        # a bot session is the pages it crawls: without one it has no first page or time
        if self.bot_traffic_enabled and not self.bot_pages_per_session >= 1:
            raise ValueError(
                f"bot_pages_per_session must be at least 1, got {self.bot_pages_per_session!r} "
                "(set bot_traffic_enabled=False for no bots)"
            )


@dataclass
class ClickstreamResult(TablesResult):
    """Result of :meth:`ClickstreamSimulator.run`.

    Attributes:
        sessions: One row per session with metadata.
        page_views: One row per page view with timestamps and dwell times.
        funnels: Funnel progression records per session.
        stats: Summary statistics dictionary.
    """

    TABLES: ClassVar[tuple[str, ...]] = ("sessions", "page_views", "funnels")

    sessions: pa.Table
    page_views: pa.Table
    funnels: pa.Table
    stats: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"ClickstreamResult(sessions={self.sessions.num_rows}, "
            f"page_views={self.page_views.num_rows}, funnels={self.funnels.num_rows}, "
            f"stats_keys={list(self.stats)})"
        )


class ClickstreamSimulator:
    """Generate web clickstream data: sessions, page views, conversion funnels, bot traffic."""

    def __init__(self, config: ClickstreamConfig | None = None) -> None:
        self._config = config or ClickstreamConfig()
        self._rng = np.random.default_rng(self._config.seed)

    def run(self) -> ClickstreamResult:
        """Execute the simulation and return a :class:`ClickstreamResult`."""
        cfg = self._config
        sess = self._sessions()
        n = len(sess["started_us"])
        if n == 0:
            empty = (
                _SESSION_SCHEMA.empty_table(),
                _PAGE_VIEW_SCHEMA.empty_table(),
                _FUNNEL_SCHEMA.empty_table(),
            )
            return ClickstreamResult(
                *empty, stats=self._stats(sess, 0, _FUNNEL_SCHEMA.empty_table())
            )
        views = self._page_views(sess)
        funnels = self._funnels(sess) if cfg.funnel_enabled else _FUNNEL_SCHEMA.empty_table()
        sessions = pa.table(
            {
                "session_id": pa.array(sess["session_id"], pa.string()),
                "user_id": pa.array(sess["user_id"], pa.string()),
                "started_at": timestamps(sess["started_us"], "UTC"),
                "device_type": pa.array(sess["device"], pa.string()),
                "referrer": pa.array(sess["referrer"], pa.string()),
                "is_bot": pa.array(sess["is_bot"], pa.bool_()),
                "is_bounce": pa.array(sess["is_bounce"], pa.bool_()),
                "user_agent": pa.array(sess["user_agent"], pa.string()),
                "duration_seconds": float_array(views["duration"]),
            },
            schema=_SESSION_SCHEMA,
        )
        page_views = pa.table(
            {
                "view_id": pa.array(views["view_id"], pa.string()),
                "session_id": pa.array(views["session_id"], pa.string()),
                "user_id": pa.array(views["user_id"], pa.string()),
                "page_url": pa.array(views["page_url"], pa.string()),
                "timestamp": timestamps(views["ts_us"], "UTC"),
                "time_on_page_seconds": float_array(views["time_on_page"]),
                "referrer_url": pa.array(views["referrer_url"], pa.string()),
            },
            schema=_PAGE_VIEW_SCHEMA,
        )
        return ClickstreamResult(
            sessions, page_views, funnels, stats=self._stats(sess, page_views.num_rows, funnels)
        )

    # ---- sessions ---------------------------------------------------------------------------

    def _offsets(self, n: int, start_minute: int) -> np.ndarray:
        """``n`` session start offsets (hours into the window), weighted towards midday by
        rejection sampling: weight ``0.5 + 0.5 sin(pi (h - 6) / 12)`` of the hour of day ``h``,
        never below 0.1 so the night is quiet, not empty."""
        rng, hours = self._rng, self._config.duration_hours
        kept: list[np.ndarray] = []
        need = n
        while need > 0:
            m = int(need * 2.6) + 32
            cand = rng.uniform(0.0, hours, m)
            minute_of_day = (np.floor(cand * 60.0).astype(np.int64) + start_minute) % 1440
            weight = np.maximum(
                0.1, 0.5 + 0.5 * np.sin(np.pi * (minute_of_day / 60.0 - 6.0) / 12.0)
            )
            take = cand[rng.random(m) < weight][:need]
            kept.append(take)
            need -= len(take)
        return np.concatenate(kept) if kept else np.empty(0)

    def _sessions(self) -> dict[str, Any]:
        cfg, rng = self._config, self._rng
        start_us = parse_start(cfg.start_time)
        start_minute = (start_us // 60_000_000) % 1440
        users = max(0, cfg.users)
        per_user = np.maximum(rng.poisson(lam=cfg.avg_sessions_per_user, size=users), 1)
        n_human = int(per_user.sum()) if users else 0
        n_bot = 0
        if cfg.bot_traffic_enabled:
            n_bot = max(1, int(n_human * cfg.bot_fraction / max(0.01, 1.0 - cfg.bot_fraction)))
        n = n_human + n_bot
        if n == 0:
            return {"started_us": np.empty(0, dtype=np.int64)}

        names = np.array([f"user_{i:06d}" for i in range(users)], dtype=object)
        user_id = np.empty(n, dtype=object)
        user_id[:n_human] = names[np.repeat(np.arange(users), per_user)] if users else []
        device = np.empty(n, dtype=object)
        referrer = np.empty(n, dtype=object)
        user_agent = np.full(n, None, dtype=object)
        is_bot = np.zeros(n, dtype=bool)
        is_bounce = np.zeros(n, dtype=bool)
        is_bounce[:n_human] = rng.random(n_human) < cfg.bounce_rate
        device[:n_human] = pick(rng, cfg.device_types, n_human)
        referrer[:n_human] = pick(rng, cfg.referrer_sources, n_human)
        if n_bot:
            is_bot[n_human:] = True
            user_agent[n_human:] = pick(rng, _BOT_USER_AGENTS, n_bot)
            device[n_human:] = "bot"
            referrer[n_human:] = "direct"
            ids = rng.integers(0, 2**32, size=n_bot, dtype=np.uint64)
            user_id[n_human:] = [f"bot_{int(i):08x}" for i in ids]

        offset = self._offsets(n, start_minute)
        started = start_us + np.round(offset * 3_600_000_000.0).astype(np.int64)
        session_id = np.array(uuid_strings(rng, n), dtype=object)
        order = np.argsort(started, kind="stable")
        return {
            "started_us": started[order],
            "session_id": session_id[order],
            "user_id": user_id[order],
            "device": device[order],
            "referrer": referrer[order],
            "is_bot": is_bot[order],
            "is_bounce": is_bounce[order],
            "user_agent": user_agent[order],
        }

    # ---- page views -------------------------------------------------------------------------

    def _urls(self, count: int) -> np.ndarray:
        rng, pool = self._rng, self._config.page_pool
        idx = rng.integers(0, len(pool), size=count)
        ids = rng.integers(1, 501, size=count)
        slugs = rng.integers(1, 501, size=count)
        urls = np.empty(count, dtype=object)
        for k, template in enumerate(pool):
            hit = idx == k
            if not hit.any():
                continue
            if "{id}" not in template and "{slug}" not in template:
                urls[hit] = template
            else:
                urls[hit] = [
                    template.replace("{id}", str(i)).replace("{slug}", f"post-{s}")
                    for i, s in zip(ids[hit].tolist(), slugs[hit].tolist(), strict=True)
                ]
        return urls

    def _page_views(self, sess: dict[str, Any]) -> dict[str, Any]:
        cfg, rng = self._config, self._rng
        n = len(sess["started_us"])
        bot, bounce = sess["is_bot"], sess["is_bounce"]
        n_pages = np.where(
            bot,
            cfg.bot_pages_per_session,
            np.where(bounce, 1, np.maximum(2, rng.poisson(lam=cfg.avg_pages_per_session, size=n))),
        ).astype(np.int64)
        total = int(n_pages.sum())
        sess_idx = np.repeat(np.arange(n), n_pages)
        first = np.cumsum(n_pages) - n_pages
        bot_view = bot[sess_idx]
        dwell = np.empty(total)
        dwell[bot_view] = rng.uniform(0.1, 0.5, int(bot_view.sum()))
        dwell[~bot_view] = np.clip(
            rng.lognormal(mean=2.7, sigma=0.8, size=int((~bot_view).sum())), 5.0, 120.0
        )
        before = np.cumsum(dwell) - dwell
        within = before - np.repeat(before[first], n_pages)
        ts_us = sess["started_us"][sess_idx] + np.round(within * 1_000_000.0).astype(np.int64)
        urls = self._urls(total)
        referrer_url = np.roll(urls, 1)
        referrer_url[first] = sess["referrer"]
        view_id = np.array(uuid_strings(rng, total), dtype=object)
        order = np.argsort(ts_us, kind="stable")
        return {
            "duration": np.round(np.bincount(sess_idx, weights=np.round(dwell, 2), minlength=n), 2),
            "view_id": view_id[order],
            "session_id": sess["session_id"][sess_idx][order],
            "user_id": sess["user_id"][sess_idx][order],
            "page_url": urls[order],
            "ts_us": ts_us[order],
            "time_on_page": np.round(dwell, 2)[order],
            "referrer_url": referrer_url[order],
        }

    # ---- funnels ----------------------------------------------------------------------------

    def _funnels(self, sess: dict[str, Any]) -> pa.Table:
        """Funnel progression of the human, non-bounced sessions. Each stage but the last has an
        independent ``funnel_drop_rate`` chance of ending the visit there."""
        cfg, rng = self._config, self._rng
        stages = cfg.funnel_stages
        eligible = np.flatnonzero(~sess["is_bot"] & ~sess["is_bounce"])
        if len(eligible) == 0 or not stages:
            return _FUNNEL_SCHEMA.empty_table()
        n_stages = len(stages)
        reached = np.full(len(eligible), n_stages, dtype=np.int64)
        if n_stages > 1:
            drops = rng.random((len(eligible), n_stages - 1)) < cfg.funnel_drop_rate
            reached = np.where(drops.any(axis=1), drops.argmax(axis=1) + 1, n_stages)
        step = rng.uniform(10.0, 60.0, size=(len(eligible), n_stages))
        offset = np.cumsum(step, axis=1) - step
        rows = np.repeat(np.arange(len(eligible)), reached)
        first = np.cumsum(reached) - reached
        order = np.arange(len(rows)) - np.repeat(first, reached)
        base = sess["started_us"][eligible][rows]
        at = base + np.round(offset[rows, order] * 1_000_000.0).astype(np.int64)
        return pa.table(
            {
                "session_id": pa.array(sess["session_id"][eligible][rows], pa.string()),
                "user_id": pa.array(sess["user_id"][eligible][rows], pa.string()),
                "stage": pa.array(np.asarray(stages, dtype=object)[order], pa.string()),
                "stage_order": pa.array(order, pa.int64()),
                "reached_at": timestamps(at, "UTC"),
                "converted": pa.array(order == n_stages - 1, pa.bool_()),
            },
            schema=_FUNNEL_SCHEMA,
        )

    # ---- stats ------------------------------------------------------------------------------

    def _stats(self, sess: dict[str, Any], n_views: int, funnels: pa.Table) -> dict[str, Any]:
        cfg = self._config
        total = len(sess["started_us"])
        bots = int(sess["is_bot"].sum()) if total else 0
        human = total - bots
        bounces = int(sess["is_bounce"].sum()) if total else 0
        converted = eligible = 0
        rate = 0.0
        if funnels.num_rows and cfg.funnel_stages:
            final = cfg.funnel_stages[-1]
            stage = np.asarray(funnels.column("stage").to_pylist(), dtype=object)
            done = np.asarray(funnels.column("converted").to_pylist(), dtype=bool)
            sid = np.asarray(funnels.column("session_id").to_pylist(), dtype=object)
            converted = len(set(sid[(stage == final) & done].tolist()))
            eligible = len(set(sid.tolist()))
            rate = round(converted / max(1, eligible), 4)
        return {
            "total_sessions": total,
            "human_sessions": human,
            "bot_sessions": bots,
            "bounce_sessions": bounces,
            "bounce_rate_actual": round(bounces / max(1, human), 4),
            "total_page_views": n_views,
            "avg_pages_per_session": round(n_views / max(1, total), 2),
            "funnel_eligible_sessions": eligible,
            "funnel_conversions": converted,
            "funnel_conversion_rate": rate,
            "unique_users": len(set(sess["user_id"].tolist())) if total else 0,
            "device_type_distribution": value_counts(sess["device"]) if total else {},
            "referrer_distribution": value_counts(sess["referrer"]) if total else {},
            "duration_hours": cfg.duration_hours,
            "seed": cfg.seed,
        }
