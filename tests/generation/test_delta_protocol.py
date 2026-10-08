"""Issue #69: Shape's own Delta writes stay at reader version 1 / writer version 2, with no table
features and no table configuration, so that every Fabric engine can read them."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

deltalake = pytest.importorskip("deltalake")

from shape.builtins.sinks.delta import DeltaSink  # noqa: E402


def _actions(table_dir: Path) -> list[dict]:
    out = []
    for log in sorted((table_dir / "_delta_log").glob("*.json")):
        out += [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return out


def _write(tmp_path, **options) -> Path:
    batch = pa.record_batch(
        {"id": [1, 2, 3], "name": ["a", "b", "c"], "ts": pa.array([0, 1, 2], pa.timestamp("us"))}
    )
    DeltaSink().write(str(tmp_path), "t", [batch], **options)
    return tmp_path / "t"


@pytest.mark.parametrize("options", [{}, {"partition_by": ["name"]}, {"mode": "append"}])
def test_protocol_is_reader_1_writer_2_with_no_features(tmp_path, options):
    actions = _actions(_write(tmp_path, **options))
    protocols = [a["protocol"] for a in actions if "protocol" in a]
    assert protocols, "no protocol action was written"
    for protocol in protocols:
        assert protocol["minReaderVersion"] == 1
        assert protocol["minWriterVersion"] == 2
        assert not protocol.get("readerFeatures")
        assert not protocol.get("writerFeatures")


@pytest.mark.parametrize("options", [{}, {"partition_by": ["name"]}])
def test_table_configuration_is_empty(tmp_path, options):
    metadata = [a["metaData"] for a in _actions(_write(tmp_path, **options)) if "metaData" in a]
    assert metadata
    for m in metadata:
        assert m.get("configuration", {}) == {}


def test_a_second_write_keeps_the_protocol(tmp_path):
    _write(tmp_path)
    table_dir = _write(tmp_path, mode="append")
    versions = [a["protocol"] for a in _actions(table_dir) if "protocol" in a]
    assert {(p["minReaderVersion"], p["minWriterVersion"]) for p in versions} == {(1, 2)}
    assert deltalake.DeltaTable(str(table_dir)).protocol().min_writer_version == 2
