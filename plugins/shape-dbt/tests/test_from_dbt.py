"""`shape from-dbt`: a dbt project's tables and tests as a generation schema."""

from __future__ import annotations

import json

import pytest
from shape_dbt.fromdbt import from_dbt, metadata, parse_data_type, select_relations
from shape_dbt.project import DbtProjectError, read_manifest, read_project, read_schema_yaml

from shape.generation.engine import Engine

SCHEMA_YML = """
version: 2
sources:
  - name: raw
    tables:
      - name: customers
        description: One row per customer.
        columns:
          - name: customer_id
            description: Primary key.
            data_type: bigint
            data_tests: [unique, not_null]
          - name: region_code
            data_type: varchar(5)
          - name: tier
            data_type: varchar
            data_tests:
              - accepted_values: {values: [gold, silver, bronze]}
      - name: orders
        columns:
          - name: order_id
            data_type: bigint
            data_tests: [unique, not_null]
          - name: customer_id
            data_tests:
              - not_null
              - relationships: {to: "source('raw', 'customers')", field: customer_id}
          - name: total
            data_type: numeric(12,2)
            tests: [not_null]
          - name: is_open
            data_type: boolean
"""


def build(text: str = SCHEMA_YML, **kw):
    return from_dbt(read_schema_yaml(text), **kw)


def test_unique_and_not_null_make_a_primary_key():
    schema, _ = build()
    assert schema.tables["customers"].primary_key == ["customer_id"]
    assert schema.tables["customers"].columns["customer_id"].nullable is False
    assert schema.tables["orders"].primary_key == ["order_id"]


def test_not_null_makes_a_column_non_nullable_and_the_rest_nullable():
    schema, _ = build()
    orders = schema.tables["orders"].columns
    assert orders["customer_id"].nullable is False
    assert orders["total"].nullable is False  # `tests:` is the key before dbt 1.8
    assert orders["is_open"].nullable is True


def test_relationships_make_a_foreign_key_and_a_relationship():
    schema, _ = build()
    fk = schema.tables["orders"].columns["customer_id"]
    assert fk.generator["strategy"] == "foreign_key"
    assert fk.generator["ref"] == "customers.customer_id"
    (rel,) = schema.relationships
    assert (rel.parent, rel.child) == ("customers", "orders")
    assert (rel.parent_columns, rel.child_columns) == (["customer_id"], ["customer_id"])
    # a key with no data_type takes its parent's type
    assert fk.type == "integer"


def test_accepted_values_make_a_weighted_enum_with_equal_weights():
    schema, _ = build()
    gen = schema.tables["customers"].columns["tier"].generator
    assert gen["strategy"] == "weighted_enum"
    assert set(gen["values"]) == {"gold", "silver", "bronze"}
    assert all(abs(w - 1 / 3) < 1e-9 for w in gen["values"].values())


def test_a_profile_supplies_the_enum_weights():
    import pyarrow as pa

    import shape

    profile = shape.profile(
        {"customers": pa.table({"tier": ["gold"] * 6 + ["silver"] * 3 + ["bronze"]})}
    )
    schema, _ = build(profile=profile)
    values = schema.tables["customers"].columns["tier"].generator["values"]
    assert values["gold"] > values["silver"] > values["bronze"]
    assert sum(values.values()) == pytest.approx(1.0)


def test_data_types_give_types_lengths_precision_and_scale():
    schema, _ = build()
    cust, orders = schema.tables["customers"].columns, schema.tables["orders"].columns
    assert cust["customer_id"].type == "integer"
    assert (cust["region_code"].type, cust["region_code"].max_length) == ("string", 5)
    assert (orders["total"].type, orders["total"].precision, orders["total"].scale) == (
        "decimal",
        12,
        2,
    )
    assert orders["is_open"].type == "boolean"


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("varchar(50)", ("varchar", 50, None, None)),
        ("text", ("varchar", None, None, None)),
        ("numeric(18,2)", ("decimal", None, 18, 2)),
        ("NUMBER(38,0)", ("bigint", None, None, None)),
        ("decimal(10, 4)", ("decimal", None, 10, 4)),
        ("timestamp with time zone", ("timestamp", None, None, None)),
        ("datetime2(6)", ("timestamp", None, None, None)),
        ("FLOAT64", ("float", None, None, None)),
        ("int64", ("bigint", None, None, None)),
        ("bit", ("boolean", None, None, None)),
        (None, (None, None, None, None)),
        ("geography", (None, None, None, None)),
    ],
)
def test_parse_data_type(declared, expected):
    assert parse_data_type(declared) == expected


def test_descriptions_become_table_descriptions_and_metadata():
    relations = read_schema_yaml(SCHEMA_YML)
    schema, _ = from_dbt(relations)
    assert schema.tables["customers"].description == "One row per customer."
    meta = metadata(relations, schema)
    col = meta["format"], meta["tables"]["customers"]["columns"]["customer_id"]
    assert col[0] == "shape-dbt-metadata"
    assert col[1]["description"] == "Primary key."
    assert col[1]["data_type"] == "bigint"
    assert "unique" in col[1]["tests"]


