"""W9-10 offline semantic graph and singular SQL acceptance."""

import json
from pathlib import Path

import pytest
from shape_dbt.fromdbt import from_dbt, metadata
from shape_dbt.project import DbtProjectError, read_manifest, read_schema_yaml
from shape_dbt.report import build_report
from shape_dbt.totests import compile_tests, contract_from_dbt_tests, normalize_contract

SEMANTIC = """
version: 2
models:
  - name: orders
    columns:
      - name: id
      - name: amount
      - name: ordered_at
semantic_models:
  - name: orders_semantic
    model: ref('orders')
    entities:
      - {name: order, type: primary, expr: id}
    dimensions:
      - name: ordered_at
        type: time
        type_params: {time_granularity: day}
    measures:
      - {name: revenue, expr: amount, agg: sum}
"""


def test_semantic_yaml_keys_types_aggregation_and_boundary():
    relations = read_schema_yaml(SEMANTIC)
    schema, _ = from_dbt(relations, smart=False)
    assert schema.tables["orders"].primary_key == ["id"]
    assert schema.tables["orders"].columns["ordered_at"].type == "timestamp"
    assert schema.tables["orders"].columns["amount"].type in ("float", "decimal")
    doc = metadata(relations, schema)
    assert doc["tables"]["orders"]["columns"]["amount"]["semantic"]["aggregation"] == "sum"
    assert doc["tables"]["orders"]["columns"]["ordered_at"]["semantic"]["granularity"] == "day"
    assert read_schema_yaml("models: [{name: empty}]")[0].columns == {}


def test_semantic_primary_preserves_alternate_unique_test():
    text = """
models:
  - name: orders
    columns:
      - {name: email, data_type: varchar, tests: [unique, not_null]}
      - {name: id, data_type: bigint}
semantic_models:
  - name: orders_semantic
    model: ref('orders')
    entities: [{name: order, type: primary, expr: id}]
"""
    relations = read_schema_yaml(text)
    schema, _ = from_dbt(relations, smart=False)
    assert schema.tables["orders"].primary_key == ["id"]
    assert metadata(relations, schema)["tables"]["orders"]["columns"]["email"]["tests"] == [
        "unique",
        "not_null",
    ]


def test_manifest_semantic_and_singular_metadata(tmp_path):
    doc = {
        "nodes": {
            "model.p.orders": {"name": "orders", "resource_type": "model", "columns": {}},
            "test.p.custom": {
                "name": "custom",
                "resource_type": "test",
                "original_file_path": "tests/custom.sql",
                "depends_on": {"nodes": ["model.p.orders"]},
                "raw_code": "unparsed nonsense",
            },
        },
        "semantic_models": {
            "semantic_model.p.orders": {
                "name": "orders_semantic",
                "model": "ref('orders')",
                "entities": [{"name": "order", "type": "primary", "expr": "id"}],
                "dimensions": [],
                "measures": [],
            }
        },
    }
    relations = read_manifest(doc)
    schema, _ = from_dbt(relations, smart=False)
    assert schema.tables["orders"].primary_key == ["id"]
    assert metadata(relations, schema)["singular_tests"] == [
        {"name": "custom", "sql_path": "tests/custom.sql", "depends_on": ["model.p.orders"]}
    ]
    with pytest.raises(DbtProjectError):
        read_manifest({"semantic_models": {}})


@pytest.mark.parametrize(
    "dialect,regexp",
    [
        ("duckdb", "regexp_matches"),
        ("postgres", "~"),
        ("snowflake", "regexp_like"),
        ("bigquery", "regexp_contains"),
    ],
)
def test_singular_sql_rules_exact_roundtrip_and_idempotence(dialect, regexp):
    contract = {
        "columns": {
            "code": {"pattern": "currency_code", "min": "A", "max": "Z"},
            "active": {"min_true_rate": 0, "max_true_rate": 1},
        }
    }
    compiled = compile_tests(contract, model="orders", dialect=dialect)
    assert len(compiled.singular_tests) == 5
    assert regexp in compiled.singular_tests["orders__code__pattern.sql"].lower()
    assert "count(" in compiled.singular_tests["orders__active__min_true_rate.sql"].lower()
    assert (
        compiled.singular_tests
        == compile_tests(contract, model="orders", dialect=dialect).singular_tests
    )
    assert contract_from_dbt_tests(compiled.yaml()) == normalize_contract(contract)
    with pytest.raises(DbtProjectError):
        compile_tests(contract, model="../orders", dialect=dialect)
    with pytest.raises(DbtProjectError):
        compile_tests(contract, model="orders", dialect="unknown")


