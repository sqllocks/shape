"""P6-12: seeding to the Fabric targets, cleanup, rollback and preflight, against recording
stand-ins (the real writers against in-repo services are in the shape-fabric plugin's tests)."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest
from demo_helpers import ROWS, FakeServices, write_schema

from shape.demo.api import demo_cleanup, demo_init, demo_preflight, demo_run
from shape.demo.runtime import DemoRuntime
from shape.scale.sinks.fabric import KqlSink, LakehouseSink, SqlDatabaseSink, WarehouseSink

WS, LH = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
ORDER = ["customer", "order", "order_line"]


class Recorder:
    """A ``shape.sinks`` writer that keeps what it was given; ``fail_on`` names a table whose
    load fails (the destination refuses it)."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[dict] = []

    def write(self, uri, table, batches, **options):
        got = list(batches)
        if table == self.fail_on:
            raise RuntimeError(f"table {table} already exists")
        self.calls.append({"uri": uri, "table": table, "options": options})
        return sum(b.num_rows for b in got)

    def tables(self) -> list[str]:
        return [c["table"] for c in self.calls]


def factory(writers: dict[str, Recorder]):
    """A ``sink_factory``: each remote sink writes through its own recorder."""

    def make(name, settings):
        w = writers.setdefault(name, Recorder())
        s = dict(settings)
        if name == "lakehouse":
            return LakehouseSink(s["base_path"], s["format"], writer=w)
        if name == "warehouse":
            return WarehouseSink(
                s["connection_string"],
                s["staging_path"],
                s["schema_name"],
                s["write_mode"],
                writer=w,
            )
        if name == "sql_database":
            return SqlDatabaseSink(
                s["connection_string"], s["schema_name"], s["write_mode"], writer=w
            )
        if name == "kql":
            return KqlSink(
                s["cluster_uri"], s["database"], s["table_prefix"], s["write_mode"], writer=w
            )
        return None  # the local Parquet sink is the real one

    return make


@pytest.fixture
def remote_profile(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DEMO_WH", "Driver={x};Server=w.datawarehouse.fabric.microsoft.com;Database=wh"
    )
    monkeypatch.setenv("DEMO_SQL", "Driver={x};Server=db.test;Database=d")
    demo_init(
        "all", workspace_id=WS, lakehouse_id=LH, warehouse_conn="env://DEMO_WH",
        warehouse_staging_path="onelake://ws/lh/Files/stage", sql_db_conn="env://DEMO_SQL",
        eventhouse_uri="https://kql.example.test", eventhouse_database="events",
        local_path=str(tmp_path / "landing"),
    )  # fmt: skip
    return "all"


def seed(schema_file, runtime, **extra):
    settings = {"scenario": "retail", "mode": "seeding", "connection": "all", "rows": 1000}
    return demo_run({**settings, "domain": str(schema_file), "seed": 4, **extra}, runtime=runtime)


def manifest(home, session: str) -> dict:
    return json.loads((home / "sessions" / f"demo-{session}.json").read_text())


def test_seeding_writes_every_table_to_every_target(home, tmp_path, schema_file, remote_profile):
    writers: dict[str, Recorder] = {}
    result = seed(schema_file, DemoRuntime(sink_factory=factory(writers)))
    assert result["success"], result
    sid = result["session_id"]
    for name in ("lakehouse", "warehouse", "sql_database", "kql"):
        assert sorted(writers[name].tables()) == sorted(ORDER), name
    # the options each destination was given: create mode only, and the schema the tables go to
    wh = writers["warehouse"].calls[0]
    assert wh["options"]["write_mode"] == "create" and wh["options"]["schema_name"] == "dbo"
    assert wh["options"]["staging_path"] == "onelake://ws/lh/Files/stage"
    assert writers["sql_database"].calls[0]["options"]["write_mode"] == "create"
    base = f"onelake://{WS}/{LH}/Files/shape_demo/{sid}"
    assert {c["uri"] for c in writers["lakehouse"].calls} == {base}

    rec = manifest(home, sid)
    got = {(a["target"], a["name"]): (a["row_count"], a["detail"]) for a in rec["artifacts"]}
    for table, rows in ROWS.items():
        assert got[("file", table)][0] == rows
        assert got[("lakehouse", table)] == (rows, f"{base}/{table}")
        assert got[("warehouse", table)] == (rows, f"dbo.{table}")
        assert got[("sql_db", table)] == (rows, f"dbo.{table}")
        assert got[("eventhouse", table)] == (rows, "")
    assert len(rec["artifacts"]) == 5 * len(ROWS)


