"""P6-12: a Spark run and its status (against a recorded Fabric stand-in), and the plain-function
API the JSON bridge calls."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from demo_helpers import ROWS, write_schema
from fakes import LH, NB, RUN, WS, FakeFabric

from shape.demo.api import (
    demo_cleanup,
    demo_init,
    demo_list,
    demo_notebook,
    demo_report,
    demo_run,
    demo_status,
)
from shape.demo.errors import DemoError, SessionNotFoundError
from shape.demo.runtime import DemoRuntime
from shape.scale.jobs import Jobs, JobStore


@pytest.fixture
def spark(tmp_path):
    demo_init("lake", workspace_id=WS, lakehouse_id=LH)
    fabric = FakeFabric()
    jobs = Jobs(JobStore(tmp_path / "jobs"), transport=fabric)
    return fabric, DemoRuntime(transport=fabric, token="tok", storage_token="stor", jobs=jobs)


def spark_run(schema_file, runtime, **extra):
    settings = {"scenario": "retail", "mode": "seeding", "connection": "lake", "rows": 1000}
    return demo_run(
        {**settings, "domain": str(schema_file), "scale_mode": "spark", **extra}, runtime=runtime
    )


def test_a_spark_run_submits_a_job_and_records_where_the_tables_will_be(home, schema_file, spark):
    fabric, runtime = spark
    result = spark_run(schema_file, runtime, seed=6)
    assert result["success"] and result["status"] == "submitted" and result["scale_mode"] == "spark"
    assert result["fabric_run_id"].startswith(RUN[:-1])
    spec = fabric.uploaded_specs()[0]
    assert spec["row_counts"] == ROWS and spec["seed"] == 6 and spec["sinks"] == ["lakehouse"]
    assert spec["table_prefix"] == "shape_shop_small_"
    record = json.loads((home / "sessions" / f"demo-{result['session_id']}.json").read_text())
    assert record["notebook_item_id"] == NB and record["workspace_id"] == WS
    tables = {a["name"]: (a["row_count"], a["detail"]) for a in record["artifacts"]}
    assert tables == {
        f"shape_shop_small_{t}": (n, f"onelake://{WS}/{LH}/Tables/shape_shop_small_{t}")
        for t, n in ROWS.items()
    }


def test_status_asks_fabric_for_the_job(home, schema_file, spark):
    fabric, runtime = spark
    result = spark_run(schema_file, runtime)
    out = demo_status(result["session_id"], runtime=runtime)
    assert (
        out["fabric"]["status"] == "submitted"
        and out["fabric"]["fabric_run_id"] == result["fabric_run_id"]
    )
    fabric.job_status = "Completed"
    assert demo_status(result["session_id"], runtime=runtime)["fabric"]["status"] == "succeeded"
    assert out["manifest"]["fabric_run_id"] == result["fabric_run_id"]


def test_status_takes_its_token_from_the_call_the_environment_or_the_profiles_sign_in(
    home, schema_file, spark, monkeypatch
):
    fabric, runtime = spark
    sid = spark_run(schema_file, runtime)["session_id"]
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    monkeypatch.setattr("shape.scale.api._auth_tokens", lambda auth: ("signed-in", "storage"))
    bare = DemoRuntime(transport=fabric)
    demo_status(sid, runtime=bare)  # no token given: the profile's sign-in is used
    assert fabric.calls[-1]["headers"]["Authorization"] == "Bearer signed-in"
    monkeypatch.setenv("SHAPE_FABRIC_TOKEN", "from-env")
    demo_status(sid, runtime=bare)
    assert fabric.calls[-1]["headers"]["Authorization"] == "Bearer from-env"
    demo_status(sid, token="given", runtime=bare)
    assert fabric.calls[-1]["headers"]["Authorization"] == "Bearer given"


def test_status_without_any_way_to_sign_in_says_so(home, schema_file, spark, monkeypatch):
    fabric, runtime = spark
    sid = spark_run(schema_file, runtime)["session_id"]
    path = home / "sessions" / f"demo-{sid}.json"
    doc = json.loads(path.read_text())
    doc["params"].pop("connection")
    path.write_text(json.dumps(doc))
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    with pytest.raises(DemoError, match="Fabric token is needed"):
        demo_status(sid, runtime=DemoRuntime(transport=fabric))


def test_cleanup_of_a_spark_session_removes_the_delta_tables(home, schema_file, spark):
    from demo_helpers import FakeServices

    fabric, runtime = spark
    sid = spark_run(schema_file, runtime)["session_id"]
    services = FakeServices()
    out = demo_cleanup(sid, runtime=DemoRuntime(services=services))
    assert out["ok"]
    assert {c[1] for c in services.calls} == {
        f"onelake://{WS}/{LH}/Tables/shape_shop_small_{t}" for t in ROWS
    }


def test_spark_needs_a_profile_with_a_lakehouse(home, schema_file):
    result = spark_run(schema_file, DemoRuntime(), connection=None)
    assert (
        result["success"] is False and "Spark mode requires a connection profile" in result["error"]
    )
    demo_init("nolake", local_path=str(home.parent / "x"))
    result = spark_run(schema_file, DemoRuntime(), connection="nolake")
    assert "requires lakehouse_id" in result["error"]


def test_auto_picks_spark_only_from_half_a_million_rows(home, tmp_path, spark):
    from shape.demo.connections import ConnectionRegistry
    from shape.demo.modes.seeding import resolve_scale_mode

    profile = ConnectionRegistry().load("lake")
    assert resolve_scale_mode("auto", profile, 499_999) == "local"
    assert resolve_scale_mode("auto", profile, 500_000) == "spark"
    assert resolve_scale_mode("auto", None, 10_000_000) == "local"
    assert resolve_scale_mode("local", profile, 10_000_000) == "local"


def test_a_spark_run_is_one_domain(home, tmp_path, spark):
    a = write_schema(tmp_path / "a.json")
    b = write_schema(tmp_path / "b.json")
    result = demo_run(
        {"scenario": "enterprise", "mode": "seeding", "connection": "lake", "domains": f"{a},{b}",
         "scale_mode": "spark", "rows": 1000},
        runtime=spark[1],
    )  # fmt: skip
    assert result["success"] is False and "spark mode runs one domain" in result["error"]


# ---- the API -----------------------------------------------------------------------------------


def test_demo_run_returns_the_payload_the_bridge_needs(home, schema_file):
    result = demo_run(
        {"scenario": "retail", "mode": "seeding", "domain": str(schema_file), "rows": 1000}
    )
    assert set(result) == {
        "success", "session_id", "scenario", "mode", "fidelity_score", "error", "artifact_count",
        "scale_mode",
    }  # fmt: skip
    assert result["success"] is True and result["artifact_count"] == 3
    assert result["scale_mode"] == "local" and result["fidelity_score"] is None
    json.dumps(result)  # JSON-safe


def test_a_failed_run_is_a_result_not_an_exception(home):
    result = demo_run({"scenario": "healthcare", "mode": "seeding", "rows": 1000})
    assert result["success"] is False and "no domain named 'healthcare'" in result["error"]
    assert (home / "sessions" / f"demo-{result['session_id']}.json").exists()


def test_status_and_report_and_cleanup_name_a_missing_session(home):
    with pytest.raises(SessionNotFoundError):
        demo_status("missing1")
    with pytest.raises(SessionNotFoundError):
        demo_report("missing1")
    with pytest.raises(SessionNotFoundError):
        demo_cleanup("missing1")


def test_messages_of_a_run_go_to_the_stream_given(home, schema_file, capsys):
    import io

    stream = io.StringIO()
    demo_run(
        {"scenario": "retail", "mode": "seeding", "domain": str(schema_file), "rows": 1000},
        runtime=DemoRuntime(out=stream),
    )
    assert "Shape Demo — retail (seeding)" in stream.getvalue()
    assert capsys.readouterr().out == ""  # standard output stays free for a JSON answer


def test_the_plain_functions_have_the_documented_signatures():
    import inspect

    from shape.demo import api

    sigs = {n: str(inspect.signature(getattr(api, n))) for n in (
        "demo_list", "demo_run", "demo_status", "demo_cleanup", "demo_preflight",
        "demo_notebook", "demo_report", "demo_init",
    )}  # fmt: skip
    assert sigs["demo_list"] == "() -> 'dict[str, Any]'"
    assert sigs["demo_run"].startswith("(params: 'Mapping[str, Any] | DemoParams', *, runtime")
    assert sigs["demo_status"].startswith("(session_id: 'str', *, token: 'str | None' = None")
    assert sigs["demo_cleanup"].startswith(
        "(session_id: 'str', *, dry_run: 'bool' = False, connection: 'str | None' = None"
    )
    assert demo_list()["count"] == 4


def test_importing_the_command_loads_no_heavy_library():
    code = (
        "import sys, shape.cli.demo, shape.demo\n"
        "bad = [m for m in ('numpy', 'pyarrow', 'shape.generation.engine') if m in sys.modules]\n"
        "assert not bad, bad"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_demo_notebook_function_writes_the_file(tmp_path):
    out = demo_notebook("retail", "seeding", tmp_path / "n.ipynb")
    assert out["path"] == str(tmp_path / "n.ipynb")
    assert json.loads((tmp_path / "n.ipynb").read_text())["nbformat"] == 4
