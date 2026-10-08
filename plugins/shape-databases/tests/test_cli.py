"""Snowflake and Databricks through ``shape generate --to``, ``shape emit --to`` and
``shape plugins list`` (the sinks are real entry points; only the connection is a fake)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from shape_databases import DatabricksSink, SnowflakeSink
from shape_databases.testing import FakeDatabricks, FakeSnowflake

from shape.cli.main import main

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "scale"))
from scale_schemas import plain_doc  # noqa: E402

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}
SF = "snowflake://shape@acct.eu-west-1/DB/PUBLIC?warehouse=WH"
DBX = "databricks://adb-1.azuredatabricks.net/sql/1.0/warehouses/w?catalog=main&schema=demo"


@pytest.fixture(autouse=True)
def _confirm_remote(monkeypatch):
    # About the sinks, not the confirmation (W1-17, tests/cli/test_remote_confirmation.py).
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")


@pytest.fixture
def schema_file(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(plain_doc(ROWS)))
    return path


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_generate_to_snowflake(capsys, schema_file, monkeypatch):
    server = FakeSnowflake()
    monkeypatch.setattr(SnowflakeSink, "default_connect", lambda self, **p: server.connect(**p))
    monkeypatch.setenv("SF_PW", "cli-secret-1")
    code, out, err = run(
        capsys,
        "generate",
        schema_file,
        "--to",
        SF,
        "--sink-config",
        "snowflake.password=env://SF_PW",
        "--json",
    )
    assert code == 0, err
    assert json.loads(out)["targets"][SF] == ROWS
    assert [len(server.rows(t)) for t in ROWS] == list(ROWS.values())
    assert server.events[0][1]["password"] == "cli-secret-1"
    assert "cli-secret-1" not in out + err
    assert all(not files for files in server.stages.values())  # nothing left staged


def test_generate_to_databricks(capsys, schema_file, monkeypatch):
    server = FakeDatabricks()
    monkeypatch.setattr(DatabricksSink, "default_connect", lambda self, **p: server.connect(**p))
    monkeypatch.setenv("DBX_T", "cli-token-1")
    code, out, err = run(
        capsys,
        "generate",
        schema_file,
        "--to",
        DBX,
        "--sink-config",
        "databricks.token=env://DBX_T",
        "--json",
    )
    assert code == 0, err
    assert json.loads(out)["targets"][DBX] == ROWS
    assert [len(server.rows(t)) for t in ROWS] == list(ROWS.values())
    assert server.events[0][1]["access_token"] == "cli-token-1"
    assert "cli-token-1" not in out + err


def test_a_literal_secret_on_the_command_line_is_refused(capsys, schema_file):
    code, out, err = run(
        capsys, "generate", schema_file, "--to", SF, "--sink-config", "snowflake.password=hunter2"
    )
    assert code == 2 and "hunter2" not in out + err
    code, out, err = run(
        capsys, "generate", schema_file, "--to", DBX, "--sink-config", "databricks.token=dapi123"
    )
    assert code == 2 and "dapi123" not in out + err


@pytest.mark.parametrize(
    ("uri", "secret"),
    [
        ("snowflake://shape:hunter2@acct/DB/PUBLIC", "hunter2"),
        ("snowflake://shape@acct/DB/PUBLIC?password=hunter2", "hunter2"),
        ("databricks://token:dapi123@h.example/sql/1.0/warehouses/w", "dapi123"),
        ("databricks://h.example/sql/1.0/warehouses/w?token=dapi123", "dapi123"),
    ],
)
def test_a_secret_in_the_uri_is_refused_and_never_echoed(capsys, schema_file, uri, secret):
    code, out, err = run(capsys, "generate", schema_file, "--to", uri)
    assert code == 2
    assert secret not in out + err
    assert "must not be part of the URI" in err


@pytest.mark.parametrize(
    ("uri", "module", "extra"),
    [
        (SF, "snowflake.connector", "snowflake"),
        (DBX, "databricks.sql", "databricks"),
    ],
)
def test_a_missing_driver_is_exit_2_with_the_pip_line(
    capsys, schema_file, monkeypatch, uri, module, extra
):
    monkeypatch.setitem(sys.modules, module.split(".")[0], None)
    monkeypatch.setitem(sys.modules, module, None)
    code, _, err = run(capsys, "generate", schema_file, "--to", uri)
    assert code == 2
    assert f"pip install 'sqllocks-shape-databases[{extra}]'" in err


def test_emit_to_snowflake_and_databricks(capsys, monkeypatch):
    sf = FakeSnowflake()
    dbx = FakeDatabricks()
    monkeypatch.setattr(SnowflakeSink, "default_connect", lambda self, **p: sf.connect(**p))
    monkeypatch.setattr(DatabricksSink, "default_connect", lambda self, **p: dbx.connect(**p))
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "pw")
    monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
    base = ["emit", "retail", "--scale", "small", "--seed", "3", "--table", "customer"]
    code, _, err = run(capsys, *base, "--max-events", "250", "--to", SF)
    assert code == 0, err
    assert len(sf.rows("customer")) == 250
    assert sum(s.startswith("COPY INTO") for s in sf.statements()) >= 1
    code, _, err = run(capsys, *base, "--max-events", "250", "--to", DBX)
    assert code == 0, err
    assert len(dbx.rows("customer")) == 250


def test_both_sinks_are_listed_by_plugins_list():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from shape.cli.main import main; sys.exit(main())",
            "plugins",
            "list",
            "--json",
            "--group",
            "shape.sinks",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = {x["name"]: x for x in json.loads(result.stdout)["payload"]}  # W1-14: under payload
    assert {"snowflake", "databricks"} <= set(rows)
    assert rows["snowflake"]["source"] == "sqllocks-shape-databases"
    assert rows["databricks"]["source"] == "sqllocks-shape-databases"


def test_importing_the_plugin_loads_no_driver():
    code = (
        "import sys, shape_databases; "
        "bad = [m for m in ('snowflake', 'databricks', 'cryptography', 'pymysql', 'psycopg') "
        "if m in sys.modules]; sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
