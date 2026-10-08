"""The hybrid simulator: a file drop and a stream of the same tables, joined by one run id."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shape_simulation.file_drop import FileDropConfig
from shape_simulation.hybrid import CORRELATION_COLUMN, HybridConfig, HybridSimulator
from shape_simulation.stream_emit import StreamEmitConfig

from shape.streaming.emit import MemorySink
from shape.streaming.emit.formats import rows_of


def config(tmp_path: Path, **kw: object) -> HybridConfig:
    return HybridConfig(
        file_drop_config=FileDropConfig(
            domain="shop",
            base_path=str(tmp_path / "landing"),
            date_range_start="2024-01-01",
            date_range_end="2024-01-30",
            lateness_enabled=False,
        ),
        stream_config=StreamEmitConfig(
            sink_type="file", sink_connection={"path": str(tmp_path / "e.jsonl")}
        ),
        **kw,  # type: ignore[arg-type]
    )


def test_the_run_id_is_in_every_row_manifest_and_event(tmp_path: Path, orders: pa.Table) -> None:
    """Regression (HY-1): the baseline stamped only the rows; manifests and events had ids of
    their own."""
    sim = HybridSimulator({"orders": orders}, config(tmp_path))
    result = sim.run()
    run_id = result.correlation_id
    assert run_id == sim.correlation_id and result.link_strategy == "correlation_id"
    assert result.file_drop_result is not None and result.stream_result is not None
    rows = pa.concat_tables([pq.read_table(p) for p in result.file_drop_result.files_written])
    assert set(rows[CORRELATION_COLUMN].to_pylist()) == {run_id}
    manifests = {
        json.loads(p.read_text())["correlation_id"] for p in result.file_drop_result.manifest_paths
    }
    assert manifests == {run_id}
    events = [json.loads(line) for line in (tmp_path / "e.jsonl").read_text().splitlines()]
    assert len(events) == 600
    assert {e["correlationid"] for e in events} == {run_id}
    assert {e["data"][CORRELATION_COLUMN] for e in events} == {run_id}
    assert f"corr_id={run_id[:8]}" in repr(result)


def test_natural_keys_stamp_nothing(tmp_path: Path, orders: pa.Table) -> None:
    result = HybridSimulator(
        {"orders": orders}, config(tmp_path, link_strategy="natural_keys")
    ).run()
    assert result.file_drop_result is not None
    rows = pq.read_table(result.file_drop_result.files_written[0])
    assert CORRELATION_COLUMN not in rows.column_names
    events = [json.loads(line) for line in (tmp_path / "e.jsonl").read_text().splitlines()]
    assert CORRELATION_COLUMN not in events[0]["data"]
    assert len({e["correlationid"] for e in events}) == 1  # one id per emitter, not the run id


def test_tables_are_routed_to_the_two_paths(
    tmp_path: Path, orders: pa.Table, products: pa.Table
) -> None:
    cfg = config(tmp_path, stream_tables=["orders"], batch_tables=["products"], concurrent=True)
    result = HybridSimulator({"orders": orders, "products": products}, cfg).run()
    assert list(result.file_drop_result.stats) == ["products"]  # type: ignore[union-attr]
    assert result.stream_result.topics_used == {"orders"}  # type: ignore[union-attr]
    assert result.stream_result.events_sent == 600  # type: ignore[union-attr]


def test_an_unknown_table_name_is_an_error_not_a_silent_skip(
    tmp_path: Path, orders: pa.Table
) -> None:
    """Regression (HY-2)."""
    with pytest.raises(ValueError, match="'ordrs'"):
        HybridSimulator(
            {"orders": orders}, config(tmp_path, stream_tables=["orders", "ordrs"])
        ).run()


def test_a_sink_and_a_missing_side(tmp_path: Path, orders: pa.Table) -> None:
    sink = MemorySink()
    cfg = config(tmp_path, batch_tables=[], stream_tables=["orders"])
    cfg.stream_config = StreamEmitConfig(max_events=5)
    HybridSimulator({"orders": orders}, cfg, sink).run()
    assert [e["shapeseq"] for b in sink.batches for e in rows_of(b)] == [0, 1, 2, 3, 4]
    assert "batch_files=" in repr(HybridSimulator({"orders": orders}, cfg, sink).run())


def test_a_link_strategy_that_does_not_exist_is_refused() -> None:
    with pytest.raises(ValueError, match="link_strategy"):
        HybridConfig(link_strategy="guess")
