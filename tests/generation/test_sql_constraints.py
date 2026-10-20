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


def test_legacy_keep_retains_primary_only_while_omitted_policy_defaults_to_keys():
    definition = schema()
    default = TargetOptions().for_sink("postgres", definition, "child")
    legacy = TargetOptions(constraints="keep").for_sink("postgres", definition, "child")
    none = TargetOptions(constraints="none").for_sink("postgres", definition, "child")
    assert default["foreign_keys"]
    assert legacy == none
    assert set(legacy) == {"primary_key", "columns"}


@pytest.mark.parametrize("sink", ["synapse", "kql"])
def test_default_does_not_add_constraints_to_targets_outside_the_requested_scope(sink):
    definition = schema()
    actual = TargetOptions().for_sink(sink, definition, "child")
    assert actual == TargetOptions(constraints="none").for_sink(sink, definition, "child")
    assert set(actual) == {"primary_key", "columns"}


@pytest.mark.parametrize(
    "features",
    [
        True,
        {"not_null": ["id"]},
        {
            "check": {
                "positive": {"op": ">=", "left": {"column": "amount"}, "right": {"literal": 0}}
            }
        },
    ],
)
def test_delta_feature_constraints_do_not_replace_generation_policy(features):
    from shape.generation.output import _options

    options = _options("delta", schema(), "child", {"constraints": features})
    assert options["constraints"] == features
    assert options["foreign_keys"]


def test_explicit_delta_generation_policy_is_separate_from_feature_mapping():
    from shape.generation.output import _options

    options = _options("delta", schema(), "child", {"constraints": True, "sql_constraints": "all"})
    assert options["constraints"] is True
    assert options["checks"] and options["defaults"]


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


def _write_engine(rows=4):
    from shape.generation.engine import Engine

    definition = schema()
    definition.business_rules = []
    definition.tables["child"].columns = {
        name: col
        for name, col in definition.tables["child"].columns.items()
        if name in {"id", "parent_id"}
    }
    return Engine(definition, row_counts={"parent": rows, "child": rows}, chunk_rows=2)


def test_constraint_preparation_precedes_parent_first_data_and_buffers_one_batch(monkeypatch):
    import threading

    import pyarrow as pa

    from shape.generation.output import write_targets

    prepared = threading.Event()
    events = []
    engine = _write_engine()
    original = engine.iter_chunks

    def chunks(name, size=None):
        for index, batch in enumerate(original(name, size)):
            if index:
                assert prepared.is_set(), "more than one batch was buffered before preparation"
            yield batch

    engine.iter_chunks = chunks

    class Sink:
        def prepare_constraints(self, uri, plans):
            assert set(plans) == {"parent", "child"}
            for plan in plans.values():
                assert isinstance(plan["schema"], pa.Schema)
                assert plan["first_batch"].num_rows == 2
            assert plans["child"]["foreign_keys"][0]["ref_table"] == "parent"
            events.append("prepare")
            prepared.set()

        def write(self, uri, table, batches, **options):
            assert prepared.is_set()
            assert options["prepared_constraints"] is True
            if table == "child":
                assert events[-1] == "parent", "child loaded before parent completed"
            events.append(table)
            return sum(batch.num_rows for batch in batches)

    sink = Sink()
    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", sink))
    found = write_targets(engine, ["postgresql://u@localhost/db"], TargetOptions(), max_workers=2)
    assert list(found.values()) == [{"parent": 4, "child": 4}]
    assert events == ["prepare", "parent", "child"]


def test_empty_tables_are_prepared_with_real_arrow_schema(monkeypatch):
    from shape.generation.output import write_targets

    plans = {}

    class Sink:
        def prepare_constraints(self, uri, options):
            plans.update(options)

        def write(self, uri, table, batches, **options):
            assert options["schema"] == plans[table]["schema"]
            assert options["schema"].names
            return sum(batch.num_rows for batch in batches)

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", Sink()))
    found = write_targets(_write_engine(0), ["postgresql://u@localhost/db"], TargetOptions())
    assert list(found.values()) == [{"parent": 0, "child": 0}]
    assert all(plan["first_batch"].num_rows == 0 for plan in plans.values())


def test_prepare_failure_never_enters_data_load(monkeypatch):
    from shape.generation.output import write_targets

    class Sink:
        def prepare_constraints(self, uri, plans):
            raise ValueError("fk_child_parent: cannot restore referenced constraint")

        def write(self, *args, **options):
            raise AssertionError("data load started after preparation failed")

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", Sink()))
    with pytest.raises(ValueError, match="fk_child_parent"):
        write_targets(_write_engine(), ["postgresql://u@localhost/db"], TargetOptions())


def test_all_target_options_are_validated_before_any_connection(monkeypatch):
    from shape.generation.output import write_targets

    class Sink:
        def write(self, *args, **options):
            raise AssertionError("sink opened before all options were validated")

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", Sink()))
    with pytest.raises(ValueError, match="invalid contract column"):
        write_targets(
            _write_engine(0),
            ["postgresql://u@localhost/db"],
            TargetOptions(
                constraints="all", contract={"tables": {"child": {"columns": {"bad": {"min": 0}}}}}
            ),
        )


def test_post_pass_empty_result_uses_schema_when_there_is_no_batch(monkeypatch):
    from shape.generation.output import write_targets

    engine = _write_engine(0)
    engine.schema.business_rules = [BusinessRule("valid", "constraint", "id >= 0", "parent")]
    prepared = {}

    class Sink:
        def prepare_constraints(self, uri, plans):
            prepared.update(plans)
            assert all(plan["first_batch"] is None for plan in plans.values())
            assert all(plan["schema"].names for plan in plans.values())

        def write(self, uri, table, batches, **options):
            assert table in prepared
            return sum(batch.num_rows for batch in batches)

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", Sink()))
    assert list(
        write_targets(engine, ["postgresql://u@localhost/db"], TargetOptions()).values()
    ) == [{"parent": 0, "child": 0}]


def test_self_reference_prepares_normally_and_cross_table_cycle_refuses_before_connection(
    monkeypatch,
):
    from shape.generation.engine import Engine
    from shape.generation.output import write_targets
    from shape.generation.schema import GenSchemaError

    engine = _write_engine()
    engine.schema.relationships.append(
        Relationship("self_parent", "parent", "parent", ["id"], ["id"], "self_referencing")
    )
    prepared = []

    class Sink:
        def prepare_constraints(self, uri, plans):
            prepared.append(plans)

        def write(self, uri, table, batches, **options):
            return sum(batch.num_rows for batch in batches)

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", Sink()))
    write_targets(engine, ["postgresql://u@localhost/db"], TargetOptions())
    assert prepared[0]["parent"]["foreign_keys"][0]["ref_table"] == "parent"
    definition = _write_engine().schema
    definition.relationships.append(Relationship("reverse", "child", "parent", ["id"], ["id"]))
    prepared.clear()
    with pytest.raises(GenSchemaError, match="child.*parent"):
        write_targets(Engine(definition), ["postgresql://u@localhost/db"], TargetOptions())
    assert prepared == []
