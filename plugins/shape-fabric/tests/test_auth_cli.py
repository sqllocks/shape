"""P6-07b through the command line: ``--auth`` and credential references on ``shape generate
--scale-mode`` (SQL sinks), ``shape emit`` (Eventhouse), with the real plugin and the in-repo fakes.

The invariant every test here ends with: **no secret appears** in standard output, standard error,
the job store's files, the log, or what a failing driver echoed back.
"""

from __future__ import annotations

import json
import logging
import sys

import pytest
from shape_fabric import _tsql, kusto
from shape_fabric.testing import (
    FAKE_ENTRA_TOKEN,
    FakeIdentity,
    FakeKeyVault,
    FakeKusto,
    FakeSqlServer,
)

from shape.cli.main import main
from shape.security import credrefs

pytestmark = pytest.mark.contract

PASSWORD = "Pa55;w0rd}value"  # made up; ';' and '}' exercise the ODBC quoting
CS = "Driver={ODBC Driver 18 for SQL Server};Server=db.example.test;Database=d"
ROWS = {"customer": 30, "order": 200}


def _col(name, strategy, type_="integer", **gen):
    return {
        "name": name,
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": False,
        "null_rate": 0.0,
    }


DOC = {
    "schema_version": 1,
    "model": {"name": "t", "seed": 5},
    "tables": {
        "customer": {
            "name": "customer",
            "primary_key": ["customer_id"],
            "columns": {"customer_id": _col("customer_id", "sequence")},
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
    "generation": {"scale": "small", "scales": {"small": ROWS}},
}


@pytest.fixture
def world(tmp_path, monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(DOC))
    server = FakeSqlServer()
    seen = {"connections": []}

    def connect(connection_string, credential=None, **kw):
        seen["connections"].append((connection_string, credential))
        return server.connect(connection_string, credential, **kw)

    monkeypatch.setattr(_tsql, "connect", connect)

    class World:
        pass

    w = World()
    w.schema, w.server, w.seen, w.tmp = str(schema), server, seen, tmp_path
    w.monkeypatch = monkeypatch

    def run(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    w.run = run

    def everything_shown(*secrets):
        """Every place a secret could have leaked to, checked for each secret."""
        jobs = tmp_path / "jobs"
        files = (
            "".join(p.read_text() for p in jobs.rglob("*") if p.is_file()) if jobs.exists() else ""
        )
        return files + caplog.text

    w.leaks = everything_shown
    return w


def sink_args(world, *extra):
    return (
        "generate", world.schema, "--scale-mode", "local_single", "--sink", "sql_database",
        *extra,
    )  # fmt: skip


def check_clean(world, out, err, *secrets):
    seen = out + err + world.leaks()
    for secret in secrets:
        assert secret not in seen, f"{secret!r} leaked"


# --- sql auth -----------------------------------------------------------------------------


def test_sql_login_from_references_writes_the_rows_and_leaks_nothing(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    monkeypatch.setenv("SHAPE_TEST_PW", PASSWORD)
    code, out, err = world.run(
        *sink_args(
            world,
            "--sink-config",
            "sql_database.connection_string=env://SHAPE_TEST_CS",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "env://SHAPE_TEST_PW",
            "--json",
        )  # fmt: skip
    )
    assert code == 0, err
    assert [len(world.server.rows("dbo", t)) for t in ROWS] == list(ROWS.values())
    (conn, credential), *_ = world.seen["connections"]
    assert conn.startswith(CS) and "UID={app}" in conn and "PWD={Pa55;w0rd}}value}" in conn
    assert credential is None  # a SQL login signs in through the connection string alone
    check_clean(world, out, err, PASSWORD, "Pa55")


def test_connection_string_and_password_from_files(world, monkeypatch):
    cs_file = world.tmp / "cs.txt"
    cs_file.write_text(CS + "\n")
    cs_file.chmod(0o600)
    pw_file = world.tmp / "pw.txt"
    pw_file.write_text(PASSWORD)
    pw_file.chmod(0o600)
    code, out, err = world.run(
        *sink_args(
            world,
            "--connection-string",
            f"file://{cs_file}",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            f"file://{pw_file}",
        )  # fmt: skip
    )
    assert code == 0, err
    assert len(world.server.rows("dbo", "customer")) == 30
    check_clean(world, out, err, PASSWORD)


def test_password_from_key_vault(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    vault = FakeKeyVault({("vault-one", "sql-password"): PASSWORD})
    monkeypatch.setattr(
        "shape_fabric.keyvault.default_credential", lambda: lambda scope: "tok-" + "k" * 20
    )
    monkeypatch.setattr("shape_fabric.keyvault.urllib_transport", vault)
    code, out, err = world.run(
        *sink_args(
            world,
            "--connection-string",
            "env://SHAPE_TEST_CS",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "kv://vault-one/sql-password",
        )  # fmt: skip
    )
    assert code == 0, err
    assert "PWD={Pa55;w0rd}}value}" in world.seen["connections"][0][0]
    assert len(vault.requests) == 1
    check_clean(world, out, err, PASSWORD, "tok-kkkk")


@pytest.mark.parametrize(
    "flags",
    [
        ("--sql-password", "hunter2hunter2"),
        ("--client-secret", "hunter2hunter2"),
        ("--connection-string", CS + ";UID=app;PWD=hunter2hunter2"),
        ("--connection-string", CS + ";Password=hunter2hunter2"),
        ("--sink-config", "sql_database.connection_string=" + CS + ";PWD=hunter2hunter2"),
        ("--sink-config", "sql_database.password=hunter2hunter2"),
    ],
)
def test_a_secret_on_the_command_line_is_refused_and_not_echoed(world, flags):
    extra = ("--auth", "sql", "--sql-user", "app") if "--sql-password" in flags else ()
    if flags[0] == "--client-secret":
        extra = ("--auth", "spn", "--tenant-id", "t", "--client-id", "c")
    code, out, err = world.run(*sink_args(world, *extra, *flags))
    assert code == 2
    assert "hunter2hunter2" not in out + err + world.leaks()
    assert "reference" in err or "password" in err
    assert world.server.tables == {}  # nothing was written


def test_sql_options_belong_to_sql_mode(world):
    code, _, err = world.run(
        *sink_args(world, "--auth", "cli", "--sql-user", "app", "--sql-password", "env://X")
    )
    assert code == 2 and "belong to --auth sql" in err


def test_a_missing_reference_is_an_error_that_names_it(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    monkeypatch.delenv("SHAPE_TEST_MISSING", raising=False)
    code, _, err = world.run(
        *sink_args(
            world,
            "--connection-string",
            "env://SHAPE_TEST_CS",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "env://SHAPE_TEST_MISSING",
        )  # fmt: skip
    )
    assert code == 1 and "SHAPE_TEST_MISSING" in err  # the sink failed; the job can be resumed


def test_the_job_record_keeps_references_so_a_resume_signs_in_again(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    monkeypatch.setenv("SHAPE_TEST_PW", PASSWORD)
    world.server.fail = lambda sql, params: (_ for _ in ()).throw(RuntimeError("boom"))
    code, out, err = world.run(
        *sink_args(
            world,
            "--connection-string",
            "env://SHAPE_TEST_CS",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "env://SHAPE_TEST_PW",
        )  # fmt: skip
    )
    assert code == 1
    record = next((world.tmp / "jobs").glob("*.json")).read_text()
    assert "env://SHAPE_TEST_PW" in record and "env://SHAPE_TEST_CS" in record
    assert PASSWORD not in record
    world.server.fail = None
    job_id = json.loads(record)["job_id"]
    code, out, err = world.run("jobs", "resume", job_id, "--json")
    assert code == 0, err
    assert len(world.server.rows("dbo", "order")) == 200
    check_clean(world, out, err, PASSWORD)


def test_a_driver_that_echoes_the_connection_string_in_its_error_leaks_nothing(world, monkeypatch):
    class Pyodbc:
        class Error(Exception):
            pass

        @classmethod
        def connect(cls, cs, **kw):
            raise cls.Error(f"[28000] Login failed. Connection: {cs}")

    monkeypatch.undo()  # use the real _tsql.connect over a fake driver
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(world.tmp / "jobs"))
    monkeypatch.setitem(sys.modules, "pyodbc", Pyodbc)
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    monkeypatch.setenv("SHAPE_TEST_PW", PASSWORD)
    code, out, err = world.run(
        *sink_args(
            world,
            "--connection-string",
            "env://SHAPE_TEST_CS",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "env://SHAPE_TEST_PW",
        )  # fmt: skip
    )
    assert code == 1
    assert "Login failed" in err and "Server=db.example.test" in err
    check_clean(world, out, err, PASSWORD, "Pa55", "w0rd")


def test_the_job_shown_by_jobs_status_has_no_secret(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_PW", PASSWORD)
    code, out, err = world.run(
        *sink_args(
            world,
            "--connection-string",
            CS,
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "env://SHAPE_TEST_PW",
            "--json",
        )  # fmt: skip
    )
    assert code == 0, err
    job_id = json.loads(out)["job_id"]
    code, out2, err2 = world.run("jobs", "status", job_id, "--json")
    code, out3, err3 = world.run("jobs", "list", "--json")
    check_clean(world, out + out2 + out3, err + err2 + err3, PASSWORD)


# --- entra modes --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flags, credential",
    [
        (("--auth", "cli"), "AzureCliCredential"),
        (("--auth", "msi"), "ManagedIdentityCredential"),
        (
            (
                "--auth",
                "spn",
                "--tenant-id",
                "t-1",
                "--client-id",
                "c-1",
                "--client-secret",
                "env://SHAPE_TEST_SPN",
            ),
            "ClientSecretCredential",
        ),  # fmt: skip
        (("--auth", "device-code", "--tenant-id", "t-1"), "DeviceCodeCredential"),
    ],
)
def test_entra_modes_sign_the_sql_connection_with_a_token(world, monkeypatch, flags, credential):
    identity = FakeIdentity().install(monkeypatch)
    monkeypatch.setattr("shape_fabric.auth._notebookutils", lambda: None)
    monkeypatch.setenv("SHAPE_TEST_SPN", PASSWORD)
    code, out, err = world.run(*sink_args(world, "--connection-string", CS, *flags))
    assert code == 0, err
    conn, cred = world.seen["connections"][0]
    assert conn == CS  # no login is added to the string
    assert cred.get_token("https://database.windows.net/.default").token == FAKE_ENTRA_TOKEN
    assert identity.calls[0]["credential"] == credential
    check_clean(world, out, err, PASSWORD, FAKE_ENTRA_TOKEN)


def test_spn_without_its_parts_says_what_is_missing(world, monkeypatch):
    FakeIdentity().install(monkeypatch)
    code, _, err = world.run(
        *sink_args(world, "--connection-string", CS, "--auth", "spn", "--tenant-id", "t")
    )
    assert code == 1 and "client-id, client-secret" in err


def test_fabric_mode_outside_a_notebook_fails_clearly(world, monkeypatch):
    monkeypatch.setattr("shape_fabric.auth._notebookutils", lambda: None)
    code, _, err = world.run(*sink_args(world, "--connection-string", CS, "--auth", "fabric"))
    assert code == 1 and "Fabric notebook" in err


def test_auth_is_validated_before_a_job_is_made(world):
    with pytest.raises(SystemExit) as stop:
        world.run(*sink_args(world, "--auth", "kerberos"))
    assert stop.value.code == 2
    assert not (world.tmp / "jobs").exists() or not list((world.tmp / "jobs").glob("*.json"))


# --- eventhouse emit ----------------------------------------------------------------------


def test_emit_to_an_eventhouse_signs_in_with_the_chosen_mode(world, monkeypatch, tmp_path):
    identity = FakeIdentity().install(monkeypatch)
    monkeypatch.setattr("shape_fabric.auth._notebookutils", lambda: None)
    kql = FakeKusto()
    monkeypatch.setattr(kusto, "urllib_transport", kql)
    code, out, err = world.run(
        "emit", world.schema, "--sink", "eventhouse://kql.example.test/db1",
        "--auth", "cli", "--table", "customer",
    )  # fmt: skip
    assert code == 0, err
    assert kql.rows and identity.calls[0]["credential"] == "AzureCliCredential"
    assert set(kql.auth) == {f"Bearer {FAKE_ENTRA_TOKEN}"}
    check_clean(world, out, err, FAKE_ENTRA_TOKEN)


def test_emit_auth_is_refused_for_a_sink_it_does_not_apply_to(world):
    code, _, err = world.run("emit", world.schema, "--auth", "cli", "--table", "customer")
    assert code == 2 and "eventhouse:// and eventstream://" in err
    code, _, err = world.run(
        "emit", world.schema, "--sink", "eventhouse://k.example.test/d", "--auth", "sql",
        "--sql-user", "u", "--sql-password", "env://X",
    )  # fmt: skip
    assert code == 2 and "database login" in err


def test_emit_connection_string_with_a_key_is_refused_as_a_literal(world):
    code, out, err = world.run(
        "emit", world.schema, "--sink", "eventstream://es1", "--table", "customer",
        "--connection-string", "Endpoint=sb://es.example.test/;SharedAccessKey=abc123abc123abc",
    )  # fmt: skip
    assert code == 2 and "env://NAME" in err and "abc123abc123abc" not in out + err


def test_references_resolve_in_the_same_way_everywhere():
    """One resolver: the signing keys and the sign-in settings share it."""
    from shape.cli import auth as cli_auth

    assert cli_auth.credrefs is credrefs
    assert credrefs.is_reference("kv://v-one/x") and credrefs.is_reference("env://X")
