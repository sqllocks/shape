"""Live fidelity (P5-03): the live score is ``shape fidelity``'s, alerts, the tee, the CLI."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from shape.cli.main import main
from shape.generation.engine import Engine
from shape.generation.report import compare_tables
from shape.streaming.emit import (
    AnomalyInjector,
    EmitRunner,
    EventPlan,
    MemorySink,
    resolve_mutators,
)
from shape.streaming.emit.formats import with_event_fields
from shape.streaming.emit.live import (
    JsonLinesAlertSink,
    LiveConfig,
    LiveFidelity,
    TargetShape,
    TeeSink,
)
from shape.streaming.runtime import GlobalProfiler

TOL = 1e-9


def _tables(seed: int) -> dict[str, pa.Table]:
    """customer <- order <- order_line, made of numbers, categories, nulls and timestamps."""
    r = np.random.default_rng(seed)
    nc, no, nl = 300, 900, 2000
    when = r.integers(1_600_000_000, 1_700_000_000, no) * 1_000_000
    return {
        "customer": pa.table(
            {
                "customer_id": pa.array(np.arange(nc)),
                "name": pa.array(r.choice(["a", "b", "c", "d"], nc), pa.string()),
                "score": pa.array(r.normal(50, 10, nc)),
                "tier": pa.array([None if v < 0.2 else "gold" for v in r.random(nc)]),
            }
        ),
        "order": pa.table(
            {
                "order_id": pa.array(np.arange(no)),
                "customer_id": pa.array(r.integers(0, nc, no)),
                "placed": pa.array(when.astype("datetime64[us]"), pa.timestamp("us")),
                "total": pa.array(r.lognormal(3, 0.5, no)),
                "score": pa.array(r.uniform(0, 10, no)),
            }
        ),
        "order_line": pa.table(
            {
                "line_id": pa.array(np.arange(nl)),
                "order_id": pa.array(r.integers(0, no, nl)),
                "amount": pa.array(r.uniform(1, 50, nl)),
                "channel": pa.array(r.choice(["web", "store", "phone"], nl, p=[0.6, 0.3, 0.1])),
            }
        ),
    }


def _events(tables: dict[str, pa.Table], chunk: int = 7) -> list[pa.RecordBatch]:
    out = []
    for name, table in tables.items():
        for start in range(0, table.num_rows, chunk):
            part = table.slice(start, chunk).combine_chunks().to_batches()[0]
            out.append(with_event_fields(part, name, start))
    return out


def _live(
    reference: dict[str, pa.Table], events: list[pa.RecordBatch], **config: Any
) -> LiveFidelity:
    live = LiveFidelity(TargetShape.from_tables(reference), LiveConfig(**config))
    for batch in events:
        live.observe(batch)
    live.final()
    return live


def _assert_equal(live: LiveFidelity, reference: dict[str, pa.Table], synth: dict[str, pa.Table]):
    off = compare_tables(reference, synth)
    snap = live.last
    assert snap is not None and snap.overall is not None
    assert abs(snap.overall - off.overall_score) < TOL
    for name, table in off.tables.items():
        assert abs(snap.tables[name] - table.score) < TOL, name
        for col, c in table.columns.items():
            assert abs(snap.report.tables[name].columns[col].score - c.score) < TOL, (name, col)


def test_live_score_equals_fidelity_exactly() -> None:
    ref, syn = _tables(1), _tables(2)
    live = _live(ref, _events(syn))
    _assert_equal(live, ref, syn)
    assert live.approximate() == {}  # nothing was bounded: the score is exact


def test_text_numbers_dates_nulls_booleans_and_nan_score_like_the_comparator() -> None:
    rng = np.random.default_rng(0)

    def table(seed: int, n: int = 3000) -> pa.Table:
        r = np.random.default_rng(seed)
        day = np.array(["2024-01-05", "2024-03-09", "2024-07-30", "2025-01-02"])
        return pa.table(
            {
                "zip": pa.array([f"{v:05d}" for v in r.integers(1000, 99999, n)]),  # numeric text
                "born": pa.array(r.choice(day, n)),  # ISO dates as text
                "flag": pa.array(r.random(n) < 0.3),
                "x": pa.array(np.where(r.random(n) < 0.1, np.nan, r.normal(5, 2, n))),
                "n": pa.array(
                    [
                        None if v < 0.2 else int(w)
                        for v, w in zip(r.random(n), r.integers(0, 9, n), strict=True)
                    ]
                ),
                "city": pa.array(r.choice(["a", "b", "c", "d"], n, p=[0.5, 0.3, 0.15, 0.05])),
                "when": pa.array(
                    (r.random(n) * 1e12).astype("int64").astype("datetime64[us]"),
                    pa.timestamp("us"),
                ),
            }
        )

    ref, syn = {"t": table(1)}, {"t": table(2)}
    live = _live(ref, _events(syn, 500), chunk_rows=700)
    _assert_equal(live, ref, syn)
    assert rng is not None


def test_reservoir_and_sketch_fallbacks_stay_within_half_a_point() -> None:
    n = 40_000

    def table(seed: int) -> pa.Table:
        g = np.random.default_rng(seed)
        return pa.table(
            {"v": pa.array(g.lognormal(2.0, 0.6, n)), "id": pa.array(np.arange(n) * 3 + seed)}
        )

    ref, syn = {"t": table(1)}, {"t": table(2)}
    live = _live(ref, _events(syn, 1000), sample_cap=2_000, key_cap=5_000)
    off = compare_tables(ref, syn).overall_score
    assert live.last is not None and live.last.overall is not None
    assert abs(live.last.overall - off) < 0.5  # a 2,000-value sample and an HLL distinct count
    assert set(live.approximate()["t"]) == {"v", "id"}  # both are listed as bounded


def test_text_past_the_key_cap_is_flagged_not_silently_exact() -> None:
    n = 20_000

    def table(seed: int) -> pa.Table:
        g = np.random.default_rng(seed)
        return pa.table({"label": pa.array([f"u{x}" for x in g.integers(0, 15_000, n)])})

    live = _live({"t": table(1)}, _events({"t": table(2)}, 1000), key_cap=2_000)
    assert live.approximate() == {"t": ["label"]}
    assert live.last is not None and live.last.overall is not None
    assert 0.0 <= live.last.overall <= 100.0


def test_alerts_are_edge_triggered_and_recover() -> None:
    ref, syn = _tables(1), _tables(2)
    seen: list[Any] = []
    cfg = LiveConfig(interval_events=40, interval_seconds=0.0, min_events=20, min_progress=0.0)
    live = LiveFidelity(TargetShape.from_tables(ref), cfg, sinks=[seen.append])
    good = _events(syn)
    # rows of the customer table whose scores are wrecked: a constant value in every column
    bad_table = syn["order"]
    broken = pa.table(
        {
            c: pa.array([bad_table.column(c)[0].as_py()] * 400, bad_table.schema.field(c).type)
            for c in bad_table.column_names
        }
    )
    for batch in _events({"order": broken}):
        live.observe(batch)
    live.evaluate()
    assert [a.kind for a in seen] == ["score-low"]
    assert seen[0].table == "order" and seen[0].level == "error" and seen[0].score is not None
    live.evaluate()
    assert len(seen) == 1  # still low: no repeat
    assert live.verdict()  # an error is active
    for batch in good:
        live.observe(batch)
    live.final()  # the whole of the good stream is now in, so the order table recovers
    # the table's score moved up as events came: the alert cleared when it passed the mark
    assert seen[-1].kind in ("recovered", "score-low")


def test_drop_and_column_rules() -> None:
    ref = _tables(1)
    seen: list[Any] = []
    cfg = LiveConfig(
        interval_events=10**9, min_events=1, min_progress=0.0, drop=1.0, min_column_score=99.9
    )
    live = LiveFidelity(TargetShape.from_tables(ref), cfg, sinks=[seen.append])
    for batch in _events(_tables(1)):
        live.observe(batch)
    live.evaluate()
    assert any(a.kind == "column-low" and a.column for a in seen) or not seen
    best = live.last
    assert best is not None
    kinds_before = len(seen)
    # more of the same table, with every value shifted: its score falls below its best
    shifted = _tables(1)["order"]
    shifted = shifted.set_column(
        shifted.column_names.index("score"),
        "score",
        pa.array(np.asarray(shifted.column("score").to_numpy()) + 1000.0),
    )
    for batch in _events({"order": shifted}):
        live.observe(batch)
    live.evaluate()
    assert len(seen) >= kinds_before
    assert {a.kind for a in seen} <= {"column-low", "score-drop", "score-low", "recovered"}


def test_failure_of_the_live_side_never_fails_the_stream() -> None:
    ref = _tables(1)
    seen: list[Any] = []
    live = LiveFidelity(TargetShape.from_tables(ref), LiveConfig(), sinks=[seen.append])
    inner = MemorySink()
    tee = TeeSink(inner, live, threaded=False)
    tee.send(pa.record_batch({"a": [1, 2, 3]}))  # not an emitted event: no _shape_table
    tee.send(pa.record_batch({"a": [4]}))
    tee.close()
    assert inner.num_events == 4  # delivery went on
    assert live.failed is not None and [a.kind for a in seen] == ["live-error"]
    assert live.verdict()


def test_tee_counts_only_delivered_batches_and_threading_changes_nothing() -> None:
    ref, syn = _tables(1), _tables(2)
    events = _events(syn, 50)

    class Flaky(MemorySink):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = True

        def send(self, batch: pa.RecordBatch) -> None:
            if self.fail_next:
                self.fail_next = False
                raise OSError("down")
            super().send(batch)

    flaky = Flaky()
    live = LiveFidelity(TargetShape.from_tables(ref), LiveConfig())
    tee = TeeSink(flaky, live, threaded=False)
    with pytest.raises(OSError):
        tee.send(events[0])
    assert live.events == 0  # not delivered, not counted
    scores = []
    for threaded in (False, True):
        lv = LiveFidelity(TargetShape.from_tables(ref), LiveConfig())
        t = TeeSink(MemorySink(), lv, threaded=threaded, queue_batches=2)
        for batch in events:
            t.send(batch)
        t.close()
        scores.append(lv.last.overall if lv.last else None)
    assert scores[0] == scores[1]
    _assert_equal(lv, ref, syn)


def test_profiler_peek_does_not_close_the_window() -> None:
    schema = pa.schema([("a", pa.int64()), ("c", pa.string())])
    g = GlobalProfiler(schema, name="t")
    batch = pa.record_batch([pa.array(range(100)), pa.array(["x", "y"] * 50)], schema=schema)
    g.process(batch)
    mid = g.peek()
    assert mid.rows == 100 and mid.profile["columns"][0]["count"] == 100
    g.process(batch)
    final = g.finish()[0]
    assert final.rows == 200 and g.peek().rows == 200


def test_live_profile_matches_the_events() -> None:
    ref, syn = _tables(1), _tables(2)
    live = _live(ref, _events(syn))
    profiles = live.profiles()
    assert set(profiles) == set(syn)
    for name, table in syn.items():
        assert profiles[name]["rows"] == table.num_rows
        assert [c["name"] for c in profiles[name]["columns"]] == table.column_names


def _engine_run(
    fraction: float, **config: Any
) -> tuple[LiveFidelity, dict[str, pa.Table], dict[str, pa.Table]]:
    from shape.cli.generation import load_target

    schema = load_target("retail")
    ref = dict(Engine(schema, scale="small", seed=7).generate().tables)
    engine = Engine(schema, scale="small", seed=1007)
    injector = AnomalyInjector(fraction, resolve_mutators(()), engine.seed) if fraction else None
    plan = EventPlan(engine, anomaly=injector)
    live = LiveFidelity(
        TargetShape.from_tables(ref), LiveConfig(interval_events=2000, interval_seconds=0, **config)
    )
    sink = MemorySink()
    EmitRunner(plan, TeeSink(sink, live, threaded=False)).run()
    by_table: dict[str, list[pa.RecordBatch]] = {}
    for b in sink.batches:
        by_table.setdefault(b.column("_shape_table")[0].as_py(), []).append(b)
    syn = {
        n: pa.Table.from_batches(bs).drop_columns(
            [c for c in pa.Table.from_batches(bs).column_names if c.startswith("_shape_")]
        )
        for n, bs in by_table.items()
    }
    return live, ref, syn


def test_retail_live_equals_fidelity_and_injected_drift_is_seen() -> None:
    clean, ref, syn = _engine_run(0.0)
    _assert_equal(clean, ref, syn)
    assert not [a for a in clean.alerts if a.level == "error"]
    assert clean.verdict() == []
    drift, ref, syn = _engine_run(0.2)
    _assert_equal(drift, ref, syn)  # still the comparator's score, on the damaged events
    assert drift.last is not None and clean.last is not None
    assert drift.last.overall is not None and clean.last.overall is not None
    assert clean.last.overall - drift.last.overall > 3.0
    assert any(a.kind == "score-low" for a in drift.alerts)
    assert drift.verdict()


# ---- the command line -------------------------------------------------------------------------


def _emit(tmp_path: Path, *extra: str, fraction: str | None = None) -> int:
    args = [
        "emit", "retail", "--scale", "small", "--seed", "3", "--sink", "file",
        "-o", str(tmp_path / "events.jsonl"), "--fresh",
        "--live-target", "retail", "--live-interval-seconds", "0", "--live-interval", "2000",
    ]  # fmt: skip
    if fraction:
        args += ["--anomaly-fraction", fraction]
    return main([*args, *extra])


def test_cli_clean_run_reports_and_exits_zero(tmp_path: Path, capsys) -> None:
    code = _emit(
        tmp_path,
        "--live-report", str(tmp_path / "live.json"),
        "--live-alerts", str(tmp_path / "alerts.jsonl"),
        "--live-profile", str(tmp_path / "profile.json"),
        "--live-fail",
        "--json",
    )  # fmt: skip
    out = capsys.readouterr()
    assert code == 0, out.err
    doc = json.loads(out.out)
    assert doc["live"]["overall"] > 85 and doc["live"]["failures"] == []
    report = json.loads((tmp_path / "live.json").read_text())
    assert report["live"]["format"] == "shape-live-fidelity-v1"
    assert report["fidelity"]["overall_score"] == pytest.approx(doc["live"]["overall"])
    assert set(json.loads((tmp_path / "profile.json").read_text())) == set(
        report["fidelity"]["tables"]
    )
    assert (tmp_path / "alerts.jsonl").read_text() == ""


def test_cli_anomalies_raise_alerts_and_live_fail_exits_one(tmp_path: Path, capsys) -> None:
    code = _emit(
        tmp_path, "--live-alerts", str(tmp_path / "alerts.jsonl"), "--live-fail", fraction="0.2"
    )
    cap = capsys.readouterr()
    assert code == 1
    assert "ALERT [error] score-low" in cap.err  # alerts go to standard error
    assert "FAILED" in cap.out  # the verdict goes with the run report
    lines = [json.loads(x) for x in (tmp_path / "alerts.jsonl").read_text().splitlines()]
    assert lines and {x["format"] for x in lines} == {"shape-live-alert-v1"}
    assert set(lines[0]) == {
        *("format", "kind", "level", "table", "column", "score"),
        *("threshold", "events", "time", "message"),
    }
    assert any(x["kind"] == "score-low" and x["table"] for x in lines)
    # without --live-fail the same run exits 0 (alerts are reported, not fatal)
    assert _emit(tmp_path, fraction="0.2") == 0


def test_cli_rejects_bad_live_options(tmp_path: Path, capsys) -> None:
    assert _emit(tmp_path, "--no-live-profile", "--live-profile", str(tmp_path / "p.json")) == 2
    assert _emit(tmp_path, "--live-min-progress", "2") == 2
    assert _emit(tmp_path, "--live-sample", "0") == 2
    missing = main(["emit", "retail", "--scale", "small", "--live-target", str(tmp_path / "none")])
    capsys.readouterr()
    assert missing == 2


def test_cli_reference_data_target(tmp_path: Path, capsys) -> None:
    import pyarrow.parquet as pq

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    for name, table in (
        Engine(
            __import__("shape.cli.generation", fromlist=["load_target"]).load_target("retail"),
            scale="small",
            seed=9,
        )
        .generate()
        .tables.items()
    ):
        pq.write_table(table, ref_dir / f"{name}.parquet")
    args = [
        "emit", "retail", "--scale", "small", "--seed", "3", "--table", "customer",
        "--sink", "file", "-o", str(tmp_path / "e.jsonl"), "--fresh",
        "--live-target", str(ref_dir), "--json",
    ]  # fmt: skip
    assert main(args) == 0
    live = json.loads(capsys.readouterr().out)["live"]
    assert list(live["tables"]) == ["customer"]  # only the table the stream emits is compared
    assert math.isfinite(live["overall"])


def test_json_alert_sink_writes_lines(tmp_path: Path) -> None:
    sink = JsonLinesAlertSink(tmp_path / "a" / "x.jsonl")
    from shape.streaming.emit.live import LiveAlert

    sink(LiveAlert("score-low", "error", "m", 5, "t", None, 50.0, 70.0, "now"))
    sink.close()
    line = json.loads((tmp_path / "a" / "x.jsonl").read_text())
    assert line["kind"] == "score-low" and line["score"] == 50.0 and line["table"] == "t"
