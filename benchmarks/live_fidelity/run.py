"""P5-03: the live fidelity score against ``shape fidelity`` on the same events.

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/live_fidelity/run.py [--workloads retail:small,...]
                                                              [--no-overhead] [--quick]

For each workload (a domain or a generation schema, at a scale) the harness

1. generates the **reference** (seed ``REF_SEED``) and writes it as Parquet;
2. emits the workload's **events** (seed ``EVENT_SEED``) through the P5-01 runtime with the P5-03
   tee in front of an in-memory sink, so the live side and the capture see the very same Arrow
   batches; while the stream runs, three prefix checkpoints compare the live per-table scores with
   ``compare_tables`` on the events captured so far;
3. writes the captured events (without the ``_shape_*`` fields) as Parquet and runs the product's
   ``shape fidelity REFERENCE EVENTS --format json`` in a subprocess: the **final** live overall and
   per-table scores must be within ``TOLERANCE`` (0.5 points, plan P5-03) of its output;
4. repeats the emit with ``--anomaly-fraction`` injected through ``shape.chaos`` (the **negative
   control**): the live side must raise at least one ``score-low`` or ``score-drop`` alert, the
   live overall must fall by at least ``MIN_DROP`` points against the clean run, and the live
   score must still equal ``shape fidelity`` on the anomalous events; the clean run must raise no
   error alert (no false alarm).

Then it measures the tee's overhead (events/s with and without it, on a file sink and on a null
sink; under the exclusive benchmark lock, each run after the load gate of plan 1.4), and writes
everything to ``$BENCH_OUT_DIR/live_fidelity/report.json`` (``--evidence FILE`` also copies it).
Exit 0 only when every check holds; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "benchmarks" / "vs_refengine"))
from common import bench_lock, wait_for_quiet  # noqa: E402
from paths import BENCH_OUT_DIR, SHAPE_PY  # noqa: E402

TOLERANCE = 0.5  # points on the 0-100 scale (plan P5-03)
REF_SEED = 7
EVENT_SEED = 1007
ANOMALY_FRACTION = 0.2
MIN_DROP = 3.0  # points the anomalies must take off the live overall
CHECKPOINTS = (0.25, 0.55, 0.85)
FIXTURES = ROOT / "benchmarks" / "vs_refengine" / "fixtures" / "schemas"
DEFAULT_WORKLOADS = (
    "retail:small",
    "retail:medium",
    "pulse_3nf:small",
    "telemetry:small",
    "telemetry:medium",
)
QUICK_WORKLOADS = ("retail:small", "pulse_3nf:small", "telemetry:small")
OUT = BENCH_OUT_DIR / "live_fidelity"


def load_schema(name: str, scratch: Path) -> Any:
    """A domain (``retail``), ``telemetry`` (this directory's own schema), or a schema fixture
    imported into the generation schema (only ``pulse`` generates without external reference data
    in this checkout)."""
    from shape.cli.generation import load_target

    if name == "telemetry":
        import telemetry

        return telemetry.schema()
    fixture = FIXTURES / f"{name}.json"
    if not fixture.is_file():
        return load_target(name)
    import schema_import

    schema = schema_import.import_dump(json.loads(fixture.read_text(encoding="utf-8")))
    return schema


def generate_tables(schema: Any, scale: str, seed: int) -> dict[str, pa.Table]:
    from shape.generation.engine import Engine

    return dict(Engine(schema, scale=scale, seed=seed).generate().tables)


def write_tables(tables: dict[str, pa.Table], where: Path) -> None:
    where.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        pq.write_table(table, where / f"{name}.parquet")


def data_columns(table: pa.Table) -> pa.Table:
    return table.drop_columns([c for c in table.column_names if c.startswith("_shape_")])


def captured_tables(batches: list[pa.RecordBatch]) -> dict[str, pa.Table]:
    by_table: dict[str, list[pa.RecordBatch]] = {}
    for b in batches:
        by_table.setdefault(b.column("_shape_table")[0].as_py(), []).append(b)
    return {n: data_columns(pa.Table.from_batches(bs)) for n, bs in by_table.items()}


class Capture:
    """The sink behind the tee: keeps every batch it was given."""

    def __init__(self) -> None:
        self.batches: list[pa.RecordBatch] = []

    def send(self, batch: pa.RecordBatch) -> None:
        self.batches.append(batch)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class Checkpoints:
    """Wraps the tee: at fixed shares of the stream, compares the live per-table scores with the
    comparator's on the events captured so far."""

    def __init__(
        self, tee: Any, live: Any, capture: Capture, reference: dict[str, pa.Table], total: int
    ) -> None:
        self.tee, self.live, self.capture, self.reference = tee, live, capture, reference
        self.marks = [int(total * f) for f in CHECKPOINTS]
        self.events = 0
        self.results: list[dict[str, Any]] = []

    def send(self, batch: pa.RecordBatch) -> None:
        self.tee.send(batch)
        self.events += batch.num_rows
        while self.marks and self.events >= self.marks[0]:
            self.marks.pop(0)
            self.results.append(self._compare())

    def _compare(self) -> dict[str, Any]:
        from shape.generation.report import compare_tables

        snap = self.live.snapshot()
        synthetic = captured_tables(self.capture.batches)
        started = {n: t for n, t in self.reference.items() if n in synthetic}
        offline = compare_tables(started, synthetic)
        diffs = {n: abs(snap.tables[n] - offline.tables[n].score) for n in started}
        return {
            "events": self.events,
            "tables": len(started),
            "max_table_diff": max(diffs.values()) if diffs else 0.0,
            "worst_table": max(diffs, key=lambda n: diffs[n]) if diffs else None,
        }

    def flush(self) -> None:
        self.tee.flush()

    def close(self) -> None:
        self.tee.close()


def shape_fidelity(reference: Path, events: Path) -> dict[str, Any]:
    """``shape fidelity`` (the product CLI) on two directories; its JSON report."""
    cp = subprocess.run(
        [
            str(SHAPE_PY.parent / "shape"),
            "fidelity",
            str(reference),
            str(events),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if cp.returncode not in (0, 1):
        raise RuntimeError(f"shape fidelity exited {cp.returncode}: {cp.stderr[-400:]}")
    return json.loads(cp.stdout)


def emit(
    schema: Any,
    scale: str,
    reference: dict[str, pa.Table],
    *,
    anomaly: float = 0.0,
    checkpoints: bool = True,
) -> dict[str, Any]:
    from shape.generation.engine import Engine
    from shape.streaming.emit import (
        AnomalyInjector,
        EmitConfig,
        EmitRunner,
        EventPlan,
        resolve_mutators,
    )
    from shape.streaming.emit.live import LiveConfig, LiveFidelity, TargetShape, TeeSink

    engine = Engine(schema, scale=scale, seed=EVENT_SEED)
    injector = AnomalyInjector(anomaly, resolve_mutators(()), engine.seed) if anomaly else None
    plan = EventPlan(engine, anomaly=injector)
    live = LiveFidelity(
        TargetShape.from_tables(reference),
        LiveConfig(interval_events=20_000, interval_seconds=0.0),
        tables=plan.tables,
    )
    capture = Capture()
    tee = TeeSink(capture, live, threaded=False)
    sink: Any = (
        Checkpoints(tee, live, capture, reference, plan.total_events) if checkpoints else tee
    )
    report = EmitRunner(plan, sink, EmitConfig()).run()
    return {
        "live": live,
        "capture": capture,
        "events": report.events,
        "checkpoints": sink.results if checkpoints else [],
        "anomalies_selected": report.anomalies_selected,
    }


def check_workload(name: str, scale: str, work: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"workload": f"{name}:{scale}", "failures": []}
    fail: list[str] = out["failures"]
    schema = load_schema(name, work)
    reference = generate_tables(schema, scale, REF_SEED)
    ref_dir = work / "reference"
    write_tables(reference, ref_dir)

    for label, anomaly in (("clean", 0.0), ("anomaly", ANOMALY_FRACTION)):
        run = emit(schema, scale, reference, anomaly=anomaly, checkpoints=anomaly == 0.0)
        live = run["live"]
        summary = live.summary()
        events_dir = work / f"events_{label}"
        write_tables(captured_tables(run["capture"].batches), events_dir)
        offline = shape_fidelity(ref_dir, events_dir)
        final = live.final()
        overall_diff = abs(final.overall - offline["overall_score"])
        table_diffs = {
            t: abs(final.tables[t] - offline["tables"][t]["score"]) for t in offline["tables"]
        }
        worst = max(table_diffs, key=lambda t: table_diffs[t])
        res = {
            "events": run["events"],
            "live_overall": final.overall,
            "fidelity_overall": offline["overall_score"],
            "overall_diff": overall_diff,
            "max_table_diff": table_diffs[worst],
            "worst_table": worst,
            "tables": {
                t: {"live": final.tables[t], "fidelity": offline["tables"][t]["score"]}
                for t in sorted(table_diffs)
            },
            "alerts": [a for a in summary["alerts"] if a["kind"] != "recovered"],
            "approximate": summary["approximate"],
            "checkpoints": run["checkpoints"],
            "anomalies_selected": run["anomalies_selected"],
            "profile_tables": len(live.profiles()),
        }
        out[label] = res
        if overall_diff > TOLERANCE:
            fail.append(
                f"{label}: overall live {final.overall:.3f} vs fidelity "
                f"{offline['overall_score']:.3f} differ by {overall_diff:.3f} > {TOLERANCE}"
            )
        if table_diffs[worst] > TOLERANCE:
            fail.append(f"{label}: table {worst} differs by {table_diffs[worst]:.3f} > {TOLERANCE}")
        for cp in run["checkpoints"]:
            if cp["max_table_diff"] > TOLERANCE:
                fail.append(
                    f"{label}: checkpoint at {cp['events']} events: table {cp['worst_table']} "
                    f"differs by {cp['max_table_diff']:.3f} > {TOLERANCE}"
                )
        if res["profile_tables"] != len(reference):
            fail.append(f"{label}: the stream profiler saw {res['profile_tables']} tables")

    clean, anomaly = out["clean"], out["anomaly"]
    errors = [a for a in clean["alerts"] if a["level"] == "error"]
    if errors:
        fail.append(f"clean run raised {len(errors)} error alert(s): {errors[0]['message']}")
    flagged = [a for a in anomaly["alerts"] if a["kind"] in ("score-low", "score-drop")]
    out["control"] = {
        "fraction": ANOMALY_FRACTION,
        "alerts": len(flagged),
        "overall_drop": clean["live_overall"] - anomaly["live_overall"],
    }
    if not flagged:
        fail.append("negative control: no score-low or score-drop alert on the anomalous stream")
    if out["control"]["overall_drop"] < MIN_DROP:
        fail.append(
            f"negative control: the live overall fell {out['control']['overall_drop']:.2f} "
            f"< {MIN_DROP} points"
        )
    if anomaly["anomalies_selected"] == 0:
        fail.append("negative control: no event was selected for mutation")
    return out


# ---- overhead ---------------------------------------------------------------------------------


class NullSink:
    def send(self, batch: pa.RecordBatch) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def rate(schema: Any, scale: str, reference: Any, mode: str, sink_kind: str, work: Path) -> float:
    from shape.generation.engine import Engine
    from shape.streaming.emit import EmitConfig, EmitRunner, EventPlan, FileSink
    from shape.streaming.emit.live import LiveConfig, LiveFidelity, TargetShape, TeeSink

    plan = EventPlan(Engine(schema, scale=scale, seed=EVENT_SEED))
    inner: Any = NullSink() if sink_kind == "null" else FileSink(work / "overhead.jsonl")
    sink = inner
    if mode != "none":
        live = LiveFidelity(
            TargetShape.from_tables(reference),
            LiveConfig(profile=mode == "tee+profile"),
            tables=plan.tables,
        )
        sink = TeeSink(inner, live)
    t0 = time.perf_counter()
    report = EmitRunner(plan, sink, EmitConfig()).run()
    return report.events / (time.perf_counter() - t0)


def overhead(name: str, scale: str, work: Path, repeats: int) -> dict[str, Any]:
    schema = load_schema(name, work)
    reference = generate_tables(schema, scale, REF_SEED)
    out: dict[str, Any] = {"workload": f"{name}:{scale}", "repeats": repeats}
    for sink_kind in ("file", "null"):
        runs: dict[str, list[float]] = {"none": [], "tee": [], "tee+profile": []}
        with bench_lock():  # a timed benchmark (plan 1.4)
            for _ in range(repeats):  # interleaved, so a slow moment hits every mode
                for mode in runs:
                    wait_for_quiet()
                    runs[mode].append(rate(schema, scale, reference, mode, sink_kind, work))
        med = {m: statistics.median(v) for m, v in runs.items()}
        out[sink_kind] = {
            "events_per_second": med,
            "all_runs": runs,
            "overhead_percent": {
                m: 100.0 * (1.0 - med[m] / med["none"]) for m in ("tee", "tee+profile")
            },
        }
    return out


def realtime(name: str, scale: str, work: Path, rate_per_s: int, seconds: float) -> dict[str, Any]:
    """The tee at a paced rate: does the rate hold, and what does the tee cost in CPU?"""
    from shape.generation.engine import Engine
    from shape.streaming.emit import EmitConfig, EmitRunner, EventPlan, FileSink
    from shape.streaming.emit.live import LiveConfig, LiveFidelity, TargetShape, TeeSink

    schema = load_schema(name, work)
    reference = generate_tables(schema, scale, REF_SEED)
    out: dict[str, Any] = {"workload": f"{name}:{scale}", "target_rate": rate_per_s}
    for mode in ("none", "tee", "tee+profile"):
        plan = EventPlan(Engine(schema, scale=scale, seed=EVENT_SEED))
        sink: Any = FileSink(work / "realtime.jsonl")
        live = None
        if mode != "none":
            live = LiveFidelity(
                TargetShape.from_tables(reference),
                LiveConfig(profile=mode == "tee+profile"),
                tables=plan.tables,
            )
            sink = TeeSink(sink, live)
        cfg = EmitConfig(realtime=True, rate=float(rate_per_s), duration=seconds)
        with bench_lock():  # a timed benchmark (plan 1.4)
            wait_for_quiet()
            cpu0, wall0 = time.process_time(), time.perf_counter()
            report = EmitRunner(plan, sink, cfg).run()
            cpu, wall = time.process_time() - cpu0, time.perf_counter() - wall0
        full = report.per_second[1:-1]  # whole seconds only
        out[mode] = {
            "events": report.events,
            "rate": report.rate,
            "rate_error_percent": 100.0 * (report.rate / rate_per_s - 1.0),
            "worst_second_error_percent": max(
                (100.0 * abs(v / rate_per_s - 1.0) for v in full), default=0.0
            ),
            "max_lag_seconds": report.max_lag,
            "cpu_seconds": cpu,
            "cpu_percent_of_one_core": 100.0 * cpu / wall,
            "live_events": None if live is None else live.events,
            "live_score": None if live is None or live.last is None else live.last.overall,
        }
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--workloads",
        help="comma-separated NAME:SCALE (default: retail, pulse and telemetry, see the source)",
    )
    ap.add_argument(
        "--quick", action="store_true", help="small workloads only, one overhead repeat"
    )
    ap.add_argument("--no-overhead", action="store_true")
    ap.add_argument("--overhead-workload", default="retail:medium")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--realtime-rate", type=int, default=10_000, help="events/s of the paced run")
    ap.add_argument("--realtime-seconds", type=float, default=20.0)
    ap.add_argument("--evidence", metavar="FILE", help="also write the report here")
    a = ap.parse_args(argv)
    names = (
        a.workloads.split(",") if a.workloads else QUICK_WORKLOADS if a.quick else DEFAULT_WORKLOADS
    )
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "tolerance_points": TOLERANCE,
        "reference_seed": REF_SEED,
        "event_seed": EVENT_SEED,
        "anomaly_fraction": ANOMALY_FRACTION,
        "workloads": [],
        "failures": [],
    }
    with tempfile.TemporaryDirectory(dir=OUT) as tmp:
        for spec in names:
            name, _, scale = spec.partition(":")
            res = check_workload(name, scale or "small", Path(tmp) / spec.replace(":", "_"))
            report["workloads"].append(res)
            report["failures"].extend(f"{spec}: {f}" for f in res["failures"])
            c = res["clean"]
            print(
                f"{spec:24s} events {c['events']:>9,}  live {c['live_overall']:.3f}  "
                f"fidelity {c['fidelity_overall']:.3f}  max table diff {c['max_table_diff']:.4f}  "
                f"control: {res['control']['alerts']} alerts, "
                f"drop {res['control']['overall_drop']:.1f}"
            )
        if not a.no_overhead:
            name, _, scale = a.overhead_workload.partition(":")
            work = Path(tmp) / "overhead"
            work.mkdir()
            report["overhead"] = overhead(name, scale, work, 1 if a.quick else a.repeats)
            for sink_kind in ("file", "null"):
                o = report["overhead"][sink_kind]
                print(
                    f"overhead ({sink_kind} sink): "
                    + ", ".join(f"{m} {v:,.0f}/s" for m, v in o["events_per_second"].items())
                    + "; "
                    + ", ".join(f"{m} {v:.1f}%" for m, v in o["overhead_percent"].items())
                )
        if not a.no_overhead:
            work = Path(tmp) / "realtime"
            work.mkdir()
            name, _, scale = a.overhead_workload.partition(":")
            report["realtime"] = rt = realtime(
                name, scale, work, a.realtime_rate, 2.0 if a.quick else a.realtime_seconds
            )
            for mode in ("none", "tee", "tee+profile"):
                r = rt[mode]
                print(
                    f"realtime {rt['target_rate']:,}/s {mode:12s} rate {r['rate']:,.0f} "
                    f"({r['rate_error_percent']:+.2f}%), worst second "
                    f"{r['worst_second_error_percent']:.1f}%, "
                    f"lag {r['max_lag_seconds'] * 1000:.1f} ms, "
                    f"cpu {r['cpu_percent_of_one_core']:.0f}% of a core"
                )
                if abs(r["rate_error_percent"]) > 5.0:
                    report["failures"].append(f"realtime {mode}: rate off by more than 5%")
    report["passed"] = not report["failures"]
    text = json.dumps(report, indent=2, default=str)
    (OUT / "report.json").write_text(text + "\n", encoding="utf-8")
    if a.evidence:
        Path(a.evidence).parent.mkdir(parents=True, exist_ok=True)
        Path(a.evidence).write_text(text + "\n", encoding="utf-8")
    for f in report["failures"]:
        print("FAIL:", f, file=sys.stderr)
    print("PASS" if report["passed"] else "FAIL")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
