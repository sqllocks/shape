"""`shape verify`: loading, the runner, the reports, the gate schema, and the command."""

from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.quality import (
    GateSchema,
    GateSchemaError,
    VerifyReport,
    VerifyRunner,
    load_gate_schema,
    load_tables,
)

SCHEMA = {
    "format": "shape-gates",
    "version": 1,
    "tables": {
        "customer": {
            "primary_key": ["id"],
            "columns": {"id": {"type": "integer"}, "name": {"type": "string", "nullable": True}},
        },
        "order": {
            "primary_key": ["id"],
            "columns": {"id": {"type": "integer"}, "customer_id": {"type": "integer"}},
        },
    },
    "relationships": [
        {
            "name": "placed_by",
            "parent": "customer",
            "child": "order",
            "parent_columns": ["id"],
            "child_columns": ["customer_id"],
        }
    ],
}


def write_data(d, orders=None):
    d.mkdir(exist_ok=True)
    pq.write_table(pa.table({"id": [1, 2], "name": ["a", None]}), d / "customer.parquet")
    pq.write_table(
        pa.table(orders or {"id": [10, 11, 12], "customer_id": [1, 2, 2]}), d / "order.parquet"
    )
    return d


def write_schema(tmp_path, doc=SCHEMA):
    p = tmp_path / "gates.json"
    p.write_text(json.dumps(doc))
    return p


def test_load_tables_formats_and_errors(tmp_path):
    d = write_data(tmp_path / "pq")
    assert sorted(load_tables(d)) == ["customer", "order"]
    assert sorted(load_tables(d, "parquet")) == ["customer", "order"]
    assert load_tables(d / "order.parquet")["order"].num_rows == 3
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "t.csv").write_text("a,b\n1,NA\n2,x\n")
    t = load_tables(tmp_path / "c")["t"]
    assert t.column("b").to_pylist() == [None, "x"]
    (tmp_path / "j").mkdir()
    (tmp_path / "j" / "t.jsonl").write_text('{"a": 1}\n{"a": 2}\n')
    assert load_tables(tmp_path / "j")["t"].num_rows == 2
    assert load_tables(tmp_path / "j", "parquet") == {}
    with pytest.raises(FileNotFoundError):
        load_tables(tmp_path / "nope")
    with pytest.raises(ValueError, match="Unsupported format"):
        load_tables(d, "xml")
    (tmp_path / "x.dat").write_text("1")
    with pytest.raises(ValueError, match="Cannot tell the format"):
        load_tables(tmp_path / "x.dat")


def test_without_a_schema_only_row_counts_are_reported(tmp_path):
    tables = load_tables(write_data(tmp_path / "d"))
    r = VerifyRunner().run(tables)
    assert r.passed and r.gate_results == [] and r.row_counts == {"customer": 2, "order": 3}


def test_runner_gate_order_and_statistical_flag(tmp_path):
    tables = load_tables(write_data(tmp_path / "d"))
    schema = GateSchema.from_dict(SCHEMA)
    r = VerifyRunner(schema).run(tables)
    assert [g.gate_name for g in r.gate_results] == [
        "schema_conformance",
        "null_constraint",
        "unique_constraint",
        "referential_integrity",
    ]
    r = VerifyRunner(schema, statistical=True).run(tables)
    assert r.gate_results[-1].gate_name == "distribution" and r.passed


def test_reports(tmp_path):
    tables = load_tables(write_data(tmp_path / "d", {"id": [1, 1], "customer_id": [1, 9]}))
    result = VerifyRunner(GateSchema.from_dict(SCHEMA), False, "d", "gates.json").run(tables)
    assert not result.passed
    doc = json.loads(VerifyReport(result).to_json())
    assert doc["passed"] is False and doc["row_counts"] == {"customer": 2, "order": 2}
    gates = {g["gate"]: g for g in doc["gates"]}
    assert gates["unique_constraint"]["errors"] == [
        "Table 'order' PK column 'id' has 1 duplicate values"
    ]
    assert gates["referential_integrity"]["details"]["orphan_counts"] == {
        "order.customer_id->customer.id": 1
    }
    md = VerifyReport(result).to_markdown()
    assert md.startswith("# Shape Verify Report")
    assert "**Overall: FAIL**" in md and "| unique_constraint | ❌ FAIL | 1 | 0 |" in md
    assert "- **ERROR:** order.customer_id has 1 orphan FK values not found in customer.id" in md
    assert "shape verify d --schema gates.json" in md
    assert "pindle" not in md and "pindle" not in doc.get("shape_version", "")


def test_gate_schema_round_trip_and_validation(tmp_path):
    s = GateSchema.from_dict(SCHEMA)
    assert GateSchema.from_dict(s.to_dict()) == s
    for bad, msg in [
        ([], "JSON object"),
        ({"format": "other"}, "not a gate schema"),
        ({"format": "shape-gates", "version": 9}, "unsupported gate schema version"),
        ({"format": "shape-gates", "version": 1, "tables": []}, "`tables` must be an object"),
        ({"format": "shape-gates", "version": 1, "tables": {"t": 1}}, "table 't' must be"),
        (
            {"format": "shape-gates", "version": 1, "relationships": [{"name": "x"}]},
            "invalid relationship",
        ),
    ]:
        with pytest.raises(GateSchemaError, match=msg):
            GateSchema.from_dict(bad)
    p = tmp_path / "bad.json"
    p.write_text("{nope")
    with pytest.raises(GateSchemaError, match="not valid JSON"):
        load_gate_schema(p)