def test_cleanup_drops_exactly_what_the_run_made(home, schema_file, remote_profile):
    result = seed(schema_file, DemoRuntime(sink_factory=factory({})))
    sid = result["session_id"]
    services = FakeServices()
    out = demo_cleanup(sid, runtime=DemoRuntime(services=services))
    assert out["ok"] and out["failed"] == []
    base = f"onelake://{WS}/{LH}/Files/shape_demo/{sid}"
    expected = set()
    for table in ORDER:
        expected |= {
            ("drop_sql_table", "warehouse", "dbo", table),
            ("drop_sql_table", "sql_db", "dbo", table),
            ("drop_kql_table", table),
            ("remove_files", f"{base}/{table}"),
        }
    assert set(services.calls) == expected
    assert sorted(out["removed"]) == ["eventhouse", "file", "lakehouse", "sql_db", "warehouse"]


def test_cleanup_reports_what_it_could_not_remove(home, schema_file, remote_profile):
    sid = seed(schema_file, DemoRuntime(sink_factory=factory({})))["session_id"]
    services = FakeServices(fail=("order",))
    out = demo_cleanup(sid, runtime=DemoRuntime(services=services))
    assert out["ok"] is False
    failed = {(f["target"], f["name"]) for f in out["failed"]}
    assert failed == {
        ("warehouse", "order"),
        ("sql_db", "order"),
        ("eventhouse", "order"),
        ("lakehouse", "order"),
    }
    assert all("failed for" in f["error"] for f in out["failed"])
    # the failed ones are not in the removed list; the rest are
    for target in ("warehouse", "sql_db", "eventhouse", "lakehouse"):
        assert "order" not in out["removed"][target]
        assert {"customer", "order_line"} <= set(out["removed"][target])


def test_cleanup_command_exits_one_when_a_removal_fails(
    run, home, schema_file, remote_profile, monkeypatch
):
    sid = seed(schema_file, DemoRuntime(sink_factory=factory({})))["session_id"]
    import shape.demo.services as services

    monkeypatch.setattr(services, "FabricServices", lambda profile: FakeServices(fail=("order",)))
    code, out, err = run("demo", "cleanup", sid)
    assert code == 1 and "FAILED: warehouse/order" in err and "Removed: warehouse/customer" in out


def test_a_cleanup_with_no_profile_cannot_reach_a_remote_target(home, schema_file, remote_profile):
    sid = seed(schema_file, DemoRuntime(sink_factory=factory({})))["session_id"]
    path = home / "sessions" / f"demo-{sid}.json"
    doc = json.loads(path.read_text())
    doc["params"].pop("connection")  # a session that lost its profile name
    path.write_text(json.dumps(doc))
    out = demo_cleanup(sid, runtime=DemoRuntime(services=FakeServices()))
    # the local files can still be removed; the remote tables are reported, not claimed removed
    assert "file" in out["removed"] and "warehouse" not in out["removed"]
    assert {f["target"] for f in out["failed"]} == {
        "warehouse",
        "sql_db",
        "eventhouse",
        "lakehouse",
    }
    assert "no connection profile" in out["failed"][0]["error"]


def test_a_table_the_destination_refuses_is_not_recorded_nor_dropped(
    home, schema_file, remote_profile
):
    writers = {"warehouse": Recorder(fail_on="customer")}  # customer already exists there
    services = FakeServices()
    result = seed(schema_file, DemoRuntime(sink_factory=factory(writers), services=services))
    assert result["success"] is False
    assert "already exists" in result["error"] and "rolled back" in result["error"]
    rec = manifest(home, result["session_id"])
    assert ("warehouse", "customer") not in {(a["target"], a["name"]) for a in rec["artifacts"]}
    # the rollback never dropped a warehouse table (the demo made none), so the user's table lives
    assert not [c for c in services.calls if c[:2] == ("drop_sql_table", "warehouse")]
    # and nothing of the failed run is left in the local folder
    landing = home.parent / "landing"
    assert not landing.exists() or not any(landing.iterdir())


def test_a_composite_run_gives_each_domain_its_own_schema_and_prefix(
    home, tmp_path, remote_profile
):
    a = write_schema(tmp_path / "alpha.json", {"customer": 5, "order": 9, "order_line": 12})
    b = write_schema(tmp_path / "beta.json", {"customer": 7, "order": 8, "order_line": 6})
    writers: dict[str, Recorder] = {}
    result = demo_run(
        {"scenario": "enterprise", "mode": "seeding", "connection": "all", "rows": 1000,
         "domains": f"{a},{b}"},
        runtime=DemoRuntime(sink_factory=factory(writers)),
    )  # fmt: skip
    assert result["success"], result
    schemas = {c["options"]["schema_name"] for c in writers["warehouse"].calls}
    assert schemas == {"alpha", "beta"}
    kql = {c["options"]["kql_table"] for c in writers["kql"].calls}
    assert "alpha_customer" in kql and "beta_order_line" in kql and len(kql) == 6
    rec = manifest(home, result["session_id"])
    details = {a["detail"] for a in rec["artifacts"] if a["target"] == "sql_db"}
    assert "alpha.customer" in details and "beta.order_line" in details


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"warehouse_conn": "env://W"}, "warehouse but no warehouse_staging_path"),
        ({"eventhouse_uri": "https://k.test"}, "Eventhouse URI but no database"),
        ({"lakehouse_id": LH}, "lakehouse_id but no workspace_id"),
    ],
)
def test_a_profile_missing_a_setting_fails_instead_of_skipping_the_target(
    home, schema_file, fields, message
):
    demo_init("half", **fields)
    result = demo_run(
        {"scenario": "retail", "mode": "seeding", "connection": "half", "domain": str(schema_file)},
        runtime=DemoRuntime(),
    )
    assert result["success"] is False and message in result["error"]


