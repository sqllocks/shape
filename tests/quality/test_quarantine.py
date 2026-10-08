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


# -- HUNT2-quality ----------------------------------------------------------------------------


def test_a_second_file_with_the_same_name_does_not_replace_the_first(tmp_path):
    """#605: the second copy and its metadata overwrote the first."""
    import json

    from shape.quality.quarantine import QuarantineManager

    for d, text in (("a", "AAA"), ("b", "BBB")):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.csv").write_text(text)
    m = QuarantineManager()
    first = m.quarantine_file(tmp_path / "a" / "x.csv", tmp_path / "q", "r1", "bad a", "g")
    second = m.quarantine_file(tmp_path / "b" / "x.csv", tmp_path / "q", "r1", "bad b", "g")
    assert first != second
    assert first.read_text() == "AAA" and second.read_text() == "BBB"
    items = m.list_quarantined(tmp_path / "q")
    assert sorted(i["reason"] for i in items) == ["bad a", "bad b"]
    assert all(i["exists"] for i in items)
    assert json.loads(open(f"{first}._quarantine_meta.json").read())["reason"] == "bad a"
    # a third one and the plain first name stay stable
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "x.csv").write_text("CCC")
    third = m.quarantine_file(tmp_path / "c" / "x.csv", tmp_path / "q", "r1", "bad c", "g")
    assert third not in (first, second) and third.read_text() == "CCC"
    assert first.name == "x.csv"


def test_a_table_quarantined_twice_in_one_run_keeps_both(tmp_path):
    import pyarrow as pa

    from shape.quality.quarantine import QuarantineManager

    m = QuarantineManager()
    t1 = m.quarantine_table(pa.table({"a": [1]}), tmp_path, "r", "t", "one", "g1")
    t2 = m.quarantine_table(pa.table({"a": [1, 2]}), tmp_path, "r", "t", "two", "g2")
    assert t1 != t2
    rep = m.get_quarantine_report(tmp_path, "r")
    assert rep["total_quarantined"] == 2 and rep["gates_triggered"] == {"g1": 1, "g2": 1}
    # a different run id starts clean
    other = m.quarantine_table(pa.table({"a": [1]}), tmp_path, "r2", "t", "one", "g1")
    assert other.name == "t.parquet"


def test_jsonl_quarantine_is_strict_json(tmp_path):
    """#606: NaN and Infinity are not JSON."""
    import json

    import pyarrow as pa

    from shape.quality.quarantine import QuarantineManager

    t = pa.table({"v": [float("nan"), float("inf"), float("-inf"), 1.5]})
    p = QuarantineManager().quarantine_table(t, tmp_path, "r", "t", "why", "g", "jsonl")
    rows = [json.loads(line, parse_constant=_reject) for line in p.read_text().splitlines()]
    assert [r["v"] for r in rows] == [None, None, None, 1.5]


def _reject(name):
    raise AssertionError(f"{name} is not valid JSON")


def test_list_quarantined_skips_a_metadata_file_that_is_not_an_object(tmp_path):
    """#607: one bad metadata file hid every other item."""
    import pyarrow as pa

    from shape.quality.quarantine import QuarantineManager

    m = QuarantineManager()
    m.quarantine_table(pa.table({"a": [1]}), tmp_path, "r", "good", "why", "g")
    bad_dir = tmp_path / "default" / "r"
    for name, text in (("a.json", "[1]"), ("b.json", '"text"'), ("c.json", "null"), ("d", "{")):
        (bad_dir / f"{name}._quarantine_meta.json").write_text(text)
    items = m.list_quarantined(tmp_path)
    assert [i["table_name"] for i in items] == ["good"]
