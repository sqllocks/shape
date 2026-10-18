"""W9-10 conformance: file-based semantic metadata and singular rule tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from shape_dbt.fromdbt import from_dbt, metadata
from shape_dbt.project import DbtProjectError, read_manifest, read_schema_yaml
from shape_dbt.report import build_report, render_markdown
from shape_dbt.totests import compile_tests, contract_from_dbt_tests, merge_schema_docs

SEMANTIC = """
version: 2
models:
- name: orders
  columns:
  - name: order_id
    data_type: bigint
semantic_models:
- name: order_facts
  model: ref('orders')
  entities:
  - {name: order, type: primary, expr: order_id}
  dimensions:
  - name: ordered_at
    type: time
    type_params: {time_granularity: day}
  measures:
  - {name: revenue, expr: amount, agg: sum}
"""


@pytest.mark.parametrize("manifest", [False, True])
def test_semantic_entities_dimensions_and_measures(manifest):
    doc = yaml.safe_load(SEMANTIC)
    relations = read_schema_yaml(SEMANTIC)
    if manifest:
        relations = read_manifest({"nodes": {"model.p.orders": {"resource_type": "model", "name": "orders", "columns": {"order_id": {"name": "order_id", "data_type": "bigint"}}}}, "semantic_models": {"semantic_model.p.order_facts": doc["semantic_models"][0]}})
    schema, _ = from_dbt(relations, smart=False)
    assert schema.tables["orders"].primary_key == ["order_id"]
    assert schema.tables["orders"].columns["ordered_at"].type == "timestamp"
    assert schema.tables["orders"].columns["amount"].type in ("float", "decimal")
    semantic = metadata(relations, schema)["tables"]["orders"]["semantic_models"]
    assert semantic[0]["dimensions"][0]["type_params"]["time_granularity"] == "day"
    assert semantic[0]["measures"][0]["agg"] == "sum"


def test_entity_test_conflict_names_both_sources():
    text = SEMANTIC.replace("data_type: bigint", "data_type: bigint\n    data_tests:\n    - relationships: {to: ref('other'), field: id}")
    with pytest.raises(DbtProjectError, match="(?s)(relationships.*order_facts|order_facts.*relationships)"):
        read_schema_yaml(text)


def test_singular_tests_are_metadata_without_sql_parsing():
    relations = read_manifest({"nodes": {
        "model.p.orders": {"name": "orders", "resource_type": "model", "columns": {}},
        "test.p.check": {"name": "check", "resource_type": "test", "original_file_path": "tests/check.sql", "raw_code": "not even SQL", "depends_on": {"nodes": ["model.p.orders"]}},
    }})
    schema, _ = from_dbt(relations, smart=False)
    doc = metadata(relations, schema)
    assert doc["format"] == "shape-dbt-metadata" and doc["version"] == 1
    assert doc["singular_tests"] == [{"name": "check", "sql_path": "tests/check.sql", "depends_on": ["model.p.orders"]}]
    assert read_manifest({"nodes": {"model.p.empty": {"name": "empty", "resource_type": "model"}}})[0].singular_tests == []


@pytest.mark.parametrize("dialect,regexp", [("duckdb", "regexp_full_match"), ("postgres", " ~ "), ("snowflake", "regexp_like"), ("bigquery", "regexp_contains")])
def test_singular_compilation_and_exact_roundtrip(dialect, regexp):
    contract = {"columns": {"code": {"pattern": "^[A-Z]+$", "min": "A", "max": "Z"}, "flag": {"min_true_rate": 0, "max_true_rate": 1}}}
    compiled = compile_tests(contract, model="orders", dialect=dialect)
    assert len(compiled.singular_tests) == 5
    assert all(p.startswith("tests/orders__") and p.endswith(".sql") for p in compiled.singular_tests)
    assert any(regexp in sql.lower() for sql in compiled.singular_tests.values())
    assert any("nullif(count(" in sql.lower() for sql in compiled.singular_tests.values())
    assert contract_from_dbt_tests(compiled.yaml(), model="orders") == contract
    assert merge_schema_docs(compiled.doc, compiled.doc) == compiled.doc
    assert compile_tests(contract, model="orders", dialect=dialect).singular_tests == compiled.singular_tests


@pytest.mark.parametrize("model,dialect", [("../bad", "duckdb"), ("ok", "unsupported")])
def test_singular_generation_refuses_unsafe_names_and_dialects(model, dialect):
    with pytest.raises(DbtProjectError):
        compile_tests({"columns": {"code": {"pattern": "a"}}}, model=model, dialect=dialect)


def test_report_impact_walk_is_column_specific_and_cycle_safe():
    manifest = {"nodes": {"model.p.orders": {"name": "orders"}, "model.p.dashboard": {"name": "dashboard", "depends_on": {"nodes": ["model.p.orders", "model.p.dashboard"]}}}, "semantic_models": {"semantic_model.p.facts": {"name": "facts", "model": "ref('orders')", "measures": [{"name": "revenue", "expr": "amount", "agg": "sum"}]}}, "metrics": {"metric.p.revenue": {"name": "revenue", "type_params": {"measure": {"name": "revenue"}}}}, "exposures": {"exposure.p.sales": {"name": "sales", "depends_on": {"nodes": ["model.p.dashboard"]}}}}
    report = build_report({"results": []}, manifest, drift={"changes": [{"column": "orders.amount"}, {"column": "orders.code"}]})
    assert report["impact"]["format"] == "shape-dbt-impact" and report["impact"]["version"] == 1
    assert report["impact"]["columns"]["orders.amount"] == {"metrics": ["revenue"], "exposures": ["sales"]}
    assert report["impact"]["columns"]["orders.code"] == {"metrics": [], "exposures": ["sales"]}
    assert "revenue" in render_markdown(report) and "sales" in render_markdown(report)
    assert build_report({"results": []})["impact"]["columns"] == {}


def test_seed_semantic_models_keys_numeric_and_first_timestamp(tmp_path):
    from shape.generation.schema import GenSchema
    from shape_dbt.seeds import semantic_models_document
    schema = GenSchema.from_dict({"name": "sample", "tables": {"orders": {"primary_key": ["id"], "columns": {"id": {"type": "integer"}, "amount": {"type": "float"}, "created": {"type": "timestamp"}, "updated": {"type": "timestamp"}}}}})
    doc = semantic_models_document(schema)
    semantic = doc["semantic_models"][0]
    assert semantic["entities"] == [{"name": "id", "type": "primary", "expr": "id"}]
    assert semantic["measures"] == [{"name": "orders_amount", "expr": "amount", "agg": "sum"}]
    assert [d["name"] for d in semantic["dimensions"]] == ["created"]
    assert semantic_models_document(GenSchema.from_dict({"name": "empty", "tables": {}})) == {"version": 2, "semantic_models": []}


def test_example_contains_semantic_assets(jaffle):
    docs = [yaml.safe_load(p.read_text()) or {} for p in jaffle.rglob("*.yml")]
    assert any(d.get("semantic_models") for d in docs)
    assert any(d.get("metrics") for d in docs)
    assert any(d.get("exposures") for d in docs)
    assert list((jaffle / "tests").glob("*.sql"))
