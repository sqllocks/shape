"""The nine validation gates, each checked against hand-computed results."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
import pyarrow as pa
import pytest

from shape.quality import (
    ColumnSpec,
    DistributionGate,
    FileFormatGate,
    GateRunner,
    GateSchema,
    NullConstraintGate,
    RangeConstraintGate,
    ReferentialIntegrityGate,
    RelationshipSpec,
    SchemaConformanceGate,
    SchemaDriftGate,
    TableSpec,
    TemporalConsistencyGate,
    UniqueConstraintGate,
    ValidationContext,
)
from shape.quality.gates import dtype_name


def schema() -> GateSchema:
    return GateSchema(
        {
            "customer": TableSpec(
                "customer",
                {
                    "id": ColumnSpec("id", "integer"),
                    "name": ColumnSpec("name", "string", nullable=True),
                    "score": ColumnSpec("score", "float", nullable=True),
                },
                ("id",),
            ),
            "order": TableSpec(
                "order",
                {
                    "id": ColumnSpec("id", "integer"),
                    "customer_id": ColumnSpec("customer_id", "integer"),
                },
                ("id",),
            ),
        },
        (RelationshipSpec("placed_by", "customer", "order", ("id",), ("customer_id",)),),
    )


def tables() -> dict[str, pa.Table]:
    return {
        "customer": pa.table({"id": [1, 2, 3], "name": ["a", None, "c"], "score": [0.5, 1.5, 2.5]}),
        "order": pa.table({"id": [10, 11, 12, 13], "customer_id": [1, 1, 2, 3]}),
    }


def ctx(t: dict[str, pa.Table] | None = None, **config: object) -> ValidationContext:
    return ValidationContext(tables=t or tables(), schema=schema(), config=dict(config))


def test_clean_data_passes_every_schema_gate():
    for gate in (
        SchemaConformanceGate(),
        NullConstraintGate(),
        UniqueConstraintGate(),
        ReferentialIntegrityGate(),
    ):
        r = gate.check(ctx())
        assert r.passed and not r.errors and not r.warnings, (gate.name, r)


def test_referential_integrity_counts_orphans_and_ignores_nulls():
    t = tables()
    t["order"] = pa.table({"id": [10, 11, 12, 13], "customer_id": [1, 9, 9, None]})
    r = ReferentialIntegrityGate().check(ctx(t))
    assert not r.passed
    assert r.errors == ["order.customer_id has 2 orphan FK values not found in customer.id"]
    assert r.details == {"orphan_counts": {"order.customer_id->customer.id": 2}}


def test_referential_integrity_text_key_does_not_match_number_key():
    t = tables()
    t["customer"] = pa.table({"id": ["1", "2", "3"], "name": ["a", "b", "c"], "score": [1.0] * 3})
    r = ReferentialIntegrityGate().check(ctx(t))
    assert r.details["orphan_counts"]["order.customer_id->customer.id"] == 4


def test_referential_integrity_numeric_widths_compare_by_value():
    t = tables()
    t["customer"] = pa.table(
        {"id": pa.array([1, 2, 3], pa.int32()), "name": ["a", "b", "c"], "score": [1.0] * 3}
    )
    assert ReferentialIntegrityGate().check(ctx(t)).passed


def test_referential_integrity_needs_schema_and_reports_missing_inputs():
    r = ReferentialIntegrityGate().check(ValidationContext(tables=tables()))
    assert not r.passed and "No schema provided" in r.errors[0]
    t = tables()
    del t["customer"]
    r = ReferentialIntegrityGate().check(ctx(t))
    assert r.passed and r.warnings == [
        "Skipping relationship 'placed_by': missing table(s) in context"
    ]
    t = tables()
    t["order"] = t["order"].drop_columns(["customer_id"])
    r = ReferentialIntegrityGate().check(ctx(t))
    assert r.errors == ["Child column 'order.customer_id' not found in DataFrame"]


def test_self_referencing_relationships_are_not_checked():
    s = GateSchema(
        {"t": TableSpec("t", {"id": ColumnSpec("id"), "p": ColumnSpec("p")}, ("id",))},
        (RelationshipSpec("tree", "t", "t", ("id",), ("p",), "self_referencing"),),
    )
    t = {"t": pa.table({"id": [1, 2], "p": [99, 98]})}
    assert ReferentialIntegrityGate().check(ValidationContext(tables=t, schema=s)).passed


def test_schema_conformance_missing_extra_and_type():
    t = tables()
    t["customer"] = pa.table({"id": ["1", "2", "3"], "extra": [1, 2, 3]})
    t["order"] = t["order"]
    r = SchemaConformanceGate().check(ctx(t))
    assert not r.passed
    assert r.errors == ["Table 'customer' missing columns: ['name', 'score']"]
    assert "Table 'customer' has unexpected columns: ['extra']" in r.warnings
    assert (
        "Table 'customer' column 'id': expected type compatible with 'integer', got 'str'"
        in r.warnings
    )
    del t["order"]
    assert (
        "Expected table 'order' not found in data" in SchemaConformanceGate().check(ctx(t)).errors
    )


def test_null_constraint_counts_nulls_and_nans():
    t = tables()
    t["order"] = pa.table({"id": [1, 2, 3], "customer_id": [None, 2, None]})
    t["customer"] = pa.table(
        {"id": pa.array([1.0, float("nan"), 3.0]), "name": ["a", "b", "c"], "score": [1.0] * 3}
    )
    r = NullConstraintGate().check(ctx(t))
    assert r.errors == [
        "Table 'customer' column 'id' is non-nullable but has 1 null values",
        "Table 'order' column 'customer_id' is non-nullable but has 2 null values",
    ]
    assert r.details == {"customer": {"id": 1}, "order": {"customer_id": 2}}


def test_unique_constraint_single_and_composite_keys():
    t = tables()
    t["order"] = pa.table({"id": [1, 1, 1, 2], "customer_id": [1, 1, 2, 3]})
    r = UniqueConstraintGate().check(ctx(t))
    assert r.errors == ["Table 'order' PK column 'id' has 2 duplicate values"]
    assert r.details == {"order": {"column": "id", "duplicates": 2}}
    s = GateSchema({"order": TableSpec("order", {}, ("id", "customer_id"))})
    r = UniqueConstraintGate().check(ValidationContext(tables=t, schema=s))
    assert r.errors == ["Table 'order' composite PK ['id', 'customer_id'] has 1 duplicate rows"]
    assert r.details == {"order": {"columns": ["id", "customer_id"], "duplicates": 1}}


def test_range_constraint():
    r = RangeConstraintGate().check(ctx())
    assert r.passed and r.warnings == ["No range constraints configured — nothing to check"]
    r = RangeConstraintGate().check(
        ctx(ranges={"customer.score": {"min": 1, "max": 2}, "bad": {}, "x.y": {}, "customer.q": {}})
    )
    assert r.errors == [
        "customer.score: 1 values below minimum 1 (actual min: 0.5)",
        "customer.score: 1 values above maximum 2 (actual max: 2.5)",
    ]
    assert r.details["customer.score"] == {
        "actual_min": 0.5,
        "actual_max": 2.5,
        "below_min": 1,
        "above_max": 1,
    }
    assert r.warnings == [
        "Invalid range key 'bad' — expected 'table.column'",
        "Table 'x' not found in data",
        "Column 'q' not found in table 'customer'",
    ]


def test_range_constraint_parses_numeric_text_and_skips_junk():
    t = {"t": pa.table({"v": ["1", "x", None, "300"]})}
    r = RangeConstraintGate().check(
        ValidationContext(tables=t, config={"ranges": {"t.v": {"max": 100}}})
    )
    assert r.errors == ["t.v: 1 values above maximum 100 (actual max: 300.0)"]


def test_temporal_consistency():
    d = datetime(2024, 1, 1)
    t = {
        "e": pa.table(
            {
                "start": pa.array([d, d, None], pa.timestamp("us")),
                "end": pa.array([d + timedelta(1), d - timedelta(1), d], pa.timestamp("us")),
                "future": pa.array(
                    [d, datetime.now() + timedelta(days=30), None], pa.timestamp("us")
                ),
            }
        )
    }
    r = TemporalConsistencyGate().check(
        ValidationContext(
            tables=t,
            config={
                "date_range": {"start": "2024-01-01", "end": "2024-01-01"},
                "no_future": ["e.future", "e.nope", "malformed"],
                "ordering": [
                    {"table": "e", "start": "start", "end": "end"},
                    {"table": "zz", "start": "a", "end": "b"},
                    {"table": "e", "start": "a", "end": "b"},
                ],
            },
        )
    )
    assert "e.end: 1 dates after 2024-01-01" in r.errors
    assert "e.start: 0" not in " ".join(r.errors)
    assert "e.future: 1 values are in the future" in r.errors
    assert "e: 1 rows where 'end' < 'start'" in r.errors
    assert r.details["e.end<start"] == 1
    assert "Table 'zz' not found for ordering check" in r.warnings
    assert "Columns 'a'/'b' not found in 'e'" in r.warnings


def test_temporal_ordering_of_incompatible_types_is_a_warning():
    t = {
        "e": pa.table({"a": [1, 2], "b": pa.array([datetime(2024, 1, 1)] * 2, pa.timestamp("us"))})
    }
    r = TemporalConsistencyGate().check(
        ValidationContext(tables=t, config={"ordering": [{"table": "e", "start": "a", "end": "b"}]})
    )
    assert r.passed and r.warnings == ["Cannot compare 'a' and 'b' in 'e' — incompatible types"]


def test_file_format_gate(tmp_path):
    import pyarrow.parquet as pq

    good = tmp_path / "good.parquet"
    pq.write_table(pa.table({"a": [1, 2]}), good)
    csv = tmp_path / "t.csv"
    csv.write_text("a,b\n1,2\n3,4\n")
    tsv = tmp_path / "t.tsv"
    tsv.write_text("a\tb\n1\t2\n")
    jl = tmp_path / "t.jsonl"
    jl.write_text('{"a": 1}\n{"a": 2}\n')
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not parquet")
    odd = tmp_path / "x.dat"
    odd.write_text("?")
    paths = [good, csv, tsv, jl, empty, bad, odd, tmp_path / "gone.csv"]
    r = FileFormatGate().check(ValidationContext(file_paths=paths))
    assert not r.passed
    assert r.details[str(good)]["rows"] == 2 and r.details[str(csv)] == {
        "size_bytes": csv.stat().st_size,
        "format": ".csv",
        "rows": 2,
        "columns": 2,
        "readable": True,
    }
    assert r.details[str(tsv)]["columns"] == 2 and r.details[str(jl)]["rows"] == 2
    assert f"File is empty (0 bytes): {empty}" in r.errors
    assert f"File not found: {tmp_path / 'gone.csv'}" in r.errors
    assert any(e.startswith(f"Failed to read {bad}:") for e in r.errors)
    assert r.details[str(bad)]["readable"] is False
    assert r.warnings == [f"Unknown file format '.dat' for {odd}"]
    assert FileFormatGate().check(ValidationContext()).warnings == [
        "No file paths provided — nothing to check"
    ]


def test_schema_drift():
    base = {
        "customer": {"columns": {"id": "int64", "name": "str", "gone": "int64", "score": "int64"}},
        "removed": {"columns": {}},
    }
    r = SchemaDriftGate().check(ctx(baseline=base))
    assert r.errors == [
        "Table 'removed' removed",
        "Table 'customer': column 'gone' removed",
        "Table 'customer': column 'score' type changed from 'int64' to 'float64'",
    ]
    assert "New table 'order' added" in r.warnings
    assert "Table 'customer': column 'name' type changed" not in " ".join(r.errors)
    assert r.details["additive"] == ["New table 'order' added"]
    assert len(r.details["breaking"]) == 3
    r = SchemaDriftGate().check(ctx())
    assert r.passed and r.warnings == ["No baseline schema configured — nothing to check"]


def test_dtype_names():
    cases = {
        pa.int16(): "int16",
        pa.uint8(): "uint8",
        pa.float32(): "float32",
        pa.float64(): "float64",
        pa.bool_(): "bool",
        pa.string(): "str",
        pa.large_string(): "str",
        pa.timestamp("us"): "datetime64[us]",
        pa.timestamp("ns", tz="UTC"): "datetime64[ns, UTC]",
        pa.duration("s"): "timedelta64[s]",
        pa.date32(): "object",
        pa.decimal128(5, 2): "object",
        pa.dictionary(pa.int8(), pa.string()): "str",
    }
    for arrow, expected in cases.items():
        assert dtype_name(arrow) == expected, arrow


def dist_ctx(values: list[float], weights=None) -> ValidationContext:
    cols = {"x": ColumnSpec("x", "float", distribution={"name": "norm", "params": {}})}
    if weights is not None:
        cols = {"x": ColumnSpec("x", "string", enum=weights)}
    return ValidationContext(
        tables={"t": pa.table({"x": values})},
        schema=GateSchema({"t": TableSpec("t", cols)}),
    )


def test_distribution_ks_passes_for_matching_data_and_warns_on_drift():
    from scipy import stats

    # exact normal quantiles: the KS statistic is tiny, with no dependence on a random draw
    good = list(stats.norm.ppf((np.arange(500) + 0.5) / 500))
    r = DistributionGate().check(dist_ctx(good))
    assert r.passed and not r.warnings and set(r.details["t.x"]) == {"ks_statistic", "p_value"}
    assert r.details["t.x"]["p_value"] > 0.05
    shifted = [v + 3 for v in good]
    r = DistributionGate().check(dist_ctx(shifted))
    assert r.passed, "distribution drift is a warning, never an error"
    assert len(r.warnings) == 1 and r.warnings[0].startswith("t.x: KS test p=0.0000 < α=0.05")


def test_distribution_small_samples_and_bad_names_are_warnings():
    r = DistributionGate().check(dist_ctx([1.0, 2.0]))
    assert r.warnings == ["t.x: too few rows (2) for KS test — skipped"]
    c = dist_ctx([float(i) for i in range(30)])
    c.schema.tables["t"].columns["x"] = ColumnSpec("x", "float", distribution={"name": "nosuch"})
    r = DistributionGate().check(c)
    assert r.passed and r.warnings[0].startswith("t.x: KS test failed")


def test_distribution_chi_squared():
    vals = ["a"] * 70 + ["b"] * 30
    ok = DistributionGate().check(dist_ctx(vals, {"a": 0.7, "b": 0.3}))
    assert ok.passed and not ok.warnings
    assert set(ok.details["t.x"]) == {"chi2", "p_value"}
    bad = DistributionGate().check(dist_ctx(vals, {"a": 0.2, "b": 0.8, "c": 0.0}))
    assert bad.passed
    assert "t.x: expected enum value 'c' is missing from data" in bad.warnings
    assert any("chi-squared p=0.0000" in w for w in bad.warnings)


def test_distribution_without_scipy_or_schema_is_skipped(monkeypatch):
    monkeypatch.setattr("shape.quality.gates._scipy_stats", lambda: None)
    r = DistributionGate().check(dist_ctx([1.0] * 30))
    assert r.passed and "scipy is not installed" in r.warnings[0]
    r = DistributionGate().check(ValidationContext())
    assert r.passed and r.warnings == ["No schema provided — distribution checks skipped"]


def test_runner_runs_all_nine_or_a_chosen_few():
    assert GateRunner.available_gates() == sorted(
        [
            "distribution",
            "file_format",
            "null_constraint",
            "range_constraint",
            "referential_integrity",
            "schema_conformance",
            "schema_drift",
            "temporal_consistency",
            "unique_constraint",
        ]
    )
    results = GateRunner().run_all(ctx())
    assert len(results) == 9
    some = GateRunner(["null_constraint", NullConstraintGate()]).run_all(ctx())
    assert [r.gate_name for r in some] == ["null_constraint"] * 2
    summary = GateRunner.summary(results)
    assert summary["all_passed"] and summary["total_gates"] == 9 and summary["failed"] == 0
    with pytest.raises(ValueError, match="Unknown gate 'nope'"):
        GateRunner(["nope"])
    with pytest.raises(ValueError, match="Unknown gate 'nope'"):
        GateRunner().run_gate("nope", ctx())
    assert GateRunner().run_gate("null_constraint", ctx()).passed


def test_custom_gate_registration():
    from shape.quality import GateResult, ValidationGate

    class Mine(ValidationGate):
        name = "mine"

        def check(self, context):
            return GateResult(self.name, False, errors=["no"])

    GateRunner.register_gate("mine", Mine)
    try:
        assert "mine" in GateRunner.available_gates()
        r = GateRunner(["mine"]).run_all(ctx())
        assert not r[0].passed and GateRunner.summary(r)["failed_gates"] == ["mine"]
    finally:
        from shape.quality import gates

        del gates._GATE_REGISTRY["mine"]
    assert repr(GateResult("g", True)) == "GateResult(g: PASS, 0 errors, 0 warnings)"


# -- HUNT2-quality ----------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset")
@pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles", "Pacific/Auckland"])
def test_no_future_on_naive_timestamps_does_not_depend_on_the_machine_zone(zone, monkeypatch):
    """#589: naive timestamps were compared with the local clock."""
    import datetime as dt

    import pyarrow as pa

    from shape.quality.gates import TemporalConsistencyGate, ValidationContext
    from shape.quality.rowlevel import row_outcomes

    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        past = now - dt.timedelta(hours=1)
        future = now + dt.timedelta(hours=1)
        t = pa.table({"ts": pa.array([past, future], pa.timestamp("us"))})
        ctx = ValidationContext(tables={"t": t}, config={"no_future": ["t.ts"]})
        result = TemporalConsistencyGate().check(ctx)
        assert result.details["t.ts"]["future_dates"] == 1, zone
        [o] = row_outcomes("temporal_consistency", ctx)
        assert o.failing_rows.tolist() == [1], zone
    finally:
        monkeypatch.undo()
        time.tzset()
