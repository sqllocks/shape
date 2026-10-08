"""``--dry-run`` (W2-09 item 6): everything a run would use, nothing opened, written or sent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from shape.cli.main import main
from shape.plugins.host import PluginHost
from shape.plugins.registry import register_builtins
from tests.streaming.emit.test_drift_stream import PLAN_DOC, SCHEMA_DOC  # the same fixtures

BASE = ["emit", "retail", "--table", "order_line", "--max-events", "1000"]


@pytest.fixture(autouse=True)
def _confirmed(monkeypatch):
    # The real runs here compare their refusals and failures with the dry run's; a target that
    # is not on this machine first needs W1-17's confirmation, which this gives (a dry run needs
    # none, so the dry runs are unchanged).
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")


class Sentinel(Exception):
    """A fake touched the destination."""


class FailingEmitter:
    """A ``fake://`` emitter that fails on connect and on write: a dry run must never call it."""

    name = "fake"
    schemes = ("fake",)
    accepts_poison = True
    event_formats = ()
    sink_config_keys = ("token",)
    touched: list[str] = []

    def emit(self, *a: Any, **k: Any) -> int:
        self.touched.append("emit")
        raise Sentinel("connected and wrote")

    def flush(self) -> None:
        self.touched.append("flush")
        raise Sentinel("flushed")

    def close(self) -> None:
        self.touched.append("close")
        raise Sentinel("closed")


@pytest.fixture()
def fake(monkeypatch):
    from shape.cli.generation import load_target

    load_target("retail", None)  # find the domains before the plugin host is replaced
    FailingEmitter.touched = []
    host = PluginHost(entry_points=lambda: [])
    register_builtins(host)
    host.register("shape.emitters", "fake", FailingEmitter, api="1.0", source="test")
    monkeypatch.setattr("shape.plugins.host.default_host", lambda: host)
    return FailingEmitter


