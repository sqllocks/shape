"""The progress line (W2-09 item 7): ``--progress/--no-progress``."""

from __future__ import annotations

import io
import sys

import pytest

from shape.cli.main import main
from shape.streaming.emit import EmitConfig, EmitRunner, MemorySink, contract
from shape.streaming.emit.progress import ProgressLine, format_eta


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make(tty: bool = False, **kw):
    out = io.StringIO()
    clock = Clock()
    return ProgressLine(out, tty=tty, clock=clock, **kw), out, clock


# ---- the line -------------------------------------------------------------------------------


def test_the_documented_line():
    p, out, clock = make(start_offset=0)
    clock.now += 12.0
    p.update(120_400, 1_000_000, retries=2, lag=0.4, dead_lettered=0, rate=9_980.0)
    assert out.getvalue() == (
        "emitted 120,400 / 1,000,000 events  9,980/s  lag 0.4s  retries 2  dead-lettered 0  "
        "eta 1m28s\n"
    )


def test_total_and_eta_only_when_known():
    p, out, _ = make()
    p.update(5_000, None, retries=0, lag=0.0, dead_lettered=3, rate=250.0)
    assert out.getvalue() == "emitted 5,000 events  250/s  lag 0.0s  retries 0  dead-lettered 3\n"


def test_no_eta_when_nothing_is_moving():
    p, out, _ = make()
    p.update(0, 100, retries=0, lag=0.0, dead_lettered=0, rate=0.0)
    assert "eta" not in out.getvalue() and out.getvalue().startswith("emitted 0 / 100 events  0/s")


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0s"), (0.4, "0s"), (1, "1s"), (59.4, "59s"), (59.6, "1m00s"), (60, "1m00s"),
        (88, "1m28s"), (3599, "59m59s"), (3600, "1h00m"), (7380, "2h03m"), (86_400, "24h00m"),
    ],
)  # fmt: skip
def test_eta_formats(seconds, text):
    assert format_eta(seconds) == text


def test_the_rate_is_measured_when_not_given():
    p, out, clock = make(start_offset=1_000)
    clock.now += 4.0
    p.update(3_000, 10_000, retries=0, lag=0.0, dead_lettered=0)  # 2,000 events in 4 s
    assert "500/s" in out.getvalue() and "eta 14s" in out.getvalue()
    clock.now += 2.0
    p.update(4_000, 10_000, retries=0, lag=0.0, dead_lettered=0)  # 1,000 more in 2 s
    assert out.getvalue().splitlines()[-1].startswith("emitted 4,000 / 10,000 events  500/s")


# ---- rewritten at most once a second ----------------------------------------------------------


def test_at_most_one_write_a_second():
    p, out, clock = make()
    for i in range(100):  # 10 updates a second for 10 seconds
        p.update(i * 10, 1_000, retries=0, lag=0.0, dead_lettered=0)
        clock.now += 0.1
    lines = out.getvalue().splitlines()
    assert 9 <= len(lines) <= 11  # one per second, the first at once
    assert [x.split()[1] for x in lines] == sorted(
        (x.split()[1] for x in lines), key=lambda s: int(s.replace(",", ""))
    )


def test_a_tty_rewrites_one_line_and_a_pipe_gets_lines():
    p, out, clock = make(tty=True)
    p.update(1_000, 5_000, retries=0, lag=0.0, dead_lettered=0, rate=100.0)
    clock.now += 1.0
    p.update(2_000, 5_000, retries=0, lag=0.0, dead_lettered=0, rate=100.0)
    text = out.getvalue()
    assert text.count("\n") == 0 and text.count("\r") == 2  # rewritten in place
    p.finish(5_000, 5_000, retries=0, lag=0.0, dead_lettered=0)
    assert out.getvalue().endswith("\n") and out.getvalue().count("\n") == 1
    q, pipe, clock2 = make(tty=False)
    q.update(1_000, 5_000, retries=0, lag=0.0, dead_lettered=0, rate=100.0)
    assert "\r" not in pipe.getvalue() and pipe.getvalue().endswith("\n")


def test_a_shorter_line_clears_the_longer_one_before_it_on_a_tty():
    p, out, clock = make(tty=True)
    p.update(1_000_000, 9_000_000, retries=12, lag=12.5, dead_lettered=100, rate=99_999.0)
    first = out.getvalue()
    clock.now += 1.0
    p.update(1, 9_000_000, retries=0, lag=0.0, dead_lettered=0, rate=1.0)
    second = out.getvalue()[len(first) :]
    assert len(second.lstrip("\r").rstrip()) < len(first.strip())
    assert len(second.lstrip("\r")) >= len(first.lstrip("\r"))  # padded over the old line