def test_the_schema_generates_with_referential_integrity_and_the_declared_values():
    schema, _ = build()
    result = Engine(schema, seed=3, row_counts={"customers": 40, "orders": 120}).generate()
    customers, orders = result.tables["customers"], result.tables["orders"]
    ids = set(customers["customer_id"].to_pylist())
    assert len(ids) == 40
    assert set(orders["customer_id"].to_pylist()) <= ids
    assert set(customers["tier"].to_pylist()) <= {"gold", "silver", "bronze"}
    assert all(len(z) <= 5 for z in customers["region_code"].to_pylist() if z is not None)


def test_the_schema_document_round_trips():
    from shape.generation.schema import GenSchema

    schema, _ = build()
    assert GenSchema.from_dict(schema.to_dict()).to_dict() == schema.to_dict()


def test_a_composite_unique_combination_is_the_primary_key():
    text = """
version: 2
models:
  - name: lines
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments: {combination_of_columns: [order_id, line_no]}
    columns:
      - {name: order_id, data_type: bigint}
      - {name: line_no, data_type: integer}
      - {name: qty, data_type: integer}
"""
    schema, _ = from_dbt(read_schema_yaml(text))
    assert schema.tables["lines"].primary_key == ["order_id", "line_no"]


def test_a_relationship_to_a_table_that_is_not_selected_is_noted_not_fatal():
    text = """
version: 2
sources:
  - name: raw
    tables:
      - name: orders
        columns:
          - {name: order_id, data_type: bigint, data_tests: [unique, not_null]}
          - name: customer_id
            data_type: bigint
            data_tests:
              - relationships: {to: ref('customers'), field: customer_id}
"""
    schema, notes = from_dbt(read_schema_yaml(text))
    assert schema.relationships == []
    assert any("customers" in n for n in notes if isinstance(n, str))


def test_models_are_used_when_a_project_has_no_sources_or_seeds():
    text = """
version: 2
models:
  - name: dim_user
    columns:
      - {name: user_id, data_type: bigint, data_tests: [unique, not_null]}
"""
    relations = read_schema_yaml(text)
    chosen, notes = select_relations(relations)
    assert [r.name for r in chosen] == ["dim_user"] and notes == []
    with pytest.raises(DbtProjectError, match="nothing to import"):
        select_relations(relations, ["source"])


def test_a_directory_is_read_from_its_yaml_files(jaffle):
    names = {r.name for r in read_project([jaffle])}
    assert {"raw_customers", "raw_orders", "raw_payments", "orders", "customers"} <= names


def test_a_missing_input_is_refused(tmp_path):
    with pytest.raises(DbtProjectError, match="no such file"):
        read_project([tmp_path / "nope.yml"])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(DbtProjectError, match="not a readable manifest"):
        read_project([bad])
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    with pytest.raises(DbtProjectError, match="not a dbt manifest"):
        read_project([empty])


def manifest() -> dict:
    """What dbt writes for the same project: tests are nodes of their own."""
    return {
        "nodes": {
            "seed.p.raw_customers": {
                "resource_type": "seed",
                "name": "raw_customers",
                "description": "One row per customer.",
                "columns": {
                    "customer_id": {
                        "name": "customer_id",
                        "data_type": "bigint",
                        "description": "pk",
                    },
                    "tier": {"name": "tier", "data_type": "varchar"},
                },
            },
            "seed.p.raw_orders": {
                "resource_type": "seed",
                "name": "raw_orders",
                "columns": {
                    "order_id": {"name": "order_id", "data_type": "bigint"},
                    "customer_id": {"name": "customer_id", "data_type": "bigint"},
                },
            },
            "test.p.unique_raw_customers_customer_id.1": {
                "resource_type": "test",
                "attached_node": "seed.p.raw_customers",
                "column_name": "customer_id",
                "test_metadata": {"name": "unique", "kwargs": {"column_name": "customer_id"}},
            },
            "test.p.not_null_raw_customers_customer_id.2": {
                "resource_type": "test",
                "attached_node": "seed.p.raw_customers",
                "column_name": "customer_id",
                "test_metadata": {"name": "not_null", "kwargs": {"column_name": "customer_id"}},
            },
            "test.p.accepted_values_raw_customers_tier.3": {
                "resource_type": "test",
                "attached_node": "seed.p.raw_customers",
                "column_name": "tier",
                "test_metadata": {
                    "name": "accepted_values",
                    "kwargs": {"values": ["gold", "silver"], "column_name": "tier"},
                },
            },
            "test.p.unique_raw_orders_order_id.4": {
                "resource_type": "test",
                "attached_node": "seed.p.raw_orders",
                "column_name": "order_id",
                "test_metadata": {"name": "unique", "kwargs": {"column_name": "order_id"}},
            },
            "test.p.not_null_raw_orders_order_id.5": {
                "resource_type": "test",
                "attached_node": "seed.p.raw_orders",
                "column_name": "order_id",
                "test_metadata": {"name": "not_null", "kwargs": {"column_name": "order_id"}},
            },
            "test.p.relationships_raw_orders_customer_id.6": {
                "resource_type": "test",
                "attached_node": "seed.p.raw_orders",
                "column_name": "customer_id",
                "test_metadata": {
                    "name": "relationships",
                    "kwargs": {
                        "to": "ref('raw_customers')",
                        "field": "customer_id",
                        "column_name": "customer_id",
                        "model": "{{ get_where_subquery(ref('raw_orders')) }}",
                    },
                },
            },
        },
        "sources": {},
    }


