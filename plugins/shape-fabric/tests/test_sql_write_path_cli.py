"""W2-10 items 2 to 4 through the command line (``shape generate --to mssql://`` and ``shape emit``)
with the real sink and the in-repo fake SQL Server: identity columns, ``--sql-constraints``,
``--write-mode upsert`` and the exit codes (2 for an impossible request, 1 for constraints that do
not hold after a load)."""

from __future__ import annotations

import copy
import json

import pytest
from shape_fabric import _tsql
from shape_fabric.testing import FakeSqlServer

from shape.cli.main import main

pytestmark = pytest.mark.contract

URI = "mssql://db.example.test/appdb?user=sa"
PASSWORD = "Sup3r-secret-pw"


def _col(name, strategy, type_="integer", **gen):
    return {
        "name": name,
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": False,
        "null_rate": 0.0,
    }


def doc(identity=True, rows=None):
    customer_id = _col("customer_id", "sequence", start=1000, step=5)
    if identity:
        customer_id["identity"] = True
    return {
        "schema_version": 1,
        "model": {"name": "t", "seed": 5},
        "tables": {
            "customer": {
                "name": "customer",
                "primary_key": ["customer_id"],
                "columns": {"customer_id": customer_id},
            },
            "order": {
                "name": "order",
                "primary_key": ["order_id"],
                "columns": {
                    "order_id": _col("order_id", "sequence"),
                    "customer_id": _col("customer_id", "foreign_key", ref="customer.customer_id"),
                },
            },
        },
        "relationships": [
            {
                "name": "o_c",
                "parent": "customer",
                "child": "order",
                "parent_columns": ["customer_id"],
                "child_columns": ["customer_id"],
            }
        ],
        "generation": {"scale": "small", "scales": {"small": rows or {"customer": 20, "order": 60}}},
    }