def test_the_final_line_is_always_written_even_inside_the_interval():
    p, out, clock = make()
    p.update(10, 100, retries=0, lag=0.0, dead_lettered=0, rate=1.0)
    clock.now += 0.01
    p.update(20, 100, retries=0, lag=0.0, dead_lettered=0, rate=1.0)  # throttled
    p.finish(100, 100, retries=1, lag=0.2, dead_lettered=4)
    lines = out.getvalue().splitlines()
    assert len(lines) == 2
    assert lines[-1].startswith("emitted 100 / 100 events") and "retries 1" in lines[-1]
    assert "dead-lettered 4" in lines[-1] and "eta" not in lines[-1]


def test_finish_without_any_update_still_writes_a_line():
    p, out, _ = make()
    p.finish(0, 0, retries=0, lag=0.0, dead_lettered=0)
    assert out.getvalue().startswith("emitted 0 / 0 events")


# ---- through the runner ----------------------------------------------------------------------


def test_the_runner_reports_progress_and_a_final_line():
    p, out, clock = make()

    class Ticking(MemorySink):
        def send(self, batch):
            clock.now += 0.4  # 0.4 s per batch
            super().send(batch)

    cfg = EmitConfig(max_events=1_000, batch_events=100, retry_backoff=0.0)
    report = EmitRunner(contract.default_plan(), Ticking(), cfg, progress=p).run()
    lines = out.getvalue().splitlines()
    assert report.events == 1_000 and 3 <= len(lines) <= 6
    assert lines[-1].startswith("emitted 1,000 / 1,000 events") and "eta" not in lines[-1]
    assert all("dead-lettered 0" in x and "retries 0" in x for x in lines)
    assert any("eta" in x for x in lines[:-1])


def test_a_resumed_run_counts_from_its_offset(tmp_path):
    p, out, clock = make(start_offset=0)
    ck = tmp_path / "ck.json"
    cfg = dict(batch_events=100, checkpoint_path=str(ck), checkpoint_every=100)
    EmitRunner(contract.default_plan(), MemorySink(), EmitConfig(max_events=400, **cfg)).run()
    EmitRunner(
        contract.default_plan(), MemorySink(), EmitConfig(max_events=900, **cfg), progress=p
    ).run()
    assert out.getvalue().splitlines()[-1].startswith("emitted 900 / 900 events")


def test_progress_shows_retries_and_the_dead_lettered_count():
    from shape.streaming.emit import DeadLetterSink, RejectedEvents

    class Flaky(MemorySink):
        fails = 1

        def send(self, batch):
            if self.fails:
                self.fails -= 1
                raise ConnectionError("blip")
            seqs = batch.column("_shape_seq").to_pylist()
            if 7 in seqs:
                raise RejectedEvents([("order_line/7", "too big")])
            super().send(batch)

    p, out, _ = make()
    sink = DeadLetterSink(Flaky(), MemorySink(), destination="x://d")
    cfg = EmitConfig(max_events=300, batch_events=100, retry_backoff=0.0)
    EmitRunner(contract.default_plan(), sink, cfg, dead_letter=sink, progress=p).run()
    assert "retries 1  dead-lettered 1" in out.getvalue().splitlines()[-1]


# ---- the command line ------------------------------------------------------------------------

BASE = ["emit", "retail", "--table", "customer", "--max-events", "300"]


def test_progress_is_off_when_stderr_is_not_a_terminal_and_never_touches_standard_output(capsys):
    assert main(BASE) == 0
    plain = capsys.readouterr()
    assert "emitted" not in plain.err.replace("events delivered", "")
    assert main([*BASE, "--progress"]) == 0
    forced = capsys.readouterr()
    assert forced.out == plain.out  # the console sink's output is byte-identical
    assert "emitted 300 / 300 events" in forced.err
    assert main([*BASE, "--no-progress"]) == 0
    off = capsys.readouterr()
    assert off.out == plain.out and "emitted" not in off.err.replace("events delivered", "")


def test_progress_defaults_on_for_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
    assert main(BASE) == 0
    err = capsys.readouterr().err
    assert "emitted 300 / 300 events" in err and "\r" in err  # rewritten in place on a terminal
    assert main([*BASE, "--no-progress"]) == 0
    assert "emitted" not in capsys.readouterr().err.replace("events delivered", "")


def test_progress_does_not_change_the_json_report(capsys):
    assert main([*BASE, "--json"]) == 0
    a = capsys.readouterr()
    assert main([*BASE, "--json", "--progress"]) == 0
    b = capsys.readouterr()
    assert a.out == b.out
    import json

    docs = [json.loads(x.err.strip().splitlines()[-1]) for x in (a, b)]
    for d in docs:
        d.pop("elapsed"), d.pop("rate"), d.pop("per_second"), d.pop("max_queue_depth")
    assert docs[0] == docs[1]
