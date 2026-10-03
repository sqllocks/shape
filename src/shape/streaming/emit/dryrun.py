"""The plan ``shape emit --dry-run`` prints (W2-09): what a run would use, resolved, with nothing
opened, written or sent.

``shape-emit-plan`` (version 1) is a persisted format: a JSON document with ``format`` and an
integer ``version``. :func:`check_plan_document` reads one back and refuses another format or a
newer version. The pieces that need the runtime's own rules live here: the pacing summary (how
long the run takes by its schedule, and its peak rate) and the text rendering.
"""

from __future__ import annotations

from typing import Any

from shape.errors import ShapeError
from shape.streaming.emit import runtime
from shape.streaming.emit.rate import DaySchedule, RateSchedule

PLAN_FORMAT = "shape-emit-plan"
PLAN_VERSION = 1


def check_plan_document(doc: Any) -> dict[str, Any]:
    """``doc`` (a parsed ``--dry-run --json`` output) if it is a plan this Shape reads."""
    if not isinstance(doc, dict) or doc.get("format") != PLAN_FORMAT:
        raise ShapeError(f"not a {PLAN_FORMAT} document")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ShapeError("the version of an emit plan must be an integer of 1 or more")
    if version > PLAN_VERSION:
        raise ShapeError(
            f"this emit plan is version {version}, which is newer than the version {PLAN_VERSION} "
            "this Shape reads; upgrade Shape to read it"
        )
    return doc


def pacing_summary(
    config: runtime.EmitConfig, plan: Any, offset: int, curve_name: str | None
) -> dict[str, Any]:
    """How the run is paced, how long it takes by its schedule and its peak rate.

    ``expected_seconds`` is ``None`` when nothing paces the run (as fast as the sink takes
    events) and for a ``--speed`` replay (the time depends on the events' own times); for
    ``--arrivals poisson`` it is the mean. ``peak_events_per_minute`` is the highest rate of the
    schedule times 60, capped by ``--max-rate``."""
    limit = (
        plan.total_events
        if config.max_events is None
        else min(plan.total_events, config.max_events)
    )
    remaining = max(0, limit - offset)
    out: dict[str, Any] = {
        "mode": "unpaced",
        "rate": None,
        "arrivals": config.arrivals,
        "bursts": len(config.bursts),
        "ramps": len(config.ramps),
        "curve": curve_name,
        "max_rate": config.max_rate,
        "speed": config.speed,
        "day_seconds": config.day_seconds,
        "expected_seconds": None,
        "peak_events_per_minute": None,
    }
    expected: float | None = None
    peak: float | None = None
    if config.day_seconds is not None:
        out["mode"] = "day-seconds"
        days = DaySchedule(plan.day_events, config.day_seconds)
        days.resume_at(offset)
        expected, peak = days.due_time(remaining), days.peak_rate()
    elif config.realtime:
        out["mode"] = "realtime"
        out["rate"] = config.rate
        origin = config.curve_origin
        if origin is None:
            origin = runtime._seconds_of_day()
        if config.curve is not None:
            out["curve_origin"] = origin
        schedule = RateSchedule(  # the mean schedule: a Poisson stream has the same integral
            config.rate,
            config.bursts,
            ramps=config.ramps,
            curve=config.curve,
            day_origin=origin,
        )
        expected = schedule.due_time(remaining)
        peak = schedule.peak_rate(max(expected, 1e-9))
    elif config.speed is not None:
        out["mode"] = "speed"
    if config.max_rate is not None and out["mode"] != "speed":
        floor = remaining / config.max_rate
        expected = floor if expected is None else max(expected, floor)
        peak = config.max_rate if peak is None else min(peak, config.max_rate)
    if expected is not None and config.duration is not None:
        expected = min(expected, config.duration)
    out["expected_seconds"] = expected
    out["peak_events_per_minute"] = None if peak is None else peak * 60.0
    return out


def render_text(doc: dict[str, Any]) -> str:
    """The plan as the text ``--dry-run`` prints."""
    t = doc["target"]
    lines = [
        f"shape {doc['command']} (dry run): nothing was opened, written or sent",
        "",
        f"target        {t['name']} (seed {t['seed']}, scale {t['scale'] or 'default'})",
    ]
    per = " a day" if doc["drift_plan"] else ""
    for table in t["tables"]:
        lines.append(f"  table       {table['name']}: {table['rows']:,} rows{per}")
    limits = doc["limits"]
    lines.append(
        f"events        {limits['events']:,} events ({t['total_events']:,} in the stream, "
        f"{doc['event_order']} order)"
    )
    lines.append(f"format        {doc['event_format']} ({doc['envelope']} envelope)")
    lines.append("destinations")
    for d in doc["destinations"]:
        plugin = f" via plugin {d['plugin']}" if d.get("plugin") else ""
        lines.append(f"  {d['role']:<11} {d['uri']}  [{d['kind']}{plugin}]")
    for c in doc["credentials"]:
        lines.append(f"  credential  {c['option']} = {c['reference']} ({c['checked']})")
    c = doc["checkpoint"]
    state = {"fresh": "fresh start", "resume": f"resume at offset {c['offset']:,}"}.get(
        c["state"], c["state"]
    )
    if c["state"] == "refused":
        state = f"refused: {c['reason']}"
    lines.append(f"checkpoint    {c['path'] or 'none'}: {state}")
    p = doc["pacing"]
    lines.append(f"pacing        {p['mode']}" + (f", {p['rate']:g} events/s" if p["rate"] else ""))
    if p["expected_seconds"] is not None:
        lines.append(f"  duration    about {p['expected_seconds']:,.1f} s")
    if p["peak_events_per_minute"] is not None:
        lines.append(f"  peak        {p['peak_events_per_minute']:,.0f} events/minute")
    if doc["drift_plan"]:
        d = doc["drift_plan"]
        lines.append(f"drift plan    {d['path']} (sha256 {d['sha256'][:12]}...)")
        for day in d["days"]:
            active = ", ".join(day["active"]) or "none"
            lines.append(
                f"  day {day['day']:<3} {day['date']}  {day['events']:,} events  active: {active}"
            )
    if doc["answer_key"]:
        lines.append(f"answer key    {doc['answer_key']}")
    return "\n".join(lines) + "\n"
