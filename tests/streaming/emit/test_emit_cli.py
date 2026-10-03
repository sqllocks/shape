"""``shape emit``: the flags, the stream's equality with ``shape generate``, and crash safety."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from shape.cli.main import main
from shape.streaming.emit import read_events
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE, decode_line
from shape.streaming.emit.sinks import repair_tail

SHAPE = [sys.executable, "-c", "import sys; from shape.cli.main import main; sys.exit(main())"]
BASE = ["emit", "retail", "--scale", "small", "--seed", "3"]


@pytest.fixture(autouse=True)
def _confirm_remote(monkeypatch):
    # These tests are about the sinks, not the confirmation (tests/cli/test_remote_confirmation.py).
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")


def _lines(path: Path) -> list[bytes]:
    return path.read_bytes().splitlines()


def _dedupe(lines: list[bytes]) -> list[bytes]:
    seen: set[tuple[str, int]] = set()
    out = []
    for line in lines:
        e = decode_line(line)
        key = (e[FIELD_TABLE], e[FIELD_SEQ])
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out


def test_console_events_are_the_generate_rows(capsys) -> None:
    assert main([*BASE, "--table", "customer", "--max-events", "5"]) == 0
    out = capsys.readouterr()
    rows = [json.loads(x) for x in out.out.splitlines()]
    assert [r["_shape_seq"] for r in rows] == [0, 1, 2, 3, 4]
    assert {r["_shape_table"] for r in rows} == {"customer"}
    assert rows[0]["_shape_event_time"] == rows[0]["signup_date"]
    assert "5 events delivered" in out.err and out.err.count("\n") == 1  # report is on stderr
    import shape

    gen = shape.generate("retail", scale="small", seed=3)["customer"]
    assert [r["customer_id"] for r in rows] == gen["customer_id"].to_pylist()[:5]


def test_file_sink_writes_a_checkpoint_beside_it(tmp_path: Path, capsys) -> None:
    out = tmp_path / "e.jsonl"
    assert main([*BASE, "--sink", "file", "-o", str(out), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["events"] == 21750 and report["complete"] is True
    assert len(_lines(out)) == 21750
    assert json.loads(Path(f"{out}.checkpoint").read_text())["complete"] is True
    # running again does nothing; --fresh starts over
    assert main([*BASE, "--sink", "file", "-o", str(out), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["already_complete"] is True
    assert len(_lines(out)) == 21750
    assert main([*BASE, "--sink", "file", "-o", str(out), "--fresh", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == 21750
    assert len(_lines(out)) == 21750


def test_cloudevents(tmp_path: Path) -> None:
    out = tmp_path / "e.jsonl"
    assert (
        main(
            [
                *BASE,
                "--sink",
                "file",
                "-o",
                str(out),
                "--envelope",
                "cloudevents",
                "--table",
                "store",
                "--max-events",
                "3",
            ]
        )
        == 0
    )
    ce = [json.loads(x) for x in _lines(out)]
    assert [c["id"] for c in ce] == ["store/0", "store/1", "store/2"]
    assert all(c["specversion"] == "1.0" and c["data"]["_shape_table"] == "store" for c in ce)


def test_anomaly_fraction_is_honoured(tmp_path: Path, capsys) -> None:
    clean, dirty = tmp_path / "c.jsonl", tmp_path / "d.jsonl"
    common = [*BASE, "--sink", "file", "--table", "order_line", "--json"]
    assert main([*common, "-o", str(clean)]) == 0
    assert main([*common, "-o", str(dirty), "--anomaly-fraction", "0.2"]) == 0
    capsys.readouterr()
    a, b = _lines(clean), _lines(dirty)
    assert len(a) == len(b) == 12500
    changed = sum(x != y for x, y in zip(a, b, strict=True))
    assert 0.1 * 12500 < changed <= 0.2 * 12500 * 1.2
    # same seed, same flags: the same anomalies
    again = tmp_path / "d2.jsonl"
    assert main([*common, "-o", str(again), "--anomaly-fraction", "0.2"]) == 0
    assert _lines(again) == b


def test_out_of_order_flag(tmp_path: Path) -> None:
    out = tmp_path / "o.jsonl"
    assert (
        main(
            [
                *BASE,
                "--sink",
                "file",
                "-o",
                str(out),
                "--table",
                "customer",
                "--out-of-order",
                "0.3",
                "--ooo-window",
                "50",
            ]
        )
        == 0
    )
    seqs = [e[FIELD_SEQ] for e in read_events(str(out))]
    assert sorted(seqs) == list(range(1000)) and seqs != sorted(seqs)


@pytest.mark.parametrize(
    "args, text",
    [
        (["--burst", "1:1:2"], "--burst needs --realtime"),
        (["--anomaly-mutator", "value-anomaly"], "needs --anomaly-fraction"),
        (["--anomaly-fraction", "2"], "between 0 and 1"),
        (["--out-of-order", "-1"], "between 0 and 1"),
        (["--realtime", "--burst", "x"], "START:DURATION:MULT"),
        (["--sink", "file"], "needs --output"),
        (["--sink", "nowhere://x"], "unknown sink"),
        (["--table", "nope"], "unknown table"),
        (["--anomaly-fraction", "0.1", "--anomaly-mutator", "nope"], "available: value-anomaly"),
    ],
)
def test_usage_errors_exit_2(args: list[str], text: str, capsys) -> None:
    assert main([*BASE, *args]) == 2
    assert text in capsys.readouterr().err


def test_repair_tail(tmp_path: Path) -> None:
    p = tmp_path / "f"
    p.write_bytes(b'{"a":1}\n{"a":2}\n{"a":')
    assert repair_tail(p) == 5 and p.read_bytes() == b'{"a":1}\n{"a":2}\n'
    assert repair_tail(p) == 0
    p.write_bytes(b"no newline at all")
    assert repair_tail(p) == 17 and p.read_bytes() == b""
    assert repair_tail(tmp_path / "missing") == 0
    big = b"x" * 200_000
    p.write_bytes(b"ok\n" + big)
    assert repair_tail(p) == len(big) and p.read_bytes() == b"ok\n"


def _spawn(args: list[str]) -> subprocess.Popen[bytes]:
    # Windows: a process group of its own, so Ctrl-Break (its graceful stop) reaches only the child.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        [*SHAPE, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags
    )


def _hard_kill(proc: subprocess.Popen[bytes]) -> None:
    """The platform's uncatchable kill: SIGKILL on POSIX, TerminateProcess on Windows."""
    proc.kill()
    proc.wait()
    if sys.platform == "win32":
        assert proc.returncode != 0
    else:
        assert proc.returncode == -signal.SIGKILL


