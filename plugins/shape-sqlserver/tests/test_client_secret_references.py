"""The profile-db secret flag resolves references without exposing credential values."""

import argparse

import pytest
from shape_sqlserver import ProfileDbCommand
from shape_sqlserver.testing import scenario

from shape.security import credrefs

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("scheme", ["env", "file", "kv"])
def test_client_secret_reference_resolves(scheme, monkeypatch, tmp_path, capsys):
    seen = {}

    def connect(text, creds):
        seen["secret"] = creds.client_secret
        return scenario("retail")

    monkeypatch.setattr("shape_sqlserver.profiler.connect", connect)
    monkeypatch.setenv("SHAPE_SQLSERVER_CLIENT_SECRET", "fallback-must-not-win")
    secret = "resolved-private-credential"
    monkeypatch.setenv("PROFILE_DB_TEST_SECRET", secret)
    path = tmp_path / "credential"
    path.write_text(secret + "\n", encoding="utf-8")
    path.chmod(0o600)
    lookups = []

    def resolve_keyvault(name):
        lookups.append(name)
        return secret

    refs = {
        "env": "env://PROFILE_DB_TEST_SECRET",
        "file": f"file://{path}",
        "kv": "kv://test-vault/test-secret",
    }
    cmd = ProfileDbCommand()
    parser = argparse.ArgumentParser()
    cmd.configure(parser)
    args = parser.parse_args(
        [
            "--server",
            "srv",
            "--database",
            "shop",
            "--auth",
            "spn",
            "--tenant-id",
            "tenant",
            "--client-id",
            "client",
            "--client-secret",
            refs[scheme],
            "-o",
            str(tmp_path / "db.shape"),
        ]
    )
    credrefs.register_resolver("kv", resolve_keyvault)
    try:
        assert cmd.run(args) == 0
    finally:
        credrefs.unregister_resolver("kv")
    assert seen["secret"] == secret
    assert lookups == (["test-vault/test-secret"] if scheme == "kv" else [])
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert captured.err == ""


@pytest.mark.parametrize("value", ["literal-private-credential", "", "other://private-value"])
def test_client_secret_literal_is_refused_without_disclosure(value, monkeypatch, tmp_path, capsys):
    connections = []

    def connect(text, creds):
        connections.append(text)
        return scenario("retail")

    monkeypatch.setattr("shape_sqlserver.profiler.connect", connect)
    cmd = ProfileDbCommand()
    parser = argparse.ArgumentParser()
    cmd.configure(parser)
    out = tmp_path / "db.shape"
    args = parser.parse_args(["--server", "srv", "--client-secret", value, "-o", str(out)])
    assert cmd.run(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "shape: error: --client-secret takes a credential reference (env://, file:// or kv://)"
    ]
    if value:
        assert value not in captured.out + captured.err
    assert connections == []
    assert not out.exists()


def test_client_secret_help_describes_reference_only_input(capsys):
    cmd = ProfileDbCommand()
    parser = argparse.ArgumentParser(prog="shape profile-db")
    cmd.configure(parser)
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--client-secret REF" in help_text
    for text in ("env://", "file://", "kv://", "literal", "SHAPE_SQLSERVER_CLIENT_SECRET"):
        assert text in help_text
