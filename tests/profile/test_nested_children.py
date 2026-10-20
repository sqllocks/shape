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

pytestmark = [pytest.mark.conformance, pytest.mark.zero_network]


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
    profiler = GlobalProfiler(table.schema)
    profiler.process(table.to_batches()[0])
    window = profiler.finish()[0]
    assert window.profile["columns"][1]["children"][0]["path"] == "$.items[]"


def test_sliced_null_parents_and_quoted_paths() -> None:
    arr = pa.array([{"a.b": [99]}, None, {"a.b": [1, 2]}, {"a.b": []}])
    cols = shape.profile(pa.table({"x": arr.slice(1)}), joint=False).to_dict()["columns"]
    child = cols["x"]["children"][0]
    assert child["path"] == '$.x["a.b"]'
    assert child["row_count"] == 2
    assert child["children"][0]["mean"] == 1.5
    assert child["length"]["mean"] == 1


def test_dataset_depth_and_sample_children() -> None:
    data = shape.profile({"t": fixture()}, max_depth=0).to_dict()
    assert "children" not in data["tables"]["t"]["columns"]["address"]
    data = shape.profile(fixture(), sample=1, sample_method="head", joint=False).to_dict()
    assert data["columns"]["address"]["children"][0]["row_count"] == 1
    assert data["columns"]["items"]["children"][0]["row_count"] == 2


def test_child_add_remove_length_distribution_and_explain() -> None:
    before = shape.profile(pa.table({"x": pa.array([{"a": [0, 1]}] * 40)}), joint=False)
    after = shape.profile(
        pa.table({"x": pa.array([{"b": [0]}, {"b": [0, 1, 2]}] * 20)}), joint=False
    )
    changes = shape.diff(before, after).to_dict()["changes"]
    assert any(c["column"] == "$.x.a" and c["kind"] == "column_removed" for c in changes)
    assert any(c["column"] == "$.x.b" and c["kind"] == "column_added" for c in changes)
    baseline = shape.profile(pa.table({"items": pa.array([[0, 1]] * 40)}), joint=False)
    current = shape.profile(pa.table({"items": pa.array([[0], [0, 1, 2]] * 20)}), joint=False)
    diff = shape.diff(baseline, current)
    assert any(
        c["column"] == "$.items" and c["kind"] == "length_change" for c in diff.to_dict()["changes"]
    )
    from shape.report.explain import explain

    assert "$.items" in explain(diff).text


def test_repeated_single_row_never_releases_child_values() -> None:
    table = pa.table({"x": pa.array([[{"number": 918273.0, "secret": "leaked@example.com"}] * 50])})
    safe = redact_profile(shape.profile(table, joint=False)).to_dict()
    child = safe["columns"]["x"]["children"][0]["children"][0]
    for key in (
        "mean",
        "std",
        "min_value",
        "max_value",
        "quantiles",
        "enum_values",
        "value_counts_ext",
    ):
        assert child.get(key) is None
    assert "leaked@example.com" not in json.dumps(safe)
    assert child["row_count"] == 50
    from shape.privacy.safe_validator import SafeProfileValidator

    unsafe = {
        "schema_version": 1,
        "row_count": 20,
        "columns": {"x": {"children": [{"top": ["a@example.com"]}]}},
    }
    assert not SafeProfileValidator().validate_data(unsafe).is_clean


def test_stream_snapshot_and_window_merge_keep_children() -> None:
    from shape.streaming.runtime import GlobalProfiler, WindowedProfiler

    table = fixture()
    profiler = GlobalProfiler(table.schema)
    profiler.process(table.slice(0, 1))
    restored = WindowedProfiler.restore(profiler.snapshot())
    restored.process(table.slice(1))
    child = restored.finish()[0].profile["columns"][1]["children"][0]["children"][0]
    assert child["path"] == "$.items[].price"
    assert child["row_count"] == 2 and child["mean"] == 1


