"""``--auth`` and credential references on ``--to`` targets: the sign-in options reach the sinks
that sign in, with P6-07b's resolver, and are refused where they have no use."""

from __future__ import annotations

import argparse
import os
import stat

import pytest

from shape.cli import to
from shape.security import credrefs

SECRET = "Zx9-to-secret-5521"


def _args(**kw):
    ns = argparse.Namespace(
        auth=None,
        tenant_id=None,
        client_id=None,
        client_secret=None,
        sql_user=None,
        sql_password=None,
        connection_string=None,
        sink_config=[],
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_no_sign_in_options_give_nothing():
    assert to.sign_in_options(_args(), "abfss") == {}


def test_a_credential_reaches_a_storage_sink(monkeypatch):
    from shape.cli import auth

    monkeypatch.setattr(auth, "make_credential", lambda settings: ("credential", settings["mode"]))
    assert to.sign_in_options(_args(auth="msi"), "abfss") == {"credential": ("credential", "msi")}
    assert to.sign_in_options(_args(auth="cli"), "delta")["credential"] == ("credential", "cli")


def test_sql_login_needs_a_connection_string_and_a_sql_sink():
    args = _args(auth="sql", sql_user="u", sql_password="env://X")
    with pytest.raises(ValueError, match="not for a storage target"):
        to.sign_in_options(args, "abfss")
    with pytest.raises(ValueError, match="needs --connection-string"):
        to.sign_in_options(args, "sqlserver")


@pytest.mark.parametrize("name", ["postgres", "mysql"])
def test_database_sinks_that_do_not_use_fabric_sign_in_refuse_it(name):
    with pytest.raises(ValueError, match="apply to abfss://"):
        to.sign_in_options(_args(auth="cli"), name)


def test_a_literal_secret_is_refused_and_not_echoed():
    with pytest.raises(ValueError) as err:
        to.sign_in_options(_args(auth="spn", client_secret=SECRET), "abfss")
    assert SECRET not in str(err.value)


def test_connection_string_reference_is_resolved(monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CONN", "Server=tcp:h.example.test;Database=d")
    got = to.sign_in_options(_args(connection_string="env://SHAPE_TEST_CONN"), "warehouse")
    assert got == {"connection_string": "Server=tcp:h.example.test;Database=d"}


def test_sink_config_uses_the_shared_resolver(monkeypatch, tmp_path):
    monkeypatch.setenv("SHAPE_TEST_KEY", SECRET)
    assert to.sink_config(["abfss.account_key=env://SHAPE_TEST_KEY"]) == {
        "abfss": {"account_key": SECRET}
    }
    if os.name == "posix":
        f = tmp_path / "k"
        f.write_text(SECRET)
        f.chmod(0o644)  # group/other readable: the shared resolver refuses it
        with pytest.raises(credrefs.CredentialReferenceError):
            to.sink_config([f"abfss.account_key=file://{f}"])
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
        assert to.sink_config([f"abfss.account_key=file://{f}"])["abfss"]["account_key"] == SECRET


def test_sink_config_refuses_a_literal_secret():
    with pytest.raises(ValueError, match="credential reference"):
        to.sink_config([f"abfss.account_key={SECRET}"])


def test_target_options_carry_the_sign_in_to_the_named_sink(monkeypatch):
    from shape.cli import auth

    monkeypatch.setattr(auth, "make_credential", lambda settings: "cred")
    args = _args(
        auth="msi",
        roll_rows=None,
        roll_seconds=None,
        commit_rows=None,
        write_mode=None,
        manifest=False,
    )
    options = to.target_options(args, "parquet", ["abfss://c@a.dfs.core.windows.net/x"])
    assert options.extra["abfss"]["credential"] == "cred"


def test_synapse_takes_the_fabric_sign_in_options(monkeypatch):
    from shape.cli import auth

    monkeypatch.setattr(auth, "make_credential", lambda settings: ("credential", settings["mode"]))
    assert to.sign_in_options(_args(auth="msi"), "synapse") == {"credential": ("credential", "msi")}
    args = _args(auth="sql", sql_user="u", sql_password="env://X")
    with pytest.raises(ValueError, match="needs --connection-string"):
        to.sign_in_options(args, "synapse")


def test_synapse_sql_login_turns_the_uri_into_an_odbc_string(monkeypatch):
    pytest.importorskip("shape_fabric")
    monkeypatch.setenv("SHAPE_TEST_SYN_PW", SECRET)
    args = _args(
        auth="sql",
        sql_user="loader",
        sql_password="env://SHAPE_TEST_SYN_PW",
        connection_string="synapse://myws.sql.azuresynapse.net/pool1",
    )
    got = to.sign_in_options(args, "synapse")
    conn = got["connection_string"]
    assert "Server=myws.sql.azuresynapse.net" in conn and "Database=pool1" in conn
    assert "UID={loader}" in conn and f"PWD={{{SECRET}}}" in conn
