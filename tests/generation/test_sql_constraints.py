"""W9-04: derive write constraints without accepting SQL text."""

from decimal import Decimal

import pytest

from shape.generation.output import TargetOptions, sql_options
from shape.generation.schema import BusinessRule, Column, GenSchema, Model, Relationship, Table


def schema():
    parent = Table("parent", {"id": Column("id", "integer", {"strategy": "sequence"})}, ["id"])
    columns = {
        "id": Column("id", "integer", {"strategy": "sequence"}),
        "parent_id": Column(
            "parent_id", "integer", {"strategy": "foreign_key", "ref": "parent.id"}
        ),
        "amount": Column("amount", "decimal", {"strategy": "x"}),
        "limit": Column("limit", "decimal", {"strategy": "x"}),
        "status": Column("status", "string", {"strategy": "constant", "value": "O'Reilly"}),
    }
    return GenSchema(
        Model("demo", "demo"),
        {"parent": parent, "child": Table("child", columns, ["id"])},
        [Relationship("child_parent", "parent", "child", ["id"], ["parent_id"])],
        [
            BusinessRule("amount_limit", "cross_column", "amount <= limit", "child"),
            BusinessRule(
                "amount_range", "constraint", "amount BETWEEN -1.25 AND 999999999999999999", "child"
            ),
        ],
    )


def render(expr):
    from shape.generation.sql_constraints import render_expression

    return render_expression(
        expr,
        quote=lambda n: '"' + n.replace('"', '""') + '"',
        literal=lambda v: "'" + v.replace("'", "''") + "'" if isinstance(v, str) else str(v),
        columns=schema().tables["child"].column_names,
    )


def test_keys_default_deduplicates_relationship_and_generator():
    options = sql_options(schema(), "child", contract={"columns": {"status": {"unique": True}}})
    assert options["foreign_keys"] == [
        {
            "columns": ["parent_id"],
            "ref_table": "parent",
            "ref_columns": ["id"],
            "name": "child_parent",
        }
    ]
    assert options["unique"] == [["status"]]
    assert options["checks"] == []
    assert options["defaults"] == {}
    assert options["primary_key"] == ["id"]


def test_all_derives_safe_checks_numeric_precision_enum_and_default():
    contract = {
        "tables": {
            "child": {
                "columns": {
                    "amount": {"min": 0, "max": 10},
                    "status": {"accepted_values": ["O'Reilly", "active"]},
                }
            }
        }
    }
    options = sql_options(schema(), "child", constraints="all", contract=contract)
    expressions = {c["name"]: render(c["expression"]) for c in options["checks"]}
    assert expressions["amount_limit"] == '("amount" <= "limit")'
    assert (
        expressions["amount_range"] == '(("amount" >= -1.25) AND ("amount" <= 999999999999999999))'
    )
    assert '("amount" >= 0)' in expressions.values()
    assert '("amount" <= 10)' in expressions.values()
    assert "(\"status\" IN ('O''Reilly', 'active'))" in expressions.values()
    assert options["defaults"] == {"status": "O'Reilly"}


def test_none_keeps_primary_only_and_disable_keeps_keys():
    assert set(sql_options(schema(), "child", constraints="none")) == {"primary_key", "columns"}
    assert sql_options(schema(), "child", constraints="disable")["foreign_keys"]
    with pytest.raises(ValueError, match="constraints"):
        sql_options(schema(), "child", constraints="maybe")


def test_target_options_carries_mode_contract_and_derived_options():
    options = TargetOptions(constraints="all", contract={"columns": {"status": {"unique": True}}})
    found = options.for_sink("postgres", schema(), "child")
    assert found["unique"] == [["status"]]
    assert found["checks"]
    assert found["defaults"]
    assert (
        TargetOptions(constraints="none")
        .for_sink("postgres", schema(), "child")
        .get("foreign_keys")
        is None
    )


@pytest.mark.parametrize(
    "rule",
    [
        "amount >= 0; DROP TABLE child",
        "amount >= nan",
        "amount >= func()",
        "unknown > 0",
        "amount BETWEEN 5 AND 1",
    ],
)
def test_unsafe_or_invalid_generation_rules_fail_closed(rule):
    definition = schema()
    definition.business_rules = [BusinessRule("unsafe", "constraint", rule, "child")]
    with pytest.raises(ValueError, match="unsafe"):
        sql_options(definition, "child", constraints="all")


@pytest.mark.parametrize(
    "expr",
    [
        "amount > 0",
        {"op": "raw", "sql": "amount > 0"},
        {"op": "=", "left": {"column": "missing"}, "right": {"literal": 1}},
        {"op": "=", "left": {"column": "amount"}, "right": {"literal": float("nan")}},
        {"op": "=", "left": {"column": "amount"}, "right": {"literal": {"sql": "NOW()"}}},
        {"op": "and", "args": []},
        {"op": "in", "left": {"column": "status"}, "values": []},
    ],
)
def test_renderer_rejects_unstructured_or_malformed_ast(expr):
    with pytest.raises(ValueError):
        render(expr)


