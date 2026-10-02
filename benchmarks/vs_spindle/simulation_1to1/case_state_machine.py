"""Case: the workflow simulator (``shape_simulation.state_machine``) against the baseline's.

Mechanism parity (same configuration and seed: the same events, summaries, statistics), the
allow-list probes, T-21 on the events and summaries, and the negative controls.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import sim_common as sc
import sim_compare as cmp

NAME = "state_machine"

ALLOWED: dict[str, dict[str, str]] = {
    "WF-1": {
        "what": "entities that never move (their initial state)",
        "baseline": "an entity whose initial state has no way out has no events, and its summary "
        "row says it started and ended in the first initial state of the workflow, whichever "
        "state it was actually given",
        "shape": "its summary row names the state it started in",
    },
    "WF-2": {
        "what": "event ids",
        "baseline": "a new random UUID for every event on every run: the same seed gives a "
        "different event_id column each time",
        "shape": "a UUID derived from the seed (a stream of its own, so no other draw moves): the "
        "same seed gives the same ids (a determinism rule, not a defect)",
    },
}

CUSTOM = {
    "states": [
        {"name": "new", "is_initial": True},
        {"name": "parked", "is_initial": True},  # an initial state with no transitions
        {"name": "work"},
        {"name": "done", "is_terminal": True},
    ],
    "transitions": [
        {
            "from_state": "new",
            "to_state": "work",
            "probability": 3.0,
            "dwell_hours_mean": 5.0,
            "dwell_hours_std": 2.0,
        },
        {
            "from_state": "new",
            "to_state": "done",
            "probability": 1.0,
            "dwell_hours_mean": 1.0,
            "dwell_hours_std": 0.1,
            "min_dwell_hours": 0.5,
        },
        {
            "from_state": "work",
            "to_state": "done",
            "probability": 1.0,
            "dwell_hours_mean": 8.0,
            "dwell_hours_std": 3.0,
        },
        {
            "from_state": "work",
            "to_state": "new",
            "probability": 0.5,
            "dwell_hours_mean": 2.0,
            "dwell_hours_std": 1.0,
        },
    ],
}
JOBS: dict[str, dict[str, Any]] = {
    "order_fulfillment": {
        "preset": "order_fulfillment",
        "config": {
            "entity_count": 2000,
            "seed": 7,
            "anomaly_skip_probability": 0.05,
            "anomaly_backward_probability": 0.05,
            "anomaly_stuck_probability": 0.03,
        },
    },
    "support_ticket": {
        "preset": "support_ticket",
        "config": {
            "entity_count": 1500,
            "seed": 8,
            "start_time": "2025-03-01T08:30:00",
            "max_transitions_per_entity": 12,
            "entity_prefix": "ticket",
        },
    },
    "onboarding_no_anomalies": {
        "preset": "employee_onboarding",
        "config": {"entity_count": 3000, "seed": 9, "anomaly_enabled": False},
    },
    "custom_graph": {
        "custom": CUSTOM,
        "config": {"entity_count": 1000, "seed": 10, "anomaly_skip_probability": 0.05},
    },
}
T21_JOB = {"preset": "order_fulfillment", "config": {"entity_count": 6000}}
EVENT_VOLATILE = ("event_id",)


# ---- the two sides ------------------------------------------------------------------------


def _graph(job: dict[str, Any], mod: Any) -> tuple[list[Any], list[Any]]:
    if "preset" in job:
        return mod.get_preset_workflow(job["preset"])
    states = [mod.StateDefinition(**s) for s in job["custom"]["states"]]
    transitions = [mod.TransitionRule(**t) for t in job["custom"]["transitions"]]
    return states, transitions


def _write(out: Path, events: pd.DataFrame, summary: pd.DataFrame, result: Any) -> dict[str, Any]:
    events.to_parquet(out / "events.parquet")
    summary.to_parquet(out / "summary.parquet")
    return {"stats": result.stats, "state_distribution": dict(result.state_distribution)}


def baseline_side(job: dict[str, Any]) -> dict[str, Any]:
    from sqllocks_spindle.simulation import state_machine as mod

    states, transitions = _graph(job, mod)
    cfg = mod.WorkflowConfig(states=states, transitions=transitions, **job["config"])
    result = mod.WorkflowSimulator(cfg).run()
    out = Path(job["out_dir"])
    if job.get("twice"):  # a second run of the same seed: are the ids the same?
        second = mod.WorkflowSimulator(cfg).run()
        pd.DataFrame({"event_id": second.events["event_id"]}).to_parquet(out / "ids_again.parquet")
    return _write(out, result.events, result.entity_summary, result)


def shape_side(job: dict[str, Any]) -> dict[str, Any]:
    from shape_simulation import state_machine as mod

    states, transitions = _graph(job, mod)
    cfg = mod.WorkflowConfig(states=states, transitions=transitions, **job["config"])
    result = mod.WorkflowSimulator(cfg).run()
    out = Path(job["out_dir"])
    if job.get("twice"):
        second = mod.WorkflowSimulator(cfg).run()
        pd.DataFrame({"event_id": second.events.column("event_id").to_pylist()}).to_parquet(
            out / "ids_again.parquet"
        )
    return _write(out, result.events.to_pandas(), result.entity_summary.to_pandas(), result)


# ---- helpers ------------------------------------------------------------------------------


def _frames(result: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = Path(result["out_dir"])
    return pd.read_parquet(out / "events.parquet"), pd.read_parquet(out / "summary.parquet")


UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def compare_outputs(
    checks: sc.Checks,
    label: str,
    b: dict[str, Any],
    s: dict[str, Any],
    *,
    dead_ends: tuple[str, ...] = (),
    first_initial: str = "",
) -> None:
    be, bs = _frames(b)
    se, ss = _frames(s)
    ok, why = cmp.frames_equal(be, se, ignore=EVENT_VOLATILE, same_order=True)
    checks.add(f"{label}: events equal (event_id aside)", ok, why)
    ids = se["event_id"]
    checks.add(
        f"{label}: Shape event_id are unique UUIDs",
        ids.is_unique and bool(ids.map(lambda v: bool(UUID4.match(v))).all()),
        f"{len(ids)} ids",
    )
    # summaries: the dead-end entities are WF-1; everything else must be equal
    zero_b = bs["total_transitions"] == 0
    zero_s = ss["total_transitions"] == 0
    rest_ok, rest_why = cmp.frames_equal(bs[~zero_b], ss[~zero_s], ignore=(), same_order=True)
    checks.add(
        f"{label}: summaries equal for entities that moved",
        rest_ok and bool((zero_b == zero_s).all()),
        rest_why,
    )
    if zero_b.any():
        explained = (
            bool((bs.loc[zero_b, ["initial_state", "final_state"]] == first_initial).all().all())
            and bool(ss.loc[zero_s, "initial_state"].isin(dead_ends).all())
            and bool((ss.loc[zero_s, "initial_state"] == ss.loc[zero_s, "final_state"]).all())
            and list(bs.loc[zero_b, "entity_id"]) == list(ss.loc[zero_s, "entity_id"])
        )
        checks.add(
            f"{label}: WF-1: {int(zero_b.sum())} entities that never moved differ only in "
            f"their initial state",
            explained,
            "",
        )
    sb, ss_ = b["stats"], s["stats"]
    stat_ok = set(sb) == set(ss_) and all(
        (abs(sb[k] - ss_[k]) <= 1e-4) if isinstance(sb[k], float) else sb[k] == ss_[k] for k in sb
    )
    checks.add(f"{label}: stats equal", stat_ok, f"{ss_}")
    dist = dict(s["state_distribution"])
    if zero_b.any():  # WF-1: the entities that never moved are counted in the first initial state
        n = int(zero_b.sum())
        for state in dead_ends:
            dist[state] = dist.get(state, 0) - n
        dist[first_initial] = dist.get(first_initial, 0) + n
        dist = {k: v for k, v in dist.items() if v}
    checks.add(
        f"{label}: state_distribution equal",
        b["state_distribution"] == dist,
        str(s["state_distribution"]),
    )


# ---- run ----------------------------------------------------------------------------------


def t21(ctx: Any, checks: sc.Checks) -> None:
    def one(side: str, seed: int) -> dict[str, Any]:
        job = {**T21_JOB, "config": {**T21_JOB["config"], "seed": seed}}
        r = ctx.run(side, NAME, f"t21_{side}_{seed}", **job)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r

    ref = one("baseline", ctx.ref_seed)
    spread = [one("baseline", sd) for sd in ctx.seeds]
    shape = one("shape", ctx.shape_seed)
    ev_r, sm_r = _frames(ref)
    ev_s, sm_s = _frames(shape)
    sp = [_frames(r) for r in spread]
    skip_e, skip_s = ("event_id", "entity_id"), ("entity_id",)
    res = cmp.t21_columns(ev_r, [e for e, _ in sp], ev_s, skip=skip_e)
    bad = [c for c, v in res.items() if not v.get("equivalent", False)]
    checks.add(f"T-21 events: (a)-(e) on {len(res) - 1} columns", not bad, f"not equivalent: {bad}")
    res = cmp.t21_columns(sm_r, [m for _, m in sp], sm_s, skip=skip_s)
    bad = [c for c, v in res.items() if not v.get("equivalent", False)]
    checks.add(
        f"T-21 entity summary: (a)-(e) on {len(res) - 1} columns", not bad, f"not equivalent: {bad}"
    )
    for key in ("total_events", "anomaly_count"):
        ok, why = cmp.count_within(
            shape["stats"][key], ref["stats"][key], [r["stats"][key] for r in spread]
        )
        checks.add(f"T-21: {key}", ok, why)
    ok, why = cmp.count_within(
        shape["stats"]["mean_completion_hours"],
        ref["stats"]["mean_completion_hours"],
        [r["stats"]["mean_completion_hours"] for r in spread],
    )
    checks.add("T-21: mean_completion_hours", ok, why)


def run(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME)
    for name, spec in JOBS.items():
        b = ctx.run("baseline", NAME, name, **spec)
        s = ctx.run("shape", NAME, name, **spec)
        if "error" in b or "error" in s:
            checks.add(
                f"{name}: both ran", False, f"baseline {b.get('error')}; Shape {s.get('error')}"
            )
            continue
        compare_outputs(checks, name, b, s, dead_ends=("parked",), first_initial="new")
    # WF-2: ids are random in the baseline and reproducible in Shape
    spec = {**JOBS["support_ticket"], "twice": True}
    b = ctx.run("baseline", NAME, "ids_baseline", **spec)
    s = ctx.run("shape", NAME, "ids_shape", **spec)
    bi = pd.read_parquet(Path(b["out_dir"]) / "events.parquet")["event_id"]
    bj = pd.read_parquet(Path(b["out_dir"]) / "ids_again.parquet")["event_id"]
    si = pd.read_parquet(Path(s["out_dir"]) / "events.parquet")["event_id"]
    sj = pd.read_parquet(Path(s["out_dir"]) / "ids_again.parquet")["event_id"]
    checks.add(
        "WF-2 probe: the baseline's ids change on every run, Shape's are reproducible",
        not bi.reset_index(drop=True).equals(bj.reset_index(drop=True)) and si.equals(sj),
        "",
    )
    t21(ctx, checks)
    return checks


# ---- negative controls --------------------------------------------------------------------


def negative_controls(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME + " negative control")
    spec = JOBS["order_fulfillment"]
    b = ctx.run("baseline", NAME, "neg_base", **spec)
    s = ctx.run("shape", NAME, "neg_shape", **spec)

    def caught(label: str, tamper: Any) -> None:
        work = sc.work_dir(NAME, "neg_tampered")
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(s["out_dir"], work)
        events = pd.read_parquet(work / "events.parquet")
        summary = pd.read_parquet(work / "summary.parquet")
        stats = dict(s["stats"])
        dist = dict(s["state_distribution"])
        events, summary, stats, dist = tamper(events, summary, stats, dist)
        events.to_parquet(work / "events.parquet")
        summary.to_parquet(work / "summary.parquet")
        probe = sc.Checks("probe")
        compare_outputs(
            probe, "tampered", b, {"out_dir": str(work), "stats": stats, "state_distribution": dist}
        )
        checks.add(
            f"caught: {label}", not probe.ok, "; ".join(c.name for c in probe.items if not c.ok)
        )

    def to_state(e: Any, m: Any, st: Any, d: Any) -> Any:
        e.loc[e.index[3], "to_state"] = (
            "returned" if e.loc[e.index[3], "to_state"] != "returned" else "shipped"
        )
        return e, m, st, d

    def time_shift(e: Any, m: Any, st: Any, d: Any) -> Any:
        e["transitioned_at"] = e["transitioned_at"] + pd.Timedelta(microseconds=1)
        return e, m, st, d

    def dwell(e: Any, m: Any, st: Any, d: Any) -> Any:
        e.loc[e.index[10], "dwell_hours"] += 0.0001
        return e, m, st, d

    def drop(e: Any, m: Any, st: Any, d: Any) -> Any:
        return e.iloc[1:], m, st, d

    def final(e: Any, m: Any, st: Any, d: Any) -> Any:
        m.loc[m.index[5], "final_state"] = (
            "cancelled" if m.loc[m.index[5], "final_state"] != "cancelled" else "delivered"
        )
        return e, m, st, d

    def stat(e: Any, m: Any, st: Any, d: Any) -> Any:
        st["anomaly_count"] += 1
        return e, m, st, d

    def dist_(e: Any, m: Any, st: Any, d: Any) -> Any:
        d[next(iter(d))] -= 1
        return e, m, st, d

    def order(e: Any, m: Any, st: Any, d: Any) -> Any:
        return e.iloc[::-1], m, st, d

    caught("a changed to_state", to_state)
    caught("a transition time moved by one microsecond", time_shift)
    caught("a dwell time changed in the fourth decimal", dwell)
    caught("a missing event", drop)
    caught("a changed final state in the summary", final)
    caught("a changed statistic", stat)
    caught("a changed state distribution", dist_)
    caught("events in another order", order)

    # T-21
    def one(side: str, seed: int) -> dict[str, Any]:
        return ctx.run(
            side,
            NAME,
            f"neg_t21_{side}_{seed}",
            **{**T21_JOB, "config": {**T21_JOB["config"], "seed": seed}},
        )

    ref = one("baseline", ctx.ref_seed)
    spread = [one("baseline", sd) for sd in ctx.seeds]
    shape = one("shape", ctx.shape_seed)
    ev_r, _ = _frames(ref)
    ev_s, _ = _frames(shape)
    sp = [_frames(r)[0] for r in spread]
    clean = cmp.t21_columns(ev_r, sp, ev_s, skip=("event_id", "entity_id"))
    checks.add(
        "control: untouched Shape events pass T-21",
        all(v.get("equivalent") for v in clean.values()),
        str([c for c, v in clean.items() if not v.get("equivalent")]),
    )
    slow = ev_s.copy()
    slow["dwell_hours"] = slow["dwell_hours"] * 1.3
    checks.add(
        "caught: dwell times 30% longer",
        not cmp.t21_columns(ev_r, sp, slow, skip=("event_id", "entity_id"))["dwell_hours"][
            "equivalent"
        ],
        "KS",
    )
    flipped = ev_s.copy()
    flipped["is_anomaly"] = flipped["is_anomaly"] | (flipped.index % 10 == 0)
    checks.add(
        "caught: a tenth of the events flagged as anomalies",
        not cmp.t21_columns(ev_r, sp, flipped, skip=("event_id", "entity_id"))["is_anomaly"][
            "equivalent"
        ],
        "TVD",
    )
    states = ev_s.copy()
    states["to_state"] = states["to_state"].where(states.index % 4 != 0, "delivered")
    checks.add(
        "caught: a quarter of the to_state values changed",
        not cmp.t21_columns(ev_r, sp, states, skip=("event_id", "entity_id"))["to_state"][
            "equivalent"
        ],
        "TVD",
    )
    return checks
