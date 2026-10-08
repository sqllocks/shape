"""W8-07: opaque nested columns and clean CLI shutdown."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape.kernel.dispatch import reset
from shape.privacy.safe_profile import SafeConfig, to_safe_profile


@pytest.fixture(params=["python", "rust"])
def kernel(request, monkeypatch):
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    reset()
    yield request.param
    reset()


CASES = [
    pa.array([[1], [1], None], pa.list_(pa.int64())),
    pa.array([{"a": 1, "b": "secret"}, {"b": "secret", "a": 1}, None]),
    pa.array([[("x", 1), ("x", 2)], [("x", 1), ("x", 2)], None], pa.map_(pa.string(), pa.int64())),
    pa.array([None, None, None], pa.list_(pa.int64())),
    pa.array([[], [], None], pa.list_(pa.int64())),
    pa.array([{"a": {"b": {"c": [1]}}}, {"a": {"b": {"c": [1]}}}, None]),
]


@pytest.mark.parametrize(
    "values", CASES, ids=["list", "struct", "map-duplicates", "null", "empty-list", "deep"]
)
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("source_kind", ["parquet", "delta"])
def test_nested_profile(kernel, values, mixed, source_kind, tmp_path):
    table = pa.table({"payload": values, **({"id": [1, 2, 3]} if mixed else {})})
    path = tmp_path / ("nested.parquet" if source_kind == "parquet" else "nested")
    if source_kind == "parquet":
        pq.write_table(table, path)
        structure = str(pq.read_schema(path).field("payload").type)
    else:
        from deltalake import DeltaTable, write_deltalake

        write_deltalake(path, table)
        structure = str(DeltaTable(path).to_pyarrow_dataset().schema.field("payload").type)
    prof = shape.profile(path, joint=True)
    doc = prof.to_dict()
    col = doc["columns"]["payload"]
    assert doc["row_count"] == 3
    assert col["dtype"] == "nested"
    assert col["row_count"] == 3
    assert col["structure"] == structure
    assert col["null_count"] == values.null_count
    assert col["null_rate"] == round(values.null_count / 3, 6)
    assert col["cardinality"] == (0 if values.null_count == 3 else 1)
    size = col["serialized_size"]
    assert size["format"] == "shape-nested-size" and size["version"] == 1
    assert size["count"] == 3 - values.null_count
    assert size["min"] is None if values.null_count == 3 else size["min"] > 0
    for field in ("enum_values", "value_counts_ext", "min_value", "max_value"):
        assert col[field] is None
    assert prof.to_dict() == shape.profile(path, joint=True).to_dict()
    safe = to_safe_profile(prof, SafeConfig()).to_dict()
    assert "secret" not in json.dumps(safe)
    safe_col = next(iter(safe["tables"].values()))["columns"]["payload"]
    assert safe_col["structure"] == structure
    assert safe_col["serialized_size"] == size
    saved = tmp_path / "nested.shape"
    shape.save(prof, saved)
    assert shape.load(saved).to_dict()["columns"]["payload"]["structure"] == col["structure"]
    if mixed:
        assert doc["columns"]["id"]["dtype"] == "integer"


def cli(*args, kernel="python"):
    return subprocess.run(
        [sys.executable, "-m", "shape", *map(str, args)],
        env={**os.environ, "SHAPE_KERNEL": kernel},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "args,expected",
    [
        (["--exclude", "payload"], ["id"]),
        (["--columns", "payload"], ["payload"]),
        (["--exclude", "payload", "--exclude", "id"], []),
    ],
)
def test_cli_selection(kernel, tmp_path, args, expected):
    path = tmp_path / "input.parquet"
    pq.write_table(pa.table({"id": [1, 2], "payload": [[1], [2]]}), path)
    output = tmp_path / "out.shape"
    result = cli("profile", path, "-o", output, *args, kernel=kernel)
    assert result.returncode == 0, result.stderr
    assert list(shape.load(output).to_dict()["columns"]) == expected


@pytest.mark.parametrize("flag", ["--exclude", "--columns"])
def test_cli_unknown_column(kernel, tmp_path, flag):
    path = tmp_path / "input.parquet"
    pq.write_table(pa.table({"id": [1]}), path)
    result = cli("profile", path, "-o", tmp_path / "out.shape", flag, "missing", kernel=kernel)
    assert result.returncode == 2
    assert "missing" in result.stderr and "not a column" in result.stderr


def test_delta_nested_and_error_shutdown(kernel, tmp_path):
    from deltalake import write_deltalake

    path = tmp_path / "delta"
    write_deltalake(path, pa.table({"id": [1, 2], "payload": [{"a": 1}, {"a": 2}]}))
    result = cli("profile", path, "-o", tmp_path / "out.shape", kernel=kernel)
    assert result.returncode == 0, result.stderr
    assert shape.load(tmp_path / "out.shape").to_dict()["columns"]["payload"]["dtype"] == "nested"
    result = cli(
        "profile", path, "-o", tmp_path / "bad.shape", "--time-column", "missing", kernel=kernel
    )
    assert result.returncode == 2, result.stderr
    assert "missing" in result.stderr
    assert "terminate" not in result.stderr


def test_consumers(kernel, tmp_path):
    from shape.generation.fit import fit_schema
    from shape.generation.learn import SchemaBuilder
    from shape.profile.merge import merge_profiles

    table = pa.table({"id": [1, 2, 3], "payload": [[1], [2], None]})
    prof = shape.profile(table, name="t", sketches=True)
    for build in (SchemaBuilder().build, fit_schema):
        with pytest.raises(ValueError, match="nested column t.payload"):
            build(prof)
    merged = merge_profiles([prof, prof])
    col = merged.to_dict()["columns"]["payload"]
    assert col["structure"] == str(table.field("payload").type)
    assert col["null_count"] == 2
    assert col["cardinality"] == 2
    assert col["serialized_size"]["count"] == 4
    assert col["serialized_size"]["total"] == 12
    assert col["value_counts_ext"] is None
    exact = merge_profiles([shape.profile(table), shape.profile(table)], exact_only=True)
    assert exact.to_dict()["columns"]["payload"]["serialized_size"]["total"] == 12


@pytest.mark.parametrize("mode", ["exact", "bounded"])
def test_batch_stream_state(kernel, mode):
    from shape.profile.engine import EngineOptions, profile_table, table_entry
    from shape.profile.nested import ProfileState

    table = pa.table({"id": [1, 2, 3], "payload": [[1], [], None]})
    batch = profile_table(table, "t", EngineOptions(mode=mode, batch_size=1))
    state = ProfileState(table.schema, mode)
    for record in table.to_batches(max_chunksize=2):
        state.update(record)
    stream = table_entry(state, "t", table.schema, mode)
    assert batch == stream
    nested = batch["columns"][1]
    assert nested["kind"] == "nested"
    assert (nested["distinct"] if mode == "exact" else round(nested["distinct"])) == 2
    assert nested["serialized_size"]["total"] == 5
    assert "top" not in nested and "min" not in nested
    if mode == "bounded":
        restored = ProfileState.from_snapshot(table.schema, state.snapshot())
        assert table_entry(restored, "t", table.schema, mode) == stream
    else:
        with pytest.raises(ValueError, match="bounded mode only"):
            state.snapshot()


def test_old_profile_compatibility(kernel, tmp_path):
    # Legacy profiles do not have structure or serialized_size. Keep their body and id.
    prof = shape.profile(pa.table({"id": [1, 2]}))
    path = tmp_path / "legacy.shape"
    shape.save(prof, path, capture="full")
    loaded = shape.load(path)
    assert loaded.to_dict() == prof.to_dict()
    assert loaded.content_id == prof.content_id
    assert "structure" not in loaded.to_dict()["columns"]["id"]
    old = shape.load(Path(__file__).parents[1] / "fixtures" / "w2_07" / "pre_w2_07_orders.shape")
    shape.save(old, path, capture="full")
    assert shape.load(path).to_dict() == old.to_dict()
    assert all("structure" not in c for c in old.to_dict()["columns"].values())


def test_nested_fingerprint(kernel):
    from shape.fingerprint import table_id

    values = pa.array([[("x", 1), ("x", 2)], None], pa.map_(pa.string(), pa.int64()))
    table = pa.table({"payload": values})
    assert table_id("t", table) == table_id("t", table.take([1, 0]))
    changed = pa.table({"payload": pa.array([[("x", 2)], None], values.type)})
    assert table_id("t", table) != table_id("t", changed)


def test_selection_api(kernel):
    table = pa.table({"id": [1], "payload": [[1]]})
    selected = shape.profile(table, columns=["id", "payload"], exclude=["payload"], sketches=True)
    assert list(selected.to_dict()["columns"]) == ["id"]
    assert selected.sketches is not None
    with pytest.raises(ValueError, match="missing.*not a column"):
        shape.profile(table, columns=["missing"])
    with pytest.raises(ValueError, match="missing.*not a column"):
        shape.profile({"t": table}, exclude=["missing"])


def test_nested_reordered_merge(kernel):
    from shape.profile.merge import MergeError, merge_profiles

    table = pa.table({"id": [1, 2], "payload": [[1], [2]]})
    first = shape.profile(table, name="t", sketches=True)
    second = shape.profile(table.select(["payload", "id"]), name="t", sketches=True)
    merged = merge_profiles([first, second]).to_dict()
    assert merged["columns"]["payload"]["cardinality"] == 2
    assert merged["columns"]["payload"]["serialized_size"]["total"] == 12
    different = shape.profile(pa.table({"id": [1, 2], "payload": [{"x": 1}, {"x": 2}]}), name="t")
    with pytest.raises(MergeError, match="payload.*structures differ"):
        merge_profiles([first, different], exact_only=True)


def test_workbook_column_selection(kernel, tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["id", "amount"])
    sheet.append([1, 2])
    path = tmp_path / "input.xlsx"
    book.save(path)
    prof = shape.profile(path, exclude=["amount"])
    assert list(next(iter(prof.tables.values()))["columns"]) == ["id"]
    with pytest.raises(ValueError, match="missing.*not a column"):
        shape.profile(path, columns=["missing"])


def test_nested_stream_runtime(kernel):
    from shape.profile.engine import EngineOptions, profile_table
    from shape.streaming.runtime import GlobalProfiler, restore_profiler

    table = pa.table({"id": [1, 2, 3], "payload": [{"a": [1]}, {"a": []}, None]})
    profiler = GlobalProfiler(table.schema, name="t")
    batches = table.to_batches(max_chunksize=1)
    profiler.process(batches[0])
    profiler = restore_profiler(profiler.snapshot())
    for batch in batches[1:]:
        profiler.process(batch)
    [window] = profiler.finish()
    assert window.profile["columns"][1]["kind"] == "nested"
    assert window.profile == profile_table(table, "t", EngineOptions(mode="bounded"))


def test_empty_nested(kernel):
    table = pa.table({"payload": pa.array([], pa.list_(pa.int64()))})
    col = shape.profile(table).to_dict()["columns"]["payload"]
    assert col["dtype"] == "nested"
    assert col["null_rate"] is None
    assert col["serialized_size"]["count"] == 0
    assert col["serialized_size"]["mean"] is None


def test_cli_nested_generation_error(kernel, tmp_path):
    path = tmp_path / "nested.shape"
    shape.save(shape.profile(pa.table({"payload": [[1], [2]]}), name="t"), path)
    result = cli("generate", "--from", path, "-o", tmp_path / "generated", kernel=kernel)
    assert result.returncode == 2
    assert "nested column t.payload" in result.stderr
    assert "Traceback" not in result.stderr
