"""W9-05 child statistics and their public consumers."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape.kernel import dispatch
from shape.privacy.redact import redact_profile
from shape.streaming.messages import StreamMessage, decode_messages


def fixture() -> pa.Table:
    return pa.table(
        {
            "address": pa.array([{"city": "X"}, {"city": None}, None]),
            "items": pa.array([[{"price": 1.0}, {"price": None}], [], None]),
            "attrs": pa.array(
                [[("a", 2)], [("b", None)], None], type=pa.map_(pa.string(), pa.int64())
            ),
        }
    )


@pytest.mark.parametrize("kernel", ["python", "rust"])
@pytest.mark.parametrize("fmt", ["table", "parquet", "ipc", "jsonl"])
def test_child_statistics(
    kernel: str, fmt: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHAPE_KERNEL", kernel)
    dispatch.reset()
    table = fixture().drop(["attrs"]) if fmt == "jsonl" else fixture()
    source = table
    if fmt != "table":
        source = tmp_path / ("input." + fmt)
        if fmt == "parquet":
            pq.write_table(table, source)
        elif fmt == "ipc":
            with (
                pa.OSFile(str(source), "wb") as sink,
                pa.ipc.new_file(sink, table.schema) as writer,
            ):
                writer.write_table(table)
        else:
            source.write_text("\n".join(json.dumps(row) for row in table.to_pylist()))
    cols = shape.profile(source, joint=False).to_dict()["columns"]
    city = cols["address"]["children"][0]
    assert city["path"] == "$.address.city"
    assert city["null_count"] == 1
    assert city["row_count"] == 2
    assert city["present"] == 0.5
    element = cols["items"]["children"][0]
    assert element["path"] == "$.items[]"
    price = element["children"][0]
    assert price["path"] == "$.items[].price"
    assert price["mean"] == 1
    assert price["null_rate"] == 0.5
    assert cols["items"]["length"]["mean"] == 1
    if fmt != "jsonl":
        assert [c["path"] for c in cols["attrs"]["children"]] == ["$.attrs{key}", "$.attrs{value}"]
    dispatch.reset()


def test_depth_and_empty_boundaries() -> None:
    assert "children" not in shape.profile(fixture(), max_depth=0).to_dict()["columns"]["items"]
    item = shape.profile(fixture(), max_depth=1).to_dict()["columns"]["items"]["children"][0]
    assert "children" not in item
    empty = shape.profile(fixture().slice(0, 0)).to_dict()["columns"]["address"]["children"][0]
    assert empty["row_count"] == 0
    assert empty["present"] is None
    for bad in (-1, True, 1.5):
        with pytest.raises(ValueError, match="max_depth"):
            shape.profile(fixture(), max_depth=bad)


def test_stream_nested_opt_in() -> None:
    messages = [StreamMessage("0", 0, '{"x":{"a":[1,2]}}')]
    batch = decode_messages(messages, nested=True)
    assert batch is not None and pa.types.is_struct(batch.schema.field("x").type)
    flat = decode_messages(messages)
    assert flat is not None and pa.types.is_string(flat.schema.field("x").type)


def test_safe_capture_recurses(tmp_path: Path) -> None:
    table = pa.table({"outer": pa.array([{"email": "nested@example.com"}] * 15)})
    full = shape.profile(table)
    safe = redact_profile(full).to_dict()
    child = safe["columns"]["outer"]["children"][0]
    assert child["enum_values"] is None
    assert child["min_value"] is None
    assert "nested@example.com" not in json.dumps(safe)
    path = tmp_path / "nested.shape"
    shape.save(full, path)
    assert (
        shape.load(path).to_dict()["columns"]["outer"]["children"]
        == safe["columns"]["outer"]["children"]
    )


def test_diff_and_contract_paths() -> None:
    base = shape.profile(pa.table({"items": pa.array([[{"price": 3.0}], [{"price": 4.0}]])}))
    current = shape.profile(pa.table({"items": pa.array([[{"price": None}], [{"price": 4.0}]])}))
    changes = shape.diff(base, current).to_dict()["changes"]
    assert any(
        c["column"] == "$.items[].price" and c["kind"] == "null_rate_change" for c in changes
    )
    result = shape.check(base, {"columns": {"items[].price": {"min": 4}}})
    assert any(
        v["column"] == "items[].price" and v["rule"] == "min"
        for v in result.to_dict()["violations"]
    )
    assert shape.check(base, {"columns": {"$.items[].price": {"min": 3}}}).passed


def test_summary_keeps_children() -> None:
    summary = shape.profile(fixture()).summary()
    assert summary["columns"]["items"]["children"][0]["children"][0]["path"] == "$.items[].price"


def test_bounded_children_and_lengths() -> None:
    from shape.profile.engine import EngineOptions, profile_table
    from shape.streaming.runtime import GlobalProfiler

    table = fixture()
    doc = profile_table(table, options=EngineOptions(mode="bounded"))
    price = doc["columns"][1]["children"][0]["children"][0]
    assert price["path"] == "$.items[].price"
    assert price["mean"] == 1
    assert price["error_models"]["quantiles"]["exact"] is False
    profiler = GlobalProfiler(table.schema, event_time=None)
    profiler.update(table.to_batches()[0])
    window = profiler.finish()[0]
    assert window.profile["columns"][1]["children"][0]["path"] == "$.items[]"
