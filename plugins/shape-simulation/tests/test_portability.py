"""Simulator side files (manifests, stats) are byte-identical on every platform."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
from shape_simulation.clickstream_patterns import ClickstreamConfig, ClickstreamSimulator
from shape_simulation.file_drop import FileDropConfig, FileDropSimulator
from shape_simulation.scd2_file_drops import SCD2FileDropConfig, SCD2FileDropSimulator


def test_pattern_stats_json_has_lf_line_ends_on_windows(tmp_path: Path, windows_text_io) -> None:
    result = ClickstreamSimulator(ClickstreamConfig(users=20, duration_hours=1.0, seed=9)).run()
    result.write(tmp_path)
    raw = (tmp_path / "stats.json").read_bytes()
    assert b"\r" not in raw and json.loads(raw)["seed"] == 9


def test_file_drop_manifest_has_lf_line_ends_on_windows(
    tmp_path: Path, orders: pa.Table, windows_text_io
) -> None:
    cfg = FileDropConfig(
        domain="shop",
        base_path=str(tmp_path),
        date_range_start="2024-01-01",
        date_range_end="2024-01-03",
        lateness_enabled=False,
        seed=3,
    )
    res = FileDropSimulator({"orders": orders}, cfg).run()
    assert res.manifest_paths
    for path in res.manifest_paths:
        assert b"\r" not in path.read_bytes()


def test_scd2_manifest_has_lf_line_ends_on_windows(
    tmp_path: Path, orders: pa.Table, windows_text_io
) -> None:
    cfg = SCD2FileDropConfig(
        domain="shop",
        base_path=str(tmp_path),
        business_key_column="order_id",
        scd2_columns=["status"],
        num_delta_days=2,
        seed=9,
    )
    res = SCD2FileDropSimulator({"orders": orders}, cfg).run()
    manifests = [p for p in res.manifest_paths if p.suffix == ".json"]
    assert manifests
    for path in manifests:
        assert b"\r" not in path.read_bytes()
