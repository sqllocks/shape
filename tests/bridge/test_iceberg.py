"""W9-16 bridge write/read with local Iceberg and scrubbed credential failures."""

import json

import pytest

import shape

pytestmark = pytest.mark.zero_network


def test_wanted_5_bridge_write_read_and_secret_errors(api12, tmp_path):
    doc = {
        "schema_version": 1,
        "model": {"name": "demo", "seed": 7},
        "tables": {
            "items": {
                "name": "items",
                "columns": {
                    "id": {
                        "name": "id",
                        "type": "integer",
                        "nullable": False,
                        "null_rate": 0.0,
                        "generator": {"strategy": "sequence", "start": 1},
                    }
                },
            }
        },
        "generation": {"scale": "small", "scales": {"small": {"items": 8}}},
    }

    path = tmp_path / "schema.json"
    path.write_text(json.dumps(doc))
    target = (tmp_path / "warehouse/ns/items").as_uri().replace("file:", "iceberg+file:", 1)
    result = api12.ok("generate", domain=str(path), to=target)
    assert result["total_rows"] == 8
    out = api12.ok("profile", source=target, output=str(tmp_path / "saved.shape"))
    assert shape.load(out["path"]).provenance["table"] == "ns.items"
    response = api12.call("profile", source="iceberg://user:private-token@catalog/ns/items")
    assert not response["ok"]
    assert "private-token" not in json.dumps(response)


def test_wanted_5_bridge_snapshot_and_multitable_boundary(api12, tmp_path):
    import pyarrow as pa

    from shape.builtins.sinks.iceberg import IcebergSink
    from shape.builtins.sources.iceberg import IcebergSource

    target = (tmp_path / "warehouse/ns/items").as_uri().replace("file:", "iceberg+file:", 1)
    IcebergSink().write(target, "items", pa.table({"id": [1]}).to_batches())
    snapshot = IcebergSource()._table(target, {}).current_snapshot().snapshot_id
    IcebergSink().write(target, "items", pa.table({"id": [2]}).to_batches(), mode="append")
    out = api12.ok("profile", source=target, source_options={"snapshot_id": snapshot})
    assert shape.load(out["path"]).tables["items"]["row_count"] == 1
    from scale_schemas import plain_doc

    path = tmp_path / "multiple.json"
    path.write_text(json.dumps(plain_doc({"customer": 1, "order": 1, "order_line": 1})))
    response = api12.call("generate", domain=str(path), to=target)
    assert not response["ok"]
    assert IcebergSource()._table(target, {}).current_snapshot().snapshot_id != snapshot