def test_file_stream_nested_option(tmp_path: Path) -> None:
    from shape.streaming.file_source import FileStreamSource

    source = tmp_path / "events.jsonl"
    source.write_text('{"x":{"a":[1,2]}}\n')
    stream = FileStreamSource()
    batches = list(stream.read(str(source), nested=True))
    assert pa.types.is_struct(batches[0][1].schema.field("x").type)


def test_engine_children_validate_as_additive_model() -> None:
    from shape.profile.engine import profile
    from shape.spec.migrate import to_model

    doc = to_model(profile(fixture(), mode="bounded"))
    assert doc["tables"]["table"]["columns"][0]["children"][0]["path"] == "$.address.city"


def test_nested_stream_rejects_invalid_option_and_later_wrong_type() -> None:
    from shape.streaming.messages import DecodeStats, StreamSourceError

    with pytest.raises(ValueError, match="nested"):
        decode_messages([], nested="yes")
    first = decode_messages([StreamMessage("0", 0, '{"x":{"a":[1]}}')], nested=True)
    stats = DecodeStats()
    assert (
        decode_messages(
            [StreamMessage("0", 1, '{"x":2}')], nested=True, schema=first.schema, stats=stats
        )
        is None
    )
    assert stats.rejected == 1
    with pytest.raises(StreamSourceError):
        decode_messages(
            [StreamMessage("0", 2, '{"x":2}')], nested=True, schema=first.schema, on_error="raise"
        )


def test_sliding_windows_merge_child_sketches() -> None:
    from shape.streaming.runtime import SlidingProfiler

    table = pa.table(
        {
            "_shape_event_time": pa.array(
                [1_000_000, 6_000_000], type=pa.timestamp("us", tz="UTC")
            ),
            "x": [[1], [3]],
        }
    )
    profiler = SlidingProfiler(table.schema, size="10s", slide="5s")
    windows = profiler.process(table) + profiler.finish()
    both = next(w for w in windows if w.rows == 2)
    child = both.profile["columns"][1]["children"][0]
    assert child["path"] == "$.x[]" and child["mean"] == 2


def test_profile_json_cli_honors_depth(tmp_path: Path, capsys) -> None:
    from shape.cli.main import main

    source = tmp_path / "nested.parquet"
    pq.write_table(fixture(), source)
    output = tmp_path / "summary.json"
    assert (
        main(
            [
                "profile",
                str(source),
                "-o",
                str(tmp_path / "nested.shape"),
                "--max-depth",
                "1",
                "--json",
                str(output),
            ]
        )
        == 0
    )
    capsys.readouterr()
    item = json.loads(output.read_text())["columns"]["items"]["children"][0]
    assert item["path"] == "$.items[]" and "children" not in item
    assert (
        main(["profile", str(source), "-o", str(tmp_path / "bad.shape"), "--max-depth", "-1"]) == 2
    )


def test_both_kernels_have_identical_exact_children(monkeypatch) -> None:
    results = []
    for kernel in ("python", "rust"):
        monkeypatch.setenv("SHAPE_KERNEL", kernel)
        dispatch.reset()
        doc = shape.profile(fixture(), joint=False).to_dict()
        results.append({name: col["children"] for name, col in doc["columns"].items()})
    assert results[0] == results[1]
    dispatch.reset()


@pytest.mark.parametrize("name", ["[]", "{key}", "{value}"])
def test_struct_reserved_names_are_unambiguous(name: str) -> None:
    child = shape.profile(pa.table({"x": [{name: 7}]}), joint=False).to_dict()["columns"]["x"][
        "children"
    ][0]
    assert child["path"] == "$.x[" + json.dumps(name) + "]"
    assert shape.check(
        shape.profile(pa.table({"x": [{name: 7}]})), {"columns": {child["path"]: {"min": 6}}}
    ).passed


def test_closed_contract_does_not_treat_children_as_extra_columns() -> None:
    profile = shape.profile(fixture(), joint=False)
    contract = {
        "columns": {name: {} for name in fixture().column_names},
        "allow_extra_columns": False,
    }
    assert shape.check(profile, contract).passed
    contract["columns"]["items[].price"] = {"min": 0}
    assert shape.check(profile, contract).passed


