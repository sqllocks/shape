from shape.drift import compare


def test_drift():
    a = {"columns": {"x": {"kind": "numeric", "mean": 10, "null_count": 0}}}
    b = {"columns": {"x": {"kind": "numeric", "mean": 20, "null_count": 0}, "y": {"kind": "text"}}}
    d = compare(a, b)
    assert d[0].score == 1 and any(x.path == "columns.x.mean" for x in d)


def _model(columns, rows=10, table="t"):
    cols = [
        {
            "arrow_type": "x",
            "kind": "float",
            "count": rows,
            "null_count": 0,
            "error_models": {},
            **c,
        }
        for c in columns
    ]
    return {
        "schema_version": 2,
        "engine": "t",
        "mode": "exact",
        "tables": {table: {"name": table, "rows": rows, "columns": cols}},
    }


def test_drift_reads_v2_models_with_the_v2_metric_names():
    base = {
        "mean": 10.0,
        "variance_population": 4.0,
        "min": 4.0,
        "max": 16.0,
        "distinct": 100.0,
        "quantiles": {"0.25": 8.5, "0.5": 10.0, "0.75": 11.5},
    }
    moved = {
        **base,
        "distinct": 50.0,
        "min": 15.0,
        "max": 22.0,
        "quantiles": {"0.25": 17.0, "0.5": 18.0, "0.75": 19.5},
    }
    a = _model([{"name": "x", **base}], 1000)
    b = _model([{"name": "x", **moved}], 1000)
    paths = {d.path: d.score for d in compare(a, b)}
    assert set(paths) == {"columns.x.distinct", "columns.x.quantiles", "columns.x.range"}
    assert paths["columns.x.distinct"] == 0.5
    assert paths["columns.x.quantiles"] > 0.9
    assert compare(a, a) == []


def test_int_to_float_is_not_a_type_change_but_text_is():
    a = _model([{"name": "x", "kind": "int"}])
    assert compare(a, _model([{"name": "x", "kind": "float"}])) == []
    changed = compare(a, _model([{"name": "x", "kind": "text"}]))
    assert changed[0].path == "columns.x.kind" and changed[0].score == 1


def test_drift_across_several_tables_prefixes_the_paths():
    a = _model([{"name": "x", "mean": 1.0}])
    b = _model([{"name": "x", "mean": 3.0}])
    a["tables"]["u"] = {"name": "u", "rows": 1, "columns": []}
    b["tables"]["v"] = {"name": "v", "rows": 1, "columns": []}
    found = {d.path: d.reason for d in compare(a, b)}
    assert found["tables.u"] == "table removed" and found["tables.v"] == "table added"
    assert "tables.t.columns.x.mean" in found


def test_table_level_changes_of_one_table_have_a_real_path():
    # #461: a single table's row-count change was addressed as "tables.None"
    a = _model([{"name": "x"}], 100)
    b = _model([{"name": "x"}], 1000)
    assert [d.path for d in compare(a, b)] == ["rows"]
    a["tables"]["u"] = {"name": "u", "rows": 1, "columns": []}
    b["tables"]["u"] = {"name": "u", "rows": 1, "columns": []}
    assert [d.path for d in compare(a, b)] == ["tables.t.rows"]


def _joint_profiles():
    import numpy as np
    import pyarrow as pa

    import shape

    zips = np.random.default_rng(1).integers(0, 50, 2000)
    city = [f"c{z}" for z in zips]
    broken = [f"c{z}" if i % 3 else f"x{i % 7}" for i, z in enumerate(zips)]
    text = [str(z) for z in zips]
    return (
        shape.profile(pa.table({"zip": text, "city": city})),
        shape.profile(pa.table({"zip": text, "city": broken})),
    )


def test_joint_changes_are_addressed_one_by_one():
    # #461: every joint change shared one path ("tables.None" for one table)
    base, cur = _joint_profiles()
    joint = [d for d in compare(base, cur) if d.kind in ("dependency_broken", "association_shift")]
    assert joint
    paths = [d.path for d in joint]
    assert all("None" not in p and p.startswith("joint.") for p in paths)
    assert len(set(paths)) == len(paths)
    assert "joint.zip -> city" in paths