def test_impact_transitive_metric_exposure_and_cycles():
    manifest = {
        "nodes": {
            "model.p.orders": {"name": "orders"},
            "model.p.dashboard": {
                "name": "dashboard",
                "depends_on": {"nodes": ["model.p.orders", "model.p.dashboard"]},
            },
        },
        "semantic_models": {
            "semantic_model.p.orders": {
                "name": "orders_semantic",
                "model": "ref('orders')",
                "measures": [{"name": "revenue", "expr": "amount", "agg": "sum"}],
            }
        },
        "metrics": {
            "metric.p.revenue": {
                "name": "revenue",
                "type": "simple",
                "type_params": {"measure": {"name": "revenue"}},
            }
        },
        "exposures": {
            "exposure.p.dashboard": {
                "name": "dashboard",
                "depends_on": {"nodes": ["model.p.dashboard"]},
            }
        },
    }
    report = build_report(
        {"results": []},
        manifest,
        drift={"changes": [{"column": "amount"}, {"column": "other"}]},
        table="orders",
    )
    assert report["impact"]["format"] == "shape-dbt-impact"
    assert report["impact"]["version"] == 1
    assert report["impact"]["columns"]["orders.amount"] == {
        "metrics": ["revenue"],
        "exposures": ["dashboard"],
    }
    assert report["impact"]["columns"]["orders.other"]["metrics"] == []
    assert build_report({"results": []})["impact"]["columns"] == {}


def test_seed_semantic_models_keys_numeric_first_timestamp():
    from shape_dbt.seeds import semantic_models_from_schema

    schema, _ = from_dbt(read_schema_yaml(SEMANTIC), smart=False)
    entry = semantic_models_from_schema(schema)[0]
    assert entry["entities"] == [{"name": "orders_id", "type": "primary", "expr": "id"}]
    assert entry["dimensions"][0]["name"] == "ordered_at"
    assert entry["measures"][0]["agg"] == "sum"
    empty, _ = from_dbt(read_schema_yaml("models: [{name: empty}]"), smart=False)
    assert semantic_models_from_schema(empty)[0]["measures"] == []


def test_example_documents_all_resources():
    root = Path(__file__).parents[3]
    import yaml

    docs = [
        yaml.safe_load(p.read_text()) or {}
        for p in (root / "examples/dbt_jaffle_shop").rglob("*.yml")
    ]
    for key in ("semantic_models", "metrics", "exposures"):
        assert any(doc.get(key) for doc in docs)
    assert list((root / "examples/dbt_jaffle_shop/tests").glob("*.sql"))
    assert "singular" in (root / "docs/DBT.md").read_text().lower()


def test_semantic_foreign_entity_relationship_conflict_and_split_files(tmp_path):
    from shape_dbt.project import read_project

    base = """
models:
  - name: customers
    columns: [{name: customer_id, tests: [unique, not_null]}]
  - name: orders
    columns:
      - name: customer_id
        tests:
          - relationships: {to: "ref('customers')", field: customer_id}
"""
    semantic = """
semantic_models:
  - name: customer_semantic
    model: ref('customers')
    entities: [{name: customer, type: primary, expr: customer_id}]
  - name: order_semantic
    model: ref('orders')
    entities: [{name: customer, type: foreign, expr: customer_id}]
"""
    (tmp_path / "schema.yml").write_text(base)
    (tmp_path / "semantic.yml").write_text(semantic)
    schema, _ = from_dbt(read_project([tmp_path]), smart=False)
    assert schema.tables["orders"].columns["customer_id"].fk_ref_table == "customers"
    conflict = base.replace("field: customer_id", "field: wrong_id") + semantic
    with pytest.raises(
        DbtProjectError, match="order_semantic.*relationships test.*orders.customer_id"
    ):
        read_schema_yaml(conflict)
    with pytest.raises(DbtProjectError, match="no matching primary entity"):
        read_schema_yaml(base + semantic.replace("type: primary", "type: natural"))
    with pytest.raises(DbtProjectError, match="unknown dimension type"):
        read_schema_yaml(SEMANTIC.replace("type: time", "type: mystery"))
    with pytest.raises(DbtProjectError, match="unknown type"):
        read_schema_yaml(SEMANTIC.replace("type: primary", "type: mystery"))


