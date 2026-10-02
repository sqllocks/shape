"""Case: the stream emitter (``shape_simulation.stream_emit``) against the baseline's.

Mechanism parity (same tables, configuration and seed: the same events in the same order, the same
replays), the allow-list probes, T-21 on the event multiset and the negative controls. The
baseline's names are mapped to Shape's (``sim_common.NAME_MAP``) before the events are compared.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import pandas as pd
import sim_common as sc
import sim_compare as cmp

NAME = "stream_emit"

ALLOWED: dict[str, dict[str, str]] = {
    "SE-1": {
        "what": "burst_windows",
        "baseline": "the setting is accepted and never read: a paced run with a burst window runs "
        "at the base rate throughout",
        "shape": "the runtime's rate schedule applies the bursts (the rate is multiplied inside "
        "each window)",
    },
    "SE-2": {
        "what": "sink_type eventstream",
        "baseline": "accepted and replaced by the console sink: the events go to standard output "
        "and nothing is sent anywhere",
        "shape": "raises a ValueError (an emitter is named by its URI, eventstream://...); a sink "
        "type that is not console, file or a URI is never a silent console",
    },
    "SE-3": {
        "what": "correlation id",
        "baseline": "a new random correlation_id for every event, so no two events of a run share "
        "one and nothing can be joined on it",
        "shape": "one correlationid for the whole run (the hybrid simulator passes its own run id "
        "to its manifests and events)",
    },
    "SE-4": {
        "what": "replay window across emit() calls",
        "baseline": "the replay buffer survives between calls: a second emit() can send again "
        "events of the first call that are not among its own events",
        "shape": "the window starts empty at every call: a replay is only ever of this call's "
        "events",
    },
}

TABLES = ["customer", "order", "return"]
JOBS: dict[str, dict[str, Any]] = {
    "mixed": {
        "tables": TABLES,
        "config": {
            "out_of_order_probability": 0.1,
            "replay_enabled": True,
            "replay_probability": 0.05,
            "replay_burst_size": 5,
            "jitter_ms": 5.0,
            "max_events": 4000,
            "seed": 7,
        },
    },
    "topics_all_events": {
        "tables": TABLES,
        "config": {
            "topics": ["cust", "ord", "ret"],
            "envelope_schema_version": "2.3",
            "envelope_source": "myapp",
            "seed": 8,
        },
    },
    "single_topic": {
        "tables": TABLES,
        "config": {
            "topics": ["everything"],
            "out_of_order_probability": 0.3,
            "max_events": 1500,
            "seed": 9,
        },
    },
    "replay_heavy": {
        "tables": ["order", "return"],
        "config": {
            "replay_enabled": True,
            "replay_probability": 0.5,
            "replay_burst_size": 150,
            "replay_window_minutes": 0.001,
            "max_events": 800,
            "seed": 10,
        },
    },
    "two_emits": {
        "tables": ["customer", "return"],
        "emits": 2,
        "config": {"out_of_order_probability": 0.2, "max_events": 600, "seed": 11},
    },
    "default_path": {
        "tables": ["return"],
        "default_path": True,
        "config": {"max_events": 50, "seed": 12},
    },
}
T21_TABLES = ["order"]
T21_CONFIG = {
    "out_of_order_probability": 0.1,
    "replay_enabled": True,
    "replay_probability": 0.02,
    "replay_burst_size": 5,
}
SHAPE_ONLY_DATA = {"_shape_event_time"}
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# ---- the two sides ------------------------------------------------------------------------


def _config_kwargs(job: dict[str, Any], side: str) -> dict[str, Any]:
    kw = dict(job["config"])
    if "burst_windows" in kw:
        if side == "baseline":
            from sqllocks_spindle.streaming.config import BurstWindow
        else:
            from shape_simulation.stream_emit import BurstWindow
        kw["burst_windows"] = [BurstWindow(*b) for b in kw["burst_windows"]]
    return kw


def _run(job: dict[str, Any], side: str) -> dict[str, Any]:
    if side == "baseline":
        from sqllocks_spindle.simulation import StreamEmitConfig, StreamEmitter

        read = lambda p: pd.read_parquet(p)  # noqa: E731
    else:
        import pyarrow.parquet as pq
        from shape_simulation.stream_emit import StreamEmitConfig, StreamEmitter

        read = lambda p: pq.read_table(p)  # noqa: E731
    out = Path(job["out_dir"])
    tables = {t: read(Path(job["input_dir"]) / f"{t}.parquet") for t in job["tables"]}
    kind = job.get("sink_type", "file")
    connection: dict[str, Any] = (
        {} if job.get("default_path") else {"path": str(out / "events.jsonl"), "mode": "w"}
    )
    cfg = StreamEmitConfig(sink_type=kind, sink_connection=connection, **_config_kwargs(job, side))
    if job.get("default_path"):
        os.chdir(out)
    printed = io.StringIO()
    started = time.time()
    results = []
    calls = job.get("emit_tables") or [None] * int(job.get("emits", 1))
    with contextlib.redirect_stdout(printed):  # the console sink binds standard output when built
        emitter = StreamEmitter(tables=tables, config=cfg)
        for names in calls:
            r = emitter.emit(tables={n: tables[n] for n in names}) if names else emitter.emit()
            results.append(
                {
                    "events_sent": r.events_sent,
                    "replay_events_sent": r.replay_events_sent,
                    "topics": sorted(r.topics_used),
                    "schema_versions": r.schema_versions,
                }
            )
    return {
        "results": results,
        "elapsed": time.time() - started,
        "stdout_lines": len([x for x in printed.getvalue().splitlines() if x.strip()]),
    }


def baseline_side(job: dict[str, Any]) -> dict[str, Any]:
    return _run(job, "baseline")


def shape_side(job: dict[str, Any]) -> dict[str, Any]:
    return _run(job, "shape")


# ---- reading and comparing events --------------------------------------------------------


def read_events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def mapped(name: str) -> str:
    """The Shape name of a baseline field (D-13)."""
    return sc.NAME_MAP.get(name, name)


def baseline_event(e: dict[str, Any]) -> dict[str, Any]:
    """A baseline event with its names, and its source and type, mapped to Shape's."""
    out: dict[str, Any] = {}
    for k, v in e.items():
        if k == "data":
            out[mapped(k)] = {mapped(f): x for f, x in v.items()}
        elif k == "source" and v == "spindle":
            out[k] = sc.NAME_MAP["spindle"]
        elif k == "type":
            out[k] = re.sub(r"^spindle\.", f"{sc.NAME_MAP['spindle']}.", v)
        else:
            out[mapped(k)] = v
    return out


