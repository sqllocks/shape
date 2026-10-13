"""Cloud credential contracts from the Owner's live findings; no network."""

import io
import logging
import os
import sys
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.builtins.sources import _azure_auth as auth
from shape.builtins.sources import azure
from shape.cli.main import main


class ClientAuthenticationError(Exception):
    """The SDK's permanent authentication exception contract."""


def test_permanent_sign_in_escapes_the_storage_sdk_retry_loop(monkeypatch, caplog):
    calls = []

    class Credential:
        def get_token(self, *args, **kwargs):
            calls.append(1)
            error = ClientAuthenticationError("AADSTS50020: user does not exist in tenant")
            logging.getLogger("azure.identity._internal.decorators").warning("failed: %s", error)
            raise error

    monkeypatch.setitem(
        sys.modules,
        "adlfs",
        SimpleNamespace(AzureBlobFileSystem=lambda **kw: SimpleNamespace(**kw)),
    )
    fs = azure._filesystem(
        azure.parse("abfss://c@acct.dfs.core.windows.net/f"), {"credential": Credential()}
    )
    # Azure Storage's retry policy catches Azure authentication exceptions. Shape's
    # permanent AuthError must leave that policy, without another credential request.
    with pytest.raises(auth.AuthError, match="tenant mismatch.*AADSTS50020"):
        for _ in range(4):
            try:
                fs.credential.get_token(auth.STORAGE_SCOPE)
            except ClientAuthenticationError:
                continue
    assert calls == [1]
    assert caplog.records == []
    logging.getLogger("azure.identity._internal.decorators").warning("another task's message")
    assert [record.message for record in caplog.records] == ["another task's message"]


def test_transient_token_failure_keeps_its_type(monkeypatch):
    error = TimeoutError("temporary outage")

    class Credential:
        def get_token(self, *args, **kwargs):
            raise error

    monkeypatch.setitem(
        sys.modules,
        "adlfs",
        SimpleNamespace(AzureBlobFileSystem=lambda **kw: SimpleNamespace(**kw)),
    )
    fs = azure._filesystem(
        azure.parse("abfss://c@acct.dfs.core.windows.net/f"), {"credential": Credential()}
    )
    with pytest.raises(TimeoutError) as caught:
        fs.credential.get_token(auth.STORAGE_SCOPE)
    assert caught.value is error


@pytest.mark.parametrize("reference", ["env", "file", "kv"])
def test_cli_cloud_profile_accepts_only_resolved_references(monkeypatch, tmp_path, reference):
    from shape.security import credrefs

    data = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2]}), data)
    calls = []

    class FS:
        def isfile(self, path):
            return True

        def open(self, path, mode="rb"):
            return io.BytesIO(data.getvalue())

    def filesystem(**options):
        calls.append(options)
        return FS()

    monkeypatch.setitem(sys.modules, "adlfs", SimpleNamespace(AzureBlobFileSystem=filesystem))
    monkeypatch.setenv("LIVE_KEY", "explicit-test-key")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "ambient-test-key")
    path = tmp_path / "key"
    path.write_text("explicit-test-key", encoding="utf-8")
    path.chmod(0o600)
    refs = {"env": "env://LIVE_KEY", "file": f"file://{path}", "kv": "kv://vault/key"}
    credrefs.register_resolver("kv", lambda _: "explicit-test-key")
    try:
        assert (
            main(
                [
                    "profile",
                    "abfss://c@acct.dfs.core.windows.net/t.parquet",
                    "--source-config",
                    f"account_key={refs[reference]}",
                    "-o",
                    str(tmp_path / "out.shape"),
                ]
            )
            == 0
        )
        assert calls and all(call["account_key"] == "explicit-test-key" for call in calls)
    finally:
        credrefs.unregister_resolver("kv")
    assert auth.resolve({}).adlfs_options == {"account_key": "ambient-test-key"}


def test_cli_refuses_a_literal_without_echoing_it(capsys):
    code = main(
        [
            "profile",
            "abfss://c@acct.dfs.core.windows.net/t.parquet",
            "--source-config",
            "account_key=must-not-appear",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "references" in captured.err and "must-not-appear" not in captured.err


class AmbientFS:
    """adlfs's documented constructor environment fallback and do_connect hook."""

    def __init__(self, **options):
        self.credential = options.get("credential")
        self.account_key = options.get("account_key") or os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
        self.connection_string = options.get("connection_string") or os.getenv(
            "AZURE_STORAGE_CONNECTION_STRING"
        )
        self.sas_token = options.get("sas_token") or os.getenv("AZURE_STORAGE_SAS_TOKEN")
        self.do_connect()

    def do_connect(self):
        if self.account_key:
            raise ValueError("Unable to determine account name for shared key credential")


def test_explicit_onelake_identity_does_not_construct_an_ambient_shared_key_client(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "another-account-key")
    monkeypatch.setitem(sys.modules, "adlfs", SimpleNamespace(AzureBlobFileSystem=AmbientFS))
    fs = azure._filesystem(
        azure.parse("abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files"),
        {"token": "test-token"},
    )
    assert fs.credential.get_token().token == "test-token"
    assert fs.account_key is None and fs.connection_string is None and fs.sas_token is None