def test_singular_sql_duckdb_boundaries_nulls_and_negative_values():
    import duckdb

    connection = duckdb.connect()
    connection.execute("create table orders(code varchar, active boolean, day date)")
    connection.execute(
        "insert into orders values ('USD', true, '2025-01-01'), "
        "('EUR', false, '2025-12-31'), (null, null, null)"
    )
    contract = {
        "columns": {
            "code": {"pattern": "currency_code", "min": "EUR", "max": "USD"},
            "active": {"min_true_rate": 0.5, "max_true_rate": 0.5},
            "day": {"min": "2025-01-01", "max": "2025-12-31"},
        }
    }
    compiled = compile_tests(contract, model="orders", dialect="duckdb")

    def run(sql):
        return connection.execute(sql.replace("{{ ref('orders') }}", "orders")).fetchall()

    assert all(run(sql) == [] for sql in compiled.singular_tests.values())
    connection.execute("insert into orders values ('invalid', true, '2026-01-01')")
    assert len(run(compiled.singular_tests["orders__code__pattern.sql"])) == 1
    assert len(run(compiled.singular_tests["orders__active__max_true_rate.sql"])) == 1
    assert len(run(compiled.singular_tests["orders__day__max.sql"])) == 1
    connection.execute("delete from orders")
    assert all(run(sql) == [] for sql in compiled.singular_tests.values())
    connection.execute("insert into orders values (null, null, null)")
    assert all(run(sql) == [] for sql in compiled.singular_tests.values())
    for invalid in (-0.1, 1.1, float("nan"), True):
        with pytest.raises(DbtProjectError, match="rate must be"):
            compile_tests(
                {"columns": {"active": {"min_true_rate": invalid}}},
                model="orders",
                dialect="duckdb",
            )
    with pytest.raises(DbtProjectError, match="unknown pattern label"):
        compile_tests(
            {"columns": {"code": {"pattern": "unknown"}}}, model="orders", dialect="duckdb"
        )
    connection.close()


def test_cli_writes_and_merges_singular_sql_idempotently(tmp_path):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    contract = {"columns": {"code": {"pattern": "currency_code"}, "active": {"min_true_rate": 0}}}
    source = tmp_path / "contract.json"
    source.write_text(json.dumps(contract))
    schema = tmp_path / "models" / "schema.yml"
    tests = tmp_path / "tests"
    tests.mkdir()
    unrelated = tests / "handwritten.sql"
    unrelated.write_text("select 1")
    args = [str(source), "--model", "orders", "-o", str(schema), "--tests-dir", str(tests)]
    assert cli.run_command(default_host(), "to-dbt-tests", args) == 0
    before = {p.name: p.read_bytes() for p in tests.glob("*.sql")}
    assert cli.run_command(default_host(), "to-dbt-tests", [*args, "--merge", str(schema)]) == 0
    assert before == {p.name: p.read_bytes() for p in tests.glob("*.sql")}
    assert contract_from_dbt_tests(schema.read_text()) == normalize_contract(contract)
    assert unrelated.read_text() == "select 1"


def test_impact_failing_column_markdown_and_v1_compatibility():
    from shape_dbt.report import render_markdown

    manifest = {
        "nodes": {
            "model.p.orders": {"name": "orders"},
            "test.p.amount": {
                "name": "amount",
                "resource_type": "test",
                "attached_node": "model.p.orders",
                "column_name": "amount",
                "test_metadata": {"name": "not_null"},
            },
        },
        "semantic_models": {
            "semantic_model.p.orders": {
                "name": "orders_semantic",
                "model": "ref('orders')",
                "measures": [{"name": "revenue", "expr": "amount"}],
            }
        },
        "metrics": {"metric.p.revenue": {"name": "revenue", "type_params": {"measure": "revenue"}}},
        "exposures": {
            "exposure.p.dashboard": {
                "name": "dashboard",
                "depends_on": {"nodes": ["model.p.orders"]},
            }
        },
    }
    report = build_report({"results": [{"unique_id": "test.p.amount", "status": "fail"}]}, manifest)
    assert report["impact"]["columns"]["orders.amount"] == {
        "metrics": ["revenue"],
        "exposures": ["dashboard"],
    }
    assert "| orders.amount | revenue | dashboard |" in render_markdown(report)
    legacy = dict(report)
    legacy.pop("impact")
    assert "FAIL" in render_markdown(legacy)
    # Version 1 readers can still use every original field after the additive change.
    assert report["format"] == "shape-dbt-report" and report["version"] == 1
    assert {"ok", "dbt", "shape", "by_column", "summary"} <= report.keys()


