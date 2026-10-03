"""AUD-gen: the generation schema document (``GenSchema``)."""

from __future__ import annotations

from shape.generation.schema import GenSchema

_MINIMAL = {
    "schema_version": 1,
    "model": {"name": "m"},
    "tables": {
        "t": {
            "name": "t",
            "primary_key": ["id"],
            "columns": {
                "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}}
            },
        }
    },
}


def test_a_document_without_generation_reads_with_the_defaults():
    # 205: generation is optional in generation-schema-v1.json, but from_dict raised
    # KeyError: 'generation'.
    schema = GenSchema.from_dict(_MINIMAL)
    assert schema.generation.scale == "small"
    assert GenSchema.from_dict(schema.to_dict()).to_dict() == schema.to_dict()


def test_to_dict_writes_dataclass_rows_in_a_generator_as_json():
    # 205: inline AddressReference rows in a generator: TypeError: Object of type
    # AddressReference is not JSON serializable.
    from dataclasses import dataclass

    @dataclass
    class Row:
        city: str
        zip: str

    schema = GenSchema.from_dict(_MINIMAL)
    schema.tables["t"].columns["id"].generator["rows"] = [Row("Austin", "78701")]
    doc = schema.to_dict()
    assert doc["tables"]["t"]["columns"]["id"]["generator"]["rows"] == [
        {"city": "Austin", "zip": "78701"}
    ]


def _orders(rules=(), corr=None, extra=None, rels=None):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from aud_gen_fixtures import build, col

    line = {
        "line_id": col("sequence"),
        "order_id": col("foreign_key", ref="order.order_id"),
        "amount": col("uniform", "float", low=1.0, high=50.0),
        "cost": col("uniform", "float", low=1.0, high=50.0),
    }
    line.update(extra or {})
    return build(
        {
            "order": (["order_id"], {"order_id": col("sequence")}),
            "order_line": (["line_id"], line),
        },
        {"order": 5, "order_line": 20},
        rels=(("order", "order_line", "order_id", "order_id"),) if rels is None else rels,
        rules=rules,
        corr=corr,
    )


def _messages(schema):
    return [f"{i.location}: {i.message}" for i in schema.validate()]


def _rule(text, kind="cross_column", **kw):
    return {"name": "r", "type": kind, "rule": text, "table": "order_line", **kw}


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "rule",
    [
        _rule("cost < amout"),
        _rule("cost is less than amount"),
        _rule("cost != amount"),
        _rule("order_line.cost >= amount", kind="cross_table", via="order_id"),
        _rule("order_line.cost >= order.total", kind="cross_table", via="order_id"),
        _rule("order_line.cost >= order.order_id", kind="cross_table", via="oid"),
    ],
)
def test_a_rule_the_engine_cannot_check_is_reported(rule):
    # 193: each of these was ignored by generation and validate() said nothing.
    messages = _messages(_orders(rules=(rule,)))
    assert any(m.startswith("business_rules.r") for m in messages), messages


@pytest.mark.parametrize(
    "pair",
    [["amout", "cost", 0.5], ["cost", "cost", 0.9], ["amount", "cost", 7]],
)
def test_a_bad_correlation_is_reported(pair):
    # 193: unknown columns, a column with itself and |r| > 1 were ignored silently.
    messages = _messages(_orders(corr={"order_line": [pair]}))
    assert any(m.startswith("correlated_columns.order_line") for m in messages), messages


def test_a_correlation_on_an_unknown_table_is_reported():
    messages = _messages(_orders(corr={"orderline": [["amount", "cost", 0.5]]}))
    assert any(m.startswith("correlated_columns.orderline") for m in messages), messages


@pytest.mark.parametrize(
    "generator",
    [
        {"strategy": "computed", "rule": "sum_children", "child_table": "x", "child_column": "a"},
        {"strategy": "computed", "rule": "sum_childs", "child_table": "order", "child_column": "a"},
        {"strategy": "foreign_key", "ref": "order"},
        {"strategy": "foreign_key", "ref": "order.order_id.extra"},
        {"strategy": "composite_foreign_key", "ref_table": "ghost", "ref_columns": ["a"]},
        {"strategy": "derived", "source": "ghost"},
    ],
)
def test_an_unresolved_reference_in_a_generator_is_reported(generator):
    # 193: a computed column on an unknown child table or rule, a foreign key without a column,
    # and references to unknown tables or columns passed validate().
    extra = {"x": {"type": "float", "generator": generator}}
    messages = _messages(_orders(extra=extra))
    assert any(m.startswith("tables.order_line.columns.x") for m in messages), messages


def test_unknown_tables_in_counts_and_uneven_relationships_are_reported():
    schema = _orders(rels=(("order", "order_line", "order_id", "order_id"),))
    schema.generation.scales["s"]["ordr"] = 5
    schema.generation.derived_counts["lines"] = {"per_parent": "order", "ratio": 2}
    schema.relationships[0].child_columns.append("line_id")
    messages = _messages(schema)
    assert any("ordr" in m for m in messages), messages
    assert any("lines" in m for m in messages), messages
    assert any(m.startswith("relationships.") and "columns" in m for m in messages), messages


def test_a_valid_schema_has_no_issue():
    rules = (
        _rule("cost < amount"),
        _rule("cost > 0"),
        _rule("order_line.order_id >= order.order_id", kind="cross_table", via="order_id"),
    )
    assert _messages(_orders(rules=rules, corr={"order_line": [["amount", "cost", 0.5]]})) == []
