"""P3-01: the stream runtime (windows, watermarks, late data, snapshot/restore)."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from shape.profile.engine import EngineOptions, profile_table
from shape.streaming.runtime import (
    GlobalProfiler,
    SessionProfiler,
    SlidingProfiler,
    TumblingProfiler,
    WindowedProfiler,
    WindowProfile,
    event_times,
    restore_profiler,
)

SEC = 1_000_000
SCHEMA = pa.schema(
    [
        ("_shape_event_time", pa.timestamp("us")),
        ("id", pa.int64()),
        ("amount", pa.float64()),
        ("cat", pa.string()),
    ]
)


def _batch(times_s, start_id: int = 0, seed: int = 0) -> pa.RecordBatch:
    """A micro-batch whose event times are ``times_s`` seconds."""
    n = len(times_s)
    rng = np.random.default_rng(seed + start_id)
    return pa.record_batch(
        [
            pa.array((np.asarray(times_s, dtype=np.int64) * SEC), type=pa.timestamp("us")),
            pa.array(np.arange(start_id, start_id + n, dtype=np.int64)),
            pa.array(np.round(rng.normal(100, 15, n), 3)),
            pa.array(rng.choice(["a", "b", "c", "dd"], n)),
        ],
        schema=SCHEMA,
    )


def _stream(n_batches: int, rows: int, seed: int, disorder: int = 0):
    """``n_batches`` micro-batches of mostly increasing event times, shuffled by ``disorder`` s."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_batches):
        base = i * rows // 2
        times = base + rng.integers(0, rows, rows)
        if disorder:
            times = np.maximum(times - rng.integers(0, disorder, rows), 0)
        out.append(_batch(times, start_id=i * rows, seed=seed))
    return out


