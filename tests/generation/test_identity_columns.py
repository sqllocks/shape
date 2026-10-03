"""W2-10 item 2 (core side): the optional ``identity`` key of a generation schema column, written
by ``shape from-ddl`` for IDENTITY / SERIAL / AUTO_INCREMENT columns, and the ``sql`` script sink's
T-SQL output for it. The SQL Server writer's side is in ``plugins/shape-fabric/tests``."""

from __future__ import annotations

import copy
import datetime as dt
import decimal
import json
from pathlib import Path

import pyarrow as pa
import pytest
from gen_fixtures import schema as fixture_schema

import shape
from shape.builtins.sinks.sql import SqlSink
from shape.cli.main import main
from shape.generation.ddl import from_ddl
from shape.generation.output import sql_options
from shape.generation.schema import GenSchema, GenSchemaError, json_schema, schema_problems

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "w2_10"


def doc_with(**changes):
    doc = copy.deepcopy(fixture_schema().to_dict())
    doc["tables"]["customer"]["columns"]["customer_id"].update(changes)
    return doc


def errors(doc):
    return [i for i in GenSchema.from_dict(doc).validate() if i.level == "error"]


# --- the key in the generation schema ----------------------------------------------------


def test_identity_is_an_optional_boolean_key_of_the_json_schema():
    props = json_schema()["$defs"]["column"]["properties"]
    assert props["identity"] == {"type": "boolean"}
    assert "identity" not in json_schema()["$defs"]["column"]["required"]
    assert schema_problems(doc_with(identity=True)) == []
    assert schema_problems(doc_with(identity=False)) == []
    assert schema_problems(doc_with(identity="yes"))  # a boolean, nothing else
    assert schema_problems(doc_with(identity=1))


def test_an_identity_column_round_trips_and_a_plain_one_has_no_key():
    doc = doc_with(identity=True)
    again = GenSchema.from_dict(doc)
    assert again.tables["customer"].columns["customer_id"].identity is True
    assert again.to_dict() == doc
    plain = fixture_schema().to_dict()
    assert all("identity" not in c for t in plain["tables"].values() for c in t["columns"].values())
    assert GenSchema.from_dict(plain).to_dict() == plain  # byte-identical to today


def test_compatibility_a_document_written_before_the_key_loads_unchanged():
    old = json.loads(json.dumps(fixture_schema().to_dict()))
    assert old["schema_version"] == 1  # still the version-1 document: the key is additive
    loaded = GenSchema.from_dict(old)
    assert all(not c.identity for t in loaded.tables.values() for c in t.columns.values())
    assert json.dumps(loaded.to_dict(), sort_keys=True) == json.dumps(old, sort_keys=True)


def test_identity_needs_an_integer_column_named_in_the_error():
    doc = doc_with(identity=True, type="string")
    (issue,) = errors(doc)
    assert "customer_id" in issue.message and "integer" in issue.message
    assert issue.location == "tables.customer.columns.customer_id"
    with pytest.raises(GenSchemaError, match="customer_id"):
        GenSchema.from_dict(doc).validate_or_raise()


def test_identity_needs_the_sequence_strategy_named_in_the_error():
    doc = doc_with(identity=True)
    doc["tables"]["customer"]["columns"]["customer_id"]["generator"] = {
        "strategy": "random_int",
        "min": 1,
        "max": 9,
    }
    (issue,) = errors(doc)
    assert "customer_id" in issue.message and "sequence" in issue.message


def test_identity_false_is_not_checked():
    doc = doc_with(identity=False, type="string")
    assert not [i for i in errors(doc) if "identity" in i.message]


def test_identity_key_is_a_column_property_not_a_generator_key():
    doc = doc_with(identity=True)
    doc["tables"]["customer"]["columns"]["customer_id"]["generator"]["identity"] = True
    issues = GenSchema.from_dict(doc).validate()
    assert any("identity" in i.message and i.level == "warning" for i in issues)


def test_the_column_options_for_sinks_carry_identity_with_its_start_and_step():
    doc = doc_with(identity=True)
    doc["tables"]["customer"]["columns"]["customer_id"]["generator"].update(start=100, step=5)
    meta = sql_options(GenSchema.from_dict(doc), "customer")["columns"]
    assert meta["customer_id"]["identity"] == {"start": 100, "step": 5}
    plain = sql_options(fixture_schema(), "customer")["columns"]
    assert all("identity" not in c for c in plain.values())  # nothing new for a plain schema


# --- shape from-ddl ----------------------------------------------------------------------

DDL = """\
CREATE TABLE [dbo].[customer] (
    customer_id INT IDENTITY(1,1) NOT NULL,
    name NVARCHAR(50) NOT NULL,
    CONSTRAINT PK_customer PRIMARY KEY (customer_id)
);
CREATE TABLE pg_t (
    pg_id SERIAL PRIMARY KEY,
    big_id BIGSERIAL,
    plain_id INT NOT NULL,
    label VARCHAR(10)
);
CREATE TABLE my_t (
    `my_id` INT AUTO_INCREMENT PRIMARY KEY,
    note VARCHAR(10)
);
"""


@pytest.mark.parametrize("smart", [True, False])
def test_from_ddl_writes_identity_for_identity_serial_and_auto_increment(smart):
    schema, _ = from_ddl(DDL, smart=smart)
    got = {(t.name, c.name): c.identity for t in schema.tables.values() for c in t.columns.values()}
    assert got[("customer", "customer_id")] is True
    assert got[("pg_t", "pg_id")] is True and got[("pg_t", "big_id")] is True
    assert got[("my_t", "my_id")] is True
    assert got[("pg_t", "plain_id")] is False and got[("customer", "name")] is False
    assert [i for i in schema.validate() if i.level == "error"] == []
    doc = schema.to_dict()
    assert doc["tables"]["customer"]["columns"]["customer_id"]["identity"] is True
    assert "identity" not in doc["tables"]["customer"]["columns"]["name"]
    assert schema_problems(doc) == []


