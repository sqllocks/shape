"""Case: the hybrid simulator (``shape_simulation.hybrid``) against the baseline's.

A file drop and a stream of the same tables. Mechanism parity (the drop's tree and the stream's
events equal, the run id aside), the allow-list probes, T-21 on both sides' output and the
negative controls. The drop and the stream themselves are verified in depth by their own cases
(``case_file_drop``, ``case_stream_emit``); this case checks that they are composed the same way.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import case_file_drop as fd
import case_stream_emit as se
import pandas as pd
import sim_common as sc
import sim_compare as cmp
import sim_trees as trees

NAME = "hybrid"

ALLOWED: dict[str, dict[str, str]] = {
    "HY-1": {
        "what": "the run id in manifests and events",
        "baseline": "the run's id is written only into the _correlation_id column of the rows; "
        "every manifest and every event has a correlation id of its own, so the files and the "
        "events cannot be joined on what the documentation says they share",
        "shape": "the run id is also the correlation_id of every manifest and the correlationid "
        "of every event (with the natural_keys strategy nothing is stamped, as before)",
    },
    "HY-2": {
        "what": "an unknown table name in stream_tables or batch_tables",
        "baseline": "names that are not tables are skipped without a word: a mistyped table "
        "silently leaves its stream or its batch out",
        "shape": "raises a ValueError that names the table",
    },
}

DROP = {"domain": "retail", "date_range_start": "2022-01-01", "date_range_end": "2022-12-31", "lateness_probability": 0.2, "duplicates_enabled": True, "seed": 51}
STREAM = {"out_of_order_probability": 0.1, "replay_enabled": True, "replay_probability": 0.05, "replay_burst_size": 5, "seed": 52}
JOBS: dict[str, dict[str, Any]] = {
    "both_paths": {
        "tables": ["order", "return"],
        "file_drop": DROP,
        "stream": STREAM,
        "hybrid": {},
    },
    "split_concurrent_natural_keys": {
        "tables": ["order", "return", "customer"],
        "file_drop": {**DROP, "lateness_enabled": False},
        "stream": {**STREAM, "max_events": 800},
        "hybrid": {"stream_tables": ["order", "customer"], "batch_tables": ["return", "customer"], "concurrent": True, "link_strategy": "natural_keys"},
    },
}
T21_TABLES = ["order", "return"]
T21_DROP = {"domain": "retail", "date_range_start": "2022-01-01", "date_range_end": "2025-12-31", "lateness_probability": 0.1, "duplicates_enabled": True, "duplicate_probability": 0.05}
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ---- the two sides ------------------------------------------------------------------------


def _run(job: dict[str, Any], side: str) -> dict[str, Any]:
    if side == "baseline":
        from sqllocks_spindle.simulation import FileDropConfig, HybridConfig, HybridSimulator, StreamEmitConfig

        read = lambda p: __import__("pandas").read_parquet(p)  # noqa: E731
    else:
        import pyarrow.parquet as pq
        from shape_simulation.file_drop import FileDropConfig
        from shape_simulation.hybrid import HybridConfig, HybridSimulator
        from shape_simulation.stream_emit import StreamEmitConfig

        read = lambda p: pq.read_table(p)  # noqa: E731
    out = Path(job["out_dir"])
    tables = {t: read(Path(job["input_dir"]) / f"{t}.parquet") for t in job["tables"]}
    drop = FileDropConfig(base_path=str(out / "landing"), **job["file_drop"])
    stream = StreamEmitConfig(sink_type="file", sink_connection={"path": str(out / "events.jsonl"), "mode": "w"}, **job["stream"])
    cfg = HybridConfig(file_drop_config=drop, stream_config=stream, **job.get("hybrid", {}))
    res = HybridSimulator(tables=tables, config=cfg).run()
    fd_res, st_res = res.file_drop_result, res.stream_result
    return {
        "correlation_id": res.correlation_id,
        "link_strategy": res.link_strategy,
        "drop_stats": fd_res.stats if fd_res else None,
        "drop_files": len(fd_res.files_written) if fd_res else 0,
        "stream": {"events_sent": st_res.events_sent, "replays": st_res.replay_events_sent, "topics": sorted(st_res.topics_used)} if st_res else None,
    }


def baseline_side(job: dict[str, Any]) -> dict[str, Any]:
    return _run(job, "baseline")


def shape_side(job: dict[str, Any]) -> dict[str, Any]:
    return _run(job, "shape")


# ---- the comparison -----------------------------------------------------------------------


def _manifest_ids(root: Path) -> set[str]:
    return {json.loads(p.read_text())["correlation_id"] for p in root.rglob("_manifest.json")}


def _column_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    for p in root.rglob("*.parquet"):
        df = pd.read_parquet(p)
        if "_correlation_id" in df.columns:
            ids |= set(df["_correlation_id"].dropna())
    return ids


def _event_ids(path: Path) -> tuple[set[str], set[str]]:
    """(correlation ids of the envelopes, ids in the data) of an events file; baseline or Shape names."""
    env, data = set(), set()
    for e in se.read_events(path):
        env.add(e.get("correlationid") or e.get("correlation_id"))
        data.add((e.get("data") or {}).get("_correlation_id"))
    return env, data


def compare_hybrid(checks: sc.Checks, label: str, b: dict[str, Any], s: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    b_root, s_root = Path(b["out_dir"]) / "landing", Path(s["out_dir"]) / "landing"
    linked = spec.get("hybrid", {}).get("link_strategy", "correlation_id") == "correlation_id"
    both = sc.Checks("inner")
    seen: dict[str, int] = {}
    inner_explain = fd._explain(seen)

    def explain(path: str, a: pd.DataFrame, b: pd.DataFrame) -> bool:
        """The drop's allowed differences, with the run id (each tool's own) left out."""
        return inner_explain(path, a.drop(columns=["_correlation_id"], errors="ignore"), b.drop(columns=["_correlation_id"], errors="ignore"))

    trees.compare_trees(both, label, b_root, s_root, ignore_columns=("_restated_at", "_correlation_id"), explain=explain)
    checks.items.extend(both.items)
    checks.add(f"{label}: result statistics equal (drop stats, files, stream counts, link strategy)", (b["drop_stats"], b["stream"], b["link_strategy"]) == (s["drop_stats"], s["stream"], s["link_strategy"]) or _stats_equal_fd2(b, s), f"{s['drop_stats']} {s['stream']}")
    if (Path(b["out_dir"]) / "events.jsonl").exists() or (Path(s["out_dir"]) / "events.jsonl").exists():
        inner = sc.Checks("inner")
        se.compare_events(inner, label, Path(b["out_dir"]) / "events.jsonl", Path(s["out_dir"]) / "events.jsonl", ignore_data=("_correlation_id",))
        checks.items.extend(inner.items)
    # the run id: a UUID, and (when linked) the one value in every row of both sides
    checks.add(f"{label}: Shape's run id is a UUID", bool(UUID.match(s["correlation_id"])), s["correlation_id"])
    col_s = _column_ids(s_root)
    env_s, data_s = _event_ids(Path(s["out_dir"]) / "events.jsonl") if (Path(s["out_dir"]) / "events.jsonl").exists() else (set(), set())
    if linked:
        checks.add(f"{label}: every row of the drop and every event carries the run id (_correlation_id)", col_s <= {s["correlation_id"]} and data_s <= {s["correlation_id"]} and (col_s or data_s), f"{col_s} {data_s}")
    else:
        checks.add(f"{label}: nothing is stamped with natural_keys", not col_s and data_s <= {None}, f"{col_s} {data_s}")
    return {"manifest_ids": _manifest_ids(s_root), "column_ids": col_s, "envelope_ids": env_s, "data_ids": data_s}


def _stats_equal_fd2(b: dict[str, Any], s: dict[str, Any]) -> bool:
    """The stats equal except that the baseline counts a file a later late arrival replaced
    twice (FD-2); everything else must be identical."""
    if b["stream"] != s["stream"] or b["link_strategy"] != s["link_strategy"]:
        return False
    if (b["drop_stats"] is None) != (s["drop_stats"] is None):
        return False
    for entity, bs in (b["drop_stats"] or {}).items():
        ss = s["drop_stats"].get(entity)
        if ss is None or bs["rows_written"] != ss["rows_written"] or bs["formats"] != ss["formats"] or ss["files"] > bs["files"]:
            return False
    return True


# ---- run ----------------------------------------------------------------------------------


def run(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME)
    first: dict[str, Any] = {}
    for name, spec in JOBS.items():
        params = {**spec, "input_dir": str(ctx.exact_dir)}
        b = ctx.run("baseline", NAME, name, **params)
        s = ctx.run("shape", NAME, name, **params)
        if "error" in b or "error" in s:
            checks.add(f"{name}: both ran", False, f"baseline {b.get('error')}; Shape {s.get('error')}")
            continue
        info = compare_hybrid(checks, name, b, s, spec)
        if name == "both_paths":
            first = {"b": b, "s": s, "info": info}
    # HY-1: the run id is where the documentation says; the baseline's manifests and events differ from it
    if first:
        b, s, info = first["b"], first["s"], first["info"]
        b_root = Path(b["out_dir"]) / "landing"
        b_manifest, b_cols = _manifest_ids(b_root), _column_ids(b_root)
        b_env, b_data = _event_ids(Path(b["out_dir"]) / "events.jsonl")
        checks.add(
            "HY-1 probe: the baseline's manifests and events carry ids other than the run id in the rows, Shape's carry the run id",
            b_cols == {b["correlation_id"]} and not (b_manifest & b_cols) and not (b_env & b_cols)
            and info["manifest_ids"] == {s["correlation_id"]} and info["envelope_ids"] == {s["correlation_id"]} and info["column_ids"] == {s["correlation_id"]},
            f"baseline: rows {len(b_cols)} id, manifests {len(b_manifest)} ids, events {len(b_env)} ids (none equal the run id); Shape: one id everywhere",
        )
    # HY-2: an unknown table name
    spec = {**JOBS["both_paths"], "hybrid": {"stream_tables": ["order", "ordr"]}}
    params = {**spec, "input_dir": str(ctx.exact_dir)}
    b = ctx.run("baseline", NAME, "unknown_table", **params)
    s = ctx.run("shape", NAME, "unknown_table", **params)
    checks.add(
        "HY-2 probe: the baseline skips an unknown stream table without a word (and streams the rest), Shape raises",
        "error" not in b and b["stream"]["topics"] == ["order"] and "error" in s and "ordr" in s["error"],
        f"baseline error {b.get('error')} stream {b.get('stream')}; Shape error {s.get('error')}",
    )
    t21(ctx, checks)
    return checks


def t21(ctx: Any, checks: sc.Checks) -> None:
    def one(side: str, tool: str, seed: int) -> dict[str, Any]:
        r = ctx.run(side, NAME, f"t21_{side}_{seed}", input_dir=str(ctx.input_dir(tool, seed)), tables=T21_TABLES, file_drop={**T21_DROP, "seed": seed}, stream={**se.T21_CONFIG, "seed": seed}, hybrid={})
        if "error" in r:
            raise RuntimeError(r["error"])
        return r

    ref = one("baseline", "baseline", ctx.ref_seed)
    spread = [one("baseline", "baseline", sd) for sd in ctx.seeds]
    shape = one("shape", "shape", ctx.shape_seed)
    for entity in T21_TABLES:  # the drop's rows
        fr, fs = fd._rows(Path(ref["out_dir"]) / "landing", entity), fd._rows(Path(shape["out_dir"]) / "landing", entity)
        sp = [fd._rows(Path(r["out_dir"]) / "landing", entity) for r in spread]
        drop = ("_correlation_id",)
        res = cmp.t21_columns(fr, sp, fs, skip=drop)
        bad = [c for c, v in res.items() if not v.get("equivalent", False)]
        checks.add(f"T-21 drop {entity}: (a)-(e) on {len(res) - 1} columns", not bad, f"not equivalent: {bad}")
    ev = lambda r, mapped: se._data_frame(Path(r["out_dir"]) / "events.jsonl", mapped)  # noqa: E731
    fr, nr, rr = ev(ref, True)
    fs, ns, rs = ev(shape, False)
    sp = [ev(r, True) for r in spread]
    res = cmp.t21_columns(fr, [d for d, _, _ in sp], fs, skip=("_correlation_id",))
    bad = [c for c, v in res.items() if not v.get("equivalent", False)]
    checks.add(f"T-21 stream: (a)-(e) on {len(res) - 1} data fields", not bad, f"not equivalent: {bad}")
    ok, why = cmp.count_within(ns, nr, [n for _, n, _ in sp])
    checks.add("T-21 stream: events delivered", ok, why)


# ---- negative controls --------------------------------------------------------------------


def negative_controls(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME + " negative control")
    spec = JOBS["both_paths"]
    params = {**spec, "input_dir": str(ctx.exact_dir)}
    b = ctx.run("baseline", NAME, "neg_base", **params)
    s = ctx.run("shape", NAME, "neg_shape", **params)

    def caught(label: str, tamper: Any) -> None:
        work = sc.work_dir(NAME, "neg_tampered")
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(s["out_dir"], work)
        tamper(work)
        probe = sc.Checks("probe")
        compare_hybrid(probe, "tampered", b, {**s, "out_dir": str(work)}, spec)
        checks.add(f"caught: {label}", not probe.ok, "; ".join(c.name for c in probe.items if not c.ok))

    def first_parquet(root: Path) -> Path:
        return sorted((root / "landing").rglob("*order*00001.parquet"))[3]

    def value(root: Path) -> None:
        p = first_parquet(root)
        df = pd.read_parquet(p)
        df.loc[df.index[0], "order_total"] = float(df["order_total"].iloc[0]) + 1
        df.to_parquet(p)

    def stamp(root: Path) -> None:
        p = first_parquet(root)
        df = pd.read_parquet(p)
        df["_correlation_id"] = "00000000-0000-0000-0000-000000000000"
        df.to_parquet(p)

    def manifest_id(root: Path) -> None:
        p = next((root / "landing").rglob("_manifest.json"))
        doc = json.loads(p.read_text())
        doc["correlation_id"] = "00000000-0000-0000-0000-000000000000"
        p.write_text(json.dumps(doc))

    def event_id(root: Path) -> None:
        path = root / "events.jsonl"
        lines = path.read_text().splitlines()
        e = json.loads(lines[4])
        e["correlationid"] = "00000000-0000-0000-0000-000000000000"
        lines[4] = json.dumps(e)
        path.write_text("\n".join(lines) + "\n")

    def drop_event(root: Path) -> None:
        path = root / "events.jsonl"
        lines = path.read_text().splitlines()
        del lines[9]
        path.write_text("\n".join(lines) + "\n")

    def drop_file(root: Path) -> None:
        first_parquet(root).unlink()

    caught("a changed value in the drop", value)
    caught("a row stamped with another run id", stamp)
    caught("a manifest with another correlation id", manifest_id)
    caught("an event with another correlation id", event_id)
    caught("a missing event", drop_event)
    caught("a missing data file", drop_file)
    return checks