def test_gate_schema_from_a_profile_artifact(tmp_path):
    import shape

    csv = tmp_path / "t.csv"
    csv.write_text("id,v\n1,a\n2,\n3,c\n")
    prof = shape.profile(str(csv))
    out = tmp_path / "t.shape"
    shape.save(prof, str(out))
    s = load_gate_schema(out)
    t = s.tables["t"]
    assert t.primary_key == ("id",)
    assert (t.columns["id"].type, t.columns["id"].nullable) == ("integer", False)
    assert (t.columns["v"].type, t.columns["v"].nullable) == ("string", True)
    js = tmp_path / "profile.json"
    js.write_text(json.dumps(prof.to_dict()))
    assert load_gate_schema(js) == s
    assert VerifyRunner(s).run(load_tables(csv)).passed
    csv.write_text("id,v\n1,a\n1,b\n,c\n")  # a duplicate key and a null where there was none
    r = VerifyRunner(s).run(load_tables(csv))
    assert not r.passed
    assert {g.gate_name for g in r.gate_results if not g.passed} == {
        "null_constraint",
        "unique_constraint",
    }


def test_gate_schema_from_a_dataset_profile_checks_the_detected_foreign_key(tmp_path):
    import shape

    customer = pa.table({"customer_id": list(range(1, 101)), "name": ["a"] * 100})
    order = pa.table(
        {"oid": list(range(1, 501)), "customer_id": [(i % 100) + 1 for i in range(500)]}
    )
    s = GateSchema.from_dict(shape.profile({"customer": customer, "order": order}).to_dict())
    assert [(r.parent, r.child) for r in s.relationships] == [("customer", "order")]
    assert VerifyRunner(s).run({"customer": customer, "order": order}).passed
    broken = pa.table({"oid": [1, 2], "customer_id": [1, 999]})
    r = VerifyRunner(s).run({"customer": customer, "order": broken})
    assert [g.gate_name for g in r.gate_results if not g.passed] == ["referential_integrity"]


def test_gate_schema_from_a_shape_model_v2():
    model = {
        "schema_version": 2,
        "engine": "e",
        "mode": "exact",
        "tables": {
            "t": {
                "name": "t",
                "rows": 3,
                "primary_key": ["a"],
                "columns": [
                    {"name": "a", "kind": "int", "null_count": 0},
                    {"name": "b", "kind": "text", "null_count": 2},
                    {"name": "c", "kind": "temporal", "null_count": 0},
                    {"name": "d", "kind": "bool", "null_count": 0},
                    {"name": "e", "kind": "float", "null_count": 0},
                ],
            }
        },
    }
    t = GateSchema.from_dict(model).tables["t"]
    assert t.primary_key == ("a",)
    assert {c.name: (c.type, c.nullable) for c in t.columns.values()} == {
        "a": ("integer", False),
        "b": ("string", True),
        "c": ("datetime", False),
        "d": ("boolean", False),
        "e": ("float", False),
    }


def test_cli_exit_codes_and_report_files(tmp_path, capsys):
    data = write_data(tmp_path / "d")
    schema = write_schema(tmp_path)
    assert main(["verify", str(data), "--schema", str(schema)]) == 0
    out = capsys.readouterr().out
    assert "Result: PASS" in out and "schema_conformance" in out and "order: 3" in out
    rep = tmp_path / "r.json"
    assert main(["verify", str(data), "--schema", str(schema), "-o", str(rep)]) == 0
    assert json.loads(rep.read_text())["passed"] is True
    md = tmp_path / "r.md"
    assert main(["verify", str(data), "--schema", str(schema), "-o", str(md)]) == 0
    assert md.read_text().startswith("# Shape Verify Report")

    bad = write_data(tmp_path / "bad", {"id": [10, 11], "customer_id": [1, 99]})
    capsys.readouterr()
    assert main(["verify", str(bad), "--schema", str(schema)]) == 1
    cap = capsys.readouterr()
    assert "Result: FAIL" in cap.out
    assert "ERROR [referential_integrity]: order.customer_id has 1 orphan" in cap.err


def test_cli_strict_fails_on_warnings(tmp_path):
    data = write_data(tmp_path / "d")
    doc = json.loads(json.dumps(SCHEMA))
    doc["tables"]["customer"]["columns"]["id"]["type"] = "string"  # type warning only
    schema = write_schema(tmp_path, doc)
    assert main(["verify", str(data), "--schema", str(schema)]) == 0
    assert main(["verify", str(data), "--schema", str(schema), "--strict"]) == 1


def test_cli_input_errors_exit_2(tmp_path, capsys):
    assert main(["verify", str(tmp_path / "missing")]) == 2
    assert "Path not found" in capsys.readouterr().err
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["verify", str(empty)]) == 2
    assert "no auto data files found" in capsys.readouterr().err
    data = write_data(tmp_path / "d")
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    assert main(["verify", str(data), "--schema", str(bad)]) == 2


def test_cli_statistical_without_declarations_passes(tmp_path):
    data = write_data(tmp_path / "d")
    schema = write_schema(tmp_path)
    assert main(["verify", str(data), "--schema", str(schema), "--statistical"]) == 0