def test_a_manifest_gives_the_same_schema_as_the_yaml():
    schema, _ = from_dbt(read_manifest(manifest()))
    assert schema.tables["raw_customers"].primary_key == ["customer_id"]
    gen = schema.tables["raw_customers"].columns["tier"].generator
    assert gen["strategy"] == "weighted_enum" and set(gen["values"]) == {"gold", "silver"}
    assert schema.tables["raw_orders"].columns["customer_id"].generator["ref"] == (
        "raw_customers.customer_id"
    )
    assert schema.tables["raw_customers"].description == "One row per customer."


def test_the_cli_writes_the_schema_and_the_metadata(tmp_path, jaffle):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    out = tmp_path / "jaffle.gen.json"
    code = cli.run_command(default_host(), "from-dbt", [str(jaffle), "-o", str(out)])
    assert code == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert set(doc["tables"]) == {"raw_customers", "raw_orders", "raw_payments"}
    meta = json.loads((tmp_path / "jaffle.gen.dbt-meta.json").read_text(encoding="utf-8"))
    assert meta["tables"]["raw_payments"]["columns"]["amount"]["data_type"] == "numeric(10,2)"
    amount = doc["tables"]["raw_payments"]["columns"]["amount"]
    assert (amount["type"], amount["precision"], amount["scale"]) == ("decimal", 10, 2)


def test_the_cli_refuses_a_bad_input_with_exit_2(tmp_path, capsys):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    code = cli.run_command(default_host(), "from-dbt", [str(tmp_path / "missing")])
    assert code == 2
    assert "no such file" in capsys.readouterr().err


def test_a_seed_with_the_name_of_a_source_is_the_same_table():
    """After `shape dbt-seeds` the project has a seed for every source it was generated from; a
    second `from-dbt` of the project must not see two tables (the seed block has no tests)."""
    relations = read_schema_yaml(SCHEMA_YML) + read_schema_yaml(
        "version: 2\nseeds:\n  - name: customers\n"
        "    config: {column_types: {customer_id: bigint}}\n"
    )
    schema, notes = from_dbt(relations)
    assert set(schema.tables) == {"customers", "orders"}
    assert schema.tables["customers"].primary_key == ["customer_id"]  # the source's tests won
    assert any("customers" in n and "seed" in n for n in notes if isinstance(n, str))


def test_two_sources_of_one_name_are_refused():
    two = (
        "version: 2\nsources:\n"
        "  - name: a\n    tables: [{name: t, columns: [{name: x}]}]\n"
        "  - name: b\n    tables: [{name: t, columns: [{name: x}]}]\n"
    )
    with pytest.raises(DbtProjectError, match="two sources are named 't'"):
        from_dbt(read_schema_yaml(two))


def test_a_manifest_that_has_both_the_seeds_and_the_sources_imports(jaffle, tmp_path):
    """The manifest of a project after `shape dbt-seeds`: a seed and a source per table. Made by
    hand here; test_dbt_build.py imports the manifest dbt itself wrote."""
    doc = manifest()
    doc["sources"] = {
        "source.p.raw.raw_customers": {
            "resource_type": "source",
            "source_name": "raw",
            "name": "raw_customers",
            "columns": {"customer_id": {"name": "customer_id", "data_type": "bigint"}},
        }
    }
    schema, notes = from_dbt(read_manifest(doc))
    assert set(schema.tables) == {"raw_customers", "raw_orders"}
    assert any("raw_customers" in n for n in notes if isinstance(n, str))


def test_a_source_relationships_test_belongs_to_the_child_table():
    """The manifest dbt writes for a source test has no attached_node, and the test depends on
    the parent as well as the child: the child is named by the test's `model` argument."""
    doc = manifest()
    for uid in [u for u in doc["nodes"] if u.startswith("test.")]:
        del doc["nodes"][uid]
    doc["nodes"]["test.p.rel"] = {
        "resource_type": "test",
        "attached_node": None,
        "column_name": "customer_id",
        "depends_on": {"nodes": ["seed.p.raw_customers", "seed.p.raw_orders"]},
        "test_metadata": {
            "name": "relationships",
            "kwargs": {
                "to": "ref('raw_customers')",
                "field": "customer_id",
                "model": "{{ get_where_subquery(ref('raw_orders')) }}",
            },
        },
    }
    by_name = {r.name: r for r in read_manifest(doc)}
    assert [t.kind for t in by_name["raw_orders"].columns["customer_id"].tests] == ["relationships"]
    assert "customer_id" not in by_name["raw_customers"].columns or not (
        by_name["raw_customers"].columns["customer_id"].tests
    )
