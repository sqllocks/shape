"""P6-12: ``shape demo init``: profiles are stored without a secret in them."""

from __future__ import annotations

import json
import os
import stat

import pytest

from shape.demo.connections import ConnectionProfile, ConnectionRegistry


def stored(home) -> dict:
    return json.loads((home / "connections.json").read_text())


def test_init_saves_a_profile_that_only_its_owner_can_read(run, home):
    code, out, _ = run(
        "demo", "init", "--name", "dev", "--workspace-id", "ws", "--lakehouse-id", "lh",
        "--local-path", "/tmp/x", "--auth", "spn", "--tenant-id", "t", "--client-id", "c",
        "--client-secret", "env://DEMO_SECRET",
    )  # fmt: skip
    assert code == 0 and "Connection profile 'dev' saved" in out
    profile = stored(home)["dev"]
    assert profile["workspace_id"] == "ws" and profile["local_path"] == "/tmp/x"
    assert profile["client_secret"] == "env://DEMO_SECRET"
    if os.name == "posix":
        assert stat.S_IMODE((home / "connections.json").stat().st_mode) == 0o600
    assert ConnectionRegistry().load("dev").targets() == ["local", "lakehouse"]


def test_init_refuses_a_literal_client_secret_and_stores_nothing(run, home):
    code, _, err = run(
        "demo", "init", "--name", "dev", "--auth", "spn", "--client-secret", "hunter2-literal"
    )
    assert code == 2 and "credential reference" in err and "hunter2-literal" not in err
    assert not (home / "connections.json").exists()


@pytest.mark.parametrize("key", ["PWD", "Password", "AccountKey"])
def test_init_refuses_a_connection_string_that_holds_a_password(run, home, key):
    conn = f"Driver={{ODBC Driver 18 for SQL Server}};Server=db.test;Database=d;UID=u;{key}=s3cret"
    code, _, err = run("demo", "init", "--name", "dev", "--sql-db-conn", conn)
    assert code == 2 and "holds a password or key" in err and "s3cret" not in err
    assert not (home / "connections.json").exists()


def test_init_takes_a_connection_string_given_as_a_reference(run, home):
    code, _, _ = run("demo", "init", "--name", "dev", "--sql-db-conn", "env://DEMO_SQL")
    assert code == 0 and stored(home)["dev"]["sql_db_conn_str"] == "env://DEMO_SQL"


@pytest.mark.parametrize("name", ["../evil", "a/b", ".hidden", "x" * 80, ""])
def test_a_profile_name_is_a_plain_name(run, home, name):
    code, _, err = run("demo", "init", "--name", name)
    assert code == 2 and "plain name" in err


def test_init_needs_a_name_when_there_is_no_terminal(run):
    code, _, err = run("demo", "init")
    assert code == 2 and "give --name" in err


def test_an_unknown_auth_method_is_refused(home):
    with pytest.raises(ValueError, match="unknown auth method"):
        ConnectionRegistry().save(ConnectionProfile(name="a", auth_method="password"))


def test_the_registry_lists_loads_and_deletes(home):
    registry = ConnectionRegistry()
    registry.save(ConnectionProfile(name="a", local_path="/tmp/a"))
    registry.save(ConnectionProfile(name="b", workspace_id="w"))
    assert registry.list() == ["a", "b"] and registry.exists("a")
    registry.delete("a")
    assert registry.list() == ["b"]
    with pytest.raises(LookupError, match="no connection profile 'a'"):
        registry.load("a")


def test_a_profile_file_that_is_not_json_is_a_message(run, home):
    home.mkdir(parents=True)
    (home / "connections.json").write_text("{not json")
    code, _, err = run("demo", "preflight")
    assert code == 2 and "not valid JSON" in err
