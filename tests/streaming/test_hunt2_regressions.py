"""Regression tests for the defects the second streaming bug hunt (HUNT2-streaming) found; each
test names its issue."""

from __future__ import annotations

import json
from pathlib import Path

from shape.cli.main import main
from shape.streaming.emit import read_answer_key

FAULTS = [
    "--out-of-order",
    "0.2",
    "--ooo-window",
    "50",
    "--anomaly-fraction",
    "0.1",
    "--duplicate-fraction",
    "0.1",
    "--duplicate-window",
    "30",
    "--poison-fraction",
    "0.05",
    "--seed",
    "7",
    "--batch-events",
    "40",
]


def _emit(out: Path, key: Path, *extra: str) -> int:
    args = ["emit", "retail", "--scale", "tiny", *FAULTS, "--sink", "file", "-o", str(out)]
    return main([*args, "--answer-key", str(key), *extra])


def _idents(path: Path) -> set[tuple[str, str, int]]:
    return {(r["kind"], r["table"], r["seq"]) for r in read_answer_key(str(path))}


def test_694_a_resumed_run_keeps_the_answer_key_of_the_first_part(tmp_path, capsys):
    whole_out, whole_key = tmp_path / "whole.jsonl", tmp_path / "whole.key"
    assert _emit(whole_out, whole_key, "--fresh") == 0
    part_out, part_key = tmp_path / "part.jsonl", tmp_path / "part.key"
    assert _emit(part_out, part_key, "--max-events", "333", "--fresh") == 0
    first_part = _idents(part_key)
    assert _emit(part_out, part_key) == 0  # resumes from the checkpoint
    capsys.readouterr()
    assert first_part <= _idents(part_key), "the first run's records were erased"
    assert _idents(part_key) == _idents(whole_key)
    # every duplicate in the stream is in the key
    seen: dict[tuple[str, int], int] = {}
    for line in part_out.read_bytes().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        k = (e["_shape_table"], e["_shape_seq"])
        seen[k] = seen.get(k, 0) + 1
    duplicated = {k for k, n in seen.items() if n > 1}
    assert duplicated <= {(t, s) for kind, t, s in _idents(part_key) if kind == "duplicate"}


def test_694_a_fresh_run_still_starts_the_answer_key_empty(tmp_path):
    key = tmp_path / "k.key"
    key.write_text('{"kind": "late", "table": "stale", "seq": 1}\n')
    assert _emit(tmp_path / "e.jsonl", key, "--max-events", "50", "--fresh") == 0
    assert all(r["table"] != "stale" for r in read_answer_key(str(key)))