def test_the_from_ddl_command_writes_the_key(tmp_path, capsys):
    sql = tmp_path / "s.sql"
    sql.write_text(DDL)
    out = tmp_path / "schema.json"
    assert main(["from-ddl", str(sql), "-o", str(out)]) == 0, capsys.readouterr().err
    doc = json.loads(out.read_text())
    assert doc["tables"]["customer"]["columns"]["customer_id"]["identity"] is True


# --- the sql script sink -----------------------------------------------------------------

SCHEMA = pa.schema(
    [
        ("id", pa.int64()),
        ("name", pa.string()),
        ("amount", pa.decimal128(10, 2)),
        ("created", pa.timestamp("us")),
    ]
)
BATCH = pa.record_batch(
    [
        [1, 2, 3],
        ["a", "b'c", None],
        [decimal.Decimal("1.50"), decimal.Decimal("2.00"), None],
        [dt.datetime(2026, 1, 2, 3, 4, 5)] * 3,
    ],
    schema=SCHEMA,
)
COLUMNS = {
    "id": {"type": "integer", "nullable": False},
    "name": {"type": "string", "nullable": True, "max_length": 40},
    "amount": {"type": "decimal", "nullable": True, "precision": 10, "scale": 2},
    "created": {"type": "timestamp", "nullable": True},
}


def script(tmp_path, dialect, columns=COLUMNS, **options):
    target = tmp_path / f"{dialect}.sql"
    SqlSink().write(
        str(target),
        "t",
        iter([BATCH]),
        sql_dialect=dialect,
        columns=columns,
        primary_key=["id"],
        schema_name="app",
        **options,
    )
    return target.read_text(encoding="utf-8")


@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse", "postgres", "mysql"])
def test_a_schema_without_identity_gives_byte_identical_scripts(tmp_path, dialect):
    golden = (FIXTURES / f"no_identity_{dialect}.sql").read_text(encoding="utf-8")
    golden = golden.replace("@VERSION@", shape.__version__)
    assert script(tmp_path, dialect) == golden
    assert script(tmp_path, dialect, identity=False) == golden  # a flag no column uses: no change


def identity_columns(start=1, step=1):
    cols = copy.deepcopy(COLUMNS)
    cols["id"]["identity"] = {"start": start, "step": step}
    return cols


def test_tsql_creates_an_identity_column_and_inserts_with_identity_insert(tmp_path):
    text = script(tmp_path, "tsql", identity_columns(100, 5))
    assert "[id]                           BIGINT IDENTITY(100, 5) NOT NULL" in text
    on = text.index("SET IDENTITY_INSERT [app].[t] ON;")
    insert = text.index("INSERT INTO [app].[t]")
    off = text.index("SET IDENTITY_INSERT [app].[t] OFF;")
    assert on < insert < off
    assert text.count("SET IDENTITY_INSERT [app].[t] ON;") == 1
    assert text.count("SET IDENTITY_INSERT [app].[t] OFF;") == 1
    assert "\nGO\n" in text  # statements are batch-separated as the rest of the script is


def test_identity_insert_surrounds_every_batch_group_exactly_once(tmp_path):
    text = script(tmp_path, "tsql", identity_columns(), batch_size=2)
    assert text.count("INSERT INTO") == 2  # two statements of two and one rows
    assert text.count("SET IDENTITY_INSERT [app].[t] ON;") == 1
    assert text.index("ON;") < text.index("INSERT INTO") < text.rindex("INSERT INTO")
    assert text.rindex("INSERT INTO") < text.index("OFF;")


def test_without_ddl_the_identity_insert_statements_are_still_written(tmp_path):
    text = script(tmp_path, "tsql", identity_columns(), ddl=False)
    assert "CREATE TABLE" not in text and "IDENTITY(" not in text
    assert "SET IDENTITY_INSERT [app].[t] ON;" in text  # the table has its identity already


def test_an_empty_table_has_no_identity_insert_around_nothing(tmp_path):
    target = tmp_path / "empty.sql"
    SqlSink().write(
        str(target), "t", iter([]), sql_dialect="tsql", columns=identity_columns(), schema=SCHEMA,
        primary_key=["id"],
    )  # fmt: skip
    text = target.read_text()
    assert "IDENTITY(1, 1)" in text and "IDENTITY_INSERT" not in text


def test_the_warehouse_dialect_ignores_identity_with_a_documented_note(tmp_path):
    golden = (FIXTURES / "no_identity_tsql-fabric-warehouse.sql").read_text(encoding="utf-8")
    text = script(tmp_path, "tsql-fabric-warehouse", identity_columns())
    assert "IDENTITY" not in text.replace("-- NOTE: identity", "")
    assert "-- NOTE: identity is not supported by Fabric Warehouse and was ignored" in text
    body = text.replace(
        "-- NOTE: identity is not supported by Fabric Warehouse and was ignored (id)\n", ""
    )
    assert body == golden.replace("@VERSION@", shape.__version__)


def test_other_dialects_ignore_identity_the_same_way(tmp_path):
    for dialect in ("postgres", "mysql"):
        text = script(tmp_path, dialect, identity_columns())
        assert "IDENTITY" not in text.replace("-- NOTE: identity", "")
        assert "-- NOTE: identity is only used by the tsql dialect and was ignored" in text


def test_the_script_is_deterministic_with_identity(tmp_path):
    a = script(tmp_path, "tsql", identity_columns())
    b = script(tmp_path, "tsql", identity_columns())
    assert a == b
