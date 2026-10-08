"""A stream into files, Delta and database tables (``--to``), readable while it runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fsspec
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.generation.output import TargetOptions
from shape.streaming.emit import EmitConfig, EmitRunner, EventPlan
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE
from shape.streaming.emit.tables import SYNTHETIC_METADATA, TableEventSink

from .conftest import make_engine

URI = "abfss://landing@acct.dfs.core.windows.net/stream"
BASE = ["emit", "retail", "--scale", "small", "--seed", "3", "--table", "customer"]


@pytest.fixture
def memfs(monkeypatch: pytest.MonkeyPatch) -> Any:
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    from shape.builtins.sources import azure

    monkeypatch.setattr(azure, "_filesystem", lambda loc, options: fs)
    return fs


def parts(fs: Any) -> list[str]:
    return sorted(
        p.lstrip("/")
        for p in fs.find("landing/stream")
        if "/_shape_tmp/" not in p and not p.rsplit("/", 1)[-1].startswith("_")
    )


def total_rows(fs: Any) -> int:
    return sum(pq.read_table(fs.open(p, "rb")).num_rows for p in parts(fs))


def test_cli_streams_into_rolling_dated_files(memfs: Any) -> None:
    code = main(
        [
            *BASE,
            "--max-events",
            "1000",
            "--to",
            URI,
            "--roll-rows",
            "300",
            "--batch-date",
            "2026-10-02",
            "--checkpoint-every",
            "100000",
            "--checkpoint-seconds",
            "1000",
        ]
    )
    assert code == 0
    files = parts(memfs)
    assert len(files) == 4 and all("ingest_date=2026-10-02" in f for f in files)
    table = pq.read_table(memfs.open(files[0], "rb"))
    assert FIELD_SEQ in table.column_names and FIELD_TABLE not in table.column_names
    assert table.schema.metadata[b"shape_synthetic"] == b"true"
    assert total_rows(memfs) == 1000


def test_synthetic_marker_can_be_turned_off(memfs: Any) -> None:
    code = main(
        [
            *BASE,
            "--max-events",
            "50",
            "--to",
            URI,
            "--no-synthetic-header",
            "--batch-date",
            "2026-10-02",
        ]
    )
    assert code == 0
    meta = pq.read_table(memfs.open(parts(memfs)[0], "rb")).schema.metadata or {}
    assert b"shape_synthetic" not in meta


def test_rows_are_readable_while_the_stream_runs(memfs: Any) -> None:
    engine = make_engine()
    plan = EventPlan(engine, tables=["customer"])
    sink = TableEventSink(
        URI, options=TargetOptions(fmt="parquet", batch_date="2026-10-02", roll_seconds=3600)
    )
    seen: list[int] = []

    class Watch:
        def send(self, batch: Any) -> None:
            sink.send(batch)

        def flush(self) -> None:
            sink.flush()
            seen.append(total_rows(memfs))  # what a reader sees at each checkpoint

        def close(self) -> None:
            sink.close()

    EmitRunner(
        plan, Watch(), EmitConfig(max_events=1000, batch_events=100, checkpoint_every=250)
    ).run()
    assert len(seen) >= 3 and seen == sorted(seen) and seen[0] > 0 and seen[0] < 1000
    assert total_rows(memfs) == 1000
    assert not [p for p in memfs.find("landing/stream") if "/_shape_tmp/" in p]


def test_no_partial_file_is_ever_visible(memfs: Any) -> None:
    plan = EventPlan(make_engine(), tables=["customer"])
    sink = TableEventSink(
        URI, options=TargetOptions(fmt="parquet", batch_date="2026-10-02", roll_rows=120)
    )
    problems: list[BaseException] = []

    class Watch:
        def send(self, batch: Any) -> None:
            sink.send(batch)
            for p in parts(memfs):
                try:
                    pq.read_table(memfs.open(p, "rb"))
                except BaseException as exc:
                    problems.append(exc)

        def flush(self) -> None: ...
        def close(self) -> None:
            sink.close()

    EmitRunner(plan, Watch(), EmitConfig(max_events=1000, batch_events=50)).run()
    assert problems == [] and total_rows(memfs) == 1000


def test_a_resumed_run_appends_new_files(memfs: Any, tmp_path: Path) -> None:
    ckpt = tmp_path / "c.json"
    args = [
        *BASE,
        "--to",
        URI,
        "--roll-rows",
        "200",
        "--batch-date",
        "2026-10-02",
        "--checkpoint",
        str(ckpt),
        "--checkpoint-every",
        "100",
    ]
    assert main([*args, "--max-events", "400"]) == 0
    first = parts(memfs)
    assert main([*args, "--max-events", "800"]) == 0  # continues at event 400
    second = parts(memfs)
    assert set(first) < set(second)
    assert total_rows(memfs) == 800


def test_duplicates_land_as_rows_a_consumer_removes_by_seq(memfs: Any) -> None:
    assert (
        main(
            [
                *BASE,
                "--max-events",
                "600",
                "--to",
                URI,
                "--duplicate-fraction",
                "0.1",
                "--batch-date",
                "2026-10-02",
            ]
        )
        == 0
    )
    seqs = [
        s
        for p in parts(memfs)
        for s in pq.read_table(memfs.open(p, "rb")).column(FIELD_SEQ).to_pylist()
    ]
    assert len(seqs) > 600 and sorted(set(seqs)) == list(range(600))


def test_poison_is_refused_for_a_table_sink(memfs: Any, capsys: Any) -> None:
    code = main([*BASE, "--max-events", "10", "--to", URI, "--poison-fraction", "0.5"])
    assert code != 0 and "JSON text" in capsys.readouterr().err


def test_delta_commits_at_every_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deltalake = pytest.importorskip("deltalake")
    from shape.builtins.sinks import delta

    monkeypatch.setattr(
        delta, "_location", lambda uri, table, options: (str(tmp_path / table), None)
    )
    plan = EventPlan(make_engine(), tables=["customer"])
    sink = TableEventSink("delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables")
    versions: list[int] = []

    class Watch:
        def send(self, batch: Any) -> None:
            sink.send(batch)

        def flush(self) -> None:
            sink.flush()
            if (tmp_path / "customer" / "_delta_log").exists():
                versions.append(
                    deltalake.DeltaTable(str(tmp_path / "customer")).to_pyarrow_table().num_rows
                )

        def close(self) -> None:
            sink.close()

    EmitRunner(
        plan, Watch(), EmitConfig(max_events=900, batch_events=100, checkpoint_every=300)
    ).run()
    assert versions[:3] == [300, 600, 900] or versions[0] >= 300
    assert deltalake.DeltaTable(str(tmp_path / "customer")).to_pyarrow_table().num_rows == 900


def test_a_database_commits_while_the_stream_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("shape_databases")
    from shape_databases import PostgresSink
    from shape_databases.testing import FakeServer

    server = FakeServer("postgres")
    monkeypatch.setattr(PostgresSink, "default_connect", lambda self, **p: server.connect(**p))
    plan = EventPlan(make_engine(), tables=["customer"])
    sink = TableEventSink("postgresql://shape@db.example/shape")
    visible: list[int] = []

    class Watch:
        def send(self, batch: Any) -> None:
            sink.send(batch)

        def flush(self) -> None:
            sink.flush()
            visible.append(len(server.committed_rows("customer")))

        def close(self) -> None:
            sink.close()

    EmitRunner(
        plan, Watch(), EmitConfig(max_events=500, batch_events=100, checkpoint_every=200)
    ).run()
    assert visible and visible[0] > 0 and visible == sorted(visible)
    assert len(server.committed_rows("customer")) == 500


def test_metadata_constant() -> None:
    assert SYNTHETIC_METADATA == {b"shape_synthetic": b"true"}
