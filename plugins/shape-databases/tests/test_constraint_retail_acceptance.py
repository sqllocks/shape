"""SHAPE-SINK-004: W9-04 Wanted 5: every retail table loads, and a planted violation fails."""

import copy
import os
import uuid

import pyarrow as pa
import pytest
from shape_databases import DuckDbSink, MySqlSink, PostgresSink, WriteError

from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.generation.output import TargetOptions, write_targets


@pytest.fixture(
    params=[
        "duckdb",
        pytest.param("postgres", marks=pytest.mark.emulator),
        pytest.param("mysql", marks=pytest.mark.emulator),
    ]
)
def retail_database(request, tmp_path):
    dialect = request.param
    if dialect == "duckdb":
        import duckdb

        path = tmp_path / "retail.duckdb"
        conn = duckdb.connect(str(path))
        sink, uri = DuckDbSink(), "duckdb:///" + path.as_posix()
    elif dialect == "postgres":
        import psycopg

        conn = psycopg.connect(
            host="localhost",
            user="shape",
            dbname="shape",
            password=os.environ["SHAPE_POSTGRES_PASSWORD"],
            autocommit=True,
        )
        sink, uri = PostgresSink(), "postgresql://shape@localhost:5432/shape"
    else:
        import pymysql

        conn = pymysql.connect(
            host="127.0.0.1",
            user="shape",
            database="shape",
            password=os.environ["SHAPE_MYSQL_PASSWORD"],
            autocommit=True,
        )
        sink, uri = MySqlSink(), "mysql://shape@localhost:3306/shape"
    try:
        yield dialect, conn, sink, uri
    finally:
        conn.close()


def _quote(name, dialect):
    char = "`" if dialect == "mysql" else '"'
    return char + name.replace(char, char + char) + char


def _execute(conn, sql):
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        return cursor.fetchall() if cursor.description else []
    finally:
        cursor.close()


def test_all_retail_tables_accept_constraints_and_named_chaos_is_rejected(retail_database):
    dialect, conn, sink, uri = retail_database
    schema = copy.deepcopy(load_domain("retail").schema)
    for table in schema.tables.values():
        for column in table.columns.values():
            if column.type == "decimal":
                column.precision, column.scale = 18, 2
    engine = Engine(schema, scale="small", seed=1042)
    prefix = "" if dialect == "duckdb" else "wr_" + uuid.uuid4().hex[:10] + "_"
    contract = {
        "tables": {
            "product": {
                "columns": {
                    "product_id": {"unique": True},
                    "unit_price": {"min": 0, "default": 1.0},
                }
            },
            "order_line": {"columns": {"quantity": {"min": 1}}},
        }
    }
    extra = {"table_prefix": prefix}
    options = TargetOptions(constraints="all", contract=contract, extra={sink.name: extra})
    expected = dict(engine.row_counts)
    assert len(expected) == 9
    assert sum(expected.values()) == 21750
    try:
        assert write_targets(engine, [uri], options, max_workers=1) == {uri: expected}
        for table, rows in expected.items():
            target = _quote(prefix + table, dialect)
            assert _execute(conn, f"SELECT count(*) FROM {target}")[0][0] == rows

        # A real generated row is changed to violate the schema's CHECK, while
        # its primary key is fresh and every foreign-key value remains valid.
        product = Engine(schema, scale="small", seed=1042).generate().tables["product"].slice(0, 1)
        key = product.schema.get_field_index("product_id")
        cost = product.schema.get_field_index("cost")
        highest = _execute(
            conn, f"SELECT max(product_id) FROM {_quote(prefix + 'product', dialect)}"
        )[0][0]
        product = product.set_column(
            key, "product_id", pa.array([highest + 1], type=product.schema.field(key).type)
        )
        product = product.set_column(cost, "cost", product.column("unit_price"))
        load_options = options.for_sink(sink.name, schema, "product")
        load_options["write_mode"] = "append"
        with pytest.raises(WriteError, match="cost_less_than_price") as error:
            sink.write(
                uri,
                "product",
                product.to_batches(),
                **load_options,
            )
        assert error.value.rows_committed == 0
        assert (
            _execute(conn, f"SELECT count(*) FROM {_quote(prefix + 'product', dialect)}")[0][0]
            == expected["product"]
        )
    finally:
        for table in reversed(engine.order):
            _execute(conn, f"DROP TABLE IF EXISTS {_quote(prefix + table, dialect)}")
