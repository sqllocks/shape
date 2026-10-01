"""The `verify` parity harness (benchmarks/vs_spindle/verify_1to1): its own logic, without the
baseline. The parity run itself needs the pinned checkout and is recorded in
docs/plans/lane_status/P6-09.md."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from shape.quality import GateSchema, VerifyRunner

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH))
spec = importlib.util.spec_from_file_location("verify_1to1", BENCH / "verify_1to1" / "verify.py")
assert spec and spec.loader
h = importlib.util.module_from_spec(spec)
sys.modules["verify_1to1"] = h
spec.loader.exec_module(h)

DUMP = {
    "tables": {
        "a": {
            "primary_key": ["id"],
            "columns": {
                "id": {"type": "integer", "nullable": False, "generator": {"strategy": "sequence"}},
                "x": {
                    "type": "float",
                    "nullable": True,
                    "generator": {"strategy": "distribution", "name": "norm", "loc": 1, "scale": 2},
                },
                "k": {
                    "type": "string",
                    "nullable": False,
                    "generator": {"strategy": "enum", "values": {"u": 0.5, "v": 0.5}},
                },
                "d": {
                    "type": "float",
                    "nullable": False,
                    "generator": {"strategy": "distribution", "distribution": "log_normal"},
                },
            },
        },
        "b": {
            "primary_key": ["id"],
            "columns": {"id": {"type": "integer", "nullable": False, "generator": {}}},
        },
    },
    "relationships": [
        {
            "name": "r",
            "parent": "a",
            "child": "b",
            "parent_columns": ["id"],
            "child_columns": ["id"],
            "type": "one_to_many",
            "cardinality": {},
            "optional": False,
        }
    ],
}


def tables() -> dict[str, pa.Table]:
    return {
        "order": pa.table({"order_id": [1, 2, 3, 4], "customer_id": [1, 2, 3, 4]}),
        "product": pa.table({"cost": [1.0], "x": [2]}),
        "store": pa.table({"s": [1]}),
        "customer": pa.table({"customer_id": [1, 2]}),
        "return": pa.table({"r": [1]}),
    }


def test_schema_conversion_keeps_declared_distributions_and_enums_only():
    doc = h.to_gate_schema(DUMP)
    cols = doc["tables"]["a"]["columns"]
    assert cols["x"]["distribution"] == {"name": "norm", "params": {"loc": 1, "scale": 2}}
    assert cols["k"]["enum"] == {"u": 0.5, "v": 0.5}
    assert "distribution" not in cols["d"] and "distribution" not in cols["id"]
    assert doc["relationships"][0] == {
        "name": "r",
        "parent": "a",
        "child": "b",
        "parent_columns": ["id"],
        "child_columns": ["id"],
        "type": "one_to_many",
    }
    assert GateSchema.from_dict(doc).tables["a"].primary_key == ("id",)


def test_mutations_inject_exactly_their_defect():
    t = tables()
    assert h.clean(t) is t
    big = {"order": pa.table({"order_id": list(range(30)), "customer_id": list(range(30))})}
    assert h.dup_pk(big)["order"].column("order_id").to_pylist()[10:13] == [1, 1, 1]
    assert h.orphan_fk(big)["order"].column("customer_id").to_pylist().count(10**9) == 25
    assert h.null_in_required(big)["order"].column("customer_id").null_count == 3
    assert "cost" not in h.missing_column(t)["product"].column_names
    assert "note" in h.extra_column(t)["store"].column_names
    assert h.retyped(t)["customer"].schema.field("customer_id").type == pa.string()
    assert "return" not in h.missing_table(t)


def test_compare_reports_each_kind_of_difference():
    gate = {"gate": "g", "passed": True, "errors": [], "warnings": [], "details": {}}
    base = {"passed": True, "row_counts": {"t": 1}, "gates": [gate]}
    assert h.compare(base, json.loads(json.dumps(base))) == []
    other = json.loads(json.dumps(base))
    other["passed"] = False
    other["row_counts"] = {"t": 2}
    other["gates"][0]["errors"] = ["e"]
    other["gates"].append({**gate, "gate": "extra"})
    out = h.compare(base, other)
    assert "overall passed: baseline True vs shape False" in out
    assert "row counts differ" in out
    assert any(d.startswith("gates differ") for d in out)
    assert any(d.startswith("g.errors") for d in out)


def test_null_coercion_warnings_are_set_aside_only_for_integer_columns(tmp_path):
    pq.write_table(
        pa.table({"i": pa.array([1, None], pa.int64()), "f": [1.5, 2.5]}), tmp_path / "t.parquet"
    )
    warn = "Table 't' column '{}': expected type compatible with 'integer', got 'float64'"
    rep = {
        "gates": [
            {
                "gate": "schema_conformance",
                "warnings": [warn.format("i"), warn.format("f"), "other"],
            }
        ]
    }
    assert h.drop_null_coercion(rep, tmp_path) == 1
    assert rep["gates"][0]["warnings"] == [warn.format("f"), "other"]


def test_scenarios_have_unique_names_and_the_schema_edits_are_pure_additions():
    names = [s[0] for s in h.SCENARIOS]
    assert len(names) == len(set(names)) and "clean" in names and "statistical_drift" in names
    base = {
        "tables": {
            "product": {
                "primary_key": [],
                "columns": {"unit_price": {"type": "float", "nullable": False, "generator": {}}},
            },
            "customer": {
                "primary_key": [],
                "columns": {
                    "gender": {"type": "string", "nullable": False, "generator": {}},
                    "loyalty_tier": {"type": "string", "nullable": False, "generator": {}},
                },
            },
        }
    }
    wrong = h._declare_wrong_stats(json.loads(json.dumps(base)))
    assert wrong["tables"]["product"]["columns"]["unit_price"]["generator"]["scale"] == 3.0
    gates = h.to_gate_schema({**wrong, "relationships": []})
    assert gates["tables"]["customer"]["columns"]["gender"]["enum"] == {"M": 0.9, "F": 0.1}


def test_a_converted_schema_runs_in_the_product(tmp_path):
    doc = h.to_gate_schema(DUMP)
    a = pa.table({"id": [1, 2], "x": [0.0, 1.0], "k": ["u", "v"], "d": [1.0, 2.0]})
    r = VerifyRunner(GateSchema.from_dict(doc)).run({"a": a, "b": pa.table({"id": [1, 2]})})
    assert r.passed
