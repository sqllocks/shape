"""Regression tests for the defects the second streaming bug hunt (HUNT2-streaming) found; each
test names its issue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.generation import load_target
from shape.cli.main import main
from shape.generation.engine import Engine
from shape.streaming.emit import (
    AnomalyInjector,
    AnswerKey,
    EmitConfig,
    EmitRunner,
    EventPlan,
    read_answer_key,
    resolve_mutators,
)
from shape.streaming.emit.sinks import MemorySink

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


def test_695_the_answer_key_names_only_events_the_run_delivered(tmp_path):
    out, key = tmp_path / "k.jsonl", tmp_path / "k.key"
    args = ["emit", "retail", "--scale", "tiny", "--anomaly-fraction", "0.2"]
    args += ["--out-of-order", "0.3", "--ooo-window", "20", "--seed", "3", "--sink", "file"]
    args += ["-o", str(out), "--answer-key", str(key), "--max-events", "100", "--fresh"]
    assert main(args) == 0
    sent = {
        (e["_shape_table"], e["_shape_seq"]) for e in map(json.loads, out.read_text().splitlines())
    }
    assert len(sent) == 100
    records = read_answer_key(str(key))
    assert records, "the faults of the delivered events are listed"
    assert [r for r in records if (r["table"], r["seq"]) not in sent] == []


def test_695_a_complete_run_lists_every_fault():
    def run(staged: bool) -> set[tuple[str, str, int]]:
        engine = Engine(load_target("retail", None), scale="tiny", seed=5)
        key = AnswerKey(staged=staged)
        injector = AnomalyInjector(0.1, resolve_mutators(()), engine.seed)
        plan = EventPlan(engine, out_of_order=0.2, ooo_window=30, anomaly=injector, answer_key=key)
        EmitRunner(plan, MemorySink(), EmitConfig(batch_events=64)).run()
        return {(r["kind"], r["table"], r["seq"]) for r in key.records}

    assert run(staged=True) == run(staged=False) != set()


def test_697_input_without_a_decodable_event_is_an_error(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n[1,2]\n42\n\n")
    out = tmp_path / "out.json"
    assert main(["stream-profile", str(bad), "-o", str(out)]) == 2
    err = capsys.readouterr().err
    assert "3" in err and "undecodable" in err and "--option format=" in err
    assert not out.exists()


def test_697_empty_and_blank_input_is_still_no_events(tmp_path, capsys):
    for text in ("", "\n\n  \n"):
        empty = tmp_path / "empty.jsonl"
        empty.write_text(text)
        assert main(["stream-profile", str(empty), "-o", str(tmp_path / "o.json")]) == 0
        assert "no events" in capsys.readouterr().err


def test_697_one_decodable_event_among_bad_lines_is_profiled(tmp_path, capsys):
    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text('garbage\n{"v": 1}\n[2]\n')
    assert main(["stream-profile", str(mixed), "-o", str(tmp_path / "o.json")]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["events"] == 1 and summary["undecodable"] == 2


def _poisoned(values: dict, *, envelope: str = "flat", time: bool = False) -> bytes:
    import pyarrow as pa

    from shape.streaming.emit.formats import (
        FIELD_POISON,
        FIELD_SEQ,
        FIELD_TABLE,
        FIELD_TIME,
        encode_batch,
    )

    cols = {**{k: [v] for k, v in values.items()}, FIELD_TABLE: ["t"], FIELD_SEQ: [7]}
    if time:
        cols[FIELD_TIME] = ["2024-01-01T00:00:00"]
    cols[FIELD_POISON] = [True]
    return encode_batch(pa.RecordBatch.from_pydict(cols), envelope).rstrip(b"\n")


def test_699_a_poison_event_is_not_json_but_keeps_its_key():
    for time in (False, True):
        line = _poisoned({"name": "x" * 30}, time=time)
        with pytest.raises(ValueError):
            json.loads(line)
        assert b'"_shape_table":"t","_shape_seq":7' in line


def test_699_a_poison_event_is_valid_utf8_whatever_it_holds():
    for n in range(1, 80):
        for time in (False, True):
            _poisoned({"name": "é" * n}, time=time).decode("utf-8")
            _poisoned({"name": "☃x" * n}, time=time).decode("utf-8")


def test_699_a_poison_cloudevent_keeps_its_id():
    line = _poisoned({"name": "x" * 30}, envelope="cloudevents")
    with pytest.raises(ValueError):
        json.loads(line)
    assert b'"id":"t/7"' in line


def test_699_poison_stays_a_strict_prefix():
    from shape.streaming.emit.formats import poison_body

    for body in (b'{"a":1}', b'{"_shape_seq":3}', b'{"x":"\xc3\xa9","_shape_seq":10,"b":2}'):
        cut = poison_body(body)
        assert body.startswith(cut) and len(cut) < len(body)
