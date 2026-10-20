"""Safe, backend-aware DDL for the derived constraint vocabulary."""

from decimal import Decimal

import pytest


def compile_ddl(options, dialect="postgres", **kwargs):
    from shape.generation.write_constraints import compile_constraints

    return compile_constraints(
        options,
        columns=["id", "parent_id", "amount", "name"],
        table="child",
        dialect=dialect,
        **kwargs,
    )


def declaration():
    return {
        "foreign_keys": [
            {
                "columns": ["parent_id"],
                "ref_table": "parent",
                "ref_columns": ["id"],
                "name": "fk_child_parent",
            }
        ],
        "unique": [["id", "name"]],
        "checks": [
            {
                "name": "ck_amount",
                "expression": {"op": ">=", "left": {"column": "amount"}, "right": {"literal": 0}},
            }
        ],
        "defaults": {"amount": Decimal("1.25"), "name": "'\\'; DROP TABLE x; --"},
    }


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "duckdb", "tsql"])
def test_enforced_constraints_and_defaults_are_separate_from_foreign_key_phase(dialect):
    result = compile_ddl(declaration(), dialect, schema_name="app")
    assert any("UNIQUE" in line for line in result.definitions)
    assert any("CHECK" in line and "ck_amount" in line for line in result.definitions)
    assert not any("FOREIGN KEY" in line for line in result.definitions)
    assert len(result.foreign_keys) == 1
    assert "REFERENCES" in result.foreign_keys[0] and "parent" in result.foreign_keys[0]
    assert result.defaults["amount"] == "1.25"
    assert result.notes == ()


def test_snowflake_informational_keys_and_unsupported_check_note():
    options = declaration()
    options["checks"].append(options["checks"][0] | {"name": "ck_other"})
    result = compile_ddl(options, "snowflake")
    assert "NOT ENFORCED" in result.definitions[0]
    assert "NOT ENFORCED" in result.foreign_keys[0]
    assert not any("CHECK" in line for line in result.definitions)
    assert len(result.notes) == 1 and "CHECK" in result.notes[0]


def test_databricks_check_is_an_after_create_alter_and_unique_default_warn_once():
    result = compile_ddl(declaration(), "databricks")
    assert result.definitions == ()
    assert len(result.after_create) == 1 and "CHECK" in result.after_create[0]
    assert "NOT ENFORCED" in result.foreign_keys[0]
    assert result.defaults == {}
    assert len(result.notes) == 2


def test_fabric_warehouse_unique_is_nonclustered_and_not_enforced():
    result = compile_ddl(declaration(), "tsql-fabric-warehouse")
    assert any(
        "UNIQUE NONCLUSTERED" in line and "NOT ENFORCED" in line for line in result.after_create
    )
    assert "NOT ENFORCED" in result.foreign_keys[0]
    assert any("CHECK" in note for note in result.notes)


@pytest.mark.parametrize(
    "options",
    [
        {"checks": [{"name": "ck", "expression": "amount > 0; DROP TABLE x"}]},
        {
            "checks": [
                {
                    "name": "ck",
                    "expression": {
                        "op": "=",
                        "left": {"column": "missing"},
                        "right": {"literal": 0},
                    },
                }
            ]
        },
        {
            "checks": [
                {
                    "name": "ck",
                    "expression": {"op": "is_null", "left": {"column": "id"}},
                    "raw_sql": "ignored",
                }
            ]
        },
        {"defaults": {"missing": 0}},
        {"defaults": {"amount": float("nan")}},
        {"defaults": {"amount": {"sql": "NOW()"}}},
        {"unique": [["missing"]]},
        {"unique": [["id", "id"]]},
        {"unique": ["id"]},
        {
            "foreign_keys": [
                {
                    "columns": ["parent_id"],
                    "ref_table": "parent",
                    "ref_columns": ["id", "name"],
                    "name": "fk",
                }
            ]
        },
        {
            "foreign_keys": [
                {
                    "columns": ["parent_id"],
                    "ref_table": "parent",
                    "ref_columns": ["id"],
                    "name": "fk\nGO",
                }
            ]
        },
        {
            "foreign_keys": [
                {"columns": ["missing"], "ref_table": "parent", "ref_columns": ["id"], "name": "fk"}
            ]
        },
    ],
)
def test_malformed_or_unsafe_options_are_refused_before_sql(options):
    with pytest.raises(ValueError):
        compile_ddl(options)


def test_reference_prefix_and_quoted_schema_are_preserved():
    result = compile_ddl(declaration(), schema_name='a"b', table_prefix="gen_")
    assert 'REFERENCES "a""b"."gen_parent" ("id")' in result.foreign_keys[0]


def test_literal_function_names_are_data_not_sql_and_compound_defaults_are_refused():
    result = compile_ddl({"defaults": {"name": "CURRENT_TIMESTAMP"}})
    assert result.defaults["name"] == "'CURRENT_TIMESTAMP'"


def test_duplicate_constraint_names_are_refused():
    options = declaration()
    options["checks"][0]["name"] = "fk_child_parent"
    with pytest.raises(ValueError, match="duplicate"):
        compile_ddl(options)


@pytest.mark.parametrize("exponent", [500, 1000])
def test_large_finite_decimal_literals_do_not_coerce_through_float(exponent):
    value = Decimal(f"1E{exponent}")
    assert compile_ddl({"defaults": {"amount": value}}).defaults["amount"] == format(value, "f")


@pytest.mark.parametrize(
    "value", [Decimal("1E1001"), Decimal("NaN"), Decimal("Infinity"), float("inf")]
)
def test_unbounded_or_nonfinite_literal_is_refused(value):
    with pytest.raises(ValueError):
        compile_ddl({"defaults": {"amount": value}})


@pytest.mark.parametrize("value", [r"C:\tmp", "'\\'; DROP TABLE x; --", "café"])
def test_mysql_string_default_is_independent_of_backslash_sql_mode(value):
    result = compile_ddl({"defaults": {"name": value}}, "mysql")
    assert result.defaults["name"] == "_utf8mb4 X'" + value.encode().hex() + "'"


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "tsql", "tsql-fabric-warehouse"])
def test_script_sink_writes_constraints_and_notes_in_their_phases(tmp_path, dialect, caplog):
    import pyarrow as pa

    from shape.builtins.sinks.sql import SqlSink

    path = tmp_path / "child.sql"
    assert (
        SqlSink().write(
            str(path),
            "child",
            [
                pa.RecordBatch.from_pydict(
                    {"id": [1], "parent_id": [1], "amount": [2], "name": ["example"]}
                )
            ],
            sql_dialect=dialect,
            **declaration(),
        )
        == 1
    )
    text = path.read_text()
    assert "UNIQUE" in text
    assert "FOREIGN KEY" in text and text.index("FOREIGN KEY") > text.index("CREATE TABLE")
    if dialect == "tsql-fabric-warehouse":
        assert "-- NOTE:" in text and "cannot hold CHECK" in text
        assert "cannot hold DEFAULT" in text
        assert sum("cannot hold CHECK" in record.message for record in caplog.records) == 1
        assert "CHECK (" not in text
    else:
        assert "ck_amount" in text and "DEFAULT 1.25" in text
