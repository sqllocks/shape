"""Credentials and connections, with pyodbc and azure-identity replaced by stand-ins."""

import struct
import sys
import types

import pytest
from shape_sqlserver import Credentials, SqlServerError, auth

pytestmark = pytest.mark.contract


class _Error(Exception):
    pass


def _fake_pyodbc(calls, fail=None):
    mod = types.ModuleType("pyodbc")
    mod.Error = _Error

    def connect(text, **kw):
        calls.append((text, kw))
        if fail:
            raise _Error(fail)
        return "connection"

    mod.connect = connect
    return mod


def test_credentials_repr_never_shows_the_secret():
    c = Credentials("spn", tenant_id="t", client_id="c", client_secret="TOP-SECRET")
    assert "TOP-SECRET" not in repr(c) and "***" in repr(c)


def test_validate():
    with pytest.raises(SqlServerError, match="unsupported auth"):
        Credentials("kerberos").validate()
    with pytest.raises(SqlServerError, match="client secret"):
        Credentials("spn", tenant_id="t", client_id="c").validate()
    Credentials("sql").validate()
    Credentials("spn", "t", "c", "s").validate()


def test_token_struct_is_length_prefixed():
    token = "abc".encode("utf-16-le")
    packed = auth.token_struct(token)
    assert packed == struct.pack("<I", len(token)) + token


def test_sql_auth_passes_the_connection_string_through(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pyodbc", _fake_pyodbc(calls))
    assert auth.connect("Server=s;UID=u;PWD=p", Credentials("sql"), timeout=7) == "connection"
    assert calls == [("Server=s;UID=u;PWD=p", {"timeout": 7, "autocommit": True})]


def test_entra_auth_sends_the_token_as_a_connection_attribute(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pyodbc", _fake_pyodbc(calls))
    seen = {}

    class Token:
        token = "entra-token"

    class Cli:
        def get_token(self, scope):
            seen["scope"] = scope
            return Token()

    ident = types.ModuleType("azure.identity")
    ident.AzureCliCredential = Cli
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.identity", ident)
    sys.modules["azure"].identity = ident
    auth.connect("Server=s", Credentials("cli"))
    text, kw = calls[0]
    assert text == "Server=s"
    assert seen["scope"] == "https://database.windows.net/.default"
    assert kw["attrs_before"] == {1256: auth.token_struct("entra-token".encode("utf-16-le"))}


def test_service_principal_builds_a_client_secret_credential(monkeypatch):
    got = {}

    class Spn:
        def __init__(self, **kw):
            got.update(kw)

        def get_token(self, scope):
            return types.SimpleNamespace(token="t")

    ident = types.ModuleType("azure.identity")
    ident.ClientSecretCredential = Spn
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.identity", ident)
    sys.modules["azure"].identity = ident
    token = auth.access_token(Credentials("spn", "tenant", "client", "secret"))
    assert token == "t".encode("utf-16-le")
    assert got == {"tenant_id": "tenant", "client_id": "client", "client_secret": "secret"}


def test_missing_azure_identity_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure", None)
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    with pytest.raises(SqlServerError, match=r"sqllocks-shape-sqlserver\[entra\]"):
        auth.access_token(Credentials("cli"))


def test_fabric_auth_outside_a_notebook_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "notebookutils", None)
    monkeypatch.setitem(sys.modules, "mssparkutils", None)
    with pytest.raises(SqlServerError, match="Fabric notebook"):
        auth.access_token(Credentials("fabric"))


def test_missing_pyodbc_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyodbc", None)
    with pytest.raises(SqlServerError, match="pyodbc is not available"):
        auth.connect("Server=s", Credentials("sql"))


def test_connect_errors_do_not_leak_the_password(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules, "pyodbc", _fake_pyodbc(calls, fail="Login failed for UID=sa;PWD=hunter2;x=1")
    )
    with pytest.raises(SqlServerError) as err:
        auth.connect("Server=s;PWD=hunter2", Credentials("sql"))
    assert "hunter2" not in str(err.value) and "could not connect" in str(err.value)
    assert err.value.__cause__ is None


def test_connect_registers_the_datetimeoffset_converter(monkeypatch):
    registered = []

    class Conn:
        def add_output_converter(self, code, func):
            registered.append(code)

    mod = types.ModuleType("pyodbc")
    mod.Error = _Error
    mod.connect = lambda text, **kw: Conn()
    monkeypatch.setitem(sys.modules, "pyodbc", mod)
    auth.connect("Server=s", Credentials("sql"))
    assert registered == [-155]


def test_managed_identity_uses_the_default_credential_chain(monkeypatch):
    got = {}

    class Default:
        def __init__(self, **kw):
            got.update(kw)

        def get_token(self, scope):
            got["scope"] = scope
            return types.SimpleNamespace(token="msi-token")

    ident = types.ModuleType("azure.identity")
    ident.DefaultAzureCredential = Default
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.identity", ident)
    sys.modules["azure"].identity = ident
    assert auth.access_token(Credentials("msi")) == "msi-token".encode("utf-16-le")
    assert got == {
        "exclude_managed_identity_credential": False,
        "scope": "https://database.windows.net/.default",
    }


@pytest.mark.parametrize("module", ["notebookutils", "mssparkutils"])
def test_fabric_auth_takes_the_notebook_token(monkeypatch, module):
    asked = []
    utils = types.SimpleNamespace(
        credentials=types.SimpleNamespace(getToken=lambda aud: asked.append(aud) or "nb-token")
    )
    if module == "notebookutils":
        notebook = types.ModuleType("notebookutils")
        notebook.mssparkutils = utils
        monkeypatch.setitem(sys.modules, "notebookutils", notebook)
    else:  # an older runtime: only the top-level mssparkutils module
        monkeypatch.setitem(sys.modules, "notebookutils", None)
        spark = types.ModuleType("mssparkutils")
        spark.credentials = utils.credentials
        monkeypatch.setitem(sys.modules, "mssparkutils", spark)
    assert auth.access_token(Credentials("fabric")) == "nb-token".encode("utf-16-le")
    assert asked == ["https://database.windows.net/"]


def test_sql_auth_has_no_token():
    with pytest.raises(SqlServerError, match="login in the connection string"):
        auth.access_token(Credentials("sql"))