def listing(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


def plan_of(capsys) -> dict[str, Any]:
    # the dry-run document of every writing command (W1-14) carries the emit plan under "plan"
    doc = json.loads(capsys.readouterr().out)
    assert doc["format"] == "shape-dry-run" and doc["version"] == 1 and "actions" in doc
    return doc["plan"]


# ---- nothing is opened, written or sent ------------------------------------------------------


def test_a_dry_run_touches_nothing_and_exits_0(fake, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    argv = [
        *BASE, "--sink", "fake://host/topic", "--dead-letter", f"file://{tmp_path}/dlq.jsonl",
        "--to", f"file://{tmp_path}/copy.jsonl", "--checkpoint", str(tmp_path / "ck.json"),
        "--answer-key", str(tmp_path / "key.jsonl"), "--out-of-order", "0.1", "--dry-run",
    ]  # fmt: skip
    before = listing(tmp_path)
    assert main(argv) == 0
    assert listing(tmp_path) == before  # no checkpoint, no answer key, no dead-letter file, no copy
    assert fake.touched == []  # no connect, no write, no flush, no close
    text = capsys.readouterr().out
    assert "dry run" in text.lower() and "fake://host/topic" in text


def test_the_fake_really_fails_when_it_is_used(fake, capsys):
    """The control: without --dry-run the same command reaches the fake and fails."""
    with pytest.raises(Sentinel):
        main([*BASE, "--sink", "fake://host/topic"])


def test_a_dry_run_sends_no_event_to_the_console(capsys):
    assert main([*BASE, "--dry-run"]) == 0
    assert "_shape_seq" not in capsys.readouterr().out


def test_a_dry_run_with_a_file_sink_does_not_create_the_file(tmp_path, capsys):
    out = tmp_path / "e.jsonl"
    assert main([*BASE, "--sink", "file", "-o", str(out), "--dry-run"]) == 0
    assert not out.exists() and not Path(f"{out}.checkpoint").exists()


# ---- the plan --------------------------------------------------------------------------------


def test_the_json_plan(fake, tmp_path, capsys):
    argv = [
        *BASE, "--seed", "5", "--scale", "small", "--sink", "fake://host/topic",
        "--dead-letter", f"file://{tmp_path}/dlq.jsonl", "--checkpoint", str(tmp_path / "ck.json"),
        "--answer-key", str(tmp_path / "key.jsonl"), "--dry-run", "--json",
    ]  # fmt: skip
    assert main(argv) == 0
    plan = plan_of(capsys)
    assert plan["format"] == "shape-emit-plan" and plan["version"] == 1
    assert plan["command"] == "emit"
    t = plan["target"]
    assert t["name"] == "retail" and t["seed"] == 5 and t["scale"] == "small"
    assert [x["name"] for x in t["tables"]] == ["order_line"]
    assert t["tables"][0]["rows"] > 0 and t["total_events"] == t["tables"][0]["rows"]
    assert plan["limits"] == {"max_events": 1000, "duration": None, "events": 1000}
    dests = {d["role"]: d for d in plan["destinations"]}
    assert dests["sink"] == {
        "role": "sink", "uri": "fake://host/topic", "kind": "emitter", "scheme": "fake",
        "plugin": "fake", "event_format": "json",
    }  # fmt: skip
    assert dests["dead-letter"]["kind"] == "emitter" and dests["dead-letter"]["scheme"] == "file"
    assert plan["checkpoint"] == {"path": str(tmp_path / "ck.json"), "state": "fresh", "offset": 0}
    assert plan["answer_key"] == str(tmp_path / "key.jsonl")
    assert plan["envelope"] == "flat" and plan["drift_plan"] is None
    assert plan["pacing"]["mode"] == "unpaced" and plan["pacing"]["expected_seconds"] is None


def test_the_text_plan_names_everything(fake, tmp_path, capsys):
    argv = [*BASE, "--sink", "fake://host/topic", "--dead-letter", "file:///dlq.jsonl", "--dry-run"]
    assert main(argv) == 0
    text = capsys.readouterr().out
    for needle in ("retail", "order_line", "fake://host/topic", "file:///dlq.jsonl", "checkpoint",
                   "fresh", "1,000 events"):  # fmt: skip
        assert needle in text, needle


def test_checkpoint_states_fresh_resume_finished_and_refused(tmp_path, capsys):
    ck = tmp_path / "ck.json"
    argv = [*BASE, "--checkpoint", str(ck)]
    assert main([*argv, "--dry-run", "--json"]) == 0
    assert plan_of(capsys)["checkpoint"]["state"] == "fresh"
    assert main([*argv, "--max-events", "400"]) == 0  # a real partial run
    capsys.readouterr()
    before = ck.read_bytes()
    assert main([*argv, "--max-events", "1000", "--dry-run", "--json"]) == 0
    c = plan_of(capsys)["checkpoint"]
    assert c["state"] == "resume" and c["offset"] == 400
    assert main([*argv, "--max-events", "1000"]) == 0  # finish it
    capsys.readouterr()
    finished = ck.read_bytes()
    assert main([*argv, "--max-events", "1000", "--dry-run", "--json"]) == 0
    assert plan_of(capsys)["checkpoint"]["state"] == "finished"
    assert ck.read_bytes() == finished and finished != before  # a dry run never writes it
    # another stream's checkpoint: refused, with the real run's message, and still exit 2
    assert main([*argv, "--seed", "99", "--dry-run", "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["plan"]["checkpoint"]["state"] == "refused"
    assert "belongs to a different stream" in captured.err
    assert ck.read_bytes() == finished
    # --fresh would ignore it
    assert main([*argv, "--seed", "99", "--fresh", "--dry-run", "--json"]) == 0
    assert plan_of(capsys)["checkpoint"]["state"] == "fresh"


# ---- the same message as the real run --------------------------------------------------------

REFUSED = [
    (["--table", "nope"], "unknown table"),
    (["--sink", "file"], "--sink file needs --output FILE"),
    (["--sink", "nope://x"], "unknown sink"),
    (["--sink", "console", "--dead-letter", "nope://x"], "unknown sink"),
    (["--burst", "0:1:2"], "--burst needs --realtime"),
    (["--realtime", "--ramp", "0:10:1:2", "--ramp", "5:10:2:3"], "ramps may not overlap"),
    (["--realtime", "--burst", "0:5:2", "--burst", "4:5:2"], "bursts may not overlap"),
    (["--realtime", "--daily-curve", "no-such"], "built-in curves are flat"),
    (["--arrivals", "poisson"], "--arrivals poisson needs --realtime"),
    (["--event-format", "avro", "--sink", "console"], "applies to kafka:// targets only"),
    (["--event-format", "avro", "--envelope", "cloudevents", "--sink", "kafka://b:9092/t"],
     "JSON only"),
    (["--out-of-order", "2"], "--out-of-order must be between 0 and 1"),
    (["--max-dead-letter", "3"], "--max-dead-letter needs --dead-letter"),
    (["--rows", "order_line=5"], "--rows needs --drift-plan"),
    (["--drift-plan", "missing-plan.json"], "missing-plan.json"),
    (["--anomaly-mutator", "x"], "--anomaly-mutator needs --anomaly-fraction"),
    (["--speed", "fast"], "speed"),
    (["--live-target", "retail", "--no-live-profile", "--live-profile", "p.json"],
     "--live-profile needs the stream profiler"),
]  # fmt: skip


@pytest.mark.parametrize(("extra", "message"), REFUSED)
def test_a_dry_run_is_refused_with_the_message_of_the_real_run(
    extra, message, capsys, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    assert main([*BASE, *extra]) == 2
    real = capsys.readouterr().err
    assert message in real
    assert main([*BASE, *extra, "--dry-run"]) == 2
    dry = capsys.readouterr().err
    assert dry == real
    assert listing(tmp_path) == []


def test_a_literal_secret_in_a_sink_config_is_refused_like_the_real_run(capsys):
    argv = [
        *BASE,
        "--sink",
        "kafka://b:9092/t",
        "--sink-config",
        "kafka.schema_registry_password=pw",
    ]
    assert main([*argv, "--event-format", "avro"]) == 2
    real = capsys.readouterr().err
    assert main([*argv, "--event-format", "avro", "--dry-run"]) == 2
    assert capsys.readouterr().err == real and "pw" not in real.replace("password", "")


def test_credential_references_are_checked_without_looking_up_a_vault(fake, capsys, monkeypatch):
    monkeypatch.setenv("DRY_TOKEN", "s3cret-value")
    argv = [*BASE, "--sink", "fake://h/t", "--dry-run", "--json"]
    assert main([*argv, "--sink-config", "fake.token=env://DRY_TOKEN"]) == 0
    out = capsys.readouterr().out
    plan = json.loads(out)["plan"]
    assert "s3cret-value" not in out  # the value is never printed
    assert plan["credentials"] == [{"option": "fake.token", "reference": "env://DRY_TOKEN",
                                    "scheme": "env", "checked": "resolved"}]  # fmt: skip
    # kv:// would need the network: only its syntax is checked
    assert main([*argv, "--sink-config", "fake.token=kv://vault/secret"]) == 0
    assert plan_of(capsys)["credentials"][0]["checked"] == "syntax"
    # the real run's errors: a missing variable, a malformed reference
    monkeypatch.delenv("DRY_TOKEN")
    for ref, text in [("env://DRY_TOKEN", "is not set"), ("env://", "needs a variable name"),
                      ("kv://vault", "kv:// needs VAULT/NAME")]:  # fmt: skip
        assert main([*argv, "--sink-config", f"fake.token={ref}"]) == 2, ref
        assert text in capsys.readouterr().err, ref
    assert fake.touched == []


# ---- the pacing summary ----------------------------------------------------------------------


def pacing(extra: list[str], capsys, events: int = 1000) -> dict[str, Any]:
    argv = ["emit", "retail", "--table", "order_line", "--max-events", str(events), *extra]
    assert main([*argv, "--dry-run", "--json"]) == 0
    return plan_of(capsys)["pacing"]


def test_pacing_summary_for_a_constant_rate(capsys):
    p = pacing(["--realtime", "--rate", "100"], capsys)
    assert p["mode"] == "realtime" and p["rate"] == 100.0 and p["arrivals"] == "constant"
    assert p["expected_seconds"] == pytest.approx(10.0)
    assert p["peak_events_per_minute"] == pytest.approx(6000.0)


def test_pacing_summary_with_a_burst_a_ramp_a_curve_and_a_cap(capsys, monkeypatch):
    p = pacing(["--realtime", "--rate", "100", "--burst", "0:2:5"], capsys)
    assert p["peak_events_per_minute"] == pytest.approx(30_000.0)
    assert p["expected_seconds"] == pytest.approx(2.0)  # 500 a second for 2 s is 1,000 events
    p = pacing(["--realtime", "--rate", "100", "--ramp", "0:10:1:3"], capsys, events=10_000)
    assert p["peak_events_per_minute"] == pytest.approx(18_000.0)
    assert p["ramps"] == 1
    monkeypatch.setattr("shape.streaming.emit.runtime._seconds_of_day", lambda: 10 * 3600.0)
    p = pacing(["--realtime", "--rate", "100", "--daily-curve", "business-hours"], capsys)
    assert p["curve"] == "business-hours" and p["curve_origin"] == 10 * 3600.0
    assert p["peak_events_per_minute"] == pytest.approx(6000.0)  # 10:00 is the busy part
    assert p["expected_seconds"] == pytest.approx(10.0)
    monkeypatch.setattr("shape.streaming.emit.runtime._seconds_of_day", lambda: 3 * 3600.0)
    p = pacing(["--realtime", "--rate", "100", "--daily-curve", "business-hours"], capsys)
    assert p["peak_events_per_minute"] == pytest.approx(900.0)  # 0.15 x 100 x 60 at night
    assert p["expected_seconds"] == pytest.approx(1000 / 15.0)
    p = pacing(["--realtime", "--rate", "100", "--burst", "0:2:5", "--max-rate", "200"], capsys)
    assert p["peak_events_per_minute"] == pytest.approx(12_000.0)  # the cap
    assert p["max_rate"] == 200.0
    assert p["expected_seconds"] == pytest.approx(5.0)  # 1,000 events at the cap of 200 a second


def test_pacing_summary_for_poisson_unpaced_speed_and_a_cap_alone(capsys):
    p = pacing(["--realtime", "--rate", "50", "--arrivals", "poisson"], capsys)
    assert p["arrivals"] == "poisson" and p["peak_events_per_minute"] == pytest.approx(3000.0)
    assert p["expected_seconds"] == pytest.approx(20.0)  # the mean: 1,000 events at 50 a second
    p = pacing([], capsys)
    assert p["mode"] == "unpaced" and p["expected_seconds"] is None
    p = pacing(["--max-rate", "250"], capsys)
    assert p["mode"] == "unpaced" and p["expected_seconds"] == pytest.approx(4.0)
    assert p["peak_events_per_minute"] == pytest.approx(15_000.0)
    p = pacing(["--speed", "60x"], capsys)
    assert p["mode"] == "speed" and p["speed"] == 60.0 and p["expected_seconds"] is None


# ---- drift plans ----------------------------------------------------------------------------


def test_a_drift_plan_in_the_plan(tmp_path, capsys):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA_DOC))
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(PLAN_DOC))
    argv = [
        "emit", str(schema), "--drift-plan", str(plan_file), "--rows", "customers=40",
        "--rows", "orders=120", "--realtime", "--day-seconds", "3", "--dry-run", "--json",
    ]  # fmt: skip
    assert main(argv) == 0
    plan = plan_of(capsys)
    d = plan["drift_plan"]
    import hashlib

    assert (
        d["path"] == str(plan_file)
        and d["sha256"] == hashlib.sha256(plan_file.read_bytes()).hexdigest()
    )
    assert len(d["days"]) == 5 and d["days"][0] == {
        "day": 0, "date": "2026-03-01", "events": 160, "active": []
    }  # fmt: skip
    assert d["days"][3]["active"] == ["nulls", "mix", "tiers"]
    assert plan["target"]["total_events"] == 800
    assert plan["pacing"]["mode"] == "day-seconds" and plan["pacing"]["day_seconds"] == 3.0
    assert plan["pacing"]["expected_seconds"] == pytest.approx(15.0)
    assert plan["pacing"]["peak_events_per_minute"] == pytest.approx(160 / 3 * 60)


def test_the_flags_dry_run_and_json_exist_for_stream_too(capsys):
    argv = ["stream", "retail", "-t", "order", "--max-events", "10", "--dry-run", "--json"]
    assert main(argv) == 0
    plan = plan_of(capsys)
    assert plan["command"] == "stream" and plan["target"]["tables"][0]["name"] == "order"
    assert plan["event_order"] == "event-time"
    assert (
        main(["emit", "retail", "--table", "order", "--max-events", "10", "--dry-run", "--json"])
        == 0
    )
    assert plan_of(capsys)["event_order"] == "row"


def test_a_dry_run_leaves_the_environment_as_it_found_it(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([*BASE, "--dry-run"]) == 0
    assert os.listdir(tmp_path) == []
