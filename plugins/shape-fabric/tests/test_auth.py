"""P6-07b: the ``--auth`` modes and the credential references, against fakes.

Entra and managed identity are ``FakeIdentity`` (a stand-in for ``azure.identity``), Key Vault is
``FakeKeyVault`` (an HTTP transport), the notebook identity is a fake ``notebookutils``. The token
requests each mode makes are pinned by tapes (``fixtures/auth_*.json`` and
``fixtures/keyvault_*.json``, replayed in ``test_recorded.py``); the tests here check behaviour
and, above all, that a secret reaches nothing but the place that needs it.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import types
from pathlib import Path

import pytest
from shape_fabric import _tsql, auth, keyvault
from shape_fabric._auth import SCOPE_SQL, SCOPE_STORAGE, token_for
from shape_fabric.errors import AuthError
from shape_fabric.testing import (
    FAKE_ENTRA_TOKEN,
    FAKE_VAULT_SECRET,
    FakeIdentity,
    FakeKeyVault,
    FakeSqlServer,
)

from shape.security import credrefs

pytestmark = pytest.mark.contract

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "66666666-7777-8888-9999-000000000000"
SECRET = "S3cr3t!value;with}odd=chars"  # a made-up secret that exercises ODBC quoting
ALL_SECRETS = (SECRET, FAKE_VAULT_SECRET)


@pytest.fixture
def identity(monkeypatch):
    return FakeIdentity().install(monkeypatch)


@pytest.fixture
def no_notebook(monkeypatch):
    monkeypatch.setattr(auth, "_notebookutils", lambda: None)


@pytest.fixture
def notebook(monkeypatch):
    class Credentials:
        def __init__(self):
            self.asked = []

        def getToken(self, audience):  # noqa: N802 - the notebookutils spelling
            self.asked.append(audience)
            return "notebook-token-" + "x" * 30

    utils = types.SimpleNamespace(credentials=Credentials())
    monkeypatch.setattr(auth, "_notebookutils", lambda: utils)
    return utils


# --- every auth mode ---------------------------------------------------------------------


def test_the_modes_are_the_baselines_six():
    assert auth.AUTH_MODES == ("cli", "msi", "spn", "sql", "device-code", "fabric", "kerberos")


def test_unknown_mode_is_an_error():
    with pytest.raises(AuthError, match="unknown --auth mode 'ntlm'"):
        auth.AuthSettings(mode="ntlm")  # was 'kerberos' until W2-10 made it a mode


def test_cli_uses_the_azure_cli_session(identity, no_notebook):
    cred = auth.build_credential(auth.AuthSettings(mode="cli"))
    assert token_for(cred, SCOPE_SQL) == FAKE_ENTRA_TOKEN
    assert [c["credential"] for c in identity.calls] == ["AzureCliCredential"] * 2
    assert identity.calls[-1]["get_token"] == [SCOPE_SQL]


def test_spn_passes_tenant_client_and_secret_and_asks_for_the_scope(identity, no_notebook):
    settings = auth.AuthSettings(
        mode="spn", tenant_id=TENANT, client_id=CLIENT, client_secret=SECRET
    )
    cred = auth.build_credential(settings)
    assert token_for(cred, SCOPE_STORAGE) == FAKE_ENTRA_TOKEN
    assert identity.secrets_seen == [SECRET]  # the credential got the real secret ...
    assert identity.calls[0]["created_with"] == {  # ... and a log of the call never would
        "tenant_id": TENANT,
        "client_id": CLIENT,
        "client_secret": "<redacted>",
    }
    assert identity.calls[-1]["get_token"] == [SCOPE_STORAGE]


@pytest.mark.parametrize("missing", ["tenant_id", "client_id", "client_secret"])
def test_spn_names_what_is_missing_and_never_a_secret(identity, missing):
    given = {"tenant_id": TENANT, "client_id": CLIENT, "client_secret": SECRET}
    given.pop(missing)
    with pytest.raises(AuthError) as err:
        auth.build_credential(auth.AuthSettings(mode="spn", **given))
    assert missing.replace("_", "-") in str(err.value)
    for secret in ALL_SECRETS:
        assert secret not in str(err.value)


def test_device_code_prints_the_address_and_code_to_stderr_not_stdout(
    identity, no_notebook, capsys
):
    cred = auth.build_credential(auth.AuthSettings(mode="device-code", tenant_id=TENANT))
    assert token_for(cred, SCOPE_SQL) == FAKE_ENTRA_TOKEN
    seen = capsys.readouterr()
    assert "https://example.test/devicelogin" in seen.err and "ABC123" in seen.err
    assert seen.out == ""
    assert identity.calls[0]["created_with"] == {"tenant_id": TENANT}


def test_msi_outside_a_notebook_is_the_managed_identity(identity, no_notebook):
    cred = auth.build_credential(auth.AuthSettings(mode="msi"))
    assert token_for(cred, SCOPE_SQL) == FAKE_ENTRA_TOKEN
    assert identity.calls[0] == {"credential": "ManagedIdentityCredential", "created_with": {}}


def test_msi_with_a_client_id_is_a_user_assigned_identity(identity, no_notebook):
    auth.build_credential(auth.AuthSettings(mode="msi", client_id=CLIENT))
    assert identity.calls[0]["created_with"] == {"client_id": CLIENT}


def test_msi_in_a_notebook_uses_the_notebook_identity_first(identity, notebook):
    cred = auth.build_credential(auth.AuthSettings(mode="msi"))
    assert token_for(cred, SCOPE_SQL).startswith("notebook-token-")
    assert notebook.credentials.asked == ["https://database.windows.net/"]
    assert identity.calls == []  # the managed identity endpoint was never touched


def test_msi_falls_back_to_the_managed_identity_when_the_notebook_has_none(identity, monkeypatch):
    class Broken:
        def getToken(self, audience):  # noqa: N802
            raise RuntimeError("no token service")

    utils = types.SimpleNamespace(credentials=Broken())
    monkeypatch.setattr(auth, "_notebookutils", lambda: utils)
    cred = auth.build_credential(auth.AuthSettings(mode="msi"))
    assert token_for(cred, SCOPE_SQL) == FAKE_ENTRA_TOKEN
    assert any(c["credential"] == "ManagedIdentityCredential" for c in identity.calls)


def test_fabric_uses_the_notebook_identity_only(identity, notebook):
    cred = auth.build_credential(auth.AuthSettings(mode="fabric"))
    assert token_for(cred, SCOPE_STORAGE).startswith("notebook-token-")
    assert notebook.credentials.asked == ["storage"]
    assert identity.calls == []


def test_fabric_outside_a_notebook_fails_without_fallback(identity, no_notebook):
    with pytest.raises(AuthError, match="Fabric notebook only"):
        auth.build_credential(auth.AuthSettings(mode="fabric"))
    assert identity.calls == []


def test_fabric_refuses_a_useless_notebook_token(monkeypatch):
    utils = types.SimpleNamespace(
        credentials=types.SimpleNamespace(getToken=lambda audience: "short")
    )
    monkeypatch.setattr(auth, "_notebookutils", lambda: utils)
    cred = auth.build_credential(auth.AuthSettings(mode="fabric"))
    with pytest.raises(AuthError, match="unusable token"):
        token_for(cred, SCOPE_SQL)


def test_sql_mode_has_no_token_credential(identity):
    assert auth.build_credential(auth.AuthSettings(mode="sql")) is None
    assert identity.calls == []


def test_a_mode_that_needs_azure_identity_says_how_to_get_it(monkeypatch, no_notebook):
    monkeypatch.setitem(sys.modules, "azure.identity", None)  # import raises ImportError
    with pytest.raises(AuthError, match=r"azure-identity.*\[entra\]"):
        auth.build_credential(auth.AuthSettings(mode="cli"))


def test_a_failing_sign_in_is_an_auth_error_without_the_secret(monkeypatch, no_notebook):
    FakeIdentity(fail=("ClientSecretCredential",)).install(monkeypatch)
    cred = auth.build_credential(
        auth.AuthSettings(mode="spn", tenant_id=TENANT, client_id=CLIENT, client_secret=SECRET)
    )
    with pytest.raises(AuthError) as err:
        token_for(cred, SCOPE_SQL)
    assert "could not sign in" in str(err.value) and SECRET not in str(err.value)


def test_a_sign_in_failure_that_echoes_the_secret_is_scrubbed(monkeypatch, no_notebook):
    fake = FakeIdentity().install(monkeypatch)
    cred = auth.build_credential(
        auth.AuthSettings(mode="spn", tenant_id=TENANT, client_id=CLIENT, client_secret=SECRET)
    )

    def echo(*scopes, **kw):
        raise RuntimeError(f"AADSTS7000215: invalid client secret {SECRET} for {CLIENT}")

    cred._credential.get_token = echo
    with pytest.raises(AuthError) as err:
        token_for(cred, SCOPE_SQL)
    assert SECRET not in str(err.value) and "AADSTS7000215" in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert fake.secrets_seen == [SECRET]


def test_settings_repr_shows_no_secret():
    settings = auth.AuthSettings(
        mode="spn", tenant_id=TENANT, client_id=CLIENT, client_secret=SECRET, sql_password=SECRET
    )
    assert SECRET not in repr(settings) and SECRET not in str(settings)
    assert "client_secret='***'" in repr(settings)


def test_settings_from_a_request_mapping_reject_unknown_keys():
    assert auth.AuthSettings.from_mapping({"mode": "cli"}).mode == "cli"
    assert auth.AuthSettings.from_mapping({}).mode == "cli"
    with pytest.raises(AuthError, match="unknown authentication setting"):
        auth.AuthSettings.from_mapping({"mode": "cli", "password": "x"})


# --- the credential references: env://, file://, kv:// ----------------------------------


@pytest.fixture
def secret_file(tmp_path):
    path = tmp_path / "secret.txt"
    path.write_text(SECRET + "\n")
    path.chmod(0o600)
    return path


def _spn_with(secret_ref, identity):
    settings = auth.AuthSettings(
        mode="spn", tenant_id=TENANT, client_id=CLIENT, client_secret=secret_ref
    )
    auth.build_credential(settings)
    return identity.secrets_seen


def test_client_secret_from_env(identity, monkeypatch, no_notebook):
    monkeypatch.setenv("SHAPE_TEST_SPN_SECRET", SECRET)
    assert _spn_with("env://SHAPE_TEST_SPN_SECRET", identity) == [SECRET]


def test_client_secret_from_a_file(identity, secret_file, no_notebook):
    assert _spn_with(f"file://{secret_file}", identity) == [SECRET]


def test_client_secret_from_key_vault(identity, monkeypatch, no_notebook):
    vault = FakeKeyVault({("vault-one", "spn-secret"): SECRET})
    monkeypatch.setattr(keyvault, "default_credential", lambda: lambda scope: "tok-" + "y" * 20)
    monkeypatch.setattr(keyvault, "urllib_transport", vault)
    assert _spn_with("kv://vault-one/spn-secret", identity) == [SECRET]
    method, url, headers = vault.requests[0]
    assert (method, url) == (
        "GET",
        "https://vault-one.vault.azure.net/secrets/spn-secret?api-version=7.4",
    )
    assert headers["Authorization"].startswith("Bearer tok-")


def test_the_kv_scheme_is_provided_by_this_plugin():
    assert credrefs.is_reference("kv://vault-one/x")
    assert credrefs._discover("kv") is keyvault.resolve


def test_an_explicit_resolver_beats_the_entry_point(identity, no_notebook):
    credrefs.register_resolver("kv", lambda rest: "from-the-registry")
    try:
        assert _spn_with("kv://anything/at-all", identity) == ["from-the-registry"]
    finally:
        credrefs.unregister_resolver("kv")


def test_key_vault_registration_helper_installs_a_working_resolver():
    vault = FakeKeyVault()
    keyvault.register(credential=lambda scope: "tok-" + "z" * 20, transport=vault)
    try:
        assert credrefs.resolve_reference("kv://vault-one/sql-password") == FAKE_VAULT_SECRET
    finally:
        credrefs.unregister_resolver("kv")


@pytest.mark.parametrize(
    "rest",
    [
        "vault-one",  # no secret
        "vault-one/",  # empty secret
        "/secret",  # empty vault
        "a/secret",  # vault name too short
        "evil.example.com/secret",  # a dot would change the host
        "vault-one/../other",  # path traversal
        "vault-one/na me",  # space
        "vault-one/name/not a version",
        "vault-one/name/v/extra",
        "-bad-vault/secret",
    ],
)
def test_key_vault_references_are_validated_before_any_request(rest):
    vault = FakeKeyVault()
    with pytest.raises(credrefs.CredentialReferenceError):
        keyvault.KeyVaultResolver(lambda s: "tok", vault)(rest)
    assert vault.requests == []


def test_key_vault_errors_name_the_secret_but_never_the_body_or_the_token():
    def leaky(method, url, headers, body, timeout):
        return 500, {}, b'{"value":"' + FAKE_VAULT_SECRET.encode() + b'"}'

    resolver = keyvault.KeyVaultResolver(lambda s: "tok-" + "q" * 20, leaky)
    with pytest.raises(credrefs.CredentialReferenceError) as err:
        resolver("vault-one/sql-password")
    text = str(err.value)
    assert "vault-one/sql-password" in text and "HTTP 500" in text
    assert FAKE_VAULT_SECRET not in text and "tok-qqqq" not in text


def test_key_vault_unreachable_and_no_value():
    def down(*a):
        raise OSError("connection refused")

    with pytest.raises(credrefs.CredentialReferenceError, match="could not be reached"):
        keyvault.KeyVaultResolver(lambda s: "t" * 20, down)("vault-one/x")

    def empty(*a):
        return 200, {}, b"{}"

    with pytest.raises(credrefs.CredentialReferenceError, match="no value"):
        keyvault.KeyVaultResolver(lambda s: "t" * 20, empty)("vault-one/x")


def test_key_vault_sign_in_failure_is_reported_without_the_cause_text():
    def broken(scope):
        raise RuntimeError("detail client_secret=" + SECRET)

    with pytest.raises(credrefs.CredentialReferenceError) as err:
        keyvault.KeyVaultResolver(broken, FakeKeyVault())("vault-one/sql-password")
    assert "sign-in failed" in str(err.value) and SECRET not in str(err.value)


def test_a_failing_reference_is_an_auth_error_that_names_the_setting(monkeypatch):
    monkeypatch.delenv("SHAPE_TEST_NOT_SET", raising=False)
    with pytest.raises(AuthError, match="client secret: .*SHAPE_TEST_NOT_SET"):
        auth.resolve_secret("env://SHAPE_TEST_NOT_SET", "client secret")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_world_readable_secret_file_is_refused_and_the_message_has_no_content(tmp_path):
    path = tmp_path / "open.txt"
    path.write_text(SECRET)
    path.chmod(0o644)
    with pytest.raises(AuthError) as err:
        auth.resolve_secret(f"file://{path}", "SQL password")
    assert "chmod 600" in str(err.value) and SECRET not in str(err.value)


# --- SQL login ---------------------------------------------------------------------------

CS = "Driver={ODBC Driver 18 for SQL Server};Server=db.example.test;Database=d;Encrypt=yes"


def _settings(**kw):
    return auth.AuthSettings(mode="sql", sql_user="app", sql_password="env://SHAPE_TEST_PW", **kw)


def test_sql_login_goes_into_the_connection_string_with_odbc_quoting(monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    options = auth.writer_options(_settings(), connection_string=CS)
    assert set(options) == {"connection_string"}
    conn = options["connection_string"]
    assert conn.startswith(CS) and "UID={app};" in conn
    assert conn.endswith("PWD={S3cr3t!value;with}}odd=chars};")  # `}` doubled, braces around


def test_sql_login_from_a_key_vault_reference(monkeypatch):
    keyvault.register(credential=lambda s: "t" * 20, transport=FakeKeyVault())
    try:
        settings = auth.AuthSettings(
            mode="sql", sql_user="app", sql_password="kv://vault-one/sql-password"
        )
        conn = auth.writer_options(settings, connection_string=CS)["connection_string"]
        assert f"PWD={{{FAKE_VAULT_SECRET}}}" in conn
    finally:
        credrefs.unregister_resolver("kv")


def test_sql_login_with_the_url_form_of_a_destination(monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    conn = auth.writer_options(_settings(), connection_string="warehouse://wh.example.test/wh1")[
        "connection_string"
    ]
    assert "Server=wh.example.test" in conn and "Database=wh1" in conn and "UID={app}" in conn


@pytest.mark.parametrize(
    "settings, message",
    [
        (auth.AuthSettings(mode="sql", sql_password="x"), "--sql-user and --sql-password"),
        (auth.AuthSettings(mode="sql", sql_user="u"), "--sql-user and --sql-password"),
    ],
)
def test_sql_mode_needs_both_user_and_password(settings, message):
    with pytest.raises(AuthError, match=message):
        auth.writer_options(settings, connection_string=CS)


def test_sql_mode_needs_a_connection_string(monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    with pytest.raises(AuthError, match="needs a connection string"):
        auth.writer_options(_settings())


@pytest.mark.parametrize("held", ["UID=a;PWD=b", "Authentication=ActiveDirectoryPassword", "pwd=x"])
def test_a_connection_string_that_already_has_a_login_is_refused(monkeypatch, held):
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    with pytest.raises(AuthError, match="already holds a login") as err:
        auth.writer_options(_settings(), connection_string=f"{CS};{held}")
    assert SECRET not in str(err.value)


def test_entra_modes_give_a_credential_option(identity, no_notebook):
    options = auth.writer_options({"mode": "cli"})
    assert set(options) == {"credential"}
    assert token_for(options["credential"], SCOPE_SQL) == FAKE_ENTRA_TOKEN
    assert auth.writer_options(None) == {}


def test_a_credential_is_passed_to_the_driver_as_a_token_and_the_login_is_not(monkeypatch):
    """The token travels in ``attrs_before`` (not in the connection string); the SQL login travels
    in the connection string (not in ``attrs_before``)."""
    calls = []

    class Pyodbc:
        class Error(Exception):
            pass

        @staticmethod
        def connect(cs, **kw):
            calls.append((cs, kw))
            return object()

    monkeypatch.setitem(sys.modules, "pyodbc", Pyodbc)
    _tsql.connect(CS, lambda scope: FAKE_ENTRA_TOKEN)
    cs, kw = calls[-1]
    assert cs == CS and FAKE_ENTRA_TOKEN.encode("utf-16-le") in kw["attrs_before"][1256]
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    conn = auth.writer_options(_settings(), connection_string=CS)["connection_string"]
    _tsql.connect(conn)
    cs, kw = calls[-1]
    assert "attrs_before" not in kw and "PWD=" in cs


def test_sql_sign_in_never_runs_a_process(monkeypatch):
    """No subprocess, no shell: the password cannot reach a command line from this code."""

    def forbidden(*a, **k):
        raise AssertionError("a process was started")

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    import os

    for name in ("system", "execv", "execvp", "spawnv", "popen"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    conn = auth.writer_options(_settings(), connection_string=CS)["connection_string"]
    server = FakeSqlServer()
    from shape_fabric import SqlDatabaseWriter
    from shape_fabric.testing import sample_batches

    with SqlDatabaseWriter(conn, connect=server.connect) as writer:
        writer.write_table("customer", sample_batches())
    assert server.rows("dbo", "customer")


# --- secrets never appear in errors, logs ------------------------------------------------


def test_a_driver_error_that_echoes_the_connection_string_is_redacted(monkeypatch):
    class Pyodbc:
        class Error(Exception):
            pass

        @classmethod
        def connect(cls, cs, **kw):
            raise cls.Error(f"login failed for {cs}")

    monkeypatch.setitem(sys.modules, "pyodbc", Pyodbc)
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    conn = auth.writer_options(_settings(), connection_string=CS)["connection_string"]
    with pytest.raises(Exception) as err:
        _tsql.connect(conn)
    assert SECRET not in str(err.value) and "S3cr3t" not in str(err.value)
    assert "Server=db.example.test" in str(err.value)  # the useful part stays


def test_nothing_is_logged_with_a_secret(identity, monkeypatch, caplog, no_notebook):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    keyvault.register(credential=lambda s: "t" * 20, transport=FakeKeyVault())
    try:
        credrefs.resolve_reference("kv://vault-one/sql-password")
    finally:
        credrefs.unregister_resolver("kv")
    auth.writer_options(_settings(), connection_string=CS)
    cred = auth.build_credential(
        auth.AuthSettings(mode="spn", tenant_id=TENANT, client_id=CLIENT, client_secret=SECRET)
    )
    token_for(cred, SCOPE_SQL)
    text = caplog.text
    for secret in (*ALL_SECRETS, FAKE_ENTRA_TOKEN):
        assert secret not in text


def test_this_plugin_never_prints_a_token_or_secret_to_the_console(
    identity, monkeypatch, capsys, no_notebook
):
    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    cred = auth.build_credential(
        auth.AuthSettings(mode="spn", tenant_id=TENANT, client_id=CLIENT, client_secret=SECRET)
    )
    token_for(cred, SCOPE_SQL)
    auth.writer_options(_settings(), connection_string=CS)
    seen = capsys.readouterr()
    for secret in (*ALL_SECRETS, FAKE_ENTRA_TOKEN):
        assert secret not in seen.out + seen.err


def test_the_committed_tapes_of_these_scenarios_hold_no_secret():
    root = Path(__file__).parent / "fixtures"
    for path in [*root.glob("auth_*.json"), *root.glob("keyvault_*.json")]:
        text = path.read_text()
        for secret in (*ALL_SECRETS, FAKE_ENTRA_TOKEN, "fake-client-secret"):
            assert secret not in text, path.name


def test_a_connection_string_with_a_login_is_redacted_wherever_a_writer_shows_it(monkeypatch):
    """The destination, the result summary and a failing statement's error all show the server
    and database, never the login's password."""
    from shape_fabric import SqlDatabaseWriter, WarehouseWriter
    from shape_fabric.errors import WriteError
    from shape_fabric.testing import MemoryFS, sample_batches

    monkeypatch.setenv("SHAPE_TEST_PW", SECRET)
    conn = auth.writer_options(_settings(), connection_string=CS)["connection_string"]
    server = FakeSqlServer()
    with SqlDatabaseWriter(conn, connect=server.connect) as writer:
        assert "Server=db.example.test" in writer.destination
        written = writer.write_table("customer", sample_batches())
        assert written
        for shown in (writer.destination, repr(writer)):
            assert "S3cr3t" not in shown and "odd=chars" not in shown
        server.fail = lambda sql, params: (_ for _ in ()).throw(RuntimeError(f"driver: {conn}"))
        with pytest.raises(WriteError) as err:
            writer.write_table("other", sample_batches(), write_mode="append")
        assert "S3cr3t" not in str(err.value) and "odd=chars" not in str(err.value)
    wh = WarehouseWriter(
        conn.replace("db.example.test", "x.datawarehouse.fabric.microsoft.com"),
        "onelake://ws/lh/Files/stage",
        connect=server.connect,
        filesystem=MemoryFS(),
    )
    assert "S3cr3t" not in wh.destination and "odd=chars" not in wh.destination
