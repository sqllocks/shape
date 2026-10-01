"""Quarantine: layout, metadata, listing, and refusal of names that escape the root."""

from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.quality import QuarantineManager


def test_quarantine_file_copies_and_writes_metadata(tmp_path):
    src = tmp_path / "in" / "orders.csv"
    src.parent.mkdir()
    src.write_text("a\n1\n")
    q = tmp_path / "q"
    dest = QuarantineManager("retail").quarantine_file(src, q, "run1", "orphans", "referential")
    assert dest == q / "retail" / "run1" / "orders.csv"
    assert dest.read_text() == "a\n1\n" and src.exists()
    meta = json.loads((q / "retail" / "run1" / "orders.csv._quarantine_meta.json").read_text())
    assert meta["original_path"] == str(src.resolve())
    assert (meta["reason"], meta["gate_name"], meta["run_id"]) == ("orphans", "referential", "run1")
    assert meta["table_name"] is None and meta["timestamp"]


@pytest.mark.parametrize(
    ("fmt", "suffix"), [("parquet", ".parquet"), ("csv", ".csv"), ("jsonl", ".jsonl")]
)
def test_quarantine_table_formats(tmp_path, fmt, suffix):
    from datetime import datetime

    t = pa.table(
        {
            "id": [1, 2],
            "at": pa.array([datetime(2024, 1, 2), None], pa.timestamp("us")),
            "s": ["x", None],
        }
    )
    dest = QuarantineManager().quarantine_table(t, tmp_path, "r", "orders", "bad", "null", fmt)
    assert dest == tmp_path / "default" / "r" / f"orders{suffix}"
    if fmt == "parquet":
        assert pq.read_table(dest).equals(t)
    elif fmt == "csv":
        assert dest.read_text().splitlines()[0] == '"id","at","s"'
    else:
        rows = [json.loads(line) for line in dest.read_text().splitlines()]
        assert rows[0] == {"id": 1, "at": "2024-01-02T00:00:00", "s": "x"}
        assert rows[1]["at"] is None
    meta = json.loads(dest.with_name(dest.name + "._quarantine_meta.json").read_text())
    assert meta["table_name"] == "orders"
    assert meta["extra"] == {"rows": 2, "columns": 3, "format": fmt}


def test_list_and_report(tmp_path):
    qm = QuarantineManager("d")
    t = pa.table({"a": [1]})
    qm.quarantine_table(t, tmp_path, "r1", "one", "x", "gate_a")
    qm.quarantine_table(t, tmp_path, "r1", "two", "y", "gate_a")
    qm.quarantine_table(t, tmp_path, "r2", "three", "z", "gate_b")
    assert QuarantineManager().list_quarantined(tmp_path / "missing") == []
    items = qm.list_quarantined(tmp_path)
    assert len(items) == 3 and all(i["exists"] for i in items)
    rep = qm.get_quarantine_report(tmp_path, "r1")
    assert rep["total_quarantined"] == 2 and rep["gates_triggered"] == {"gate_a": 2}
    assert rep["domain"] == "d" and rep["run_id"] == "r1"
    (tmp_path / "d" / "r1" / "one.parquet").unlink()
    gone = [i for i in qm.list_quarantined(tmp_path) if i["table_name"] == "one"]
    assert gone[0]["exists"] is False
    (tmp_path / "d" / "r1" / "broken._quarantine_meta.json").write_text("{not json")
    assert len(qm.list_quarantined(tmp_path)) == 3


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "a\\b", "", ".", "x\0y", "/abs"])
def test_names_cannot_escape_the_quarantine_root(tmp_path, bad):
    t = pa.table({"a": [1]})
    root = tmp_path / "q"
    with pytest.raises(ValueError, match="invalid"):
        QuarantineManager(bad)
    with pytest.raises(ValueError, match="invalid run_id"):
        QuarantineManager().quarantine_table(t, root, bad, "t", "r")
    with pytest.raises(ValueError, match="invalid table name"):
        QuarantineManager().quarantine_table(t, root, "run", bad, "r")
    src = tmp_path / "f.csv"
    src.write_text("a\n")
    with pytest.raises(ValueError, match="invalid run_id"):
        QuarantineManager().quarantine_file(src, root, bad, "r")
    assert not root.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.csv"]


def test_unknown_format_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unsupported format"):
        QuarantineManager().quarantine_table(
            pa.table({"a": [1]}), tmp_path, "r", "t", "x", fmt="xls"
        )
