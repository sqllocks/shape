"""Cloud DDL and catalog safeguards; live service enforcement remains an Owner action."""

import pyarrow as pa
import pytest
from shape_databases import DatabricksSink, SnowflakeSink
from shape_databases.testing import FakeDatabricks, FakeSnowflake

from shape.errors import ShapeError


@pytest.fixture(params=["snowflake", "databricks"])
def cloud(request):
    if request.param == "snowflake":
        server = FakeSnowflake()
        sink = SnowflakeSink(connect=server.connect)
        return (
            sink,
            server,
            "snowflake://shape@account/db/PUBLIC",
            {"password": "fake-cloud-password"},
            "PUBLIC",
        )
    server = FakeDatabricks()
    return (
        DatabricksSink(connect=server.connect),
        server,
        "databricks://adb-123.azuredatabricks.net/sql/1.0/warehouses/test?catalog=main&schema=demo",
        {"token": "fake-cloud-token"},
        "demo",
    )


def batch(id=1):
    return pa.RecordBatch.from_pydict({"id": [id], "amount": [3]})


def test_cloud_supported_ddl_and_post_create_phases(cloud, caplog):
    sink, server, uri, auth, schema_name = cloud
    options = {"schema": batch().schema, "primary_key": ["id"], "schema_name": schema_name, **auth}
    sink.prepare_constraints(
        uri,
        {
            "parent": options,
            "child": {
                **options,
                "unique": [["amount"]],
                "defaults": {"amount": 2},
                "checks": [
                    {
                        "name": "ck_amount",
                        "expression": {
                            "op": ">=",
                            "left": {"column": "amount"},
                            "right": {"literal": 0},
                        },
                    }
                ],
                "foreign_keys": [
                    {
                        "name": "fk_parent",
                        "columns": ["id"],
                        "ref_table": "parent",
                        "ref_columns": ["id"],
                    }
                ],
            },
        },
    )
    statements = server.statements()
    create = [i for i, sql in enumerate(statements) if sql.startswith("CREATE TABLE")]
    foreign = next(
        i for i, sql in enumerate(statements) if "ADD CONSTRAINT" in sql and "FOREIGN KEY" in sql
    )
    assert max(create) < foreign and "NOT ENFORCED" in statements[foreign]
    if sink.dialect == "databricks":
        assert "PRIMARY KEY (`id`) NOT ENFORCED" in statements[create[0]]
        assert any("ADD CONSTRAINT `ck_amount` CHECK" in sql for sql in statements)
    else:
        assert "UNIQUE" in statements[create[-1]] and "DEFAULT 2" in statements[create[-1]]


@pytest.mark.parametrize("mode", ["replace", "truncate"])
def test_cloud_referenced_parent_refusal_names_constraint_before_mutation(cloud, mode):
    from shape_databases._cloud_constraint_catalog import INCOMING_DATABRICKS

    sink, server, uri, auth, schema_name = cloud
    sink.write(uri, "parent", [batch()], schema_name=schema_name, **auth)
    query = (
        f'SHOW EXPORTED KEYS IN TABLE "{schema_name}"."parent"'
        if sink.dialect == "snowflake"
        else INCOMING_DATABRICKS
    )
    server.catalog_answers[query] = (["fk_name"], [("fk_external_child",)])
    with pytest.raises(ShapeError, match="fk_external_child"):
        sink.write(uri, "parent", [batch(2)], schema_name=schema_name, write_mode=mode, **auth)
    assert server.rows("parent", schema_name) == [(1, 3)]
    assert not any(sql.startswith(("DROP TABLE", "TRUNCATE")) for sql in server.statements())


def test_cloud_matching_metadata_allows_append_and_changed_constraint_refuses(cloud):
    sink, server, uri, auth, schema_name = cloud
    checks = [
        {
            "name": "ck_amount",
            "expression": {"op": ">=", "left": {"column": "amount"}, "right": {"literal": 0}},
        }
    ]
    options = {
        "primary_key": ["id"],
        "unique": [["amount"]],
        "defaults": {"amount": 2},
        "checks": checks,
        "schema_name": schema_name,
        **auth,
    }
    sink.write(uri, "child", [batch()], **options)
    ddl = next(sql for sql in server.statements() if sql.startswith("CREATE TABLE"))
    quote = '"' if sink.dialect == "snowflake" else "`"
    qualified = f"{quote}{schema_name}{quote}.{quote}child{quote}"
    query = (
        "SELECT GET_DDL('TABLE', %s)"
        if sink.dialect == "snowflake"
        else f"SHOW CREATE TABLE {qualified}"
    )
    server.catalog_answers[query] = (["ddl"], [(ddl,)])
    if sink.dialect == "snowflake":
        server.catalog_answers[f"DESCRIBE TABLE {qualified}"] = (
            ["name", "default"],
            [("id", None), ("amount", "2")],
        )
    else:
        server.catalog_answers[f"SHOW TBLPROPERTIES {qualified}"] = (
            ["key", "value"],
            [("delta.constraints.ck_amount", "(amount >= 0)")],
        )
    assert sink.write(uri, "child", [batch(2)], write_mode="append", **options) == 1
    if sink.dialect == "snowflake":
        options["defaults"] = {"amount": 5}
        expected = "DEFAULT.*amount"
    else:
        checks[0]["expression"]["right"]["literal"] = 2
        expected = "ck_amount"
    with pytest.raises(ShapeError, match=expected):
        sink.write(uri, "child", [batch(3)], write_mode="truncate", **options)
    assert server.rows("child", schema_name) == [(1, 3), (2, 3)]