def _approx(a, b, path="$"):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            assert a is b, path
        elif math.isnan(a) or math.isnan(b):
            assert math.isnan(a) and math.isnan(b), path
        else:
            assert a == pytest.approx(b, rel=1e-9, abs=1e-9), (path, a, b)
    elif isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            _approx(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _approx(x, y, f"{path}[{i}]")
    else:
        assert a == b, (path, a, b)


def _run(profiler: WindowedProfiler, batches) -> list[WindowProfile]:
    return list(profiler.run(batches))


def _documents(windows: list[WindowProfile]) -> list[dict]:
    return [w.to_dict() for w in windows]


LOOSE = timedelta(seconds=500)  # more than the disorder `_stream` produces: nothing is late
ROOT = Path(__file__).resolve().parents[2]


def _d2_table(n: int) -> pa.Table:
    """Dataset D2 (the T-22 mixed table) at ``n`` rows, from the benchmark generator."""
    path = ROOT / "benchmarks" / "vs_spindle" / "profile_1to1" / "datasets.py"
    spec = importlib.util.spec_from_file_location("vs_spindle_datasets", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vs_spindle_datasets"] = mod
    spec.loader.exec_module(mod)
    table: pa.Table = mod._d2_table(n)
    return table


# ------------------------------------------------------------------ tumbling


def test_tumbling_windows_close_when_the_watermark_passes_their_end():
    p = TumblingProfiler(SCHEMA, timedelta(seconds=10))
    assert p.watermark is None
    assert p.process(_batch([1, 3, 9])) == []  # watermark 9 s: window [0, 10) still open
    closed = p.process(_batch([12, 14]))  # watermark 14 s closes [0, 10)
    assert [(w.start, w.end, w.rows) for w in closed] == [(0, 10 * SEC, 3)]
    assert closed[0].kind == "tumbling" and closed[0].profile["rows"] == 3
    rest = p.finish()
    assert [(w.start, w.end, w.rows) for w in rest] == [(10 * SEC, 20 * SEC, 2)]
    assert p.finished and p.windows_emitted == 2 and p.rows_in == 5
    with pytest.raises(RuntimeError, match="finished"):
        p.process(_batch([1]))


def test_each_window_profile_is_the_bounded_batch_profile_of_its_rows():
    batches = _stream(8, 400, seed=3)
    p = TumblingProfiler(SCHEMA, timedelta(seconds=60), allowed_lateness=LOOSE)
    windows = _run(p, batches)
    assert p.late_events == 0
    table = pa.Table.from_batches(batches)
    ts = table["_shape_event_time"].cast(pa.int64()).to_numpy()
    assert sum(w.rows for w in windows) == table.num_rows
    for w in windows:
        rows = np.flatnonzero((ts >= w.start) & (ts < w.end))
        want = profile_table(table.take(pa.array(rows)), "stream", EngineOptions(mode="bounded"))
        assert w.rows == want["rows"] == len(rows)
        _approx(w.profile, want)


def test_offset_shifts_the_window_grid():
    p = TumblingProfiler(SCHEMA, timedelta(seconds=10), offset=timedelta(seconds=5))
    out = _run(p, [_batch([6, 14, 15, 20])])
    assert [(w.start // SEC, w.end // SEC, w.rows) for w in out] == [(5, 15, 2), (15, 25, 2)]


def test_dates_and_zoned_timestamps_are_event_times():
    us, valid = event_times(pa.array([1, 2], type=pa.date32()))
    assert us.tolist() == [86_400 * SEC, 2 * 86_400 * SEC] and valid.all()
    us, _ = event_times(pa.array([5], type=pa.timestamp("ms", "UTC")))
    assert us.tolist() == [5000]
    us, valid = event_times(pa.array([None, 7], type=pa.timestamp("ns")))
    assert valid.tolist() == [False, True] and us[1] == 0  # 7 ns truncates to 0 µs
    with pytest.raises(TypeError):
        event_times(pa.array([1]))


# ---------------------------------------------------------------------- late


def test_late_rows_are_counted_dropped_and_sent_to_the_sink():
    seen: list[pa.RecordBatch] = []
    p = TumblingProfiler(SCHEMA, timedelta(seconds=10), late_sink=seen.append)
    p.process(_batch([1, 2]))
    assert [w.rows for w in p.process(_batch([25]))] == [2]  # window [0, 10) closed
    p.process(_batch([3, 4, 26], start_id=100))  # 3 and 4 belong to the closed window
    assert p.late_events == 2
    assert len(seen) == 1 and seen[0].column("id").to_pylist() == [100, 101]
    tail = p.finish()
    assert [(w.start // SEC, w.rows) for w in tail] == [(20, 2)]  # the late rows are in no window


def test_allowed_lateness_keeps_windows_open_for_disordered_rows():
    strict = TumblingProfiler(SCHEMA, timedelta(seconds=10))
    lenient = TumblingProfiler(
        SCHEMA, timedelta(seconds=10), allowed_lateness=timedelta(seconds=15)
    )
    stream = [_batch([2, 8]), _batch([22]), _batch([5, 9], start_id=10), _batch([40])]
    got_strict = _run(strict, stream)
    got_lenient = _run(lenient, stream)
    assert strict.late_events == 2 and lenient.late_events == 0
    first = lambda ws: next(w for w in ws if w.start == 0)  # noqa: E731
    assert first(got_strict).rows == 2 and first(got_lenient).rows == 4
    assert lenient.watermark == 40 * SEC - 15 * SEC


def test_the_watermark_moves_at_batch_boundaries():
    # in one micro-batch nothing is late, whatever the order inside it
    p = TumblingProfiler(SCHEMA, timedelta(seconds=10))
    out = _run(p, [_batch([30, 1, 2, 31])])
    assert p.late_events == 0 and sum(w.rows for w in out) == 4


def test_null_event_times_are_counted_and_skipped():
    p = TumblingProfiler(SCHEMA, timedelta(seconds=10))
    times = pa.array([1 * SEC, None, 2 * SEC], type=pa.timestamp("us"))
    b = _batch([0, 0, 0]).set_column(0, SCHEMA.field(0), times)
    out = _run(p, [b])
    assert p.null_event_time == 1 and [w.rows for w in out] == [2]


# ------------------------------------------------------------------- sliding


def test_sliding_windows_overlap_and_match_the_batch_profile_of_their_rows():
    batches = _stream(6, 300, seed=5)
    p = SlidingProfiler(
        SCHEMA, timedelta(seconds=60), timedelta(seconds=20), allowed_lateness=LOOSE
    )
    windows = _run(p, batches)
    table = pa.Table.from_batches(batches)
    ts = table["_shape_event_time"].cast(pa.int64()).to_numpy()
    assert p.late_events == 0
    starts = [w.start for w in windows]
    assert starts == sorted(starts) and all(w.end - w.start == 60 * SEC for w in windows)
    assert all(b - a == 20 * SEC for a, b in zip(starts, starts[1:], strict=False))
    for w in windows:
        rows = np.flatnonzero((ts >= w.start) & (ts < w.end))
        want = profile_table(table.take(pa.array(rows)), "stream", EngineOptions(mode="bounded"))
        assert w.rows == want["rows"] == len(rows)
        got = {c["name"]: c for c in w.profile["columns"]}
        for c in want["columns"]:
            g = got[c["name"]]
            assert g["count"] == c["count"] and g["null_count"] == c["null_count"]
            if c["kind"] in ("int", "float"):
                assert g["mean"] == pytest.approx(c["mean"], rel=1e-9)
                assert g["min"] == c["min"] and g["max"] == c["max"]
            # sketches merge within their bounds: a few hundred distinct values are exact enough
            assert g["distinct"] == pytest.approx(c["distinct"], rel=0.05)


def test_a_sliding_window_of_one_pane_is_not_merged():
    p = SlidingProfiler(SCHEMA, timedelta(seconds=10), timedelta(seconds=10))
    q = TumblingProfiler(SCHEMA, timedelta(seconds=10))
    stream = _stream(4, 100, seed=2)
    assert _documents(_run(p, stream))[0]["profile"] == _documents(_run(q, stream))[0]["profile"]


def test_panes_are_released_once_their_last_window_closed():
    p = SlidingProfiler(SCHEMA, timedelta(seconds=60), timedelta(seconds=20))
    for i in range(200):
        p.process(_batch([i * 7, i * 7 + 3], start_id=2 * i))
    assert len(p._panes) <= 60 // 20 + 2  # open windows hold panes; nothing else


def test_sliding_geometry_is_validated():
    with pytest.raises(ValueError, match="slide must not exceed size"):
        SlidingProfiler(SCHEMA, timedelta(seconds=10), timedelta(seconds=20))
    with pytest.raises(ValueError, match="positive"):
        TumblingProfiler(SCHEMA, timedelta(0))
    with pytest.raises(ValueError, match="zero or more"):
        TumblingProfiler(SCHEMA, timedelta(seconds=1), allowed_lateness=timedelta(seconds=-1))
    with pytest.raises(ValueError, match="no event-time column"):
        TumblingProfiler(SCHEMA, timedelta(seconds=1), event_time="nope")
    with pytest.raises(TypeError, match="timestamp or a date"):
        TumblingProfiler(SCHEMA, timedelta(seconds=1), event_time="id")


# ------------------------------------------------------------------ sessions


def test_sessions_split_on_gaps_and_close_after_the_gap():
    p = SessionProfiler(SCHEMA, timedelta(seconds=10))
    out = p.process(_batch([1, 4, 8, 30]))  # {1,4,8} ends at 18; 30 opens a second session
    assert [(w.start // SEC, w.end // SEC, w.rows) for w in out] == [(1, 18, 3)]
    tail = p.finish()
    assert [(w.start // SEC, w.end // SEC, w.rows) for w in tail] == [(30, 40, 1)]


def test_an_event_that_bridges_two_sessions_merges_them():
    p = SessionProfiler(SCHEMA, timedelta(seconds=10), allowed_lateness=timedelta(seconds=30))
    assert p.process(_batch([0, 1])) == []
    assert p.process(_batch([15, 16], start_id=10)) == []  # a second session [15, 26)
    assert p.process(_batch([8], start_id=20)) == []  # 8 is within 10 s of 1 and of 15
    out = p.finish()
    assert [(w.start // SEC, w.end // SEC, w.rows) for w in out] == [(0, 26, 5)]
    ids = {c["name"]: c for c in out[0].profile["columns"]}["id"]
    assert ids["min"] == 0 and ids["max"] == 20 and ids["count"] == 5


def test_session_late_rows_inside_an_open_session_are_accepted():
    p = SessionProfiler(SCHEMA, timedelta(seconds=10), allowed_lateness=timedelta(seconds=2))
    p.process(_batch([0, 20]))  # watermark 18 closes [0, 10); [20, 30) is open
    p.process(_batch([5], start_id=5))  # 5 + 10 <= 18 and no open session holds it: late
    p.process(_batch([14], start_id=6))  # 14 + 10 > 18: kept (it overlaps the open session)
    assert p.late_events == 1
    out = p.finish()
    assert [(w.start // SEC, w.rows) for w in out] == [(14, 2)]


# ------------------------------------------------------------------- global


def test_global_replay_equals_bounded_batch_profiling():
    batches = _stream(10, 500, seed=9)
    table = pa.Table.from_batches(batches)
    [w] = _run(GlobalProfiler(SCHEMA, name="t"), batches)
    assert (w.start, w.end, w.kind) == (None, None, "global")
    _approx(w.profile, profile_table(table, "t", EngineOptions(mode="bounded")))


def test_replay_of_d2_in_bounded_mode_equals_batch_bounded_profiling():
    """Gate G3: a bounded-mode stream replay of D2 equals batch bounded profiling of D2, within
    the T-14 bounds. The two use the same sketches in the same order, so they agree exactly
    (floating-point sums aside) - well inside the bounds."""
    table = _d2_table(60_000)
    schema = table.schema
    # replay in micro-batches of uneven size
    sizes = [1000, 4096, 777, 25_000]
    p = GlobalProfiler(schema, name="d2")
    pos = 0
    i = 0
    while pos < table.num_rows:
        step = sizes[i % len(sizes)]
        p.process(table.slice(pos, step).combine_chunks().to_batches()[0])
        pos += step
        i += 1
    [w] = p.finish()
    batch = profile_table(table, "d2", EngineOptions(mode="bounded"))
    assert w.rows == batch["rows"] == table.num_rows
    got = {c["name"]: c for c in w.profile["columns"]}
    for want in batch["columns"]:
        g = got[want["name"]]
        assert g["error_models"] == want["error_models"]
        assert g["count"] == want["count"] and g["null_count"] == want["null_count"]
        if "distinct" in want:
            assert g["distinct"] == pytest.approx(want["distinct"], rel=1e-12)
            assert g["top"] == want["top"]
        if "quantiles" in want:
            assert g["quantiles"] == want["quantiles"]
        _approx(g, want, want["name"])


# ----------------------------------------------------------- snapshot/restore

KINDS = {
    "tumbling": lambda **kw: TumblingProfiler(SCHEMA, timedelta(seconds=45), **kw),
    "sliding": lambda **kw: SlidingProfiler(
        SCHEMA, timedelta(seconds=60), timedelta(seconds=20), **kw
    ),
    "session": lambda **kw: SessionProfiler(SCHEMA, timedelta(seconds=8), **kw),
    "global": lambda **kw: GlobalProfiler(SCHEMA),
}


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_killing_and_restoring_mid_stream_gives_the_uninterrupted_output(kind):
    make = KINDS[kind]
    kwargs = {} if kind == "global" else {"allowed_lateness": timedelta(seconds=15)}
    batches = _stream(12, 250, seed=17, disorder=40)
    whole = make(**kwargs)
    want = _documents(_run(whole, batches))
    assert want  # the stream does close windows
    for cut in range(0, len(batches) + 1):
        first = make(**kwargs)
        out = []
        for b in batches[:cut]:
            out += first.process(b)
        snap = json.loads(json.dumps(first.snapshot()))  # through JSON, as a checkpoint file
        del first  # killed
        revived = restore_profiler(snap)
        for b in batches[cut:]:
            out += revived.process(b)
        out += revived.finish()
        assert _documents(out) == want, f"{kind}: restored after batch {cut}"
        assert revived.late_events == whole.late_events
        assert revived.rows_in == whole.rows_in and revived.windows_emitted == whole.windows_emitted
        assert revived.watermark == whole.watermark


def test_restore_keeps_the_late_sink_the_caller_gives_it():
    p = TumblingProfiler(SCHEMA, timedelta(seconds=10))
    p.process(_batch([1, 30]))
    seen: list[pa.RecordBatch] = []
    q = TumblingProfiler.restore(p.snapshot(), late_sink=seen.append)
    q.process(_batch([2], start_id=9))
    assert len(seen) == 1 and q.late_events == 1


def test_snapshots_are_json_safe_and_typed():
    p = SlidingProfiler(SCHEMA, timedelta(seconds=60), timedelta(seconds=20))
    p.process(_batch([1, 25, 70]))
    snap = p.snapshot()
    assert snap["format"] == "shape-stream-window-v1" and snap["kind"] == "sliding"
    assert json.loads(json.dumps(snap)) == snap
    with pytest.raises(ValueError, match="cannot restore"):
        TumblingProfiler.restore(snap)  # a sliding snapshot is not a tumbling profiler
    with pytest.raises(ValueError, match="not a stream window snapshot"):
        restore_profiler({"format": "other"})


def test_a_snapshot_taken_after_finish_restores_as_finished():
    p = TumblingProfiler(SCHEMA, timedelta(seconds=10))
    p.process(_batch([1]))
    p.finish()
    q = restore_profiler(p.snapshot())
    assert q.finished and q.finish() == []
    with pytest.raises(RuntimeError):
        q.process(_batch([1]))


def test_schema_mismatches_are_rejected():
    p = TumblingProfiler(SCHEMA, timedelta(seconds=10))
    other = pa.record_batch([pa.array([1])], names=["x"])
    with pytest.raises(ValueError, match="schema differs"):
        p.process(other)


# ----------------------------------------------------------- determinism

_SCRIPT = """
import hashlib, json, sys
from datetime import timedelta
sys.path.insert(0, sys.argv[1])
from test_runtime_child import run
print(hashlib.sha256(json.dumps(run(), sort_keys=True).encode()).hexdigest())
"""


@pytest.mark.parametrize("kernel", ["python", "rust"])
def test_results_are_identical_across_processes(tmp_path, kernel):
    """Same stream, different ``PYTHONHASHSEED`` and process: byte-identical output."""
    from shape.kernel import dispatch

    if kernel == "rust":
        dispatch._import_native()  # the build under test must have the native kernel
    child = tmp_path / "test_runtime_child.py"
    child.write_text(
        "import json\n"
        "from datetime import timedelta\n"
        "import numpy as np, pyarrow as pa\n"
        "from shape.streaming.runtime import SessionProfiler, SlidingProfiler\n"
        "SC = pa.schema([('_shape_event_time', pa.timestamp('us')), ('id', pa.int64()),\n"
        "                ('cat', pa.string())])\n"
        "def run():\n"
        "    rng = np.random.default_rng(4)\n"
        "    out = []\n"
        "    ps = [SlidingProfiler(SC, timedelta(seconds=60), timedelta(seconds=30)),\n"
        "          SessionProfiler(SC, timedelta(seconds=5))]\n"
        "    for i in range(10):\n"
        "        t = (i * 20 + rng.integers(0, 40, 300)) * 1_000_000\n"
        "        b = pa.record_batch([pa.array(t, pa.timestamp('us')),\n"
        "            pa.array(rng.integers(0, 50, 300)),\n"
        "            pa.array(rng.choice(['x', 'y', 'zz'], 300))], schema=SC)\n"
        "        for p in ps:\n"
        "            out += [w.to_dict() for w in p.process(b)]\n"
        "    for p in ps:\n"
        "        out += [w.to_dict() for w in p.finish()]\n"
        "    return out\n"
    )
    digests = set()
    for seed in ("0", "1", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "SHAPE_KERNEL": kernel}
        r = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(tmp_path)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        digests.add(r.stdout.strip())
    assert len(digests) == 1
