"""W2-10 items 2 to 4 on the writer and the ``sqlserver`` sink, against the in-repo fake server:
identity columns (``identity=keep|server``), constraint toggling (``constraints=keep|disable``) and
idempotent reruns (``write_mode=upsert``). The command-line side is in
``test_sql_write_path_cli.py``.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from shape_fabric import SqlDatabaseWriter, SqlServerSink, WriteError, _tsql
from shape_fabric.errors import ConstraintError
from shape_fabric.testing import FakeSqlServer

from shape.errors import ShapeError

pytestmark = pytest.mark.contract

CS = "Driver={ODBC Driver 18 for SQL Server};Server=db.example.test;Database=d"
URI = "mssql://db.example.test/appdb?user=sa"
IDENTITY_COLS = {
    "customer_id": {"nullable": False, "identity": {"start": 1000, "step": 5}},
    "name": {"nullable": True, "max_length": 40},
}
PLAIN_COLS = {"customer_id": {"nullable": False}, "name": {"nullable": True, "max_length": 40}}
SCHEMA = pa.schema([("customer_id", pa.int64()), ("name", pa.string())])


def batch(start=0, n=4, *, step=1, first=1000, label="name"):
    ids = [first + (start + i) * step for i in range(n)]
    return pa.RecordBatch.from_pydict(
        {"customer_id": ids, "name": [f"{label}-{i}" for i in ids]}, schema=SCHEMA
    )


def make(server=None, **kw):
    server = server or FakeSqlServer()
    return server, SqlDatabaseWriter(CS, connect=server.connect, **kw)


def written(server, table="customer"):
    return server.rows("dbo", table)


def statements(server, prefix):
    return [s for s in server.statements if " ".join(s.split()).startswith(prefix)]


# --- identity ----------------------------------------------------------------------------


def test_create_makes_a_bigint_identity_column_from_the_sequence():
    _, writer = make()
    ddl = writer.create_ddl("customer", SCHEMA, columns=IDENTITY_COLS, primary_key=["customer_id"])
    assert "[customer_id] BIGINT IDENTITY(1000, 5) NOT NULL" in ddl
    assert "[name] NVARCHAR(40) NULL" in ddl
    assert ddl.count("IDENTITY") == 1
    plain = _tsql.create_table_sql("app", "t", SCHEMA, warehouse=False, columns=PLAIN_COLS)
    assert "IDENTITY" not in plain


def test_a_true_flag_means_one_one():
    cols = {"customer_id": {"nullable": False, "identity": True}}
    ddl = _tsql.create_table_sql("app", "t", SCHEMA.remove(1), warehouse=False, columns=cols)
    assert "BIGINT IDENTITY(1, 1) NOT NULL" in ddl


@pytest.mark.parametrize("bad", [{"start": "1; DROP TABLE x", "step": 1}, {"start": 1, "step": 0}])
def test_a_bad_seed_or_step_never_reaches_the_ddl(bad):
    cols = {"customer_id": {"nullable": False, "identity": bad}}
    with pytest.raises(ShapeError):
        _tsql.create_table_sql("app", "t", SCHEMA.remove(1), warehouse=False, columns=cols)


def test_keep_inserts_the_generated_keys_between_identity_insert_on_and_off():
    server, writer = make()
    assert (
        writer.write_table(
            "customer",
            [batch(0, 3, step=5), batch(3, 2, step=5)],
            columns=IDENTITY_COLS,
            primary_key=["customer_id"],
        )
        == 5
    )
    assert [r[0] for r in written(server)] == [1000, 1005, 1010, 1015, 1020]  # the keys are kept
    log = server.statements
    on = log.index("SET IDENTITY_INSERT [dbo].[customer] ON")
    off = log.index("SET IDENTITY_INSERT [dbo].[customer] OFF")
    first_insert = next(i for i, s in enumerate(log) if s.startswith("INSERT INTO"))
    last_insert = max(i for i, s in enumerate(log) if s.startswith("INSERT INTO"))
    assert on < first_insert <= last_insert < off
    assert server.identity_insert is None  # left off


def test_keep_is_the_default_and_a_child_foreign_key_still_finds_its_parent():
    server = FakeSqlServer()
    _, writer = make(server)
    writer.write_table(
        "customer", [batch(0, 3, step=5)], columns=IDENTITY_COLS, primary_key=["customer_id"]
    )
    server.add_foreign_key(
        ("dbo", "customer"), ["customer_id"], ("dbo", "customer"), ["customer_id"], "FK_self"
    )
    child = pa.RecordBatch.from_pydict(
        {"customer_id": [1000, 1015], "name": ["a", "b"]}, schema=SCHEMA
    )
    with pytest.raises(WriteError, match="FOREIGN KEY"):  # 1015 is no generated key
        writer.write_table("customer", [child], write_mode="append", columns=IDENTITY_COLS)


def test_server_leaves_the_column_out_and_numbers_the_rows_itself():
    server, writer = make()
    writer.write_table(
        "customer", [batch(0, 3, step=5)], columns=IDENTITY_COLS, primary_key=["customer_id"],
        identity="server",
    )  # fmt: skip
    assert [r[0] for r in written(server)] == [
        1000,
        1005,
        1010,
    ]  # seed 1000, step 5 from the server
    assert [r[1] for r in written(server)] == ["name-1000", "name-1005", "name-1010"]
    inserts = statements(server, "INSERT INTO")
    assert inserts and all("[customer_id]" not in s for s in inserts)
    assert not statements(server, "SET IDENTITY_INSERT")


def test_server_with_a_foreign_key_to_the_column_is_refused_before_any_connection():
    server, writer = make()
    refs = [{"child": "order", "column": "customer_id", "parent_column": "customer_id"}]
    with pytest.raises(ShapeError) as err:
        writer.write_table(
            "customer", [batch()], columns=IDENTITY_COLS, identity="server",
            identity_references=refs,
        )  # fmt: skip
    assert str(err.value) == (
        "identity=server would break the foreign key order.customer_id -> customer.customer_id"
    )
    assert server.connections == 0 and server.statements == []


def test_server_with_a_reference_to_another_column_is_fine():
    server, writer = make()
    refs = [{"child": "order", "column": "code", "parent_column": "code"}]  # not the identity one
    writer.write_table(
        "customer", [batch()], columns=IDENTITY_COLS, identity="server", identity_references=refs
    )
    assert len(written(server)) == 4


def test_keep_ignores_the_references():
    server, writer = make()
    refs = [{"child": "order", "column": "customer_id", "parent_column": "customer_id"}]
    writer.write_table("customer", [batch()], columns=IDENTITY_COLS, identity_references=refs)
    assert len(written(server)) == 4


def test_an_unknown_identity_value_is_refused():
    server, writer = make()
    for bad in ("auto", "", None, True):
        with pytest.raises(ShapeError, match="identity must be keep or server"):
            writer.write_table("customer", [batch()], columns=IDENTITY_COLS, identity=bad)
    assert server.connections == 0


def test_identity_insert_is_turned_off_again_when_the_insert_fails():
    server, writer = make()

    def boom(sql, params):
        if sql.startswith("INSERT INTO"):
            raise RuntimeError("disk full")

    server.fail = boom
    with pytest.raises(WriteError):
        writer.write_table("customer", [batch()], columns=IDENTITY_COLS)
    assert "SET IDENTITY_INSERT [dbo].[customer] OFF" in server.statements
    assert server.identity_insert is None


def test_append_into_a_table_that_already_has_the_identity():
    server, writer = make()
    writer.write_table("customer", [batch(0, 2, step=5)], columns=IDENTITY_COLS)
    writer.write_table(
        "customer", [batch(2, 2, step=5)], write_mode="append", columns=IDENTITY_COLS
    )
    assert [r[0] for r in written(server)] == [1000, 1005, 1010, 1015]


def test_a_table_without_identity_gets_no_identity_statements_at_all():
    server, writer = make()
    writer.write_table("customer", [batch()], columns=PLAIN_COLS, primary_key=["customer_id"])
    assert not statements(server, "SET IDENTITY_INSERT")
    create = statements(server, "CREATE TABLE")[0]
    assert "IDENTITY" not in create
    assert create == _tsql.create_table_sql(
        "dbo", "customer", SCHEMA, warehouse=False, columns=PLAIN_COLS, primary_key=["customer_id"]
    )


def test_the_ddl_without_identity_is_what_it_was_before_this_package():
    # the statement text of a table without identity, written out (a regression guard)
    assert _tsql.create_table_sql(
        "app",
        "t",
        SCHEMA,
        warehouse=False,
        columns={"customer_id": {"nullable": False}},
        primary_key=["customer_id"],
    ) == (  # fmt: skip
        "CREATE TABLE [app].[t] (\n"
        "    [customer_id] BIGINT NOT NULL,\n"
        "    [name] NVARCHAR(MAX) NULL,\n"
        "    CONSTRAINT [PK_t] PRIMARY KEY ([customer_id])\n"
        ")"
    )


def test_a_warehouse_ignores_identity():
    server = FakeSqlServer()
    writer = SqlDatabaseWriter(CS, connect=server.connect, warehouse=True)
    writer.write_table("customer", [batch()], columns=IDENTITY_COLS, primary_key=["customer_id"])
    assert not statements(server, "SET IDENTITY_INSERT")
    assert "IDENTITY" not in statements(server, "CREATE TABLE")[0]
    assert len(written(server)) == 4


# --- the sink: options, URI query, references --------------------------------------------


def sink_for(server):
    return SqlServerSink(connect=server.connect)


def test_the_sink_takes_identity_and_constraints_as_options_and_in_the_uri():
    server = FakeSqlServer()
    sink = sink_for(server)
    sink.write(
        URI, "customer", iter([batch(0, 2, step=5)]), columns=IDENTITY_COLS, identity="server"
    )
    assert not statements(server, "SET IDENTITY_INSERT")
    server2 = FakeSqlServer()
    sink_for(server2).write(
        URI + "&identity=keep&constraints=keep", "customer", iter([batch(0, 2, step=5)]),
        columns=IDENTITY_COLS,
    )  # fmt: skip
    assert statements(server2, "SET IDENTITY_INSERT")


def test_the_sink_refuses_server_with_a_referencing_key_and_never_connects():
    server = FakeSqlServer()
    refs = [{"child": "order", "column": "customer_id", "parent_column": "customer_id"}]
    with pytest.raises(ShapeError, match="identity=server would break the foreign key"):
        sink_for(server).write(
            URI + "&identity=server", "customer", iter([batch()]), columns=IDENTITY_COLS,
            identity_references=refs,
        )  # fmt: skip
    assert server.connections == 0


@pytest.mark.parametrize(
    "bad", [{"identity": "auto"}, {"constraints": "off"}, {"constraints": True}, {"identity": 3}]
)
def test_the_sink_refuses_bad_values_before_connecting(bad):
    server = FakeSqlServer()
    with pytest.raises(ShapeError):
        sink_for(server).write(URI, "customer", iter([batch()]), **bad)
    assert server.connections == 0


def test_preflight_refuses_a_whole_run_before_anything_is_written():
    sink = SqlServerSink(connect=FakeSqlServer().connect)
    refs = [{"child": "order", "column": "customer_id", "parent_column": "customer_id"}]
    per_table = {
        "unrelated": {"columns": PLAIN_COLS, "primary_key": ["customer_id"]},
        "customer": {
            "columns": IDENTITY_COLS, "identity": "server", "identity_references": refs,
        },
    }  # fmt: skip
    with pytest.raises(ShapeError, match="identity=server would break the foreign key"):
        sink.preflight(URI, per_table)
    sink.preflight(URI, {"unrelated": per_table["unrelated"]})  # nothing to refuse


def test_preflight_refuses_an_upsert_into_a_table_without_a_key():
    sink = SqlServerSink(connect=FakeSqlServer().connect)
    with pytest.raises(ShapeError, match="upsert needs a primary key on events"):
        sink.preflight(URI, {"events": {"write_mode": "upsert", "columns": PLAIN_COLS}})


# --- constraints -------------------------------------------------------------------------


def parent_and_child(server=None):
    """customer and order created by an earlier run, with a foreign key and a check constraint."""
    server = server or FakeSqlServer()
    _, writer = make(server)
    cust = pa.RecordBatch.from_pydict(
        {"customer_id": [1, 2, 3], "name": list("abc")}, schema=SCHEMA
    )
    writer.write_table("customer", [cust], columns=PLAIN_COLS, primary_key=["customer_id"])
    order_schema = pa.schema([("order_id", pa.int64()), ("customer_id", pa.int64())])
    order = pa.RecordBatch.from_pydict({"order_id": [10], "customer_id": [1]}, schema=order_schema)
    writer.write_table(
        "order", [order], columns={"order_id": {"nullable": False}}, primary_key=["order_id"]
    )
    server.add_foreign_key(
        ("dbo", "order"), ["customer_id"], ("dbo", "customer"), ["customer_id"], "FK_order_customer"
    )
    server.add_check(("dbo", "order"), "CK_order_positive", lambda row: row["order_id"] > 0)
    return server, order_schema


def orders(ids, customers, schema):
    return pa.RecordBatch.from_pydict({"order_id": ids, "customer_id": customers}, schema=schema)


def test_keep_is_the_default_and_never_touches_constraints():
    server, order_schema = parent_and_child()
    _, writer = make(server)
    with pytest.raises(WriteError, match="FOREIGN KEY"):  # the violation stops the load as before
        writer.write_table(
            "order", [orders([11], [99], order_schema)], write_mode="append",
            columns={"order_id": {"nullable": False}},
        )  # fmt: skip
    assert not statements(server, "ALTER TABLE")


def test_disable_turns_checking_off_before_the_load_and_validates_after():
    server, order_schema = parent_and_child()
    _, writer = make(server)
    n = writer.write_table(
        "order",
        [orders([11, 12], [2, 3], order_schema)],
        write_mode="append",
        constraints="disable",
    )
    assert n == 2 and len(written(server, "order")) == 3
    log = server.statements
    off = log.index("ALTER TABLE [dbo].[order] NOCHECK CONSTRAINT ALL")
    first_insert = next(
        i for i, s in enumerate(log) if i > off and s.startswith("INSERT INTO [dbo].[order]")
    )
    on = log.index("ALTER TABLE [dbo].[order] WITH CHECK CHECK CONSTRAINT ALL")
    assert off < first_insert < on
    assert not any(c.disabled for c in server.tables[("dbo", "order")].constraints)
    assert all(c.trusted for c in server.tables[("dbo", "order")].constraints)


def test_disable_lets_a_violating_row_load_then_exits_1_and_names_the_constraint():
    server, order_schema = parent_and_child()
    _, writer = make(server)
    with pytest.raises(ConstraintError) as err:
        writer.write_table(
            "order", [orders([11, 12], [2, 99], order_schema)], write_mode="append",
            constraints="disable",
        )  # fmt: skip
    text = str(err.value)
    assert "dbo.order" in text and "FK_order_customer" in text
    assert "left disabled" in text
    assert "CK_order_positive" not in text  # that one holds
    assert err.value.exit_code == 1 and isinstance(err.value, WriteError)
    assert len(written(server, "order")) == 3  # the rows are committed
    by_name = {c.name: c for c in server.tables[("dbo", "order")].constraints}
    assert by_name["FK_order_customer"].disabled  # the one that does not hold stays disabled
    assert not by_name["CK_order_positive"].disabled  # the one that holds was re-enabled


def test_every_constraint_that_does_not_hold_is_named():
    server, order_schema = parent_and_child()
    _, writer = make(server)
    with pytest.raises(ConstraintError) as err:
        writer.write_table(
            "order", [orders([-5], [99], order_schema)], write_mode="append", constraints="disable"
        )
    assert "FK_order_customer" in str(err.value) and "CK_order_positive" in str(err.value)


def test_a_table_created_by_the_run_has_nothing_to_disable():
    server, writer = make()
    writer.write_table("customer", [batch()], constraints="disable")
    assert not statements(server, "ALTER TABLE")


def test_a_failed_load_with_disable_puts_checking_back_on():
    server, order_schema = parent_and_child()
    _, writer = make(server)

    def boom(sql, params):
        if sql.startswith("INSERT INTO"):
            raise RuntimeError("deadlock victim")

    server.fail = boom
    with pytest.raises(WriteError, match="deadlock victim"):
        writer.write_table(
            "order", [orders([11], [2], order_schema)], write_mode="append", constraints="disable"
        )
    server.fail = None
    assert not any(c.disabled for c in server.tables[("dbo", "order")].constraints)
    assert "ALTER TABLE [dbo].[order] CHECK CONSTRAINT ALL" in server.statements


def test_unknown_constraints_values_are_refused_before_connecting():
    server, writer = make()
    for bad in ("off", "", None, False, "DISABLE"):
        with pytest.raises(ShapeError, match="constraints must be keep or disable"):
            writer.write_table("customer", [batch()], constraints=bad)
    assert server.connections == 0


def test_truncate_of_a_referenced_table_uses_delete():
    server, order_schema = parent_and_child()
    _, writer = make(server)
    server.tables[("dbo", "order")].rows.clear()
    server.snapshot()
    cust = pa.RecordBatch.from_pydict({"customer_id": [7, 8], "name": ["x", "y"]}, schema=SCHEMA)
    writer.write_table("customer", [cust], write_mode="truncate", columns=PLAIN_COLS)
    assert [r[0] for r in written(server)] == [7, 8]
    assert "DELETE FROM [dbo].[customer]" in server.statements
    assert not statements(server, "TRUNCATE")


def test_truncate_of_an_unreferenced_table_still_truncates():
    server, writer = make()
    writer.write_table("customer", [batch()])
    writer.write_table("customer", [batch(10, 2)], write_mode="truncate")
    assert "TRUNCATE TABLE [dbo].[customer]" in server.statements
    assert not statements(server, "DELETE FROM")


def test_deleting_a_parent_whose_children_exist_fails_clearly():
    server, _ = parent_and_child()
    _, writer = make(server)
    with pytest.raises(WriteError, match="REFERENCE constraint"):
        writer.write_table("customer", [batch()], write_mode="truncate", columns=PLAIN_COLS)


def test_the_sink_passes_constraints_through():
    server, order_schema = parent_and_child()
    with pytest.raises(ConstraintError):
        sink_for(server).write(
            URI, "order", iter([orders([11], [99], order_schema)]), write_mode="append",
            constraints="disable",
        )  # fmt: skip


# --- upsert ------------------------------------------------------------------------------


def keyed_server():
    server = FakeSqlServer()
    server.enforce_keys = True
    return server


def upsert(writer, batches, **kw):
    kw.setdefault("columns", PLAIN_COLS)
    kw.setdefault("primary_key", ["customer_id"])
    return writer.write_table("customer", batches, write_mode="upsert", **kw)


def test_upsert_creates_the_table_and_loads_through_a_temporary_table_and_merge():
    server = keyed_server()
    _, writer = make(server)
    assert upsert(writer, [batch(0, 3)]) == 3
    assert sorted(r[0] for r in written(server)) == [1000, 1001, 1002]
    assert statements(server, "CREATE TABLE [#shape_stage]")
    merges = statements(server, "MERGE")
    assert merges and all("WITH (HOLDLOCK)" in m for m in merges)
    assert "ON t.[customer_id] = s.[customer_id]" in " ".join(merges[0].split())
    assert not statements(server, "INSERT INTO [dbo]")  # nothing goes straight into the target
    assert ("#temp", "#shape_stage") not in server.tables  # the stage is dropped again


def test_rerunning_the_same_command_leaves_the_same_rows_and_count():
    server = keyed_server()
    for _ in range(3):
        _, writer = make(server)
        assert upsert(writer, [batch(0, 25), batch(25, 25)], batch_size=10) == 50
    assert len(written(server)) == 50
    assert sorted(r[0] for r in written(server)) == [1000 + i for i in range(50)]


def test_upsert_updates_non_key_columns_and_inserts_missing_rows_and_keeps_others():
    server = keyed_server()
    _, writer = make(server)
    upsert(writer, [batch(0, 3)])
    upsert(writer, [batch(2, 3, label="new")])  # 1002 changes, 1003 and 1004 are new
    rows = {r[0]: r[1] for r in written(server)}
    assert rows == {
        1000: "name-1000", 1001: "name-1001",
        1002: "new-1002", 1003: "new-1003", 1004: "new-1004",
    }  # fmt: skip


def test_plain_append_would_have_duplicated_so_upsert_is_what_makes_a_rerun_safe():
    server = keyed_server()
    _, writer = make(server)
    writer.write_table("customer", [batch()], columns=PLAIN_COLS, primary_key=["customer_id"])
    with pytest.raises(WriteError, match="PRIMARY KEY"):
        writer.write_table("customer", [batch()], write_mode="append", columns=PLAIN_COLS)


def test_a_rerun_after_a_run_killed_mid_table_completes_it_without_duplicates():
    server = keyed_server()
    seen = {"merges": 0}

    def kill(sql, params):
        if sql.startswith("MERGE"):
            seen["merges"] += 1
            if seen["merges"] == 3:
                raise RuntimeError("connection reset by peer")

    server.fail = kill
    _, writer = make(server)
    with pytest.raises(WriteError, match="connection reset"):
        upsert(writer, [batch(0, 50)], batch_size=10, commit_rows=10)
    partial = len(written(server))
    assert 0 < partial < 50  # the committed chunks stay
    server.fail = None
    _, writer = make(server)
    assert upsert(writer, [batch(0, 50)], batch_size=10, commit_rows=10) == 50
    assert sorted(r[0] for r in written(server)) == [1000 + i for i in range(50)]


def test_upsert_without_a_primary_key_is_refused_before_connecting():
    server = keyed_server()
    _, writer = make(server)
    with pytest.raises(ShapeError) as err:
        writer.write_table("events", [batch()], write_mode="upsert", columns=PLAIN_COLS)
    assert str(err.value) == "upsert needs a primary key on events"
    assert server.connections == 0


def test_upsert_with_a_composite_key():
    server = keyed_server()
    schema = pa.schema([("a", pa.int64()), ("b", pa.int64()), ("v", pa.string())])

    def rows(*triples):
        cols = list(zip(*triples, strict=True))
        return pa.RecordBatch.from_pydict(dict(zip("abv", cols, strict=True)), schema=schema)

    cols = {"a": {"nullable": False}, "b": {"nullable": False}}
    _, writer = make(server)
    writer.write_table(
        "t",
        [rows((1, 1, "x"), (1, 2, "y"))],
        write_mode="upsert",
        columns=cols,
        primary_key=["a", "b"],
    )
    writer.write_table(
        "t",
        [rows((1, 2, "Y"), (2, 1, "z"))],
        write_mode="upsert",
        columns=cols,
        primary_key=["a", "b"],
    )
    assert sorted(server.rows("dbo", "t")) == [(1, 1, "x"), (1, 2, "Y"), (2, 1, "z")]
    merge = " ".join(statements(server, "MERGE")[0].split())
    assert "ON t.[a] = s.[a] AND t.[b] = s.[b]" in merge
    assert (
        "UPDATE SET t.[v] = s.[v]" in merge
        and "t.[a] = s.[a], " not in merge.split("UPDATE SET")[1]
    )


def test_a_table_of_only_key_columns_inserts_the_missing_rows_and_updates_nothing():
    server = keyed_server()
    schema = pa.schema([("a", pa.int64())])
    cols = {"a": {"nullable": False}}
    _, writer = make(server)
    for ids in ([1, 2], [2, 3]):
        batch_ = pa.RecordBatch.from_pydict({"a": ids}, schema=schema)
        writer.write_table("k", [batch_], write_mode="upsert", columns=cols, primary_key=["a"])
    assert sorted(r[0] for r in server.rows("dbo", "k")) == [1, 2, 3]
    assert "WHEN MATCHED" not in " ".join(statements(server, "MERGE")[0].split())


def test_upsert_with_identity_keep_keeps_the_generated_identity_values():
    server = keyed_server()
    _, writer = make(server)
    for _ in range(2):
        upsert(writer, [batch(0, 4, step=5)], columns=IDENTITY_COLS)
    assert sorted(r[0] for r in written(server)) == [1000, 1005, 1010, 1015]
    assert "SET IDENTITY_INSERT [dbo].[customer] ON" in server.statements
    assert server.identity_insert is None
    merge = " ".join(statements(server, "MERGE")[0].split())
    assert (
        "t.[customer_id] = s.[customer_id], " not in merge.split("UPDATE SET")[-1]
    )  # never updated


def test_upsert_with_identity_server_on_an_identity_key_is_refused():
    server = keyed_server()
    _, writer = make(server)
    with pytest.raises(ShapeError, match="identity=server cannot match rows on the identity key"):
        upsert(writer, [batch()], columns=IDENTITY_COLS, identity="server")
    assert server.connections == 0


def test_upsert_with_identity_server_on_a_non_key_identity_column():
    server = keyed_server()
    schema = pa.schema([("code", pa.string()), ("n", pa.int64())])
    cols = {"code": {"nullable": False, "max_length": 10}, "n": {"identity": True}}
    _, writer = make(server)
    for _ in range(2):
        b = pa.RecordBatch.from_pydict({"code": ["a", "b"], "n": [7, 8]}, schema=schema)
        writer.write_table(
            "t", [b], write_mode="upsert", columns=cols, primary_key=["code"], identity="server"
        )
    assert sorted(server.rows("dbo", "t")) == [("a", 1), ("b", 2)]  # numbered by the server, once


def test_upsert_is_refused_for_a_warehouse():
    server = FakeSqlServer()
    writer = SqlDatabaseWriter(CS, connect=server.connect, warehouse=True)
    with pytest.raises(ShapeError, match="upsert"):
        upsert(writer, [batch()])


def test_upsert_commit_rows_makes_rows_visible_while_loading():
    server = keyed_server()
    _, writer = make(server)
    counts = []

    def source():
        for i in range(3):
            yield batch(i * 10, 10)
            counts.append(len(server._committed[0][("dbo", "customer")].rows))  # committed state

    upsert(writer, source(), batch_size=10, commit_rows=10)
    assert counts[1] >= 10 and counts[2] >= 20


def test_the_sink_upserts_and_a_rerun_is_idempotent():
    server = keyed_server()
    for _ in range(2):
        n = sink_for(server).write(
            URI, "customer", iter([batch(0, 20)]), write_mode="upsert", columns=PLAIN_COLS,
            primary_key=["customer_id"], batch_size=7,
        )  # fmt: skip
        assert n == 20
    assert len(written(server)) == 20


def test_the_other_writers_still_refuse_upsert():
    from shape_fabric.sqldb import check_mode

    with pytest.raises(ShapeError, match="unknown write mode 'upsert'"):
        check_mode("upsert")