def _graceful_stop(proc: subprocess.Popen[bytes]) -> None:
    """The platform's catchable stop: SIGTERM on POSIX, Ctrl-Break (SIGBREAK) on Windows."""
    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGTERM)


def _wait_for_events(
    path: Path, n: int, proc: subprocess.Popen[bytes], timeout: float = 60
) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists() and path.read_bytes().count(b"\n") >= n:
            return
        assert proc.poll() is None, "the process ended before the kill point"
        time.sleep(0.02)
    raise AssertionError("timed out waiting for events")


STREAM = ["--out-of-order", "0.15", "--ooo-window", "200", "--anomaly-fraction", "0.05"]


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> list[bytes]:
    out = tmp_path_factory.mktemp("ref") / "ref.jsonl"
    assert main([*BASE, "--sink", "file", "-o", str(out), *STREAM]) == 0
    lines = _lines(out)
    assert len(lines) == 21750
    return lines


@pytest.mark.parametrize("kill_at", [1500, 9000, 21000])
def test_kill_9_then_restart_equals_an_uninterrupted_run(
    tmp_path: Path, reference: list[bytes], kill_at: int
) -> None:
    out = tmp_path / "e.jsonl"
    args = [
        *BASE,
        "--sink",
        "file",
        "-o",
        str(out),
        *STREAM,
        "--realtime",
        "--rate",
        "6000",
        "--checkpoint-every",
        "100000",
    ]
    proc = _spawn(args)
    _wait_for_events(out, kill_at, proc)
    _hard_kill(proc)
    killed_with = len(_lines(out))
    assert killed_with < 21750
    # restart: same command, finishes the stream
    rc = subprocess.run([*SHAPE, *args], capture_output=True)
    assert rc.returncode == 0, rc.stderr.decode()
    lines = _lines(out)
    assert len(lines) >= 21750
    assert _dedupe(lines) == reference  # same events, same order, byte for byte
    doc = json.loads(Path(f"{out}.checkpoint").read_text())
    assert doc["complete"] is True and doc["offset"] == 21750