@pytest.fixture
def world(tmp_path, monkeypatch, capsys):
    server = FakeSqlServer()
    server.enforce_keys = True
    monkeypatch.setattr(_tsql, "connect", server.connect)

    class W:
        pass

    w = W()
    w.server, w.tmp = server, tmp_path

    def schema(identity=True, rows=None, name="schema.json"):
        path = tmp_path / name
        path.write_text(json.dumps(doc(identity, rows)))
        return str(path)

    def run(*argv):
        code = main([str(a) for a in argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    w.schema, w.run = schema, run
    w.rows = lambda table: server.rows("dbo", table)
    return w


def statements(server, prefix):
    return [s for s in server.statements if " ".join(s.split()).startswith(prefix)]


# --- identity ----------------------------------------------------------------------------


def test_generate_creates_the_identity_column_and_keeps_the_keys_children_reference(world):
    code, _, err = world.run("generate", world.schema(), "--to", URI, "--seed", 3)
    assert code == 0, err
    assert "[customer_id] BIGINT IDENTITY(1000, 5) NOT NULL" in statements(world.server, "CREATE TABLE [dbo].[customer]")[0].replace("    ", "").replace("\n", " ") or True
    create = " ".join(statements(world.server, "CREATE TABLE [dbo].[customer]")[0].split())
    assert "[customer_id] BIGINT IDENTITY(1000, 5) NOT NULL" in create
    parents = [r[0] for r in world.rows("customer")]
    assert parents == [1000 + 5 * i for i in range(20)]
    children = {r[1] for r in world.rows("order")}
    assert children and children <= set(parents)  # every child key is a parent key that was kept
    assert statements(world.server, "SET IDENTITY_INSERT [dbo].[customer] ON")
    assert statements(world.server, "SET IDENTITY_INSERT [dbo].[customer] OFF")
    assert not statements(world.server, "SET IDENTITY_INSERT [dbo].[order]")


def test_a_schema_without_identity_makes_the_tables_it_always_did(world):
    code, _, err = world.run("generate", world.schema(identity=False), "--to", URI)
    assert code == 0, err
    assert not statements(world.server, "SET IDENTITY_INSERT")
    assert all("IDENTITY" not in s for s in statements(world.server, "CREATE TABLE"))


def test_identity_server_with_a_referencing_foreign_key_is_exit_2_and_writes_nothing(world):
    code, _, err = world.run(
        "generate", world.schema(), "--to", URI, "--sink-config", "mssql.identity=server"
    )
    assert code == 2
    assert (
        "identity=server would break the foreign key order.customer_id -> customer.customer_id"
        in err
    )
    assert world.server.connections == 0 and world.server.tables == {}  # refused before any write


def test_the_sink_config_also_takes_the_sink_name(world):
    code, _, err = world.run(
        "generate", world.schema(), "--to", URI, "--sink-config", "sqlserver.identity=server"
    )
    assert code == 2 and "identity=server would break the foreign key" in err


def test_identity_server_without_a_referencing_key_lets_the_server_number_the_rows(world):
    only = doc()
    del only["tables"]["order"]
    only["relationships"] = []
    only["tables"]["customer"]["columns"]["code"] = _col(
        "code", "choice", "string", values=["a", "b"]
    )
    only["generation"]["scales"]["small"] = {"customer": 12}
    path = world.tmp / "only.json"
    path.write_text(json.dumps(only))
    code, _, err = world.run(
        "generate", path, "--to", URI, "--sink-config", "mssql.identity=server"
    )
    assert code == 0, err
    assert [r[0] for r in world.rows("customer")] == [1000 + 5 * i for i in range(12)]  # server seed/step
    assert not statements(world.server, "SET IDENTITY_INSERT")
    assert all("[customer_id]" not in s for s in statements(world.server, "INSERT INTO"))


def test_a_bad_identity_value_is_exit_2(world):
    code, _, err = world.run(
        "generate", world.schema(), "--to", URI, "--sink-config", "mssql.identity=auto"
    )
    assert code == 2 and "identity must be keep or server" in err
    assert world.server.connections == 0


def test_emit_to_mssql_keeps_the_identity_values(world):
    code, _, err = world.run(
        "emit", world.schema(), "--to", URI, "--max-events", 40, "--seed", 3
    )
    assert code == 0, err
    parents = {r[0] for r in world.rows("customer")}
    assert parents <= {1000 + 5 * i for i in range(20)} and parents
    assert statements(world.server, "SET IDENTITY_INSERT [dbo].[customer] ON")
    assert world.server.identity_insert is None


def test_emit_refuses_identity_server_with_a_foreign_key_too(world):
    code, _, err = world.run(
        "emit", world.schema(), "--to", URI, "--max-events", 40,
        "--sink-config", "mssql.identity=server",
    )  # fmt: skip
    assert code == 2 and "identity=server would break the foreign key" in err
    assert world.server.tables == {}


# --- constraints -------------------------------------------------------------------------


def precreate(world, **flags):
    """Load once so both tables exist, then empty them, and add a check the generated orders
    break (their customer keys go up to 1095)."""
    assert world.run("generate", world.schema(), "--to", URI, "--seed", 3)[0] == 0
    for table in ("order", "customer"):
        world.server.tables[("dbo", table)].rows.clear()
    world.server.add_check(("dbo", "order"), "CK_order_customer_small", lambda r: r["customer_id"] < 1020)


def test_without_disable_a_violating_row_stops_the_load_with_exit_2(world):
    precreate(world)
    code, _, err = world.run(
        "generate", world.schema(), "--to", URI, "--seed", 3, "--write-mode", "append"
    )
    assert code == 2 and "CHECK constraint" in err
    assert not statements(world.server, "ALTER TABLE")


def test_disable_loads_then_exits_1_and_names_the_constraint_left_disabled(world):
    precreate(world)
    code, _, err = world.run(
        "generate", world.schema(), "--to", URI, "--seed", 3, "--write-mode", "append",
        "--sql-constraints", "disable",
    )  # fmt: skip
    assert code == 1
    assert "dbo.order.CK_order_customer_small" in err and "left disabled" in err
    assert len(world.rows("order")) == 60  # the rows were loaded
    assert world.server.tables[("dbo", "order")].constraints[0].disabled


def test_disable_with_data_that_holds_is_exit_0_and_checking_is_back_on(world):
    assert world.run("generate", world.schema(), "--to", URI, "--seed", 3)[0] == 0
    for table in ("order", "customer"):
        world.server.tables[("dbo", table)].rows.clear()
    world.server.add_check(("dbo", "order"), "CK_ok", lambda r: r["order_id"] >= 0)
    code, _, err = world.run(
        "generate", world.schema(), "--to", URI, "--seed", 3, "--write-mode", "append",
        "--sql-constraints", "disable",
    )  # fmt: skip
    assert code == 0, err
    assert not any(c.disabled for c in world.server.tables[("dbo", "order")].constraints)


def test_sql_constraints_is_for_mssql_targets(world, tmp_path):
    code, _, err = world.run(
        "generate", world.schema(), "--to", f"{tmp_path}/out/", "--sql-constraints", "disable"
    )
    assert code == 2


def test_emit_takes_sql_constraints(world):
    args = ("emit", world.schema(), "--to", URI, "--max-events", 80, "--seed", 3)
    assert world.run(*args)[0] == 0  # the tables an emit makes (they carry the _shape_* columns)
    for table in ("order", "customer"):
        world.server.tables[("dbo", table)].rows.clear()
    world.server.add_check(("dbo", "order"), "CK_never", lambda r: r["order_id"] < 0)
    code, _, err = world.run(*args, "--write-mode", "append", "--sql-constraints", "disable")
    assert code == 1, err
    assert "dbo.order.CK_never" in err and "left disabled" in err
    assert statements(world.server, "ALTER TABLE [dbo].[order] NOCHECK CONSTRAINT ALL")
    assert world.rows("order")


# --- upsert ------------------------------------------------------------------------------


def snapshot(world):
    return {t: sorted(world.rows(t)) for t in ("customer", "order")}


def test_generate_upsert_rerun_leaves_the_same_rows(world):
    args = ("generate", world.schema(), "--to", URI, "--seed", 3, "--write-mode", "upsert")
    assert world.run(*args)[0] == 0
    first = snapshot(world)
    assert len(first["customer"]) == 20 and len(first["order"]) == 60
    for _ in range(2):
        code, _, err = world.run(*args)
        assert code == 0, err
        assert snapshot(world) == first
    assert statements(world.server, "MERGE")


def test_generate_upsert_keeps_the_identity_values(world):
    args = ("generate", world.schema(), "--to", URI, "--seed", 3, "--write-mode", "upsert")
    assert world.run(*args)[0] == 0 and world.run(*args)[0] == 0
    assert sorted(r[0] for r in world.rows("customer")) == [1000 + 5 * i for i in range(20)]


def test_plain_append_of_the_same_run_collides_on_the_key_while_upsert_does_not(world):
    base = ("generate", world.schema(identity=False), "--to", URI, "--seed", 3)
    assert world.run(*base)[0] == 0
    code, _, err = world.run(*base, "--write-mode", "append")
    assert code == 2 and "PRIMARY KEY" in err
    assert world.run(*base, "--write-mode", "upsert")[0] == 0


def test_a_table_without_a_primary_key_is_refused_before_anything_is_written(world):
    no_key = doc()
    no_key["tables"]["order"]["primary_key"] = []
    path = world.tmp / "nokey.json"
    path.write_text(json.dumps(no_key))
    code, _, err = world.run("generate", path, "--to", URI, "--write-mode", "upsert")
    assert code == 2 and "upsert needs a primary key on order" in err
    assert world.server.connections == 0


def test_emit_upsert_rerun_leaves_the_same_rows(world):
    args = (
        "emit", world.schema(), "--to", URI, "--max-events", 50, "--seed", 3,
        "--write-mode", "upsert",
    )  # fmt: skip
    code, _, err = world.run(*args)
    assert code == 0, err
    first = snapshot(world)
    assert first["customer"] or first["order"]
    code, _, err = world.run(*args)
    assert code == 0, err
    assert snapshot(world) == first


def test_upsert_is_a_write_mode_choice_and_other_databases_refuse_it(world, capsys):
    code, _, err = world.run(
        "generate", world.schema(), "--to", "postgresql://db.example.test/appdb",
        "--write-mode", "upsert",
    )  # fmt: skip
    assert code == 2 and "upsert" in err


def test_no_secret_is_printed_by_any_of_it(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_PW", PASSWORD)
    code, out, err = world.run(
        "generate", world.schema(), "--to", URI, "--write-mode", "upsert", "--auth", "sql",
        "--sql-user", "app", "--sql-password", "env://SHAPE_TEST_PW",
        "--connection-string", "Driver={ODBC Driver 18 for SQL Server};Server=h;Database=d",
    )  # fmt: skip
    assert code == 0, err
    assert PASSWORD not in out + err
