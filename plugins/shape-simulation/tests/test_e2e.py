"""End to end: a domain's generated tables through every simulator and through ``shape simulate``.

The retail domain comes from the ``sqllocks-shape-domains`` plugin; the tests are skipped when it
is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shape_simulation.cli import SimulateCommand, generate_tables
from shape_simulation.file_drop import FileDropConfig, FileDropSimulator
from shape_simulation.hybrid import HybridConfig, HybridSimulator
from shape_simulation.scd2_file_drops import SCD2FileDropConfig, SCD2FileDropSimulator
from shape_simulation.state_machine import WorkflowConfig, WorkflowSimulator, get_preset_workflow
from shape_simulation.stream_emit import StreamEmitConfig, StreamEmitter

from shape.cli.main import main
from shape.plugins import kit

pytest.importorskip("shape_domains")


@pytest.fixture(scope="module")
def retail() -> dict[str, pa.Table]:
    return generate_tables("retail", "small", 42)


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_domains_tables_through_the_file_drop(
    tmp_path: Path, retail: dict[str, pa.Table]
) -> None:
    cfg = FileDropConfig(
        domain="retail",
        base_path=str(tmp_path),
        date_range_start="2022-01-01",
        date_range_end="2026-02-28",
        entities=["order", "return"],
        lateness_probability=0.1,
        seed=42,
    )
    res = FileDropSimulator(retail, cfg).run()
    assert set(res.stats) == {"order", "return"}
    for entity in ("order", "return"):
        files = [p for p in res.files_written if f"/{entity}/" in p.as_posix()]
        ids = pa.concat_tables([pq.read_table(p) for p in files])[f"{entity}_id"].to_pylist()
        assert sorted(ids) == sorted(retail[entity][f"{entity}_id"].to_pylist())  # every row, once


def test_a_domains_tables_through_the_stream(tmp_path: Path, retail: dict[str, pa.Table]) -> None:
    path = tmp_path / "events.jsonl"
    cfg = StreamEmitConfig(
        sink_type="file",
        sink_connection={"path": str(path)},
        out_of_order_probability=0.05,
        seed=42,
    )
    result = StreamEmitter({"order": retail["order"]}, cfg).emit()
    events = read_lines(path)
    assert result.events_sent == len(events) == retail["order"].num_rows
    assert {e["shapeseq"] for e in events} == set(range(retail["order"].num_rows))
    assert events[0]["data"]["_shape_event_time"] == events[0]["data"]["order_date"]


def test_a_domains_tables_through_the_scd2_drop(
    tmp_path: Path, retail: dict[str, pa.Table]
) -> None:
    cfg = SCD2FileDropConfig(
        domain="retail",
        base_path=str(tmp_path),
        business_key_column="customer_id",
        scd2_columns=["loyalty_tier", "email"],
        num_delta_days=5,
        seed=42,
    )
    res = SCD2FileDropSimulator({"customer": retail["customer"]}, cfg).run()
    snapshot = pq.read_table(res.initial_load_path)
    assert snapshot.num_rows == retail["customer"].num_rows and len(res.delta_paths) == 5
    assert res.stats["total_updates"] > 0 and res.stats["total_new"] > 0


def test_a_domains_tables_through_the_hybrid(tmp_path: Path, retail: dict[str, pa.Table]) -> None:
    cfg = HybridConfig(
        file_drop_config=FileDropConfig(
            domain="retail",
            base_path=str(tmp_path / "landing"),
            date_range_start="2022-01-01",
            date_range_end="2025-12-31",
            entities=["order"],
            seed=42,
        ),
        stream_config=StreamEmitConfig(
            sink_type="file", sink_connection={"path": str(tmp_path / "e.jsonl")}, seed=42
        ),
        stream_tables=["return"],
        batch_tables=["order"],
    )
    result = HybridSimulator(retail, cfg).run()
    assert (
        result.stream_result is not None
        and result.stream_result.events_sent == retail["return"].num_rows
    )
    assert {e["correlationid"] for e in read_lines(tmp_path / "e.jsonl")} == {result.correlation_id}


def test_the_workflow_simulator_runs_every_preset() -> None:
    for preset in ("order_fulfillment", "support_ticket", "employee_onboarding"):
        states, transitions = get_preset_workflow(preset)
        out = WorkflowSimulator(
            WorkflowConfig(states=states, transitions=transitions, entity_count=50, seed=1)
        ).run()
        assert out.entity_summary.num_rows == 50 and out.events.num_rows >= 50


# ---- the command line ---------------------------------------------------------------------


def run(argv: list[str]) -> int:
    return int(main(["simulate", *argv]))


def test_cli_file_drop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(
        [
            "file-drop",
            "retail",
            "--scale",
            "small",
            "--from",
            "2022-01-01",
            "--to",
            "2022-12-31",
            "--entity",
            "order",
            "--format",
            "parquet,csv",
            "--duplicates",
            "0.05",
            "--late",
            "0.2",
            "--backfill",
            "3",
            "--restate",
            "0.2",
            "-o",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "file-drop:" in out
    stats = json.loads(out.splitlines()[-1])
    assert stats["order"]["formats"] == ["parquet", "csv"] and stats["order"]["rows_written"] > 0
    assert list(tmp_path.rglob("_manifest.json")) and list(tmp_path.rglob("*.csv"))
    run(
        [
            "file-drop",
            "retail",
            "--scale",
            "small",
            "--from",
            "2022-01-01",
            "--to",
            "2022-01-31",
            "--entity",
            "order",
            "--multi-file",
            "2",
            "--no-manifest",
            "--no-done-flag",
            "--late",
            "0",
            "-o",
            str(tmp_path / "multi"),
        ]
    )
    assert not list((tmp_path / "multi").rglob("_manifest.json"))


def test_cli_scd2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(
        [
            "scd2",
            "retail",
            "--scale",
            "small",
            "--entity",
            "customer",
            "--key",
            "customer_id",
            "--track",
            "loyalty_tier",
            "--days",
            "3",
            "-o",
            str(tmp_path),
        ]
    )
    assert code == 0 and "3 delta files" in capsys.readouterr().out
    assert len(list(tmp_path.rglob("customer_delta.parquet"))) == 3


def test_cli_stream_to_a_file_and_the_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "e.jsonl"
    assert (
        run(
            [
                "stream",
                "retail",
                "--scale",
                "small",
                "--table",
                "return",
                "--max-events",
                "20",
                "--replay",
                "0.5",
                "--out-of-order",
                "0.1",
                "--topic",
                "returns",
                "--sink",
                "file",
                "-o",
                str(path),
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "events and" in err and "returns" in err
    events = read_lines(path)
    assert {e["topic"] for e in events} == {"returns"} and sum(
        not e.get("replay") for e in events
    ) == 20
    assert (
        run(["stream", "retail", "--scale", "small", "--table", "return", "--max-events", "3"]) == 0
    )
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["shapetable"] == "return"


def test_cli_hybrid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        run(
            [
                "hybrid",
                "retail",
                "--scale",
                "small",
                "--from",
                "2022-01-01",
                "--to",
                "2022-12-31",
                "--batch-table",
                "order",
                "--stream-table",
                "return",
                "--concurrent",
                "-o",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert "hybrid: run" in capsys.readouterr().out
    run_id = {e["correlationid"] for e in read_lines(tmp_path / "events.jsonl")}
    assert len(run_id) == 1
    manifests = {
        json.loads(p.read_text())["correlation_id"]
        for p in (tmp_path / "landing").rglob("_manifest.json")
    }
    assert manifests == run_id


def test_cli_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        run(
            [
                "workflow",
                "--preset",
                "support_ticket",
                "--entities",
                "30",
                "--format",
                "csv",
                "-o",
                str(tmp_path),
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["stats"]["total_entities"] == 30 and sum(doc["state_distribution"].values()) == 30
    assert (tmp_path / "events.csv").exists() and (tmp_path / "entity_summary.csv").exists()


def test_cli_bad_input_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        run(
            [
                "file-drop",
                "retail",
                "--from",
                "2022-02-01",
                "--to",
                "2022-01-01",
                "-o",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "before" in capsys.readouterr().err
    assert (
        run(
            [
                "scd2",
                "retail",
                "--scale",
                "small",
                "--entity",
                "nope",
                "--key",
                "id",
                "-o",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert run(["stream", "retail", "--scale", "small", "--table", "nope"]) == 2
    assert run(["stream", "retail", "--scale", "small", "--sink", "eventstream"]) == 2
    assert run(["file-drop", "no_such_domain", "--from", "2022-01-01", "--to", "2022-01-02"]) == 2


def test_the_command_conforms_to_the_plugin_api(tmp_path: Path) -> None:
    kit.check_command(
        SimulateCommand(),
        argv=["workflow", "--preset", "order_fulfillment", "--entities", "5", "-o", str(tmp_path)],
    )