def test_preflight_checks_each_target_and_reports_each(
    home, tmp_path, remote_profile, run, monkeypatch
):
    import shape.demo.services as services

    fake = FakeServices(fail=("sql_db",))
    monkeypatch.setattr(services, "FabricServices", lambda profile: fake)
    code, out, _ = run("demo", "preflight", "--connection", "all")
    assert code == 1
    assert "[OK] Local folder" in out and "[OK] Lakehouse: lakehouse answered" in out
    assert "[OK] Warehouse" in out and "[OK] Eventhouse" in out
    assert "[FAIL] SQL DB: check failed for sql_db" in out
    assert [c[1] for c in fake.calls] == ["lakehouse", "warehouse", "sql_db", "eventhouse"]


def test_preflight_does_not_pass_a_target_it_did_not_check(home):
    demo_init(
        "thin", sql_db_conn="env://DEMO_SQL", eventhouse_uri="https://k.test", lakehouse_id=LH
    )
    out = demo_preflight("thin", runtime=DemoRuntime(services=FakeServices()))
    checks = {c["target"]: c for c in out["profiles"][0]["checks"]}
    assert checks["sql_db"]["status"] == "ok"  # the stand-in answered
    assert checks["eventhouse"] == {
        "target": "eventhouse",
        "status": "fail",
        "message": "database is not set",
    }
    assert (
        checks["lakehouse"]["status"] == "fail" and "workspace_id" in checks["lakehouse"]["message"]
    )
    assert out["ok"] is False


def test_preflight_of_a_local_folder(home, tmp_path):
    demo_init("here", local_path=str(tmp_path / "new" / "deeper"))
    out = demo_preflight("here")
    assert out["profiles"][0]["checks"][0]["status"] == "fail"  # its parent does not exist
    file = tmp_path / "afile"
    file.write_text("x")
    demo_init("bad", local_path=str(file))
    assert demo_preflight("bad")["profiles"][0]["checks"][0]["message"].endswith("is not a folder")
    (tmp_path / "ok").mkdir()
    demo_init("fine", local_path=str(tmp_path / "ok" / "landing"))
    assert demo_preflight("fine")["ok"] is True


def test_preflight_with_no_profiles_is_a_message(run):
    code, _, err = run("demo", "preflight")
    assert code == 2 and "no connection profiles found" in err


def test_a_profile_with_no_targets_is_skipped_not_ok(home):
    demo_init("empty", workspace_id=WS)
    out = demo_preflight("empty")
    assert out["profiles"][0]["checks"][0]["status"] == "skipped" and out["ok"] is True


def test_the_pyarrow_batches_the_recorders_saw_are_the_domains_rows(
    home, schema_file, remote_profile
):
    # a guard on the recorder itself: it counts rows from real batches
    w = Recorder()
    batch = pa.RecordBatch.from_pydict({"a": [1, 2, 3]})
    assert w.write("u", "t", iter([batch, batch])) == 6


@pytest.mark.parametrize(
    ("target", "name", "detail"),
    [
        ("warehouse", "t", "dbo.t; DROP TABLE users"),
        ("sql_db", "t", "a.b.c"),
        ("sql_db", "t", "dbo.t]"),
        ("eventhouse", "x'; .drop table y", ""),
    ],
)
def test_cleanup_refuses_a_recorded_name_that_is_not_a_plain_table_name(home, target, name, detail):
    (home / "sessions").mkdir(parents=True)
    record = {
        "session_id": "hostile1", "scenario": "retail", "mode": "seeding", "started_at": "t",
        "finished_at": None, "success": True, "error": None, "params": {}, "metrics": {},
        "artifacts": [{"target": target, "name": name, "row_count": 1, "detail": detail}],
    }  # fmt: skip
    (home / "sessions" / "demo-hostile1.json").write_text(json.dumps(record))
    demo_init("any", sql_db_conn="env://DEMO_SQL")
    services = FakeServices()
    out = demo_cleanup("hostile1", connection="any", runtime=DemoRuntime(services=services))
    assert out["ok"] is False and "unsafe table name" in out["failed"][0]["error"]
    assert services.calls == []  # nothing was sent to the destination