def _key(e: dict[str, Any]) -> tuple[str, int, bool]:
    data = e.get("data") or {}
    return data.get("_shape_table"), data.get("_shape_seq"), bool(e.get("replay", False))


def compare_events(
    checks: sc.Checks,
    label: str,
    b_path: Path,
    s_path: Path,
    *,
    ids: bool = True,
    ignore_data: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """The events of Shape's file equal the baseline's, in the same order, field by field."""
    be = [baseline_event(e) for e in read_events(b_path)]
    se = read_events(s_path)
    checks.add(f"{label}: same number of events", len(be) == len(se), f"{len(be)} vs {len(se)}")
    if len(be) != len(se):
        return se
    kb, ks = [_key(e) for e in be], [_key(e) for e in se]
    first_bad = next((i for i, (x, y) in enumerate(zip(kb, ks, strict=True)) if x != y), None)
    checks.add(
        f"{label}: same order of (table, row, replay) for every event",
        first_bad is None,
        f"first difference at event {first_bad}",
    )
    # envelope: the baseline's keys mapped, plus Shape's own (shapetable, shapeseq)
    bad_keys = [
        i
        for i, (x, y) in enumerate(zip(be, se, strict=True))
        if set(y) != set(x) | {"shapetable", "shapeseq"}
    ]
    checks.add(
        f"{label}: envelope fields (mapped baseline names + shapetable, shapeseq)",
        not bad_keys,
        f"first at {bad_keys[:3]}",
    )
    plain = ("specversion", "source", "type", "datacontenttype", "topic", "schemaversion")
    bad_vals = [
        i
        for i, (x, y) in enumerate(zip(be, se, strict=True))
        if any(x.get(k) != y.get(k) for k in plain)
    ]
    checks.add(
        f"{label}: envelope values equal (specversion, source, type, datacontenttype, topic, "
        f"schemaversion)",
        not bad_vals,
        f"first at {bad_vals[:3]}",
    )
    forms = [
        i
        for i, y in enumerate(se)
        if not ISO_Z.match(y.get("time", ""))
        or (y.get("replay") and not ISO_Z.match(y.get("replaytime", "")))
        or y.get("id") != f"{y.get('shapetable')}/{y.get('shapeseq')}"
    ]
    checks.add(
        f"{label}: Shape time/replaytime are ISO-8601 UTC, id is <table>/<row>",
        not forms,
        f"first at {forms[:3]}",
    )
    if ids:
        seen: dict[tuple[str, int], str] = {}
        dup_ok = True
        for y in se:
            key = (y["shapetable"], y["shapeseq"])
            if y.get("replay"):
                dup_ok &= seen.get(key) == y["id"]
            else:
                seen.setdefault(key, y["id"])
        checks.add(f"{label}: a replayed event repeats the id of its original", dup_ok, "")
    # data: names and order of the fields, then the values
    order_ok = True
    firsts: dict[str, list[str]] = {}
    for x, y in zip(be, se, strict=True):
        t = x["data"]["_shape_table"]
        if t not in firsts:
            firsts[t] = list(x["data"])
            order_ok &= [k for k in y.get("data", {}) if k not in SHAPE_ONLY_DATA] == firsts[t]
    checks.add(f"{label}: data field names and order (mapped)", order_ok, "")
    bdf = pd.DataFrame([x["data"] for x in be])
    sdf = pd.DataFrame(
        [{k: v for k, v in y.get("data", {}).items() if k not in SHAPE_ONLY_DATA} for y in se]
    )
    ok, why = cmp.frames_equal(bdf, sdf, same_order=False, ignore=ignore_data)
    checks.add(f"{label}: data values equal", ok, why)
    # the event time field Shape adds is the table's first date or timestamp column
    et_bad = 0
    for y in se:
        d = y.get("data", {})
        if "_shape_event_time" in d:
            col = next(
                (
                    c
                    for c, v in d.items()
                    if isinstance(v, str) and DATETIME.match(v) and not c.startswith("_")
                ),
                None,
            )
            et_bad += col is None or d[col] != d["_shape_event_time"]
    checks.add(
        f"{label}: _shape_event_time is the first date column's value",
        et_bad == 0,
        f"{et_bad} events",
    )
    return se


# ---- mechanism parity ---------------------------------------------------------------------


def mechanism(
    ctx: Any, checks: sc.Checks, name: str, spec: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = {**spec, "input_dir": str(ctx.exact_dir)}
    b = ctx.run("baseline", NAME, name, **params)
    s = ctx.run("shape", NAME, name, **params)
    if "error" in b or "error" in s:
        checks.add(f"{name}: both ran", False, f"baseline {b.get('error')}; Shape {s.get('error')}")
        return b, s
    bf = Path(b["out_dir"]) / (
        "spindle_events.jsonl" if spec.get("default_path") else "events.jsonl"
    )
    sf = Path(s["out_dir"]) / ("events.jsonl" if spec.get("default_path") else "events.jsonl")
    checks.add(
        f"{name}: the default event file is named events.jsonl (baseline's name mapped)",
        bf.exists()
        and sf.exists()
        and (not spec.get("default_path") or sc.NAME_MAP["spindle_events.jsonl"] == "events.jsonl"),
        f"{bf.name} / {sf.name}",
    )
    if bf.exists() and sf.exists():
        compare_events(checks, name, bf, sf)
    rb, rs = b["results"], s["results"]
    checks.add(
        f"{name}: results (events sent, replays, topics, schema versions) equal",
        [(r["events_sent"], r["replay_events_sent"], r["topics"], r["schema_versions"]) for r in rb]
        == [
            (r["events_sent"], r["replay_events_sent"], r["topics"], r["schema_versions"])
            for r in rs
        ],
        f"{[(r['events_sent'], r['replay_events_sent']) for r in rs]}",
    )
    return b, s


# ---- probes -------------------------------------------------------------------------------


def probe_se1(ctx: Any, checks: sc.Checks) -> None:
    """SE-1: burst windows. 300 paced events at 100/s; a window of 3x for the first 1.5 s."""
    cfg = {
        "realtime": True,
        "rate_per_sec": 100.0,
        "max_events": 300,
        "burst_windows": [[0.0, 1.5, 3.0]],
        "seed": 1,
    }
    params = {"input_dir": str(ctx.exact_dir), "tables": ["order"], "config": cfg}
    b = ctx.run("baseline", NAME, "burst_baseline", **params)
    s = ctx.run("shape", NAME, "burst_shape", **params)
    eb, es = b.get("elapsed", 0.0), s.get("elapsed", 0.0)
    checks.add(
        "SE-1 probe: the baseline ignores the burst window (paced at the base rate: about 3 "
        "s), Shape applies it (about 1 s)",
        eb >= 2.9 and 0.8 <= es <= 1.9,
        f"baseline {eb:.2f} s, Shape {es:.2f} s for 300 events at 100/s with a 3x burst for 1.5 s",
    )


def probe_se2(ctx: Any, checks: sc.Checks) -> None:
    params = {
        "input_dir": str(ctx.exact_dir),
        "tables": ["return"],
        "sink_type": "eventstream",
        "config": {"max_events": 20, "seed": 1},
    }
    b = ctx.run("baseline", NAME, "eventstream", **params)
    s = ctx.run("shape", NAME, "eventstream", **params)
    checks.add(
        "SE-2 probe: the baseline's eventstream sink prints the events on standard output, "
        "Shape raises",
        "error" not in b and b["stdout_lines"] == 20 and "error" in s and "sink_type" in s["error"],
        f"baseline error {b.get('error')} stdout lines {b.get('stdout_lines')}; Shape error "
        f"{s.get('error')}",
    )


def probe_se3_se4(ctx: Any, checks: sc.Checks) -> None:
    spec = {
        "tables": ["customer", "return"],
        "emit_tables": [["customer"], ["return"]],
        "config": {
            "replay_enabled": True,
            "replay_probability": 0.9,
            "replay_burst_size": 5,
            "max_events": 40,
            "seed": 3,
        },
    }
    params = {**spec, "input_dir": str(ctx.exact_dir)}
    b = ctx.run("baseline", NAME, "replay_across_emits", **params)
    s = ctx.run("shape", NAME, "replay_across_emits", **params)
    be = [baseline_event(e) for e in read_events(Path(b["out_dir"]) / "events.jsonl")]
    se = read_events(Path(s["out_dir"]) / "events.jsonl")
    cb = {e["correlationid"] for e in be}
    cs = {e["correlationid"] for e in se}
    primaries = sum(1 for e in be if not e.get("replay"))
    checks.add(
        "SE-3 probe: every baseline event has its own correlation id (a replay repeats its "
        "original's), Shape's events share one",
        len(cb) == primaries and primaries > 1 and len(cs) == 1,
        f"baseline {len(cb)} ids for {primaries} primary events; Shape {len(cs)}",
    )

    def foreign_replays(events: list[dict[str, Any]], per_call: int = 40) -> int:
        """Replays in the second call (after the first ``per_call`` primary events and their
        replays) of events that are not among the second call's own primary events."""
        primaries = 0
        second: list[dict[str, Any]] = []
        for e in events:
            primaries += not e.get("replay")
            if primaries > per_call:
                second.append(e)
        own = {
            (e["data"]["_shape_table"], e["data"]["_shape_seq"])
            for e in second
            if not e.get("replay")
        }
        return sum(
            1
            for e in second
            if e.get("replay") and (e["data"]["_shape_table"], e["data"]["_shape_seq"]) not in own
        )

    fb, fs = foreign_replays(be), foreign_replays(se)
    checks.add(
        "SE-4 probe: the baseline's second call replays events of the first, Shape's never does",
        fb > 0 and fs == 0,
        f"replays of events outside the second call: baseline {fb}, Shape {fs}",
    )


# ---- T-21 ---------------------------------------------------------------------------------


def _data_frame(path: Path, mapped_names: bool) -> tuple[pd.DataFrame, int, int]:
    events = read_events(path)
    rows = []
    for e in events:
        d = baseline_event(e)["data"] if mapped_names else e["data"]
        rows.append(
            {
                k: v
                for k, v in d.items()
                if k not in SHAPE_ONLY_DATA and k not in ("_shape_table", "_shape_seq")
            }
        )
    return (
        pd.DataFrame(rows),
        len(events),
        sum(1 for e in events if e.get("replay") or e.get("_replay")),
    )


def t21(ctx: Any, checks: sc.Checks) -> None:
    def one(side: str, tool: str, seed: int) -> tuple[pd.DataFrame, int, int]:
        r = ctx.run(
            side,
            NAME,
            f"t21_{side}_{seed}",
            input_dir=str(ctx.input_dir(tool, seed)),
            tables=T21_TABLES,
            config={**T21_CONFIG, "seed": seed},
        )
        if "error" in r:
            raise RuntimeError(r["error"])
        return _data_frame(Path(r["out_dir"]) / "events.jsonl", side == "baseline")

    ref, ref_n, ref_r = one("baseline", "baseline", ctx.ref_seed)
    spread = [one("baseline", "baseline", sd) for sd in ctx.seeds]
    shape, shape_n, shape_r = one("shape", "shape", ctx.shape_seed)
    res = cmp.t21_columns(ref, [d for d, _, _ in spread], shape)
    bad = [c for c, v in res.items() if not v.get("equivalent", False)]
    checks.add(
        f"T-21 events: (a)-(e) on {len(res) - 1} data fields", not bad, f"not equivalent: {bad}"
    )
    ok, why = cmp.count_within(shape_n, ref_n, [n for _, n, _ in spread])
    checks.add("T-21: events delivered (primary and replays)", ok, why)
    ok, why = cmp.count_within(shape_r, ref_r, [r for _, _, r in spread])
    checks.add("T-21: replayed events", ok, why)


# ---- run ----------------------------------------------------------------------------------


def run(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME)
    for name, spec in JOBS.items():
        mechanism(ctx, checks, name, spec)
    probe_se1(ctx, checks)
    probe_se2(ctx, checks)
    probe_se3_se4(ctx, checks)
    t21(ctx, checks)
    return checks


# ---- negative controls --------------------------------------------------------------------


def negative_controls(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME + " negative control")
    spec = {**JOBS["mixed"], "input_dir": str(ctx.exact_dir)}
    b = ctx.run("baseline", NAME, "neg_base", **spec)
    s = ctx.run("shape", NAME, "neg_shape", **spec)
    bf = Path(b["out_dir"]) / "events.jsonl"
    original = read_events(Path(s["out_dir"]) / "events.jsonl")

    def caught(label: str, tamper: Any) -> None:
        events = json.loads(json.dumps(original))
        tamper(events)
        work = sc.work_dir(NAME, "neg_tampered")
        work.mkdir(parents=True, exist_ok=True)
        path = work / "events.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        probe = sc.Checks("probe")
        compare_events(probe, "tampered", bf, path)
        checks.add(
            f"caught: {label}", not probe.ok, "; ".join(c.name for c in probe.items if not c.ok)
        )
        shutil.rmtree(work)

    def drop(ev: list[dict[str, Any]]) -> None:
        del ev[17]

    def value(ev: list[dict[str, Any]]) -> None:
        ev[5]["data"]["order_id" if "order_id" in ev[5]["data"] else "customer_id"] += 1

    def swap(ev: list[dict[str, Any]]) -> None:
        ev[10], ev[11] = ev[11], ev[10]

    def etype(ev: list[dict[str, Any]]) -> None:
        ev[3]["type"] = ev[3]["type"].replace("shape.", "x.")

    def topic(ev: list[dict[str, Any]]) -> None:
        ev[4]["topic"] = "other"

    def nodata(ev: list[dict[str, Any]]) -> None:
        ev[6].pop("data")

    def replay_flag(ev: list[dict[str, Any]]) -> None:
        i = next(i for i, e in enumerate(ev) if e.get("replay"))
        ev[i].pop("replay")
        ev[i].pop("replaytime")

    def rename(ev: list[dict[str, Any]]) -> None:
        d = ev[2]["data"]
        k = next(iter(d))
        d["renamed"] = d.pop(k)

    def seqs(ev: list[dict[str, Any]]) -> None:
        ev[8]["data"]["_shape_seq"] += 1

    for label, fn in (
        ("a missing event", drop),
        ("a changed data value", value),
        ("two events swapped", swap),
        ("a changed type", etype),
        ("a changed topic", topic),
        ("an event without data", nodata),
        ("a replay that lost its flag", replay_flag),
        ("a renamed data field", rename),
        ("a changed _shape_seq", seqs),
    ):
        caught(label, fn)

    # T-21 tampering
    def one(side: str, tool: str, seed: int) -> tuple[pd.DataFrame, int, int]:
        r = ctx.run(
            side,
            NAME,
            f"neg_t21_{side}_{seed}",
            input_dir=str(ctx.input_dir(tool, seed)),
            tables=T21_TABLES,
            config={**T21_CONFIG, "seed": seed},
        )
        return _data_frame(Path(r["out_dir"]) / "events.jsonl", side == "baseline")

    ref, _, _ = one("baseline", "baseline", ctx.ref_seed)
    sp = [one("baseline", "baseline", sd)[0] for sd in ctx.seeds]
    shape, _, _ = one("shape", "shape", ctx.shape_seed)
    clean = cmp.t21_columns(ref, sp, shape)
    checks.add(
        "control: untouched Shape events pass T-21",
        all(v.get("equivalent") for v in clean.values()),
        str([c for c, v in clean.items() if not v.get("equivalent")]),
    )
    moved = shape.copy()
    moved["order_date"] = pd.to_datetime(moved["order_date"]) + pd.Timedelta(days=365)
    checks.add(
        "caught: every order_date a year later",
        not cmp.t21_columns(ref, sp, moved)["order_date"]["equivalent"],
        "KS",
    )
    cat = shape.copy()
    cat["status"] = cat["status"].where(cat.index % 3 != 0, "unseen")
    checks.add(
        "caught: a third of status values replaced",
        not cmp.t21_columns(ref, sp, cat)["status"]["equivalent"],
        "TVD / vocabulary",
    )
    nulls = shape.copy()
    nulls.loc[nulls.index % 4 == 0, "store_id"] = None
    checks.add(
        "caught: a quarter of store_id nulled",
        not cmp.t21_columns(ref, sp, nulls)["store_id"]["equivalent"],
        "null rate",
    )
    return checks
