"""``shape demo`` against the real writers and the in-repo services (P6-12): the operations a
demo needs (remove a table, remove files, check a target), and a full seed, preflight and cleanup
over the four Fabric targets with fake SQL, Kusto and OneLake services.

Live runs against a real workspace are the owner's O-02 (nightly)."""

from __future__ import annotations

import json

import pytest
from shape_fabric import EventhouseWriter, targets
from shape_fabric.testing import FakeKusto, FakeSqlServer, MemoryFS

from shape.demo.api import demo_cleanup, demo_init, demo_preflight, demo_run
from shape.demo.connections import ConnectionRegistry
from shape.demo.runtime import DemoRuntime
from shape.demo.services import FabricServices
from shape.scale.sinks.fabric import KqlSink, LakehouseSink, SqlDatabaseSink, WarehouseSink

pytestmark = pytest.mark.contract

WS = "11111111-1111-1111-1111-111111111111"
LH = "22222222-2222-2222-2222-222222222222"
WAREHOUSE = "Driver={x};Server=w.datawarehouse.fabric.microsoft.com;Database=wh"
SQLDB = "Driver={x};Server=db.example.test;Database=d"
KQL = "http://kql.example.test"
ROWS = {"customer": 30, "order": 200}


def schema_doc() -> dict:
    def col(name, strategy, type_="integer", **gen):
        return {
            "name": name,
            "type": type_,
            "generator": {"strategy": strategy, **gen},
            "nullable": False,
            "null_rate": 0.0,
        }

    return {
        "schema_version": 1,
        "model": {"name": "t", "seed": 5},
        "tables": {
            "customer": {
                "name": "customer",
                "primary_key": ["customer_id"],
                "columns": {
                    "customer_id": col("customer_id", "sequence"),
                    "score": col("score", "distribution", "float", low=0.0, high=9.0),
                },
            },
            "order": {
                "name": "order",
                "primary_key": ["order_id"],
                "columns": {
                    "order_id": col("order_id", "sequence"),
                    "customer_id": col("customer_id", "foreign_key", ref="customer.customer_id"),
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
        "generation": {"scale": "small", "scales": {"small": ROWS}},
    }


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAPE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    # no sign-in is built: the in-repo services take no credential
    monkeypatch.setattr("shape.scale.sinks.fabric.auth_options", lambda auth, cs=None: None)
    return tmp_path / "home"


# ---- the operations ----------------------------------------------------------------------------


def test_drop_sql_table_quotes_both_names_and_can_be_repeated():
    server = FakeSqlServer()
    conn = server.connect("x")
    cur = conn.cursor()
    cur.execute("CREATE TABLE [dbo].[a]]b] (\n[id] INT NOT NULL\n)")
    targets.drop_sql_table(SQLDB, "dbo", "a]b", connect=server.connect)
    assert ("dbo", "a]b") not in server.tables
    targets.drop_sql_table(SQLDB, "dbo", "a]b", connect=server.connect)  # IF EXISTS: no error
    drops = [s for s in server.statements if s.startswith("DROP TABLE")]
    assert drops == ["DROP TABLE IF EXISTS [dbo].[a]]b]"] * 2


def test_a_hostile_table_name_cannot_change_the_statement():
    server = FakeSqlServer()
    targets.drop_sql_table(SQLDB, "dbo", "x]; DROP TABLE [dbo].[y", connect=server.connect)
    assert server.statements == ["DROP TABLE IF EXISTS [dbo].[x]]; DROP TABLE [dbo]].[y]"]


def test_drop_sql_table_rolls_back_and_raises_when_the_server_refuses():
    server = FakeSqlServer()

    def refuse(sql, params):
        raise RuntimeError("permission denied")

    server.fail = refuse
    with pytest.raises(RuntimeError, match="permission denied"):
        targets.drop_sql_table(SQLDB, "dbo", "t", connect=server.connect)


def test_check_sql_runs_a_query_and_names_the_kind_of_server():
    server = FakeSqlServer()
    assert targets.check_sql(SQLDB, connect=server.connect) == "database reachable"
    assert targets.check_sql(WAREHOUSE, connect=server.connect) == "warehouse reachable"
    assert server.statements == ["SELECT 1", "SELECT 1"]


def test_check_sql_raises_when_the_server_cannot_be_reached():
    def down(connection_string, credential=None, **_kw):
        raise ConnectionError("no route to host")

    with pytest.raises(ConnectionError, match="no route"):
        targets.check_sql(SQLDB, connect=down)


def test_drop_and_check_kql():
    kusto = FakeKusto()
    uri = "eventhouse://kql.example.test/db1?tls=false"
    kusto.tables["gen_customer"] = ["id"]
    assert targets.check_kql(uri, transport=kusto) == "database db1 reachable"
    targets.drop_kql_table(uri, "gen_customer", transport=kusto)
    assert "gen_customer" not in kusto.tables
    assert kusto.commands[-1] == ("db1", ".drop table ['gen_customer'] ifexists")


def test_a_hostile_kql_name_is_refused_before_a_command_is_sent():
    kusto = FakeKusto()
    with pytest.raises(Exception, match="name"):
        targets.drop_kql_table(
            "eventhouse://kql.example.test/db1?tls=false", "a\x00b", transport=kusto
        )
    assert kusto.commands == []


def test_remove_files_and_check_files_on_a_filesystem_and_a_folder(tmp_path):
    fs = MemoryFS()
    base = f"onelake://{WS}/{LH}/Files"
    with pytest.raises(Exception, match="does not exist"):
        targets.check_files(base, filesystem=fs)
    fs.files[f"{WS}/{LH}/Files/shape_demo/s1/customer/part-0001.parquet"] = b"x"
    fs.files[f"{WS}/{LH}/Files/shape_demo/s1/order/part-0001.parquet"] = b"y"
    assert targets.check_files(base, filesystem=fs) == "folder reachable"
    targets.remove_files(f"{base}/shape_demo/s1/customer", filesystem=fs)
    assert list(fs.files) == [f"{WS}/{LH}/Files/shape_demo/s1/order/part-0001.parquet"]
    targets.remove_files(f"{base}/shape_demo/s1/customer", filesystem=fs)  # already gone: fine

    folder = tmp_path / "landing" / "s1"
    (folder / "t").mkdir(parents=True)
    (folder / "t" / "f").write_text("x")
    targets.remove_files(str(folder / "t"))
    assert not (folder / "t").exists() and folder.exists()


# ---- a full run --------------------------------------------------------------------------------


def test_seed_check_and_clean_up_all_four_targets_with_the_real_writers(tmp_path, home):
    fs = MemoryFS()
    warehouse, sqldb, kusto = FakeSqlServer(fs), FakeSqlServer(), FakeKusto()
    schema = tmp_path / "shop.json"
    schema.write_text(json.dumps(schema_doc()))

    def connect(connection_string, credential=None, **_kw):
        server = warehouse if "datawarehouse" in connection_string else sqldb
        return server.connect(connection_string)

    class Kql:
        name = "eventhouse"
        schemes = ("eventhouse",)

        def write(self, uri, table, batches, **options):
            return EventhouseWriter(uri, transport=kusto, busy_pause=0.001).write_table(
                table, batches, **options
            )

    def sinks(name, s):
        if name == "lakehouse":
            return LakehouseSink(s["base_path"], s["format"], writer_options={"filesystem": fs})
        if name == "warehouse":
            return WarehouseSink(
                s["connection_string"], s["staging_path"], s["schema_name"], s["write_mode"],
                writer_options={"connection": warehouse.connect("injected"), "filesystem": fs},
            )  # fmt: skip
        if name == "sql_database":
            return SqlDatabaseSink(
                s["connection_string"], s["schema_name"], s["write_mode"],
                writer_options={"connection": sqldb.connect("injected")},
            )  # fmt: skip
        if name == "kql":
            return KqlSink(
                s["cluster_uri"], s["database"], s["table_prefix"], s["write_mode"], writer=Kql()
            )
        return None

    demo_init(
        "all", workspace_id=WS, lakehouse_id=LH, warehouse_conn=WAREHOUSE,
        warehouse_staging_path=f"onelake://{WS}/{LH}/Files/stage", sql_db_conn=SQLDB,
        eventhouse_uri=KQL, eventhouse_database="events", local_path=str(tmp_path / "landing"),
    )  # fmt: skip
    services = FabricServices(
        ConnectionRegistry().load("all"), connect=connect, transport=kusto, filesystem=fs
    )
    fs.files[f"{WS}/{LH}/Files/.keep"] = b""  # a lakehouse's Files area exists

    # preflight: every target answers through the real checks
    out = demo_preflight("all", runtime=DemoRuntime(services=services))
    assert out["ok"], out
    assert {c["target"]: c["status"] for c in out["profiles"][0]["checks"]} == {
        "local": "ok", "lakehouse": "ok", "warehouse": "ok", "sql_db": "ok", "eventhouse": "ok",
    }  # fmt: skip

    # seed
    result = demo_run(
        {"scenario": "retail", "mode": "seeding", "connection": "all", "rows": 1000,
         "domain": str(schema), "seed": 3},
        runtime=DemoRuntime(sink_factory=sinks),
    )  # fmt: skip
    assert result["success"], result
    sid = result["session_id"]
    for table, rows in ROWS.items():
        assert len(warehouse.rows("dbo", table)) == rows
        assert len(sqldb.rows("dbo", table)) == rows
        assert len(kusto.by_table[table]) == rows
    landing = tmp_path / "landing" / sid
    assert sorted(p.name for p in landing.iterdir() if not p.name.startswith(".")) == [
        "customer",
        "order",
    ]
    lake = [f for f in fs.files if f"shape_demo/{sid}/" in f]
    assert lake and all(f.endswith(".parquet") for f in lake)

    # cleanup: everything the run made is gone, and only that
    fs.files[f"{WS}/{LH}/Files/unrelated.txt"] = b"mine"
    done = demo_cleanup(sid, runtime=DemoRuntime(services=services))
    assert done["ok"] and done["failed"] == [], done
    assert warehouse.tables == {} and sqldb.tables == {} and kusto.tables == {}
    assert not landing.exists() and (tmp_path / "landing").exists()
    assert not [f for f in fs.files if f"shape_demo/{sid}/" in f]
    assert f"{WS}/{LH}/Files/unrelated.txt" in fs.files and f"{WS}/{LH}/Files/.keep" in fs.files
    again = demo_cleanup(sid, runtime=DemoRuntime(services=services))
    assert again["ok"]  # a second cleanup finds nothing to do


def test_a_table_that_exists_is_neither_overwritten_nor_recorded_nor_dropped(tmp_path, home):
    sqldb = FakeSqlServer()
    cursor = sqldb.connect("x").cursor()
    cursor.execute("CREATE TABLE [dbo].[customer] (\n[mine] INT NOT NULL\n)")
    sqldb.connect("x").commit()
    sqldb.tables[("dbo", "customer")].rows.append((7,))
    sqldb.snapshot()  # a committed row of the user's
    schema = tmp_path / "shop.json"
    schema.write_text(json.dumps(schema_doc()))
    demo_init("db", sql_db_conn=SQLDB)

    def sinks(name, s):
        return SqlDatabaseSink(
            s["connection_string"], s["schema_name"], s["write_mode"],
            writer_options={"connection": sqldb.connect("injected")},
        )  # fmt: skip

    services = FabricServices(
        ConnectionRegistry().load("db"), connect=lambda cs, c=None, **k: sqldb.connect(cs)
    )
    result = demo_run(
        {
            "scenario": "retail", "mode": "seeding", "connection": "db", "rows": 1000,
            "domain": str(schema),
        },
        runtime=DemoRuntime(sink_factory=sinks, services=services),
    )  # fmt: skip
    assert result["success"] is False and "already exists" in result["error"]
    # the run made nothing, so there is nothing to roll back; the user's table and row are intact
    assert "rolled back" not in result["error"]
    assert sqldb.rows("dbo", "customer") == [(7,)]
    record = json.loads((home / "sessions" / f"demo-{result['session_id']}.json").read_text())
    assert record["artifacts"] == []
