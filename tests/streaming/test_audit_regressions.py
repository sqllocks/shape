"""Regression tests for the defects the streaming audit (AUD-stream) found; each test names its
issue."""

import json

import pytest

from shape.cli.main import main
from shape.streaming import file_source


def _events(path, n=50):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"v": i, "_shape_event_time": f"2026-01-01T00:{i:02d}:00"}) + "\n")


def _windows(path):
    return [(d["start"], d["rows"]) for d in map(json.loads, path.read_text().splitlines())]


def test_153_interrupt_then_resume_writes_complete_windows(tmp_path, monkeypatch, capsys):
    events = tmp_path / "ev" / "a.jsonl"
    _events(events)
    args = ["stream-profile", str(events), "--window", "tumbling", "--size", "10m"]
    args += ["--batch-size", "15", "--checkpoint-every", "1"]
    whole = tmp_path / "whole.jsonl"
    assert main([*args, "--windows", str(whole)]) == 0

    original = file_source.FileStreamSource.read
    seen = {"n": 0}

    def interrupted(self, uri, start=None, **options):
        for item in original(self, uri, start, **options):
            seen["n"] += 1
            if seen["n"] == 2 and start is None:
                raise KeyboardInterrupt
            yield item

    monkeypatch.setattr(file_source.FileStreamSource, "read", interrupted)
    resumed = tmp_path / "resumed.jsonl"
    checkpoint = tmp_path / "ck.json"
    run = [*args, "--windows", str(resumed), "--checkpoint", str(checkpoint)]
    assert main(run) == 0
    assert main(run) == 0
    capsys.readouterr()
    assert _windows(resumed) == _windows(whole)
