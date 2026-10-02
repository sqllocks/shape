"""End to end: the simulators through the command line, the generation engine, the stream
emitters and chaos, the way a user meets them."""

from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.api import generate
from shape.chaos import inject_anomalies
from shape.cli.main import main
from shape.generation.domains import domain_names
from shape.plugins import kit
from shape.streaming.emit import FileSink, MemorySink, read_events
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE, FIELD_TIME
from shape_simulation.clickstream_patterns import ClickstreamConfig, ClickstreamSimulator
from shape_simulation.financial_patterns import FinancialStreamConfig, FinancialStreamSimulator
from shape_simulation.iot_patterns import IoTTelemetryConfig, IoTTelemetrySimulator
from shape_simulation.pulse_patterns import PulseDemandConfig, PulseDemandSimulator

BASE_DOMAINS = {"financial": "financial", "iot": "iot", "pulse": "pulse"}


def cli(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def read_dir(path, fmt="parquet"):
    return {p.stem: pq.read_table(p) for p in sorted(path.glob(f"*.{fmt}"))}


def test_plugin_is_installed_and_conforms(capsys):
    code, out, _ = cli(capsys, "plugins", "list", "--group", "shape.commands")
    assert code == 0 and "simulate" in out
    kit.check_installed("sqllocks-shape-simulation")
    code, out, _ = cli(capsys, "simulate", "--help")
    assert code == 0 and "operational-log" in out


def test_clickstream_command_writes_reproducible_tables(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    for out in (a, b):
        code, text, _ = cli(capsys, "simulate", "clickstream", "--set", "users=80", "--set", "duration_hours=6", "--seed", 9, "-o", out)
        assert code == 0 and "sessions:" in text and "page_views:" in text
    ta, tb = read_dir(a), read_dir(b)
    assert set(ta) == {"sessions", "page_views", "funnels"} and all(ta[k].equals(tb[k]) for k in ta)
    stats = json.loads((a / "stats.json").read_text())
    assert stats["total_sessions"] == ta["sessions"].num_rows and stats["seed"] == 9
    api = ClickstreamSimulator(ClickstreamConfig(users=80, duration_hours=6.0, seed=9)).run()
    assert api.sessions.equals(ta["sessions"]) and api.page_views.equals(ta["page_views"])


@pytest.mark.parametrize("fmt", ["csv", "jsonl"])
def test_other_file_formats(tmp_path, capsys, fmt):
    code, out, _ = cli(capsys, "simulate", "operational-log", "--set", "duration_hours=2", "--set", "events_per_hour=20", "-o", tmp_path, "--format", fmt, "--json")
    assert code == 0
    summary = json.loads(out)
    assert summary["pattern"] == "operational-log" and set(summary["tables"]) == {"logs", "traces", "service_health"}
    for name, path in summary["files"].items():
        assert path.endswith(f"{name}.{fmt}") and (tmp_path / f"{name}.{fmt}").stat().st_size > 0
    if fmt == "jsonl":
        lines = (tmp_path / "logs.jsonl").read_text().splitlines()
        assert len(lines) == summary["tables"]["logs"] and json.loads(lines[0])["service"]


def test_events_are_flat_stream_events_with_idempotency_keys(capsys):
    code, out, err = cli(capsys, "simulate", "operational-log", "--set", "duration_hours=2", "--set", "events_per_hour=15", "--events", "service_health")
    assert code == 0 and err == ""
    events = [json.loads(line) for line in out.splitlines()]
    assert len(events) == 5 and [e[FIELD_SEQ] for e in events] == list(range(5))
    assert {e[FIELD_TABLE] for e in events} == {"service_health"} and events[0]["service"] == "api-gateway"
    code, out, _ = cli(capsys, "simulate", "clickstream", "--set", "users=20", "--events", "funnels")
    funnel = [json.loads(line) for line in out.splitlines()]
    assert funnel and all(FIELD_TIME in e for e in funnel)
    assert len({(e[FIELD_TABLE], e[FIELD_SEQ]) for e in funnel}) == len(funnel)


def test_command_errors_exit_2(capsys, tmp_path):
    for argv in (
        ["simulate", "clickstream", "--set", "nope=1"],
        ["simulate", "clickstream", "--set", "users"],
        ["simulate", "clickstream", "--domain", "iot"],
        ["simulate", "operational-log", "--events", "missing"],
        ["simulate", "iot", "--domain", str(tmp_path / "absent.json")],
    ):
        code, _, err = cli(capsys, *argv)
        assert code == 2 and err.startswith("shape: error:"), argv
    code, _, err = cli(capsys, "simulate", "not-a-pattern")
    assert code == 2 and "invalid choice" in err


@pytest.mark.parametrize("kind", ["financial", "iot", "pulse"])
def test_engine_tables_through_the_command_match_the_api(kind, schema_files, tmp_path, capsys):
    code, out, _ = cli(capsys, "simulate", kind, "--domain", schema_files[kind], "--seed", 7, "-o", tmp_path, "--json")
    assert code == 0
    files = read_dir(tmp_path)
    base = generate(json.loads(schema_files[kind].read_text()), seed=7).tables
    configured = {
        "financial": lambda: FinancialStreamSimulator(tables=base, config=FinancialStreamConfig(seed=7)),
        "iot": lambda: IoTTelemetrySimulator(tables=base, config=IoTTelemetryConfig(seed=7)),
        "pulse": lambda: PulseDemandSimulator(base, PulseDemandConfig(seed=7)),
    }
    result = configured[kind]().run()
    expected = result.table_map()
    assert set(files) == set(expected)
    for name, table in expected.items():
        assert files[name].num_rows == table.num_rows, name
    assert json.loads(out)["stats"] == json.loads(json.dumps(result.stats, default=str))


def test_the_shipped_domains_where_installed(tmp_path, capsys):
    """The same command on the domains that ship with Shape (those installed here)."""
    installed = set(domain_names())
    checked = []
    for kind, domain in BASE_DOMAINS.items():
        if domain not in installed:
            continue
        out = tmp_path / kind
        code, text, _ = cli(capsys, "simulate", kind, "--domain", domain, "--scale", "small", "--seed", 3, "-o", out, "--json")
        assert code == 0, text
        summary = json.loads(text)
        assert all(rows >= 0 for rows in summary["tables"].values()) and summary["tables"]
        if kind == "iot":
            fleet = read_dir(out)["fleet_status"]
            assert fleet.column("last_reading_at").null_count < fleet.num_rows  # sensors resolved
        if kind == "financial":
            assert sum(read_dir(out)["settlements"].column("transaction_count").to_pylist()) > 0
        checked.append(kind)
    assert set(checked) == {k for k, d in BASE_DOMAINS.items() if d in installed}


def test_result_events_through_the_emit_runtime_sinks(tmp_path):
    result = ClickstreamSimulator(ClickstreamConfig(users=40, seed=2)).run()
    batch = result.events("sessions")
    assert batch.num_rows == result.sessions.num_rows and batch.schema.names[-3:] == [FIELD_TABLE, FIELD_SEQ, FIELD_TIME]
    mem = MemorySink()
    mem.send(batch)
    assert mem.num_events == result.sessions.num_rows
    sink = FileSink(tmp_path / "events.jsonl")
    sink.send(batch)
    sink.send(batch)  # at-least-once: the same events arrive twice
    sink.close()
    every = list(read_events(str(tmp_path / "events.jsonl")))
    unique = list(read_events(str(tmp_path / "events.jsonl"), dedupe=True))
    assert len(every) == 2 * len(unique) == 2 * result.sessions.num_rows
    assert [e[FIELD_SEQ] for e in unique] == list(range(result.sessions.num_rows))
    assert unique[0]["session_id"] == result.sessions.column("session_id")[0].as_py()
    later = result.events("sessions", seq_start=1000)
    assert later.column(FIELD_SEQ)[0].as_py() == 1000
    with pytest.raises(KeyError):
        result.events("nope")


def test_chaos_on_simulated_events_keeps_the_stream_keys():
    result = ClickstreamSimulator(ClickstreamConfig(users=60, seed=4)).run()
    batch = result.events("page_views")
    hit = inject_anomalies(batch, fraction=0.2, seed=1, protect=(FIELD_TABLE, FIELD_SEQ, FIELD_TIME))
    assert 0.1 < len(hit.rows) / batch.num_rows < 0.3
    assert hit.batch.column(FIELD_SEQ).equals(batch.column(FIELD_SEQ)) and hit.batch.column(FIELD_TABLE).equals(batch.column(FIELD_TABLE))
    assert not hit.batch.equals(batch)
    again = inject_anomalies(batch, fraction=0.2, seed=1, protect=(FIELD_TABLE, FIELD_SEQ, FIELD_TIME))
    assert again.batch.equals(hit.batch) and again.rows == hit.rows


def test_write_returns_paths_and_rejects_unknown_formats(tmp_path):
    result = ClickstreamSimulator(ClickstreamConfig(users=10, seed=1)).run()
    paths = result.write(tmp_path / "out", "parquet")
    assert set(paths) == {"sessions", "page_views", "funnels"} and all(p.exists() for p in paths.values())
    assert pq.read_table(paths["sessions"]).equals(result.sessions)
    with pytest.raises(ValueError, match="unknown format"):
        result.write(tmp_path, "xml")
    assert isinstance(result.sessions, pa.Table)
