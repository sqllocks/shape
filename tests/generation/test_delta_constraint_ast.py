"""W9-07 integration of safe schema-derived Delta constraints (depends W9-04)."""

import deltalake
import pyarrow as pa
import pytest

from shape.builtins.sinks import delta
from shape.builtins.sinks.delta import DeltaSink


def batch(ids=(1, 2)):
    return pa.record_batch({"id": list(ids), "label": ["a"] * len(ids)})


def write(tmp_path, **options):
    DeltaSink().write(str(tmp_path), "t", [batch()], **options)
    return deltalake.DeltaTable(str(tmp_path / "t"))


def read(table):
    return pa.table(
        deltalake.QueryBuilder().register("t", table).execute("SELECT * FROM t").read_all()
    )


@pytest.mark.conformance
def test_wanted2_schema_derived_ast_checks_enforced_atomically(tmp_path):
    """SHAPE-SINK-002: the write path consumes schema-derived safe expressions."""
    expression = {"op": ">", "left": {"column": "id"}, "right": {"literal": 0}}
    table = write(tmp_path, constraints={"check": {"positive": expression}})
    assert "delta.constraints.positive" in table.metadata().configuration
    before = table.version()
    with pytest.raises(ValueError, match="positive"):
        DeltaSink().write(str(tmp_path), "t", [batch((0,))], mode="append")
    actual = deltalake.DeltaTable(str(tmp_path / "t"))
    assert actual.version() == before
    assert read(actual).to_pydict() == batch().to_pydict()


def test_wanted2_free_text_check_refused_before_storage(tmp_path, monkeypatch):
    def no_storage(*args, **kwargs):
        pytest.fail("free-text check must be refused before opening storage")

    monkeypatch.setattr(delta, "_location", no_storage)
    with pytest.raises(ValueError, match="constraint.*structured"):
        write(tmp_path, constraints={"check": {"positive": "id > 0"}})


def test_wanted2_generation_and_contract_checks_reach_delta(tmp_path):
    from shape.generation.output import sql_options
    from shape.generation.schema import BusinessRule, Column, GenSchema, Model, Table

    schema = GenSchema(
        Model("ast", "ast"),
        {
            "t": Table(
                "t",
                {
                    "id": Column("id", "integer", {"strategy": "sequence"}),
                    "label": Column("label", "string", {"strategy": "constant", "value": "a"}),
                },
            )
        },
        business_rules=[BusinessRule("positive", "constraint", "id > 0", "t")],
    )
    options = sql_options(
        schema,
        "t",
        constraints="all",
        contract={"columns": {"label": {"accepted_values": ["a", "O'Reilly"]}}},
    )
    table = write(tmp_path, **options)
    stored = table.metadata().configuration
    assert "delta.constraints.positive" in stored
    assert "delta.constraints.ck_t_label_enum" in stored
    before = table.version()
    with pytest.raises(ValueError, match="positive"):
        DeltaSink().write(str(tmp_path), "t", [batch((0,))], mode="append")
    assert deltalake.DeltaTable(str(tmp_path / "t")).version() == before


@pytest.mark.parametrize(
    "expression",
    [
        {"op": ">", "left": {"column": "missing"}, "right": {"literal": 0}},
        {"op": "raw", "sql": "id > 0"},
        {"op": ">", "left": {"column": "id"}, "right": {"literal": float("nan")}},
        {"op": "in", "left": {"column": "id"}, "values": [{"sql": "0"}]},
    ],
)
def test_wanted2_invalid_ast_refused_before_storage(tmp_path, monkeypatch, expression):
    def no_storage(*args, **kwargs):
        pytest.fail("invalid constraints must be refused before opening storage")

    monkeypatch.setattr(delta, "_location", no_storage)
    with pytest.raises(ValueError, match="constraint"):
        write(tmp_path, checks=[{"name": "safe", "expression": expression}])


def test_wanted2_enum_literals_preserve_quotes_and_sql_text_as_values(tmp_path):
    values = ["quote'and\\slash", "x'); DROP TABLE t; --"]
    expression = {"op": "in", "left": {"column": "label"}, "values": values}
    rows = pa.record_batch({"id": [1, 2], "label": values})
    DeltaSink().write(
        str(tmp_path), "t", [rows], checks=[{"name": "choices", "expression": expression}]
    )
    table = deltalake.DeltaTable(str(tmp_path / "t"))
    assert read(table).to_pydict() == rows.to_pydict()
    before = table.version()
    with pytest.raises(ValueError, match="choices"):
        DeltaSink().write(str(tmp_path), "t", [batch((3,))], mode="append")
    assert deltalake.DeltaTable(str(tmp_path / "t")).version() == before


@pytest.mark.parametrize("name", [[], {"bad": 1}])
def test_wanted2_compound_constraint_names_refused_before_storage(tmp_path, monkeypatch, name):
    def no_storage(*args, **kwargs):
        pytest.fail("compound constraint names must be refused before opening storage")

    monkeypatch.setattr(delta, "_location", no_storage)
    expression = {"op": ">", "left": {"column": "id"}, "right": {"literal": 0}}
    with pytest.raises(ValueError, match="constraint"):
        write(tmp_path, checks=[{"name": name, "expression": expression}])