def test_semantic_seed_writer_idempotence_no_timestamp_and_foreign_keys(tmp_path):
    import yaml
    from shape_dbt.seeds import write_semantic_models

    schema, _ = from_dbt(
        read_schema_yaml("""
models:
  - name: customers
    columns: [{name: id, tests: [unique, not_null]}]
  - name: orders
    columns:
      - {name: id, tests: [unique, not_null]}
      - name: customer_id
        tests: [{relationships: {to: "ref('customers')", field: id}}]
      - {name: amount, data_type: double}
"""),
        smart=False,
    )
    path = write_semantic_models(tmp_path, schema)
    before = path.read_bytes()
    assert write_semantic_models(tmp_path, schema).read_bytes() == before
    entries = yaml.safe_load(before)["semantic_models"]
    assert entries[1]["dimensions"] == []
    assert {"name": "customers_id", "type": "foreign", "expr": "customer_id"} in entries[1][
        "entities"
    ]
    assert [m["expr"] for m in entries[1]["measures"]] == ["amount"]


def test_cli_semantic_seeds_and_default_singular_directory(tmp_path):
    import yaml

    from shape.plugins import cli
    from shape.plugins.host import default_host

    project = tmp_path / "project"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: generated\nversion: 1.0.0\n")
    schema, _ = from_dbt(read_schema_yaml(SEMANTIC), smart=False)
    source = tmp_path / "schema.gen.json"
    source.write_text(json.dumps(schema.to_dict()))
    host = default_host()
    assert (
        cli.run_command(
            host,
            "dbt-seeds",
            [str(source), "--project", str(project), "--rows", "orders=1", "--semantic-models"],
        )
        == 0
    )
    semantic = project / "models" / "_shape_semantic_models.yml"
    assert (
        yaml.safe_load(semantic.read_text())["semantic_models"][0]["defaults"]["agg_time_dimension"]
        == "ordered_at"
    )
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"columns": {"code": {"pattern": "currency_code"}}}))
    out = project / "models" / "_tests.yml"
    assert (
        cli.run_command(host, "to-dbt-tests", [str(contract), "--model", "orders", "-o", str(out)])
        == 0
    )
    assert (project / "tests" / "orders__code__pattern.sql").is_file()
    assert not (project / "models" / "tests").exists()


def test_semantic_sql_expressions_are_derived_and_kept_as_metadata():
    text = (
        SEMANTIC.replace("expr: id", "expr: \"concat(id, '-order')\"")
        .replace("expr: amount", "expr: amount * 2")
        .replace(
            "name: ordered_at\n        type:",
            "name: ordered_day\n        expr: date_trunc('day', ordered_at)\n        type:",
        )
    )
    relations = read_schema_yaml(text)
    schema, _ = from_dbt(relations, smart=False)
    assert schema.tables["orders"].primary_key == ["order"]
    assert schema.tables["orders"].columns["revenue"].type == "float"
    columns = metadata(relations, schema)["tables"]["orders"]["columns"]
    assert columns["order"]["semantic"]["expression"] == "concat(id, '-order')"
    assert columns["revenue"]["semantic"]["expression"] == "amount * 2"
    assert columns["ordered_day"]["semantic"]["expression"] == "date_trunc('day', ordered_at)"