def test_ast_literal_is_data_and_tree_is_bounded():
    attack = "x'); DROP TABLE parent; --"
    expr = {"op": "in", "left": {"column": "status"}, "values": [attack]}
    assert render(expr) == "(\"status\" IN ('x''); DROP TABLE parent; --'))"
    assert render(
        {
            "op": "=",
            "left": {"column": "amount"},
            "right": {"literal": Decimal("1.000000000000000001")},
        }
    ).endswith("1.000000000000000001)")
    for _ in range(40):
        expr = {"op": "and", "args": [expr]}
    with pytest.raises(ValueError, match="deep"):
        render(expr)


@pytest.mark.parametrize(
    "contract",
    [
        {"columns": {"missing": {"unique": True}}},
        {"columns": {"amount": {"min": "0 OR 1=1"}}},
        {"columns": {"status": {"accepted_values": []}}},
        {"columns": {"amount": {"min": float("inf")}}},
    ],
)
def test_bad_contract_constraints_refused(contract):
    with pytest.raises(ValueError):
        sql_options(schema(), "child", constraints="all", contract=contract)


def test_invalid_composite_reference_is_refused():
    definition = schema()
    definition.relationships[0].parent_columns = ["id", "missing"]
    with pytest.raises(ValueError, match="child_parent"):
        sql_options(definition, "child")


def test_delta_and_script_options_receive_same_derived_model():
    from shape.generation.output import _options

    definition = schema()
    expected = sql_options(definition, "child", constraints="all")
    for sink in ("delta", "postgres"):
        result = TargetOptions(constraints="all").for_sink(sink, definition, "child")
        assert result["checks"] == expected["checks"]
        assert result["columns"] == expected["columns"]
    for sink in ("delta", "sql"):
        result = _options(sink, definition, "child", {"constraints": "all"})
        assert result["checks"] == expected["checks"]


def test_composite_generator_reference_and_null_enum():
    definition = schema()
    definition.tables["child"].columns["parent_id"].generator = {
        "strategy": "composite_foreign_key",
        "ref_table": "parent",
        "ref_columns": ["id"],
    }
    definition.relationships = []
    options = sql_options(
        definition,
        "child",
        constraints="all",
        contract={"columns": {"status": {"accepted_values": [None]}}},
    )
    assert options["foreign_keys"][0]["columns"] == ["parent_id"]
    assert any(render(c["expression"]) == '("status" IS NULL)' for c in options["checks"])


def test_huge_numeric_exponent_refused():
    definition = schema()
    definition.business_rules = [
        BusinessRule("huge", "constraint", "amount > 1e999999999", "child")
    ]
    with pytest.raises(ValueError, match="huge"):
        sql_options(definition, "child", constraints="all")


def test_foreign_key_names_and_column_pairs_are_unambiguous():
    definition = schema()
    definition.relationships.append(Relationship("child_parent", "parent", "child", ["id"], ["id"]))
    with pytest.raises(ValueError, match="duplicate.*child_parent"):
        sql_options(definition, "child")
    definition = schema()
    definition.relationships[0].child_columns = ["parent_id", "parent_id"]
    definition.relationships[0].parent_columns = ["id", "id"]
    with pytest.raises(ValueError, match="child_parent"):
        sql_options(definition, "child")


@pytest.mark.parametrize("tables", [[], "child", 1])
def test_contract_table_container_must_be_an_object(tables):
    with pytest.raises(ValueError, match="contract tables"):
        sql_options(schema(), "child", constraints="all", contract={"tables": tables})


def test_numeric_literals_are_bounded_before_sql_rendering():
    expression = {
        "op": "=",
        "left": {"column": "amount"},
        "right": {"literal": Decimal("1e999999999")},
    }
    with pytest.raises(ValueError, match="exponent"):
        render(expression)


def test_ast_node_limit_counts_operands_and_enum_values():
    comparison = {"op": "=", "left": {"column": "amount"}, "right": {"literal": 1}}
    with pytest.raises(ValueError, match="nodes"):
        render({"op": "and", "args": [comparison] * 342})
    enum = {"op": "in", "left": {"column": "status"}, "values": list(range(1023))}
    with pytest.raises(ValueError, match="nodes"):
        render(enum)
    # One conjunction plus 341 expressions and 682 operands is exactly 1024 nodes.
    assert render({"op": "and", "args": [comparison] * 341}).count('"amount"') == 341