def test_repeated_temporal_children_release_no_date_values() -> None:
    from datetime import datetime

    profile = shape.profile(pa.table({"x": [[datetime(1987, 7, 5, 13, 7)]] * 1}), joint=False)
    child = redact_profile(profile).to_dict()["columns"]["x"]["children"][0]
    for key in ("hour_histogram", "dow_histogram", "temporal_histogram"):
        assert child.get(key) is None


def test_bounded_length_distribution_is_compared_and_has_error_models() -> None:
    from shape.profile.engine import profile

    base = profile(pa.table({"x": [[1, 2]] * 40}), mode="bounded")
    current = profile(pa.table({"x": [[1], [1, 2, 3]] * 20}), mode="bounded")
    length = base["tables"]["table"]["columns"][0]["length"]
    assert length["row_count"] == 40
    assert length["error_models"]["quantiles"]["exact"] is False
    assert all(isinstance(k, str) for k in length["quantiles"])
    changes = shape.diff(base, current).to_dict()["changes"]
    assert any(c["kind"] == "length_change" and c["column"] == "$.x" for c in changes)


def test_quoted_child_paths_are_literal_policy_and_privacy_names() -> None:
    from shape.report.explain import explain

    path = '$.x["a.b"]'
    profile = shape.profile(pa.table({"x": [{"a.b": 12345.0}] * 40}), joint=False)
    report = {
        "changes": [
            {
                "column": path,
                "kind": "mean_shift",
                "severity": "high",
                "score": 1.0,
                "baseline": 12345,
                "current": 67890,
            }
        ]
    }
    explanation = explain(report, baseline=profile)
    assert explanation.to_dict()["columns"][0]["withheld"] is True
    assert "12345" not in json.dumps(explanation.to_dict())
    current = shape.profile(pa.table({"x": [{"a.b": None}, {"a.b": 12345.0}] * 20}), joint=False)
    changes = shape.diff(profile, current, only_columns=[path]).to_dict()["changes"]
    assert changes and all(c["column"] == path for c in changes)


def test_child_diff_keeps_dataset_scope_and_default_broker_exclusion() -> None:
    before = shape.profile({"left": pa.table({"x": [{"a": 1}] * 40})}, joint=False)
    after = shape.profile({"left": pa.table({"x": [{"a": None}, {"a": 1}] * 20})}, joint=False)
    changes = shape.diff(before, after).to_dict()["changes"]
    child = next(c for c in changes if c["column"] == "$.x.a")
    assert child["table"] == "left"
    first = shape.profile(
        pa.table({"_shape_headers": [{"x": 1}] * 40, "id": range(40)}), joint=False
    )
    second = shape.profile(
        pa.table({"_shape_headers": [{"x": None}, {"x": 1}] * 20, "id": range(40)}), joint=False
    )
    assert not shape.diff(first, second).to_dict()["changes"]
    assert shape.diff(first, second, only_columns=["$._shape_headers.x"]).to_dict()["changes"]


def test_old_opaque_checkpoint_remains_readable_without_inventing_children() -> None:
    from shape.profile.child_state import ProfileState

    table = fixture()
    old = dispatch.get_kernel().ProfileState(table.schema, "bounded")
    old.update(table.to_batches()[0])
    restored = ProfileState.from_snapshot(table.schema, old.snapshot())
    result = restored.finalize()
    assert restored.rows == 3 and "children" not in result["columns"][0]


def test_child_checkpoint_reads_across_kernels(monkeypatch) -> None:
    from shape.profile.child_state import ProfileState

    table = fixture()
    monkeypatch.setenv("SHAPE_KERNEL", "python")
    dispatch.reset()
    before = ProfileState(table.schema, "bounded")
    before.update(table.to_batches()[0])
    raw = before.snapshot()
    monkeypatch.setenv("SHAPE_KERNEL", "rust")
    dispatch.reset()
    after = ProfileState.from_snapshot(table.schema, raw)
    child = after.finalize()["columns"][1]["children"][0]["children"][0]
    assert child["path"] == "$.items[].price" and child["mean"] == 1
    dispatch.reset()
