"""ISS-stream (#33): ``shape stream-profile`` reads files and standard input, with no broker.

Everything the command does with a broker (windows, lateness, event time, checkpoints) is
checked here on JSON lines, CSV, Parquet, CloudEvents, a folder, a glob and stdin.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.plugins.api.v1 import StreamOffset
from shape.streaming.file_source import FileStreamSource, is_file_uri
from shape.streaming.messages import StreamSourceError

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def events(n: int = 360, step: float = 1.0):
    return [
        {
            "id": i,
            "amount": round(i * 0.5, 1),
            "kind": "abc"[i % 3],
            "_shape_event_time": at(i * step),
        }
        for i in range(n)
    ]


def write_jsonl(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def run(capsys):
    def go(*argv):
        code = main(["stream-profile", *map(str, argv)])
        out, err = capsys.readouterr()
        return code, out, err

    return go


def summary(out: str) -> dict:
    return json.loads(out.strip().splitlines()[-1])


def windows_of(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ------------------------------------------------------------------ the issue


def test_a_file_is_a_stream_source_by_path_and_by_file_uri(run, tmp_path):
    src = write_jsonl(tmp_path / "events.jsonl", events(100))
    for uri in (str(src), src.as_uri(), f"file://{src}"):
        out = tmp_path / "g.json"
        code, stdout, err = run(uri, "-o", out)
        assert code == 0, err
        assert summary(stdout)["events"] == 100
        assert json.loads(out.read_text())["tables"]["stream"]["rows"] == 100


def test_standard_input_is_a_stream_source(run, tmp_path, monkeypatch):
    data = "".join(json.dumps(r) + "\n" for r in events(50))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data.encode())))
    out = tmp_path / "g.json"
    code, stdout, err = run("-", "-o", out)
    assert code == 0, err
    assert summary(stdout)["events"] == 50


def test_a_file_run_gives_the_profile_a_broker_run_gives(run, tmp_path):
    rows = events(400)
    src = write_jsonl(tmp_path / "e.jsonl", rows)
    out = tmp_path / "g.json"
    assert run(src, "-o", out, "--batch-size", 70)[0] == 0
    got = json.loads(out.read_text())["tables"]["stream"]
    import pyarrow.compute as pc

    from shape.profile.engine import EngineOptions, profile_table
    from shape.streaming.messages import StreamMessage, decode_messages

    batch = decode_messages([StreamMessage("0", i, json.dumps(r)) for i, r in enumerate(rows)])
    want = profile_table(pa.Table.from_batches([batch]), "stream", EngineOptions(mode="bounded"))
    assert got["rows"] == want["rows"] == 400
    for g, w in zip(got["columns"], want["columns"], strict=True):
        assert g["name"] == w["name"] and g["count"] == w["count"]
    assert pc.sum(batch.column("id")).as_py() == sum(range(400))


def test_tumbling_windows_over_a_file(run, tmp_path):
    src = write_jsonl(tmp_path / "e.jsonl", events(360))
    out = tmp_path / "w.jsonl"
    code, stdout, err = run(src, "--window", "tumbling", "--size", "60s", "--windows", out)
    assert code == 0, err
    wins = windows_of(out)
    assert [w["rows"] for w in wins] == [60] * 6
    assert wins[0]["start"] == at(0) and wins[0]["end"] == at(60)
    assert summary(stdout)["windows"] == 6 and summary(stdout)["late_events"] == 0


def test_sliding_and_session_windows_over_a_file(run, tmp_path):
    rows = events(120) + [
        {**r, "_shape_event_time": at(1000 + r["id"])} for r in events(20)
    ]  # a gap, then a second burst
    src = write_jsonl(tmp_path / "e.jsonl", rows)
    out = tmp_path / "s.jsonl"
    assert run(src, "--window", "session", "--gap", "30s", "--windows", out)[0] == 0
    assert [w["rows"] for w in windows_of(out)] == [120, 20]
    out2 = tmp_path / "sl.jsonl"
    assert (
        run(src, "--window", "sliding", "--size", "60s", "--slide", "30s", "--windows", out2)[0]
        == 0
    )
    assert windows_of(out2)[0]["rows"] == 30  # the first window starts half empty


def test_event_time_field_and_unit(run, tmp_path):
    rows = [{"id": i, "ts": 1_767_225_600 + i} for i in range(120)]  # epoch seconds
    src = write_jsonl(tmp_path / "e.jsonl", rows)
    out = tmp_path / "w.jsonl"
    code, _, err = run(
        src, "--event-time", "ts", "--event-time-unit", "s",
        "--window", "tumbling", "--size", "60s", "--windows", out,
    )  # fmt: skip
    assert code == 0, err
    assert [w["rows"] for w in windows_of(out)] == [60, 60]


# ----------------------------------------------------------------- ordering


def shuffled(n: int = 360):
    rows = events(n)
    return rows[::2] + rows[1::2]  # every even second first, then every odd one


def test_file_order_replays_out_of_order_rows_as_late_and_event_time_order_does_not(run, tmp_path):
    src = write_jsonl(tmp_path / "e.jsonl", shuffled())
    base = ("--window", "tumbling", "--size", "60s", "--batch-size", 20)
    in_file_order = tmp_path / "a.jsonl"
    code, stdout, _ = run(src, *base, "--windows", in_file_order)
    assert code == 0 and summary(stdout)["late_events"] > 0
    by_time = tmp_path / "b.jsonl"
    code, stdout, _ = run(src, *base, "--windows", by_time, "--order", "event-time")
    assert code == 0 and summary(stdout)["late_events"] == 0
    assert [w["rows"] for w in windows_of(by_time)] == [60] * 6
    assert sum(w["rows"] for w in windows_of(in_file_order)) < 360


def test_event_time_order_is_stable_and_puts_rows_without_a_time_last(tmp_path):
    rows = [
        {"id": 0, "_shape_event_time": at(30)},
        {"id": 1},
        {"id": 2, "_shape_event_time": at(10)},
        {"id": 3, "_shape_event_time": at(30)},
    ]
    src = write_jsonl(tmp_path / "e.jsonl", rows)
    source = FileStreamSource()
    [(_, batch)] = source.read(str(src), None, order="event-time")
    assert batch.column("id").to_pylist() == [2, 0, 3, 1]


# -------------------------------------------------------------------- formats


def test_csv_and_parquet_rows_are_events(run, tmp_path):
    rows = events(120)
    table = pa.table(
        {
            "id": [r["id"] for r in rows],
            "amount": [r["amount"] for r in rows],
            "ts": pa.array(
                [T0 + timedelta(seconds=i) for i in range(120)], pa.timestamp("us", tz="UTC")
            ),
        }
    )
    pacsv.write_csv(table, tmp_path / "e.csv")
    pq.write_table(table, tmp_path / "e.parquet")
    for name in ("e.csv", "e.parquet"):
        out = tmp_path / f"{name}.w.jsonl"
        code, stdout, err = run(
            tmp_path / name,
            "--event-time",
            "ts",
            "--window",
            "tumbling",
            "--size",
            "60s",
            "--windows",
            out,
        )
        assert code == 0, (name, err)
        assert [w["rows"] for w in windows_of(out)] == [60, 60], name
        cols = windows_of(out)[0]["profile"]["columns"]
        assert {c["name"] for c in cols} == {"id", "amount", "_shape_event_time"}, name


def test_cloudevents_envelopes_are_read_as_their_data(run, tmp_path):
    rows = events(60)
    flat = write_jsonl(tmp_path / "flat.jsonl", rows)
    wrapped = tmp_path / "ce.jsonl"
    wrapped.write_text(
        "".join(
            json.dumps(
                {
                    "specversion": "1.0",
                    "id": f"t/{i}",
                    "type": "shape.t.row",
                    "source": "shape://x",
                    "data": r,
                }
            )
            + "\n"
            for i, r in enumerate(rows)
        )
    )
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert run(flat, "-o", a)[0] == 0 and run(wrapped, "-o", b)[0] == 0
    assert json.loads(a.read_text())["tables"] == json.loads(b.read_text())["tables"]


def test_what_shape_stream_writes_can_be_replayed(run, tmp_path):
    pytest.importorskip("shape_domains")
    events_file = tmp_path / "customers.jsonl"
    code = main(
        [
            "stream",
            "retail",
            "-t",
            "customer",
            "--max-events",
            "200",
            "--sink",
            "file",
            "-o",
            str(events_file),
        ]
    )
    assert code == 0
    out = tmp_path / "w.jsonl"
    code, stdout, err = run(events_file, "--window", "tumbling", "--size", "30d", "--windows", out)
    assert code == 0, err
    s = summary(stdout)
    assert s["events"] == 200 and s["undecodable"] == 0 and s["rejected"] == 0
    assert sum(w["rows"] for w in windows_of(out)) + s["late_events"] + s["null_event_time"] == 200
    assert s["null_event_time"] == 0


def test_the_format_can_be_named_when_the_suffix_does_not_say(run, tmp_path):
    src = tmp_path / "events.dat"
    pacsv.write_csv(pa.table({"a": [1, 2, 3]}), src)
    out = tmp_path / "g.json"
    code, stdout, err = run(src, "-o", out, "--option", "format=csv")
    assert code == 0, err
    assert summary(stdout)["events"] == 3


# ------------------------------------------------------ folders, globs, lines


def test_a_folder_and_a_glob_are_read_in_name_order(tmp_path):
    for i in range(3):
        write_jsonl(tmp_path / f"day-{i}.jsonl", [{"id": i * 10 + j} for j in range(2)])
    (tmp_path / "notes.txt").write_text("not an event")
    (tmp_path / "_hidden.jsonl").write_text('{"id": 999}\n')
    for uri in (str(tmp_path), str(tmp_path / "day-*.jsonl")):
        got = [v for _, b in FileStreamSource().read(uri, None) for v in b.column("id").to_pylist()]
        assert got == [0, 1, 10, 11, 20, 21], uri


def test_files_of_different_formats_are_refused_not_merged(tmp_path):
    write_jsonl(tmp_path / "a.jsonl", [{"a": 1}])
    pacsv.write_csv(pa.table({"a": [1]}), tmp_path / "b.csv")
    with pytest.raises(StreamSourceError, match="different formats"):
        list(FileStreamSource().read(str(tmp_path / "*"), None))


def test_bad_lines_are_counted_and_blank_lines_are_skipped(run, tmp_path):
    src = tmp_path / "e.jsonl"
    src.write_text('{"id": 1}\n\nnot json\n[1, 2]\n{"id": 2}\n   \n')
    out = tmp_path / "g.json"
    code, stdout, _ = run(src, "-o", out)
    s = summary(stdout)
    assert code == 0 and s["events"] == 2 and s["undecodable"] == 2


def test_an_empty_file_is_not_an_error(run, tmp_path):
    src = tmp_path / "e.jsonl"
    src.write_text("")
    code, stdout, _ = run(src, "-o", tmp_path / "g.json")
    assert code == 0 and summary(stdout)["events"] == 0


# -------------------------------------------------------- checkpoint & limits


def test_a_killed_run_resumes_from_its_checkpoint_to_the_uninterrupted_windows(tmp_path):
    from shape.streaming.checkpoint import FileCheckpointStore
    from shape.streaming.consumer import StreamConsumer
    from shape.streaming.runtime import TumblingProfiler

    src = write_jsonl(tmp_path / "e.jsonl", events(360))
    options = {"batch_size": 25, "schema": None}
    first = next(FileStreamSource().read(str(src), None, batch_size=25))[1]
    schema = first.schema

    def consumer(store=None):
        profiler = TumblingProfiler(schema, "60s")
        return StreamConsumer(
            FileStreamSource(), str(src), profiler, store, checkpoint_every=1,
            options={**options, "schema": schema},
        )  # fmt: skip

    whole = [w.to_dict() for w in consumer().run()]
    assert [w["rows"] for w in whole] == [60] * 6

    got = []
    killed = consumer(FileCheckpointStore(tmp_path / "ck.json"))
    run = killed.run()
    for window in run:
        got.append(window.to_dict())
        if killed.batches_processed >= 9:
            break  # the process "dies" here: batch 10's checkpoint is never written
    run.close()
    assert not killed.profiler.finished
    resumed = consumer(FileCheckpointStore(tmp_path / "ck.json"))
    assert resumed.source_offset == {"0": 225}  # nine batches of 25 rows
    got.extend(w.to_dict() for w in resumed.run())
    seen = {(w["start_us"], w["end_us"]): w for w in got}  # windows are identified by their key
    assert [seen[k] for k in sorted(seen)] == whole


def test_max_events_stops_the_read(run, tmp_path):
    src = write_jsonl(tmp_path / "e.jsonl", events(100))
    code, stdout, _ = run(src, "-o", tmp_path / "g.json", "--max-events", 30, "--batch-size", 8)
    assert code == 0 and summary(stdout)["events"] == 30


# ---------------------------------------------------------------------- errors


def test_a_missing_file_and_a_bad_option_are_clear_errors(run, tmp_path, capsys):
    code, _, err = run(tmp_path / "nope.jsonl", "-o", tmp_path / "g.json")
    assert code == 2 and "no such file" in err
    src = write_jsonl(tmp_path / "e.jsonl", events(3))
    assert run(src, "-o", tmp_path / "g.json", "--option", "format=xml")[0] == 2
    assert run(src, "-o", tmp_path / "g.json", "--option", "nope=1")[0] == 2
    assert run(tmp_path / "x*.jsonl", "-o", tmp_path / "g.json")[0] == 2


def test_follow_and_start_latest_are_refused_for_files(run, tmp_path):
    src = write_jsonl(tmp_path / "e.jsonl", events(3))
    code, _, err = run(src, "-o", tmp_path / "g.json", "--follow", "--max-events", 2)
    assert code == 2 and "--follow" in err
    code, _, err = run(src, "-o", tmp_path / "g.json", "--start", "latest")
    assert code == 2 and "latest" in err


def test_which_uris_are_files():
    for uri in ("-", "events.jsonl", "/tmp/x", "file:///tmp/x", "FILE:///tmp/x", "C:\\data\\x.csv"):
        assert is_file_uri(uri), uri
    for uri in ("kafka://h:9092/t", "eventhubs://ns/hub", "mem://x"):
        assert not is_file_uri(uri), uri


def test_a_resumed_offset_skips_what_was_consumed(tmp_path):
    src = write_jsonl(tmp_path / "e.jsonl", [{"id": i} for i in range(10)])
    got = list(FileStreamSource().read(str(src), StreamOffset({"0": 6}), batch_size=2))
    assert [v for _, b in got for v in b.column("id").to_pylist()] == [6, 7, 8, 9]
    assert [o.value["0"] for o, _ in got] == [8, 10]