def test_semantic_composite_entities_preserve_tuples_nulls_and_parent_order():
    import duckdb
    from shape_dbt.seeds import semantic_models_from_schema

    from shape.generation.schema import Relationship

    schema, _ = from_dbt(
        read_schema_yaml("""
models:
  - name: parents
    tests: [{dbt_utils.unique_combination_of_columns: {combination_of_columns: [a, b]}}]
    columns: [{name: a, data_type: varchar}, {name: b, data_type: varchar}]
  - name: children
    columns: [{name: x, data_type: varchar}, {name: y, data_type: varchar}]
  - name: flags
    tests: [{dbt_utils.unique_combination_of_columns: {combination_of_columns: [a, b]}}]
    columns: [{name: a, data_type: boolean}, {name: b, data_type: bigint}]
"""),
        smart=False,
    )
    # Relationship components can arrive in a different order from the parent's key.
    schema.relationships.append(
        Relationship("parent_child", "parents", "children", ["b", "a"], ["y", "x"])
    )
    entries = {m["name"]: m for m in semantic_models_from_schema(schema, dialect="duckdb")}
    parent = entries["parents_semantic"]["entities"]
    child = entries["children_semantic"]["entities"]
    assert len(parent) == len(child) == 1
    assert parent[0]["name"] == child[0]["name"] == "parents_key"
    con = duckdb.connect()
    con.execute("create table parents(a varchar, b varchar)")
    con.execute("insert into parents values ('a|b', 'c'), ('a', 'b|c'), ('', ':'), (':', '')")
    encoded = [
        r[0] for r in con.execute("select " + parent[0]["expr"] + " from parents").fetchall()
    ]
    assert len(set(encoded)) == 4
    con.execute("create table children(x varchar, y varchar)")
    con.execute("insert into children values ('a|b', 'c'), (null, 'c'), ('a', null)")
    children = [
        r[0] for r in con.execute("select " + child[0]["expr"] + " from children").fetchall()
    ]
    assert children == [encoded[0], None, None]
    flags = entries["flags_semantic"]["entities"][0]["expr"]
    encoded_flags = con.execute(
        "select " + flags + " from (values (true, 1), (false, 1), (true, 11)) t(a,b)"
    ).fetchall()
    assert len(set(encoded_flags)) == 3
    assert (
        "datalength"
        in semantic_models_from_schema(schema, dialect="tsql")[0]["entities"][0]["expr"]
    )
    assert (
        "as string"
        in semantic_models_from_schema(schema, dialect="spark")[0]["entities"][0]["expr"]
    )
    con.close()


def test_multiple_measures_on_one_column_keep_all_aggregations():
    text = SEMANTIC + "      - {name: average_revenue, expr: amount, agg: average}\n"
    relations = read_schema_yaml(text)
    schema, _ = from_dbt(relations, smart=False)
    semantic = metadata(relations, schema)["tables"]["orders"]["columns"]["amount"]["semantic"]
    assert [(measure["name"], measure["aggregation"]) for measure in semantic["measures"]] == [
        ("revenue", "sum"),
        ("average_revenue", "average"),
    ]


def test_impact_computed_measures_identifiers_literals_and_unknown_columns():
    expression = (
        'amount * tax_rate + coalesce("discount", 0) + '
        "case when 'ignored' = 'tax_rate' then 0 else 1 end /* ignored */ -- ignored\n"
    )
    manifest = {
        "nodes": {
            "model.p.orders": {
                "name": "orders",
                "columns": {
                    name: {"name": name} for name in ("amount", "tax_rate", "discount", "ignored")
                },
            }
        },
        "semantic_models": {
            "semantic_model.p.orders": {
                "name": "orders_semantic",
                "model": "ref('orders')",
                "measures": [
                    {"name": "net", "expr": expression},
                    {"name": "unknown", "expr": "not_a_column * 2"},
                ],
            }
        },
        "metrics": {
            "metric.p.net": {"name": "net_metric", "type_params": {"measure": "net"}},
            "metric.p.unknown": {"name": "unknown_metric", "type_params": {"measure": "unknown"}},
        },
    }
    report = build_report(
        {"results": []},
        manifest,
        drift={
            "changes": [
                {"column": name}
                for name in ("amount", "tax_rate", "discount", "ignored", "not_a_column")
            ]
        },
        table="orders",
    )
    for name in ("amount", "tax_rate", "discount"):
        assert report["impact"]["columns"]["orders." + name]["metrics"] == ["net_metric"]
    for name in ("ignored", "not_a_column"):
        assert report["impact"]["columns"]["orders." + name]["metrics"] == []