def test_sigterm_shuts_down_with_a_checkpoint_and_loses_nothing(
    tmp_path: Path, reference: list[bytes]
) -> None:
    out = tmp_path / "e.jsonl"
    args = [*BASE, "--sink", "file", "-o", str(out), *STREAM, "--realtime", "--rate", "6000"]
    proc = _spawn(args)
    _wait_for_events(out, 4000, proc)
    _graceful_stop(proc)
    assert proc.wait(timeout=30) == 0
    assert b"stop-request" in (proc.stdout.read() if proc.stdout else b"")
    first = _lines(out)
    doc = json.loads(Path(f"{out}.checkpoint").read_text())
    assert doc["complete"] is False and doc["offset"] == len(first)  # the shutdown checkpoint
    rc = subprocess.run([*SHAPE, *args], capture_output=True)
    assert rc.returncode == 0, rc.stderr.decode()
    assert _lines(out) == reference  # a clean stop leaves no duplicates at all


def test_restart_with_other_options_is_refused(tmp_path: Path, capsys) -> None:
    out = tmp_path / "e.jsonl"
    assert main([*BASE, "--sink", "file", "-o", str(out), "--max-events", "10"]) == 0
    assert main([*BASE, "--sink", "file", "-o", str(out), "--seed", "4"]) == 2
    assert "different stream" in capsys.readouterr().err
    assert len(_lines(out)) == 10


def test_a_torn_last_line_is_repaired_on_resume(tmp_path: Path, reference: list[bytes]) -> None:
    out = tmp_path / "e.jsonl"
    args = [*BASE, "--sink", "file", "-o", str(out), *STREAM]
    assert main([*args, "--max-events", "7000", "--checkpoint-every", "100000"]) == 0
    # a killed writer left half a line after the checkpointed events
    with open(out, "ab") as f:
        f.write(b'{"order_id":1,"cust')
    assert main(args) == 0
    assert _lines(out) == reference


def test_kill_9_with_a_stale_checkpoint_sends_duplicates_that_dedupe_removes(
    tmp_path: Path, reference: list[bytes]
) -> None:
    out = tmp_path / "e.jsonl"
    args = [*BASE, "--sink", "file", "-o", str(out), *STREAM]
    assert main([*args, "--max-events", "3000"]) == 0  # leaves a checkpoint at 3000
    live = [
        *args,
        "--realtime",
        "--rate",
        "6000",
        "--checkpoint-every",
        "100000000",
        "--checkpoint-seconds",
        "100000",
    ]
    proc = _spawn(live)
    _wait_for_events(out, 9000, proc)
    _hard_kill(proc)
    delivered = len(_lines(out))
    assert 9000 <= delivered < 21750
    assert json.loads(Path(f"{out}.checkpoint").read_text())["offset"] == 3000  # stale
    rc = subprocess.run([*SHAPE, *args], capture_output=True)
    assert rc.returncode == 0, rc.stderr.decode()
    lines = _lines(out)
    assert len(lines) == 21750 + (delivered - 3000)  # everything after the checkpoint came twice
    assert _dedupe(lines) == reference
